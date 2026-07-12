"""Free-transform hooks for the object tool."""

from __future__ import annotations

import math
from collections.abc import Callable

from ..core.work import get_work
from ..utils import free_transform, object_selection
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import object_tool_selection

_HANDLE_HIT_MM = 3.0


def mode_key(context) -> str:
    """右クリックメニュー「自由変形」で指定された対象キーを返す."""
    wm = getattr(context, "window_manager", None) if context is not None else None
    return str(getattr(wm, "bmanga_free_transform_key", "") or "")


def clear_mode(context) -> None:
    wm = getattr(context, "window_manager", None) if context is not None else None
    if wm is not None and str(getattr(wm, "bmanga_free_transform_key", "") or ""):
        wm.bmanga_free_transform_key = ""


def free_action_for_hit(hit: dict | None, *, ctrl: bool, context=None) -> str:
    if hit is None:
        return ""
    if str(hit.get("kind", "") or "") not in {"balloon", "effect"}:
        return ""
    if not ctrl:
        # Ctrl 無しでも、「自由変形」モード中の対象なら角ドラッグを変形にする
        key = str(hit.get("key", "") or "")
        if not key or context is None or key != mode_key(context):
            return ""
    return free_transform.action_for_part(str(hit.get("part", "") or ""))


def _entry_for_key(context, kind: str, page_id: str, item_id: str):
    work = get_work(context)
    if kind == "balloon":
        if page_id == OUTSIDE_STACK_KEY:
            index, entry = object_tool_selection.find_shared_balloon_by_key(work, item_id)
            return index, entry
        _page_index, _page, index, entry = object_tool_selection.find_balloon_by_key(work, page_id, item_id)
        return index, entry
    if kind == "text":
        if page_id == OUTSIDE_STACK_KEY:
            index, entry = object_tool_selection.find_shared_text_by_key(work, item_id)
            return index, entry
        _page_index, _page, index, entry = object_tool_selection.find_text_by_key(work, page_id, item_id)
        return index, entry
    return -1, None


def _quad_for_key(context, key: str, *, force: bool = False):
    """自由変形の四隅クアッドを返す.

    force=True の場合、まだ自由変形が無効 (オフセット 0) でも矩形そのままの
    クアッドを返す。「自由変形」モード開始直後の最初の角ドラッグに必要。
    """
    kind, page_id, item_id = object_selection.parse_key(key)
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        return None
    if kind in {"balloon", "text"}:
        _index, entry = _entry_for_key(context, kind, page_id, item_id)
        if entry is None:
            return None
        quad = free_transform.entry_quad(entry, rect)
        if quad is None and force:
            quad = free_transform.quad_from_rect_offsets(rect, free_transform.entry_offsets(entry))
        return quad
    if kind == "effect":
        obj, layer = object_tool_selection.find_effect_layer(item_id)
        payload = free_transform.effect_payload_for_layer(obj, layer)
        if not free_transform.effect_payload_enabled(payload):
            if not force:
                return None
            return free_transform.quad_from_rect_offsets(rect, free_transform.zero_offsets())
        return free_transform.quad_from_rect_offsets(rect, payload.get("offsets"))
    return None


def _expanded_quad_for_hit(quad: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    points = free_transform.ordered_quad_points(quad)
    if not points:
        return quad
    cx = sum(x for x, _y in points) / len(points)
    cy = sum(y for _x, y in points) / len(points)
    outset = float(getattr(object_tool_selection, "SELECTION_HANDLE_OUTSET_MM", 3.0))
    expanded = {}
    for key, (x, y) in quad.items():
        dx = float(x) - cx
        dy = float(y) - cy
        length = math.hypot(dx, dy)
        if length <= 1.0e-9:
            expanded[key] = (float(x), float(y))
        else:
            expanded[key] = (float(x) + dx / length * outset, float(y) + dy / length * outset)
    return expanded


def _hit_for_selected_key(context, key: str, x_mm: float, y_mm: float, *, force: bool = False) -> dict | None:
    kind, page_id, item_id = object_selection.parse_key(key)
    if kind not in {"balloon", "effect"}:
        return None
    quad = _quad_for_key(context, key, force=force)
    if not quad:
        return None
    part = free_transform.hit_quad_corner(_expanded_quad_for_hit(quad), x_mm, y_mm, _HANDLE_HIT_MM)
    if not part:
        return None
    if kind == "effect":
        return {
            "kind": "effect",
            "layer_name": item_id,
            "part": part,
            "key": key,
            "world": (float(x_mm), float(y_mm)),
        }
    index, entry = _entry_for_key(context, kind, page_id, item_id)
    if entry is None:
        return None
    return {
        "kind": kind,
        "page_id": page_id,
        "index": index,
        "part": part,
        "key": key,
        "world": (float(x_mm), float(y_mm)),
    }


def hit_transformed_handle_at_event(
    context,
    event,
    event_world_xy: Callable,
) -> dict | None:
    ctrl = bool(getattr(event, "ctrl", False))
    mkey = mode_key(context)
    if not ctrl and not mkey:
        return None
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    if ctrl:
        keys = list(object_selection.get_keys(context))
        active_key = object_tool_selection.active_selection_key(context)
        if active_key and active_key not in keys:
            keys.append(active_key)
    else:
        # 「自由変形」モード中は対象キーの角だけを Ctrl 無しで掴める
        keys = [mkey]
    for key in reversed(keys):
        hit = _hit_for_selected_key(context, key, float(x_mm), float(y_mm), force=not ctrl)
        if hit is not None:
            return hit
    return None


# 回転リング判定・回転角度計算は operators/object_rotation.py へ移設した
# (ゲート問題・幾何の重なり問題を修正するため、hit_object_at_event の結果や
# 他ハンドルとの優先順位を踏まえた判定に作り直す必要があったため)。
