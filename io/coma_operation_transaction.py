"""同一ページのコマNative変更とDomain checkpointを一括確定する。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
import uuid

import bpy

from ..bmanga_core.domain_ids import UIDKind, derived_uid
from ..bmanga_core.domain_model import document_hash
from ..bmanga_core.domain_store import (
    ApplyPagePatch,
    ApplyProjectPatch,
    page_patch,
    project_patch,
)
from ..utils import paths
from . import (
    domain_projection,
    domain_projection_ids,
    domain_runtime,
    native_tree_transaction,
    page_operation_transaction,
)
from .save_baseline import (
    record_successful_tree_change,
    record_successful_write,
    restore_baseline_registry,
    snapshot_baseline_registry,
)
from .project_file_lock import guard_path_write, work_lock


class ComaOperationTransactionError(RuntimeError):
    """コマ操作を一括確定または完全復旧できない。"""


@dataclass(slots=True)
class _LayerObjectBackup:
    obj: object
    kind: str
    stable_id: str
    object_name: str
    data_name: str
    collection_names: tuple[str, ...]
    parent_key: str
    restored: bool = False


def _page_layer_objects(
    page_id: str,
    *,
    parent_keys: set[str] | None = None,
) -> list[object]:
    from ..utils import layer_object_model

    prefix = f"{page_id}:"
    result = []
    for obj in layer_object_model.iter_layer_objects():
        parent_key = layer_object_model.parent_key(obj)
        if parent_keys is not None:
            if parent_key in parent_keys:
                result.append(obj)
            continue
        if parent_key == page_id or parent_key.startswith(prefix):
            result.append(obj)
    return result


def _capture_page_layer_objects(
    page_id: str,
    parent_keys: set[str],
) -> list[_LayerObjectBackup]:
    from ..utils import layer_object_model, object_naming

    backups: list[_LayerObjectBackup] = []
    for source in _page_layer_objects(page_id, parent_keys=parent_keys):
        clone = source.copy()
        source_data = getattr(source, "data", None)
        clone.data = source_data.copy() if source_data is not None else None
        token = uuid.uuid4().hex
        clone.name = f"__BManga_ComaRollback_{token}"
        if clone.data is not None:
            clone.data.name = f"__BManga_ComaRollbackData_{token}"
        clone[object_naming.PROP_MANAGED] = False
        backups.append(
            _LayerObjectBackup(
                clone,
                layer_object_model.layer_kind(source),
                layer_object_model.stable_id(source),
                str(getattr(source, "name", "") or ""),
                str(getattr(source_data, "name", "") or ""),
                tuple(
                    str(collection.name)
                    for collection in getattr(source, "users_collection", ())
                ),
                str(source.get(object_naming.PROP_PARENT_KEY, "") or ""),
            )
        )
    return backups


def _removed_coma_parent_keys(page, remove_ids: tuple[str, ...]) -> set[str]:
    targets = {str(value or "") for value in remove_ids if str(value or "")}
    keys = set(targets)
    page_id = str(getattr(page, "id", "") or "")
    for coma in getattr(page, "comas", ()):
        coma_id = str(getattr(coma, "coma_id", "") or "")
        entry_id = str(getattr(coma, "id", "") or "")
        if coma_id not in targets and entry_id not in targets:
            continue
        keys.update((coma_id, entry_id))
    keys.discard("")
    if page_id:
        keys.update(f"{page_id}:{key}" for key in tuple(keys))
    return keys


def _remove_unlinked_backup(backup: _LayerObjectBackup) -> None:
    if backup.restored:
        return
    obj = backup.obj
    data = getattr(obj, "data", None)
    if obj.name in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    if data is None or getattr(data, "users", 1) != 0:
        return
    blocks = getattr(bpy.data, "grease_pencils_v3", None)
    if blocks is None:
        blocks = getattr(bpy.data, "grease_pencils", None)
    if blocks is not None and data.name in blocks:
        blocks.remove(data)


def _restore_page_layer_objects(
    context,
    backups: list[_LayerObjectBackup],
) -> None:
    from ..operators import effect_line_op
    from ..utils import layer_object_model, layer_stack, object_naming

    for backup in backups:
        current = next(
            (
                obj
                for obj in layer_object_model.iter_layer_objects(backup.kind)
                if layer_object_model.stable_id(obj) == backup.stable_id
            ),
            None,
        )
        if current is not None and not layer_object_model.remove_layer_object(current):
            raise ComaOperationTransactionError(
                "コマ操作前の手描き／効果線を復元できません"
            )
    restored: list[object] = []
    for backup in backups:
        obj = backup.obj
        obj.name = backup.object_name
        if getattr(obj, "data", None) is not None and backup.data_name:
            obj.data.name = backup.data_name
        obj[object_naming.PROP_MANAGED] = True
        obj[object_naming.PROP_PARENT_KEY] = backup.parent_key
        linked = False
        for collection_name in backup.collection_names:
            collection = bpy.data.collections.get(collection_name)
            if collection is None:
                raise ComaOperationTransactionError(
                    f"復元先Collectionがありません: {collection_name}"
                )
            collection.objects.link(obj)
            linked = True
        if not linked:
            raise ComaOperationTransactionError(
                f"復元先Collectionを特定できません: {backup.object_name}"
            )
        backup.restored = True
        restored.append(obj)
    for obj in restored:
        if layer_object_model.layer_kind(obj) != "effect":
            continue
        layer = layer_object_model.content_layer(obj)
        bounds = effect_line_op.effect_layer_bounds(obj, layer)
        if layer is not None and bounds is not None:
            effect_line_op._write_effect_strokes(
                context,
                obj,
                layer,
                bounds,
                propagate_link=False,
            )
    layer_stack.sync_layer_stack_after_data_change(context)


class ComaOperationTransaction:
    """Native計画を先に適用し、現在のUI投影を一つのcheckpointへ保存する。"""

    def __init__(
        self,
        context,
        work,
        page,
        *,
        copy_pairs: tuple[tuple[str, str], ...] = (),
        remove_ids: tuple[str, ...] = (),
    ) -> None:
        self.context = context
        self.work = work
        self.page = page
        self.page_id = str(getattr(page, "id", "") or "")
        self.work_dir = Path(str(work.work_dir)).resolve(strict=True)
        self.closed = False
        self.temp_root: Path | None = None
        self.layer_backups: list[_LayerObjectBackup] = []
        self._lock_context = work_lock(self.work_dir, blocking=True)
        self._lock_context.__enter__()
        try:
            self._setup(copy_pairs=copy_pairs, remove_ids=remove_ids)
        except BaseException:
            self._cleanup()
            raise

    def _setup(
        self,
        *,
        copy_pairs: tuple[tuple[str, str], ...],
        remove_ids: tuple[str, ...],
    ) -> None:
        self.snapshot = page_operation_transaction._capture_memory(
            self.context,
            self.work,
        )
        self.baseline = snapshot_baseline_registry()
        self.repository = domain_runtime.repository_for(self.work_dir)
        self.repository.recover()
        native_tree_transaction.recover_pending_native_transactions(
            self.work_dir,
            repository=self.repository,
        )
        with guard_path_write(self.repository.project_path):
            pass
        project = self.repository.load_project()
        self.page_uid = domain_projection.ensure_page_uid(
            self.page,
            project.project_uid,
        )
        persisted_page = self.repository.load_page(self.page_uid)
        domain_runtime.store_for(
            self.work_dir,
            initial_project=project,
        )
        domain_runtime.hydrate_page(self.work_dir, persisted_page)
        self.initial_page_hash = document_hash(persisted_page)
        affected_parent_keys = _removed_coma_parent_keys(
            self.page,
            remove_ids,
        )
        if affected_parent_keys:
            self.layer_backups = _capture_page_layer_objects(
                self.page_id,
                affected_parent_keys,
            )
        native_by_display = {
            node.display_id: node.native_uid
            for node in persisted_page.nodes.values()
            if node.kind == "coma" and node.native_uid
        }
        self.repository.journal_dir.mkdir(parents=True, exist_ok=True)
        self.temp_root = Path(
            tempfile.mkdtemp(
                prefix=".coma-operation-",
                dir=str(self.repository.journal_dir),
            )
        )
        additions = []
        removals = []
        self.changed_paths: list[Path] = []
        for source_id, target_id in copy_pairs:
            source_uid = native_by_display.get(source_id)
            if not source_uid:
                continue
            target_uid = derived_uid(UIDKind.COMA, self.page_uid, target_id)
            source = (
                self.repository.page_dir(self.page_uid)
                / paths.COMAS_DIR_NAME
                / source_uid
            )
            if not source.is_dir():
                continue
            staged = self.temp_root / target_uid
            shutil.copytree(source, staged, symlinks=True)
            destination = (
                self.repository.page_dir(self.page_uid)
                / paths.COMAS_DIR_NAME
                / target_uid
            )
            additions.append(
                native_tree_transaction.Addition(
                    staged,
                    destination,
                    native_tree_transaction.Owner(
                        "coma",
                        self.page_uid,
                        target_uid,
                    ),
                )
            )
            self.changed_paths.append(destination)
        for source_id in remove_ids:
            source_uid = native_by_display.get(source_id)
            if not source_uid:
                continue
            source = (
                self.repository.page_dir(self.page_uid)
                / paths.COMAS_DIR_NAME
                / source_uid
            )
            if not source.is_dir():
                continue
            removals.append(
                native_tree_transaction.Removal(
                    source,
                    native_tree_transaction.Owner(
                        "coma",
                        self.page_uid,
                        source_uid,
                    ),
                )
            )
            self.changed_paths.append(source)
        self.native = (
            native_tree_transaction.NativeTreeTransaction(
                self.work_dir,
                repository=self.repository,
                additions=additions,
                removals=removals,
            )
            if additions or removals
            else None
        )
        if self.native is not None:
            self.native.prepare()
        self.applied = False

    def apply_native(self) -> None:
        if self.native is not None:
            self.native.apply()
        self.applied = True

    def commit(self) -> None:
        if not self.applied:
            self.apply_native()
        self.page.coma_count = len(self.page.comas)
        projected_project = domain_projection.project_document_from_work(
            self.work
        )
        projected_page = domain_projection.page_document_from_projection(
            self.work,
            self.page,
            context=self.context,
        )
        expected_page_hash = document_hash(projected_page)
        store = domain_runtime.store_for(
            self.work_dir,
            initial_project=projected_project,
        )
        try:
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
                            store.pages.get(projected_page.page_uid),
                            projected_page,
                            require_candidate_revision=False,
                        )
                    )
                )
                project = store.project
                page = store.pages[self.page_uid]
                self.repository.checkpoint(project, [page])
                if self.native is not None:
                    self.native.recover()
                store.mark_checkpointed(
                    project=True,
                    page_uids=(self.page_uid,),
                )
        except BaseException:
            self._recover_after_failure(expected_page_hash=expected_page_hash)
            raise
        try:
            domain_projection.bind_project_document(self.work, project)
            domain_projection.bind_page_document(self.page, page)
            record_successful_write(self.repository.project_path)
            record_successful_write(
                self.repository.page_path(self.page_uid)
            )
            if self.changed_paths:
                record_successful_tree_change(*self.changed_paths)
        except BaseException:
            self.work.loaded = False
            raise
        finally:
            self._cleanup()

    def _recover_after_failure(
        self,
        *,
        expected_page_hash: str | None = None,
    ) -> None:
        domain_committed = False
        recovery_complete = False
        try:
            self.repository.recover()
            if self.native is not None:
                domain_committed = self.native.recover()
            elif expected_page_hash is not None:
                recovered_hash = document_hash(
                    self.repository.load_page(self.page_uid)
                )
                if recovered_hash == expected_page_hash:
                    domain_committed = True
                elif recovered_hash != self.initial_page_hash:
                    raise ComaOperationTransactionError(
                        "コマ操作のDomain状態を確定できません"
                    )
            recovery_complete = True
        finally:
            try:
                if recovery_complete and not domain_committed:
                    page_operation_transaction._restore_memory(
                        self.context,
                        self.work,
                        self.snapshot,
                    )
                    _restore_page_layer_objects(
                        self.context,
                        self.layer_backups,
                    )
                    restore_baseline_registry(self.baseline)
                elif not recovery_complete:
                    self.work.loaded = False
            except BaseException:
                self.work.loaded = False
                raise
            finally:
                self._cleanup()

    def abort(self) -> None:
        if self.closed:
            return
        self._recover_after_failure()

    def _cleanup(self) -> None:
        if self.closed:
            return
        for backup in self.layer_backups:
            _remove_unlinked_backup(backup)
        if self.temp_root is not None:
            shutil.rmtree(self.temp_root, ignore_errors=True)
        self.closed = True
        self._lock_context.__exit__(None, None, None)


def coma_uid_for_entry(work, page, entry) -> str:
    project_uid = domain_projection.ensure_project_uid(work)
    page_uid = domain_projection.ensure_page_uid(page, project_uid)
    return domain_projection_ids.ensure_coma_uid(entry, page_uid)


__all__ = (
    "ComaOperationTransaction",
    "ComaOperationTransactionError",
    "coma_uid_for_entry",
)
