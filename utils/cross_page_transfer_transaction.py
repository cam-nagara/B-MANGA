"""クロスページ移動のロック内トランザクション実装。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
import uuid


_STAGED_KINDS = frozenset({"effect", "gp"})


@dataclass
class _PendingObject:
    kind: str
    bmanga_id: str
    source_data: dict
    token: str


@dataclass
class _TransferPlan:
    context: object
    work: object
    source_page: object
    work_dir: Path
    source_page_id: str
    target_page_id: str
    layer_items: list
    target_data: dict
    target_original: dict
    source_offset: tuple[float, float]
    target_offset: tuple[float, float]
    target_parent_kind: str
    target_parent_key: str
    target_folder_id: str
    transferred: int = 0
    entries_to_remove: list[tuple[str, str]] = field(default_factory=list)
    balloon_id_map: dict[str, str] = field(default_factory=dict)
    text_id_map: dict[str, str] = field(default_factory=dict)
    copied_rasters: list[Path] = field(default_factory=list)
    staged_tokens: dict[str, set[str]] = field(
        default_factory=lambda: {"effect": set(), "gp": set(), "asset": set(), "link": set()}
    )
    pending_objects: list[_PendingObject] = field(default_factory=list)
    removed_pending: list[_PendingObject] = field(default_factory=list)
    removed_display_entries: list[tuple[str, str]] = field(default_factory=list)
    source_snapshots: list[tuple[str, str, dict, int]] = field(default_factory=list)
    target_written: bool = False
    source_modified: bool = False
    transfer_id: str = field(default_factory=lambda: f"move_{uuid.uuid4().hex}")
    uid_map: dict[str, str] = field(default_factory=dict)
    allocated_ids: set[str] = field(default_factory=set)
    source_link_original: dict[str, str] = field(default_factory=dict)
    link_modified: bool = False
    source_blend_save_attempted: bool = False


def _build_plan(
    context,
    work,
    source_page,
    target_page_id: str,
    layer_items: list,
    target_parent_kind: str,
    target_coma_id: str,
    target_folder_id: str,
    drop_world_xy_mm: tuple[float, float] | None,
) -> _TransferPlan | None:
    from . import cross_page_transfer as base

    work_dir = base._work_dir(work)
    source_id = str(getattr(source_page, "id", "") or "")
    if work_dir is None or not source_id or not target_page_id or source_id == target_page_id:
        return None
    pages = list(getattr(work, "pages", []) or [])
    source_index = next(
        (i for i, page in enumerate(pages) if str(getattr(page, "id", "") or "") == source_id),
        -1,
    )
    if source_index < 0:
        return None
    target_data = base._read_target_page_json(work_dir, target_page_id)
    if target_data is None:
        base._logger.warning("target page.json not found: %s", target_page_id)
        return None
    scene = getattr(context, "scene", None)
    source_offset = base._page_offset_mm(work, scene, source_index)
    target_offset = base._target_page_offset_mm(work, scene, target_page_id)
    parent_kind, coma_id = _resolve_target_parent(
        target_data,
        target_offset,
        target_parent_kind,
        target_coma_id,
        drop_world_xy_mm,
    )
    parent_key = f"{target_page_id}:{coma_id}" if parent_kind == "coma" and coma_id else target_page_id
    from . import layer_links

    return _TransferPlan(
        context=context,
        work=work,
        source_page=source_page,
        work_dir=work_dir,
        source_page_id=source_id,
        target_page_id=target_page_id,
        layer_items=list(layer_items),
        target_data=target_data,
        target_original=copy.deepcopy(target_data),
        source_offset=source_offset,
        target_offset=target_offset,
        target_parent_kind=parent_kind,
        target_parent_key=parent_key,
        target_folder_id=str(target_folder_id or ""),
        source_link_original=layer_links._load_map(context),
    )


def _resolve_target_parent(
    target_data: dict,
    target_offset: tuple[float, float],
    parent_kind: str,
    coma_id: str,
    drop_world_xy_mm: tuple[float, float] | None,
) -> tuple[str, str]:
    from . import cross_page_transfer as base

    if drop_world_xy_mm is None or coma_id:
        return parent_kind, coma_id
    local_x = drop_world_xy_mm[0] - target_offset[0]
    local_y = drop_world_xy_mm[1] - target_offset[1]
    resolved = base._resolve_coma_from_json(target_data, local_x, local_y)
    return ("coma", resolved) if resolved else (parent_kind, coma_id)


def _item_entry_id(item) -> tuple[str, str]:
    from .layer_hierarchy import split_child_key

    kind = str(getattr(item, "kind", "") or "")
    _page_id, entry_id = split_child_key(str(getattr(item, "key", "") or ""))
    return kind, entry_id


def _append_non_text_entry(plan: _TransferPlan, item) -> bool:
    from . import cross_page_transfer as base

    kind, entry_id = _item_entry_id(item)
    if kind in {"text", *_STAGED_KINDS}:
        return True
    if kind not in base._SUPPORTED_KINDS or not entry_id:
        return False
    entry = base._find_entry_in_page(plan.source_page, kind, entry_id)
    entry_dict = base._serialize_entry(entry, kind) if entry is not None else None
    list_key = base._json_list_key(kind)
    if entry_dict is None or list_key is None:
        return False
    entry_dict = base._convert_coords(entry_dict, plan.source_offset, plan.target_offset)
    base._set_parent_in_dict(entry_dict, plan.target_parent_kind, plan.target_parent_key)
    plan.target_data.setdefault(list_key, [])
    new_id = _allocate_json_identity(plan, kind, entry_id, entry_dict, list_key)
    if not new_id:
        return False
    if kind == "balloon":
        if not _append_balloon_children(plan, entry_id, new_id):
            return False
        for key in ("textId", "text_id"):
            old_text_id = str(entry_dict.get(key, "") or "")
            if old_text_id in plan.text_id_map:
                entry_dict[key] = plan.text_id_map[old_text_id]
        plan.balloon_id_map[entry_id] = new_id
    plan.target_data[list_key].append(entry_dict)
    plan.entries_to_remove.append((kind, entry_id))
    _record_page_uid(plan, kind, entry_id, new_id)
    plan.transferred += 1
    return True


def _allocate_json_identity(
    plan: _TransferPlan,
    kind: str,
    entry_id: str,
    entry_dict: dict,
    list_key: str,
) -> str:
    from . import cross_page_transfer as base

    if kind == "raster":
        path = base._copy_raster_image(
            plan.work_dir,
            plan.source_page_id,
            entry_dict,
            plan.target_data,
        )
        if path is None:
            return ""
        plan.copied_rasters.append(path)
        return str(entry_dict.get("id", "") or "")
    existing = base._existing_ids_in_json(plan.target_data, list_key)
    new_id = base._unique_id(existing, entry_id, kind)
    entry_dict["id"] = new_id
    return new_id


def _record_page_uid(plan: _TransferPlan, kind: str, old_id: str, new_id: str) -> None:
    from . import layer_stack

    old_uid = layer_stack.target_uid(kind, f"{plan.source_page_id}:{old_id}")
    new_uid = layer_stack.target_uid(kind, f"{plan.target_page_id}:{new_id}")
    plan.uid_map[old_uid] = new_uid


def _append_balloon_children(plan: _TransferPlan, balloon_id: str, new_id: str) -> bool:
    from . import cross_page_transfer as base

    for text_id in base._collect_child_text_ids(plan.source_page, balloon_id):
        new_text_id = base._transfer_child_text(
            plan.source_page,
            plan.target_data,
            text_id,
            new_id,
            plan.source_offset,
            plan.target_offset,
            plan.target_parent_kind,
            plan.target_parent_key,
            plan.entries_to_remove,
        )
        if not new_text_id:
            return False
        plan.text_id_map[text_id] = new_text_id
        _record_page_uid(plan, "text", text_id, new_text_id)
    return True


def _append_text_entry(plan: _TransferPlan, item) -> bool:
    from . import cross_page_transfer as base

    kind, entry_id = _item_entry_id(item)
    if kind != "text" or not entry_id or ("text", entry_id) in plan.entries_to_remove:
        return kind != "text" or bool(entry_id)
    entry = base._find_entry_in_page(plan.source_page, "text", entry_id)
    entry_dict = base._serialize_entry(entry, "text") if entry is not None else None
    if entry_dict is None:
        return False
    entry_dict = base._convert_coords(entry_dict, plan.source_offset, plan.target_offset)
    base._set_parent_in_dict(entry_dict, plan.target_parent_kind, plan.target_parent_key)
    old_parent = str(entry_dict.get("parentBalloonId", "") or "")
    if old_parent in plan.balloon_id_map:
        entry_dict["parentBalloonId"] = plan.balloon_id_map[old_parent]
    else:
        # 親フキダシを同じ転送に含めていない子テキストは、移動先で同じIDを
        # 持つ無関係なフキダシへ誤接続しないよう、単独テキストへ戻す。
        entry_dict["parentBalloonId"] = ""
    plan.target_data.setdefault("texts", [])
    existing = base._existing_ids_in_json(plan.target_data, "texts")
    new_id = base._unique_id(existing, entry_id, "text")
    entry_dict["id"] = new_id
    plan.target_data["texts"].append(entry_dict)
    plan.entries_to_remove.append(("text", entry_id))
    _record_page_uid(plan, "text", entry_id, new_id)
    plan.transferred += 1
    return True


def _prepare_json(plan: _TransferPlan) -> bool:
    for item in plan.layer_items:
        if not _append_non_text_entry(plan, item):
            return False
    for item in plan.layer_items:
        if not _append_text_entry(plan, item):
            return False
    return True


def _allocate_managed_id(plan: _TransferPlan, kind: str) -> str:
    from . import cross_page_stage, layer_object_model

    staged = cross_page_stage._read(cross_page_stage.staged_path(plan.work_dir, plan.target_page_id))
    key = "effects" if kind == "effect" else "gp_layers"
    existing = {
        str(entry.get("bmanga_id", "") or "")
        for entry in staged.get(key, []) if isinstance(entry, dict)
    }
    for _attempt in range(128):
        candidate = layer_object_model.make_stable_id(kind)
        if candidate not in existing and candidate not in plan.allocated_ids:
            plan.allocated_ids.add(candidate)
            return candidate
    return ""


def _stage_effect(plan: _TransferPlan, item) -> bool:
    from . import cross_page_stage
    from . import cross_page_transfer as base

    if str(getattr(item, "kind", "") or "") != "effect":
        return True
    bmanga_id = base._layer_object_id(item, "effect")
    source_data = base._extract_effect_meta(bmanga_id) if bmanga_id else None
    if source_data is None:
        return False
    target_data = copy.deepcopy(source_data)
    dx = plan.source_offset[0] - plan.target_offset[0]
    dy = plan.source_offset[1] - plan.target_offset[1]
    target_data["x"] = float(target_data.get("x", 0)) + dx
    target_data["y"] = float(target_data.get("y", 0)) + dy
    if "center_x" in target_data and "center_y" in target_data:
        target_data["center_x"] = float(target_data["center_x"]) + dx
        target_data["center_y"] = float(target_data["center_y"]) + dy
        target_data["center_xy_mm"] = [target_data["center_x"], target_data["center_y"]]
    target_data["parent_key"] = plan.target_parent_key
    target_data["folder_id"] = plan.target_folder_id
    new_id = _allocate_managed_id(plan, "effect")
    if not new_id:
        return False
    target_data["bmanga_id"] = new_id
    target_data["source_bmanga_id"] = bmanga_id
    if not _record_staged_object(plan, "effect", bmanga_id, source_data, target_data):
        return False
    plan.uid_map[f"effect:{bmanga_id}"] = f"effect:{new_id}"
    return True


def _stage_gp(plan: _TransferPlan, item) -> bool:
    from . import cross_page_gp_transfer
    from . import cross_page_transfer as base

    if str(getattr(item, "kind", "") or "") != "gp":
        return True
    bmanga_id = base._layer_object_id(item, "gp")
    source_data = cross_page_gp_transfer.serialize_object(bmanga_id) if bmanga_id else None
    if source_data is None:
        return False
    target_data = copy.deepcopy(source_data)
    target_data["parent_key"] = plan.target_parent_key
    target_data["folder_id"] = plan.target_folder_id
    new_id = _allocate_managed_id(plan, "gp")
    if not new_id:
        return False
    target_data["bmanga_id"] = new_id
    target_data["source_bmanga_id"] = bmanga_id
    if not _record_staged_object(plan, "gp", bmanga_id, source_data, target_data):
        return False
    plan.uid_map[f"gp:{bmanga_id}"] = f"gp:{new_id}"
    return True


def _record_staged_object(
    plan: _TransferPlan,
    kind: str,
    bmanga_id: str,
    source_data: dict,
    target_data: dict,
) -> bool:
    from . import cross_page_stage

    stage = cross_page_stage.stage_effect if kind == "effect" else cross_page_stage.stage_gp
    if not stage(plan.work_dir, plan.target_page_id, target_data):
        return False
    token = cross_page_stage._entry_token(kind, target_data)
    if not token:
        return False
    plan.staged_tokens[kind].add(token)
    plan.pending_objects.append(_PendingObject(kind, bmanga_id, source_data, token))
    plan.entries_to_remove.append((kind, bmanga_id))
    plan.transferred += 1
    return True


def _prepare_stages(plan: _TransferPlan) -> bool:
    for item in plan.layer_items:
        if not _stage_effect(plan, item):
            return False
    for item in plan.layer_items:
        if not _stage_gp(plan, item):
            return False
    return True


def _moved_link_groups(plan: _TransferPlan) -> list[list[str]]:
    grouped: dict[str, list[str]] = {}
    for old_uid, new_uid in plan.uid_map.items():
        group_id = plan.source_link_original.get(old_uid, "")
        if group_id and new_uid not in grouped.setdefault(group_id, []):
            grouped[group_id].append(new_uid)
    return [uids for uids in grouped.values() if len(uids) >= 2]


def _prepare_link_stage(plan: _TransferPlan) -> bool:
    from . import cross_page_stage

    groups = _moved_link_groups(plan)
    if not groups:
        return True
    entry = {"transfer_id": plan.transfer_id, "groups": groups}
    if not cross_page_stage.stage_link_transfer(plan.work_dir, plan.target_page_id, entry):
        return False
    token = cross_page_stage._entry_token("link", entry)
    if not token:
        return False
    plan.staged_tokens["link"].add(token)
    return True


def _apply_source_link_removal(plan: _TransferPlan) -> None:
    from . import layer_links

    moved = set(plan.uid_map)
    mapping = {
        uid: group
        for uid, group in plan.source_link_original.items()
        if uid not in moved
    }
    counts: dict[str, int] = {}
    for group in mapping.values():
        counts[group] = counts.get(group, 0) + 1
    mapping = {uid: group for uid, group in mapping.items() if counts.get(group, 0) >= 2}
    if mapping != plan.source_link_original:
        layer_links._save_map(plan.context, mapping)
        plan.link_modified = True


def _collect_source_snapshots(plan: _TransferPlan) -> None:
    from . import cross_page_transfer as base

    seen: set[tuple[str, str]] = set()
    for kind, entry_id in plan.entries_to_remove:
        key = (kind, entry_id)
        if kind in _STAGED_KINDS or key in seen:
            continue
        snapshot = base._source_entry_snapshot(plan.source_page, kind, entry_id)
        if snapshot is not None:
            plan.source_snapshots.append(snapshot)
            seen.add(key)


def _rollback(plan: _TransferPlan, staged_tokens: dict[str, set[str]] | None = None) -> None:
    from . import cross_page_transfer as base

    base._rollback_transfer(
        plan.work_dir,
        plan.source_page,
        plan.target_page_id,
        plan.target_original,
        plan.source_snapshots,
        plan.copied_rasters,
        staged_tokens if staged_tokens is not None else plan.staged_tokens,
        source_was_modified=plan.source_modified,
        target_was_written=plan.target_written,
    )


def _save_json_transaction(plan: _TransferPlan) -> bool:
    from ..io import page_io
    from . import cross_page_transfer as base

    has_json = any(kind not in _STAGED_KINDS for kind, _entry_id in plan.entries_to_remove)
    if not has_json:
        return True
    if not base._write_target_page_json(plan.work_dir, plan.target_page_id, plan.target_data):
        return False
    plan.target_written = True
    plan.source_modified = True
    for kind, entry_id in plan.entries_to_remove:
        if kind not in _STAGED_KINDS and not base._remove_entry_from_page(plan.source_page, kind, entry_id):
            base._logger.error("source entry removal failed: %s/%s", kind, entry_id)
            return False
    try:
        page_io.save_page_json(plan.work_dir, plan.source_page)
    except Exception:  # noqa: BLE001
        base._logger.exception("source page.json save failed")
        return False
    return True


def _remove_pending_object(pending: _PendingObject) -> bool:
    from . import cross_page_gp_transfer
    from . import cross_page_transfer as base

    if pending.kind == "effect":
        return base._remove_effect_objects(pending.bmanga_id)
    return cross_page_gp_transfer.remove_object(pending.bmanga_id)


def _restore_pending_object(plan: _TransferPlan, pending: _PendingObject) -> bool:
    from . import cross_page_gp_transfer, cross_page_stage

    if pending.kind == "effect":
        return cross_page_stage._restore_effect(
            plan.context,
            pending.source_data,
            plan.source_page_id,
        ) is not None
    parent_key = str(pending.source_data.get("parent_key", plan.source_page_id) or plan.source_page_id)
    return cross_page_gp_transfer.create_object(
        plan.context,
        pending.source_data,
        parent_key,
    ) is not None


def _tokens_without(plan: _TransferPlan, protected: set[str]) -> dict[str, set[str]]:
    return {
        kind: set(tokens) - protected
        for kind, tokens in plan.staged_tokens.items()
    }


def _finalize_objects(plan: _TransferPlan) -> bool:
    from . import cross_page_transfer as base

    for pending in plan.pending_objects:
        if _remove_pending_object(pending):
            plan.removed_pending.append(pending)
            continue
        base._logger.error("source object removal failed: %s/%s", pending.kind, pending.bmanga_id)
        return False
    return _remove_source_display_objects(plan)


def _remove_source_display_objects(plan: _TransferPlan) -> bool:
    """一覧データと別に残るフキダシ／テキスト実体も移動元から除く。"""
    from . import balloon_curve_object, cross_page_transfer as base, text_real_object

    seen: set[tuple[str, str]] = set()
    for kind, entry_id in plan.entries_to_remove:
        key = (kind, entry_id)
        if key in seen or kind not in {"balloon", "text"}:
            continue
        seen.add(key)
        if kind == "balloon":
            existed = balloon_curve_object.find_balloon_object(entry_id) is not None
            removed = balloon_curve_object.remove_balloon_objects_by_id(entry_id)
            if balloon_curve_object.find_balloon_object(entry_id) is not None:
                base._logger.error("source balloon object removal failed: %s", entry_id)
                return False
        else:
            existed = text_real_object.find_text_object(plan.source_page_id, entry_id) is not None
            removed = text_real_object.remove_text_real_object(plan.source_page_id, entry_id)
            if text_real_object.find_text_object(plan.source_page_id, entry_id) is not None:
                base._logger.error("source text object removal failed: %s", entry_id)
                return False
        if existed or removed:
            plan.removed_display_entries.append(key)
    return True


def _restore_source_display_objects(plan: _TransferPlan) -> None:
    """保存失敗時、復元済み一覧データから移動元の表示実体を作り直す。"""
    from . import balloon_curve_object, cross_page_transfer as base, text_real_object

    scene = getattr(plan.context, "scene", None)
    priorities = {"balloon": 0, "text": 1}
    for kind, entry_id in sorted(
        plan.removed_display_entries,
        key=lambda item: priorities.get(item[0], 9),
    ):
        entry = base._find_entry_in_page(plan.source_page, kind, entry_id)
        if entry is None:
            base._logger.error("source display rollback entry missing: %s/%s", kind, entry_id)
            continue
        try:
            if kind == "balloon":
                restored = balloon_curve_object.ensure_balloon_curve_object(
                    scene=scene,
                    entry=entry,
                    page=plan.source_page,
                )
            else:
                restored = text_real_object.ensure_text_real_object(
                    scene=scene,
                    entry=entry,
                    page=plan.source_page,
                )
            if restored is None:
                base._logger.error("source display rollback failed: %s/%s", kind, entry_id)
        except Exception:  # noqa: BLE001
            base._logger.exception("source display rollback failed: %s/%s", kind, entry_id)


def _restore_source_memory(plan: _TransferPlan) -> set[str]:
    from . import cross_page_transfer as base, layer_links

    base._restore_source_entries(plan.source_page, plan.source_snapshots)
    _restore_source_display_objects(plan)
    layer_links._save_map(plan.context, plan.source_link_original)
    failed = {
        pending.token
        for pending in plan.removed_pending
        if not _restore_pending_object(plan, pending)
    }
    if failed:
        base._logger.error("source object rollback incomplete; destination stage retained")
    return failed


def _save_source_blend(plan: _TransferPlan) -> bool:
    from ..io import blend_io

    plan.source_blend_save_attempted = True
    states = []
    for page in getattr(plan.work, "pages", []) or []:
        if str(getattr(page, "id", "") or "") == plan.source_page_id:
            continue
        loaded = bool(getattr(page, "detail_loaded", False))
        states.append((page, loaded))
        if loaded:
            page.detail_loaded = False
    try:
        return bool(blend_io.save_page_blend(plan.work_dir, plan.source_page_id))
    finally:
        for page, loaded in states:
            page.detail_loaded = loaded


def _abort_transaction(plan: _TransferPlan) -> None:
    from . import cross_page_transfer as base

    protected = _restore_source_memory(plan)
    if plan.source_blend_save_attempted and not _save_source_blend(plan):
        base._logger.error("source page rollback save failed; object stages retained")
        protected.update(
            token
            for kind in _STAGED_KINDS
            for token in plan.staged_tokens.get(kind, set())
        )
    _rollback(plan, _tokens_without(plan, protected))


def execute_locked(
    context,
    work,
    source_page,
    target_page_id: str,
    layer_items: list,
    *,
    target_parent_kind: str,
    target_coma_id: str,
    target_folder_id: str,
    drop_world_xy_mm: tuple[float, float] | None,
) -> int:
    """ロック取得済みの移動を、計画→書込→確定の順で実行する。"""
    from . import cross_page_transfer as base

    if base.unsupported_layer_kinds(layer_items):
        return 0
    plan = _build_plan(
        context,
        work,
        source_page,
        target_page_id,
        layer_items,
        target_parent_kind,
        target_coma_id,
        target_folder_id,
        drop_world_xy_mm,
    )
    if plan is None:
        return 0
    try:
        if not _prepare_json(plan):
            _abort_transaction(plan)
            return 0
        if not _prepare_stages(plan) or not _prepare_link_stage(plan):
            _abort_transaction(plan)
            return 0
        if plan.transferred == 0:
            return 0
        _collect_source_snapshots(plan)
        if not _save_json_transaction(plan):
            _abort_transaction(plan)
            return 0
        if not _finalize_objects(plan):
            _abort_transaction(plan)
            return 0
        _apply_source_link_removal(plan)
        if not _save_source_blend(plan):
            _abort_transaction(plan)
            return 0
        return plan.transferred
    except Exception:  # noqa: BLE001
        base._logger.exception("cross-page transfer execution failed")
        _abort_transaction(plan)
        return 0
