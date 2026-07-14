"""Blender実体を使う詳細データ形式版1へのページ単位変換。

``project_content_migration`` の Inspector / Converter / Validator 契約へそのまま
渡せる公開関数を提供する。各公開関数は指定された ``page.blend`` だけを、別の
Blenderプロセスで開く。検査は保存せず、変換はオーケストレーターが用意した
``staged_path`` だけへ保存する。

旧ポインタUIDは値から対象を推測しない。旧ファイル内に
``bmanga_detail_legacy_uid_map`` が明示保存されていて、記述された種別・Object・
内部レイヤーを一意に照合できる場合だけ正規UIDへ変換する。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping
import uuid

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_worker_runtime_module():
    if __package__:
        return importlib.import_module(
            f"{__package__}.detail_data_blender_worker_runtime"
        )
    name = "bmanga_detail_data_blender_worker_runtime"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "io" / "detail_data_blender_worker_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_WORKER_RUNTIME = _load_worker_runtime_module()
LEGACY_GP_OBJECT = "bmanga_master_sketch"
LEGACY_EFFECT_OBJECT = "BManga_EffectLines"
EFFECT_META_PROP = "bmanga_effect_line_meta"
LINK_PROP = "bmanga_layer_link_groups"
LEGACY_UID_MAP_PROP = "bmanga_detail_legacy_uid_map"
INTERNAL_MASK_LAYER = "__bmanga_mask"
_ID_NAMESPACE = uuid.UUID("bf0ef436-4ec9-49cb-b6ee-b09c973047b3")
_RUNTIME_PACKAGE = (__package__ or "").split(".", 1)[0]


def inspect_page(page_id: str, page_path: str | Path):
    """指定ページを別Blenderで読取専用検査し、``PageInspection`` を返す。"""
    pcm = _runtime_module("io.project_content_migration")
    payload = _run_worker("inspect", page_id, Path(page_path))
    raw = payload["inspection"]
    issues = tuple(pcm.MigrationIssue(**item) for item in raw.get("issues", ()))
    return pcm.PageInspection(
        estimated_output_bytes=int(raw.get("estimated_output_bytes", 0)),
        issues=issues,
        facts=dict(raw.get("facts", {})),
    )


def convert_page(task) -> None:
    """``PageConversionTask.staged_path`` だけを別Blenderで変換・保存する。"""
    request = {"inspection_facts": dict(getattr(task, "inspection_facts", {}) or {})}
    _run_worker("convert", str(task.page_id), Path(task.staged_path), request=request)


def validate_page(page_id: str, page_path: str | Path) -> bool:
    """保存済みページを別Blenderで再読込し、形式版1の不変条件を検証する。"""
    _run_worker("validate", page_id, Path(page_path))
    return True


def callbacks():
    """``(Inspector, Converter, Validator)`` を返す。"""
    return inspect_page, convert_page, validate_page


def _run_worker(
    mode: str,
    page_id: str,
    page_path: Path,
    *,
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _WORKER_RUNTIME.run_worker(
        bpy.app.binary_path,
        Path(__file__),
        mode,
        page_id,
        page_path,
        request=request,
    )


def _ensure_worker_runtime() -> None:
    global _RUNTIME_PACKAGE
    if _RUNTIME_PACKAGE:
        return
    package_name = "bmanga_detail_migration_runtime"
    existing = sys.modules.get(package_name)
    if existing is None:
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
    _ensure_worker_runtime()
    return importlib.import_module(f"{_RUNTIME_PACKAGE}.{relative_name}")


def _open_page(path: Path) -> bpy.types.Scene:
    if not path.is_file():
        raise FileNotFoundError(path)
    bpy.ops.wm.open_mainfile(filepath=str(path.resolve()), load_ui=False)
    return bpy.context.scene


def _issue(code: str, page_id: str, path: Path, message: str, **extra) -> dict[str, str]:
    item = {
        "code": str(code),
        "page_id": str(page_id),
        "page_path": str(path),
        "message": str(message),
        "raw_uid": "",
        "link_group": "",
    }
    item.update({key: str(value) for key, value in extra.items()})
    return item


def _stable_id(page_id: str, kind: str, source_object: str, node_path: tuple[str, ...]) -> str:
    seed = "|".join(("detail-v1", page_id, kind, source_object, *node_path))
    return f"{kind}_{uuid.uuid5(_ID_NAMESPACE, seed).hex[:16]}"


def _parent_spec(raw_key: str, page_id: str) -> tuple[str, str]:
    key = str(raw_key or "")
    if key == "__outside__":
        return "outside", ""
    if not key:
        return "page", str(page_id)
    return ("coma", key) if ":" in key else ("page", key)


def _walk_legacy_gp(page_id: str, obj, kind: str):
    gp_utils = _runtime_module("utils.gpencil")
    gp_parent = _runtime_module("utils.gp_layer_parenting")
    groups: list[tuple[dict[str, Any], Any]] = []
    layers: list[tuple[dict[str, Any], Any]] = []
    order = 0

    def walk(nodes, parent_path: tuple[str, ...], parent_group_path: tuple[str, ...]):
        nonlocal order
        for index, node in enumerate(list(nodes)):
            token = f"{index}:{getattr(node, 'name', '')}"
            path = (*parent_path, token)
            order += 1
            if gp_utils.is_layer_group(node):
                record = _group_record(page_id, obj, node, kind, path, parent_group_path, order)
                groups.append((record, node))
                walk(getattr(node, "children", ()), path, path)
                continue
            if str(getattr(node, "name", "") or "") == INTERNAL_MASK_LAYER:
                continue
            parent_kind, parent_key = _parent_spec(gp_parent.parent_key(node), page_id)
            record = {
                "kind": kind,
                "source_object": str(obj.name),
                "source_layer": str(node.name),
                "path": list(path),
                "group_path": list(parent_group_path),
                "stable_id": _stable_id(page_id, kind, str(obj.name), path),
                "title": str(node.name),
                "parent_kind": parent_kind,
                "parent_key": parent_key,
                "z_index": 200 + order * 10,
            }
            layers.append((record, node))

    nodes = getattr(getattr(obj, "data", None), "root_nodes", None)
    walk(nodes if nodes is not None else getattr(obj.data, "layers", ()), (), ())
    _finish_group_records(page_id, groups, layers)
    return groups, layers


def _group_record(page_id, obj, node, kind, path, parent_group_path, order):
    return {
        "kind": "layer_folder",
        "source_kind": kind,
        "source_object": str(obj.name),
        "source_group": str(node.name),
        "path": list(path),
        "parent_group_path": list(parent_group_path),
        "stable_id": _stable_id(page_id, "layer_folder", str(obj.name), path),
        "title": str(node.name),
        "expanded": bool(getattr(node, "is_expanded", True)),
        "hidden": bool(getattr(node, "hide", False)),
        "locked": bool(getattr(node, "lock", False)),
        "z_index": 200 + order * 10,
    }


def _finish_group_records(page_id, groups, layers) -> None:
    folder_by_path = {tuple(record["path"]): record for record, _node in groups}
    for record, _node in groups:
        path = tuple(record["path"])
        descendants = [
            layer_record
            for layer_record, _layer in layers
            if tuple(layer_record["group_path"][: len(path)]) == path
        ]
        parents = {(item["parent_kind"], item["parent_key"]) for item in descendants}
        record["semantic_parents"] = [list(item) for item in sorted(parents)]
        parent_path = tuple(record["parent_group_path"])
        if parent_path:
            record["parent_kind"] = "folder"
            record["parent_key"] = folder_by_path[parent_path]["stable_id"]
        elif len(parents) == 1:
            record["parent_kind"], record["parent_key"] = next(iter(parents))
        else:
            record["parent_kind"], record["parent_key"] = "page", page_id
    folder_ids = {tuple(record["path"]): record["stable_id"] for record, _ in groups}
    for record, _node in layers:
        record["folder_id"] = folder_ids.get(tuple(record["group_path"]), "")


def _load_effect_meta(obj) -> tuple[dict[str, Any], str]:
    try:
        raw = str(obj.data.get(EFFECT_META_PROP, "") or "")
        value = json.loads(raw) if raw else {}
    except Exception as exc:
        return {}, f"効果線設定を読めません: {exc}"
    if not isinstance(value, dict):
        return {}, "効果線設定のルートがオブジェクトではありません"
    return value, ""


def _analyze_scene(page_id: str, path: Path):
    gp_helper = _runtime_module("utils.gp_object_layer")
    layer_model = _runtime_module("utils.layer_object_model")
    object_naming = _runtime_module("utils.object_naming")
    issues: list[dict[str, str]] = []
    model: dict[str, Any] = {"gp": [], "effect": [], "groups": []}
    master = bpy.data.objects.get(LEGACY_GP_OBJECT)
    effect = bpy.data.objects.get(LEGACY_EFFECT_OBJECT)
    if _legacy_source_is_readable(page_id, path, master, LEGACY_GP_OBJECT, issues):
        groups, layers = _walk_legacy_gp(page_id, master, "gp")
        model["master"], model["groups"], model["gp"] = master, groups, layers
        _inspect_layer_masks(page_id, path, gp_helper, layers, issues)
        _inspect_group_parents(page_id, path, groups, issues)
    if _legacy_source_is_readable(page_id, path, effect, LEGACY_EFFECT_OBJECT, issues):
        groups, layers = _walk_legacy_gp(page_id, effect, "effect")
        model["effect_source"], model["effect"] = effect, layers
        _inspect_legacy_effect(page_id, path, effect, groups, layers, issues)
    _inspect_current_objects(page_id, path, layer_model, issues)
    records = _plain_records(model)
    _inspect_target_collisions(page_id, path, layer_model, records, issues)
    _inspect_parent_targets(page_id, path, records, issues)
    uid_map, link_issues = _analyze_link_map(page_id, path, records)
    issues.extend(link_issues)
    saved_links, saved_link_error = _json_id_property(bpy.context.scene, LINK_PROP)
    if saved_link_error:
        saved_links = {}
    manifest_api = _runtime_module("io.detail_data_migration_manifest")
    canonical_links = manifest_api.canonical_link_map(saved_links, uid_map)
    current_ids = _current_managed_ids(layer_model)
    existing_folders = manifest_api.capture_existing_folders(
        bpy.context.scene, object_naming
    )
    page_version = _runtime_module("utils.layer_uid").scene_detail_data_version(
        bpy.context.scene
    )
    facts = manifest_api.build_inspection_facts(
        page_id,
        page_version,
        records,
        uid_map,
        current_ids,
        existing_folders,
        canonical_links,
    )
    model["facts"] = facts
    return facts, issues, model


def _legacy_source_is_readable(page_id, path, obj, expected_name, issues) -> bool:
    if obj is None:
        return False
    if getattr(obj, "type", "") != "GREASEPENCIL":
        issues.append(_issue(
            "invalid_legacy_object", page_id, path, f"{expected_name} が手描き実体ではありません"
        ))
        return False
    data = getattr(obj, "data", None)
    if getattr(obj, "library", None) is not None or getattr(data, "library", None) is not None:
        issues.append(_issue(
            "linked_legacy_object", page_id, path, f"{expected_name} が外部ファイル参照です"
        ))
    if int(getattr(data, "users", 0) or 0) != 1:
        issues.append(_issue(
            "shared_legacy_data", page_id, path, f"{expected_name} の描画データが他Objectと共有されています"
        ))
    if getattr(obj, "animation_data", None) is not None or len(getattr(obj, "constraints", ())) > 0:
        issues.append(_issue(
            "unsupported_legacy_animation",
            page_id,
            path,
            f"{expected_name} に変換できないObjectアニメーションまたは制約があります",
        ))
    return True


def _inspect_layer_masks(page_id, path, helper, layers, issues) -> None:
    for _record, layer in layers:
        message = helper.legacy_layer_migration_issue(layer)
        if message:
            issues.append(_issue("unsupported_gp_mask", page_id, path, message))


def _inspect_group_parents(page_id, path, groups, issues) -> None:
    for record, _node in groups:
        parents = record.get("semantic_parents", [])
        if len(parents) > 1:
            issues.append(_issue(
                "ambiguous_gp_group_parent",
                page_id,
                path,
                f"内部フォルダー「{record['title']}」が複数の所属先を含みます",
            ))


def _inspect_legacy_effect(page_id, path, obj, groups, layers, issues) -> None:
    helper = _runtime_module("utils.gp_object_layer")
    if groups:
        issues.append(_issue(
            "unsupported_effect_group", page_id, path, "旧効果線に内部フォルダーがあります"
        ))
    _inspect_layer_masks(page_id, path, helper, layers, issues)
    meta, error = _load_effect_meta(obj)
    if error:
        issues.append(_issue("invalid_effect_meta", page_id, path, error))
        return
    names = {record["source_layer"] for record, _layer in layers}
    if set(meta) != names:
        issues.append(_issue(
            "effect_meta_mismatch", page_id, path, "旧効果線の内部レイヤーと設定が一致しません"
        ))
    for record, _layer in layers:
        entry = meta.get(record["source_layer"])
        if not _valid_effect_entry(entry):
            issues.append(_issue(
                "invalid_effect_meta", page_id, path, f"効果線「{record['title']}」の設定が不足しています"
            ))
        record["effect_meta"] = entry if isinstance(entry, dict) else {}


def _valid_effect_entry(entry) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("params"), dict):
        return False
    try:
        for key in ("x", "y", "w", "h"):
            float(entry[key])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _inspect_current_objects(page_id, path, layer_model, issues) -> None:
    seen: set[tuple[str, str]] = set()
    for obj in layer_model.iter_layer_objects():
        kind = layer_model.layer_kind(obj)
        stable_id = layer_model.stable_id(obj)
        identity = kind, stable_id
        valid, message = layer_model.validate_single_content_layer(obj)
        if not stable_id or identity in seen or not valid:
            issues.append(_issue(
                "invalid_managed_layer", page_id, path, message or "管理レイヤーの安定IDが重複しています"
            ))
        seen.add(identity)


def _inspect_target_collisions(page_id, path, layer_model, records, issues) -> None:
    targets = {
        (record["kind"], record["stable_id"])
        for key in ("gp", "effect")
        for record in records[key]
    }
    for obj in layer_model.iter_layer_objects():
        identity = layer_model.layer_kind(obj), layer_model.stable_id(obj)
        if identity in targets:
            issues.append(_issue(
                "migration_target_collision",
                page_id,
                path,
                "移行先と同じ安定IDの管理レイヤーが既にあります",
            ))


def _inspect_parent_targets(page_id, path, records, issues) -> None:
    on = _runtime_module("utils.object_naming")
    targets = {
        (record["parent_kind"], record["parent_key"])
        for key in ("groups", "gp", "effect")
        for record in records[key]
        if record.get("parent_kind") in {"page", "coma"}
    }
    for parent_kind, parent_key in sorted(targets):
        if on.find_collection_by_bmanga_id(parent_key, kind=parent_kind) is None:
            issues.append(_issue(
                "missing_parent_collection",
                page_id,
                path,
                f"所属先Collectionがありません: {parent_key}",
            ))


def _plain_records(model) -> dict[str, list[dict[str, Any]]]:
    return {
        key: [dict(record) for record, _node in model.get(key, [])]
        for key in ("groups", "gp", "effect")
    }


def _current_managed_ids(layer_model) -> dict[str, list[str]]:
    return {
        kind: sorted(
            str(layer_model.stable_id(obj))
            for obj in layer_model.iter_layer_objects(kind)
        )
        for kind in ("gp", "effect")
    }


def _analyze_link_map(page_id: str, path: Path, records):
    layer_uid = _runtime_module("utils.layer_uid")
    issues: list[dict[str, str]] = []
    mapping, error = _json_id_property(bpy.context.scene, LINK_PROP)
    evidence, evidence_error = _json_id_property(bpy.context.scene, LEGACY_UID_MAP_PROP)
    if error:
        issues.append(_issue("invalid_link_map", page_id, path, error))
        return {}, issues
    if evidence_error:
        issues.append(_issue("invalid_uid_evidence", page_id, path, evidence_error))
        evidence = {}
    uid_map: dict[str, str] = {}
    groups_by_uid: dict[str, str] = {}
    for raw_uid, link_group in mapping.items():
        canonical = str(raw_uid) if layer_uid.is_valid_uid(raw_uid) else ""
        if not canonical:
            canonical = _resolve_uid_evidence(evidence.get(raw_uid), records)
        if not canonical:
            issues.append(_issue(
                "unresolved_pointer_uid" if layer_uid.is_legacy_pointer_uid(raw_uid) else "invalid_saved_uid",
                page_id,
                path,
                "保存後に逆引きできない旧リンクUIDがあります",
                raw_uid=raw_uid,
                link_group=link_group,
            ))
            continue
        previous = groups_by_uid.get(canonical)
        if previous and previous != str(link_group):
            issues.append(_issue(
                "conflicting_link_uid", page_id, path, "同じ移行先UIDに異なるリンク群があります",
                raw_uid=raw_uid, link_group=link_group,
            ))
            continue
        uid_map[str(raw_uid)] = canonical
        groups_by_uid[canonical] = str(link_group)
    return uid_map, issues


def _json_id_property(owner, key: str) -> tuple[dict[str, Any], str]:
    raw = str(owner.get(key, "") or "")
    if not raw:
        return {}, ""
    try:
        value = json.loads(raw)
    except Exception as exc:
        return {}, f"{key} を読めません: {exc}"
    if not isinstance(value, dict):
        return {}, f"{key} のルートがオブジェクトではありません"
    return {str(item): value[item] for item in value}, ""


def _resolve_uid_evidence(descriptor, records) -> str:
    if not isinstance(descriptor, Mapping):
        return ""
    kind = str(descriptor.get("kind", "") or "")
    source_object = str(descriptor.get("object", "") or "")
    source_name = str(descriptor.get("layer", descriptor.get("group", "")) or "")
    source_key = "source_group" if kind == "gp_folder" else "source_layer"
    record_key = "groups" if kind == "gp_folder" else kind
    candidates = [
        item for item in records.get(record_key, [])
        if item.get("source_object") == source_object and item.get(source_key) == source_name
    ]
    if len(candidates) != 1:
        return ""
    target_kind = "layer_folder" if kind == "gp_folder" else kind
    layer_uid = _runtime_module("utils.layer_uid")
    try:
        return layer_uid.make_managed_uid(target_kind, candidates[0]["stable_id"])
    except (TypeError, ValueError):
        # 不正な旧対応表は空UIDとして返し、呼出側で事前停止issueへ変換する。
        return ""


def _convert_scene(page_id: str, path: Path, model, expected_manifest) -> None:
    _create_generic_folders(model["groups"])
    for record, layer in model["gp"]:
        _clone_gp_layer(model["master"], record, layer)
    for record, layer in model["effect"]:
        _clone_effect_layer(model["effect_source"], record, layer)
    _normalize_current_gp_materials()
    _rewrite_link_map(model["facts"]["uidMap"])
    _remove_legacy_object(model.get("master"))
    _remove_legacy_object(model.get("effect_source"))
    _runtime_module("utils.layer_uid").stamp_scene_detail_data_version(
        bpy.context.scene
    )
    _runtime_module("io.detail_data_migration_manifest").store_manifest(
        bpy.context.scene, expected_manifest
    )
    _validate_scene(page_id, path)


def _normalize_current_gp_materials() -> None:
    """旧集約由来・既存個別Objectの双方を専用Materialへ正規化する。"""

    layer_model = _runtime_module("utils.layer_object_model")
    gp_utils = _runtime_module("utils.gpencil")
    for obj in layer_model.iter_layer_objects("gp"):
        gp_utils.ensure_unique_object_materials(obj)


def _create_generic_folders(groups) -> None:
    work = getattr(bpy.context.scene, "bmanga_work", None)
    if work is None:
        raise RuntimeError("作品情報がページ用blendファイルにありません")
    outliner = _runtime_module("utils.outliner_model")
    # _walk_legacy_gp は親→子の先行順を返す。この順がレイヤー一覧の正本。
    for record, group in groups:
        entry = next((item for item in work.layer_folders if item.id == record["stable_id"]), None)
        if entry is None:
            entry = work.layer_folders.add()
        entry.id = record["stable_id"]
        entry.title = record["title"]
        entry.parent_key = record["parent_key"]
        entry.expanded = record["expanded"]
        entry.visible = not bool(record["hidden"])
        entry.locked = bool(record["locked"])
        coll = outliner.ensure_folder_collection(
            bpy.context.scene,
            record["stable_id"],
            record["title"],
            record["parent_kind"],
            record["parent_key"],
            record["z_index"],
        )
        if coll is not None:
            coll.hide_viewport = bool(record["hidden"])
            coll.hide_render = bool(record["hidden"])
            if hasattr(coll, "hide_select"):
                coll.hide_select = bool(record["locked"])


def _clone_gp_layer(source_obj, record, source_layer):
    gp_parent = _runtime_module("utils.gp_layer_parenting")
    layer_model = _runtime_module("utils.layer_object_model")
    clone = _clone_legacy_layer_safe(source_obj, source_layer, record)
    content = layer_model.content_layer(clone)
    gp_parent.set_parent_key(content, record["parent_key"])
    layer_model.initialize_user_state(clone)
    return clone


def _clone_legacy_layer_safe(source_obj, source_layer, record):
    """グループ操作後の無効RNA参照を保持せず、1内部レイヤーを複製する。"""
    helper = _runtime_module("utils.gp_object_layer")
    layer_sync = _runtime_module("utils.layer_object_sync")
    source_matrix = _stored_world_matrix(source_obj)
    source_name = str(source_layer.name)
    clone = source_obj.copy()
    clone.data = source_obj.data.copy()
    clone.animation_data_clear()
    _isolate_copied_layer(clone.data, source_name)
    # stamp_layer_object() also runs the current page/coma mask synchronizer.
    # When its target mesh is unavailable it removes __bmanga_mask, which must
    # not erase a legacy mask during migration.  Preserve the isolated GP data
    # only when such a source mask actually exists; otherwise keep any mask the
    # current synchronizer legitimately creates.
    preserved_mask_data = (
        clone.data.copy() if clone.data.layers.get(INTERNAL_MASK_LAYER) is not None else None
    )
    stamped_data = clone.data
    layer_sync.stamp_layer_object(
        clone,
        kind="gp",
        bmanga_id=record["stable_id"],
        title=record["title"],
        z_index=record["z_index"],
        parent_kind=record["parent_kind"],
        parent_key=record["parent_key"],
        folder_id=record.get("folder_id", ""),
        scene=bpy.context.scene,
    )
    if preserved_mask_data is not None:
        clone.data = preserved_mask_data
        _remove_gp_data_if_orphan(stamped_data)
    content = clone.data.layers.get("content")
    target_matrix = _stored_world_matrix(clone)
    transform = target_matrix.inverted_safe() @ source_matrix
    helper._transform_layer_points(content, transform)
    internal_mask = clone.data.layers.get(INTERNAL_MASK_LAYER)
    if internal_mask is not None:
        helper._transform_layer_points(internal_mask, transform)
    clone["bmanga_user_visible"] = not bool(getattr(source_layer, "hide", False))
    clone["bmanga_user_locked"] = bool(getattr(source_layer, "lock", False))
    # 保存値だけでなくObject・内部contentの実状態も同時に揃える。
    layer_model = _runtime_module("utils.layer_object_model")
    layer_model.set_user_visible(clone, clone["bmanga_user_visible"])
    layer_model.set_user_locked(clone, clone["bmanga_user_locked"])
    _runtime_module("utils.gpencil").ensure_unique_object_materials(clone)
    return clone


def _stored_world_matrix(obj):
    """非表示Collection内でも保存値だけから確定するworld行列を返す。"""
    basis = obj.matrix_basis.copy()
    parent = getattr(obj, "parent", None)
    if parent is None:
        return basis
    return _stored_world_matrix(parent) @ obj.matrix_parent_inverse @ basis


def _isolate_copied_layer(gp_data, source_name: str) -> None:
    gp_utils = _runtime_module("utils.gpencil")
    layers = gp_data.layers
    if layers.get(source_name) is None:
        raise RuntimeError("移行元の手描きレイヤーを複製できませんでした")
    for name in [str(layer.name) for layer in list(layers)]:
        if name in {source_name, INTERNAL_MASK_LAYER}:
            continue
        current = layers.get(name)
        if current is not None:
            layers.remove(current)
    for name in (source_name, INTERNAL_MASK_LAYER):
        current = layers.get(name)
        if current is not None:
            gp_utils.move_layer_to_group(gp_data, current, None)
    _remove_all_layer_groups(gp_data)
    selected = layers.get(source_name)
    selected.name = "content"
    layers.active = selected


def _remove_all_layer_groups(gp_data) -> None:
    groups = getattr(gp_data, "layer_groups", None)
    if groups is None:
        return
    while len(groups):
        before = len(groups)
        for group in list(groups):
            if len(getattr(group, "children", ())) == 0:
                groups.remove(group)
                break
        if len(groups) >= before:
            raise RuntimeError("内部フォルダーを個別レイヤーから除去できませんでした")


def _clone_effect_layer(source_obj, record, source_layer):
    clone = _clone_gp_layer(source_obj, record, source_layer)
    layer_sync = _runtime_module("utils.layer_object_sync")
    layer_model = _runtime_module("utils.layer_object_model")
    gp_parent = _runtime_module("utils.gp_layer_parenting")
    effect_op = _runtime_module("operators.effect_line_op")
    layer_sync.stamp_layer_object(
        clone,
        kind="effect",
        bmanga_id=record["stable_id"],
        title=record["title"],
        z_index=record["z_index"],
        parent_kind=record["parent_kind"],
        parent_key=record["parent_key"],
        folder_id=record.get("folder_id", ""),
        scene=bpy.context.scene,
    )
    content = layer_model.content_layer(clone)
    gp_parent.set_parent_key(content, record["parent_key"])
    meta = json.loads(json.dumps(record["effect_meta"], ensure_ascii=False))
    clone.data[EFFECT_META_PROP] = json.dumps({"content": meta}, ensure_ascii=False, separators=(",", ":"))
    # The current effect writer builds the display mesh from settings, but it
    # clears the controller's current GP drawing first.  Keep a full data-block
    # copy so every legacy frame/stroke/point attribute remains available after
    # the display and helper objects have been generated.
    generated_data = clone.data
    preserved_data = generated_data.copy()
    bounds = tuple(float(meta[key]) for key in ("x", "y", "w", "h"))
    proxy = effect_op._EffectParamProxy(bpy.context.scene.bmanga_effect_line_params, meta["params"])
    try:
        written = effect_op._write_effect_strokes(
            bpy.context,
            clone,
            content,
            bounds,
            seed=int(meta.get("seed", 0) or 0),
            params_override=proxy,
            propagate_link=False,
            center_xy_mm=_effect_center(meta, bounds),
        )
    finally:
        clone.data = preserved_data
        _remove_gp_data_if_orphan(generated_data)
    if written != 1:
        raise RuntimeError(f"効果線「{record['title']}」を再生成できませんでした")
    content = layer_model.content_layer(clone)
    gp_parent.set_parent_key(content, record["parent_key"])
    visible = not bool(getattr(source_layer, "hide", False))
    locked = bool(getattr(source_layer, "lock", False))
    layer_model.set_user_visible(clone, visible)
    layer_model.set_user_locked(clone, locked)
    return clone


def _effect_center(meta, bounds) -> tuple[float, float]:
    return (
        float(meta.get("center_x", bounds[0] + bounds[2] * 0.5)),
        float(meta.get("center_y", bounds[1] + bounds[3] * 0.5)),
    )


def _rewrite_link_map(uid_map: Mapping[str, str]) -> None:
    scene = bpy.context.scene
    mapping, error = _json_id_property(scene, LINK_PROP)
    if error:
        raise RuntimeError(error)
    rewritten = {str(uid_map.get(raw_uid, raw_uid)): str(group) for raw_uid, group in mapping.items()}
    scene[LINK_PROP] = json.dumps(rewritten, ensure_ascii=False, separators=(",", ":"))
    if LEGACY_UID_MAP_PROP in scene:
        del scene[LEGACY_UID_MAP_PROP]


def _remove_legacy_object(obj) -> None:
    if obj is None:
        return
    data = getattr(obj, "data", None)
    bpy.data.objects.remove(obj, do_unlink=True)
    _remove_gp_data_if_orphan(data)


def _remove_gp_data_if_orphan(data) -> None:
    if data is None or getattr(data, "users", 1) != 0:
        return
    blocks = getattr(bpy.data, "grease_pencils_v3", None) or getattr(
        bpy.data, "grease_pencils", None
    )
    if blocks is not None:
        blocks.remove(data)


def _validate_scene(page_id: str, path: Path) -> dict[str, Any]:
    layer_model = _runtime_module("utils.layer_object_model")
    layer_uid = _runtime_module("utils.layer_uid")
    gp_parent = _runtime_module("utils.gp_layer_parenting")
    effect_object = _runtime_module("utils.effect_line_object")
    outliner = _runtime_module("utils.outliner_model")
    on = _runtime_module("utils.object_naming")
    if layer_uid.scene_detail_data_version(bpy.context.scene) != 1:
        raise AssertionError("ページ用blendファイルの作品データ版が一致しません")
    if bpy.data.objects.get(LEGACY_GP_OBJECT) or bpy.data.objects.get(LEGACY_EFFECT_OBJECT):
        raise AssertionError("旧集約Objectが残っています")
    identities: set[tuple[str, str]] = set()
    gp_material_owners: dict[int, str] = {}
    counts = {"gp": 0, "effect": 0}
    for obj in layer_model.iter_layer_objects():
        kind, stable_id = layer_model.layer_kind(obj), layer_model.stable_id(obj)
        layer_uid.make_managed_uid(kind, stable_id)
        if (kind, stable_id) in identities:
            raise AssertionError("管理レイヤーの安定IDが重複しています")
        identities.add((kind, stable_id))
        valid, message = layer_model.validate_single_content_layer(obj)
        if not valid:
            raise AssertionError(message)
        if len(getattr(obj.data, "layer_groups", ())) != 0:
            raise AssertionError("管理レイヤーに内部フォルダーが残っています")
        content = layer_model.content_layer(obj)
        _validate_layer_user_state(obj, content, kind, layer_model)
        if gp_parent.parent_key(content) != layer_model.parent_key(obj):
            raise AssertionError("手描き内容とObjectの所属が一致しません")
        folder_id = layer_model.folder_id(obj)
        if folder_id and on.find_collection_by_bmanga_id(folder_id, kind="folder") is None:
            raise AssertionError("移行先フォルダーCollectionがありません")
        _validate_object_collection(obj, folder_id, layer_model.parent_key(obj), outliner, on)
        if kind == "gp":
            for material in getattr(getattr(obj, "data", None), "materials", ()):
                if material is None:
                    continue
                pointer = int(material.as_pointer())
                previous = gp_material_owners.get(pointer)
                if previous is not None and previous != stable_id:
                    raise AssertionError("手描きレイヤー間でMaterialが共有されています")
                gp_material_owners[pointer] = stable_id
        if kind == "effect":
            _validate_effect(obj, effect_object)
        counts[kind] += 1
    _validate_link_map(layer_uid)
    manifest_result = _validate_saved_manifest(page_id, layer_model, on)
    return {
        "pageId": page_id,
        "path": str(path),
        **counts,
        "manifest": manifest_result,
    }


def _validate_saved_manifest(page_id: str, layer_model, object_naming):
    manifest_api = _runtime_module("io.detail_data_migration_manifest")
    return manifest_api.validate_manifest(
        bpy.context.scene,
        page_id,
        layer_model,
        object_naming,
        LINK_PROP,
    )


def _validate_layer_user_state(obj, content, kind, layer_model) -> None:
    visible = layer_model.user_visible(obj)
    locked = layer_model.user_locked(obj)
    if hasattr(content, "hide") and bool(content.hide) != (not visible):
        raise AssertionError("レイヤーの保存表示と実表示が一致しません")
    if hasattr(content, "lock") and bool(content.lock) != locked:
        raise AssertionError("レイヤーの保存ロックと実ロックが一致しません")
    if bool(getattr(obj, "hide_select", False)) != locked:
        raise AssertionError("Objectの選択ロックが保存値と一致しません")
    if kind == "gp":
        if bool(getattr(obj, "hide_viewport", False)) != (not visible):
            raise AssertionError("手描きObjectの表示が保存値と一致しません")
        if bool(getattr(obj, "hide_render", False)) != (not visible):
            raise AssertionError("手描きObjectのレンダー表示が保存値と一致しません")


def _validate_object_collection(obj, folder_id, parent_key, outliner, on) -> None:
    collection = outliner.find_managed_parent_collection(obj)
    if collection is None:
        raise AssertionError("管理レイヤーの所属Collectionがありません")
    expected_kind = "folder" if folder_id else (
        "coma" if ":" in parent_key else ("page" if parent_key else "outside")
    )
    expected_id = folder_id or parent_key
    if on.get_kind(collection) != expected_kind:
        raise AssertionError("管理レイヤーの所属Collection種別が一致しません")
    if expected_id and on.get_bmanga_id(collection) != expected_id:
        raise AssertionError("管理レイヤーの所属Collection IDが一致しません")


def _validate_effect(obj, effect_object) -> None:
    meta, error = _load_effect_meta(obj)
    if error or set(meta) != {"content"} or not _valid_effect_entry(meta.get("content")):
        raise AssertionError(error or "効果線設定がcontentへ正規化されていません")
    display = effect_object.find_effect_display_object(obj)
    if display is None:
        raise AssertionError("効果線の表示実体がありません")
    if len(getattr(getattr(display, "data", None), "vertices", ())) == 0:
        raise AssertionError("効果線の表示実体が空です")
    if str(display.get(effect_object.PROP_EFFECT_CONTROLLER_ID, "") or "") != str(obj.get("bmanga_id", "") or ""):
        raise AssertionError("効果線の表示実体が別の制御対象を参照しています")


def _validate_link_map(layer_uid) -> None:
    mapping, error = _json_id_property(bpy.context.scene, LINK_PROP)
    if error:
        raise AssertionError(error)
    for raw_uid in mapping:
        layer_uid.validate_uid(raw_uid)
        if layer_uid.is_legacy_pointer_uid(raw_uid):
            raise AssertionError("旧ポインタUIDが残っています")
    if LEGACY_UID_MAP_PROP in bpy.context.scene:
        raise AssertionError("移行専用UID対応表が残っています")


def _worker_inspect(page_id: str, path: Path) -> dict[str, Any]:
    _open_page(path)
    facts, issues, _model = _analyze_scene(page_id, path)
    return {
        "inspection": {
            "estimated_output_bytes": max(path.stat().st_size * 2, path.stat().st_size),
            "issues": issues,
            "facts": facts,
        }
    }


def _worker_convert(page_id: str, path: Path, request_path: Path) -> dict[str, Any]:
    _open_page(path)
    facts, issues, model = _analyze_scene(page_id, path)
    if issues:
        raise RuntimeError("事前検査不合格のページは変換できません")
    request = _WORKER_RUNTIME.read_json(request_path) if request_path.is_file() else {}
    expected_facts = dict(request.get("inspection_facts", {}) or {})
    expected = expected_facts.get("sourceSignature", "")
    expected_manifest = expected_facts.get("migrationManifest")
    if not isinstance(expected_manifest, Mapping):
        raise RuntimeError("事前検査の移行マニフェストがありません")
    if expected and expected != facts["sourceSignature"]:
        raise RuntimeError("事前検査後にページ内容が変わりました")
    if dict(expected_manifest) != facts["migrationManifest"]:
        raise RuntimeError("事前検査後に移行対象のID・順序・状態が変わりました")
    _convert_scene(page_id, path, model, expected_manifest)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    return {"converted": True}


def _worker_validate(page_id: str, path: Path) -> dict[str, Any]:
    _open_page(path)
    return {"validation": _validate_scene(page_id, path)}


def _worker_main() -> None:
    _WORKER_RUNTIME.worker_main(
        sys.argv,
        ensure_runtime=_ensure_worker_runtime,
        inspect_callback=_worker_inspect,
        convert_callback=_worker_convert,
        validate_callback=_worker_validate,
    )


__all__ = ["callbacks", "convert_page", "inspect_page", "validate_page"]


if __name__ == "__main__":
    _worker_main()
