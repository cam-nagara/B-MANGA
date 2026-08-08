"""Alt+D&D が使う、選択・子孫・リンクを閉包したページ間移送."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import uuid

import bpy

from ..core.work import get_work
from ..io import blend_io, coma_io, page_io, schema, work_io
from . import (
    asset_bundle,
    cross_page_gp_transfer,
    cross_page_stage,
    json_io,
    layer_links,
    layer_stack as layer_stack_utils,
    layer_transfer_ownership,
    layer_transfer_recovery_manifest,
    log,
    page_file_scene,
    page_grid,
    paths,
)
from .layer_hierarchy import coma_stack_key, split_child_key
_logger = log.get_logger(__name__)
_TRANSFERABLE_KINDS = frozenset(asset_bundle.SUPPORTED_LAYER_KINDS)
_RECOVERY_DIR_NAME = "_transfer_recovery"
_RECOVERY_MANIFEST_NAME = "transaction.json"

@dataclass(frozen=True)
class TransferGroup:
    """一回のドラッグで不可分に扱うレイヤー集合."""

    items: tuple[object, ...]
    uids: tuple[str, ...]
    anchor_uid: str
    anchor_world_xy_mm: tuple[float, float]
    source_page_id: str
@dataclass(frozen=True)
class _ComaMove:
    source_id: str
    target_id: str


class LayerTransferRollbackError(RuntimeError):
    """ページ間移送の復元を完了できず、作品をfail-closedにした。"""


class LayerTransferRecoveryError(RuntimeError):
    """起動時のページ間移送復旧に未完了項目が残った。"""


class LayerTransferCleanupError(RuntimeError):
    """terminal journalを安全に削除できなかった。"""


def _mark_transfer_fail_closed(work) -> None:
    try:
        work.loaded = False
    except Exception:  # noqa: BLE001
        _logger.exception("transfer fail-closed state could not be recorded")


def build_transfer_group(
    context,
    *,
    anchor_item=None,
    require_anchor_world: bool = True,
) -> TransferGroup | None:
    """現在選択から子孫・リンク・フキダシ/テキストの閉包を作る."""
    explicit_anchor_uid = (
        layer_stack_utils.stack_item_uid(anchor_item)
        if anchor_item is not None
        else ""
    )
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return None
    items = list(stack)
    active_index = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
    active_item = items[active_index] if 0 <= active_index < len(items) else None
    anchor_item = (
        next(
            (
                candidate
                for candidate in items
                if layer_stack_utils.stack_item_uid(candidate) == explicit_anchor_uid
            ),
            None,
        )
        if explicit_anchor_uid
        else active_item
    )
    selected = {
        layer_stack_utils.stack_item_uid(item)
        for index, item in enumerate(items)
        if (
            (anchor_item is None and index == active_index)
            or layer_stack_utils.is_item_selected(context, item)
        )
        if str(getattr(item, "kind", "") or "") in _TRANSFERABLE_KINDS
    }
    if anchor_item is not None:
        if str(getattr(anchor_item, "kind", "") or "") not in _TRANSFERABLE_KINDS:
            return None
        selected.add(layer_stack_utils.stack_item_uid(anchor_item))
    selected.discard("")
    if not selected:
        return None
    selected = _expand_transfer_closure(context, items, selected)
    resolved_selected = {
        layer_stack_utils.stack_item_uid(item)
        for item in items
        if layer_stack_utils.stack_item_uid(item) in selected
    }
    if resolved_selected != selected:
        _logger.error("transfer closure contains unresolved layer UIDs")
        return None
    if any(
        str(getattr(item, "kind", "") or "") not in _TRANSFERABLE_KINDS
        for item in items
        if layer_stack_utils.stack_item_uid(item) in selected
    ):
        return None
    group_items = tuple(
        item
        for item in items
        if layer_stack_utils.stack_item_uid(item) in selected
        and str(getattr(item, "kind", "") or "") in _TRANSFERABLE_KINDS
    )
    if not group_items:
        return None
    anchor_uid = layer_stack_utils.stack_item_uid(anchor_item) if anchor_item is not None else ""
    if anchor_uid not in selected:
        anchor_uid = layer_stack_utils.stack_item_uid(group_items[0])
    anchor = next(
        (item for item in group_items if layer_stack_utils.stack_item_uid(item) == anchor_uid),
        group_items[0],
    )
    role, current_page_id, _coma_id = page_file_scene.current_role(context)
    if (
        role != page_file_scene.ROLE_PAGE
        or not layer_transfer_ownership.validate_single_page_group(
            context,
            group_items,
            current_page_id,
        )
    ):
        _logger.error("transfer group contains mixed or unresolved ownership")
        return None
    source_page_id = current_page_id
    work = get_work(context)
    if (
        work is None
        or not getattr(work, "work_dir", "")
        or any(
            str(getattr(item, "kind", "") or "") == "layer_folder"
            and cross_page_stage.has_pending_transfer_target_folder(
                work.work_dir,
                source_page_id,
                str(getattr(item, "key", "") or ""),
            )
            for item in group_items
        )
    ):
        _logger.error("pending transfer target folder cannot be transferred")
        return None
    center = _item_world_center(context, anchor, group_items)
    if not source_page_id or (center is None and require_anchor_world):
        return None
    if center is None:
        center = (0.0, 0.0)
    return TransferGroup(
        items=group_items,
        uids=tuple(layer_stack_utils.stack_item_uid(item) for item in group_items),
        anchor_uid=anchor_uid,
        anchor_world_xy_mm=center,
        source_page_id=source_page_id,
    )


def transfer_group_to_page(
    context,
    target,
    *,
    drop_world_xy_mm: tuple[float, float] | None = None,
    anchor_item=None,
) -> int | None:
    """別ページなら原子的移送を実行。同一ページなら ``None`` を返す."""
    from . import history_runtime

    if history_runtime.mutation_blocked(context):
        return 0
    if target is None or getattr(target, "kind", "") not in {"page", "coma"}:
        return None
    target_page = getattr(target, "page", None)
    target_page_id = str(getattr(target_page, "id", "") or "")
    target_folder_key = str(getattr(target, "folder_key", "") or "")
    role, current_page_id, _coma_id = page_file_scene.current_role(context)
    if (
        role != page_file_scene.ROLE_PAGE
        or not target_page_id
        or target_page_id == current_page_id
    ):
        return None
    group = build_transfer_group(context, anchor_item=anchor_item)
    if group is None or group.source_page_id != current_page_id:
        return 0
    if not layer_transfer_ownership.validate_single_page_group(
        context,
        group.items,
        current_page_id,
        forbidden_folder_key=target_folder_key,
    ):
        return 0
    if target_folder_key and not _target_folder_owned_by_page(
        context,
        target_folder_key,
        target_page_id,
    ):
        return 0
    final_drop = drop_world_xy_mm or group.anchor_world_xy_mm
    return _execute_cross_page(
        context,
        group,
        target_page,
        final_drop,
        target_folder_key=target_folder_key,
    )


def _target_folder_owned_by_page(
    context,
    folder_key: str,
    page_id: str,
) -> bool:
    from . import layer_folder

    work = get_work(context)
    folder = layer_folder.find_folder(work, str(folder_key or ""))
    if folder is None:
        return False
    semantic_parent = layer_folder.semantic_parent_key_for_folder(
        work,
        folder_key,
    )
    owner_page_id, _child_id = split_child_key(semantic_parent)
    return bool(page_id) and owner_page_id == page_id


def _expand_descendants(items: list[object], selected: set[str]) -> set[str]:
    result = set(selected)
    changed = True
    while changed:
        changed = False
        containers = {
            str(getattr(item, "key", "") or "")
            for item in items
            if layer_stack_utils.stack_item_uid(item) in result
            and str(getattr(item, "kind", "") or "") in {"coma", "layer_folder"}
        }
        for item in items:
            uid = layer_stack_utils.stack_item_uid(item)
            if uid in result:
                continue
            if str(getattr(item, "kind", "") or "") not in _TRANSFERABLE_KINDS:
                continue
            if str(getattr(item, "parent_key", "") or "") in containers:
                result.add(uid)
                changed = True
    return result


def _expand_transfer_closure(
    context,
    items: list[object],
    selected: set[str],
) -> set[str]:
    """子孫・link・フキダシ/テキスト関係を固定点まで閉じる."""

    result = set(selected)
    while True:
        before = set(result)
        result = _expand_descendants(items, result)
        result = _expand_links_and_text_pairs(context, items, result)
        if result == before:
            return result


def _expand_links_and_text_pairs(context, items: list[object], selected: set[str]) -> set[str]:
    result = set(selected)
    for uid in tuple(result):
        result.update(layer_links.linked_uids_for_uid(context, uid))
    identities: dict[tuple[str, str, str], str] = {}
    resolved_by_uid: dict[str, dict] = {}
    for item in items:
        uid = layer_stack_utils.stack_item_uid(item)
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        if resolved is None:
            continue
        resolved_by_uid[uid] = resolved
        page = resolved.get("page")
        target = resolved.get("target")
        page_id = str(getattr(page, "id", "") or "")
        entry_id = str(getattr(target, "id", "") or "")
        identities[(str(getattr(item, "kind", "") or ""), page_id, entry_id)] = uid
    for uid in tuple(result):
        resolved = resolved_by_uid.get(uid)
        if resolved is None:
            continue
        item = next(
            (candidate for candidate in items if layer_stack_utils.stack_item_uid(candidate) == uid),
            None,
        )
        target = resolved.get("target")
        page = resolved.get("page")
        if item is None or target is None or page is None:
            continue
        kind = str(getattr(item, "kind", "") or "")
        page_id = str(getattr(page, "id", "") or "")
        if kind == "text":
            balloon_id = str(getattr(target, "parent_balloon_id", "") or "")
            partner = identities.get(("balloon", page_id, balloon_id), "")
            if partner:
                result.add(partner)
        elif kind == "balloon":
            balloon_id = str(getattr(target, "id", "") or "")
            for text in getattr(page, "texts", []) or []:
                if str(getattr(text, "parent_balloon_id", "") or "") != balloon_id:
                    continue
                partner = identities.get(
                    ("text", page_id, str(getattr(text, "id", "") or "")),
                    "",
                )
                if partner:
                    result.add(partner)
    return result


def _source_page_id(context, items: tuple[object, ...]) -> str:
    role, page_id, _coma_id = page_file_scene.current_role(context)
    if role == page_file_scene.ROLE_PAGE:
        return page_id
    for item in items:
        key = str(getattr(item, "key", "") or "")
        candidate, child = split_child_key(key)
        if child and paths.is_valid_page_id(candidate):
            return candidate
        parent_page, _parent_child = split_child_key(
            str(getattr(item, "parent_key", "") or "")
        )
        if paths.is_valid_page_id(parent_page):
            return parent_page
    return ""


def _item_world_center(context, item, group_items) -> tuple[float, float] | None:
    try:
        from ..operators import object_tool_selection

        resolved = layer_stack_utils.resolve_stack_item(context, item)
        key = layer_stack_utils._object_selection_key_for_stack_item(item, resolved)
        rect = object_tool_selection.selection_bounds_for_key(context, key) if key else None
        if rect is not None:
            return (
                float(rect.x) + float(rect.width) * 0.5,
                float(rect.y) + float(rect.height) * 0.5,
            )
        rects = []
        for child in group_items:
            resolved = layer_stack_utils.resolve_stack_item(context, child)
            key = layer_stack_utils._object_selection_key_for_stack_item(child, resolved)
            candidate = object_tool_selection.selection_bounds_for_key(context, key) if key else None
            if candidate is not None:
                rects.append(candidate)
        if rects:
            left = min(float(rect.x) for rect in rects)
            bottom = min(float(rect.y) for rect in rects)
            right = max(float(rect.x) + float(rect.width) for rect in rects)
            top = max(float(rect.y) + float(rect.height) for rect in rects)
            return (left + right) * 0.5, (bottom + top) * 0.5
    except Exception:  # noqa: BLE001
        _logger.exception("transfer anchor bounds resolution failed")
    return None


def _execute_cross_page(
    context,
    group: TransferGroup,
    target_page,
    drop_world_xy_mm,
    *,
    target_folder_key: str = "",
) -> int:
    work = get_work(context)
    work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
    source_page = _page_by_id(work, group.source_page_id)
    target_page_id = str(getattr(target_page, "id", "") or "")
    if work is None or work_dir is None or source_page is None or not work_dir.is_dir():
        return 0
    if target_folder_key and not _target_folder_owned_by_page(
        context,
        target_folder_key,
        target_page_id,
    ):
        return 0
    try:
        payload = asset_bundle.build_payload(context, group.items, name="ページ間移送")
    except Exception:  # noqa: BLE001
        _logger.exception("transfer payload build failed")
        return 0
    source_index = _page_index(work, group.source_page_id)
    target_index = _page_index(work, target_page_id)
    if source_index < 0 or target_index < 0:
        return 0
    source_offset = page_grid.page_total_offset_mm(work, context.scene, source_index)
    target_offset = page_grid.page_total_offset_mm(work, context.scene, target_index)
    payload["origin"] = {
        "x": group.anchor_world_xy_mm[0] - source_offset[0],
        "y": group.anchor_world_xy_mm[1] - source_offset[1],
    }
    payload["transfer"] = {
        "sourcePageId": group.source_page_id,
        "targetPageId": target_page_id,
        "targetFolderKey": str(target_folder_key or ""),
        "targetFolderOwnerPageId": target_page_id if target_folder_key else "",
        "anchorUid": group.anchor_uid,
        "uids": list(group.uids),
    }
    coma_moves = _prepare_coma_ids(
        payload,
        work_dir,
        source_page,
        target_page,
    )
    target_local = (
        float(drop_world_xy_mm[0]) - target_offset[0],
        float(drop_world_xy_mm[1]) - target_offset[1],
    )
    snapshots = _capture_snapshots(context, work, source_page, group)
    stage_id = ""
    moved_comas: list[_ComaMove] = []
    recovery_dir: Path | None = None
    backup: dict[Path, Path | None] = {}
    from ..io.project_file_lock import work_lock

    try:
        with work_lock(work_dir, blocking=True):
            try:
                stage_id = cross_page_stage.stage_asset_bundle(
                    work_dir,
                    target_page_id,
                    payload,
                    target_local,
                    ready=False,
                )
                if not stage_id:
                    raise RuntimeError("destination staging failed")
                recovery_dir, backup = _create_recovery_backup(
                    context,
                    work_dir,
                    work,
                    source_page,
                    group.source_page_id,
                    target_page_id,
                    stage_id,
                    coma_moves,
                )
                moved_comas = _move_coma_files(
                    work_dir,
                    group.source_page_id,
                    target_page_id,
                    coma_moves,
                    completed=moved_comas,
                )
                if not _remove_source_group(context, group):
                    raise RuntimeError("source group removal failed")
                _remove_source_links(context, group.uids)
                record_current = lambda: _record_recovery_current_state(
                    recovery_dir,
                    work_dir,
                )
                _save_source(
                    context,
                    work_dir,
                    work,
                    source_page,
                    on_boundary=record_current,
                )
                if not blend_io.save_page_blend(work_dir, group.source_page_id):
                    raise RuntimeError("source page.blend save failed")
                record_current()
                if not cross_page_stage.mark_asset_bundle_ready(
                    work_dir,
                    target_page_id,
                    stage_id,
                ):
                    raise RuntimeError("destination staging commit failed")
                from . import layer_transfer_history

                layer_transfer_history.register(
                    context,
                    work_dir=work_dir,
                    source_page_id=group.source_page_id,
                    target_page_id=target_page_id,
                    stage_id=stage_id,
                    recovery_dir=recovery_dir,
                )
            except Exception as transfer_error:  # noqa: BLE001
                _logger.exception("transfer group transaction failed")
                try:
                    _rollback(
                        context,
                        work,
                        source_page,
                        snapshots,
                        work_dir,
                        target_page_id,
                        stage_id,
                        moved_comas,
                        backup,
                        recovery_dir,
                    )
                except LayerTransferRollbackError:
                    raise
                except BaseException as rollback_error:
                    _mark_transfer_fail_closed(work)
                    raise LayerTransferRollbackError(
                        "ページ間移送の復元処理を完了できませんでした"
                    ) from rollback_error
                if isinstance(transfer_error, LayerTransferRollbackError):
                    _mark_transfer_fail_closed(work)
                    raise transfer_error
                return 0
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        return len(group.items)
    except LayerTransferRollbackError:
        raise
    except Exception:  # noqa: BLE001
        _logger.exception("transfer group transaction failed")
        return 0


def _prepare_coma_ids(payload, work_dir: Path, source_page, target_page) -> list[_ComaMove]:
    existing = set(coma_io.existing_coma_ids(work_dir, str(getattr(target_page, "id", "") or "")))
    existing.update(coma_io.page_data_coma_ids(target_page))
    moves: list[_ComaMove] = []
    for entry in payload.get("entries", []) or []:
        if not isinstance(entry, dict) or str(entry.get("kind", "") or "") != "coma":
            continue
        source_id = str(entry.get("source_id", "") or "")
        index = paths.next_available_coma_index(sorted(existing))
        target_id = paths.format_coma_id(index)
        existing.add(target_id)
        entry["target_id"] = target_id
        data = entry.get("data")
        if isinstance(data, dict):
            data["id"] = target_id
            data["comaId"] = target_id
        moves.append(_ComaMove(source_id, target_id))
    return moves


def _capture_snapshots(context, work, source_page, group: TransferGroup) -> dict:
    effects = {}
    gps = {}
    from . import cross_page_transfer

    for item in group.items:
        kind = str(getattr(item, "kind", "") or "")
        _page_id, entry_id = split_child_key(str(getattr(item, "key", "") or ""))
        entry_id = entry_id or str(getattr(item, "key", "") or "")
        if kind == "effect":
            effects[entry_id] = cross_page_transfer._extract_effect_meta(entry_id)
        elif kind == "gp":
            gps[entry_id] = cross_page_gp_transfer.serialize_object(entry_id)
    page_snapshot = copy.deepcopy(asset_bundle._pg_to_dict(source_page))
    _remove_snapshot_keys(page_snapshot, {"coma_number"})
    return {
        "work": copy.deepcopy(schema.work_to_dict(work)),
        # 公開page.jsonは再生成可能な表示名などを意図的に省く。途中失敗時は
        # それらも含めて完全復元する必要があるため、メモリ用にはRNA全項目を
        # 別形式で退避する（公開ファイル形式には書き出さない）。
        "page": page_snapshot,
        "links": layer_links._load_map(context),
        "effects": effects,
        "gps": gps,
    }


def _remove_snapshot_keys(value, keys: set[str]) -> None:
    """RNAの派生setterを完全復元スナップショットから除く."""
    if isinstance(value, dict):
        for key in keys:
            value.pop(key, None)
        for child in value.values():
            _remove_snapshot_keys(child, keys)
    elif isinstance(value, list):
        for child in value:
            _remove_snapshot_keys(child, keys)


def _move_coma_files(
    work_dir,
    source_page_id,
    target_page_id,
    moves,
    *,
    completed: list[_ComaMove] | None = None,
) -> list[_ComaMove]:
    completed = completed if completed is not None else []
    for move in moves:
        try:
            coma_io.move_coma_files(
                work_dir,
                source_page_id,
                target_page_id,
                move.source_id,
                move.target_id,
            )
        except Exception as exc:
            source_dir = paths.coma_dir(work_dir, source_page_id, move.source_id)
            target_dir = paths.coma_dir(work_dir, target_page_id, move.target_id)
            moved = bool(getattr(exc, "moved_to_destination", False))
            if moved or (not source_dir.exists() and target_dir.is_dir()):
                completed.append(move)
            raise
        completed.append(move)
    return completed


def _remove_source_group(context, group: TransferGroup) -> bool:
    priorities = {
        "effect": 0,
        "gp": 0,
        "raster": 1,
        "image_path": 1,
        "image": 1,
        "fill": 1,
        "text": 2,
        "balloon": 3,
        "layer_folder": 4,
        "coma": 5,
    }
    ordered = sorted(
        group.items,
        key=lambda item: priorities.get(str(getattr(item, "kind", "") or ""), 2),
    )
    for item in ordered:
        if not _remove_source_item(context, item):
            return False
    return True


def _remove_source_item(context, item) -> bool:
    from . import (
        balloon_curve_object,
        cross_page_transfer,
        fill_real_object,
        image_path_object,
        image_real_object,
        text_real_object,
    )

    resolved = layer_stack_utils.resolve_stack_item(context, item)
    if resolved is None:
        return False
    kind = str(getattr(item, "kind", "") or "")
    target = resolved.get("target")
    page = resolved.get("page")
    index = int(resolved.get("index", -1))
    if kind == "coma" and page is not None and 0 <= index < len(page.comas):
        page.comas.remove(index)
        page.coma_count = len(page.comas)
        return True
    if kind == "balloon" and page is not None and 0 <= index < len(page.balloons):
        balloon_curve_object.remove_balloon_objects_by_id(str(getattr(target, "id", "") or ""))
        page.balloons.remove(index)
        return True
    if kind == "text" and page is not None and 0 <= index < len(page.texts):
        text_real_object.remove_text_real_object(
            str(getattr(page, "id", "") or ""),
            str(getattr(target, "id", "") or ""),
        )
        page.texts.remove(index)
        return True
    if kind == "effect":
        from . import layer_object_model

        return cross_page_transfer._remove_effect_objects(
            layer_object_model.stable_id(resolved.get("object"))
        )
    if kind == "gp":
        from . import layer_object_model

        return cross_page_gp_transfer.remove_object(
            layer_object_model.stable_id(resolved.get("object"))
        )
    scene = context.scene
    specs = {
        "image": ("bmanga_image_layers", image_real_object.remove_image_real_object),
        "image_path": ("bmanga_image_path_layers", image_path_object.remove_image_path_object),
        "fill": ("bmanga_fill_layers", fill_real_object.remove_fill_real_object),
        "raster": ("bmanga_raster_layers", _remove_raster_runtime),
    }
    if kind in specs:
        coll = getattr(scene, specs[kind][0], None)
        if coll is None or not (0 <= index < len(coll)):
            return False
        entry_id = str(getattr(target, "id", "") or "")
        specs[kind][1](entry_id)
        coll.remove(index)
        return True
    if kind == "layer_folder":
        work = get_work(context)
        coll = getattr(work, "layer_folders", None) if work is not None else None
        if coll is None or not (0 <= index < len(coll)):
            return False
        coll.remove(index)
        return True
    return False


def _remove_raster_runtime(raster_id: str) -> bool:
    from . import object_naming

    obj = object_naming.find_object_by_bmanga_id(raster_id, kind="raster")
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
    return True


def _remove_source_links(context, uids) -> None:
    original = layer_links._load_map(context)
    moved = set(uids)
    remaining = {uid: group for uid, group in original.items() if uid not in moved}
    counts = {}
    for group in remaining.values():
        counts[group] = counts.get(group, 0) + 1
    layer_links._save_map(
        context,
        {uid: group for uid, group in remaining.items() if counts.get(group, 0) >= 2},
    )


def _save_source(
    context,
    work_dir: Path,
    work,
    page,
    *,
    on_boundary=None,
) -> None:
    from . import layer_command_runtime

    layer_command_runtime.commit_projection(context, operation="transfer.source")
    if on_boundary is not None:
        on_boundary()
    page_io.save_page_json(work_dir, page)
    if on_boundary is not None:
        on_boundary()
    work_io.save_work_json(work_dir, work)
    if on_boundary is not None:
        on_boundary()
    page_io.save_pages_json(work_dir, work)
    if on_boundary is not None:
        on_boundary()


def _backup_source_files(
    work_dir: Path,
    page_id: str,
    recovery_dir: Path,
) -> dict[Path, Path | None]:
    recovery_dir.mkdir(parents=True, exist_ok=True)
    source_paths = (
        paths.page_blend_path(work_dir, page_id),
        paths.page_meta_path(work_dir, page_id),
        paths.project_meta_path(work_dir),
    )
    backup: dict[Path, Path | None] = {}
    for index, source in enumerate(source_paths):
        if not source.is_file():
            backup[source] = None
            continue
        target = recovery_dir / f"{index}_{source.name}"
        shutil.copy2(source, target)
        backup[source] = target
    return backup


def _create_recovery_backup(
    context,
    work_dir: Path,
    work,
    source_page,
    source_page_id: str,
    target_page_id: str,
    stage_id: str,
    coma_moves: list[_ComaMove],
) -> tuple[Path, dict[Path, Path | None]]:
    recovery_dir = (
        paths.page_dir(work_dir, source_page_id)
        / _RECOVERY_DIR_NAME
        / stage_id
    )
    recovery_dir.mkdir(parents=True, exist_ok=False)
    rollback_backup: dict[Path, Path | None] = {}
    manifest_path = recovery_dir / _RECOVERY_MANIFEST_NAME
    try:
        rollback_backup = _backup_source_files(
            work_dir,
            source_page_id,
            recovery_dir / "rollback",
        )
        target_stage = cross_page_stage.asset_entry_snapshot(
            work_dir,
            target_page_id,
            stage_id,
        )
        if target_stage is None:
            raise RuntimeError("prepared target stage identity is missing")
        manifest = layer_transfer_recovery_manifest.build(
            work_dir,
            recovery_dir,
            source_page_id,
            target_page_id,
            stage_id,
            coma_moves,
            rollback_backup,
            target_stage,
            phase="preparing",
        )
        json_io.write_json(manifest_path, manifest)

        def record_known_state() -> None:
            layer_transfer_recovery_manifest.append_current_state(
                manifest_path,
                work_dir,
                manifest,
            )

        # 異常終了ではメモリを失うため、移送前の未保存状態も専用の復旧点へ
        # 保存する。通常の例外rollbackには開始前のdisk bytesを別途使う。
        _save_source(
            context,
            work_dir,
            work,
            source_page,
            on_boundary=record_known_state,
        )
        if not blend_io.save_page_blend(work_dir, source_page_id):
            raise RuntimeError("source recovery point save failed")
        record_known_state()
        recovery_backup = _backup_source_files(
            work_dir,
            source_page_id,
            recovery_dir / "crash",
        )
        layer_transfer_recovery_manifest.replace_backup(
            manifest_path,
            work_dir,
            manifest,
            recovery_backup,
            phase="prepared",
        )
        if not _restore_source_files(work_dir, rollback_backup):
            _mark_transfer_fail_closed(work)
            raise LayerTransferRollbackError(
                "ページ間移送の開始前ファイルを復元できませんでした"
            )
        from ..io.save_baseline import record_successful_tree_change

        record_successful_tree_change(recovery_dir)
        return recovery_dir, rollback_backup
    except LayerTransferRollbackError:
        raise
    except Exception as create_error:
        restored = not rollback_backup or _restore_source_files(
            work_dir,
            rollback_backup,
        )
        if not restored:
            _mark_transfer_fail_closed(work)
            raise LayerTransferRollbackError(
                "ページ間移送の復旧点作成失敗後に元ファイルを復元できませんでした"
            ) from create_error
        try:
            _remove_stage(work_dir, target_page_id, stage_id)
            if _recovery_stage_state(work_dir, target_page_id, stage_id):
                raise LayerTransferCleanupError(
                    "failed recovery-point stage remains"
                )
            if manifest_path.is_file():
                _set_recovery_terminal(
                    recovery_dir,
                    "rollback_applied",
                    work_dir=work_dir,
                )
            _remove_recovery_dir(recovery_dir)
        except Exception as cleanup_error:
            _mark_transfer_fail_closed(work)
            raise LayerTransferRollbackError(
                "ページ間移送の復旧点失敗後の資料を安全に破棄できませんでした"
            ) from cleanup_error
        raise


def _rollback(
    context,
    work,
    source_page,
    snapshots,
    work_dir,
    target_page_id,
    stage_id,
    moved_comas,
    backup,
    recovery_dir,
) -> None:
    failures: list[str] = []
    if stage_id:
        try:
            _remove_stage(work_dir, target_page_id, stage_id)
            if _recovery_stage_state(work_dir, target_page_id, stage_id):
                failures.append("stage")
        except Exception:  # noqa: BLE001
            failures.append("stage")
            _logger.exception("transfer stage rollback failed: %s", stage_id)
    for move in reversed(moved_comas):
        try:
            coma_io.move_coma_files(
                work_dir,
                target_page_id,
                str(getattr(source_page, "id", "") or ""),
                move.target_id,
                move.source_id,
            )
        except Exception:  # noqa: BLE001
            failures.append(f"coma:{move.source_id}")
            _logger.exception("coma file rollback failed: %s", move.source_id)
    try:
        schema.work_from_dict(work, snapshots["work"])
        restored_page = _page_by_id(work, str(snapshots["page"].get("id", "") or ""))
        if restored_page is not None:
            with schema._suspend_load_property_side_effects():
                asset_bundle._dict_to_pg(restored_page, snapshots["page"])
        layer_links._save_map(context, snapshots["links"])
        _restore_layer_objects(context, snapshots, str(getattr(source_page, "id", "") or ""))
        # 失敗経路では表示番号を再採番しない。完全スナップショットで元の順序も
        # 戻っており、再採番はcNN.jsonへの不要な書込みと競合検知を起こす。
        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        failures.append("memory")
        _logger.exception("transfer memory rollback failed")
    if not _restore_source_files(work_dir, backup):
        failures.append("source-files")
    elif backup:
        try:
            from . import layer_command_runtime

            layer_command_runtime.reload_active_page_from_repository(context)
        except Exception:  # noqa: BLE001
            failures.append("repository-reload")
            _logger.exception("transfer repository reload after rollback failed")
    if not failures:
        try:
            _set_recovery_terminal(
                recovery_dir,
                "rollback_applied",
                work_dir=work_dir,
            )
            _remove_recovery_dir(recovery_dir)
            return
        except Exception as cleanup_error:
            _mark_transfer_fail_closed(work)
            raise LayerTransferRollbackError(
                "ページ間移送の復元資料を安全に破棄できませんでした"
            ) from cleanup_error
    _mark_transfer_fail_closed(work)
    labels = ", ".join(dict.fromkeys(failures))
    raise LayerTransferRollbackError(
        f"ページ間移送の復元を完了できませんでした: {labels}"
    )


def _restore_source_files(
    work_dir: Path,
    backup: dict[Path, Path | None],
) -> bool:
    restored = True
    for destination, saved in backup.items():
        try:
            from ..io.project_file_lock import guard_path_write
            from ..io.save_baseline import record_successful_write

            with guard_path_write(destination):
                if saved is None:
                    destination.unlink(missing_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, destination)
                record_successful_write(destination)
        except Exception:  # noqa: BLE001
            restored = False
            _logger.exception("transfer file rollback failed: %s", destination)
    page_io.invalidate_page_json_write_cache(tuple(backup))
    if restored:
        try:
            from ..io import domain_runtime

            domain_runtime.repository_for(work_dir).accept_recovered_files(backup)
        except Exception:  # noqa: BLE001
            restored = False
            _logger.exception("transfer repository recovery baseline update failed")
    return restored


def _remove_recovery_dir(recovery_dir: Path | None) -> None:
    if recovery_dir is None:
        return
    recovery_dir = _recovery_cleanup_candidate(recovery_dir)
    if recovery_dir is None:
        return
    try:
        cleanup_dir = recovery_dir
        if not _is_recovery_tombstone(recovery_dir):
            cleanup_dir = recovery_dir.with_name(
                f".cleanup-{recovery_dir.name}-{uuid.uuid4().hex}"
            )
            os.replace(recovery_dir, cleanup_dir)
        shutil.rmtree(cleanup_dir)
        from ..io.save_baseline import record_successful_tree_change

        record_successful_tree_change(recovery_dir, cleanup_dir)
        parent = cleanup_dir.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception as exc:
        raise LayerTransferCleanupError(
            f"transfer recovery cleanup failed: {recovery_dir}"
        ) from exc


def _is_recovery_tombstone(path: Path) -> bool:
    name = path.name
    if not name.startswith(".cleanup-"):
        return False
    suffix = name.rsplit("-", 1)[-1]
    return len(suffix) == 32 and all(ch in "0123456789abcdef" for ch in suffix)


def _recovery_cleanup_candidate(path: Path) -> Path | None:
    if path.exists():
        return path
    candidates = tuple(
        candidate
        for candidate in path.parent.glob(f".cleanup-{path.name}-*")
        if candidate.is_dir() and _is_recovery_tombstone(candidate)
    )
    if len(candidates) > 1:
        raise LayerTransferCleanupError(
            f"multiple transfer cleanup tombstones exist: {path}"
        )
    return candidates[0] if candidates else None


def _tombstone_logical_path(path: Path) -> Path:
    name = path.name
    prefix = ".cleanup-"
    if not _is_recovery_tombstone(path) or not name.startswith(prefix):
        raise LayerTransferCleanupError(f"invalid recovery tombstone: {path}")
    stage_id = name[len(prefix):].rsplit("-", 1)[0]
    if not stage_id:
        raise LayerTransferCleanupError(f"invalid recovery tombstone stage: {path}")
    return path.with_name(stage_id)


def _set_recovery_terminal(
    recovery_dir: Path | None,
    phase: str,
    *,
    work_dir: Path,
) -> None:
    if recovery_dir is None:
        return
    recovery_dir = _recovery_cleanup_candidate(recovery_dir)
    if recovery_dir is None:
        return
    if _is_recovery_tombstone(recovery_dir):
        return
    manifest_path = recovery_dir / _RECOVERY_MANIFEST_NAME
    manifest = json_io.read_json(manifest_path)
    manifest, _backup = layer_transfer_recovery_manifest.validate(
        work_dir,
        recovery_dir,
        manifest,
    )
    layer_transfer_recovery_manifest.set_terminal(
        manifest_path,
        manifest,
        phase,
    )


def _record_recovery_current_state(
    recovery_dir: Path | None,
    work_dir: Path,
) -> None:
    if recovery_dir is None:
        raise LayerTransferRecoveryError("transfer recovery directory is missing")
    manifest_path = recovery_dir / _RECOVERY_MANIFEST_NAME
    manifest = json_io.read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise LayerTransferRecoveryError("transfer recovery manifest is invalid")
    layer_transfer_recovery_manifest.append_current_state(
        manifest_path,
        work_dir,
        manifest,
    )


def recover_interrupted_transfers(work_dir: Path) -> tuple[Path, ...]:
    """preparedのまま異常終了したページ間移送を次回起動時に復元する."""
    root = Path(work_dir).resolve()
    if not root.is_dir():
        return ()
    from ..io.project_file_lock import work_lock

    restored: set[Path] = set()
    with work_lock(root, blocking=True):
        recovery_root_dirs = sorted(
            path
            for path in root.glob(f"pages/page_*/{_RECOVERY_DIR_NAME}")
            if path.is_dir()
        )
        failures: list[str] = []
        for recovery_root in recovery_root_dirs:
            for tombstone in sorted(recovery_root.iterdir()):
                if not tombstone.is_dir() or not _is_recovery_tombstone(tombstone):
                    continue
                try:
                    tombstone_manifest = tombstone / _RECOVERY_MANIFEST_NAME
                    if tombstone_manifest.is_file():
                        logical_dir = _tombstone_logical_path(tombstone)
                        terminal = (
                            layer_transfer_recovery_manifest.validate_schema(
                                root,
                                logical_dir,
                                json_io.read_json(tombstone_manifest),
                            )
                        )
                        if terminal["phase"] not in (
                            layer_transfer_recovery_manifest.TERMINAL_PHASES
                        ):
                            raise LayerTransferRecoveryError(
                                "active recovery journal was found in a tombstone"
                            )
                    _remove_recovery_dir(tombstone)
                except Exception:
                    failures.append(str(tombstone))
                    _logger.exception(
                        "transfer recovery tombstone cleanup failed: %s",
                        tombstone,
                    )
        recovery_dirs = sorted(
            path
            for path in root.glob(f"pages/page_*/{_RECOVERY_DIR_NAME}/*")
            if path.is_dir() and not _is_recovery_tombstone(path)
        )
        manifests = [path / _RECOVERY_MANIFEST_NAME for path in recovery_dirs]
        # 壊れたmanifestも「復旧資料あり」とみなし、対応するprepared stageを
        # orphan掃除で消さない。資料を残せば再試行・手動救出ができる。
        journal_ids = {manifest_path.parent.name for manifest_path in manifests}
        for manifest_path in manifests:
            try:
                manifest = layer_transfer_recovery_manifest.validate_schema(
                    root,
                    manifest_path.parent,
                    json_io.read_json(manifest_path),
                )
                stage_id = manifest["stage_id"]
                source_page_id = manifest["source_page_id"]
                target_page_id = manifest["target_page_id"]
                phase = manifest["phase"]
                if phase in layer_transfer_recovery_manifest.TERMINAL_PHASES:
                    _remove_recovery_dir(manifest_path.parent)
                    continue
                state = _recovery_stage_state(root, target_page_id, stage_id)
                if state not in {"", "prepared", "ready"}:
                    raise LayerTransferRecoveryError(
                        f"unknown transfer stage state: {state}"
                    )
                if (
                    phase
                    == layer_transfer_recovery_manifest.TARGET_SAVED_PHASE
                ):
                    if state == "ready":
                        if not cross_page_stage.asset_entry_matches_snapshot(
                            root,
                            target_page_id,
                            stage_id,
                            manifest["target_stage"],
                            state="ready",
                        ):
                            raise LayerTransferRecoveryError(
                                "saved transfer stage identity does not match journal"
                            )
                        cross_page_stage.discard_asset_bundle_stage_strict(
                            root,
                            target_page_id,
                            stage_id,
                            lock_held=True,
                        )
                    elif state:
                        raise LayerTransferRecoveryError(
                            "saved transfer has an invalid stage state"
                        )
                    layer_transfer_recovery_manifest.set_terminal(
                        manifest_path,
                        manifest,
                        "committed",
                    )
                    _remove_recovery_dir(manifest_path.parent)
                    continue
                if state == "ready":
                    if not cross_page_stage.asset_entry_matches_snapshot(
                        root,
                        target_page_id,
                        stage_id,
                        manifest["target_stage"],
                        state="ready",
                    ):
                        raise LayerTransferRecoveryError(
                            "ready transfer stage identity does not match journal"
                        )
                    manifest, _backup = layer_transfer_recovery_manifest.validate(
                        root,
                        manifest_path.parent,
                        manifest,
                    )
                    _assert_ready_target_ownership(root, manifest)
                    # readyは移送元の保存が終わっただけで、移送先page.blend /
                    # page.jsonへの実体化・保存はまだ終わっていない。移送先保存後の
                    # finalize_target_transfer_stage()まで復旧資料を保持する。
                    continue
                manifest, backup = layer_transfer_recovery_manifest.validate(
                    root,
                    manifest_path.parent,
                    manifest,
                )
                layer_transfer_recovery_manifest.assert_current_allowed(
                    root,
                    manifest_path.parent,
                    manifest,
                )
                if not _restore_manifest_comas(root, manifest):
                    failures.append(f"{manifest_path}: coma")
                    continue
                if not _restore_source_files(root, backup):
                    failures.append(f"{manifest_path}: source-files")
                    continue
                _remove_stage(root, target_page_id, stage_id)
                if _recovery_stage_state(root, target_page_id, stage_id):
                    failures.append(f"{manifest_path}: stage")
                    continue
                source_blend = paths.page_blend_path(root, source_page_id)
                if backup.get(source_blend) is not None:
                    restored.add(source_blend)
                layer_transfer_recovery_manifest.set_terminal(
                    manifest_path,
                    manifest,
                    "rollback_applied",
                )
                _remove_recovery_dir(manifest_path.parent)
            except Exception:  # noqa: BLE001
                failures.append(str(manifest_path))
                _logger.exception("interrupted transfer recovery failed: %s", manifest_path)
        _remove_orphan_prepared_stages(root, journal_ids)
        if failures:
            labels = ", ".join(dict.fromkeys(failures))
            raise LayerTransferRecoveryError(
                f"ページ間移送の起動時復旧を完了できませんでした: {labels}"
            )
    return tuple(sorted(restored, key=str))


def _has_transfer_recovery_root(work_dir: Path) -> bool:
    pages_root = Path(work_dir) / paths.PAGES_DIR_NAME
    try:
        entries = os.scandir(pages_root)
    except OSError:
        return False
    with entries:
        for entry in entries:
            if (
                not paths.is_valid_page_uid(entry.name)
                or entry.is_symlink()
                or not entry.is_dir(follow_symlinks=False)
            ):
                continue
            try:
                if (Path(entry.path) / _RECOVERY_DIR_NAME).is_dir():
                    return True
            except OSError:
                return True
    return False


def has_transfer_recovery_journal(work_dir: Path) -> bool:
    """openを止めて同期復旧すべきjournal/tombstoneがあるか返す."""

    root = Path(work_dir).resolve()
    return root.is_dir() and _has_transfer_recovery_root(root)


def _assert_ready_target_ownership(work_dir: Path, manifest: dict) -> None:
    """ready stageが参照する移送先folderを永続Domain上で検証する."""

    target_stage = manifest.get("target_stage")
    entry = target_stage.get("entry") if isinstance(target_stage, dict) else None
    payload = entry.get("payload") if isinstance(entry, dict) else None
    transfer = payload.get("transfer") if isinstance(payload, dict) else None
    if not isinstance(transfer, dict):
        return
    source_page_id = str(manifest.get("source_page_id", "") or "")
    target_page_id = str(manifest.get("target_page_id", "") or "")
    if (
        str(transfer.get("sourcePageId", "") or "") != source_page_id
        or str(transfer.get("targetPageId", "") or "") != target_page_id
    ):
        raise LayerTransferRecoveryError(
            "ready transfer payload ownership does not match journal"
        )
    folder_key = str(transfer.get("targetFolderKey", "") or "")
    folder_owner = str(transfer.get("targetFolderOwnerPageId", "") or "")
    if not folder_key:
        if folder_owner:
            raise LayerTransferRecoveryError(
                "ready transfer has a folder owner without a folder"
            )
        return
    if folder_owner != target_page_id:
        raise LayerTransferRecoveryError(
            "ready transfer target folder owner does not match journal"
        )
    from ..io import domain_runtime

    repository = domain_runtime.repository_for(work_dir)
    document = repository.load_page(
        paths.resolve_page_uid(work_dir, target_page_id)
    )
    matches = [
        node
        for node in document.nodes.values()
        if node.kind == "folder" and node.display_id == folder_key
    ]
    if len(matches) != 1:
        raise LayerTransferRecoveryError(
            "ready transfer target folder is missing or has moved"
        )


def mark_target_transfer_stage_saved(
    work_dir: Path,
    target_page_id: str,
    stage_id: str,
    *,
    target_saved: bool = False,
) -> bool:
    """移送先のnative/Domain保存成功をstage削除前に耐久記録する."""

    root = Path(work_dir).resolve()
    stage_name = str(stage_id or "")
    if (
        not root.is_dir()
        or not paths.is_valid_page_id(str(target_page_id or ""))
        or not stage_name
        or Path(stage_name).name != stage_name
        or target_saved is not True
    ):
        raise LayerTransferRecoveryError("invalid transfer target saved identity")
    candidates = tuple(
        path
        for path in root.glob(
            f"{paths.PAGES_DIR_NAME}/page_*/{_RECOVERY_DIR_NAME}/{stage_name}"
        )
        if path.is_dir() and not path.is_symlink()
    )
    if not candidates:
        # 通常素材stageにはtransfer journalが無い。
        return False
    if len(candidates) != 1:
        raise LayerTransferRecoveryError(
            "duplicate transfer recovery journals were found"
        )
    recovery_dir = candidates[0]
    manifest_path = recovery_dir / _RECOVERY_MANIFEST_NAME
    manifest, _backup = layer_transfer_recovery_manifest.validate(
        root,
        recovery_dir,
        json_io.read_json(manifest_path),
    )
    if (
        manifest["stage_id"] != stage_name
        or manifest["target_page_id"] != str(target_page_id)
    ):
        raise LayerTransferRecoveryError(
            "transfer target finalization identity does not match journal"
        )
    if not cross_page_stage.asset_entry_matches_snapshot(
        root,
        str(target_page_id),
        stage_name,
        manifest["target_stage"],
        state="ready",
    ):
        raise LayerTransferRecoveryError(
            "saved transfer stage does not match journal"
        )
    layer_transfer_recovery_manifest.set_target_saved(
        manifest_path,
        manifest,
    )
    return True


def finalize_target_transfer_stage(
    work_dir: Path,
    target_page_id: str,
    stage_id: str,
    *,
    target_saved: bool = False,
) -> bool:
    """stage耐久削除後にjournalをcommit済みとして閉じる."""

    root = Path(work_dir).resolve()
    stage_name = str(stage_id or "")
    if (
        not root.is_dir()
        or not paths.is_valid_page_id(str(target_page_id or ""))
        or not stage_name
        or Path(stage_name).name != stage_name
        or target_saved is not True
    ):
        raise LayerTransferRecoveryError("invalid transfer target finalization identity")
    candidates = tuple(
        path
        for path in root.glob(
            f"{paths.PAGES_DIR_NAME}/page_*/{_RECOVERY_DIR_NAME}/{stage_name}"
        )
        if path.is_dir() and not path.is_symlink()
    )
    if not candidates:
        return False
    if len(candidates) != 1:
        raise LayerTransferRecoveryError(
            "duplicate transfer recovery journals were found"
        )
    recovery_dir = candidates[0]
    manifest_path = recovery_dir / _RECOVERY_MANIFEST_NAME
    manifest = layer_transfer_recovery_manifest.validate_schema(
        root,
        recovery_dir,
        json_io.read_json(manifest_path),
    )
    if (
        manifest["stage_id"] != stage_name
        or manifest["target_page_id"] != str(target_page_id)
        or manifest["phase"]
        != layer_transfer_recovery_manifest.TARGET_SAVED_PHASE
    ):
        raise LayerTransferRecoveryError(
            "transfer target finalization identity does not match journal"
        )
    if cross_page_stage.asset_entry_snapshot(
        root,
        str(target_page_id),
        stage_name,
    ) is not None:
        raise LayerTransferRecoveryError(
            "transfer target stage still exists during finalization"
        )
    layer_transfer_recovery_manifest.set_terminal(
        manifest_path,
        manifest,
        "committed",
    )
    _remove_recovery_dir(recovery_dir)
    return True


def _load_recovery_files(
    work_dir: Path,
    recovery_dir: Path,
    manifest: dict,
) -> dict[Path, Path | None]:
    backup: dict[Path, Path | None] = {}
    for record in manifest.get("files", []) or []:
        if not isinstance(record, dict):
            raise ValueError("invalid recovery file record")
        relative = Path(str(record.get("relative_path", "") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe recovery destination")
        destination = (work_dir / relative).resolve()
        if not destination.is_relative_to(work_dir):
            raise ValueError("recovery destination escaped work directory")
        if not bool(record.get("existed", False)):
            backup[destination] = None
            continue
        saved = (recovery_dir / str(record.get("backup_name", "") or "")).resolve()
        if not saved.is_relative_to(recovery_dir.resolve()) or not saved.is_file():
            raise FileNotFoundError(saved)
        backup[destination] = saved
    return backup


def _restore_manifest_comas(work_dir: Path, manifest: dict) -> bool:
    source_page_id = str(manifest.get("source_page_id", "") or "")
    target_page_id = str(manifest.get("target_page_id", "") or "")
    restored = True
    for record in reversed(manifest.get("coma_moves", []) or []):
        if not isinstance(record, dict) or not bool(record.get("source_existed", True)):
            continue
        source_id = str(record.get("source_id", "") or "")
        target_id = str(record.get("target_id", "") or "")
        source_dir = paths.coma_dir(work_dir, source_page_id, source_id)
        target_dir = paths.coma_dir(work_dir, target_page_id, target_id)
        if source_dir.is_dir() and not target_dir.exists():
            continue
        if not source_dir.exists() and target_dir.is_dir():
            try:
                coma_io.move_coma_files(
                    work_dir,
                    target_page_id,
                    source_page_id,
                    target_id,
                    source_id,
                )
                continue
            except Exception:  # noqa: BLE001
                _logger.exception("interrupted coma recovery failed: %s", source_id)
        restored = False
    return restored


def _recovery_stage_state(work_dir: Path, page_id: str, stage_id: str) -> str:
    data = cross_page_stage._read(cross_page_stage.staged_path(work_dir, page_id))
    for entry in data.get(cross_page_stage.ASSET_ENTRIES_KEY, []) or []:
        if (
            isinstance(entry, dict)
            and str(entry.get("stage_id", "") or "") == stage_id
        ):
            return str(entry.get("state", "ready") or "ready")
    return ""


def _remove_orphan_prepared_stages(work_dir: Path, journal_ids: set[str]) -> None:
    pages_root = work_dir / paths.PAGES_DIR_NAME
    if not pages_root.is_dir():
        return
    for page_dir in pages_root.iterdir():
        if not page_dir.is_dir() or not paths.is_valid_page_uid(page_dir.name):
            continue
        try:
            page_id = paths.page_display_id(work_dir, page_dir.name)
        except (KeyError, ValueError):
            continue
        data = cross_page_stage._read(cross_page_stage.staged_path(work_dir, page_id))
        for entry in tuple(data.get(cross_page_stage.ASSET_ENTRIES_KEY, []) or []):
            if not isinstance(entry, dict):
                continue
            stage_id = str(entry.get("stage_id", "") or "")
            state = str(entry.get("state", "ready") or "ready")
            if stage_id and state == "prepared" and stage_id not in journal_ids:
                _remove_stage(work_dir, page_id, stage_id)


def _restore_layer_objects(context, snapshots, page_id: str) -> None:
    for data in snapshots.get("effects", {}).values():
        if isinstance(data, dict):
            cross_page_stage._restore_effect(context, data, page_id)
    for data in snapshots.get("gps", {}).values():
        if not isinstance(data, dict):
            continue
        parent_key = str(data.get("parent_key", page_id) or page_id)
        cross_page_gp_transfer.create_object(context, data, parent_key)
    try:
        from . import layer_object_sync

        layer_object_sync.mirror_work_to_outliner(
            context.scene,
            get_work(context),
            allow_object_writeback=False,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("transfer display rollback sync failed")


def _remove_stage(work_dir: Path, page_id: str, stage_id: str) -> None:
    path = cross_page_stage.staged_path(work_dir, page_id)
    data = cross_page_stage._read(path)
    tokens = {
        cross_page_stage._entry_token("asset", entry)
        for entry in data.get(cross_page_stage.ASSET_ENTRIES_KEY, [])
        if isinstance(entry, dict)
        and str(entry.get("stage_id", "") or "") == str(stage_id or "")
    }
    cross_page_stage._remove_processed_entries(
        work_dir,
        page_id,
        {"asset": {token for token in tokens if token}},
    )


def _page_by_id(work, page_id: str):
    if work is None:
        return None
    return next(
        (
            page
            for page in getattr(work, "pages", []) or []
            if str(getattr(page, "id", "") or "") == str(page_id or "")
        ),
        None,
    )


def _page_index(work, page_id: str) -> int:
    if work is None:
        return -1
    return next(
        (
            index
            for index, page in enumerate(getattr(work, "pages", []) or [])
            if str(getattr(page, "id", "") or "") == str(page_id or "")
        ),
        -1,
    )


__all__ = [
    "LayerTransferCleanupError",
    "LayerTransferRecoveryError",
    "LayerTransferRollbackError",
    "TransferGroup",
    "build_transfer_group",
    "has_transfer_recovery_journal",
    "finalize_target_transfer_stage",
    "mark_target_transfer_stage_saved",
    "recover_interrupted_transfers",
    "transfer_group_to_page",
]
