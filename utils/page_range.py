"""Helpers for keeping page-number range and actual pages in sync."""

from __future__ import annotations

from pathlib import Path

from ..io import page_io, work_io
from . import gp_object_layer
from . import layer_object_model
from . import layer_stack as layer_stack_utils
from . import log, page_grid
from .layer_hierarchy import page_stack_key, split_child_key

_logger = log.get_logger(__name__)
_RANGE_HIDDEN_PROP = "bmanga_range_hidden"
_RANGE_HIDE_VIEWPORT_PROP = "bmanga_range_original_hide_viewport"
_RANGE_HIDE_RENDER_PROP = "bmanga_range_original_hide_render"
_RANGE_HIDE_LAYER_PROP = "bmanga_range_original_layer_hide"


def page_slot_count(work) -> int:
    """Return the number of numbered pages represented by the page entries.

    A spread is one overview entry, but it still occupies two numbered pages.
    Counting collection entries directly would therefore create a phantom normal
    page whenever a work containing a spread is reopened.
    """

    return sum(
        2 if bool(getattr(page, "spread", False)) else 1
        for page in getattr(work, "pages", [])
    )


def desired_page_count(work) -> int:
    info = getattr(work, "work_info", None)
    if info is None:
        return page_slot_count(work)
    start = int(getattr(info, "page_number_start", 1))
    end = int(getattr(info, "page_number_end", start))
    return max(1, end - start + 1)


def page_in_range(page) -> bool:
    """Return whether a page is inside the work-info start/end page range."""
    return bool(getattr(page, "in_page_range", True))


def page_visible_in_work(page) -> bool:
    """Return effective page visibility, including user eye state and range state."""
    return bool(getattr(page, "visible", True)) and page_in_range(page)


def iter_in_range_pages(work):
    """Yield ``(index, page)`` for pages currently included in the page range."""
    if work is None:
        return
    for index, page in enumerate(getattr(work, "pages", [])):
        if page_in_range(page):
            yield index, page


def in_range_page_count(work) -> int:
    return sum(1 for _index, _page in iter_in_range_pages(work))


def _clamp_active_page_to_range(work) -> bool:
    pages = getattr(work, "pages", [])
    if len(pages) == 0:
        changed = int(getattr(work, "active_page_index", -1)) != -1
        work.active_page_index = -1
        return changed
    active = int(getattr(work, "active_page_index", -1))
    if 0 <= active < len(pages) and page_in_range(pages[active]):
        return False
    for index, page in iter_in_range_pages(work):
        work.active_page_index = index
        return active != index
    work.active_page_index = -1
    return active != -1


def update_page_range_visibility(work) -> bool:
    """Sync page range flags to work-info count without deleting existing pages."""
    if work is None:
        return False
    desired = desired_page_count(work)
    changed = False
    slot_index = 0
    for page in getattr(work, "pages", []):
        in_range = slot_index < desired
        if hasattr(page, "in_page_range") and bool(page.in_page_range) != in_range:
            page.in_page_range = in_range
            changed = True
        slot_index += 2 if bool(getattr(page, "spread", False)) else 1
    if _clamp_active_page_to_range(work):
        changed = True
    if _apply_range_visibility_to_gp_layers(work):
        changed = True
    if changed:
        # 範囲外になったページの paper_bg Mesh を viewport から隠す。
        # 範囲内に戻ったページは表示を復帰させる。
        try:
            import bpy

            from . import paper_bg_object as _pbg

            scene = bpy.context.scene if bpy.context else None
            if scene is not None:
                _pbg.refresh_paper_bg_visibility(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("paper_bg visibility refresh failed")
    return changed


def _set_range_hidden(obj) -> bool:
    if bool(obj.get(_RANGE_HIDDEN_PROP, False)):
        return False
    layer = layer_object_model.content_layer(obj)
    obj[_RANGE_HIDE_VIEWPORT_PROP] = bool(getattr(obj, "hide_viewport", False))
    obj[_RANGE_HIDE_RENDER_PROP] = bool(getattr(obj, "hide_render", False))
    obj[_RANGE_HIDE_LAYER_PROP] = bool(getattr(layer, "hide", False)) if layer else False
    obj[_RANGE_HIDDEN_PROP] = True
    obj.hide_viewport = True
    obj.hide_render = True
    if layer is not None:
        layer.hide = True
    return True


def _restore_range_hidden(obj) -> bool:
    if not bool(obj.get(_RANGE_HIDDEN_PROP, False)):
        return False
    layer = layer_object_model.content_layer(obj)
    obj.hide_viewport = bool(obj.get(_RANGE_HIDE_VIEWPORT_PROP, False))
    obj.hide_render = bool(obj.get(_RANGE_HIDE_RENDER_PROP, False))
    if layer is not None:
        layer.hide = bool(obj.get(_RANGE_HIDE_LAYER_PROP, False))
    for name in (
        _RANGE_HIDDEN_PROP,
        _RANGE_HIDE_VIEWPORT_PROP,
        _RANGE_HIDE_RENDER_PROP,
        _RANGE_HIDE_LAYER_PROP,
    ):
        if name in obj:
            del obj[name]
    return True


def _apply_range_visibility_to_gp_layers(work) -> bool:
    """範囲外ページの個別GPを隠し、ユーザーの表示状態を保持する。"""
    changed = False
    visible_page_keys = {
        page_stack_key(page)
        for page in getattr(work, "pages", [])
        if page_in_range(page)
    }
    for obj in layer_object_model.iter_layer_objects("gp"):
        parent_key = layer_object_model.parent_key(obj)
        if not parent_key:
            changed = _restore_range_hidden(obj) or changed
            continue
        page_key, _child_key = split_child_key(parent_key)
        if page_key and page_key not in visible_page_keys:
            changed = _set_range_hidden(obj) or changed
        else:
            changed = _restore_range_hidden(obj) or changed
    return changed


def sync_end_number_to_existing_pages(work) -> None:
    """Make end number cover all existing pages without deleting anything."""
    info = getattr(work, "work_info", None)
    if info is None:
        return
    start = max(0, int(getattr(info, "page_number_start", 1)))
    count = max(1, page_slot_count(work))
    min_end = start + count - 1
    if int(getattr(info, "page_number_end", start)) < min_end:
        info.page_number_end = min_end


def sync_end_number_to_page_count(work) -> None:
    """Set end number so the current start/end range matches existing pages."""
    info = getattr(work, "work_info", None)
    if info is None:
        return
    count = page_slot_count(work)
    if count <= 0:
        return
    start = max(0, int(getattr(info, "page_number_start", 1)))
    end = start + count - 1
    if int(getattr(info, "page_number_end", start)) != end:
        info.page_number_end = end
    update_page_range_visibility(work)


def ensure_pages_for_number_range(context) -> int:
    """Create missing pages for the current start/end range. Never removes pages."""
    from ..core.work import get_work

    work = get_work(context)
    if not (work and getattr(work, "loaded", False) and getattr(work, "work_dir", "")):
        return 0
    try:
        from ..core.mode import MODE_PAGE, get_mode

        if get_mode(context) != MODE_PAGE:
            return 0
    except Exception:  # noqa: BLE001
        return 0
    desired = desired_page_count(work)
    current = page_slot_count(work)
    range_changed = update_page_range_visibility(work)

    work_dir = Path(work.work_dir)
    created = 0
    previous_active = int(getattr(work, "active_page_index", -1))
    try:
        from ..operators.coma_op import create_basic_frame_coma

        for _ in range(max(0, desired - current)):
            entry = page_io.register_new_page(work)
            if hasattr(entry, "in_page_range"):
                entry.in_page_range = True
            page_io.ensure_page_dir(work_dir, entry.id)
            create_basic_frame_coma(work, entry, work_dir)
            gp_object_layer.ensure_default_page_layer(context.scene, entry.id)
            created += 1
        range_changed = update_page_range_visibility(work) or range_changed
        if (
            0 <= previous_active < len(work.pages)
            and page_in_range(work.pages[previous_active])
        ):
            work.active_page_index = previous_active
        else:
            _clamp_active_page_to_range(work)
        if created == 0 and not range_changed:
            return 0
        page_grid.apply_page_collection_transforms(context, work)
        page_io.save_pages_json(work_dir, work)
        work_io.save_work_json(work_dir, work)
        layer_stack_utils.sync_layer_stack_after_data_change(
            context,
            align_page_order=True,
            align_coma_order=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_pages_for_number_range failed")
    try:
        for area in getattr(context, "screen", None).areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return created
