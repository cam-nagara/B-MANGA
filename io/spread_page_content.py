"""見開き結合・解除用の page.blend とページディレクトリの安全な取引。

原本ページは検証済みの一時ディレクトリが完成するまで変更しない。確定時だけ
作品ロック下で同一ボリュームの ``os.replace`` を使い、例外時はディレクトリと
JSON sidecar を元へ戻す。子 Blender は両方の ``page.blend`` を実際に開き、
ページ Collection の全実体を統合・分配する。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
from typing import Any, Mapping
import uuid


SOURCE_PAGE_PROP = "bmanga_spread_source_page_id"
SOURCE_PAGES_PROP = "bmanga_spread_source_pages"
LINK_PROP = "bmanga_layer_link_groups"
_WORKER_TOKEN_ENV = "BMANGA_DETAIL_MIGRATION_WORKER_TOKEN"
_WORKER_CLAIM_ENV = "BMANGA_DETAIL_MIGRATION_WORKER_CLAIM"
_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = (__package__ or "").split(".", 1)[0]


def _load_fs_transaction():
    if __package__:
        return importlib.import_module(f"{__package__}.spread_fs_transaction")
    name = "bmanga_spread_fs_transaction"
    spec = importlib.util.spec_from_file_location(name, _ROOT / "io" / "spread_fs_transaction.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_FS = _load_fs_transaction()
MANIFEST_NAME = _FS.MANIFEST_NAME
SpreadContentError = _FS.SpreadContentError
_copy_mapped_comas = _FS._copy_mapped_comas
_copy_page_shell = _FS._copy_page_shell
_copy_selected_comas = _FS._copy_selected_comas
_inject_failure = _FS._inject_failure
_install_directories_and_json = _FS._install_directories_and_json
_is_coma_id = _FS._is_coma_id
_is_derived_only_page_dir = _FS._is_derived_only_page_dir
_merge_extra_assets = _FS._merge_extra_assets
_require_page_source = _FS._require_page_source
_validate_staged_page = _FS._validate_staged_page
_write_coma_jsons = _FS._write_coma_jsons
_write_json = _FS._write_json


def merge_page_content(
    work_dir: Path,
    first_page_id: str,
    second_page_id: str,
    spread_id: str,
    *,
    request: Mapping[str, Any],
    coma_maps: Mapping[str, Mapping[str, str]],
    manifest: Mapping[str, Any],
    work_json: Mapping[str, Any],
    pages_json: Mapping[str, Any],
    page_json: Mapping[str, Any],
    fail_phase: str = "",
) -> dict[str, Any]:
    """両ページを一時領域で統合し、成功時だけ作品へ確定する。"""

    work = Path(work_dir).resolve(strict=True)
    first_dir = work / first_page_id
    second_dir = work / second_page_id
    _require_page_source(first_dir)
    _require_page_source(second_dir)
    spread_target = work / spread_id
    if spread_target.exists() or spread_target.is_symlink():
        raise SpreadContentError(f"見開き保存先が既にあります: {spread_id}")
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_spread_merge_", dir=work.parent))
    try:
        staged = temp_root / spread_id
        _copy_page_shell(first_dir, staged)
        _merge_extra_assets(second_dir, staged)
        _copy_mapped_comas(first_dir, staged, coma_maps.get(first_page_id, {}))
        _copy_mapped_comas(second_dir, staged, coma_maps.get(second_page_id, {}))
        worker_request = dict(request)
        worker_request.update({
            "operation": "merge",
            "second_path": str((second_dir / "page.blend").resolve()),
            "output_path": str((staged / "page.blend").resolve()),
        })
        worker_result = _run_worker(spread_id, first_dir / "page.blend", worker_request)
        final_manifest = dict(manifest)
        final_manifest["objectMaps"] = worker_result.get("objectMaps", {})
        final_manifest["linkGroupMaps"] = worker_result.get("linkGroupMaps", {})
        final_page_json = worker_result.get("pageData", page_json)
        _write_coma_jsons(staged, final_page_json)
        _write_json(staged / "page.json", final_page_json)
        _write_json(staged / MANIFEST_NAME, final_manifest)
        _validate_staged_page(staged, spread_id)
        _inject_failure(fail_phase, "after_stage")
        _install_directories_and_json(
            work,
            removals=(first_dir, second_dir),
            additions=((staged, work / spread_id),),
            work_json=work_json,
            pages_json=pages_json,
            fail_phase=fail_phase,
        )
        return {"manifest": final_manifest, "pageData": dict(final_page_json)}
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def split_page_content(
    work_dir: Path,
    spread_id: str,
    page_ids: tuple[str, str],
    *,
    requests: Mapping[str, Mapping[str, Any]],
    coma_maps: Mapping[str, Mapping[str, str]],
    work_json: Mapping[str, Any],
    pages_json: Mapping[str, Any],
    page_jsons: Mapping[str, Mapping[str, Any]],
    fail_phase: str = "",
) -> None:
    """見開き page.blend を原ページ印で左右へ分配して原子的に確定する。"""

    work = Path(work_dir).resolve(strict=True)
    spread_dir = work / spread_id
    _require_page_source(spread_dir)
    derived_targets = []
    for page_id in page_ids:
        target = work / page_id
        if _is_derived_only_page_dir(target):
            derived_targets.append(target)
        elif target.exists() or target.is_symlink():
            raise SpreadContentError(f"解除先ページが既にあります: {page_id}")
    assigned_comas = set().union(
        *(set(coma_maps.get(page_id, {})) for page_id in page_ids)
    )
    stored_comas = {
        item.name for item in spread_dir.iterdir()
        if item.is_dir() and _is_coma_id(item.name)
    }
    if stored_comas - assigned_comas:
        raise SpreadContentError(
            "所属元を確認できないコマ保存フォルダーがあります: "
            + ", ".join(sorted(stored_comas - assigned_comas))
        )
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_spread_split_", dir=work.parent))
    try:
        additions = []
        for page_id in page_ids:
            staged = temp_root / page_id
            _copy_page_shell(spread_dir, staged)
            _copy_selected_comas(spread_dir, staged, coma_maps.get(page_id, {}))
            worker_request = dict(requests[page_id])
            worker_request.update({
                "operation": "split",
                "source_page_id": page_id,
                "output_path": str((staged / "page.blend").resolve()),
            })
            _run_worker(page_id, spread_dir / "page.blend", worker_request)
            _write_coma_jsons(staged, page_jsons[page_id])
            _write_json(staged / "page.json", page_jsons[page_id])
            _validate_staged_page(staged, page_id)
            additions.append((staged, work / page_id))
        _inject_failure(fail_phase, "after_stage")
        _install_directories_and_json(
            work,
            removals=(spread_dir, *derived_targets),
            additions=tuple(additions),
            work_json=work_json,
            pages_json=pages_json,
            fail_phase=fail_phase,
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def read_manifest(work_dir: Path, spread_id: str) -> dict[str, Any]:
    path = Path(work_dir) / spread_id / MANIFEST_NAME
    if not path.is_file():
        raise SpreadContentError(
            "この見開きには安全な解除情報がありません。元データを保護するため解除を中止しました"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SpreadContentError("見開き解除情報の形式が不正です")
    return value


def _run_worker(page_id: str, source: Path, request: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _runtime_module("io.detail_data_blender_worker_runtime")
    import bpy

    return runtime.run_worker(
        bpy.app.binary_path,
        Path(__file__),
        "convert",
        page_id,
        source,
        request=request,
    )


def _ensure_runtime() -> None:
    global _PACKAGE
    if _PACKAGE:
        return
    package_name = "bmanga_spread_worker_runtime"
    spec = importlib.util.spec_from_file_location(
        package_name,
        _ROOT / "__init__.py",
        submodule_search_locations=[str(_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    _PACKAGE = package_name


def _runtime_module(relative_name: str):
    _ensure_runtime()
    return importlib.import_module(f"{_PACKAGE}.{relative_name}")


def _worker_convert(page_id: str, source_path: Path, request_path: Path) -> dict[str, Any]:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise SpreadContentError("見開きワーカー要求が不正です")
    operation = str(request.get("operation", ""))
    if operation == "merge":
        return _worker_merge(page_id, source_path, request)
    if operation == "split":
        return _worker_split(page_id, source_path, request)
    raise SpreadContentError(f"未対応の見開き処理です: {operation}")


def _open_blend(path: Path):
    import bpy

    bpy.ops.wm.open_mainfile(filepath=str(path.resolve()), load_ui=False)
    return bpy.context.scene


def _find_page_collection(scene, page_id: str):
    def walk(collection):
        for child in collection.children:
            if (
                str(child.get("bmanga_kind", "") or "") == "page"
                and str(child.get("bmanga_id", "") or "") == page_id
            ) or child.name == page_id:
                return child
            found = walk(child)
            if found is not None:
                return found
        return None

    return walk(scene.collection)


def _iter_tree(root, seen=None):
    seen = seen if seen is not None else set()
    pointer = root.as_pointer()
    if pointer in seen:
        return
    seen.add(pointer)
    yield root
    for child in tuple(root.children):
        yield from _iter_tree(child, seen)


def _tree_objects(root):
    seen = set()
    for collection in _iter_tree(root):
        for obj in tuple(collection.objects):
            if obj.as_pointer() not in seen:
                seen.add(obj.as_pointer())
                yield obj


def _stamp_source(root, page_id: str) -> None:
    for collection in _iter_tree(root):
        collection[SOURCE_PAGE_PROP] = page_id
        for obj in tuple(collection.objects):
            obj[SOURCE_PAGE_PROP] = page_id


def _append_scene(path: Path):
    import bpy

    before = {scene.as_pointer() for scene in bpy.data.scenes}
    with bpy.data.libraries.load(str(path.resolve()), link=False) as (source, target):
        target.scenes = list(source.scenes[:1])
    return next(scene for scene in bpy.data.scenes if scene.as_pointer() not in before)


def _worker_merge(page_id: str, source_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    import bpy

    first_id = str(request["first_page_id"])
    second_id = str(request["second_page_id"])
    scene = _open_blend(source_path)
    first_root = _find_page_collection(scene, first_id)
    if first_root is None:
        raise SpreadContentError(f"{first_id} のページCollectionがありません")
    second_scene = _append_scene(Path(request["second_path"]))
    second_root = _find_page_collection(second_scene, second_id)
    if second_root is None:
        raise SpreadContentError(f"{second_id} のページCollectionがありません")
    _stamp_source(first_root, first_id)
    _stamp_source(second_root, second_id)
    _set_source_offsets(first_root, float(request.get("right_page_offset_mm", 0.0) or 0.0))
    _set_source_offsets(second_root, 0.0)
    maps = _prepare_object_maps(first_root, second_root, request)
    _remap_tree(first_root, first_id, page_id, maps.get(first_id, {}))
    _remap_tree(second_root, second_id, page_id, maps.get(second_id, {}))
    for child in tuple(second_root.children):
        first_root.children.link(child)
        second_root.children.unlink(child)
    for obj in tuple(second_root.objects):
        first_root.objects.link(obj)
        second_root.objects.unlink(obj)
    first_root.name = page_id
    first_root["bmanga_kind"] = "page"
    first_root["bmanga_id"] = page_id
    first_root[SOURCE_PAGES_PROP] = json.dumps([first_id, second_id], separators=(",", ":"))
    links, link_group_maps = _merge_link_maps(
        scene, second_scene, maps, first_id, second_id, page_id
    )
    worker_request = dict(request)
    worker_request["page_data"] = _page_data_with_object_maps(
        request["page_data"], request.get("entity_sources", {}), maps
    )
    _apply_scene_metadata(scene, page_id, worker_request)
    _sync_balloon_transforms_from_metadata(scene, page_id)
    scene[LINK_PROP] = json.dumps(links, ensure_ascii=False, separators=(",", ":"))
    _unlink_collection(second_scene.collection, second_root)
    bpy.data.scenes.remove(second_scene)
    if second_root.users == 0:
        bpy.data.collections.remove(second_root)
    output = Path(request["output_path"])
    bpy.ops.wm.save_as_mainfile(filepath=str(output), compress=False)
    _open_blend(output)
    _validate_worker_scene(page_id, expected_sources={first_id, second_id})
    return {
        "operation": "merge",
        "objectMaps": maps,
        "linkGroupMaps": link_group_maps,
        "pageData": worker_request["page_data"],
    }


def _prepare_object_maps(first_root, second_root, request: Mapping[str, Any]):
    first_id = str(request["first_page_id"])
    second_id = str(request["second_page_id"])
    supplied = request.get("id_maps", {})
    maps: dict[str, dict[str, dict[str, str]]] = {
        first_id: _normalize_kind_maps(supplied.get(first_id, {})),
        second_id: _normalize_kind_maps(supplied.get(second_id, {})),
    }
    for obj in _tree_objects(first_root):
        kind = str(obj.get("bmanga_kind", "") or "")
        old = str(obj.get("bmanga_id", "") or "")
        if kind and old and not (kind == "text" and old.startswith(f"{first_id}:")):
            maps[first_id].setdefault(kind, {}).setdefault(old, old)
    used = set()
    for obj in _tree_objects(first_root):
        kind = str(obj.get("bmanga_kind", "") or "")
        old = str(obj.get("bmanga_id", "") or "")
        if kind and old:
            used.add((kind, _mapped_object_id(kind, old, first_id, str(request["target_page_id"]), maps[first_id])))
    second_maps = maps[second_id]
    processed: set[tuple[str, str]] = set()
    second_objects = list(_tree_objects(second_root))
    second_objects.sort(
        key=lambda obj: str(obj.get("bmanga_kind", "") or "") not in {"gp", "effect"}
    )
    for obj in second_objects:
        kind = str(obj.get("bmanga_kind", "") or "")
        old = str(obj.get("bmanga_id", "") or "")
        if not kind or not old:
            continue
        source_key = (kind, old)
        if source_key in processed:
            continue
        processed.add(source_key)
        new = _mapped_object_id(kind, old, second_id, str(request["target_page_id"]), second_maps)
        controller_old = str(obj.get("bmanga_effect_controller_id", "") or "")
        controller_new = second_maps.get("effect", {}).get(controller_old, controller_old)
        if controller_old and controller_new != controller_old and controller_old in old:
            new = old.replace(controller_old, controller_new)
            second_maps.setdefault(kind, {})[old] = new
        if (kind, new) in used:
            if kind not in {"gp", "effect"}:
                raise SpreadContentError(
                    f"{kind} の安定IDが衝突しています。原本を保護するため統合を中止しました: {old}"
                )
            salt = 0
            while (kind, new) in used:
                salt += 1
                token = uuid.uuid5(
                    uuid.NAMESPACE_URL, f"{second_id}|{kind}|{old}|{salt}"
                ).hex[:16]
                new = f"{kind}_{token}"
            second_maps.setdefault(kind, {})[old] = new
        elif not (kind == "text" and old.startswith(f"{second_id}:")):
            second_maps.setdefault(kind, {}).setdefault(old, new)
        used.add((kind, new))
    return maps


def _normalize_kind_maps(value) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(kind): {str(old): str(new) for old, new in mapping.items()}
        for kind, mapping in value.items()
        if isinstance(mapping, Mapping)
    }


def _set_source_offsets(root, x_mm: float) -> None:
    """全面座標の実体だけに見開き内オフセットを付ける。"""

    for obj in _tree_objects(root):
        kind = str(obj.get("bmanga_kind", "") or "")
        if kind in {"gp", "effect", "raster"}:
            obj["bmanga_subpage_offset_x_mm"] = float(x_mm)
            obj["bmanga_subpage_offset_y_mm"] = 0.0


def _clear_source_offsets(root) -> None:
    for obj in _tree_objects(root):
        for key in ("bmanga_subpage_offset_x_mm", "bmanga_subpage_offset_y_mm"):
            if key in obj:
                del obj[key]


def _page_data_with_object_maps(page_data, entity_sources, maps):
    """ワーカーが決めた GP/効果線IDをコマ参照にも反映する。"""

    data = deepcopy(dict(page_data))
    coma_sources = entity_sources.get("coma", {}) if isinstance(entity_sources, Mapping) else {}
    for coma in data.get("comas", []):
        coma_id = str(coma.get("comaId", "") or "")
        source_id = str(coma_sources.get(coma_id, "") or "")
        flat: dict[str, str] = {}
        for mapping in maps.get(source_id, {}).values():
            flat.update(mapping)
        coma["layerRefs"] = [flat.get(str(value), str(value)) for value in coma.get("layerRefs", [])]
    return data


def _mapped_object_id(kind: str, value: str, old_page: str, new_page: str, maps) -> str:
    mapping = maps.get(kind, {})
    if value in mapping:
        return mapping[value]
    if kind == "text" and value.startswith(f"{old_page}:"):
        raw = value.split(":", 1)[1]
        return f"{new_page}:{mapping.get(raw, raw)}"
    return value


def _remap_tree(root, old_page: str, new_page: str, maps) -> None:
    seen_objects = set()
    seen_data = set()
    for collection in _iter_tree(root):
        _remap_id_properties(collection, old_page, new_page, maps)
        for obj in tuple(collection.objects):
            pointer = obj.as_pointer()
            if pointer in seen_objects:
                continue
            seen_objects.add(pointer)
            kind = str(obj.get("bmanga_kind", "") or "")
            old_id = str(obj.get("bmanga_id", "") or "")
            _remap_id_properties(obj, old_page, new_page, maps)
            if old_id:
                obj["bmanga_id"] = _mapped_object_id(kind, old_id, old_page, new_page, maps)
            data = getattr(obj, "data", None)
            data_pointer = data.as_pointer() if data is not None else 0
            if data is not None and data_pointer not in seen_data:
                seen_data.add(data_pointer)
                _remap_id_properties(data, old_page, new_page, maps)


def _remap_id_properties(block, old_page: str, new_page: str, maps) -> None:
    try:
        keys = tuple(block.keys())
    except Exception:
        return
    flat = {}
    for mapping in maps.values():
        flat.update(mapping)
    for key in keys:
        if not str(key).startswith("bmanga_"):
            continue
        if key in {SOURCE_PAGE_PROP, SOURCE_PAGES_PROP, "bmanga_title"}:
            continue
        value = block.get(key)
        if not isinstance(value, str):
            continue
        block[key] = _remap_string(value, old_page, new_page, flat)


def _remap_string(value: str, old_page: str, new_page: str, flat: Mapping[str, str]) -> str:
    if value in flat:
        return flat[value]
    if value == old_page:
        return new_page
    prefix = f"{old_page}:"
    if value.startswith(prefix):
        tail = value[len(prefix):]
        return f"{new_page}:{flat.get(tail, tail)}"
    if value.startswith("{") or value.startswith("["):
        try:
            parsed = json.loads(value)
        except Exception:
            return value
        return json.dumps(_remap_json_value(parsed, old_page, new_page, flat), ensure_ascii=False)
    return value


def _remap_json_value(value, old_page: str, new_page: str, flat):
    if isinstance(value, str):
        return _remap_string(value, old_page, new_page, flat)
    if isinstance(value, list):
        return [_remap_json_value(item, old_page, new_page, flat) for item in value]
    if isinstance(value, dict):
        return {
            _remap_string(str(key), old_page, new_page, flat): _remap_json_value(item, old_page, new_page, flat)
            for key, item in value.items()
        }
    return value


def _load_links(scene) -> dict[str, str]:
    try:
        value = json.loads(str(scene.get(LINK_PROP, "") or "{}"))
    except Exception:
        value = {}
    return {str(key): str(group) for key, group in value.items()} if isinstance(value, dict) else {}


def _merge_link_maps(first_scene, second_scene, maps, first_id, second_id, target_id):
    merged = {}
    source_links = {
        first_id: _load_links(first_scene),
        second_id: _load_links(second_scene),
    }
    reserved_groups = {group for links in source_links.values() for group in links.values()}
    used_groups = set()
    group_maps: dict[str, dict[str, str]] = {}
    for scene, source_id in ((first_scene, first_id), (second_scene, second_id)):
        source_group_map = group_maps.setdefault(source_id, {})
        for group in sorted(set(source_links[source_id].values())):
            mapped_group = group
            if mapped_group in used_groups:
                salt = 0
                while True:
                    salt += 1
                    candidate = "layer_link_" + uuid.uuid5(
                        uuid.NAMESPACE_URL, f"spread|{source_id}|{group}|{salt}"
                    ).hex
                    if candidate not in used_groups and candidate not in reserved_groups:
                        mapped_group = candidate
                        break
            source_group_map[group] = mapped_group
            used_groups.add(mapped_group)
        for uid, group in source_links[source_id].items():
            mapped = _remap_uid(uid, source_id, target_id, maps.get(source_id, {}))
            mapped_group = source_group_map[group]
            if mapped in merged and merged[mapped] != mapped_group:
                raise SpreadContentError(f"リンクUIDが衝突しています: {mapped}")
            merged[mapped] = mapped_group
    return merged, group_maps


def _remap_uid(uid: str, old_page: str, new_page: str, maps) -> str:
    parts = str(uid).split(":")
    if len(parts) < 2:
        return uid
    kind = parts[0]
    kind_map = maps.get(kind, {})
    if kind == "layer_folder":
        kind_map = maps.get("folder", kind_map)
    if kind in {"gp", "effect", "layer_folder", "raster", "image", "image_path", "fill"}:
        parts[1] = kind_map.get(parts[1], parts[1])
    elif kind in {"balloon", "text"} and len(parts) == 3:
        if parts[1] == old_page:
            parts[1] = new_page
        parts[2] = kind_map.get(parts[2], parts[2])
    elif kind == "page" and parts[1] == old_page:
        parts[1] = new_page
    elif kind in {"coma", "coma_preview", "balloon_group"} and len(parts) >= 3:
        if parts[1] == old_page:
            parts[1] = new_page
        target_kind = "coma" if kind.startswith("coma") else "balloon"
        mapping = maps.get(target_kind, {})
        parts[2] = mapping.get(parts[2], parts[2])
    return ":".join(parts)


def _apply_scene_metadata(scene, page_id: str, request: Mapping[str, Any]) -> None:
    schema = _runtime_module("io.schema")
    work = scene.bmanga_work
    schema.work_from_dict(work, dict(request["work_data"]))
    schema.pages_from_dict(work, dict(request["pages_data"]))
    page = next((entry for entry in work.pages if str(entry.id) == page_id), None)
    if page is None:
        raise SpreadContentError(f"{page_id} のページ情報がありません")
    schema.page_from_dict(page, dict(request["page_data"]))
    work.work_dir = str(request.get("work_dir", work.work_dir))
    work.loaded = True
    scene.bmanga_current_page_id = page_id


def _worker_split(page_id: str, source_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    import bpy

    scene = _open_blend(source_path)
    spread_id = str(request["spread_id"])
    source_id = str(request["source_page_id"])
    root = _find_page_collection(scene, spread_id)
    if root is None:
        raise SpreadContentError("見開きページCollectionがありません")
    _filter_tree_for_source(root, source_id, request.get("source_memberships", {}))
    reverse_maps = _normalize_kind_maps(request.get("reverse_id_maps", {}))
    _remap_tree(root, spread_id, page_id, reverse_maps)
    _clear_source_offsets(root)
    root.name = page_id
    root["bmanga_kind"] = "page"
    root["bmanga_id"] = page_id
    root[SOURCE_PAGE_PROP] = source_id
    root[SOURCE_PAGES_PROP] = json.dumps([source_id], separators=(",", ":"))
    _apply_scene_metadata(scene, page_id, request)
    _sync_balloon_transforms_from_metadata(scene, page_id)
    links = {}
    memberships = request.get("source_memberships", {})
    reverse_groups = request.get("reverse_link_group_map", {})
    if not isinstance(reverse_groups, Mapping):
        reverse_groups = {}
    for uid, group in _load_links(scene).items():
        owner = _uid_source(uid, memberships)
        if owner and owner != source_id:
            continue
        mapped = _remap_uid(uid, spread_id, page_id, reverse_maps)
        if _uid_belongs_to_scene(mapped, scene, page_id):
            links[mapped] = str(reverse_groups.get(group, group))
    scene[LINK_PROP] = json.dumps(links, ensure_ascii=False, separators=(",", ":"))
    output = Path(request["output_path"])
    bpy.ops.wm.save_as_mainfile(filepath=str(output), compress=False)
    _open_blend(output)
    _validate_worker_scene(page_id, expected_sources={source_id})
    return {"operation": "split", "pageId": page_id}


def _filter_tree_for_source(root, source_id: str, memberships) -> None:
    import bpy

    spread_id = str(root.get("bmanga_id", "") or "")
    children = tuple(root.children)
    child_sources = [(child, _source_for_block(child, memberships)) for child in children]
    for child, marker in child_sources:
        if not marker:
            raise SpreadContentError(
                f"所属元を確認できないCollectionがあります: {child.name}。解除を中止しました"
            )
        if marker != source_id:
            root.children.unlink(child)
            if child.users == 0:
                bpy.data.collections.remove(child)
    for obj in tuple(root.objects):
        if not _is_regenerated_page_helper(obj, spread_id):
            continue
        root.objects.unlink(obj)
        if obj.users == 0:
            data = obj.data
            bpy.data.objects.remove(obj)
            _remove_orphan_data(data)
    objects = tuple(root.objects)
    object_sources = [(obj, _source_for_block(obj, memberships)) for obj in objects]
    for obj, marker in object_sources:
        if not marker:
            raise SpreadContentError(
                f"所属元を確認できないレイヤーがあります: {obj.name}。解除を中止しました"
            )
        if marker != source_id:
            root.objects.unlink(obj)
            if obj.users == 0:
                data = obj.data
                bpy.data.objects.remove(obj)
                _remove_orphan_data(data)


def _sync_balloon_transforms_from_metadata(scene, page_id: str) -> None:
    """Move preserved editable balloon curves to the transformed metadata position."""

    module = _runtime_module("utils.balloon_curve_object")
    work = scene.bmanga_work
    page = next((entry for entry in work.pages if str(entry.id) == page_id), None)
    if page is None:
        raise SpreadContentError(f"{page_id} のフキダシ位置を同期できません")
    for entry in page.balloons:
        if module.sync_balloon_object_transform_only(scene, work, page, entry):
            continue
        if module.ensure_balloon_curve_object(scene=scene, entry=entry, page=page) is None:
            raise SpreadContentError(
                f"フキダシ実体を同期できません: {str(getattr(entry, 'id', '') or '')}"
            )


def _source_for_block(block, memberships) -> str:
    marker = str(block.get(SOURCE_PAGE_PROP, "") or "")
    if marker:
        return marker if not isinstance(memberships, Mapping) or marker in memberships else ""
    parent = getattr(block, "parent", None)
    if parent is not None:
        parent_source = _source_for_block(parent, memberships)
        if parent_source:
            return parent_source
    kind = str(block.get("bmanga_kind", "") or "")
    stable_id = str(block.get("bmanga_id", "") or "")
    if kind == "text" and ":" in stable_id:
        stable_id = stable_id.split(":", 1)[1]
    matches = []
    for source_id, kind_values in memberships.items() if isinstance(memberships, Mapping) else ():
        values = kind_values.get(kind, ()) if isinstance(kind_values, Mapping) else ()
        if stable_id and stable_id in values:
            matches.append(str(source_id))
    if len(matches) == 1:
        return matches[0]
    try:
        reference_values = {
            str(block.get(key, "") or "")
            for key in block.keys()
            if str(key).startswith("bmanga_") and isinstance(block.get(key), str)
        }
    except Exception:
        reference_values = set()
    reference_values.discard("")
    reference_sources = set()
    for source_id, kind_values in memberships.items() if isinstance(memberships, Mapping) else ():
        if not isinstance(kind_values, Mapping):
            continue
        if any(
            value in values
            for values in kind_values.values()
            for value in reference_values
        ):
            reference_sources.add(str(source_id))
    return next(iter(reference_sources)) if len(reference_sources) == 1 else ""


def _is_regenerated_page_helper(block, spread_id: str) -> bool:
    if not spread_id:
        return False
    return any(
        str(block.get(prop, "") or "") == spread_id
        for prop in (
            "bmanga_paper_bg_page_id",
            "bmanga_paper_guide_page_id",
            "bmanga_page_mask_volume_owner_id",
            "bmanga_page_preview_page_id",
        )
    )


def _remove_orphan_data(data) -> None:
    import bpy

    if data is None or data.users:
        return
    for collection in (
        bpy.data.meshes,
        bpy.data.curves,
        getattr(bpy.data, "grease_pencils", ()),
        bpy.data.cameras,
    ):
        try:
            if data.name in collection:
                collection.remove(data)
                return
        except Exception:
            continue


def _uid_belongs_to_scene(uid: str, scene, page_id: str) -> bool:
    if f":{page_id}:" in uid:
        return True
    if uid.startswith(("gp:", "effect:")):
        stable = uid.split(":", 1)[1]
        return any(str(obj.get("bmanga_id", "") or "") == stable for obj in scene.objects)
    return True


def _uid_source(uid: str, memberships) -> str:
    parts = str(uid).split(":")
    if len(parts) < 2 or not isinstance(memberships, Mapping):
        return ""
    kind = parts[0]
    if kind in {"page", "outside_group"}:
        return ""
    membership_kind = {
        "layer_folder": "folder",
        "coma_preview": "coma",
        "balloon_group": "balloon",
    }.get(kind, kind)
    if kind in {"balloon", "text", "coma", "coma_preview", "balloon_group"}:
        stable_id = parts[2] if len(parts) >= 3 else ""
    else:
        stable_id = parts[1]
    matches = []
    for source_id, kind_values in memberships.items():
        values = kind_values.get(membership_kind, ()) if isinstance(kind_values, Mapping) else ()
        if stable_id and stable_id in values:
            matches.append(str(source_id))
    if len(matches) > 1:
        raise SpreadContentError(f"リンクUIDの所属元が重複しています: {uid}")
    if matches:
        return matches[0]
    owned_kinds = {
        "gp", "effect", "layer_folder", "raster", "image",
        "balloon", "text", "coma", "coma_preview", "balloon_group",
    }
    outside_entry = kind in {"balloon", "text"} and len(parts) >= 3 and parts[1] == "__outside__"
    if kind in owned_kinds and not outside_entry:
        raise SpreadContentError(f"リンクUIDの所属元を確認できません: {uid}")
    return ""


def _unlink_collection(parent, target) -> bool:
    for child in tuple(parent.children):
        if child == target:
            parent.children.unlink(child)
            return True
        if _unlink_collection(child, target):
            return True
    return False


def _validate_worker_scene(page_id: str, *, expected_sources: set[str]) -> None:
    import bpy

    root = _find_page_collection(bpy.context.scene, page_id)
    if root is None:
        raise SpreadContentError(f"保存後に {page_id} のページCollectionがありません")
    found = {
        str(block.get(SOURCE_PAGE_PROP, "") or "")
        for block in _iter_tree(root)
        if str(block.get(SOURCE_PAGE_PROP, "") or "")
    }
    found.update(
        str(obj.get(SOURCE_PAGE_PROP, "") or "")
        for obj in _tree_objects(root)
        if str(obj.get(SOURCE_PAGE_PROP, "") or "")
    )
    try:
        listed = json.loads(str(root.get(SOURCE_PAGES_PROP, "[]") or "[]"))
        if isinstance(listed, list):
            found.update(str(value) for value in listed if value)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    if found != expected_sources:
        raise SpreadContentError(
            "ページ実内容の所属元を保存できませんでした: "
            f"期待={sorted(expected_sources)} / 実際={sorted(found)}"
        )


def _worker_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--page-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--worker-token", required=True)
    args = parser.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])
    output = Path(args.output)
    try:
        inherited = str(os.environ.get(_WORKER_TOKEN_ENV, "") or "")
        if not inherited or not secrets.compare_digest(inherited, args.worker_token):
            raise SpreadContentError("見開きワーカーの所有トークンを確認できません")
        os.environ[_WORKER_CLAIM_ENV] = args.worker_token
        _ensure_runtime()
        payload = _worker_convert(args.page_id, Path(args.page_path), Path(args.request))
        _write_json(output, {"ok": True, **payload})
    except BaseException as exc:
        import traceback

        _write_json(output, {"ok": False, "error": f"{exc}\n{traceback.format_exc()}"})
        raise


if __name__ == "__main__":
    _worker_main(sys.argv)


__all__ = [
    "MANIFEST_NAME",
    "SOURCE_PAGE_PROP",
    "SpreadContentError",
    "merge_page_content",
    "read_manifest",
    "split_page_content",
]
