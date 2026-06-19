"""フキダシカーブの手編集状態を保存・比較するヘルパ."""

from __future__ import annotations

import json
from typing import Any

import bpy

PROP_SOURCE_STATE = "bmanga_balloon_curve_source_state"
PROP_BASE_SNAPSHOT = "bmanga_balloon_curve_base_snapshot"
PROP_SOURCE_SIGNATURE = "bmanga_balloon_curve_signature"

STATE_GENERATED = "generated"
STATE_MANUAL = "manual"
STATE_FREEFORM = "freeform"


def _round_float(value: float) -> float:
    return round(float(value), 7)


def _vector_payload(vector) -> list[float]:
    return [_round_float(vector[0]), _round_float(vector[1]), _round_float(vector[2])]


def _stable_handle_payload(point, handle_attr: str, type_attr: str) -> list[float]:
    handle_type = str(getattr(point, type_attr, "") or "")
    if handle_type in {"AUTO", "VECTOR"}:
        return _vector_payload(point.co)
    return _vector_payload(getattr(point, handle_attr))


def snapshot_curve(obj: bpy.types.Object | None) -> dict[str, Any] | None:
    """現在の Curve 制御点情報を JSON 化しやすい dict で返す."""
    if obj is None or getattr(obj, "type", "") != "CURVE":
        return None
    curve = getattr(obj, "data", None)
    if curve is None:
        return None
    splines: list[dict[str, Any]] = []
    for spline in getattr(curve, "splines", []) or []:
        spline_type = str(getattr(spline, "type", "") or "")
        item: dict[str, Any] = {
            "type": spline_type,
            "cyclic": bool(getattr(spline, "use_cyclic_u", False)),
            "points": [],
        }
        if spline_type == "BEZIER":
            for point in getattr(spline, "bezier_points", []) or []:
                item["points"].append(
                    {
                        "co": _vector_payload(point.co),
                        "handle_left": _stable_handle_payload(point, "handle_left", "handle_left_type"),
                        "handle_right": _stable_handle_payload(point, "handle_right", "handle_right_type"),
                        "handle_left_type": str(getattr(point, "handle_left_type", "") or ""),
                        "handle_right_type": str(getattr(point, "handle_right_type", "") or ""),
                        "radius": _round_float(getattr(point, "radius", 1.0)),
                        "tilt": _round_float(getattr(point, "tilt", 0.0)),
                    }
                )
        else:
            for point in getattr(spline, "points", []) or []:
                co = getattr(point, "co", (0.0, 0.0, 0.0, 1.0))
                item["points"].append(
                    {
                        "co": [_round_float(co[0]), _round_float(co[1]), _round_float(co[2]), _round_float(co[3])],
                        "radius": _round_float(getattr(point, "radius", 1.0)),
                        "tilt": _round_float(getattr(point, "tilt", 0.0)),
                    }
                )
        splines.append(item)
    return {"splines": splines}


def snapshot_signature(snapshot: dict[str, Any] | None) -> list[tuple[str, bool, int]]:
    if not snapshot:
        return []
    out: list[tuple[str, bool, int]] = []
    for spline in snapshot.get("splines", []) or []:
        out.append(
            (
                str(spline.get("type", "") or ""),
                bool(spline.get("cyclic", False)),
                len(spline.get("points", []) or []),
            )
        )
    return out


def _loads(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(str(value))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _canonical_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    payload = json.loads(_dumps(snapshot))
    for spline in payload.get("splines", []) or []:
        if str(spline.get("type", "") or "") != "BEZIER":
            continue
        for point in spline.get("points", []) or []:
            co = point.get("co")
            if not co:
                continue
            if str(point.get("handle_left_type", "") or "") in {"AUTO", "VECTOR"}:
                point["handle_left"] = list(co)
            if str(point.get("handle_right_type", "") or "") in {"AUTO", "VECTOR"}:
                point["handle_right"] = list(co)
    return payload


def _dumps(payload: dict[str, Any] | list[Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mark_generated(obj: bpy.types.Object | None) -> None:
    snapshot = snapshot_curve(obj)
    if obj is None or snapshot is None:
        return
    obj[PROP_SOURCE_STATE] = STATE_GENERATED
    obj[PROP_BASE_SNAPSHOT] = _dumps(snapshot)
    obj[PROP_SOURCE_SIGNATURE] = _dumps(snapshot_signature(snapshot))


def mark_freeform(obj: bpy.types.Object | None) -> None:
    snapshot = snapshot_curve(obj)
    if obj is None or snapshot is None:
        return
    obj[PROP_SOURCE_STATE] = STATE_FREEFORM
    obj[PROP_BASE_SNAPSHOT] = _dumps(snapshot)
    obj[PROP_SOURCE_SIGNATURE] = _dumps(snapshot_signature(snapshot))


def detect_state(obj: bpy.types.Object | None) -> str:
    """最後の生成時からの差分を見て現在状態を返す."""
    if obj is None or getattr(obj, "type", "") != "CURVE":
        return STATE_FREEFORM
    current = snapshot_curve(obj)
    if current is None:
        obj[PROP_SOURCE_STATE] = STATE_FREEFORM
        return STATE_FREEFORM
    if str(obj.get(PROP_SOURCE_STATE, "") or "") == STATE_FREEFORM:
        return STATE_FREEFORM
    base = _canonical_snapshot(_loads(obj.get(PROP_BASE_SNAPSHOT, "")))
    if base is None:
        obj[PROP_SOURCE_STATE] = STATE_FREEFORM
        return STATE_FREEFORM
    if current == base:
        obj[PROP_SOURCE_STATE] = STATE_GENERATED
        return STATE_GENERATED
    if snapshot_signature(current) == snapshot_signature(base):
        obj[PROP_SOURCE_STATE] = STATE_MANUAL
        return STATE_MANUAL
    obj[PROP_SOURCE_STATE] = STATE_FREEFORM
    return STATE_FREEFORM


def is_manual_or_freeform(obj: bpy.types.Object | None) -> bool:
    return detect_state(obj) in {STATE_MANUAL, STATE_FREEFORM}


def manual_delta(obj: bpy.types.Object | None) -> dict[str, Any] | None:
    """基準形状との差分を返す。制御点数・順序が同じ場合のみ有効。"""
    if obj is None:
        return None
    current = snapshot_curve(obj)
    base = _canonical_snapshot(_loads(obj.get(PROP_BASE_SNAPSHOT, "")))
    if current is None or base is None:
        return None
    if snapshot_signature(current) != snapshot_signature(base):
        return None
    splines_delta: list[dict[str, Any]] = []
    for cur_spline, base_spline in zip(current.get("splines", []) or [], base.get("splines", []) or []):
        points_delta: list[dict[str, Any]] = []
        for cur_point, base_point in zip(cur_spline.get("points", []) or [], base_spline.get("points", []) or []):
            point_delta: dict[str, Any] = {}
            for key in ("co", "handle_left", "handle_right"):
                if key not in cur_point or key not in base_point:
                    continue
                point_delta[key] = [
                    _round_float(float(c) - float(b))
                    for c, b in zip(cur_point[key], base_point[key])
                ]
            point_delta["radius"] = _round_float(float(cur_point.get("radius", 1.0)) - float(base_point.get("radius", 1.0)))
            point_delta["tilt"] = _round_float(float(cur_point.get("tilt", 0.0)) - float(base_point.get("tilt", 0.0)))
            points_delta.append(point_delta)
        splines_delta.append({"points": points_delta})
    return {"splines": splines_delta}


def apply_delta(obj: bpy.types.Object | None, delta: dict[str, Any] | None) -> bool:
    """生成直後のカーブに、手編集差分を再適用する."""
    if obj is None or getattr(obj, "type", "") != "CURVE" or not delta:
        return False
    try:
        for spline, spline_delta in zip(obj.data.splines, delta.get("splines", []) or []):
            if str(getattr(spline, "type", "") or "") != "BEZIER":
                continue
            for point, point_delta in zip(spline.bezier_points, spline_delta.get("points", []) or []):
                for key, attr in (
                    ("co", "co"),
                    ("handle_left", "handle_left"),
                    ("handle_right", "handle_right"),
                ):
                    values = point_delta.get(key)
                    if not values:
                        continue
                    target = getattr(point, attr)
                    target.x += float(values[0])
                    target.y += float(values[1])
                    target.z += float(values[2])
                point.radius = max(0.0, float(getattr(point, "radius", 1.0)) + float(point_delta.get("radius", 0.0)))
                point.tilt = float(getattr(point, "tilt", 0.0)) + float(point_delta.get("tilt", 0.0))
        return True
    except Exception:  # noqa: BLE001
        return False
