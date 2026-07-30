"""コマのページ間移動をDomain/Native/Blenderメモリの一括取引にする。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil
import tempfile

from ..bmanga_core.domain_store import (
    ApplyPagePatch,
    ApplyProjectPatch,
    page_patch,
    project_patch,
)
from ..utils import layer_reparent, layer_stack, log, page_grid, paths
from . import (
    coma_io,
    coma_move_recovery,
    domain_projection,
    domain_projection_ids,
    domain_runtime,
    native_tree_transaction,
    page_io,
    page_operation_transaction,
    schema,
)
from .project_file_lock import guard_path_write, work_lock
from .save_baseline import (
    record_successful_tree_change,
    record_successful_write,
    restore_baseline_registry,
    snapshot_baseline_registry,
)


FaultHook = Callable[[str], None]
_logger = log.get_logger(__name__)


class ComaMoveTransactionError(RuntimeError):
    """コマ移動またはその完全復元に失敗した。"""


def _call_fault(hook: FaultHook | None, phase: str) -> None:
    if hook is not None:
        hook(phase)


def _ensure_page_loaded(work_dir: Path, page) -> None:
    if not bool(getattr(page, "detail_loaded", False)):
        page_io.load_page_json(work_dir, page)


def _copy_coma_entry(source, target) -> None:
    schema.coma_entry_from_dict(target, schema.coma_entry_to_dict(source))


def _project_page_entry_only(page, document) -> None:
    """Scene共通collectionを触らず、ページ固有PropertyGroupだけを投影する。"""

    payload = domain_projection.page_payload_from_document(
        document,
        display_id=str(getattr(page, "id", "") or ""),
        title=str(getattr(page, "title", "") or ""),
        spread=bool(getattr(page, "spread", False)),
    )
    with schema._suspend_load_property_side_effects():
        schema.page_from_dict(page, payload)
        domain_projection.bind_page_document(page, document)
        page.detail_loaded = True


def _page_index(work, page_id: str) -> int:
    return next(
        (
            index
            for index, candidate in enumerate(work.pages)
            if str(getattr(candidate, "id", "") or "") == page_id
        ),
        -1,
    )


def _rollback_published_directory(
    source: Path,
    destination: Path,
    published: bool,
) -> None:
    if not published:
        return
    if not source.is_dir():
        raise ComaMoveTransactionError(
            f"コマ移動の原本が見つかりません: {source}"
        )
    if destination.is_dir():
        shutil.rmtree(destination)
    elif destination.exists():
        raise ComaMoveTransactionError(
            f"コマ移動の公開先がディレクトリではありません: {destination}"
        )


def _rollback_runtime(
    context,
    work,
    snapshot,
    *,
    old_parent_key: str,
    new_parent_key: str,
    dx_mm: float,
    dy_mm: float,
    translation_started: bool,
    gp_reparent_started: bool,
    effect_reparent_started: bool,
) -> None:
    page_operation_transaction._restore_memory(context, work, snapshot)
    if effect_reparent_started:
        layer_stack.reparent_effect_layers(context, new_parent_key, old_parent_key)
    if gp_reparent_started:
        layer_stack.reparent_gp_layers(context, new_parent_key, old_parent_key)
    if translation_started:
        layer_stack.translate_effect_layers_for_parent_keys(
            context,
            {old_parent_key},
            -dx_mm,
            -dy_mm,
        )


def _raise_rollback_failure(original: BaseException, errors: list[BaseException]) -> None:
    if not errors:
        raise original
    detail = "; ".join(str(error) for error in errors)
    raise ComaMoveTransactionError(
        "コマ移動を完全には復元できませんでした。作品を開き直してください"
        + (f": {detail}" if detail else "")
    ) from errors[0]


def move_coma_to_page(
    context,
    work,
    source_page,
    target_page,
    source_index: int,
    *,
    fault_hook: FaultHook | None = None,
) -> str:
    """選択コマを別ページへ移し、全保存対象が揃った時だけ確定する。"""

    work_dir = Path(str(getattr(work, "work_dir", "") or "")).resolve(strict=True)
    with work_lock(work_dir, blocking=True):
        with guard_path_write(paths.project_meta_path(work_dir)):
            pass
        _ensure_page_loaded(work_dir, source_page)
        if source_page == target_page:
            raise ComaMoveTransactionError("同じページへは移動できません")
        if not 0 <= source_index < len(source_page.comas):
            raise ComaMoveTransactionError("移動元のコマが見つかりません")

        project_uid = domain_projection.ensure_project_uid(work)
        source_page_uid = domain_projection.ensure_page_uid(
            source_page, project_uid
        )
        target_page_uid = domain_projection.ensure_page_uid(
            target_page, project_uid
        )
        repository = domain_runtime.repository_for(work_dir)
        repository.recover()
        native_tree_transaction.recover_pending_native_transactions(
            work_dir,
            repository=repository,
        )
        coma_move_recovery.recover_interrupted_coma_moves(
            work_dir,
            repository=repository,
        )
        persisted_project = repository.load_project()
        source_base_document = repository.load_page(source_page_uid)
        target_base_document = repository.load_page(target_page_uid)
        domain_runtime.store_for(
            work_dir,
            initial_project=persisted_project,
        )
        domain_runtime.hydrate_page(work_dir, source_base_document)
        domain_runtime.hydrate_page(work_dir, target_base_document)
        target_was_loaded = bool(getattr(target_page, "detail_loaded", False))
        snapshot = page_operation_transaction._capture_memory(context, work)
        baseline = snapshot_baseline_registry()
        if not target_was_loaded:
            _project_page_entry_only(target_page, target_base_document)
        source_entry = source_page.comas[source_index]
        source_page_id = str(source_page.id)
        target_page_id = str(target_page.id)
        source_coma_uid = domain_projection_ids.ensure_coma_uid(
            source_entry, source_page_uid
        )
        destination_id = coma_io.allocate_new_coma_id(
            work_dir,
            target_page_id,
            page=target_page,
        )
        source_dir = (
            repository.page_dir(source_page_uid)
            / paths.COMAS_DIR_NAME
            / source_coma_uid
        )

        old_parent_key = layer_stack.gp_parent_key_for_coma(
            source_page, source_entry
        )
        new_parent_key = ""
        dx_mm = 0.0
        dy_mm = 0.0
        translation_started = False
        gp_reparent_started = False
        effect_reparent_started = False
        directory_published = False
        destination_dir: Path | None = None
        transaction_root = Path(tempfile.mkdtemp(
            prefix=f".{work_dir.name}.coma-move-",
            dir=str(work_dir.parent),
        ))
        staged_dir = transaction_root / "native"

        try:
            new_entry = target_page.comas.add()
            _copy_coma_entry(source_entry, new_entry)
            new_entry.coma_id = destination_id
            new_entry.id = destination_id
            new_entry.title = source_entry.title
            destination_coma_uid = domain_projection_ids.ensure_coma_uid(
                new_entry, target_page_uid
            )
            destination_dir = (
                repository.page_dir(target_page_uid)
                / paths.COMAS_DIR_NAME
                / destination_coma_uid
            )
            new_parent_key = layer_stack.gp_parent_key_for_coma(
                target_page, new_entry
            )
            source_page_index = _page_index(work, source_page_id)
            target_page_index = _page_index(work, target_page_id)
            if source_page_index >= 0 and target_page_index >= 0:
                src_x, src_y = page_grid.page_total_offset_mm(
                    work, context.scene, source_page_index
                )
                dst_x, dst_y = page_grid.page_total_offset_mm(
                    work, context.scene, target_page_index
                )
                dx_mm, dy_mm = dst_x - src_x, dst_y - src_y
                translation_started = True
                layer_stack.translate_effect_layers_for_parent_keys(
                    context, {old_parent_key}, dx_mm, dy_mm
                )
            _call_fault(fault_hook, "after_effect_translation")

            gp_reparent_started = True
            layer_stack.reparent_gp_layers(
                context, old_parent_key, new_parent_key
            )
            _call_fault(fault_hook, "after_gp_reparent")
            effect_reparent_started = True
            layer_stack.reparent_effect_layers(
                context, old_parent_key, new_parent_key
            )
            _call_fault(fault_hook, "after_effect_reparent")

            layer_reparent._move_page_coma_children_to_page(
                context,
                source_page,
                target_page,
                str(getattr(source_entry, "coma_id", "") or ""),
                str(getattr(source_entry, "id", "") or ""),
                old_parent_key,
                new_parent_key,
            )
            _call_fault(fault_hook, "after_child_projection")

            source_page.comas.remove(source_index)
            if not source_page.comas:
                source_page.active_coma_index = -1
            elif source_index >= len(source_page.comas):
                source_page.active_coma_index = len(source_page.comas) - 1
            source_page.coma_count = len(source_page.comas)
            target_page.coma_count = len(target_page.comas)
            _call_fault(fault_hook, "before_checkpoint")

            projected_project = domain_projection.project_document_from_work(work)
            projected_source = domain_projection.page_document_from_projection(
                work, source_page, context=context
            )
            projected_target = domain_projection.page_document_from_projection(
                work,
                target_page,
                context=context,
                preserve_document=target_base_document,
            )
            store = domain_runtime.store_for(
                work_dir,
                initial_project=projected_project,
            )
            with store.transaction():
                store.execute(
                    ApplyProjectPatch(
                        project_patch(
                            store.project,
                            projected_project,
                            require_candidate_revision=False,
                        )
                    )
                )
                store.execute(
                    ApplyPagePatch(
                        page_patch(
                            store.pages.get(projected_source.page_uid),
                            projected_source,
                            require_candidate_revision=False,
                        )
                    )
                )
                store.execute(
                    ApplyPagePatch(
                        page_patch(
                            store.pages.get(projected_target.page_uid),
                            projected_target,
                            require_candidate_revision=False,
                        )
                    )
                )
                project_document = store.project
                source_document = store.pages[source_page_uid]
                target_document = store.pages[target_page_uid]

                def install_native_directory() -> bool:
                    nonlocal directory_published
                    if source_dir.exists():
                        if destination_dir is None:
                            raise ComaMoveTransactionError(
                                "移動先コマの保存先を確定できません"
                            )
                        if destination_dir.exists():
                            raise FileExistsError(
                                f"destination already exists: {destination_dir}"
                            )
                        # 復旧マーカーも複製と同じrenameで公開する。processが
                        # 直後に終了しても次回起動時にDomain世代から撤回/確定できる。
                        coma_move_recovery.publish_native_copy(
                            source_dir,
                            staged_dir,
                            destination_dir,
                            source_page_uid=source_page_uid,
                            source_coma_uid=source_coma_uid,
                            target_page_uid=target_page_uid,
                            target_coma_uid=destination_coma_uid,
                        )
                        directory_published = True
                    _call_fault(fault_hook, "after_directory_move")
                    return True

                def repository_phase(state, index: int) -> None:
                    _call_fault(
                        fault_hook,
                        f"repository:{state.value}:{index}",
                    )

                repository.checkpoint(
                    project_document,
                    (source_document, target_document),
                    native_checkpoint=install_native_directory,
                    phase_hook=repository_phase,
                )
                store.mark_checkpointed(
                    project=True,
                    page_uids=(source_page_uid, target_page_uid),
                )

            if directory_published:
                coma_move_recovery.recover_interrupted_coma_moves(
                    work_dir,
                    repository=repository,
                )
            domain_projection.bind_project_document(work, project_document)
            domain_projection.bind_page_document(source_page, source_document)
            domain_projection.bind_page_document(target_page, target_document)
            target_page.coma_count = sum(
                node.kind == "coma" for node in target_document.nodes.values()
            )
            if not target_was_loaded:
                from ..utils import page_detail

                target_count = int(target_page.coma_count)
                page_detail.clear_page_detail(target_page)
                target_page.coma_count = target_count
            try:
                record_successful_write(repository.project_path)
                record_successful_write(repository.page_path(source_page_uid))
                record_successful_write(repository.page_path(target_page_uid))
                if directory_published and destination_dir is not None:
                    record_successful_tree_change(source_dir, destination_dir)
            except Exception:  # noqa: BLE001
                _logger.exception("coma move baseline refresh failed")
            return destination_id
        except BaseException as original:
            rollback_errors: list[BaseException] = []
            if destination_dir is not None:
                try:
                    _rollback_published_directory(
                        source_dir,
                        destination_dir,
                        directory_published,
                    )
                except BaseException as exc:  # noqa: BLE001
                    rollback_errors.append(exc)
            try:
                _rollback_runtime(
                    context,
                    work,
                    snapshot,
                    old_parent_key=old_parent_key,
                    new_parent_key=new_parent_key,
                    dx_mm=dx_mm,
                    dy_mm=dy_mm,
                    translation_started=translation_started,
                    gp_reparent_started=gp_reparent_started,
                    effect_reparent_started=effect_reparent_started,
                )
            except BaseException as exc:  # noqa: BLE001
                rollback_errors.append(exc)
            try:
                restore_baseline_registry(baseline)
            except BaseException as exc:  # noqa: BLE001
                rollback_errors.append(exc)
            _raise_rollback_failure(original, rollback_errors)
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)


__all__ = (
    "ComaMoveTransactionError",
    "move_coma_to_page",
)
