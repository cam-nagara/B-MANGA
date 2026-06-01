"""Shared 2x2 corner free-transform helpers for B-Name objects."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from .geom import Rect, m_to_mm, mm_to_m

BOTTOM_LEFT = "bottom_left"
BOTTOM_RIGHT = "bottom_right"
TOP_LEFT = "top_left"
TOP_RIGHT = "top_right"
CORNERS = (BOTTOM_LEFT, BOTTOM_RIGHT, TOP_RIGHT, TOP_LEFT)
CORNER_PARTS = {BOTTOM_LEFT, BOTTOM_RIGHT, TOP_LEFT, TOP_RIGHT}
FREE_ACTION_PREFIX = "free_"
EFFECT_META_KEY = "free_transform"

_ENTRY_PROP_BY_CORNER = {
    BOTTOM_LEFT: "free_transform_bottom_left",
    BOTTOM_RIGHT: "free_transform_bottom_right",
    TOP_LEFT: "free_transform_top_left",
    TOP_RIGHT: "free_transform_top_right",
}


def action_for_part(part: str) -> str:
    return f"{FREE_ACTION_PREFIX}{part}" if str(part or "") in CORNER_PARTS else ""


def corner_from_action(action: str) -> str:
    value = str(action or "")
    if not value.startswith(FREE_ACTION_PREFIX):
        return ""
    corner = value[len(FREE_ACTION_PREFIX):]
    return corner if corner in CORNER_PARTS else ""


def is_free_action(action: str) -> bool:
    return bool(corner_from_action(action))


def _pair(value: Any) -> tuple[float, float]:
    try:
        return float(value[0]), float(value[1])
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def zero_offsets() -> dict[str, tuple[float, float]]:
    return {corner: (0.0, 0.0) for corner in CORNERS}


def entry_offsets(entry) -> dict[str, tuple[float, float]]:
    offsets = zero_offsets()
    if entry is None:
        return offsets
    for corner, prop_name in _ENTRY_PROP_BY_CORNER.items():
        offsets[corner] = _pair(getattr(entry, prop_name, (0.0, 0.0)))
    return offsets


def offsets_are_zero(offsets: dict[str, tuple[float, float]] | None) -> bool:
    if not offsets:
        return True
    return all(abs(float(x)) <= 1.0e-7 and abs(float(y)) <= 1.0e-7 for x, y in offsets.values())


def entry_enabled(entry) -> bool:
    if entry is None or not bool(getattr(entry, "free_transform_enabled", False)):
        return False
    return not offsets_are_zero(entry_offsets(entry))


def entry_snapshot(entry) -> dict[str, Any]:
    return {
        "enabled": bool(getattr(entry, "free_transform_enabled", False)),
        "offsets": entry_offsets(entry),
    }


def set_entry_offsets(entry, offsets: dict[str, tuple[float, float]], *, enabled: bool = True) -> None:
    if entry is None:
        return
    if hasattr(entry, "free_transform_enabled"):
        entry.free_transform_enabled = bool(enabled) and not offsets_are_zero(offsets)
    for corner, prop_name in _ENTRY_PROP_BY_CORNER.items():
        if hasattr(entry, prop_name):
            x, y = offsets.get(corner, (0.0, 0.0))
            setattr(entry, prop_name, (float(x), float(y)))


def apply_corner_drag_to_entry(entry, snapshot: dict[str, Any] | None, corner: str, dx: float, dy: float) -> None:
    if corner not in CORNER_PARTS:
        return
    offsets = zero_offsets()
    if snapshot is not None:
        raw = snapshot.get("offsets")
        if isinstance(raw, dict):
            for key in CORNERS:
                offsets[key] = _pair(raw.get(key, (0.0, 0.0)))
    x, y = offsets.get(corner, (0.0, 0.0))
    offsets[corner] = (float(x) + float(dx), float(y) + float(dy))
    if hasattr(entry, "free_transform_enabled"):
        enabled = not offsets_are_zero(offsets)
        if bool(getattr(entry, "free_transform_enabled", False)) != enabled:
            entry.free_transform_enabled = enabled
    prop_name = _ENTRY_PROP_BY_CORNER.get(corner)
    if prop_name and hasattr(entry, prop_name):
        setattr(entry, prop_name, offsets[corner])


def _offset_at(offsets: dict[str, tuple[float, float]], u: float, v: float) -> tuple[float, float]:
    bl = offsets.get(BOTTOM_LEFT, (0.0, 0.0))
    br = offsets.get(BOTTOM_RIGHT, (0.0, 0.0))
    tl = offsets.get(TOP_LEFT, (0.0, 0.0))
    tr = offsets.get(TOP_RIGHT, (0.0, 0.0))
    dx = (
        bl[0] * (1.0 - u) * (1.0 - v)
        + br[0] * u * (1.0 - v)
        + tl[0] * (1.0 - u) * v
        + tr[0] * u * v
    )
    dy = (
        bl[1] * (1.0 - u) * (1.0 - v)
        + br[1] * u * (1.0 - v)
        + tl[1] * (1.0 - u) * v
        + tr[1] * u * v
    )
    return dx, dy


def transform_point(
    x_mm: float,
    y_mm: float,
    rect: tuple[float, float, float, float],
    offsets: dict[str, tuple[float, float]] | None,
) -> tuple[float, float]:
    if offsets_are_zero(offsets):
        return float(x_mm), float(y_mm)
    rx, ry, rw, rh = rect
    rw = max(1.0e-6, float(rw))
    rh = max(1.0e-6, float(rh))
    u = (float(x_mm) - float(rx)) / rw
    v = (float(y_mm) - float(ry)) / rh
    dx, dy = _offset_at(offsets or zero_offsets(), u, v)
    return float(x_mm) + dx, float(y_mm) + dy


def transform_points(
    points: Iterable[tuple[float, float]],
    rect: tuple[float, float, float, float],
    offsets: dict[str, tuple[float, float]] | None,
) -> list[tuple[float, float]]:
    return [transform_point(x, y, rect, offsets) for x, y in points]


def transform_entry_local_point(entry, x_mm: float, y_mm: float) -> tuple[float, float]:
    if not entry_enabled(entry):
        return float(x_mm), float(y_mm)
    rect = (
        0.0,
        0.0,
        max(1.0e-6, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(1.0e-6, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    return transform_point(x_mm, y_mm, rect, entry_offsets(entry))


def transform_entry_local_points(entry, points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    return [transform_entry_local_point(entry, x, y) for x, y in points]


def transform_entry_anchor(entry, anchor):
    if not entry_enabled(entry):
        return anchor
    from .balloon_shapes import BezierAnchor

    return BezierAnchor(
        co=transform_entry_local_point(entry, *anchor.co),
        handle_left=transform_entry_local_point(entry, *anchor.handle_left) if anchor.handle_left is not None else None,
        handle_right=transform_entry_local_point(entry, *anchor.handle_right) if anchor.handle_right is not None else None,
        handle_left_type=anchor.handle_left_type,
        handle_right_type=anchor.handle_right_type,
    )


def quad_from_rect_offsets(
    rect: Rect | tuple[float, float, float, float],
    offsets: dict[str, tuple[float, float]] | None,
) -> dict[str, tuple[float, float]]:
    if isinstance(rect, Rect):
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
    else:
        x, y, w, h = rect
    base = {
        BOTTOM_LEFT: (float(x), float(y)),
        BOTTOM_RIGHT: (float(x) + float(w), float(y)),
        TOP_RIGHT: (float(x) + float(w), float(y) + float(h)),
        TOP_LEFT: (float(x), float(y) + float(h)),
    }
    if offsets_are_zero(offsets):
        return base
    out = {}
    for corner, point in base.items():
        ox, oy = (offsets or {}).get(corner, (0.0, 0.0))
        out[corner] = (point[0] + float(ox), point[1] + float(oy))
    return out


def entry_quad(entry, rect: Rect) -> dict[str, tuple[float, float]] | None:
    if not entry_enabled(entry):
        return None
    return quad_from_rect_offsets(rect, entry_offsets(entry))


def quad_bounds(quad: dict[str, tuple[float, float]]) -> Rect | None:
    if not quad:
        return None
    xs = [float(p[0]) for p in quad.values()]
    ys = [float(p[1]) for p in quad.values()]
    left = min(xs)
    bottom = min(ys)
    return Rect(left, bottom, max(0.0, max(xs) - left), max(0.0, max(ys) - bottom))


def ordered_quad_points(quad: dict[str, tuple[float, float]]) -> list[tuple[float, float]]:
    return [quad[BOTTOM_LEFT], quad[BOTTOM_RIGHT], quad[TOP_RIGHT], quad[TOP_LEFT]]


def point_in_quad(
    quad: dict[str, tuple[float, float]],
    x_mm: float,
    y_mm: float,
    tolerance_mm: float = 0.0,
) -> bool:
    points = ordered_quad_points(quad) if quad else []
    if len(points) < 3:
        return False
    x = float(x_mm)
    y = float(y_mm)
    tol = max(0.0, float(tolerance_mm))
    if tol > 0.0:
        for start, end in zip(points, points[1:] + points[:1]):
            if _point_segment_distance((x, y), start, end) <= tol:
                return True
    inside = False
    prev_x, prev_y = points[-1]
    for curr_x, curr_y in points:
        crosses = (float(curr_y) > y) != (float(prev_y) > y)
        if crosses:
            denom = float(prev_y) - float(curr_y)
            if abs(denom) > 1.0e-9:
                intersect_x = float(curr_x) + (y - float(curr_y)) * (float(prev_x) - float(curr_x)) / denom
                if x < intersect_x:
                    inside = not inside
        prev_x, prev_y = curr_x, curr_y
    return inside


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = float(ex) - float(sx)
    dy = float(ey) - float(sy)
    length_sq = dx * dx + dy * dy
    if length_sq <= 1.0e-12:
        return ((px - float(sx)) ** 2 + (py - float(sy)) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - float(sx)) * dx + (py - float(sy)) * dy) / length_sq))
    nx = float(sx) + dx * t
    ny = float(sy) + dy * t
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5


def hit_quad_corner(
    quad: dict[str, tuple[float, float]],
    x_mm: float,
    y_mm: float,
    tolerance_mm: float,
) -> str:
    best = ""
    best_dist = float("inf")
    for corner, point in quad.items():
        dx = float(x_mm) - float(point[0])
        dy = float(y_mm) - float(point[1])
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < best_dist:
            best = corner
            best_dist = dist
    return best if best and best_dist <= max(0.1, float(tolerance_mm)) else ""


def _effect_payload(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    offsets = zero_offsets()
    raw_offsets = data.get("offsets") if isinstance(data, dict) else None
    if isinstance(raw_offsets, dict):
        for corner in CORNERS:
            offsets[corner] = _pair(raw_offsets.get(corner, (0.0, 0.0)))
    return {
        "enabled": bool(data.get("enabled", False)) if isinstance(data, dict) else False,
        "offsets": offsets,
    }


def effect_payload_from_meta_entry(meta_entry: Any) -> dict[str, Any]:
    if not isinstance(meta_entry, dict):
        return _effect_payload(None)
    return _effect_payload(meta_entry.get(EFFECT_META_KEY))


def effect_payload_enabled(payload: dict[str, Any] | None) -> bool:
    if not payload or not bool(payload.get("enabled", False)):
        return False
    raw = payload.get("offsets")
    return isinstance(raw, dict) and not offsets_are_zero(raw)


def effect_payload_for_layer(obj, layer) -> dict[str, Any]:
    try:
        import json

        raw = str(getattr(obj, "data", {}).get("bname_effect_line_meta", "") or "{}")
        meta = json.loads(raw) if raw else {}
        layer_id = str(getattr(layer, "name", "") or "")
        entry = meta.get(layer_id)
        if entry is None:
            entry = meta.get(str(getattr(layer, "info", "") or ""))
        return effect_payload_from_meta_entry(entry)
    except Exception:  # noqa: BLE001
        return _effect_payload(None)


def set_effect_payload_on_meta_entry(meta_entry: dict[str, Any], payload: dict[str, Any]) -> None:
    offsets = payload.get("offsets") if isinstance(payload, dict) else None
    serial_offsets = {}
    if isinstance(offsets, dict):
        for corner in CORNERS:
            x, y = _pair(offsets.get(corner, (0.0, 0.0)))
            serial_offsets[corner] = [round(float(x), 6), round(float(y), 6)]
    meta_entry[EFFECT_META_KEY] = {
        "enabled": bool(payload.get("enabled", False)) and not offsets_are_zero(offsets),
        "offsets": serial_offsets,
    }


def apply_corner_drag_to_effect_entry(
    meta_entry: dict[str, Any],
    snapshot: dict[str, Any] | None,
    corner: str,
    dx: float,
    dy: float,
) -> None:
    if corner not in CORNER_PARTS:
        return
    payload = _effect_payload(snapshot)
    offsets = payload["offsets"]
    x, y = offsets.get(corner, (0.0, 0.0))
    offsets[corner] = (float(x) + float(dx), float(y) + float(dy))
    payload["enabled"] = True
    payload["offsets"] = offsets
    set_effect_payload_on_meta_entry(meta_entry, payload)


def transform_effect_strokes(
    strokes,
    bounds: tuple[float, float, float, float],
    payload: dict[str, Any] | None,
):
    if not effect_payload_enabled(payload):
        return strokes
    offsets = payload.get("offsets")
    out = []
    for stroke in strokes or ():
        new_points = []
        for point in getattr(stroke, "points_xyz", []) or []:
            try:
                x_m, y_m, z_m = point
            except Exception:  # noqa: BLE001
                continue
            x_mm, y_mm = transform_point(m_to_mm(float(x_m)), m_to_mm(float(y_m)), bounds, offsets)
            new_points.append((mm_to_m(x_mm), mm_to_m(y_mm), float(z_m)))
        try:
            out.append(replace(stroke, points_xyz=new_points))
        except Exception:  # noqa: BLE001
            out.append(stroke)
    return out
