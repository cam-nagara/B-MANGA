"""Blender投影を一度だけDomain Commandへ確定するレイヤー操作境界。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from . import log


_logger = log.get_logger(__name__)


class LayerCommandRollbackError(RuntimeError):
    """Layer Command失敗後の正式状態復元も完遂できなかった。"""

    def __init__(
        self,
        operation: str,
        operation_error: BaseException,
        rollback_error: BaseException,
    ) -> None:
        self.operation = operation
        self.operation_error = operation_error
        self.rollback_error = rollback_error
        super().__init__(
            f"Layer Command「{operation}」のrollbackを完遂できませんでした: "
            f"{type(rollback_error).__name__}: {rollback_error}"
        )


@dataclass(frozen=True)
class LayerMemorySnapshot:
    work_payload: dict
    page_payload: dict
    page_id: str
    links: dict[str, str]
    effects: dict[str, dict | None]
    gps: dict[str, dict | None]
    runtime_ids: dict[str, frozenset[str]]
    stack_uids: tuple[str, ...]
    selected_uids: tuple[str, ...]
    active_uid: str


def capture(context, items) -> LayerMemorySnapshot:
    """一操作で触れ得る正式投影を、変更前に完全退避する。"""

    from ..core.work import get_work
    from ..io import schema
    from . import asset_bundle, layer_links, layer_stack

    work = get_work(context)
    page = _active_page(work)
    if work is None or page is None:
        raise RuntimeError("layer command requires an active page")
    del items
    effects, gps = _capture_native_layers(str(getattr(page, "id", "") or ""))
    runtime_ids = _capture_runtime_object_ids(context)
    stack_uids = _stack_order_snapshot(context)
    selected, active = _selection_snapshot(context)
    return LayerMemorySnapshot(
        work_payload=copy.deepcopy(schema.work_to_dict(work)),
        page_payload=copy.deepcopy(asset_bundle._pg_to_dict(page)),
        page_id=str(getattr(page, "id", "") or ""),
        links=copy.deepcopy(layer_links._load_map(context)),
        effects=effects,
        gps=gps,
        runtime_ids=runtime_ids,
        stack_uids=stack_uids,
        selected_uids=selected,
        active_uid=active,
    )


def restore(context, snapshot: LayerMemorySnapshot) -> None:
    """失敗したレイヤー操作を、Domain確定前の投影へ戻す。"""

    from ..core.work import get_work
    from ..io import schema
    from . import (
        asset_bundle,
        layer_links,
        layer_stack,
        layer_stack_command_runtime,
        layer_transfer_group,
    )

    work = get_work(context)
    if work is None:
        raise RuntimeError("layer command rollback has no work")
    with layer_stack_command_runtime.suppress_commits():
        schema.work_from_dict(work, copy.deepcopy(snapshot.work_payload))
        page = _page_by_id(work, snapshot.page_id)
        if page is None:
            raise RuntimeError("layer command rollback page is missing")
        with schema._suspend_load_property_side_effects():
            asset_bundle._dict_to_pg(
                page,
                copy.deepcopy(snapshot.page_payload),
                raw_scalars=True,
            )
        layer_links._save_map(context, copy.deepcopy(snapshot.links))
        _restore_domain_bindings(work, page)
        _remove_runtime_object_extras(context, snapshot.runtime_ids)
        _replace_native_layers(
            context,
            snapshot,
            snapshot.page_id,
        )
        layer_stack.sync_layer_stack_after_data_change(
            context,
            allow_object_writeback=False,
            strict=True,
        )
        _restore_stack_order(context, snapshot.stack_uids)
        layer_stack.remember_layer_stack_signature(context)
        _restore_selection(context, snapshot)
        _restore_stack_order(context, snapshot.stack_uids)
        layer_stack.remember_layer_stack_signature(context)


def _restore_domain_bindings(work, page) -> None:
    """PropertyGroup再生成で失われたDomain UID bindingを正本から戻す。"""

    from ..io import domain_projection, domain_runtime

    work_dir = Path(str(getattr(work, "work_dir", "") or ""))
    if not work_dir.is_dir():
        raise RuntimeError("layer command rollback Domain store is unavailable")
    store = domain_runtime.store_for(work_dir)
    page_uid = _page_uid(page)
    document = store.pages.get(page_uid)
    if document is None:
        raise RuntimeError("layer command rollback Domain page is missing")
    domain_projection.bind_project_document(work, store.project)
    domain_projection.bind_page_document(page, document)


def commit_projection(context, *, operation: str) -> None:
    """現在のBlender投影を、共通のLayer Commandで一度だけ確定する。"""

    from ..core.work import get_work
    from ..io import domain_projection, domain_projection_tree, domain_runtime
    from . import history_runtime

    if history_runtime.mutation_blocked(context):
        raise RuntimeError("履歴復元が未完了のためレイヤー変更を保存できません")

    work = get_work(context)
    page = _active_page(work)
    work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
    if work is None or page is None or work_dir is None or not work_dir.is_dir():
        raise RuntimeError("layer command projection is not writable")
    projected_project = domain_projection.project_document_from_work(work)
    store = domain_runtime.store_for(work_dir, initial_project=projected_project)
    page_uid = domain_projection.ensure_page_uid(
        page,
        store.project.project_uid,
    )
    current_page = store.pages.get(page_uid)
    if current_page is None:
        raise RuntimeError("layer command requires a hydrated Domain page")
    candidate_page = domain_projection.page_document_from_projection(
        work,
        page,
        context=context,
        preserve_document=current_page,
        preserve_missing_projection=False,
    )
    order = domain_projection_tree.tree_order(candidate_page)
    if len(order) != len(candidate_page.nodes) - 1 or len(order) != len(set(order)):
        raise RuntimeError("layer command produced an invalid LayerOrder")
    with store.transaction():
        event_types = _apply_projection_commands(
            store,
            projected_project,
            candidate_page,
            operation,
        )
        document = store.pages[page_uid]
        domain_projection.bind_project_document(work, store.project)
        domain_projection.bind_page_document(page, document)
    _logger.info(
        "layer command committed: events=%s page_uid=%s revision=%d nodes=%d",
        ",".join(event_types),
        page_uid,
        document.revision,
        len(order),
    )


def _apply_projection_commands(
    store,
    projected_project,
    candidate_page,
    operation: str,
) -> tuple[str, ...]:
    from ..bmanga_core.domain_store import (
        ApplyProjectPatch,
        project_patch,
    )
    from ..bmanga_core.layer_commands import ApplyLayerMutation
    from ..io import domain_projection

    current_project = store.project
    candidate_project = domain_projection.preserve_project_projection(
        current_project,
        projected_project,
    )
    delta = project_patch(current_project, candidate_project)
    event_types: list[str] = []
    if not delta.is_empty:
        event_types.append(store.execute(ApplyProjectPatch(delta)).event_type)
    current_page = store.pages[candidate_page.page_uid]
    event_types.append(
        store.execute(
            ApplyLayerMutation(
                page_uid=candidate_page.page_uid,
                candidate=candidate_page,
                expected_revision=current_page.revision,
                operation=operation,
            )
        ).event_type
    )
    return tuple(event_types)


def reload_active_page_from_repository(context) -> None:
    """disk rollback後のRepository世代をStoreと投影へ戻す。"""

    from ..core.work import get_work
    from ..io import domain_projection, domain_runtime

    work = get_work(context)
    page = _active_page(work)
    work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
    if page is None or work_dir is None:
        raise RuntimeError("layer rollback has no active page")
    page_uid = _page_uid(page)
    repository = domain_runtime.repository_for(work_dir)
    document = repository.load_page(page_uid)
    domain_runtime.hydrate_page(work_dir, document)
    domain_projection.bind_page_document(page, document)


def restore_active_page_from_domain(context) -> None:
    """Command拒否時に現行Domainを正式投影と実体へ戻す。"""

    from ..core.work import get_work
    from ..io import domain_layer_order, domain_projection, domain_runtime
    from . import layer_stack, layer_stack_command_runtime

    work = get_work(context)
    page = _active_page(work)
    work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
    if page is None or work_dir is None:
        raise RuntimeError("layer rollback has no active Domain page")
    store = domain_runtime.store_for(work_dir)
    page_uid = _page_uid(page)
    document = store.pages.get(page_uid)
    if document is None:
        raise RuntimeError("layer rollback Domain page is missing")
    with layer_stack_command_runtime.suppress_commits():
        domain_projection.apply_project_document(work, store.project)
        active_index = next(
            (
                index
                for index, entry in enumerate(work.pages)
                if _page_uid(entry) == page_uid
            ),
            -1,
        )
        if active_index < 0:
            raise RuntimeError("layer rollback page projection is missing")
        work.active_page_index = active_index
        page = work.pages[active_index]
        domain_projection.apply_page_document(page, document, context=context)
        layer_stack.sync_layer_stack_after_data_change(
            context,
            allow_object_writeback=False,
            strict=True,
        )
        domain_layer_order.project_document_order(context, document)
        layer_stack.remember_layer_stack_signature(context)


def execute(
    context,
    *,
    items,
    operation: str,
    mutate,
    before_restore=None,
) -> int:
    """同一ページ操作をsnapshot→変更→Command確定の一取引で実行する。"""

    from . import history_runtime

    if history_runtime.mutation_blocked(context):
        return 0
    snapshot = capture(context, items)

    def _restore_transaction() -> None:
        if before_restore is not None:
            before_restore()
        restore(context, snapshot)

    try:
        changed = int(mutate())
        if changed <= 0:
            try:
                _restore_transaction()
            except Exception as rollback_error:
                operation_error = RuntimeError(
                    f"layer command {operation} returned no changes"
                )
                raise fail_closed_rollback(
                    context,
                    operation=operation,
                    operation_error=operation_error,
                    rollback_error=rollback_error,
                ) from rollback_error
            return 0
        commit_projection(context, operation=operation)
        return changed
    except LayerCommandRollbackError:
        raise
    except Exception as operation_error:
        _logger.exception("layer command failed: %s", operation)
        try:
            _restore_transaction()
        except Exception as rollback_error:
            _logger.exception("layer command rollback failed: %s", operation)
            raise fail_closed_rollback(
                context,
                operation=operation,
                operation_error=operation_error,
                rollback_error=rollback_error,
            ) from rollback_error
        return 0


def mark_fail_closed(context) -> None:
    """復元未完了の正式状態を保存・追加編集できない状態へ移す。"""

    try:
        from ..core.work import get_work

        work = get_work(context)
        if work is not None:
            work.loaded = False
    except Exception:  # noqa: BLE001
        _logger.exception("layer command fail-closed transition failed")


def fail_closed_rollback(
    context,
    *,
    operation: str,
    operation_error: BaseException,
    rollback_error: BaseException,
) -> LayerCommandRollbackError:
    mark_fail_closed(context)
    return LayerCommandRollbackError(
        operation,
        operation_error,
        rollback_error,
    )


def execution_roots(context, items) -> tuple[object, ...]:
    """子孫とフキダシ付随テキストの二重実行を除いた操作rootを返す。"""

    from . import layer_stack

    values = tuple(items)
    selected_uids = {layer_stack.stack_item_uid(item) for item in values}
    containers = {
        str(getattr(item, "key", "") or ""): item
        for item in values
        if str(getattr(item, "kind", "") or "") in {"coma", "layer_folder"}
    }
    balloon_ids = {
        str(getattr(resolved.get("target"), "id", "") or "")
        for item in values
        if str(getattr(item, "kind", "") or "") == "balloon"
        for resolved in (layer_stack.resolve_stack_item(context, item),)
        if resolved is not None
    }
    result = []
    for item in values:
        if _has_selected_ancestor(item, containers, selected_uids):
            continue
        if _is_attached_text_of_selected_balloon(context, item, balloon_ids):
            continue
        result.append(item)
    return tuple(result)


def _has_selected_ancestor(item, containers, selected_uids: set[str]) -> bool:
    from . import layer_stack

    parent_key = str(getattr(item, "parent_key", "") or "")
    seen: set[str] = set()
    while parent_key and parent_key not in seen:
        seen.add(parent_key)
        parent = containers.get(parent_key)
        if parent is None:
            return False
        if layer_stack.stack_item_uid(parent) in selected_uids:
            return True
        parent_key = str(getattr(parent, "parent_key", "") or "")
    return False


def _is_attached_text_of_selected_balloon(context, item, balloon_ids) -> bool:
    from . import layer_stack

    if str(getattr(item, "kind", "") or "") != "text":
        return False
    resolved = layer_stack.resolve_stack_item(context, item)
    target = resolved.get("target") if resolved is not None else None
    return str(getattr(target, "parent_balloon_id", "") or "") in balloon_ids


def _selection_snapshot(context) -> tuple[tuple[str, ...], str]:
    from . import layer_stack

    scene = context.scene
    stack = getattr(scene, "bmanga_layer_stack", ())
    active_index = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    selected = tuple(
        layer_stack.stack_item_uid(item)
        for item in stack
        if layer_stack.is_item_selected(context, item)
    )
    active = (
        layer_stack.stack_item_uid(stack[active_index])
        if 0 <= active_index < len(stack)
        else ""
    )
    return selected, active


def _stack_order_snapshot(context) -> tuple[str, ...]:
    from . import layer_stack

    result: list[str] = []
    for item in getattr(context.scene, "bmanga_layer_stack", ()):
        try:
            uid = layer_stack.stack_item_uid(item)
        except ValueError:
            continue
        if uid:
            result.append(uid)
    return tuple(result)


def _restore_stack_order(context, ordered_uids: tuple[str, ...]) -> None:
    from . import layer_stack

    stack = getattr(context.scene, "bmanga_layer_stack", None)
    if stack is None:
        return
    current_uids: list[str] = []
    for item in stack:
        try:
            current_uids.append(layer_stack.stack_item_uid(item))
        except ValueError:
            current_uids.append("")
    desired = [uid for uid in ordered_uids if uid in current_uids]
    for target_index, uid in enumerate(desired):
        current_index = current_uids.index(uid)
        if current_index >= 0 and current_index != target_index:
            stack.move(current_index, target_index)
            moved_uid = current_uids.pop(current_index)
            current_uids.insert(target_index, moved_uid)


def _restore_selection(context, snapshot: LayerMemorySnapshot) -> None:
    from . import layer_stack

    stack = getattr(context.scene, "bmanga_layer_stack", ())
    selected = set(snapshot.selected_uids)
    active_index = -1
    for index, item in enumerate(stack):
        uid = layer_stack.stack_item_uid(item)
        layer_stack.set_item_selected(context, item, uid in selected)
        if uid == snapshot.active_uid:
            active_index = index
    if active_index >= 0:
        layer_stack.set_active_stack_index_silently(context, active_index)


def _capture_native_layers(page_id: str) -> tuple[dict, dict]:
    from . import cross_page_gp_transfer, cross_page_transfer, layer_object_model

    snapshots = {"effect": {}, "gp": {}}
    for kind in snapshots:
        for obj in layer_object_model.iter_layer_objects(kind):
            if layer_object_model.parent_key(obj).split(":", 1)[0] != page_id:
                continue
            stable_id = str(layer_object_model.stable_id(obj) or "")
            payload = (
                cross_page_transfer._extract_effect_meta(stable_id)
                if kind == "effect"
                else cross_page_gp_transfer.serialize_object(stable_id)
            )
            if not stable_id or not isinstance(payload, dict):
                raise RuntimeError(f"{kind} layer cannot be snapshotted")
            snapshots[kind][stable_id] = payload
    return snapshots["effect"], snapshots["gp"]


def _capture_runtime_object_ids(context) -> dict[str, frozenset[str]]:
    """同一ページCommandが生成し得る非Native実体の開始集合を固定する。"""

    from . import object_naming as on

    kinds = ("balloon", "text", "image", "image_path", "raster", "fill")
    values: dict[str, set[str]] = {kind: set() for kind in kinds}
    for obj in tuple(getattr(context.scene, "objects", ()) or ()):
        kind = str(obj.get(on.PROP_KIND, "") or "")
        identity = str(obj.get(on.PROP_ID, "") or "")
        if kind in values and identity:
            values[kind].add(identity)
    return {kind: frozenset(ids) for kind, ids in values.items()}


def _remove_runtime_object_extras(
    context,
    expected: dict[str, frozenset[str]],
) -> None:
    """操作中に生成され、snapshot復元後は正式PGを持たない実体を除去する。"""

    from ..operators import raster_layer_op
    from . import (
        balloon_curve_object,
        fill_real_object,
        image_path_object,
        image_real_object,
        object_naming as on,
        text_real_object,
    )

    actual = _capture_runtime_object_ids(context)
    extras = {
        kind: sorted(actual.get(kind, frozenset()) - expected.get(kind, frozenset()))
        for kind in actual
    }
    removers = {
        "balloon": balloon_curve_object.remove_balloon_objects_by_id,
        "image": image_real_object.remove_image_real_object,
        "image_path": image_path_object.remove_image_path_object,
        "raster": raster_layer_op._purge_raster_runtime_by_id,  # noqa: SLF001
        "fill": fill_real_object.remove_fill_real_object,
    }
    failures: list[str] = []
    for kind, identities in extras.items():
        for identity in identities:
            try:
                if kind == "text":
                    page_id, text_id = (
                        text_real_object.split_text_object_bmanga_id(identity)
                    )
                    removed = text_real_object.remove_text_real_object(
                        page_id,
                        text_id,
                    )
                else:
                    removed = removers[kind](identity)
                if removed is False:
                    # 一部helperは対象Datablockが既に無い場合Falseを返す。
                    still_exists = any(
                        str(obj.get(on.PROP_KIND, "") or "") == kind
                        and str(obj.get(on.PROP_ID, "") or "") == identity
                        for obj in tuple(getattr(context.scene, "objects", ()) or ())
                    )
                    if still_exists:
                        failures.append(f"{kind}:{identity}")
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "layer rollback extra runtime object removal failed: %s:%s",
                    kind,
                    identity,
                )
                failures.append(f"{kind}:{identity}")
    if failures:
        raise RuntimeError(
            "layer rollback could not remove extra runtime objects: "
            + ", ".join(failures)
        )


def _replace_native_layers(
    context,
    snapshot: LayerMemorySnapshot,
    page_id: str,
) -> None:
    from . import layer_object_model, layer_transfer_group

    for kind in ("effect", "gp"):
        for obj in tuple(layer_object_model.iter_layer_objects(kind)):
            if layer_object_model.parent_key(obj).split(":", 1)[0] == page_id:
                if not layer_object_model.remove_layer_object(obj):
                    raise RuntimeError(f"{kind} layer rollback removal failed")
    layer_transfer_group._restore_layer_objects(
        context,
        {"effects": snapshot.effects, "gps": snapshot.gps},
        page_id,
    )


def _active_page(work):
    if work is None:
        return None
    index = int(getattr(work, "active_page_index", -1))
    pages = getattr(work, "pages", ())
    return pages[index] if 0 <= index < len(pages) else None


def _page_by_id(work, page_id: str):
    return next(
        (
            page
            for page in getattr(work, "pages", ())
            if str(getattr(page, "id", "") or "") == page_id
        ),
        None,
    )


def _page_uid(page) -> str:
    from ..io.domain_projection_ids import PAGE_UID_PROP, custom_get

    return str(custom_get(page, PAGE_UID_PROP, "") or "")


__all__ = (
    "LayerCommandRollbackError",
    "LayerMemorySnapshot",
    "capture",
    "commit_projection",
    "execution_roots",
    "execute",
    "fail_closed_rollback",
    "mark_fail_closed",
    "reload_active_page_from_repository",
    "restore_active_page_from_domain",
    "restore",
)
