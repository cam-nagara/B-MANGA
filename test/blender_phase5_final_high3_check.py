"""Blender 5.2実機: Phase 5最終High 2/3の復旧・履歴原子性。"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
PKG = "bmanga_phase5_final_high3"
PROJECT_UID = "project_33333333333333333333333333333333"
SOURCE_UID = "page_11111111111111111111111111111111"
TARGET_UID = "page_22222222222222222222222222222222"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PKG,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "bmanga.project",
        "schemaVersion": 1,
        "projectUid": PROJECT_UID,
        "revision": 1,
        "settings": {},
        "pageOrder": [SOURCE_UID, TARGET_UID],
        "pages": {
            SOURCE_UID: {
                "uid": SOURCE_UID,
                "displayId": "p0001",
                "displayNumber": 1,
                "title": "",
                "spread": False,
                "sourcePageUids": [],
                "settings": {},
            },
            TARGET_UID: {
                "uid": TARGET_UID,
                "displayId": "p0002",
                "displayNumber": 2,
                "title": "",
                "spread": False,
                "sourcePageUids": [],
                "settings": {},
            },
        },
    }
    (root / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    (root / "pages" / SOURCE_UID).mkdir(parents=True)
    (root / "pages" / TARGET_UID).mkdir(parents=True)


def _test_uncommitted_never_instantiates() -> None:
    from bmanga_phase5_final_high3.utils import asset_bundle, cross_page_stage

    calls = []
    original = asset_bundle.instantiate_payload
    asset_bundle.instantiate_payload = (
        lambda *_args, **_kwargs: calls.append("created") or {"created_new_count": 1}
    )
    try:
        for entry in (
            {
                "stage_id": "prepared_never_create",
                "state": "prepared",
                "payload": {"entries": [{"kind": "text"}]},
                "drop_local_xy_mm": [0.0, 0.0],
            },
            {
                "stage_id": "missing_state_never_create",
                "payload": {"entries": [{"kind": "text"}]},
                "drop_local_xy_mm": [0.0, 0.0],
            },
            {
                "stage_id": "unknown_state_never_create",
                "state": "unknown",
                "payload": {"entries": [{"kind": "text"}]},
                "drop_local_xy_mm": [0.0, 0.0],
            },
        ):
            created, processed = cross_page_stage._process_assets(
                bpy.context,
                SimpleNamespace(),
                [entry],
            )
            assert created == 0 and processed == set()
    finally:
        asset_bundle.instantiate_payload = original
    assert calls == []


def _test_reopened_transfer_waits_for_save() -> None:
    from bmanga_phase5_final_high3.utils import asset_bundle, cross_page_stage

    entry = {
        "stage_id": "reopened_transfer",
        "target_page_id": "p0002",
        "state": "ready",
        "drop_local_xy_mm": [0.0, 0.0],
        "payload": {
            "entries": [{"kind": "text", "data": {"id": "reopened"}}],
            "transfer": {
                "sourcePageId": "p0001",
                "targetPageId": "p0002",
            },
        },
    }
    original_complete = cross_page_stage.asset_stage_complete
    original_tokens = cross_page_stage._asset_token_matches
    original_runtime = cross_page_stage._runtime_keys
    original_instantiate = asset_bundle.instantiate_payload
    cross_page_stage.asset_stage_complete = lambda *_args, **_kwargs: True
    cross_page_stage._asset_token_matches = lambda *_args, **_kwargs: True
    cross_page_stage._runtime_keys = lambda *_args, **_kwargs: set()
    asset_bundle.instantiate_payload = (
        lambda *_args, **_kwargs: {"created_new_count": 0}
    )
    try:
        created, processed = cross_page_stage._process_assets(
            bpy.context,
            SimpleNamespace(),
            [entry],
        )
    finally:
        cross_page_stage.asset_stage_complete = original_complete
        cross_page_stage._asset_token_matches = original_tokens
        cross_page_stage._runtime_keys = original_runtime
        asset_bundle.instantiate_payload = original_instantiate
    assert created == 0 and processed == set()


def _test_orphan_prepared_without_journal() -> None:
    from bmanga_phase5_final_high3.utils import (
        cross_page_stage,
        handlers,
        lifecycle_scheduler,
        layer_transfer_group,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_orphan_prepared_") as tmp:
        root = Path(tmp)
        _write_project(root)
        stage_path = cross_page_stage.staged_path(root, "p0002")
        stage_path.write_text(
            json.dumps({
                "asset_bundles": [{
                    "stage_id": "crash_before_journal",
                    "state": "prepared",
                    "payload": {"entries": [{"kind": "text"}]},
                    "drop_local_xy_mm": [0.0, 0.0],
                }]
            }),
            encoding="utf-8",
        )
        assert not any(root.glob("pages/*/_transfer_recovery"))
        assert not layer_transfer_group.has_transfer_recovery_journal(root)
        scheduled = []
        original_schedule = lifecycle_scheduler.schedule
        lifecycle_scheduler.schedule = (
            lambda name, callback, **kwargs: scheduled.append(
                (name, callback, kwargs)
            )
            or 0
        )
        try:
            handlers._schedule_transfer_orphan_recovery(root)
        finally:
            lifecycle_scheduler.schedule = original_schedule
        assert layer_transfer_group._recovery_stage_state(
            root,
            "p0002",
            "crash_before_journal",
        ) == "prepared"
        assert len(scheduled) == 1
        name, callback, kwargs = scheduled[0]
        assert name == handlers._TRANSFER_ORPHAN_RECOVERY_TASK
        assert kwargs["first_interval"] > 0.0
        assert callback() is None
        assert layer_transfer_group._recovery_stage_state(
            root,
            "p0002",
            "crash_before_journal",
        ) == ""
        recovery_root = (
            root
            / "pages"
            / SOURCE_UID
            / layer_transfer_group._RECOVERY_DIR_NAME
        )
        recovery_root.mkdir()
        assert layer_transfer_group.has_transfer_recovery_journal(root)
        # 再起動後のtarget open相当でもpreparedは生成されず、再試行可能。
        created, processed = cross_page_stage._process_assets(
            bpy.context,
            SimpleNamespace(),
            [{
                "stage_id": "crash_before_journal",
                "state": "prepared",
                "payload": {"entries": [{"kind": "text"}]},
                "drop_local_xy_mm": [0.0, 0.0],
            }],
        )
        assert created == 0 and processed == set()


def _ready_transfer_journal(
    root: Path,
    stage_id: str,
    *,
    target_folder_key: str = "",
):
    from bmanga_phase5_final_high3.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
        layer_transfer_recovery_manifest,
        paths,
    )

    _write_project(root)
    source_blend = paths.page_blend_path(root, "p0001")
    source_meta = paths.page_meta_path(root, "p0001")
    source_blend.write_bytes(b"source-blend")
    source_meta.write_text('{"schema":"bmanga.page"}', encoding="utf-8")
    recovery_dir = (
        paths.page_dir(root, "p0001")
        / layer_transfer_group._RECOVERY_DIR_NAME
        / stage_id
    )
    recovery_dir.mkdir(parents=True)
    backup = layer_transfer_group._backup_source_files(
        root,
        "p0001",
        recovery_dir / "rollback",
    )
    prepared = {
        "stage_id": stage_id,
        "target_page_id": "p0002",
        "drop_local_xy_mm": [0.0, 0.0],
        "payload": {
            "entries": [{"kind": "text", "data": {"id": "text_stage"}}],
            "transfer": {
                "sourcePageId": "p0001",
                "targetPageId": "p0002",
                "targetFolderKey": target_folder_key,
                "targetFolderOwnerPageId": (
                    "p0002" if target_folder_key else ""
                ),
            },
        },
        "state": "prepared",
    }
    target_stage = cross_page_stage.asset_entry_identity(prepared)
    assert target_stage is not None
    manifest = layer_transfer_recovery_manifest.build(
        root,
        recovery_dir,
        "p0001",
        "p0002",
        stage_id,
        [],
        backup,
        target_stage,
        phase="prepared",
    )
    json_io.write_json(
        recovery_dir / layer_transfer_group._RECOVERY_MANIFEST_NAME,
        manifest,
    )
    ready = copy.deepcopy(prepared)
    ready["state"] = "ready"
    stage_path = cross_page_stage.staged_path(root, "p0002")
    json_io.write_json(stage_path, {"asset_bundles": [ready]})
    return recovery_dir, stage_path, ready, backup


def _test_ready_journal_waits_for_target_save() -> None:
    from bmanga_phase5_final_high3.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
        layer_transfer_recovery_manifest,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_ready_wait_") as tmp:
        root = Path(tmp)
        recovery_dir, stage_path, _entry, _backup = _ready_transfer_journal(
            root,
            "ready_waits_for_target",
        )
        assert layer_transfer_group.recover_interrupted_transfers(root) == ()
        assert recovery_dir.is_dir()
        assert stage_path.is_file()
        try:
            layer_transfer_group.finalize_target_transfer_stage(
                root,
                "p0002",
                "ready_waits_for_target",
            )
        except layer_transfer_group.LayerTransferRecoveryError:
            pass
        else:
            raise AssertionError("unsaved target stage was finalized")
        assert layer_transfer_group.mark_target_transfer_stage_saved(
            root,
            "p0002",
            "ready_waits_for_target",
            target_saved=True,
        )
        manifest_path = (
            recovery_dir / layer_transfer_group._RECOVERY_MANIFEST_NAME
        )
        manifest = json_io.read_json(manifest_path)
        assert (
            manifest["phase"]
            == layer_transfer_recovery_manifest.TARGET_SAVED_PHASE
        )
        try:
            layer_transfer_group.finalize_target_transfer_stage(
                root,
                "p0002",
                "ready_waits_for_target",
                target_saved=True,
            )
        except layer_transfer_group.LayerTransferRecoveryError:
            pass
        else:
            raise AssertionError("journal closed before durable stage cleanup")
        assert recovery_dir.is_dir()
        assert stage_path.is_file()
        # target_saved後・stage削除前の終了は、起動時にstage→journalの順で収束。
        assert layer_transfer_group.recover_interrupted_transfers(root) == ()
        assert not recovery_dir.exists()
        assert not stage_path.exists()

    with tempfile.TemporaryDirectory(prefix="bmanga_ready_cleanup_fault_") as tmp:
        root = Path(tmp)
        recovery_dir, stage_path, _entry, _backup = _ready_transfer_journal(
            root,
            "ready_cleanup_fault",
        )
        assert layer_transfer_group.mark_target_transfer_stage_saved(
            root,
            "p0002",
            "ready_cleanup_fault",
            target_saved=True,
        )
        original_cleanup = cross_page_stage.discard_asset_bundle_stage_strict
        original_log = layer_transfer_group._logger.exception
        cross_page_stage.discard_asset_bundle_stage_strict = (
            lambda *_args, **_kwargs:
            (_ for _ in ()).throw(
                cross_page_stage.StagedImportCleanupError("injected cleanup")
            )
        )
        layer_transfer_group._logger.exception = lambda *_args, **_kwargs: None
        try:
            try:
                layer_transfer_group.recover_interrupted_transfers(root)
            except layer_transfer_group.LayerTransferRecoveryError:
                pass
            else:
                raise AssertionError("failed stage cleanup discarded journal")
        finally:
            cross_page_stage.discard_asset_bundle_stage_strict = original_cleanup
            layer_transfer_group._logger.exception = original_log
        assert recovery_dir.is_dir()
        assert stage_path.is_file()
        assert layer_transfer_group.recover_interrupted_transfers(root) == ()
        assert not recovery_dir.exists()
        assert not stage_path.exists()

    with tempfile.TemporaryDirectory(prefix="bmanga_ready_stage_gone_") as tmp:
        root = Path(tmp)
        recovery_dir, stage_path, _entry, backup = _ready_transfer_journal(
            root,
            "ready_stage_gone",
        )
        assert layer_transfer_group.mark_target_transfer_stage_saved(
            root,
            "p0002",
            "ready_stage_gone",
            target_saved=True,
        )
        next(path for path in backup.values() if path is not None).unlink()
        assert cross_page_stage.discard_asset_bundle_stage_strict(
            root,
            "p0002",
            "ready_stage_gone",
        )
        assert not stage_path.exists()
        assert recovery_dir.is_dir()
        assert layer_transfer_group.recover_interrupted_transfers(root) == ()
        assert not recovery_dir.exists()

    with tempfile.TemporaryDirectory(prefix="bmanga_stage_unlink_fault_") as tmp:
        blocked = Path(tmp) / "stage-as-directory"
        blocked.mkdir()
        original_log = cross_page_stage._logger.exception
        cross_page_stage._logger.exception = lambda *_args, **_kwargs: None
        try:
            try:
                cross_page_stage._write_or_remove(blocked, {}, strict=True)
            except cross_page_stage.StagedImportCleanupError:
                pass
            else:
                raise AssertionError("strict stage cleanup swallowed unlink failure")
        finally:
            cross_page_stage._logger.exception = original_log


def _test_target_owner_failure_is_fail_closed() -> None:
    from bmanga_phase5_final_high3.utils import (
        asset_bundle,
        cross_page_stage,
        cross_page_stage_command,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_target_owner_") as tmp:
        root = Path(tmp)
        recovery_dir, stage_path, ready, backup = _ready_transfer_journal(
            root,
            "target_owner_changed",
            target_folder_key="missing_target_folder",
        )
        from bmanga_phase5_final_high3.utils import layer_transfer_group

        original_recovery_log = layer_transfer_group._logger.exception
        layer_transfer_group._logger.exception = lambda *_args, **_kwargs: None
        try:
            try:
                layer_transfer_group.recover_interrupted_transfers(root)
            except layer_transfer_group.LayerTransferRecoveryError:
                pass
            else:
                raise AssertionError(
                    "missing target folder passed startup preflight"
                )
        finally:
            layer_transfer_group._logger.exception = original_recovery_log
        work = SimpleNamespace(loaded=True, work_dir=str(root))
        page = SimpleNamespace(id="p0002", texts=[])
        restored = []
        original_resolve = cross_page_stage._resolve_page_context
        original_instantiate = asset_bundle.instantiate_payload
        original_capture = cross_page_stage_command.capture
        original_restore = cross_page_stage_command.restore
        original_log_exception = cross_page_stage._logger.exception
        cross_page_stage._resolve_page_context = (
            lambda *_args, **_kwargs: (work, page, "p0002")
        )
        asset_bundle.instantiate_payload = (
            lambda _context, payload, **_kwargs:
            asset_bundle._validated_transfer_target_folder(
                SimpleNamespace(layer_folders=[]),
                page,
                payload,
            )
        )
        cross_page_stage_command.capture = lambda *_args: "snapshot"
        cross_page_stage_command.restore = (
            lambda _context, snapshot: restored.append(snapshot)
        )
        cross_page_stage._logger.exception = lambda *_args, **_kwargs: None
        try:
            try:
                cross_page_stage.process_staged_imports(bpy.context)
            except cross_page_stage.StagedImportIntegrityError:
                pass
            else:
                raise AssertionError("target ownership mismatch was accepted")
        finally:
            cross_page_stage._resolve_page_context = original_resolve
            asset_bundle.instantiate_payload = original_instantiate
            cross_page_stage_command.capture = original_capture
            cross_page_stage_command.restore = original_restore
            cross_page_stage._logger.exception = original_log_exception
        assert work.loaded is False
        assert restored == ["snapshot"]
        assert recovery_dir.is_dir()
        assert stage_path.is_file()
        assert json.loads(stage_path.read_text(encoding="utf-8"))[
            "asset_bundles"
        ] == [ready]
        assert all(saved is None or saved.is_file() for saved in backup.values())


def _backup(history, root: Path, name: str, content: bytes) -> Path:
    path = root / "backups" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _record(history, root: Path, token: str):
    destination = root / f"{token}.json"
    destination.write_bytes(f"{token}-post".encode())
    pre = _backup(history, root, f"{token}-pre", f"{token}-pre".encode())
    post = _backup(history, root, f"{token}-post", f"{token}-post".encode())
    pre_files = {destination: pre}
    post_files = {destination: post}
    return history.TransferHistoryRecord(
        token=token,
        work_dir=root,
        source_page_id="p0001",
        target_page_id="p0002",
        stage_id=token,
        recovery_dir=root / token,
        manifest={"target_stage": {}},
        pre_files=pre_files,
        post_files=post_files,
        pre_fingerprints=history._backup_generation(pre_files),
        post_fingerprints=history._current_generation(post_files),
        pre_backup_fingerprints=history._backup_generation(pre_files),
        post_backup_fingerprints=history._backup_generation(post_files),
        stage_entry={},
    )


def _expect_failure(call) -> None:
    try:
        call()
    except RuntimeError:
        return
    raise AssertionError("expected history failure was accepted")


def _reset_history_runtime(runtime) -> None:
    runtime._restoring = False
    runtime._blocked_error = ""


def _test_group_preflight_zero_writes() -> None:
    from bmanga_phase5_final_high3.utils import (
        history_runtime,
        layer_transfer_history as history,
    )

    for mode in ("later_external", "later_backup"):
        with tempfile.TemporaryDirectory(prefix=f"bmanga_history_{mode}_") as tmp:
            root = Path(tmp)
            first = _record(history, root, f"{mode}_first")
            later = _record(history, root, f"{mode}_later")
            if mode == "later_external":
                next(iter(later.post_files)).write_bytes(b"external")
            else:
                next(iter(later.pre_files.values())).write_bytes(b"damaged")
            writes = []
            original_stage = history._preflight_stage
            original_apply = history._apply_files
            history._preflight_stage = lambda *_args, **_kwargs: None
            history._apply_files = lambda step, **_kwargs: writes.append(step.token)
            work = SimpleNamespace(loaded=True)
            context = SimpleNamespace(scene=SimpleNamespace(bmanga_work=work))
            try:
                _expect_failure(
                    lambda: history._apply_group(
                        (
                            history._HistoryStep(first.token, first, True),
                            history._HistoryStep(later.token, later, True),
                        ),
                        context=context,
                    )
                )
            finally:
                history._preflight_stage = original_stage
                history._apply_files = original_apply
                _reset_history_runtime(history_runtime)
            assert writes == []
            assert work.loaded is False


def _test_group_compensation() -> None:
    from bmanga_phase5_final_high3.utils import (
        history_runtime,
        layer_transfer_history as history,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_history_compensate_") as tmp:
        root = Path(tmp)
        first = _record(history, root, "comp_first")
        later = _record(history, root, "comp_later")
        records = {first.token: first, later.token: later}
        original_stage = history._preflight_stage
        original_undo = history._undo_files
        original_redo = history._redo_files
        original_post = history._assert_postcondition
        original_reload = history._reload_domain
        history._preflight_stage = lambda *_args, **_kwargs: None
        calls = []

        def write_generation(record, *, undo: bool):
            destination = next(iter(record.pre_files))
            source = next(iter(
                record.pre_files.values() if undo else record.post_files.values()
            ))
            destination.write_bytes(source.read_bytes())
            calls.append(("undo" if undo else "redo", record.token))
            if undo and record.token == later.token:
                raise RuntimeError("injected second-token apply failure")

        history._undo_files = lambda record: write_generation(record, undo=True)
        history._redo_files = lambda record: write_generation(record, undo=False)
        history._assert_postcondition = lambda *_args, **_kwargs: None
        history._reload_domain = lambda *_args, **_kwargs: None
        work = SimpleNamespace(loaded=True)
        context = SimpleNamespace(scene=SimpleNamespace(bmanga_work=work))
        try:
            _expect_failure(
                lambda: history._apply_group(
                    tuple(
                        history._HistoryStep(token, records[token], True)
                        for token in (first.token, later.token)
                    ),
                    context=context,
                )
            )
        finally:
            history._preflight_stage = original_stage
            history._undo_files = original_undo
            history._redo_files = original_redo
            history._assert_postcondition = original_post
            history._reload_domain = original_reload
            _reset_history_runtime(history_runtime)
        for record in records.values():
            destination = next(iter(record.post_files))
            source = next(iter(record.post_files.values()))
            assert destination.read_bytes() == source.read_bytes()
        assert calls == [
            ("undo", first.token),
            ("undo", later.token),
            ("redo", later.token),
            ("redo", first.token),
        ]
        assert work.loaded is True

        # 先行tokenのgroup compensation自体が失敗した場合は、以後の保存を
        # 許さないfail-closedへ移る。
        history._preflight_stage = lambda *_args, **_kwargs: None

        def fail_group_redo(record):
            if record.token == first.token:
                raise RuntimeError("injected group compensation failure")
            write_generation(record, undo=False)

        history._undo_files = lambda record: write_generation(record, undo=True)
        history._redo_files = fail_group_redo
        history._assert_postcondition = lambda *_args, **_kwargs: None
        history._reload_domain = lambda *_args, **_kwargs: None
        work.loaded = True
        try:
            _expect_failure(
                lambda: history._apply_group(
                    tuple(
                        history._HistoryStep(token, records[token], True)
                        for token in (first.token, later.token)
                    ),
                    context=context,
                )
            )
        finally:
            history._preflight_stage = original_stage
            history._undo_files = original_undo
            history._redo_files = original_redo
            history._assert_postcondition = original_post
            history._reload_domain = original_reload
            _reset_history_runtime(history_runtime)
        assert work.loaded is False


def main() -> None:
    _load_addon()
    _test_uncommitted_never_instantiates()
    _test_reopened_transfer_waits_for_save()
    _test_orphan_prepared_without_journal()
    _test_ready_journal_waits_for_target_save()
    _test_target_owner_failure_is_fail_closed()
    _test_group_preflight_zero_writes()
    _test_group_compensation()
    print("BMANGA_PHASE5_FINAL_HIGH3_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
