"""別ページへ送る実体レイヤーと素材の二段階ステージング。

ステージファイルは対象 ``page.blend`` の保存成功後まで残す。復元した実体には
ステージ識別子を付けるため、同じ画面で複数回呼ばれた場合も、保存直後に異常終了
して次回ロードされた場合も重複しない。
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import uuid

import bpy

from . import cross_page_gp_transfer, cross_page_link_stage, json_io, log, paths


_logger = log.get_logger(__name__)

STAGED_IMPORTS_NAME = "_staged_imports.json"
ASSET_ENTRIES_KEY = "asset_bundles"
LINK_ENTRIES_KEY = cross_page_link_stage.LINK_ENTRIES_KEY
STAGE_OBJECT_PROP = "bmanga_staged_import_key"
ASSET_STAGE_PROP = "bmanga_asset_stage_id"
ASSET_STAGE_INDEX_PROP = "bmanga_asset_stage_index"
ASSET_STAGE_TOKEN_PROP = "bmanga_asset_stage_token"
_ASSET_MANIFEST_PROP = "bmanga_asset_stage_manifest"
_RUNTIME_KEYS_PROP = "bmanga_staged_import_runtime_keys"


def staged_path(work_dir: Path, page_id: str) -> Path:
    return paths.page_dir(Path(work_dir), page_id) / STAGED_IMPORTS_NAME


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json_io.read_json(path)
    return data if isinstance(data, dict) else {}


def _write_or_remove(path: Path, data: dict) -> None:
    populated = any(
        isinstance(data.get(key), list) and bool(data[key])
        for key in ("effects", "gp_layers", ASSET_ENTRIES_KEY, LINK_ENTRIES_KEY)
    )
    if populated:
        json_io.write_json(path, data)
        return
    try:
        from ..io.project_content_migration_lock import guard_path_write
        from ..io.project_content_save_baseline import record_successful_write

        with guard_path_write(path):
            path.unlink(missing_ok=True)
            record_successful_write(path)
    except Exception:  # noqa: BLE001
        _logger.exception("completed staged imports cleanup failed: %s", path)


def _append_unique(work_dir: Path, page_id: str, key: str, entry: dict, identity: str) -> bool:
    if not paths.is_valid_page_id(page_id) or not identity:
        return False
    path = staged_path(work_dir, page_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ..io.project_content_migration_lock import work_lock

        with work_lock(Path(work_dir)):
            data = _read(path)
            entries = data.get(key, [])
            if not isinstance(entries, list):
                entries = []
            id_key = {
                ASSET_ENTRIES_KEY: "stage_id",
                LINK_ENTRIES_KEY: "transfer_id",
            }.get(key, "bmanga_id")
            entries = [
                item for item in entries
                if not isinstance(item, dict) or str(item.get(id_key, "") or "") != identity
            ]
            entries.append(copy.deepcopy(entry))
            data[key] = entries
            data["version"] = max(3, int(data.get("version", 0) or 0))
            json_io.write_json(path, data)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("staged import write failed: %s", path)
        return False


def stage_effect(work_dir: Path, page_id: str, entry: dict) -> bool:
    stable_id = str(entry.get("bmanga_id", "") or "").strip()
    return _append_unique(work_dir, page_id, "effects", entry, stable_id)


def stage_gp(work_dir: Path, page_id: str, entry: dict) -> bool:
    stable_id = str(entry.get("bmanga_id", "") or "").strip()
    return _append_unique(work_dir, page_id, "gp_layers", entry, stable_id)


def stage_link_transfer(work_dir: Path, page_id: str, entry: dict) -> bool:
    """移動対象同士のリンク復元情報を対象ページへ残す。"""
    transfer_id = str(entry.get("transfer_id", "") or "").strip()
    return _append_unique(work_dir, page_id, LINK_ENTRIES_KEY, entry, transfer_id)


def _normalized_asset_payload(payload: dict, stage_id: str) -> dict:
    result = copy.deepcopy(payload)
    normalized = []
    for index, raw in enumerate(result.get("entries", []) or []):
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        if not str(entry.get("source_uid", "") or ""):
            entry["source_uid"] = f"stage-source:{stage_id}:{index}"
        normalized.append(entry)
    result["entries"] = normalized
    return result


def stage_asset_bundle(
    work_dir: Path,
    page_id: str,
    payload: dict,
    drop_local_xy_mm: tuple[float, float],
) -> str:
    if not paths.is_valid_page_id(page_id) or not isinstance(payload, dict):
        return ""
    stage_id = f"asset_{uuid.uuid4().hex}"
    entry = {
        "stage_id": stage_id,
        "target_page_id": page_id,
        "drop_local_xy_mm": [float(drop_local_xy_mm[0]), float(drop_local_xy_mm[1])],
        "payload": _normalized_asset_payload(payload, stage_id),
    }
    return stage_id if _append_unique(work_dir, page_id, ASSET_ENTRIES_KEY, entry, stage_id) else ""


def _runtime_keys(context) -> set[str]:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return set()
    raw = str(wm.get(_RUNTIME_KEYS_PROP, "") or "")
    try:
        values = json.loads(raw) if raw else []
    except Exception:  # noqa: BLE001
        values = []
    return {str(value) for value in values if str(value)} if isinstance(values, list) else set()


def _set_runtime_keys(context, values: set[str]) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    if values:
        wm[_RUNTIME_KEYS_PROP] = json.dumps(sorted(values), separators=(",", ":"))
    elif _RUNTIME_KEYS_PROP in wm:
        del wm[_RUNTIME_KEYS_PROP]


def _mark_runtime(context, key: str) -> None:
    values = _runtime_keys(context)
    values.add(key)
    _set_runtime_keys(context, values)


def _clear_runtime(context, keys: set[str]) -> None:
    if not keys:
        return
    _set_runtime_keys(context, _runtime_keys(context) - keys)


def _entry_key(kind: str, entry: dict) -> str:
    id_key = {"asset": "stage_id", "link": "transfer_id"}.get(kind, "bmanga_id")
    ident = str(entry.get(id_key, "") or "")
    return f"{kind}:{ident}"


def _entry_token(kind: str, entry: dict) -> str:
    """ステージ項目の identity と内容を結び付けた確定トークンを返す。"""
    if not isinstance(entry, dict):
        return ""
    identity = _entry_key(kind, entry)
    if identity.endswith(":"):
        return ""
    try:
        encoded = json.dumps(
            entry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return ""
    return f"{identity}:{hashlib.sha256(encoded).hexdigest()}"


def _remove_processed_entries(
    work_dir: Path,
    page_id: str,
    processed: dict[str, set[str]],
) -> int:
    """最新ステージをロック内で再読込し、実際に処理した版だけを除去する。"""
    if not any(processed.values()):
        return 0
    path = staged_path(work_dir, page_id)
    try:
        from ..io.project_content_migration_lock import work_lock

        with work_lock(work_dir, blocking=True):
            latest = _read(path)
            removed = 0
            for kind, key in (
                ("effect", "effects"),
                ("gp", "gp_layers"),
                ("asset", ASSET_ENTRIES_KEY),
                ("link", LINK_ENTRIES_KEY),
            ):
                tokens = processed.get(kind, set())
                source = latest.get(key, []) if isinstance(latest.get(key), list) else []
                keep = []
                for entry in source:
                    token = _entry_token(kind, entry) if isinstance(entry, dict) else ""
                    if token and token in tokens:
                        removed += 1
                    else:
                        keep.append(entry)
                latest[key] = keep
            _write_or_remove(path, latest)
            return removed
    except Exception:  # noqa: BLE001
        _logger.exception("processed staged imports cleanup failed: %s", path)
        return 0


def _valid_parent(obj, entry: dict, page_id: str) -> bool:
    from . import layer_object_model

    expected = str(entry.get("parent_key", page_id) or page_id)
    return layer_object_model.parent_key(obj) == expected


def _find_layer_object(kind: str, entry: dict, page_id: str):
    from . import layer_object_model

    stable_id = str(entry.get("bmanga_id", "") or "").strip()
    obj = layer_object_model.find_layer_object(kind, stable_id) if stable_id else None
    return obj if obj is not None and _valid_parent(obj, entry, page_id) else None


def _restore_effect(context, entry: dict, page_id: str):
    from ..core import effect_line as effect_line_core
    from ..operators import effect_line_op
    from . import effect_line_object, layer_object_model, layer_object_sync

    params_dict = entry.get("params")
    if not isinstance(params_dict, dict):
        return None
    obj = _find_layer_object("effect", entry, page_id)
    if obj is not None:
        return obj
    parent_key = str(entry.get("parent_key", page_id) or page_id)
    created = None
    try:
        effect_line_core.effect_params_from_dict(context.scene.bmanga_effect_line_params, params_dict)
        bounds = tuple(
            float(entry.get(key, default))
            for key, default in (("x", 70), ("y", 110), ("w", 80), ("h", 100))
        )
        created, _layer = effect_line_op._create_effect_layer(context, bounds, parent_key=parent_key)
        if created is None:
            return None
        desired_id = str(entry.get("bmanga_id", "") or "").strip()
        if desired_id and layer_object_model.stable_id(created) != desired_id:
            effect_line_object.delete_effect_display_object(created)
        layer_object_sync.stamp_layer_object(
            created,
            kind="effect",
            bmanga_id=desired_id,
            title=str(entry.get("title", "") or "効果線"),
            z_index=int(entry.get("z_index", 210) or 210),
            parent_kind="coma" if ":" in parent_key else "page",
            parent_key=parent_key,
            folder_id=str(entry.get("folder_id", "") or ""),
            scene=context.scene,
        )
        layer = layer_object_model.content_layer(created)
        if layer is None:
            raise RuntimeError("効果線の内容を復元できませんでした")
        center = entry.get("center_xy_mm")
        effect_line_op._write_effect_strokes(
            context,
            created,
            layer,
            bounds,
            seed=int(entry["seed"]) if entry.get("seed") is not None else None,
            center_xy_mm=tuple(center) if center else None,
        )
        layer_object_model.set_user_visible(created, bool(entry.get("visible", True)))
        layer_object_model.set_user_locked(created, bool(entry.get("locked", False)))
        return created
    except Exception:  # noqa: BLE001
        _logger.exception("staged effect creation failed")
        if created is not None:
            layer_object_model.remove_layer_object(created)
        return None


def _restore_gp(context, entry: dict, page_id: str):
    obj = _find_layer_object("gp", entry, page_id)
    if obj is not None:
        return obj
    try:
        parent_key = str(entry.get("parent_key", page_id) or page_id)
        return cross_page_gp_transfer.create_object(context, entry, parent_key)
    except Exception:  # noqa: BLE001
        _logger.exception("staged GP creation failed")
        return None


def _process_layers(context, page_id: str, kind: str, entries: list) -> tuple[int, set[str]]:
    runtime = _runtime_keys(context)
    created = 0
    processed: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        token = _entry_token(kind, entry)
        if not token:
            continue
        existing = _find_layer_object(kind, entry, page_id)
        if existing is not None:
            saved_token = str(existing.get(STAGE_OBJECT_PROP, "") or "")
            if saved_token == token:
                # この画面で復元した直後なら、まだ page.blend に保存されていないため
                # ステージは残す。既に保存済みの実体を次回ロードで見つけた場合だけ
                # 確定済みとしてステージを除去する。どちらの場合も再生成は不要。
                if token not in runtime:
                    processed.add(token)
                continue
            identity_prefix = _entry_key(kind, entry) + ":"
            if not saved_token.startswith(identity_prefix):
                _logger.error("staged %s identity conflicts with an unstaged object", kind)
                continue
            if not _remove_staged_layer_object(kind, entry, existing):
                _logger.error("old staged %s could not be replaced", kind)
                continue
            _clear_runtime(context, {saved_token})
        obj = _restore_effect(context, entry, page_id) if kind == "effect" else _restore_gp(context, entry, page_id)
        if obj is None:
            continue
        obj[STAGE_OBJECT_PROP] = token
        _mark_runtime(context, token)
        created += 1
    return created, processed


def _remove_staged_layer_object(kind: str, entry: dict, existing) -> bool:
    stable_id = str(entry.get("bmanga_id", "") or "")
    if kind == "effect":
        from . import cross_page_transfer

        return cross_page_transfer._remove_effect_objects(stable_id)
    return cross_page_gp_transfer.remove_object(stable_id)


def _asset_manifest(scene) -> dict:
    raw = str(scene.get(_ASSET_MANIFEST_PROP, "") or "") if scene is not None else ""
    try:
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    return data if isinstance(data, dict) else {}


def _clear_asset_manifest(context, stage_ids: set[str]) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or not stage_ids:
        return
    manifest = _asset_manifest(scene)
    for stage_id in stage_ids:
        manifest.pop(stage_id, None)
    if manifest:
        scene[_ASSET_MANIFEST_PROP] = json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    elif _ASSET_MANIFEST_PROP in scene:
        del scene[_ASSET_MANIFEST_PROP]


def _asset_identity(created, kind: str) -> str:
    target = created[0] if isinstance(created, tuple) else created
    if target is None:
        return ""
    if kind in {"gp", "effect"}:
        from . import layer_object_model

        return layer_object_model.stable_id(target)
    if kind == "coma":
        return str(getattr(target, "coma_id", "") or getattr(target, "id", "") or "")
    return str(getattr(target, "id", "") or "")


def stamp_asset_created(context, created, stage_id: str, index: int, kind: str) -> None:
    target = created[0] if isinstance(created, tuple) else created
    if target is None:
        return
    try:
        target[ASSET_STAGE_PROP] = stage_id
        target[ASSET_STAGE_INDEX_PROP] = int(index)
    except Exception:  # noqa: BLE001
        _logger.exception("asset stage stamp failed: %s[%d]", stage_id, index)
    identity = _asset_identity(created, kind)
    if not identity:
        return
    scene = getattr(context, "scene", None)
    manifest = _asset_manifest(scene)
    stage = manifest.setdefault(stage_id, {})
    if not isinstance(stage, dict):
        stage = {}
        manifest[stage_id] = stage
    stage[str(int(index))] = {"kind": str(kind), "id": identity}
    scene[_ASSET_MANIFEST_PROP] = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))


def _stamp_asset_token(context, page, payload: dict, stage_id: str, token: str) -> None:
    if not token:
        return
    supported = {"coma", "balloon", "text", "effect", "raster", "gp"}
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    manifest = _asset_manifest(getattr(context, "scene", None))
    stage_manifest = manifest.get(stage_id)
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get("kind") not in supported:
            continue
        kind = str(entry.get("kind", "") or "")
        created = find_asset_created(context, page, stage_id, index, kind)
        target = created[0] if isinstance(created, tuple) else created
        if target is None:
            continue
        try:
            target[ASSET_STAGE_TOKEN_PROP] = token
        except Exception:  # noqa: BLE001
            _logger.exception("asset stage token stamp failed: %s[%d]", stage_id, index)
        record = stage_manifest.get(str(index)) if isinstance(stage_manifest, dict) else None
        if isinstance(record, dict):
            record["token"] = token
    if manifest:
        context.scene[_ASSET_MANIFEST_PROP] = json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _asset_token_matches(context, page, payload: dict, stage_id: str, token: str) -> bool:
    supported = {"coma", "balloon", "text", "effect", "raster", "gp"}
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    checked = False
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get("kind") not in supported:
            continue
        checked = True
        kind = str(entry.get("kind", "") or "")
        created = find_asset_created(context, page, stage_id, index, kind)
        target = created[0] if isinstance(created, tuple) else created
        if target is None:
            return False
        try:
            if str(target.get(ASSET_STAGE_TOKEN_PROP, "") or "") != token:
                return False
        except Exception:  # noqa: BLE001
            return False
    return checked


def _asset_stage_targets(context, page, stage_id: str) -> list[tuple[str, object]]:
    from . import layer_object_model

    targets: list[tuple[str, object]] = []
    collections = (
        ("coma", getattr(page, "comas", [])),
        ("balloon", getattr(page, "balloons", [])),
        ("text", getattr(page, "texts", [])),
        ("raster", getattr(context.scene, "bmanga_raster_layers", [])),
    )
    for kind, collection in collections:
        for target in collection:
            if str(target.get(ASSET_STAGE_PROP, "") or "") == stage_id:
                targets.append((kind, target))
    for kind in ("effect", "gp"):
        for target in layer_object_model.iter_layer_objects(kind):
            if str(target.get(ASSET_STAGE_PROP, "") or "") == stage_id:
                targets.append((kind, target))
    priorities = {"effect": 0, "gp": 1, "raster": 2, "text": 3, "balloon": 4, "coma": 5}
    return sorted(targets, key=lambda item: priorities[item[0]])


def _remove_collection_item(collection, target) -> bool:
    for index, current in enumerate(collection):
        if current == target:
            collection.remove(index)
            return True
    return False


def _remove_asset_target(context, page, kind: str, target) -> bool:
    if kind == "effect":
        from . import cross_page_transfer, layer_object_model

        return cross_page_transfer._remove_effect_objects(layer_object_model.stable_id(target))
    if kind == "gp":
        from . import layer_object_model

        return cross_page_gp_transfer.remove_object(layer_object_model.stable_id(target))
    if kind == "raster":
        from . import asset_bundle_extended

        return asset_bundle_extended.remove_staged_raster(context, target)
    if kind == "text":
        from . import text_real_object

        text_id = str(getattr(target, "id", "") or "")
        text_real_object.remove_text_real_object(str(getattr(page, "id", "") or ""), text_id)
        return _remove_collection_item(page.texts, target)
    if kind == "balloon":
        from . import balloon_curve_object

        balloon_curve_object.remove_balloon_objects_by_id(str(getattr(target, "id", "") or ""))
        return _remove_collection_item(page.balloons, target)
    return _remove_asset_coma(page, target)


def _remove_asset_coma(page, target) -> bool:
    from . import coma_border_object, coma_plane

    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(target, "coma_id", "") or getattr(target, "id", "") or "")
    coma_border_object.remove_coma_border(page_id, coma_id)
    coma_plane.remove_coma_plane(page_id, coma_id)
    removed = _remove_collection_item(page.comas, target)
    page.coma_count = len(page.comas)
    return removed


def _replace_asset_stage(context, page, stage_id: str) -> bool:
    targets = _asset_stage_targets(context, page, stage_id)
    prefix = f"asset:{stage_id}:"
    old_tokens = {
        str(target.get(ASSET_STAGE_TOKEN_PROP, "") or "")
        for _kind, target in targets
    }
    if not targets or any(not token.startswith(prefix) for token in old_tokens):
        _logger.error("staged asset identity conflicts with unstamped content: %s", stage_id)
        return False
    for kind, target in targets:
        if not _remove_asset_target(context, page, kind, target):
            _logger.error("old staged asset could not be replaced: %s/%s", stage_id, kind)
            return False
    _clear_runtime(context, old_tokens)
    _clear_asset_manifest(context, {stage_id})
    return True


def _manifest_asset_created(context, page, stage_id: str, index: int, kind: str):
    manifest = _asset_manifest(getattr(context, "scene", None))
    stage = manifest.get(stage_id)
    record = stage.get(str(int(index))) if isinstance(stage, dict) else None
    if not isinstance(record, dict) or str(record.get("kind", "") or "") != kind:
        return None
    identity = str(record.get("id", "") or "")
    if not identity:
        return None
    if kind in {"gp", "effect"}:
        from . import layer_object_model

        return layer_object_model.find_layer_object(kind, identity)
    collections = {
        "coma": getattr(page, "comas", []),
        "balloon": getattr(page, "balloons", []),
        "text": getattr(page, "texts", []),
        "raster": getattr(context.scene, "bmanga_raster_layers", []),
    }
    for entry in collections.get(kind, []):
        current = str(
            getattr(entry, "coma_id", "") or getattr(entry, "id", "") or ""
        )
        if current == identity:
            return entry
    return None


def find_asset_created(context, page, stage_id: str, index: int, kind: str):
    def matches(value) -> bool:
        try:
            return (
                str(value.get(ASSET_STAGE_PROP, "") or "") == stage_id
                and int(value.get(ASSET_STAGE_INDEX_PROP, -1)) == int(index)
            )
        except Exception:  # noqa: BLE001
            return False

    collections = {
        "coma": getattr(page, "comas", []),
        "balloon": getattr(page, "balloons", []),
        "text": getattr(page, "texts", []),
        "raster": getattr(context.scene, "bmanga_raster_layers", []),
    }
    if kind in collections:
        found = next((entry for entry in collections[kind] if matches(entry)), None)
        return found or _manifest_asset_created(context, page, stage_id, index, kind)
    if kind in {"gp", "effect"}:
        from . import layer_object_model

        found = next((obj for obj in layer_object_model.iter_layer_objects(kind) if matches(obj)), None)
        return found or _manifest_asset_created(context, page, stage_id, index, kind)
    return None


def asset_stage_complete(context, page, payload: dict, stage_id: str) -> bool:
    supported = {"coma", "balloon", "text", "effect", "raster", "gp"}
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    valid = [entry for entry in entries if isinstance(entry, dict) and entry.get("kind") in supported]
    if not valid:
        return False
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get("kind") not in supported:
            continue
        kind = str(entry.get("kind", "") or "")
        created = find_asset_created(context, page, stage_id, index, kind)
        if created is None:
            return False
        if kind == "raster":
            from . import asset_bundle_extended

            if not asset_bundle_extended.raster_payload_is_durable(context, created, entry):
                return False
    return True


def _process_assets(context, page, entries: list) -> tuple[int, set[str]]:
    from . import asset_bundle

    runtime = _runtime_keys(context)
    created = 0
    processed: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        stage_id = str(entry.get("stage_id", "") or "")
        payload = entry.get("payload")
        drop = entry.get("drop_local_xy_mm")
        token = _entry_token("asset", entry)
        if not stage_id or not isinstance(payload, dict) or not isinstance(drop, (list, tuple)) or len(drop) < 2:
            continue
        was_complete = asset_stage_complete(context, page, payload, stage_id)
        if was_complete and not _asset_token_matches(context, page, payload, stage_id, token):
            if not _replace_asset_stage(context, page, stage_id):
                continue
            was_complete = False
        try:
            result = asset_bundle.instantiate_payload(
                context,
                payload,
                drop_local_xy_mm=(float(drop[0]), float(drop[1])),
                defer_to_page_file=False,
                stage_id=stage_id,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("staged asset creation failed: %s", stage_id)
            continue
        complete = asset_stage_complete(context, page, payload, stage_id)
        new_count = int(result.get("created_new_count", 0) or 0)
        if not complete:
            continue
        if new_count > 0:
            _stamp_asset_token(context, page, payload, stage_id, token)
        token_matches = _asset_token_matches(context, page, payload, stage_id, token)
        if was_complete and new_count == 0 and token not in runtime and token_matches:
            _clear_asset_manifest(context, {stage_id})
            if token:
                processed.add(token)
            continue
        if new_count > 0:
            _mark_runtime(context, token)
            created += new_count
    return created, processed


def _resolve_page_context(context, page_id: str = ""):
    from ..core.work import get_work
    from . import page_file_scene

    role, current_page_id, _ = page_file_scene.current_role(context)
    requested = str(page_id or current_page_id or "")
    explicit_page_scene = bool(
        requested
        and page_file_scene.current_page_id(getattr(context, "scene", None)) == requested
        and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
    )
    if not (
        (role == page_file_scene.ROLE_PAGE and requested == current_page_id)
        or explicit_page_scene
    ):
        return None, None, ""
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False) or not getattr(work, "work_dir", ""):
        return None, None, ""
    page = next((p for p in work.pages if str(getattr(p, "id", "") or "") == requested), None)
    return work, page, requested


def process_staged_imports(context, *, page_id: str = "") -> int:
    """対象ページで復元する。保存されるまではステージを削除しない。"""
    work, page, page_id = _resolve_page_context(context, page_id)
    if work is None or page is None:
        return 0
    work_dir = Path(work.work_dir)
    path = staged_path(work_dir, page_id)
    if not path.is_file():
        return 0
    try:
        from ..io.project_content_migration_lock import work_lock

        with work_lock(work_dir, blocking=True):
            return _process_staged_imports_locked(context, work_dir, page, page_id, path)
    except Exception:  # noqa: BLE001
        _logger.exception("staged import processing failed: %s", path)
        return 0


def _process_staged_imports_locked(context, work_dir: Path, page, page_id: str, path: Path) -> int:
    data = _read(path)
    effects = data.get("effects", []) if isinstance(data.get("effects"), list) else []
    gp_layers = data.get("gp_layers", []) if isinstance(data.get("gp_layers"), list) else []
    assets = data.get(ASSET_ENTRIES_KEY, []) if isinstance(data.get(ASSET_ENTRIES_KEY), list) else []
    links = data.get(LINK_ENTRIES_KEY, []) if isinstance(data.get(LINK_ENTRIES_KEY), list) else []
    effect_created, processed_effects = _process_layers(context, page_id, "effect", effects)
    gp_created, processed_gp = _process_layers(context, page_id, "gp", gp_layers)
    asset_created, processed_assets = _process_assets(context, page, assets)
    processed_links, pending_links = cross_page_link_stage.process(
        context,
        page,
        links,
        _entry_token,
    )
    for transfer_id in pending_links:
        _logger.error("staged link transfer is not ready: %s", transfer_id)
    _remove_processed_entries(
        work_dir,
        page_id,
        {
            "effect": processed_effects,
            "gp": processed_gp,
            "asset": processed_assets,
            "link": processed_links,
        },
    )
    created = effect_created + gp_created + asset_created
    if created:
        try:
            from . import layer_stack

            layer_stack.sync_layer_stack_after_data_change(context)
        except Exception:  # noqa: BLE001
            _logger.exception("staged layer stack sync failed")
    return created


def _saved_page_id(context, work_dir: Path, blend_path: str | Path) -> str:
    from . import page_file_scene

    path = Path(str(blend_path or bpy.data.filepath or ""))
    if not path:
        return ""
    role, page_id, _ = page_file_scene.role_from_path(path, work_dir)
    return page_id if role == page_file_scene.ROLE_PAGE else ""


def commit_staged_imports_after_save(
    context=None,
    *,
    blend_path: str | Path = "",
    metadata_saved: bool = False,
    native_save_succeeded: bool = True,
) -> int:
    """blend と page.json の双方が保存済みのときだけステージを確定する。"""
    from ..core.work import get_work

    ctx = context or bpy.context
    if metadata_saved is not True or native_save_succeeded is not True:
        return 0
    work = get_work(ctx)
    if work is None or not getattr(work, "loaded", False) or not getattr(work, "work_dir", ""):
        return 0
    work_dir = Path(work.work_dir)
    page_id = _saved_page_id(ctx, work_dir, blend_path)
    page = next((p for p in work.pages if str(getattr(p, "id", "") or "") == page_id), None)
    if page is None:
        return 0
    path = staged_path(work_dir, page_id)
    if not path.is_file():
        return 0
    try:
        from ..io.project_content_migration_lock import work_lock

        with work_lock(work_dir):
            data = _read(path)
            removed_keys: set[str] = set()
            removed = 0
            for kind, key in (("effect", "effects"), ("gp", "gp_layers")):
                source = data.get(key, []) if isinstance(data.get(key), list) else []
                keep = []
                for entry in source:
                    token = _entry_token(kind, entry) if isinstance(entry, dict) else ""
                    obj = _find_layer_object(kind, entry, page_id) if isinstance(entry, dict) else None
                    saved_token = str(obj.get(STAGE_OBJECT_PROP, "") or "") if obj is not None else ""
                    if token and obj is not None and saved_token == token:
                        removed += 1
                        removed_keys.add(token)
                    else:
                        keep.append(entry)
                data[key] = keep
            source_links = data.get(LINK_ENTRIES_KEY, []) if isinstance(data.get(LINK_ENTRIES_KEY), list) else []
            keep_links = []
            for entry in source_links:
                transfer_id = str(entry.get("transfer_id", "") or "") if isinstance(entry, dict) else ""
                token = _entry_token("link", entry) if isinstance(entry, dict) else ""
                if transfer_id and cross_page_link_stage.is_saved(ctx, entry, token):
                    removed += 1
                    removed_keys.add(token)
                else:
                    keep_links.append(entry)
            data[LINK_ENTRIES_KEY] = keep_links
            source_assets = data.get(ASSET_ENTRIES_KEY, []) if isinstance(data.get(ASSET_ENTRIES_KEY), list) else []
            keep_assets = []
            removed_asset_stage_ids: set[str] = set()
            for entry in source_assets:
                stage_id = str(entry.get("stage_id", "") or "") if isinstance(entry, dict) else ""
                payload = entry.get("payload") if isinstance(entry, dict) else None
                token = _entry_token("asset", entry) if isinstance(entry, dict) else ""
                if (
                    stage_id
                    and token
                    and isinstance(payload, dict)
                    and asset_stage_complete(ctx, page, payload, stage_id)
                    and _asset_token_matches(ctx, page, payload, stage_id, token)
                ):
                    removed += 1
                    removed_keys.add(token)
                    removed_asset_stage_ids.add(stage_id)
                else:
                    keep_assets.append(entry)
            data[ASSET_ENTRIES_KEY] = keep_assets
            _write_or_remove(path, data)
        _clear_runtime(ctx, removed_keys)
        _clear_asset_manifest(ctx, removed_asset_stage_ids)
        return removed
    except Exception:  # noqa: BLE001
        _logger.exception("staged imports commit failed: %s", path)
        return 0


__all__ = [
    "ASSET_ENTRIES_KEY",
    "ASSET_STAGE_INDEX_PROP",
    "ASSET_STAGE_PROP",
    "ASSET_STAGE_TOKEN_PROP",
    "LINK_ENTRIES_KEY",
    "STAGED_IMPORTS_NAME",
    "asset_stage_complete",
    "commit_staged_imports_after_save",
    "find_asset_created",
    "process_staged_imports",
    "stage_asset_bundle",
    "stage_effect",
    "stage_gp",
    "stage_link_transfer",
    "staged_path",
    "stamp_asset_created",
]
