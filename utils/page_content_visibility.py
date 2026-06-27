"""ページ一覧で表示する中身を選択ページ周辺へ絞る."""

from __future__ import annotations

import json
from pathlib import Path

import bpy

from . import gp_layer_parenting, object_naming as on, page_range
from .layer_hierarchy import OUTSIDE_STACK_KEY

DETAIL_RADIUS = 1
PROP_VIRTUAL_HIDDEN = "bmanga_page_list_virtual_hidden"
PROP_PREVIOUS_HIDE_VIEWPORT = "bmanga_page_list_previous_hide_viewport"
GP_HIDE_MAP_PROP = "bmanga_page_list_gp_hide_original_map_json"

_APPLY_SCHEDULED = False


def is_work_blend_scene(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None or not bool(getattr(scene, "bmanga_overview_mode", False)):
        return False
    try:
        from . import paths

        raw_path = str(getattr(bpy.data, "filepath", "") or "")
        if not raw_path:
            return bool(getattr(scene, "bmanga_overview_mode", False))
        filepath = Path(raw_path)
        return filepath.name == paths.WORK_BLEND_NAME
    except Exception:  # noqa: BLE001
        return bool(getattr(scene, "bmanga_overview_mode", False))


def schedule_apply(context=None) -> None:
    global _APPLY_SCHEDULED
    if _APPLY_SCHEDULED:
        return
    _APPLY_SCHEDULED = True

    def _run():
        global _APPLY_SCHEDULED
        _APPLY_SCHEDULED = False
        try:
            if is_work_blend_scene(getattr(bpy.context, "scene", None)):
                try:
                    from ..operators import raster_layer_op

                    raster_layer_op.ensure_all_raster_runtime(bpy.context)
                except Exception:  # noqa: BLE001
                    pass
            apply_page_content_visibility(bpy.context)
        except Exception:  # noqa: BLE001
            pass
        return None

    try:
        bpy.app.timers.register(_run, first_interval=0.05)
    except Exception:  # noqa: BLE001
        _APPLY_SCHEDULED = False


def _detail_page_ids(work) -> set[str]:
    if work is None or not getattr(work, "loaded", False):
        return set()
    pages = list(getattr(work, "pages", []) or [])
    if not pages:
        return set()
    active = int(getattr(work, "active_page_index", -1))
    if active < 0 or active >= len(pages) or not page_range.page_visible_in_work(pages[active]):
        active = -1
        for index, page in enumerate(pages):
            if page_range.page_visible_in_work(page):
                active = index
                break
    if active < 0:
        return set()
    first = max(0, active - DETAIL_RADIUS)
    last = min(len(pages) - 1, active + DETAIL_RADIUS)
    return {
        str(getattr(pages[index], "id", "") or "")
        for index in range(first, last + 1)
        if page_range.page_visible_in_work(pages[index])
    }


def detail_page_ids(context=None, work=None) -> set[str]:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    work = work or getattr(scene, "bmanga_work", None)
    try:
        from . import page_file_scene

        page_id = page_file_scene.current_page_id(scene)
        if page_id and page_file_scene.is_page_edit_scene(scene):
            return {page_id}
    except Exception:  # noqa: BLE001
        pass
    if not is_work_blend_scene(scene):
        return {
            str(getattr(page, "id", "") or "")
            for page in getattr(work, "pages", []) or []
            if page_range.page_visible_in_work(page)
        }
    return _detail_page_ids(work)


def _page_lookup(work) -> set[str]:
    return {
        str(getattr(page, "id", "") or "")
        for page in getattr(work, "pages", []) or []
        if str(getattr(page, "id", "") or "")
    }


def _balloon_page_lookup(work) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            continue
        for entry in getattr(page, "balloons", []) or []:
            balloon_id = str(getattr(entry, "id", "") or "")
            if balloon_id:
                lookup[balloon_id] = page_id
    return lookup


def _folder_page_lookup(work, page_ids: set[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    try:
        from . import layer_folder
    except Exception:  # noqa: BLE001
        return lookup
    for folder in getattr(work, "layer_folders", []) or []:
        folder_id = str(getattr(folder, "id", "") or "")
        if not folder_id:
            continue
        parent_key = layer_folder.semantic_parent_key_for_folder(work, folder_id)
        page_id = _page_from_parent_key(parent_key, page_ids)
        if page_id:
            lookup[folder_id] = page_id
    return lookup


def _page_from_parent_key(
    parent_key: str,
    page_ids: set[str],
    folder_pages: dict[str, str] | None = None,
) -> str:
    key = str(parent_key or "")
    if not key or key == OUTSIDE_STACK_KEY:
        return ""
    if folder_pages is not None and key in folder_pages:
        return folder_pages[key]
    if ":" in key:
        page_id = key.split(":", 1)[0]
        return page_id if page_id in page_ids else ""
    if key in page_ids:
        return key
    return ""


def _is_content_object(obj) -> bool:
    kind = str(obj.get(on.PROP_KIND, "") or "")
    if kind in {"balloon", "balloon_group", "image", "image_path", "text", "raster", "gp", "effect"}:
        return True
    if kind.startswith("effect_"):
        return True
    balloon_owner_props = (
        "bmanga_balloon_fill_mesh_owner_id",
        "bmanga_balloon_line_mesh_owner_id",
        "bmanga_balloon_fill_owner_id",
        "bmanga_balloon_source_owner_id",
        "bmanga_balloon_clip_mask_owner_id",
        "bmanga_balloon_merge_group_id",
        "bmanga_balloon_merge_source_ids",
    )
    return any(str(obj.get(prop, "") or "") for prop in balloon_owner_props)


def _object_page_id(
    obj,
    *,
    page_ids: set[str],
    folder_pages: dict[str, str],
    balloon_pages: dict[str, str],
) -> str:
    page_id = _page_from_parent_key(
        str(obj.get(on.PROP_PARENT_KEY, "") or ""),
        page_ids,
        folder_pages,
    )
    if page_id:
        return page_id
    for prop in (
        "bmanga_balloon_fill_mesh_owner_id",
        "bmanga_balloon_line_mesh_owner_id",
        "bmanga_balloon_fill_owner_id",
        "bmanga_balloon_source_owner_id",
        "bmanga_balloon_clip_mask_owner_id",
        "bmanga_balloon_merge_group_id",
    ):
        owner = str(obj.get(prop, "") or "")
        if owner in balloon_pages:
            return balloon_pages[owner]
    raw_sources = str(obj.get("bmanga_balloon_merge_source_ids", "") or "")
    if raw_sources:
        for source_id in raw_sources.replace(",", " ").split():
            if source_id in balloon_pages:
                return balloon_pages[source_id]
    return ""


def _set_virtual_hidden(obj, hidden: bool) -> bool:
    if obj is None:
        return False
    changed = False
    if hidden:
        if not bool(obj.get(PROP_VIRTUAL_HIDDEN, False)):
            obj[PROP_PREVIOUS_HIDE_VIEWPORT] = bool(getattr(obj, "hide_viewport", False))
            obj[PROP_VIRTUAL_HIDDEN] = True
        if not bool(getattr(obj, "hide_viewport", False)):
            obj.hide_viewport = True
            changed = True
        return changed
    if bool(obj.get(PROP_VIRTUAL_HIDDEN, False)):
        previous = bool(obj.get(PROP_PREVIOUS_HIDE_VIEWPORT, False))
        if bool(getattr(obj, "hide_viewport", False)) != previous:
            obj.hide_viewport = previous
            changed = True
        for prop in (PROP_VIRTUAL_HIDDEN, PROP_PREVIOUS_HIDE_VIEWPORT):
            try:
                del obj[prop]
            except Exception:  # noqa: BLE001
                pass
    return changed


def _load_gp_hide_map(gp_data) -> dict[str, bool]:
    try:
        raw = str(gp_data.get(GP_HIDE_MAP_PROP, "") or "")
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return {str(key): bool(value) for key, value in data.items() if key}


def _save_gp_hide_map(gp_data, data: dict[str, bool]) -> None:
    try:
        if data:
            gp_data[GP_HIDE_MAP_PROP] = json.dumps(data, ensure_ascii=False, sort_keys=True)
        elif GP_HIDE_MAP_PROP in gp_data:
            del gp_data[GP_HIDE_MAP_PROP]
    except Exception:  # noqa: BLE001
        pass


def _apply_gp_layer_visibility(
    work,
    page_ids: set[str],
    folder_pages: dict[str, str],
    visible_page_ids: set[str],
) -> int:
    try:
        from . import gpencil as gp_utils

        obj = gp_utils.get_master_gpencil()
    except Exception:  # noqa: BLE001
        return 0
    gp_data = getattr(obj, "data", None)
    layers = getattr(gp_data, "layers", None)
    if layers is None:
        return 0
    state = _load_gp_hide_map(gp_data)
    known = set()
    changed = 0
    for layer in layers:
        name = str(getattr(layer, "name", "") or "")
        if name:
            known.add(name)
        parent_key = gp_layer_parenting.parent_key(layer)
        page_id = _page_from_parent_key(parent_key, page_ids, folder_pages)
        if not page_id:
            if name in state:
                previous = bool(state.pop(name))
                if bool(getattr(layer, "hide", False)) != previous:
                    layer.hide = previous
                    changed += 1
            continue
        should_hide = page_id not in visible_page_ids
        if should_hide:
            if name and name not in state:
                state[name] = bool(getattr(layer, "hide", False))
            if not bool(getattr(layer, "hide", False)):
                layer.hide = True
                changed += 1
        elif name in state:
            previous = bool(state.pop(name))
            if bool(getattr(layer, "hide", False)) != previous:
                layer.hide = previous
                changed += 1
    for stale in set(state) - known:
        state.pop(stale, None)
        changed += 1
    _save_gp_hide_map(gp_data, state)
    return changed


def raster_entry_in_detail_window(context, entry) -> bool:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None or not is_work_blend_scene(scene):
        return True
    work = getattr(scene, "bmanga_work", None)
    parent_key = str(getattr(entry, "parent_key", "") or "")
    page_ids = _page_lookup(work)
    page_id = _page_from_parent_key(parent_key, page_ids, _folder_page_lookup(work, page_ids))
    if not page_id:
        return True
    return page_id in detail_page_ids(context, work)


def apply_page_content_visibility(context=None, work=None) -> int:
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    work = work or getattr(scene, "bmanga_work", None)
    if work is None or not getattr(work, "loaded", False):
        return 0
    page_ids = _page_lookup(work)
    if not page_ids:
        return 0
    visible_page_ids = detail_page_ids(context, work)
    folder_pages = _folder_page_lookup(work, page_ids)
    balloon_pages = _balloon_page_lookup(work)
    changed = 0
    for obj in bpy.data.objects:
        if not _is_content_object(obj):
            if bool(obj.get(PROP_VIRTUAL_HIDDEN, False)):
                changed += int(_set_virtual_hidden(obj, False))
            continue
        page_id = _object_page_id(
            obj,
            page_ids=page_ids,
            folder_pages=folder_pages,
            balloon_pages=balloon_pages,
        )
        if not page_id:
            changed += int(_set_virtual_hidden(obj, False))
            continue
        changed += int(_set_virtual_hidden(obj, page_id not in visible_page_ids))
    changed += _apply_gp_layer_visibility(work, page_ids, folder_pages, visible_page_ids)
    try:
        for area in getattr(getattr(context, "screen", None), "areas", []) or []:
            if getattr(area, "type", "") == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return changed


def restore_all_virtual_hidden(context=None, work=None) -> int:
    """ページ一覧用に一時非表示にした中身をユーザー本来の表示へ戻す."""
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    work = work or getattr(scene, "bmanga_work", None)
    page_ids = _page_lookup(work) if work is not None else set()
    folder_pages = _folder_page_lookup(work, page_ids) if work is not None else {}
    changed = 0
    for obj in bpy.data.objects:
        if bool(obj.get(PROP_VIRTUAL_HIDDEN, False)):
            changed += int(_set_virtual_hidden(obj, False))
    if work is not None and page_ids:
        changed += _apply_gp_layer_visibility(work, page_ids, folder_pages, page_ids)
    try:
        for area in getattr(getattr(context, "screen", None), "areas", []) or []:
            if getattr(area, "type", "") == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return changed
