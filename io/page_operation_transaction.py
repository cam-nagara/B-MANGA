"""ページ複製・削除をファイルとJSONの一括取引として扱う。"""

from __future__ import annotations

import copy
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import uuid

import bpy

from ..utils import json_io, layer_links, log, page_range, paths
from . import page_io, schema, work_io
from .project_content_migration_lock import (
    allow_owned_recovery_journal,
    guard_path_write,
    work_lock,
)
from .project_content_save_baseline import (
    record_successful_tree_change,
    record_successful_write,
)
from .project_content_sidecar_save_guard import (
    begin_sidecar_save,
    commit_sidecars,
    mark_sidecar_writes_started,
    restore_sidecars,
)


_WORK_LAYER_SPECS = (
    ("raster_layers", "raster"),
    ("image_layers", "image"),
    ("fill_layers", "fill"),
    ("image_path_layers", "image_path"),
)

_logger = log.get_logger(__name__)


@dataclass(slots=True)
class _MemorySnapshot:
    work_data: dict[str, Any]
    pages_data: dict[str, Any]
    page_details: dict[str, dict[str, Any]]
    detail_loaded: dict[str, bool]
    work_dir: str
    loaded: bool
    link_json: str


@dataclass(slots=True)
class _DuplicatePlan:
    source_page_id: str
    target_page_id: str
    target_dir: Path
    stage_dir: Path
    work_data: dict[str, Any]
    pages_data: dict[str, Any]
    target_detail: dict[str, Any]
    layer_maps: dict[str, dict[str, str]]
    worker_result: dict[str, Any]
    staged_rasters: list[tuple[Path, Path]]


@dataclass(slots=True)
class _DeletePlan:
    page_id: str
    page_dir: Path
    page_quarantine: Path
    work_data: dict[str, Any]
    pages_data: dict[str, Any]
    details: dict[str, dict[str, Any]]
    removed_uids: set[str]
    removed_rasters: list[dict[str, Any]]


def _capture_memory(context, work) -> _MemorySnapshot:
    details: dict[str, dict[str, Any]] = {}
    loaded: dict[str, bool] = {}
    for page in work.pages:
        page_id = str(getattr(page, "id", "") or "")
        details[page_id] = schema.page_to_dict(page)
        loaded[page_id] = bool(getattr(page, "detail_loaded", True))
    scene = getattr(context, "scene", None)
    link_json = str(scene.get(layer_links.LINK_PROP, "") or "") if scene is not None else ""
    return _MemorySnapshot(
        work_data=copy.deepcopy(schema.work_to_dict(work)),
        pages_data=copy.deepcopy(schema.pages_to_dict(work)),
        page_details=copy.deepcopy(details),
        detail_loaded=loaded,
        work_dir=str(getattr(work, "work_dir", "") or ""),
        loaded=bool(getattr(work, "loaded", False)),
        link_json=link_json,
    )


def _apply_memory(
    context,
    work,
    work_data: dict[str, Any],
    pages_data: dict[str, Any],
    page_details: dict[str, dict[str, Any]],
    detail_loaded: dict[str, bool],
    *,
    work_dir: str,
    loaded: bool,
) -> None:
    work.loaded = False
    schema.work_from_dict(work, copy.deepcopy(work_data))
    schema.pages_from_dict(work, copy.deepcopy(pages_data))
    for page in work.pages:
        page_id = str(getattr(page, "id", "") or "")
        detail = page_details.get(page_id)
        if detail is not None:
            schema.page_from_dict(page, copy.deepcopy(detail))
        page.detail_loaded = bool(detail_loaded.get(page_id, detail is not None))
        page.coma_count = len(page.comas)
    work.work_dir = work_dir
    work.loaded = loaded


def _restore_memory(context, work, snapshot: _MemorySnapshot) -> None:
    _apply_memory(
        context,
        work,
        snapshot.work_data,
        snapshot.pages_data,
        snapshot.page_details,
        snapshot.detail_loaded,
        work_dir=snapshot.work_dir,
        loaded=snapshot.loaded,
    )
    scene = getattr(context, "scene", None)
    if scene is not None:
        scene[layer_links.LINK_PROP] = snapshot.link_json


def _persisted_page_detail(
    snapshot: _MemorySnapshot, work_dir: Path, page_id: str
) -> dict[str, Any]:
    """未読込ページはメモリの空値でなく、保存済みpage.jsonを正本にする。"""
    if snapshot.detail_loaded.get(page_id, False):
        return copy.deepcopy(snapshot.page_details.get(page_id, {}))
    page_path = paths.page_meta_path(work_dir, page_id)
    if not page_path.is_file():
        return copy.deepcopy(snapshot.page_details.get(page_id, {}))
    value = json_io.read_json(page_path)
    if not isinstance(value, dict):
        raise ValueError(f"ページ情報がオブジェクトではありません: {page_path}")
    return copy.deepcopy(value)


def _replace_page_key(value: object, source_page_id: str, target_page_id: str) -> str:
    text = str(value or "")
    if text == source_page_id:
        return target_page_id
    prefix = f"{source_page_id}:"
    return f"{target_page_id}:{text[len(prefix):]}" if text.startswith(prefix) else text


def _folder_ids_for_page(work_data: dict[str, Any], page_id: str) -> list[str]:
    folders = [item for item in work_data.get("layer_folders", []) if isinstance(item, dict)]
    selected: list[str] = []
    selected_set: set[str] = set()
    changed = True
    while changed:
        changed = False
        for item in folders:
            folder_id = str(item.get("id", "") or "")
            parent = str(item.get("parentKey", item.get("parent_key", "")) or "")
            if not folder_id or folder_id in selected_set:
                continue
            if _replace_page_key(parent, page_id, "") != parent or parent in selected_set:
                selected.append(folder_id)
                selected_set.add(folder_id)
                changed = True
    return selected


def _unique_id(prefix: str, used: set[str]) -> str:
    for _attempt in range(256):
        candidate = f"{prefix}_{uuid.uuid4().hex[:16]}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError("複製先の識別子を確保できませんでした")


def _duplicate_folders(
    work_data: dict[str, Any], source_page_id: str, target_page_id: str
) -> dict[str, str]:
    folders = work_data.setdefault("layer_folders", [])
    source_ids = _folder_ids_for_page(work_data, source_page_id)
    source_set = set(source_ids)
    used = {str(item.get("id", "") or "") for item in folders if isinstance(item, dict)}
    folder_map = {folder_id: _unique_id("folder", used) for folder_id in source_ids}
    clones = []
    for item in folders:
        if not isinstance(item, dict) or str(item.get("id", "") or "") not in source_set:
            continue
        clone = copy.deepcopy(item)
        old_id = str(clone.get("id", "") or "")
        parent = str(clone.get("parentKey", clone.get("parent_key", "")) or "")
        clone["id"] = folder_map[old_id]
        clone["parentKey"] = folder_map.get(
            parent, _replace_page_key(parent, source_page_id, target_page_id)
        )
        clone.pop("parent_key", None)
        clones.append(clone)
    folders.extend(clones)
    return folder_map


def _entry_belongs_to_page(entry: dict[str, Any], page_id: str, folder_ids: set[str]) -> bool:
    parent = str(entry.get("parentKey", entry.get("parent_key", "")) or "")
    folder = str(entry.get("folderKey", entry.get("folder_key", "")) or "")
    return parent == page_id or parent.startswith(f"{page_id}:") or folder in folder_ids


def _duplicate_work_layers(
    work_data: dict[str, Any],
    source_page_id: str,
    target_page_id: str,
    folder_map: dict[str, str],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    source_folders = set(folder_map)
    for list_key, kind in _WORK_LAYER_SPECS:
        entries = work_data.setdefault(list_key, [])
        used = {
            str(item.get("id", "") or "")
            for item in entries
            if isinstance(item, dict)
        }
        id_map: dict[str, str] = {}
        clones = []
        for item in list(entries):
            if not isinstance(item, dict) or not _entry_belongs_to_page(item, source_page_id, source_folders):
                continue
            clone = copy.deepcopy(item)
            old_id = str(clone.get("id", "") or "")
            new_id = _unique_id(kind, used)
            id_map[old_id] = new_id
            clone["id"] = new_id
            parent_key_name = "parentKey" if "parentKey" in clone else "parent_key"
            clone[parent_key_name] = _replace_page_key(
                clone.get(parent_key_name, ""), source_page_id, target_page_id
            )
            folder_key_name = "folderKey" if "folderKey" in clone else "folder_key"
            if folder_key_name in clone:
                clone[folder_key_name] = folder_map.get(str(clone.get(folder_key_name, "") or ""), "")
            if kind == "raster":
                clone["image_name"] = f"raster_{new_id}"
                clone["filepath_rel"] = f"{paths.RASTER_DIR_NAME}/{new_id}.png"
            clones.append(clone)
        entries.extend(clones)
        if id_map:
            result[kind] = id_map
    return result


def _retarget_page_detail(
    source: dict[str, Any],
    source_page_id: str,
    target_page_id: str,
    folder_map: dict[str, str],
) -> dict[str, Any]:
    result = copy.deepcopy(source)
    result["id"] = target_page_id
    result["title"] = ""
    result["offsetXMm"] = 0.0
    result["offsetYMm"] = 0.0

    def visit(value):
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if key in {"parentKey", "parent_key", "pageId", "page_id", "ownerPageId"}:
                    value[key] = _replace_page_key(child, source_page_id, target_page_id)
                elif key in {"folderKey", "folder_key"}:
                    value[key] = folder_map.get(str(child or ""), "")
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return result


def _new_pages_data(
    snapshot: _MemorySnapshot,
    source_index: int,
    target_page_id: str,
    target_detail: dict[str, Any],
) -> dict[str, Any]:
    data = copy.deepcopy(snapshot.pages_data)
    pages = data.setdefault("pages", [])
    clone = copy.deepcopy(pages[source_index])
    clone.update(
        id=target_page_id,
        title="",
        dir=f"{target_page_id}/",
        offsetXMm=0.0,
        offsetYMm=0.0,
        thumbnail="",
        comaCount=len(target_detail.get("comas", []) or []),
    )
    pages.insert(source_index + 1, clone)
    data["activePageIndex"] = source_index + 1
    data["totalPages"] = len(pages)
    return data


def _check_copy_source(source: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise FileNotFoundError(f"複製元ページフォルダーがありません: {source}")
    root = source.resolve(strict=True)
    if os.path.normcase(str(root)) != os.path.normcase(str(source.absolute())):
        raise RuntimeError("複製元ページフォルダーが別の場所を指しています")
    for item in source.rglob("*"):
        if item.is_symlink():
            raise RuntimeError(f"ページ内のシンボリックリンクは複製できません: {item}")
        try:
            item.resolve(strict=True).relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"ページ外を指す項目は複製できません: {item}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if source.stat().st_size != target.stat().st_size or _sha256(source) != _sha256(target):
        raise OSError(f"複製内容を検証できませんでした: {source}")


def _raster_source(work_dir: Path, entry: dict[str, Any]) -> Path | None:
    raster_id = str(entry.get("id", "") or "")
    rel = str(entry.get("filepath_rel", "") or "")
    candidates = [work_dir / rel] if rel else []
    if raster_id:
        candidates.append(paths.raster_png_path(work_dir, raster_id))
    root = work_dir.resolve()
    for candidate in candidates:
        if candidate.is_symlink():
            continue
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if resolved.is_file() and not resolved.is_symlink():
            return resolved
    return None


def _stage_rasters(
    snapshot: _MemorySnapshot,
    layer_maps: dict[str, dict[str, str]],
    work_dir: Path,
    stage_root: Path,
) -> list[tuple[Path, Path]]:
    id_map = layer_maps.get("raster", {})
    if not id_map:
        return []
    staged = []
    for entry in snapshot.work_data.get("raster_layers", []) or []:
        if not isinstance(entry, dict):
            continue
        old_id = str(entry.get("id", "") or "")
        new_id = id_map.get(old_id)
        if not new_id:
            continue
        source = _raster_source(work_dir, entry)
        if source is None:
            raise FileNotFoundError(f"ラスターレイヤー画像がありません: {old_id}")
        stage = stage_root / "raster" / f"{new_id}.png"
        _verified_copy(source, stage)
        staged.append((stage, paths.raster_png_path(work_dir, new_id)))
    return staged


def _run_page_blend_worker(
    page_blend: Path,
    source_page_id: str,
    target_page_id: str,
    folder_map: dict[str, str],
    layer_maps: dict[str, dict[str, str]],
    target_detail: dict[str, Any],
) -> dict[str, Any]:
    if not page_blend.is_file():
        return {"idMaps": {}}
    from . import detail_data_blender_worker_runtime

    request = {
        "sourcePageId": source_page_id,
        "targetPageId": target_page_id,
        "folderMap": folder_map,
        "entryIdMaps": layer_maps,
        "balloonIds": [str(item.get("id", "") or "") for item in target_detail.get("balloons", [])],
        "textIds": [str(item.get("id", "") or "") for item in target_detail.get("texts", [])],
    }
    return detail_data_blender_worker_runtime.run_worker(
        bpy.app.binary_path,
        Path(__file__).with_name("page_operation_blender_worker.py"),
        "convert",
        target_page_id,
        page_blend,
        request=request,
    )


def _publish_directory(stage: Path, target: Path) -> None:
    with guard_path_write(target):
        if target.exists():
            raise FileExistsError(target)
        moved = False
        try:
            os.replace(stage, target)
            moved = True
            record_successful_tree_change(target)
        except BaseException:
            if moved and target.exists() and not stage.exists():
                os.replace(target, stage)
            raise


def _publish_file(stage: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with guard_path_write(target):
        if target.exists():
            raise FileExistsError(target)
        moved = False
        try:
            os.replace(stage, target)
            moved = True
            record_successful_write(target)
        except BaseException:
            if moved and target.exists() and not stage.exists():
                os.replace(target, stage)
            raise


def _remove_published(paths_to_remove: list[Path]) -> None:
    for target in reversed(paths_to_remove):
        with guard_path_write(target):
            if target.is_dir():
                shutil.rmtree(target)
                record_successful_tree_change(target)
            else:
                target.unlink(missing_ok=True)
                record_successful_write(target)


def _complete_rollback(actions) -> None:
    first_error = None
    for action in actions:
        try:
            action()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise RuntimeError("ページ操作を完全には復元できませんでした") from first_error


def _restore_sidecar_files(token, paths_to_record: tuple[Path, ...]) -> None:
    if token is None:
        return
    restore_sidecars(token)
    for path in paths_to_record:
        record_successful_write(path)


def _commit_sidecars_checked(token) -> None:
    """確定書込みが失敗した時だけ、復元可能な状態へ戻す。"""
    try:
        commit_sidecars(token)
    except BaseException:
        try:
            durable_status = str(json_io.read_json(token.journal_path).get("status", ""))
        except BaseException:
            durable_status = ""
        if durable_status == "committed":
            _logger.warning(
                "ページ操作は確定済みですが、保存退避物の後片付けに失敗しました: %s",
                token.transaction_dir,
            )
            return
        if durable_status in {"secured", "writing", "restored"}:
            token.status = durable_status
        raise


def _load_link_map(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return {str(key): str(group) for key, group in value.items()} if isinstance(value, dict) else {}


def _clone_current_links(context, source_raw: str, uid_map: dict[str, str]) -> None:
    mapping = _load_link_map(source_raw)
    groups: dict[str, list[str]] = {}
    for old_uid, group in mapping.items():
        new_uid = uid_map.get(old_uid)
        if new_uid:
            groups.setdefault(group, []).append(new_uid)
    for members in groups.values():
        unique = list(dict.fromkeys(members))
        if len(unique) < 2:
            continue
        group_id = f"layer_link_{uuid.uuid4().hex}"
        mapping.update({uid: group_id for uid in unique})
    scene = getattr(context, "scene", None)
    if scene is not None:
        scene[layer_links.LINK_PROP] = json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))


def _page_entry_uid_map(
    source_page_id: str,
    target_page_id: str,
    target_detail: dict[str, Any],
    layer_maps: dict[str, dict[str, str]],
    worker_result: dict[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for kind in ("balloon", "text"):
        for item in target_detail.get(f"{kind}s", []) or []:
            entry_id = str(item.get("id", "") or "")
            if entry_id:
                result[f"{kind}:{source_page_id}:{entry_id}"] = f"{kind}:{target_page_id}:{entry_id}"
    for kind in ("image", "raster"):
        for old_id, new_id in layer_maps.get(kind, {}).items():
            result[f"{kind}:{old_id}"] = f"{kind}:{new_id}"
    for kind, id_map in dict(worker_result.get("idMaps", {}) or {}).items():
        if kind not in {"gp", "effect"} or not isinstance(id_map, dict):
            continue
        for old_id, new_id in id_map.items():
            result[f"{kind}:{old_id}"] = f"{kind}:{new_id}"
    return result


def _prepare_duplicate_plan(
    snapshot: _MemorySnapshot,
    source_index: int,
    source_page_id: str,
    target_page_id: str,
    work_dir: Path,
    transaction_root: Path,
) -> _DuplicatePlan:
    source_dir = paths.page_dir(work_dir, source_page_id)
    stage_dir = transaction_root / target_page_id
    _check_copy_source(source_dir)
    shutil.copytree(source_dir, stage_dir)
    work_data = copy.deepcopy(snapshot.work_data)
    folder_map = _duplicate_folders(work_data, source_page_id, target_page_id)
    layer_maps = _duplicate_work_layers(work_data, source_page_id, target_page_id, folder_map)
    source_detail = _persisted_page_detail(snapshot, work_dir, source_page_id)
    target_detail = _retarget_page_detail(
        source_detail, source_page_id, target_page_id, folder_map
    )
    pages_data = _new_pages_data(snapshot, source_index, target_page_id, target_detail)
    json_io.write_json(stage_dir / paths.PAGE_META_NAME, target_detail)
    worker_result = _run_page_blend_worker(
        stage_dir / paths.PAGE_BLEND_NAME,
        source_page_id,
        target_page_id,
        folder_map,
        layer_maps,
        target_detail,
    )
    return _DuplicatePlan(
        source_page_id, target_page_id, paths.page_dir(work_dir, target_page_id), stage_dir,
        work_data, pages_data, target_detail, layer_maps, worker_result,
        _stage_rasters(snapshot, layer_maps, work_dir, transaction_root),
    )


def _apply_duplicate_plan(context, work, snapshot: _MemorySnapshot, plan: _DuplicatePlan) -> None:
    details = copy.deepcopy(snapshot.page_details)
    details[plan.target_page_id] = plan.target_detail
    loaded = dict(snapshot.detail_loaded)
    loaded[plan.target_page_id] = True
    _apply_memory(
        context, work, plan.work_data, plan.pages_data, details, loaded,
        work_dir=snapshot.work_dir, loaded=snapshot.loaded,
    )
    uid_map = _page_entry_uid_map(
        plan.source_page_id,
        plan.target_page_id,
        plan.target_detail,
        plan.layer_maps,
        plan.worker_result,
    )
    _clone_current_links(context, snapshot.link_json, uid_map)
    page_range.sync_end_number_to_page_count(work)


def _commit_duplicate_plan(
    context, work, snapshot: _MemorySnapshot, plan: _DuplicatePlan, work_dir: Path
) -> None:
    sidecar_paths = (paths.pages_meta_path(work_dir), paths.work_meta_path(work_dir))
    sidecar = begin_sidecar_save(work_dir, sidecar_paths)
    published: list[Path] = []
    with allow_owned_recovery_journal(sidecar.journal_path):
        try:
            _publish_directory(plan.stage_dir, plan.target_dir)
            published.append(plan.target_dir)
            for stage, target in plan.staged_rasters:
                _publish_file(stage, target)
                published.append(target)
            _apply_duplicate_plan(context, work, snapshot, plan)
            mark_sidecar_writes_started(sidecar)
            page_io.save_pages_json(work_dir, work)
            work_io.save_work_json(work_dir, work)
            _commit_sidecars_checked(sidecar)
        except BaseException:
            _complete_rollback((
                lambda: _remove_published(published),
                lambda: _restore_sidecar_files(sidecar, sidecar_paths),
                lambda: _restore_memory(context, work, snapshot),
            ))
            raise


def duplicate_page(context, work, source_index: int) -> str:
    """ページを新IDへ複製し、全保存対象が揃った時だけ公開する。"""
    work_dir = Path(str(getattr(work, "work_dir", "") or "")).resolve(strict=True)
    with work_lock(work_dir, blocking=True):
        snapshot = _capture_memory(context, work)
        source_page_id = str(work.pages[source_index].id)
        target_page_id = page_io.allocate_new_page_id(work)
        with guard_path_write(paths.pages_meta_path(work_dir)):
            pass
        transaction_root = Path(tempfile.mkdtemp(
            prefix=f".{work_dir.name}.page-duplicate-", dir=str(work_dir.parent)
        ))
        try:
            plan = _prepare_duplicate_plan(
                snapshot, source_index, source_page_id, target_page_id, work_dir, transaction_root
            )
            _commit_duplicate_plan(context, work, snapshot, plan, work_dir)
            return target_page_id
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)


def _removed_uids(
    page_id: str,
    page_detail: dict[str, Any],
    work_data: dict[str, Any],
    folder_ids: set[str],
) -> set[str]:
    result = {
        f"{kind}:{page_id}:{str(item.get('id', '') or '')}"
        for kind in ("balloon", "text")
        for item in page_detail.get(f"{kind}s", []) or []
        if str(item.get("id", "") or "")
    }
    for list_key, kind in _WORK_LAYER_SPECS:
        if kind not in {"image", "raster"}:
            continue
        for item in work_data.get(list_key, []) or []:
            if isinstance(item, dict) and _entry_belongs_to_page(item, page_id, folder_ids):
                entry_id = str(item.get("id", "") or "")
                if entry_id:
                    result.add(f"{kind}:{entry_id}")
    return result


def _delete_data(
    snapshot: _MemorySnapshot,
    page_id: str,
    page_detail: dict[str, Any],
) -> tuple[dict, dict, dict, set[str], list[dict]]:
    work_data = copy.deepcopy(snapshot.work_data)
    folder_ids = set(_folder_ids_for_page(work_data, page_id))
    removed_rasters: list[dict] = []
    for list_key, _kind in _WORK_LAYER_SPECS:
        retained = []
        for item in work_data.get(list_key, []) or []:
            if isinstance(item, dict) and _entry_belongs_to_page(item, page_id, folder_ids):
                if list_key == "raster_layers":
                    removed_rasters.append(copy.deepcopy(item))
                continue
            retained.append(item)
        work_data[list_key] = retained
    work_data["layer_folders"] = [
        item
        for item in work_data.get("layer_folders", []) or []
        if not isinstance(item, dict) or str(item.get("id", "") or "") not in folder_ids
    ]
    pages_data = copy.deepcopy(snapshot.pages_data)
    old_pages = list(pages_data.get("pages", []) or [])
    old_active = int(pages_data.get("activePageIndex", -1))
    removed_index = next(
        (index for index, item in enumerate(old_pages) if str(item.get("id", "") or "") == page_id),
        -1,
    )
    pages_data["pages"] = [item for item in old_pages if str(item.get("id", "") or "") != page_id]
    pages_data["totalPages"] = len(pages_data["pages"])
    if not pages_data["pages"]:
        pages_data["activePageIndex"] = -1
    elif old_active > removed_index or old_active >= len(pages_data["pages"]):
        pages_data["activePageIndex"] = max(0, old_active - 1)
    details = copy.deepcopy(snapshot.page_details)
    details.pop(page_id, None)
    removed = _removed_uids(
        page_id, page_detail, snapshot.work_data, folder_ids
    )
    return work_data, pages_data, details, removed, removed_rasters


def _quarantine_path(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with guard_path_write(source):
        moved = False
        try:
            os.replace(source, target)
            moved = True
            if source.suffix:
                record_successful_write(source)
            else:
                record_successful_tree_change(source)
        except BaseException:
            if moved and target.exists() and not source.exists():
                os.replace(target, source)
            raise
    return True


def _restore_quarantine(source: Path, quarantine: Path) -> None:
    if not quarantine.exists():
        return
    source.parent.mkdir(parents=True, exist_ok=True)
    with guard_path_write(source):
        if source.exists():
            raise FileExistsError(source)
        restored = False
        try:
            os.replace(quarantine, source)
            restored = True
            if source.suffix:
                record_successful_write(source)
            else:
                record_successful_tree_change(source)
        except BaseException:
            if restored and source.exists() and not quarantine.exists():
                os.replace(source, quarantine)
            raise


def _remove_deleted_links(context, raw: str, removed_uids: set[str]) -> None:
    mapping = _load_link_map(raw)
    mapping = {uid: group for uid, group in mapping.items() if uid not in removed_uids}
    scene = getattr(context, "scene", None)
    if scene is not None:
        scene[layer_links.LINK_PROP] = json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))


def _prepare_delete_plan(
    snapshot: _MemorySnapshot, page_id: str, work_dir: Path, transaction_root: Path
) -> _DeletePlan:
    page_dir = paths.page_dir(work_dir, page_id)
    if page_dir.is_symlink():
        raise RuntimeError("ページフォルダーがシンボリックリンクのため削除できません")
    if page_dir.exists():
        try:
            resolved_page = page_dir.resolve(strict=True)
            resolved_page.relative_to(work_dir.resolve(strict=True))
        except ValueError as exc:
            raise RuntimeError("ページフォルダーが作品外を指しているため削除できません") from exc
        if os.path.normcase(str(resolved_page)) != os.path.normcase(str(page_dir.absolute())):
            raise RuntimeError("ページフォルダーが別の場所を指しているため削除できません")
    page_detail = _persisted_page_detail(snapshot, work_dir, page_id)
    work_data, pages_data, details, removed_uids, removed_rasters = _delete_data(
        snapshot, page_id, page_detail
    )
    return _DeletePlan(
        page_id,
        page_dir,
        transaction_root / page_id,
        work_data,
        pages_data,
        details,
        removed_uids,
        removed_rasters,
    )


def _quarantine_delete_rasters(
    plan: _DeletePlan,
    work_dir: Path,
    transaction_root: Path,
    raster_moves: list[tuple[Path, Path]],
) -> None:
    retained = {
        str(item.get("filepath_rel", "") or "")
        for item in plan.work_data.get("raster_layers", []) or []
        if isinstance(item, dict) and str(item.get("filepath_rel", "") or "")
    }
    for entry in plan.removed_rasters:
        if str(entry.get("filepath_rel", "") or "") in retained:
            continue
        source = _raster_source(work_dir, entry)
        if source is None:
            continue
        quarantine = transaction_root / "raster" / f"{len(raster_moves):04d}_{source.name}"
        if _quarantine_path(source, quarantine):
            raster_moves.append((source, quarantine))


def _apply_delete_plan(context, work, snapshot: _MemorySnapshot, plan: _DeletePlan) -> None:
    loaded = {
        key: value for key, value in snapshot.detail_loaded.items() if key != plan.page_id
    }
    _apply_memory(
        context, work, plan.work_data, plan.pages_data, plan.details, loaded,
        work_dir=snapshot.work_dir, loaded=snapshot.loaded,
    )
    _remove_deleted_links(context, snapshot.link_json, plan.removed_uids)
    page_range.sync_end_number_to_page_count(work)


def _rollback_delete(
    context,
    work,
    snapshot: _MemorySnapshot,
    plan: _DeletePlan,
    page_moved: bool,
    raster_moves: list[tuple[Path, Path]],
    sidecar,
    sidecar_paths: tuple[Path, ...],
) -> None:
    actions = [
        (lambda source=source, quarantine=quarantine: _restore_quarantine(source, quarantine))
        for source, quarantine in reversed(raster_moves)
    ]
    if page_moved:
        actions.append(lambda: _restore_quarantine(plan.page_dir, plan.page_quarantine))
    actions.extend((
        lambda: _restore_sidecar_files(sidecar, sidecar_paths),
        lambda: _restore_memory(context, work, snapshot),
    ))
    _complete_rollback(actions)


def _cleanup_committed_delete(transaction_root: Path) -> None:
    """論理確定後の隔離物を消す。失敗時は安全側として退避物を残す。"""
    try:
        shutil.rmtree(transaction_root)
    except BaseException:
        _logger.exception(
            "ページ削除は確定済みですが、隔離した削除対象を消去できませんでした: %s",
            transaction_root,
        )


def delete_page(context, work, page_index: int) -> str:
    """ページを同一ボリュームへ隔離し、JSON確定後だけ実削除する。"""
    work_dir = Path(str(getattr(work, "work_dir", "") or "")).resolve(strict=True)
    with work_lock(work_dir, blocking=True):
        snapshot = _capture_memory(context, work)
        page_id = str(work.pages[page_index].id)
        with guard_path_write(paths.pages_meta_path(work_dir)):
            pass
        transaction_root = Path(tempfile.mkdtemp(
            prefix=f".{work_dir.name}.page-delete-", dir=str(work_dir.parent)
        ))
        sidecar_paths = (paths.pages_meta_path(work_dir), paths.work_meta_path(work_dir))
        plan = None
        sidecar = None
        page_moved, raster_moves = False, []
        committed = False
        rollback_complete = False
        with ExitStack() as recovery_access:
            try:
                plan = _prepare_delete_plan(snapshot, page_id, work_dir, transaction_root)
                sidecar = begin_sidecar_save(work_dir, sidecar_paths)
                recovery_access.enter_context(
                    allow_owned_recovery_journal(sidecar.journal_path)
                )
                page_moved = _quarantine_path(plan.page_dir, plan.page_quarantine)
                _quarantine_delete_rasters(
                    plan, work_dir, transaction_root, raster_moves
                )
                _apply_delete_plan(context, work, snapshot, plan)
                mark_sidecar_writes_started(sidecar)
                page_io.save_pages_json(work_dir, work)
                work_io.save_work_json(work_dir, work)
                _commit_sidecars_checked(sidecar)
                committed = True
                return page_id
            except BaseException:
                if plan is not None:
                    _rollback_delete(
                        context, work, snapshot, plan, page_moved, raster_moves,
                        sidecar, sidecar_paths,
                    )
                rollback_complete = True
                raise
            finally:
                if committed:
                    _cleanup_committed_delete(transaction_root)
                elif rollback_complete:
                    shutil.rmtree(transaction_root, ignore_errors=True)


__all__ = ["delete_page", "duplicate_page"]
