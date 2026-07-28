"""オブジェクトツールの同一点クリック循環を管理する."""

from __future__ import annotations

import time

from ..ui import selection_cycle_overlay
from ..utils import object_selection
from . import (
    effect_line_op,
    object_tool_click_candidates,
    view_event_region,
)

CLICK_DISTANCE_PX = 8.0
MAX_INTERVAL_SEC = 2.0
_EMPTY_ANCHOR = (-1.0e9, -1.0e9)


def overlapping_balloon_cycle_is_active(owner, event, hit: dict | None) -> bool:
    """フキダシ上のテキストでは、素早い連続クリックも循環へ回す."""
    if hit is None or str(hit.get("kind", "") or "") != "text":
        return False
    keys = tuple(getattr(owner, "_click_cycle_keys", ()) or ())
    if len(keys) < 2 or str(hit.get("key", "") or "") not in keys:
        return False
    if not any(object_selection.parse_key(key)[0] == "balloon" for key in keys[1:]):
        return False
    anchor_x, anchor_y = getattr(owner, "_click_cycle_anchor_px", _EMPTY_ANCHOR)
    dx = float(getattr(event, "mouse_x", 0.0)) - float(anchor_x)
    dy = float(getattr(event, "mouse_y", 0.0)) - float(anchor_y)
    return (dx * dx + dy * dy) ** 0.5 <= CLICK_DISTANCE_PX


def clear(owner) -> None:
    """循環状態と一時表示を破棄する."""
    owner._click_cycle_anchor_px = _EMPTY_ANCHOR
    owner._click_cycle_keys = ()
    owner._click_cycle_hits = ()
    owner._click_cycle_index = -1
    owner._click_cycle_last_time = 0.0
    selection_cycle_overlay.clear()


def reset_if_pointer_moved(owner, event) -> None:
    """候補地点からカーソルが離れたら次回を通常選択へ戻す."""
    if not getattr(owner, "_click_cycle_keys", ()):
        return
    anchor_x, anchor_y = getattr(owner, "_click_cycle_anchor_px", _EMPTY_ANCHOR)
    dx = float(getattr(event, "mouse_x", 0.0)) - float(anchor_x)
    dy = float(getattr(event, "mouse_y", 0.0)) - float(anchor_y)
    if (dx * dx + dy * dy) ** 0.5 > CLICK_DISTANCE_PX:
        clear(owner)


def _same_cycle(owner, primary_key: str, mouse_x: float, mouse_y: float, now: float) -> bool:
    cached_hits = tuple(getattr(owner, "_click_cycle_hits", ()) or ())
    cached_keys = tuple(getattr(owner, "_click_cycle_keys", ()) or ())
    anchor_x, anchor_y = getattr(owner, "_click_cycle_anchor_px", _EMPTY_ANCHOR)
    distance = ((mouse_x - anchor_x) ** 2 + (mouse_y - anchor_y) ** 2) ** 0.5
    elapsed = now - float(getattr(owner, "_click_cycle_last_time", 0.0) or 0.0)
    return (
        len(cached_hits) >= 2
        and primary_key in cached_keys
        and distance <= CLICK_DISTANCE_PX
        and elapsed <= MAX_INTERVAL_SEC
    )


def _candidate_state(
    owner,
    context,
    primary_hit: dict,
    x_mm: float,
    y_mm: float,
    mouse_x: float,
    mouse_y: float,
    now: float,
):
    primary_key = str(primary_hit.get("key", "") or "")
    if _same_cycle(owner, primary_key, mouse_x, mouse_y, now):
        candidates = [dict(hit) for hit in owner._click_cycle_hits]
        keys = tuple(owner._click_cycle_keys)
        index = (int(getattr(owner, "_click_cycle_index", -1)) + 1) % len(candidates)
        return candidates, keys, index
    candidates = object_tool_click_candidates.candidates_at_world(
        context,
        float(x_mm),
        float(y_mm),
        primary_hit,
    )
    if len(candidates) < 2:
        return None
    keys = tuple(str(hit.get("key", "") or "") for hit in candidates)
    return candidates, keys, 0


def _store_and_show(
    owner,
    context,
    view,
    candidates: list[dict],
    keys: tuple[str, ...],
    index: int,
    mouse_xy: tuple[float, float],
    now: float,
) -> dict:
    owner._click_cycle_anchor_px = mouse_xy
    owner._click_cycle_keys = keys
    owner._click_cycle_hits = tuple(dict(hit) for hit in candidates)
    owner._click_cycle_index = index
    owner._click_cycle_last_time = now
    chosen = candidates[index]
    _area, region, _rv3d, region_x, region_y = view
    selection_cycle_overlay.show(
        region,
        float(region_x),
        float(region_y),
        object_tool_click_candidates.selection_display_name(
            context,
            str(chosen.get("key", "") or ""),
        ),
        index=index + 1,
        total=len(candidates),
    )
    return chosen


def choose_hit(owner, context, event, primary_hit: dict | None) -> dict | None:
    """通常ヒットを先頭に、同一点の重なり候補をクリックごとに送る."""
    if primary_hit is None or str(getattr(event, "value", "") or "") != "PRESS":
        return primary_hit
    if str(primary_hit.get("part", "") or "") not in {"body", "move"}:
        clear(owner)
        return primary_hit
    x_mm, y_mm = effect_line_op._event_world_xy_mm(context, event)
    view = view_event_region.view3d_window_under_event(context, event)
    if x_mm is None or y_mm is None or view is None:
        clear(owner)
        return primary_hit

    now = time.monotonic()
    mouse_x = float(getattr(event, "mouse_x", 0.0))
    mouse_y = float(getattr(event, "mouse_y", 0.0))
    state = _candidate_state(
        owner,
        context,
        primary_hit,
        float(x_mm),
        float(y_mm),
        mouse_x,
        mouse_y,
        now,
    )
    if state is None:
        clear(owner)
        return primary_hit
    candidates, keys, index = state
    return _store_and_show(
        owner,
        context,
        view,
        candidates,
        keys,
        index,
        (mouse_x, mouse_y),
        now,
    )
