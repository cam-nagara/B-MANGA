"""Free-transform hooks for the object tool."""

from __future__ import annotations

from collections.abc import Callable

from ..core.work import get_work
from ..utils import free_transform, object_selection
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import object_tool_selection

_HANDLE_HIT_MM = 3.0


def free_action_for_hit(hit: dict | None, *, ctrl: bool) -> str:
    if not ctrl or hit is None:
        return ""
    if str(hit.get("kind", "") or "") not in {"balloon", "effect"}:
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


def _quad_for_key(context, key: str):
    kind, page_id, item_id = object_selection.parse_key(key)
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        return None
    if kind in {"balloon", "text"}:
        _index, entry = _entry_for_key(context, kind, page_id, item_id)
        if entry is None:
            return None
        return free_transform.entry_quad(entry, rect)
    if kind == "effect":
        obj, layer = object_tool_selection.find_effect_layer(item_id)
        payload = free_transform.effect_payload_for_layer(obj, layer)
        if not free_transform.effect_payload_enabled(payload):
            return None
        return free_transform.quad_from_rect_offsets(rect, payload.get("offsets"))
    return None


def _hit_for_selected_key(context, key: str, x_mm: float, y_mm: float) -> dict | None:
    kind, page_id, item_id = object_selection.parse_key(key)
    if kind not in {"balloon", "effect"}:
        return None
    quad = _quad_for_key(context, key)
    if not quad:
        return None
    part = free_transform.hit_quad_corner(quad, x_mm, y_mm, _HANDLE_HIT_MM)
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
    if not bool(getattr(event, "ctrl", False)):
        return None
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    keys = list(object_selection.get_keys(context))
    active_key = object_tool_selection.active_selection_key(context)
    if active_key and active_key not in keys:
        keys.append(active_key)
    for key in reversed(keys):
        hit = _hit_for_selected_key(context, key, float(x_mm), float(y_mm))
        if hit is not None:
            return hit
    return None
