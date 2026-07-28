"""同一点クリック循環の候補列挙と表示名解決."""

from __future__ import annotations

from ..core.work import get_work
from ..utils import (
    layer_display,
    layer_object_model,
    layer_stack as layer_stack_utils,
    object_selection,
)
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import object_tool_selection as selection

CLICK_CYCLE_KINDS = {
    "coma",
    "balloon",
    "text",
    "effect",
    "image",
    "image_path",
    "gp",
    "raster",
    "fill",
}


def _find_entry_target(context, work, kind: str, page_id: str, item_id: str):
    if kind == "coma":
        if page_id == OUTSIDE_STACK_KEY:
            return selection.find_shared_coma_by_key(work, item_id)[1]
        return selection.find_coma_by_key(work, page_id, item_id)[3]
    if kind == "balloon":
        if page_id == OUTSIDE_STACK_KEY:
            return selection.find_shared_balloon_by_key(work, item_id)[1]
        return selection.find_balloon_by_key(work, page_id, item_id)[3]
    if kind == "text":
        if page_id == OUTSIDE_STACK_KEY:
            return selection.find_shared_text_by_key(work, item_id)[1]
        return selection.find_text_by_key(work, page_id, item_id)[3]
    finders = {
        "image": selection.find_image_by_key,
        "image_path": selection.find_image_path_by_key,
        "raster": selection.find_raster_by_key,
        "fill": selection.find_fill_by_key,
    }
    finder = finders.get(kind)
    return finder(context, item_id)[1] if finder is not None else None


def selection_display_name(context, key: str) -> str:
    """レイヤー一覧と同じ由来の、クリック循環表示用レイヤー名を返す."""
    work = get_work(context)
    kind, page_id, item_id = object_selection.parse_key(key)
    target = _find_entry_target(context, work, kind, page_id, item_id)
    if kind == "text":
        return layer_display.text_entry_display_name(target, item_id) or "テキスト"
    if kind == "gp":
        obj, _layer = selection.find_gp_layer(item_id)
        return layer_object_model.display_title(obj) or layer_stack_utils._jp_layer_label(kind, item_id)
    if kind == "effect":
        obj, _layer = selection.find_effect_layer(item_id)
        return layer_object_model.display_title(obj) or layer_stack_utils._jp_layer_label(kind, item_id)
    if kind == "coma":
        title = str(getattr(target, "title", "") or "").replace("基本枠", "").strip(" -_　")
        return title or layer_stack_utils._coma_display_label(target, item_id)
    title = str(getattr(target, "title", "") or "") if target is not None else ""
    return title or layer_stack_utils._jp_layer_label(kind, item_id) or "レイヤー"


def candidates_at_world(
    context,
    x_mm: float,
    y_mm: float,
    primary_hit: dict | None,
) -> list[dict]:
    """通常ヒットを先頭に、同一点の選択可能レイヤーを軽量列挙する."""
    if primary_hit is None:
        return []
    primary_key = str(primary_hit.get("key", "") or "")
    primary_kind = object_selection.parse_key(primary_key)[0]
    if not primary_key or primary_kind not in CLICK_CYCLE_KINDS:
        return []
    if not selection.selection_key_pickable(context, primary_key):
        return []

    candidates = [dict(primary_hit)]
    seen = {primary_key}
    work = get_work(context)
    for item in selection._iter_rect_select_candidates(context):
        key = str(item.get("key", "") or "")
        kind = object_selection.parse_key(key)[0]
        if not key or key in seen or kind not in CLICK_CYCLE_KINDS:
            continue
        rect = item.get("rect")
        hit = item.get("hit")
        if rect is None or hit is None or not selection.rect_contains_point(rect, x_mm, y_mm):
            continue
        if not selection.hit_visible_at_world(context, hit, x_mm, y_mm):
            continue
        if kind == "raster":
            _index, entry = selection.find_raster_by_key(
                context,
                object_selection.parse_key(key)[2],
            )
            if entry is None or work is None:
                continue
            if selection._raster_alpha_at_world(context, work, entry, x_mm, y_mm) <= 0.01:
                continue
        candidate = dict(hit)
        candidate.setdefault("world", (float(x_mm), float(y_mm)))
        candidates.append(candidate)
        seen.add(key)
    return candidates
