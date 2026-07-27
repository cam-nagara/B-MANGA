"""Alt+D&D が使う、選択・子孫・リンクを閉包したページ間移送."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

import bpy

from ..core.work import get_work
from ..io import blend_io, coma_io, page_io, schema, work_io
from . import (
    asset_bundle,
    cross_page_gp_transfer,
    cross_page_stage,
    layer_links,
    layer_stack as layer_stack_utils,
    log,
    page_file_scene,
    page_grid,
    paths,
)
from .layer_hierarchy import coma_stack_key, split_child_key


_logger = log.get_logger(__name__)
_TRANSFERABLE_KINDS = frozenset(asset_bundle.SUPPORTED_LAYER_KINDS)


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


def build_transfer_group(context, *, anchor_item=None) -> TransferGroup | None:
    """現在選択から子孫・リンク・フキダシ/テキストの閉包を作る."""
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return None
    items = list(stack)
    active_index = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
    active_item = items[active_index] if 0 <= active_index < len(items) else None
    anchor_item = anchor_item or active_item
    selected = {
        layer_stack_utils.stack_item_uid(item)
        for index, item in enumerate(items)
        if index == active_index or layer_stack_utils.is_item_selected(context, item)
    }
    if anchor_item is not None:
        selected.add(layer_stack_utils.stack_item_uid(anchor_item))
    selected.discard("")
    if not selected:
        return None
    selected = _expand_descendants(items, selected)
    selected = _expand_links_and_text_pairs(context, items, selected)
    selected = _expand_descendants(items, selected)
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
    source_page_id = _source_page_id(context, group_items)
    center = _item_world_center(context, anchor, group_items)
    if not source_page_id or center is None:
        return None
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
    if target is None or getattr(target, "kind", "") not in {"page", "coma"}:
        return None
    target_page = getattr(target, "page", None)
    target_page_id = str(getattr(target_page, "id", "") or "")
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
    final_drop = drop_world_xy_mm or group.anchor_world_xy_mm
    return _execute_cross_page(context, group, target_page, final_drop)


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
            if str(getattr(item, "parent_key", "") or "") in containers:
                result.add(uid)
                changed = True
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


def _execute_cross_page(context, group: TransferGroup, target_page, drop_world_xy_mm) -> int:
    work = get_work(context)
    work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
    source_page = _page_by_id(work, group.source_page_id)
    target_page_id = str(getattr(target_page, "id", "") or "")
    if work is None or work_dir is None or source_page is None or not work_dir.is_dir():
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
    from ..io.project_content_migration_lock import work_lock

    with tempfile.TemporaryDirectory(prefix="bmanga_transfer_") as temp_name:
        backup = _backup_source_files(work_dir, group.source_page_id, Path(temp_name))
        try:
            with work_lock(work_dir, blocking=True):
                stage_id = cross_page_stage.stage_asset_bundle(
                    work_dir,
                    target_page_id,
                    payload,
                    target_local,
                    ready=False,
                )
                if not stage_id:
                    raise RuntimeError("destination staging failed")
                moved_comas = _move_coma_files(
                    work_dir,
                    group.source_page_id,
                    target_page_id,
                    coma_moves,
                )
                if not _remove_source_group(context, group):
                    raise RuntimeError("source group removal failed")
                _remove_source_links(context, group.uids)
                _save_source(work_dir, work, source_page)
                if not blend_io.save_page_blend(work_dir, group.source_page_id):
                    raise RuntimeError("source page.blend save failed")
                if not cross_page_stage.mark_asset_bundle_ready(
                    work_dir,
                    target_page_id,
                    stage_id,
                ):
                    raise RuntimeError("destination staging commit failed")
            layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
            return len(group.items)
        except Exception:  # noqa: BLE001
            _logger.exception("transfer group transaction failed")
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
            )
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


def _move_coma_files(work_dir, source_page_id, target_page_id, moves) -> list[_ComaMove]:
    completed = []
    for move in moves:
        coma_io.move_coma_files(
            work_dir,
            source_page_id,
            target_page_id,
            move.source_id,
            move.target_id,
        )
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


def _save_source(work_dir: Path, work, page) -> None:
    page_io.save_page_json(work_dir, page)
    work_io.save_work_json(work_dir, work)
    page_io.save_pages_json(work_dir, work)


def _backup_source_files(work_dir: Path, page_id: str, temp_dir: Path) -> dict[Path, Path]:
    source_paths = (
        paths.page_blend_path(work_dir, page_id),
        paths.page_meta_path(work_dir, page_id),
        paths.work_meta_path(work_dir),
        paths.pages_meta_path(work_dir),
    )
    backup = {}
    for index, source in enumerate(source_paths):
        if not source.is_file():
            continue
        target = temp_dir / f"{index}_{source.name}"
        shutil.copy2(source, target)
        backup[source] = target
    return backup


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
) -> None:
    if stage_id:
        _remove_stage(work_dir, target_page_id, stage_id)
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
        _logger.exception("transfer memory rollback failed")
    for destination, saved in backup.items():
        try:
            from ..io.project_content_migration_lock import guard_path_write
            from ..io.project_content_save_baseline import record_successful_write

            with guard_path_write(destination):
                shutil.copy2(saved, destination)
                record_successful_write(destination)
        except Exception:  # noqa: BLE001
            _logger.exception("transfer file rollback failed: %s", destination)


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


__all__ = ["TransferGroup", "build_transfer_group", "transfer_group_to_page"]
