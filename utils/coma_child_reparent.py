"""コマ結合時にコマ配下レイヤーの所属を移し替えるヘルパ."""

from __future__ import annotations

import bpy

from ..core.work import get_work
from . import layer_stack as layer_stack_utils
from . import object_naming as on
from .layer_hierarchy import coma_stack_key


def _old_parent_keys(page, panel) -> set[str]:
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(panel, "coma_id", "") or "")
    entry_id = str(getattr(panel, "id", "") or "")
    keys = {
        coma_stack_key(page, panel),
        coma_id,
        entry_id,
    }
    if page_id and coma_id:
        keys.add(f"{page_id}:{coma_id}")
    if page_id and entry_id:
        keys.add(f"{page_id}:{entry_id}")
    keys.discard("")
    return keys


def _entry_parent_matches(entry, keys: set[str]) -> bool:
    return str(getattr(entry, "parent_key", "") or "") in keys


def _set_entry_parent(entry, new_key: str) -> bool:
    changed = False
    if hasattr(entry, "parent_kind") and str(getattr(entry, "parent_kind", "") or "") != "coma":
        entry.parent_kind = "coma"
        changed = True
    if hasattr(entry, "parent_key") and str(getattr(entry, "parent_key", "") or "") != new_key:
        entry.parent_key = new_key
        changed = True
    return changed


def _reparent_entries(entries, old_keys: set[str], new_key: str) -> int:
    changed = 0
    for entry in list(entries or []):
        if not _entry_parent_matches(entry, old_keys):
            continue
        if _set_entry_parent(entry, new_key):
            changed += 1
    return changed


def _reparent_scene_entries(context, old_keys: set[str], new_key: str) -> int:
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    changed = 0
    for attr in (
        "bmanga_raster_layers",
        "bmanga_image_layers",
        "bmanga_image_path_layers",
        "bmanga_fill_layers",
    ):
        changed += _reparent_entries(getattr(scene, attr, None), old_keys, new_key)
    return changed


def _reparent_layer_folders(work, old_keys: set[str], new_key: str) -> int:
    changed = 0
    for folder in list(getattr(work, "layer_folders", []) or []):
        if str(getattr(folder, "parent_key", "") or "") not in old_keys:
            continue
        folder.parent_key = new_key
        changed += 1
    return changed


def _reparent_runtime_objects(old_keys: set[str], new_key: str) -> int:
    changed = 0
    for obj in bpy.data.objects:
        try:
            if str(obj.get(on.PROP_PARENT_KEY, "") or "") not in old_keys:
                continue
            obj[on.PROP_PARENT_KEY] = new_key
            changed += 1
        except Exception:  # noqa: BLE001
            continue
    return changed


def _append_layer_refs(dst, src) -> int:
    existing = {str(getattr(ref, "layer_id", "") or "") for ref in getattr(dst, "layer_refs", []) or []}
    added_count = 0
    for ref in getattr(src, "layer_refs", []) or []:
        layer_id = str(getattr(ref, "layer_id", "") or "")
        if not layer_id or layer_id in existing:
            continue
        added = dst.layer_refs.add()
        added.layer_id = layer_id
        existing.add(layer_id)
        added_count += 1
    return added_count


def reparent_coma_children(context, page, old_panel, new_panel) -> int:
    """old_panel 配下のレイヤーを new_panel 配下へ移す."""
    work = get_work(context)
    new_key = coma_stack_key(page, new_panel)
    old_keys = _old_parent_keys(page, old_panel)
    changed = 0
    changed += _append_layer_refs(new_panel, old_panel)
    changed += layer_stack_utils.reparent_gp_layers(context, coma_stack_key(page, old_panel), new_key)
    changed += layer_stack_utils.reparent_effect_layers(context, coma_stack_key(page, old_panel), new_key)
    changed += _reparent_entries(getattr(page, "balloons", None), old_keys, new_key)
    changed += _reparent_entries(getattr(page, "texts", None), old_keys, new_key)
    changed += _reparent_scene_entries(context, old_keys, new_key)
    if work is not None:
        changed += _reparent_layer_folders(work, old_keys, new_key)
    changed += _reparent_runtime_objects(old_keys, new_key)
    if changed:
        layer_stack_utils.tag_view3d_redraw(context)
    return changed
