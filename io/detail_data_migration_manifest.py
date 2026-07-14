"""詳細データ移行で事前確定した内容を保存後の実体と照合する。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


MANIFEST_PROP = "bmanga_detail_migration_manifest_v1"
MANIFEST_SCHEMA_VERSION = 1


def build_inspection_facts(
    page_id: str,
    page_version: int,
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    uid_map: Mapping[str, str],
    current_ids: Mapping[str, Sequence[str]],
    existing_folders: Sequence[Mapping[str, Any]],
    links: Mapping[str, str],
) -> dict[str, Any]:
    signature = source_signature(
        records, uid_map, current_ids, existing_folders, links
    )
    migration_manifest = build_manifest(
        page_id,
        signature,
        current_ids=current_ids,
        migrated_records=records,
        folder_records=tuple(existing_folders) + tuple(records.get("groups", ())),
        links=links,
    )
    return {
        "pageId": str(page_id),
        "legacyGpCount": len(records.get("gp", ())),
        "legacyEffectCount": len(records.get("effect", ())),
        "legacyFolderCount": len(records.get("groups", ())),
        "existingFolderCount": len(existing_folders),
        "pageDetailDataVersion": int(page_version),
        "folderManifest": _project_folder_manifest(records.get("groups", ()), page_id),
        "uidMap": dict(uid_map),
        "sourceSignature": signature,
        "migrationManifest": migration_manifest,
    }


def source_signature(records, uid_map, current_ids, existing_folders, links) -> str:
    encoded = json.dumps(
        {
            "records": records,
            "uidMap": uid_map,
            "currentIds": current_ids,
            "existingFolders": existing_folders,
            "canonicalLinks": links,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project_folder_manifest(records, page_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": str(record["stable_id"]),
            "title": str(record["title"]),
            "parentKey": str(record["parent_key"]),
            "expanded": bool(record["expanded"]),
            "visible": not bool(record["hidden"]),
            "locked": bool(record["locked"]),
            "pageId": str(page_id),
            "sourceObject": str(record["source_object"]),
            "sourceGroup": str(record["source_group"]),
        }
        for record in records
    ]


def capture_existing_folders(scene, object_naming) -> list[dict[str, Any]]:
    """現在のページblendに実体がある汎用フォルダーを保存順で取得する。"""

    work = getattr(scene, "bmanga_work", None)
    collections = _scene_folder_collections(scene, object_naming)
    records = []
    seen = set()
    for entry in list(getattr(work, "layer_folders", ()) or ()):
        folder_id = str(getattr(entry, "id", "") or "")
        if not folder_id or folder_id in seen:
            raise ValueError("既存フォルダーIDが空または重複しています")
        seen.add(folder_id)
        coll = collections.get(folder_id)
        if coll is None:
            raise ValueError(f"既存フォルダーCollectionがありません: {folder_id}")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        parent_kind = _parent_kind(parent_key, object_naming)
        _validate_existing_folder_source(
            scene, entry, coll, folder_id, parent_kind, parent_key, object_naming
        )
        records.append({
            "stable_id": folder_id,
            "title": str(getattr(entry, "title", "") or ""),
            "parent_kind": parent_kind,
            "parent_key": parent_key,
            "expanded": bool(getattr(entry, "expanded", True)),
            "hidden": not bool(getattr(entry, "visible", True)),
            "locked": bool(getattr(entry, "locked", False)),
            "z_index": int(coll.get(object_naming.PROP_Z_INDEX, 0) or 0),
        })
    if set(collections) != seen:
        raise ValueError("作品情報にない既存フォルダーCollectionがあります")
    return records


def _validate_existing_folder_source(
    scene, entry, coll, folder_id, parent_kind, parent_key, object_naming
) -> None:
    expected_title = str(getattr(entry, "title", "") or "")
    title_prop = getattr(object_naming, "PROP_TITLE", "bmanga_title")
    if str(coll.get(title_prop, "") or "") != expected_title:
        raise ValueError(f"既存フォルダーの表示名が一致しません: {folder_id}")
    if str(coll.get(object_naming.PROP_PARENT_KEY, "") or "") != parent_key:
        raise ValueError(f"既存フォルダーの所属が一致しません: {folder_id}")
    hidden = not bool(getattr(entry, "visible", True))
    locked = bool(getattr(entry, "locked", False))
    if bool(coll.hide_viewport) != hidden or bool(coll.hide_render) != hidden:
        raise ValueError(f"既存フォルダーの表示状態が一致しません: {folder_id}")
    if hasattr(coll, "hide_select") and bool(coll.hide_select) != locked:
        raise ValueError(f"既存フォルダーのロック状態が一致しません: {folder_id}")
    parent = _find_collection_parent(getattr(scene, "collection", None), coll)
    if parent is None or object_naming.get_kind(parent) != parent_kind:
        raise ValueError(f"既存フォルダーの親種別が一致しません: {folder_id}")
    if parent_key and object_naming.get_bmanga_id(parent) != parent_key:
        raise ValueError(f"既存フォルダーの親IDが一致しません: {folder_id}")


def _scene_folder_collections(scene, object_naming) -> dict[str, Any]:
    pending = [getattr(scene, "collection", None)]
    seen_pointers = set()
    folders = {}
    while pending:
        coll = pending.pop()
        if coll is None or _pointer(coll) in seen_pointers:
            continue
        seen_pointers.add(_pointer(coll))
        pending.extend(list(getattr(coll, "children", ()) or ()))
        if object_naming.get_kind(coll) != "folder":
            continue
        folder_id = str(object_naming.get_bmanga_id(coll) or "")
        if not folder_id or folder_id in folders:
            raise ValueError("既存フォルダーCollectionのIDが空または重複しています")
        folders[folder_id] = coll
    return folders


def _parent_kind(parent_key: str, object_naming) -> str:
    if not parent_key or parent_key == "__outside__":
        return "outside"
    for kind in ("folder", "coma", "page", "outside"):
        if object_naming.find_collection_by_bmanga_id(parent_key, kind=kind) is not None:
            return kind
    return "coma" if ":" in parent_key else "page"


def canonical_link_map(saved_mapping: Mapping[str, Any], uid_map: Mapping[str, str]) -> dict[str, str]:
    """旧UIDを事前検査済みの正規UIDへ置換した完全なリンク表を返す。"""

    return {
        str(uid_map.get(str(raw_uid), str(raw_uid))): str(group)
        for raw_uid, group in sorted(saved_mapping.items(), key=lambda item: str(item[0]))
    }


def build_manifest(
    page_id: str,
    source_signature: str,
    *,
    current_ids: Mapping[str, Sequence[str]],
    migrated_records: Mapping[str, Sequence[Mapping[str, Any]]],
    folder_records: Sequence[Mapping[str, Any]],
    links: Mapping[str, str],
) -> dict[str, Any]:
    """保存前の読取専用factsだけから、保存後に必要な完全一致条件を作る。"""

    managed_ids = {}
    for kind in ("gp", "effect"):
        values = [str(item) for item in current_ids.get(kind, ())]
        values.extend(str(item.get("stable_id", "")) for item in migrated_records.get(kind, ()))
        if not all(values) or len(values) != len(set(values)):
            raise ValueError(f"{kind}の移行先IDが空または重複しています")
        managed_ids[kind] = sorted(values)
    folders = [_folder_definition(record, index) for index, record in enumerate(folder_records)]
    folder_ids = [item["id"] for item in folders]
    if not all(folder_ids) or len(folder_ids) != len(set(folder_ids)):
        raise ValueError("移行後フォルダーIDが空または重複しています")
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "pageId": str(page_id),
        "sourceSignature": str(source_signature),
        "managedIds": managed_ids,
        "counts": {kind: len(values) for kind, values in managed_ids.items()},
        "folders": folders,
        "linkMap": {str(key): str(value) for key, value in sorted(links.items())},
    }


def _folder_definition(record: Mapping[str, Any], order: int) -> dict[str, Any]:
    return {
        "id": str(record.get("stable_id", "")),
        "title": str(record.get("title", "")),
        "parentKind": str(record.get("parent_kind", "")),
        "parentKey": str(record.get("parent_key", "")),
        "expanded": bool(record.get("expanded", True)),
        "visible": not bool(record.get("hidden", False)),
        "locked": bool(record.get("locked", False)),
        "zIndex": int(record.get("z_index", 0)),
        "order": int(order),
    }


def store_manifest(scene, manifest: Mapping[str, Any]) -> None:
    scene[MANIFEST_PROP] = json.dumps(
        dict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def load_manifest(scene) -> dict[str, Any]:
    raw = str(scene.get(MANIFEST_PROP, "") or "")
    if not raw:
        raise AssertionError("移行検証マニフェストがありません")
    try:
        manifest = json.loads(raw)
    except Exception as exc:
        raise AssertionError(f"移行検証マニフェストを読めません: {exc}") from exc
    if not isinstance(manifest, dict):
        raise AssertionError("移行検証マニフェストのルートが不正です")
    return manifest


def validate_manifest(scene, page_id: str, layer_model, object_naming, link_prop: str) -> dict[str, Any]:
    """保存済みmanifestと再読込後のID・順序・状態・リンクを完全照合する。"""

    manifest = load_manifest(scene)
    if int(manifest.get("schemaVersion", 0) or 0) != MANIFEST_SCHEMA_VERSION:
        raise AssertionError("移行検証マニフェストの版が一致しません")
    if str(manifest.get("pageId", "") or "") != str(page_id):
        raise AssertionError("移行検証マニフェストのページIDが一致しません")
    if not str(manifest.get("sourceSignature", "") or ""):
        raise AssertionError("移行検証マニフェストの元データ署名がありません")
    identities = _validate_managed_ids(manifest, layer_model)
    _validate_folders(scene, manifest, object_naming)
    _validate_links(scene, manifest, link_prop)
    return {
        "sourceSignature": str(manifest["sourceSignature"]),
        "gpIds": identities["gp"],
        "effectIds": identities["effect"],
        "folderCount": len(manifest.get("folders", ())),
    }


def _validate_managed_ids(manifest: Mapping[str, Any], layer_model) -> dict[str, list[str]]:
    expected_root = manifest.get("managedIds")
    expected_counts = manifest.get("counts")
    if not isinstance(expected_root, Mapping) or not isinstance(expected_counts, Mapping):
        raise AssertionError("移行検証マニフェストのID一覧が不正です")
    actual = {}
    for kind in ("gp", "effect"):
        expected_raw = expected_root.get(kind)
        if not isinstance(expected_raw, list) or not all(isinstance(item, str) for item in expected_raw):
            raise AssertionError(f"{kind}の移行先ID一覧が不正です")
        expected = sorted(expected_raw)
        if len(expected) != len(set(expected)) or int(expected_counts.get(kind, -1)) != len(expected):
            raise AssertionError(f"{kind}の移行先ID件数が不正です")
        actual[kind] = sorted(
            str(layer_model.stable_id(obj))
            for obj in layer_model.iter_layer_objects(kind)
        )
        if actual[kind] != expected:
            raise AssertionError(f"{kind}の移行先IDまたは件数が事前検査と一致しません")
    return actual


def _validate_folders(scene, manifest: Mapping[str, Any], object_naming) -> None:
    expected = manifest.get("folders")
    if not isinstance(expected, list) or not all(isinstance(item, Mapping) for item in expected):
        raise AssertionError("移行検証マニフェストのフォルダー一覧が不正です")
    ids = [str(item.get("id", "") or "") for item in expected]
    if not all(ids) or len(ids) != len(set(ids)):
        raise AssertionError("移行検証マニフェストのフォルダーIDが不正です")
    work = getattr(scene, "bmanga_work", None)
    if work is None and expected:
        raise AssertionError("ページ用blendファイルに作品情報がありません")
    entries = list(getattr(work, "layer_folders", ()) or ())
    actual_order = [str(getattr(entry, "id", "") or "") for entry in entries]
    if actual_order != ids:
        raise AssertionError("フォルダー順が事前検査と一致しません")
    try:
        collections = _scene_folder_collections(scene, object_naming)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc
    if set(collections) != set(ids):
        raise AssertionError("フォルダーCollection一覧が事前検査と一致しません")
    by_id = {str(entry.id): entry for entry in entries}
    for index, item in enumerate(expected):
        _validate_folder(
            scene,
            item,
            index,
            by_id.get(ids[index]),
            collections.get(ids[index]),
            object_naming,
        )


def _validate_folder(scene, expected, index, entry, coll, object_naming) -> None:
    folder_id = str(expected.get("id", "") or "")
    if entry is None or int(expected.get("order", -1)) != index:
        raise AssertionError(f"フォルダー定義を再読込できません: {folder_id}")
    fields = {
        "title": str(getattr(entry, "title", "") or ""),
        "parentKey": str(getattr(entry, "parent_key", "") or ""),
        "expanded": bool(getattr(entry, "expanded", True)),
        "visible": bool(getattr(entry, "visible", True)),
        "locked": bool(getattr(entry, "locked", False)),
    }
    if any(fields[name] != expected.get(name) for name in fields):
        raise AssertionError(f"フォルダー状態が事前検査と一致しません: {folder_id}")
    if coll is None:
        raise AssertionError(f"フォルダーCollectionがありません: {folder_id}")
    title_prop = getattr(object_naming, "PROP_TITLE", "bmanga_title")
    if str(coll.get(title_prop, "") or "") != str(expected.get("title", "")):
        raise AssertionError(f"フォルダーの表示名が一致しません: {folder_id}")
    if str(coll.get(object_naming.PROP_PARENT_KEY, "") or "") != str(expected.get("parentKey", "")):
        raise AssertionError(f"フォルダーの所属が一致しません: {folder_id}")
    parent = _find_collection_parent(getattr(scene, "collection", None), coll)
    expected_parent_kind = str(expected.get("parentKind", "") or "")
    if parent is None or object_naming.get_kind(parent) != expected_parent_kind:
        raise AssertionError(f"フォルダーの親種別が一致しません: {folder_id}")
    if str(expected.get("parentKey", "") or "") and (
        object_naming.get_bmanga_id(parent) != str(expected.get("parentKey", ""))
    ):
        raise AssertionError(f"フォルダーの親IDが一致しません: {folder_id}")
    if int(coll.get(object_naming.PROP_Z_INDEX, 0) or 0) != int(expected.get("zIndex", 0)):
        raise AssertionError(f"フォルダーの並び値が一致しません: {folder_id}")
    hidden = not bool(expected.get("visible", True))
    if bool(coll.hide_viewport) != hidden or bool(coll.hide_render) != hidden:
        raise AssertionError(f"フォルダーの表示状態が一致しません: {folder_id}")
    if hasattr(coll, "hide_select") and bool(coll.hide_select) != bool(expected.get("locked", False)):
        raise AssertionError(f"フォルダーのロック状態が一致しません: {folder_id}")


def _find_collection_parent(root, target):
    if root is None:
        return None
    pending = [root]
    seen = set()
    target_pointer = _pointer(target)
    while pending:
        parent = pending.pop()
        pointer = _pointer(parent)
        if pointer in seen:
            continue
        seen.add(pointer)
        children = list(getattr(parent, "children", ()) or ())
        if any(_pointer(child) == target_pointer for child in children):
            return parent
        pending.extend(children)
    return None


def _pointer(value):
    callback = getattr(value, "as_pointer", None)
    return int(callback()) if callable(callback) else id(value)


def _validate_links(scene, manifest: Mapping[str, Any], link_prop: str) -> None:
    expected = manifest.get("linkMap")
    if not isinstance(expected, Mapping):
        raise AssertionError("移行検証マニフェストのリンク表が不正です")
    raw = str(scene.get(link_prop, "") or "")
    try:
        actual = json.loads(raw) if raw else {}
    except Exception as exc:
        raise AssertionError(f"保存済みリンク表を読めません: {exc}") from exc
    if not isinstance(actual, dict) or {
        str(key): str(value) for key, value in actual.items()
    } != {str(key): str(value) for key, value in expected.items()}:
        raise AssertionError("正規リンク表が事前検査と一致しません")


__all__ = [
    "MANIFEST_PROP",
    "build_inspection_facts",
    "build_manifest",
    "canonical_link_map",
    "capture_existing_folders",
    "load_manifest",
    "store_manifest",
    "validate_manifest",
]
