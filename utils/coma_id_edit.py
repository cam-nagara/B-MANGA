"""コマ番号の個別変更ヘルパ."""

from __future__ import annotations

from pathlib import Path

import bpy

from ..io import coma_io, page_io
from . import gp_layer_parenting as gp_parent
from . import layer_stack as layer_stack_utils
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)
_TEMP_KEY_PREFIX = "__coma_id_edit_tmp__"


def format_coma_id(index: int) -> str:
    value = max(1, int(index))
    if value < 100:
        return f"c{value:02d}"
    return f"c{value:d}"


def coma_number_from_id(value: str) -> int:
    text = str(value or "").strip()
    if text[:1].lower() == "c" and text[1:].isdigit():
        return max(1, int(text[1:]))
    if text.isdigit():
        return max(1, int(text))
    return 1


def find_page_for_coma(context, target_coma):
    work = getattr(getattr(context, "scene", None), "bname_work", None)
    if work is None:
        return None, None, -1, -1
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        for coma_index, coma in enumerate(getattr(page, "comas", []) or []):
            try:
                if coma.as_pointer() == target_coma.as_pointer():
                    return work, page, page_index, coma_index
            except Exception:  # noqa: BLE001
                if coma is target_coma:
                    return work, page, page_index, coma_index
    return work, None, -1, -1


def set_coma_display_number(context, target_coma, number: int) -> bool:
    """レイヤー一覧の表示番号だけを変え、実データ名と並び順は触らない。"""
    if context is None or target_coma is None:
        return False
    work, page, _page_index, coma_index = find_page_for_coma(context, target_coma)
    if work is None or page is None or not (0 <= coma_index < len(page.comas)):
        return False
    display_number = max(1, int(number))
    if int(getattr(target_coma, "display_number", 0) or 0) == display_number:
        return False
    target_coma.display_number = display_number
    work_dir_text = str(getattr(work, "work_dir", "") or "")
    if work_dir_text:
        work_dir = Path(work_dir_text)
        try:
            page_io.save_page_json(work_dir, page)
            page_io.save_pages_json(work_dir, work)
            coma_io.save_coma_meta(work_dir, str(getattr(page, "id", "") or ""), target_coma)
        except Exception:  # noqa: BLE001
            _logger.exception("coma display number save failed")
    try:
        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        _logger.exception("coma display number layer stack sync failed")
    return True


def _replace_parent_key_on_entry(entry, old_key: str, new_key: str) -> None:
    if str(getattr(entry, "parent_key", "") or "") != old_key:
        return
    if hasattr(entry, "parent_kind"):
        entry.parent_kind = "coma"
    if hasattr(entry, "scope"):
        entry.scope = "page"
    entry.parent_key = new_key


def _retarget_parent_keys(context, page, remaps: list[tuple[str, str]]) -> None:
    scene = getattr(context, "scene", None)
    page_id = str(getattr(page, "id", "") or "")
    phases = [
        (old, f"{page_id}:{_TEMP_KEY_PREFIX}{index}")
        for index, (old, _new) in enumerate(remaps)
    ]
    phases.extend(
        (f"{page_id}:{_TEMP_KEY_PREFIX}{index}", new)
        for index, (_old, new) in enumerate(remaps)
    )
    for old_key, new_key in phases:
        for collection_name in ("balloons", "texts"):
            for entry in getattr(page, collection_name, []) or []:
                _replace_parent_key_on_entry(entry, old_key, new_key)
        work = getattr(scene, "bname_work", None) if scene is not None else None
        for folder in getattr(work, "layer_folders", []) or []:
            _replace_parent_key_on_entry(folder, old_key, new_key)
        for collection_name in ("bname_raster_layers", "bname_image_layers"):
            for entry in getattr(scene, collection_name, []) or []:
                _replace_parent_key_on_entry(entry, old_key, new_key)
        for layer in layer_stack_utils.gp_layers_for_parent_keys(context, {old_key}):
            gp_parent.set_parent_key(layer, new_key)
        for layer in layer_stack_utils.effect_layers_for_parent_keys(context, {old_key}):
            gp_parent.set_parent_key(layer, new_key)
        for obj in bpy.data.objects:
            if str(obj.get(on.PROP_PARENT_KEY, "") or "") == old_key:
                obj[on.PROP_PARENT_KEY] = new_key
        for coll in bpy.data.collections:
            if str(coll.get(on.PROP_PARENT_KEY, "") or "") == old_key:
                coll[on.PROP_PARENT_KEY] = new_key


def _retarget_coma_collections(page_id: str, remaps: list[tuple[str, str]]) -> None:
    pairs = []
    for index, (old_key, _new_key) in enumerate(remaps):
        coll = on.find_collection_by_bname_id(old_key, kind="coma")
        if coll is not None:
            temp_key = f"{page_id}:{_TEMP_KEY_PREFIX}{index}"
            coll[on.PROP_ID] = temp_key
            pairs.append((coll, temp_key))
    for coll, temp_key in pairs:
        index = int(temp_key.rsplit(_TEMP_KEY_PREFIX, 1)[1])
        new_id = remaps[index][1].split(":", 1)[1]
        coll[on.PROP_ID] = f"{page_id}:{new_id}"


def _set_coma_id(coma, new_id: str) -> None:
    coma.id = new_id
    coma.coma_id = new_id


def _update_current_coma_id(context, old_id: str, new_id: str) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    if str(getattr(scene, "bname_current_coma_id", "") or "") == old_id:
        scene.bname_current_coma_id = new_id


def rename_coma_to_number(context, target_coma, number: int) -> bool:
    """コマの並び順を変えずに番号だけ変更する。重複時は番号を入れ替える。"""
    if context is None or target_coma is None:
        return False
    work, page, _page_index, coma_index = find_page_for_coma(context, target_coma)
    if work is None or page is None or not (0 <= coma_index < len(page.comas)):
        return False
    new_id = format_coma_id(number)
    old_id = str(getattr(target_coma, "coma_id", "") or getattr(target_coma, "id", "") or "")
    if old_id == new_id:
        return False
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return False
    swap_index = -1
    for index, coma in enumerate(page.comas):
        if index == coma_index:
            continue
        if str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "") == new_id:
            swap_index = index
            break

    remaps = [(f"{page_id}:{old_id}", f"{page_id}:{new_id}")]
    _set_coma_id(target_coma, new_id)
    _update_current_coma_id(context, old_id, new_id)
    if swap_index >= 0 and old_id:
        swap = page.comas[swap_index]
        swap_old = str(getattr(swap, "coma_id", "") or getattr(swap, "id", "") or "")
        if swap_old:
            remaps.append((f"{page_id}:{swap_old}", f"{page_id}:{old_id}"))
            _set_coma_id(swap, old_id)
            _update_current_coma_id(context, swap_old, old_id)

    _retarget_parent_keys(context, page, remaps)
    _retarget_coma_collections(page_id, remaps)
    work_dir_text = str(getattr(work, "work_dir", "") or "")
    if work_dir_text:
        work_dir = Path(work_dir_text)
        try:
            page_io.save_page_json(work_dir, page)
            page_io.save_pages_json(work_dir, work)
            coma_io.save_coma_meta(work_dir, page_id, target_coma)
            if swap_index >= 0:
                coma_io.save_coma_meta(work_dir, page_id, page.comas[swap_index])
        except Exception:  # noqa: BLE001
            _logger.exception("coma number edit save failed")
    try:
        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        _logger.exception("coma number edit layer stack sync failed")
    try:
        from . import layer_object_sync as los

        los.mirror_work_to_outliner(context.scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("coma number edit outliner sync failed")
    return True
