"""複製した page.blend 内の所有ページ・安定ID・リンクを付け替える。"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
import uuid

import bpy


ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_PACKAGE = (__package__ or "").split(".", 1)[0]


def _load_worker_runtime_module():
    if __package__:
        return importlib.import_module(f"{__package__}.detail_data_blender_worker_runtime")
    name = "bmanga_page_operation_worker_runtime"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "io" / "detail_data_blender_worker_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_WORKER_RUNTIME = _load_worker_runtime_module()


def _ensure_runtime() -> None:
    global _RUNTIME_PACKAGE
    if _RUNTIME_PACKAGE:
        return
    package_name = "bmanga_page_operation_runtime"
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name,
            ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        module.register()
    _RUNTIME_PACKAGE = package_name


def _runtime_module(relative_name: str):
    _ensure_runtime()
    return importlib.import_module(f"{_RUNTIME_PACKAGE}.{relative_name}")


def _replace_page_key(value: object, source_page_id: str, target_page_id: str) -> str:
    text = str(value or "")
    if text == source_page_id:
        return target_page_id
    prefix = f"{source_page_id}:"
    return f"{target_page_id}:{text[len(prefix):]}" if text.startswith(prefix) else text


def _retarget_block(block, source_page_id: str, target_page_id: str, folder_map: dict[str, str]) -> None:
    object_naming = _runtime_module("utils.object_naming")
    for key in list(block.keys()):
        value = block.get(key)
        if not isinstance(value, str):
            continue
        updated = _replace_page_key(value, source_page_id, target_page_id)
        if updated != value:
            block[key] = updated
    parent = str(block.get(object_naming.PROP_PARENT_KEY, "") or "")
    folder = str(block.get(object_naming.PROP_FOLDER_ID, "") or "")
    if parent:
        block[object_naming.PROP_PARENT_KEY] = folder_map.get(
            parent, _replace_page_key(parent, source_page_id, target_page_id)
        )
    if folder in folder_map:
        block[object_naming.PROP_FOLDER_ID] = folder_map[folder]


def _retarget_collections(source_page_id: str, target_page_id: str, folder_map: dict[str, str]) -> None:
    object_naming = _runtime_module("utils.object_naming")
    for collection in bpy.data.collections:
        kind = str(collection.get(object_naming.PROP_KIND, "") or "")
        stable_id = str(collection.get(object_naming.PROP_ID, "") or "")
        _retarget_block(collection, source_page_id, target_page_id, folder_map)
        if kind == "page" and stable_id == source_page_id:
            collection[object_naming.PROP_ID] = target_page_id
            page_z = object_naming.page_id_to_z_number(target_page_id)
            collection[object_naming.PROP_Z_INDEX] = page_z
            object_naming.assign_canonical_name(
                collection,
                "page",
                page_z,
                target_page_id,
                str(collection.get(object_naming.PROP_TITLE, "") or ""),
            )
        elif kind == "coma":
            updated = _replace_page_key(stable_id, source_page_id, target_page_id)
            if updated != stable_id:
                collection[object_naming.PROP_ID] = updated
        elif kind == "folder" and stable_id in folder_map:
            new_id = folder_map[stable_id]
            collection[object_naming.PROP_ID] = new_id
            collection[object_naming.PROP_FOLDER_ID] = new_id
            object_naming.assign_canonical_name(
                collection,
                "folder",
                int(collection.get(object_naming.PROP_Z_INDEX, 0) or 0),
                new_id,
                str(collection.get(object_naming.PROP_TITLE, "") or ""),
            )


def _retarget_effect_helpers(
    source_page_id: str,
    target_page_id: str,
    folder_map: dict[str, str],
    controller_ids: dict[str, str],
) -> None:
    object_naming = _runtime_module("utils.object_naming")
    effect_helper_prop = "bmanga_effect_controller_id"
    for obj in bpy.data.objects:
        _retarget_block(obj, source_page_id, target_page_id, folder_map)
        old_controller = str(obj.get(effect_helper_prop, "") or "")
        new_controller = controller_ids.get(old_controller)
        if not new_controller:
            continue
        obj[effect_helper_prop] = new_controller
        stable_id = str(obj.get(object_naming.PROP_ID, "") or "")
        if stable_id and old_controller in stable_id:
            obj[object_naming.PROP_ID] = stable_id.replace(old_controller, new_controller)


def _retarget_layer_objects(
    source_page_id: str,
    target_page_id: str,
    folder_map: dict[str, str],
) -> dict[str, dict[str, str]]:
    layer_model = _runtime_module("utils.layer_object_model")
    layer_sync = _runtime_module("utils.layer_object_sync")
    controllers = [
        obj
        for obj in layer_model.iter_layer_objects()
        if layer_model.parent_key(obj) == source_page_id
        or layer_model.parent_key(obj).startswith(f"{source_page_id}:")
    ]
    id_maps: dict[str, dict[str, str]] = {"gp": {}, "effect": {}}
    controller_ids: dict[str, str] = {}
    for obj in controllers:
        kind = layer_model.layer_kind(obj)
        old_id = layer_model.stable_id(obj)
        new_id = layer_model.make_stable_id(kind)
        id_maps[kind][old_id] = new_id
        controller_ids[old_id] = new_id
        old_parent = layer_model.parent_key(obj)
        new_parent = _replace_page_key(old_parent, source_page_id, target_page_id)
        old_folder = layer_model.folder_id(obj)
        matrix = obj.matrix_world.copy()
        layer_sync.stamp_layer_object(
            obj,
            kind=kind,
            bmanga_id=new_id,
            title=layer_model.display_title(obj),
            z_index=layer_model.z_index(obj),
            parent_kind="coma" if ":" in new_parent else "page",
            parent_key=new_parent,
            folder_id=folder_map.get(old_folder, ""),
            scene=bpy.context.scene,
            apply_page_offset=False,
        )
        obj.matrix_world = matrix
    _retarget_effect_helpers(source_page_id, target_page_id, folder_map, controller_ids)
    return {kind: mapping for kind, mapping in id_maps.items() if mapping}


def _retarget_entry_objects(
    request: dict[str, Any],
    source_page_id: str,
    target_page_id: str,
    folder_map: dict[str, str],
) -> None:
    """work.json側の個別レイヤー実体を複製後のIDへ合わせる。"""
    object_naming = _runtime_module("utils.object_naming")
    raw_maps = dict(request.get("entryIdMaps", {}) or {})
    entry_maps = {
        str(kind): {str(old): str(new) for old, new in dict(mapping or {}).items()}
        for kind, mapping in raw_maps.items()
        if isinstance(mapping, dict)
    }
    for obj in bpy.data.objects:
        _retarget_block(obj, source_page_id, target_page_id, folder_map)
        kind = str(obj.get(object_naming.PROP_KIND, "") or "")
        stable_id = str(obj.get(object_naming.PROP_ID, "") or "")
        replacement = entry_maps.get(kind, {}).get(stable_id, "")
        if replacement:
            obj[object_naming.PROP_ID] = replacement


def _entry_uid_map(request: dict[str, Any], id_maps: dict[str, dict[str, str]]) -> dict[str, str]:
    source_page_id = str(request["sourcePageId"])
    target_page_id = str(request["targetPageId"])
    result: dict[str, str] = {}
    for kind, request_key in (("balloon", "balloonIds"), ("text", "textIds")):
        for entry_id in request.get(request_key, []) or []:
            entry_id = str(entry_id or "")
            if entry_id:
                result[f"{kind}:{source_page_id}:{entry_id}"] = f"{kind}:{target_page_id}:{entry_id}"
    for kind in ("image", "raster"):
        mapping = dict(request.get("entryIdMaps", {}).get(kind, {}) or {})
        for old_id, new_id in mapping.items():
            result[f"{kind}:{old_id}"] = f"{kind}:{new_id}"
    for kind in ("gp", "effect"):
        for old_id, new_id in id_maps.get(kind, {}).items():
            result[f"{kind}:{old_id}"] = f"{kind}:{new_id}"
    return result


def _retarget_links(request: dict[str, Any], id_maps: dict[str, dict[str, str]]) -> None:
    layer_links = _runtime_module("utils.layer_links")
    scene = bpy.context.scene
    raw = str(scene.get(layer_links.LINK_PROP, "") or "")
    try:
        mapping = json.loads(raw) if raw else {}
    except Exception:
        mapping = {}
    if not isinstance(mapping, dict):
        mapping = {}
    uid_map = _entry_uid_map(request, id_maps)
    untouched_groups: dict[str, list[str]] = {}
    cloned_groups: dict[str, list[str]] = {}
    for uid, group in mapping.items():
        uid = str(uid)
        group = str(group)
        if uid in uid_map:
            cloned_groups.setdefault(group, []).append(uid_map[uid])
        else:
            untouched_groups.setdefault(group, []).append(uid)
    rewritten: dict[str, str] = {}
    for group, members in untouched_groups.items():
        unique = list(dict.fromkeys(members))
        if len(unique) >= 2:
            rewritten.update({uid: group for uid in unique})
    for members in cloned_groups.values():
        unique = list(dict.fromkeys(members))
        if len(unique) < 2:
            continue
        group = f"layer_link_{uuid.uuid4().hex}"
        rewritten.update({uid: group for uid in unique})
    scene[layer_links.LINK_PROP] = json.dumps(rewritten, ensure_ascii=False, separators=(",", ":"))


def _stamp_scene_page(target_page_id: str) -> None:
    scene = bpy.context.scene
    if hasattr(scene, "bmanga_current_page_id"):
        scene.bmanga_current_page_id = target_page_id
    page_grid = _runtime_module("utils.page_grid")
    if page_grid.PROP_GP_SAVED_PAGE_OFFSET in scene:
        stored = dict(scene.get(page_grid.PROP_GP_SAVED_PAGE_OFFSET, {}) or {})
        stored["page_id"] = target_page_id
        scene[page_grid.PROP_GP_SAVED_PAGE_OFFSET] = stored


def _validate(source_page_id: str, target_page_id: str, id_maps: dict[str, dict[str, str]]) -> None:
    layer_model = _runtime_module("utils.layer_object_model")
    object_naming = _runtime_module("utils.object_naming")
    for obj in layer_model.iter_layer_objects():
        parent = layer_model.parent_key(obj)
        if parent == source_page_id or parent.startswith(f"{source_page_id}:"):
            raise RuntimeError("複製後の手描き／効果線に元ページ所属が残っています")
    for kind, mapping in id_maps.items():
        for old_id, new_id in mapping.items():
            if (
                old_id == new_id
                or layer_model.find_layer_object(kind, new_id) is None
                or layer_model.find_layer_object(kind, old_id) is not None
            ):
                raise RuntimeError("複製後の手描き／効果線識別子を確認できません")
            if kind == "effect" and any(
                str(obj.get("bmanga_effect_controller_id", "") or "") == old_id
                for obj in bpy.data.objects
            ):
                raise RuntimeError("複製後の効果線補助実体に元識別子が残っています")
    if object_naming.find_collection_by_bmanga_id(source_page_id, kind="page") is not None:
        raise RuntimeError("複製後のページCollectionに元ページIDが残っています")
    if object_naming.find_collection_by_bmanga_id(target_page_id, kind="page") is None:
        raise RuntimeError("複製後のページCollectionがありません")


def _convert(_page_id: str, page_path: Path, request_path: Path) -> dict[str, Any]:
    request = _WORKER_RUNTIME.read_json(request_path)
    source_page_id = str(request["sourcePageId"])
    target_page_id = str(request["targetPageId"])
    folder_map = {
        str(key): str(value) for key, value in dict(request.get("folderMap", {}) or {}).items()
    }
    bpy.ops.wm.open_mainfile(filepath=str(page_path.resolve()), load_ui=False)
    _retarget_collections(source_page_id, target_page_id, folder_map)
    id_maps = _retarget_layer_objects(source_page_id, target_page_id, folder_map)
    _retarget_entry_objects(
        request, source_page_id, target_page_id, folder_map
    )
    _retarget_links(request, id_maps)
    _stamp_scene_page(target_page_id)
    _validate(source_page_id, target_page_id, id_maps)
    bpy.ops.wm.save_as_mainfile(filepath=str(page_path.resolve()))
    bpy.ops.wm.open_mainfile(filepath=str(page_path.resolve()), load_ui=False)
    _validate(source_page_id, target_page_id, id_maps)
    return {"idMaps": id_maps}


def _inspect(_page_id: str, page_path: Path) -> dict[str, Any]:
    return {"path": str(page_path)}


def _validate_callback(_page_id: str, page_path: Path) -> dict[str, Any]:
    return {"path": str(page_path)}


if __name__ == "__main__":
    _WORKER_RUNTIME.worker_main(
        sys.argv,
        ensure_runtime=_ensure_runtime,
        inspect_callback=_inspect,
        convert_callback=_convert,
        validate_callback=_validate_callback,
    )
