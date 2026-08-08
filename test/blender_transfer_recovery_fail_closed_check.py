"""Blender 5.2実機: ページ間移送の二重故障をfail-closedで保持する。"""

from __future__ import annotations

import importlib.util
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PAGE = "page_11111111111111111111111111111111"
TARGET_PAGE = "page_22222222222222222222222222222222"
SOURCE_ID = "p0001"
TARGET_ID = "p0002"
PROJECT_UID = "project_33333333333333333333333333333333"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_transfer_recovery_test",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_transfer_recovery_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _source_files(paths, root: Path) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.json").write_text(
        json.dumps(
            {
                "schema": "bmanga.project",
                "schemaVersion": 1,
                "projectUid": PROJECT_UID,
                "revision": 1,
                "settings": {},
                "pageOrder": [SOURCE_PAGE, TARGET_PAGE],
                "pages": {
                    SOURCE_PAGE: {
                        "uid": SOURCE_PAGE,
                        "displayId": SOURCE_ID,
                        "displayNumber": 1,
                        "title": "",
                        "spread": False,
                        "sourcePageUids": [],
                        "settings": {},
                    },
                    TARGET_PAGE: {
                        "uid": TARGET_PAGE,
                        "displayId": TARGET_ID,
                        "displayNumber": 2,
                        "title": "",
                        "spread": False,
                        "sourcePageUids": [],
                        "settings": {},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    page_dir = paths.page_dir(root, SOURCE_ID)
    page_dir.mkdir(parents=True, exist_ok=True)
    page_blend = paths.page_blend_path(root, SOURCE_ID)
    page_json = paths.page_meta_path(root, SOURCE_ID)
    project_json = paths.project_meta_path(root)
    page_blend.write_bytes(b"before-blend")
    for page_uid, display_id, coma_id, digit in (
        (SOURCE_PAGE, SOURCE_ID, "c01", "4"),
        (TARGET_PAGE, TARGET_ID, "c02", "5"),
    ):
        target = paths.page_meta_path(root, page_uid)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(_page_document(page_uid, display_id, coma_id, digit)),
            encoding="utf-8",
        )
    return page_blend, page_json, project_json


def _page_document(page_uid: str, display_id: str, coma_id: str, digit: str):
    root_uid = "node_" + digit * 32
    coma_node_uid = "node_" + str(int(digit) + 2) * 32
    return {
        "schema": "bmanga.page",
        "schemaVersion": 1,
        "projectUid": PROJECT_UID,
        "pageUid": page_uid,
        "revision": 1,
        "settings": {},
        "tree": {
            "rootUid": root_uid,
            "nodes": {
                root_uid: {
                    "uid": root_uid,
                    "kind": "page",
                    "displayId": display_id,
                    "title": "",
                    "settings": {},
                    "nativeUid": "",
                },
                coma_node_uid: {
                    "uid": coma_node_uid,
                    "kind": "coma",
                    "displayId": coma_id,
                    "title": "",
                    "settings": {},
                    "nativeUid": "coma_" + digit * 32,
                },
            },
            "children": {root_uid: [coma_node_uid], coma_node_uid: []},
        },
        "links": {},
    }


def _test_recovery_point_double_fault(package, root: Path) -> None:
    from bmanga_transfer_recovery_test.io import blend_io, project_file_lock
    from bmanga_transfer_recovery_test.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
        paths,
    )

    page_blend, page_json, project_json = _source_files(paths, root)
    work = SimpleNamespace(loaded=True)
    page = SimpleNamespace()
    original_save = layer_transfer_group._save_source
    original_blend_save = blend_io.save_page_blend
    original_restore = layer_transfer_group._restore_source_files

    def mutate_source(*_args, on_boundary=None):
        page_json.write_text('{"after":"page"}', encoding="utf-8")
        project_data = json.loads(project_json.read_text(encoding="utf-8"))
        project_data["revision"] = 2
        project_json.write_text(json.dumps(project_data), encoding="utf-8")
        if on_boundary is not None:
            on_boundary()

    layer_transfer_group._save_source = mutate_source
    blend_io.save_page_blend = lambda *_args, **_kwargs: True
    layer_transfer_group._restore_source_files = lambda *_args, **_kwargs: False
    json_io.write_json(
        cross_page_stage.staged_path(root, TARGET_ID),
        {
            "asset_bundles": [{
                "stage_id": "double_fault",
                "target_page_id": TARGET_ID,
                "drop_local_xy_mm": [0.0, 0.0],
                "payload": {"entries": []},
                "state": "prepared",
            }],
        },
    )
    try:
        with project_file_lock.work_lock(root, blocking=True):
            try:
                layer_transfer_group._create_recovery_backup(
                    None,
                    root,
                    work,
                    page,
                    SOURCE_ID,
                    TARGET_ID,
                    "double_fault",
                    [],
                )
                raise AssertionError("source restore failure was not propagated")
            except layer_transfer_group.LayerTransferRollbackError:
                pass
    finally:
        layer_transfer_group._save_source = original_save
        blend_io.save_page_blend = original_blend_save
        layer_transfer_group._restore_source_files = original_restore

    recovery_dir = (
        paths.page_dir(root, SOURCE_ID)
        / "_transfer_recovery"
        / "double_fault"
    )
    manifest = json.loads(
        (recovery_dir / "transaction.json").read_text(encoding="utf-8")
    )
    assert manifest["phase"] == "prepared"
    assert manifest["files"]
    assert not work.loaded
    assert page_blend.is_file()
    assert recovery_dir.is_dir()


def _write_broken_recoveries(package, root: Path) -> tuple[Path, ...]:
    from bmanga_transfer_recovery_test.utils import json_io, paths

    json_io.write_json(
        root / "project.json",
        {
            "schema": "bmanga.project",
            "schemaVersion": 1,
            "projectUid": PROJECT_UID,
            "revision": 1,
            "settings": {},
            "pageOrder": [SOURCE_PAGE, TARGET_PAGE],
            "pages": {
                SOURCE_PAGE: {
                    "uid": SOURCE_PAGE,
                    "displayId": "p0001",
                    "displayNumber": 1,
                    "title": "",
                    "spread": False,
                    "sourcePageUids": [],
                    "settings": {},
                },
                TARGET_PAGE: {
                    "uid": TARGET_PAGE,
                    "displayId": "p0002",
                    "displayNumber": 2,
                    "title": "",
                    "spread": False,
                    "sourcePageUids": [],
                    "settings": {},
                },
            },
        },
    )
    for page_uid, display_id, coma_id, digit in (
        (SOURCE_PAGE, "p0001", "c01", "4"),
        (TARGET_PAGE, "p0002", "c02", "5"),
    ):
        root_uid = "node_" + digit * 32
        coma_node_uid = "node_" + str(int(digit) + 2) * 32
        json_io.write_json(
            paths.page_meta_path(root, page_uid),
            {
                "schema": "bmanga.page",
                "schemaVersion": 1,
                "projectUid": PROJECT_UID,
                "pageUid": page_uid,
                "revision": 1,
                "settings": {},
                "tree": {
                    "rootUid": root_uid,
                    "nodes": {
                        root_uid: {
                            "uid": root_uid,
                            "kind": "page",
                            "displayId": display_id,
                            "title": "",
                            "settings": {},
                            "nativeUid": "",
                        },
                        coma_node_uid: {
                            "uid": coma_node_uid,
                            "kind": "coma",
                            "displayId": coma_id,
                            "title": "",
                            "settings": {},
                            "nativeUid": "coma_" + digit * 32,
                        },
                    },
                    "children": {root_uid: [coma_node_uid], coma_node_uid: []},
                },
                "links": {},
            },
        )
    recovery_root = paths.page_dir(root, SOURCE_PAGE) / "_transfer_recovery"
    corrupt = recovery_root / "corrupt"
    missing_manifest = recovery_root / "missing_manifest"
    missing_backup = recovery_root / "missing_backup"
    coma_failure = recovery_root / "coma_failure"
    for directory in (corrupt, missing_manifest, missing_backup, coma_failure):
        directory.mkdir(parents=True, exist_ok=True)
    (corrupt / "transaction.json").write_text("{broken", encoding="utf-8")
    json_io.write_json(
        missing_backup / "transaction.json",
        {
            "version": 2,
            "phase": "prepared",
            "stage_id": "missing_backup",
            "source_page_id": "p0001",
            "target_page_id": "p0002",
            "files": [
                {
                    "relative_path": (
                        paths.page_blend_path(root, SOURCE_PAGE)
                        .relative_to(root)
                        .as_posix()
                    ),
                    "backup_name": "crash/not-there.blend",
                    "existed": True,
                }
            ],
            "coma_moves": [],
        },
    )
    json_io.write_json(
        coma_failure / "transaction.json",
        {
            "version": 2,
            "phase": "prepared",
            "stage_id": "coma_failure",
            "source_page_id": "p0001",
            "target_page_id": "p0002",
            "files": [],
            "coma_moves": [
                {
                    "source_id": "c01",
                    "target_id": "c02",
                    "source_existed": True,
                }
            ],
        },
    )
    return corrupt, missing_manifest, missing_backup, coma_failure


def _test_startup_recovery_aggregate(package, root: Path) -> None:
    from bmanga_transfer_recovery_test.io import native_save_guard
    from bmanga_transfer_recovery_test.utils import handlers, layer_transfer_group

    recovery_dirs = _write_broken_recoveries(package, root)
    original_transfer_log = layer_transfer_group._logger.exception
    layer_transfer_group._logger.exception = lambda *_args, **_kwargs: None
    try:
        try:
            layer_transfer_group.recover_interrupted_transfers(root)
            raise AssertionError("broken recovery records were accepted")
        except layer_transfer_group.LayerTransferRecoveryError as exc:
            message = str(exc)
            assert "corrupt" in message
            assert "missing_manifest" in message
            assert "missing_backup" in message
            assert "coma_failure" in message
    finally:
        layer_transfer_group._logger.exception = original_transfer_log
    assert all(path.is_dir() for path in recovery_dirs)

    work_blend = root / "work.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(work_blend))
    package.register()
    work = bpy.context.scene.bmanga_work
    work.loaded = True
    original_find = handlers._find_work_root
    original_deactivate = handlers._deactivate_noncanonical_work_copy
    original_native_recovery = native_save_guard.recover_pending_native_saves
    original_notice = handlers._show_native_save_notice
    original_handler_log = handlers._logger.exception
    original_transfer_log = layer_transfer_group._logger.exception
    handlers._find_work_root = lambda *_args, **_kwargs: root
    handlers._deactivate_noncanonical_work_copy = lambda *_args, **_kwargs: False
    native_save_guard.recover_pending_native_saves = lambda *_args, **_kwargs: ()
    handlers._show_native_save_notice = lambda *_args, **_kwargs: None
    handlers._logger.exception = lambda *_args, **_kwargs: None
    layer_transfer_group._logger.exception = lambda *_args, **_kwargs: None
    try:
        handlers._hydrate_current_file("")
    finally:
        handlers._find_work_root = original_find
        handlers._deactivate_noncanonical_work_copy = original_deactivate
        native_save_guard.recover_pending_native_saves = original_native_recovery
        handlers._show_native_save_notice = original_notice
        handlers._logger.exception = original_handler_log
        layer_transfer_group._logger.exception = original_transfer_log
    assert not work.loaded
    assert handlers.save_scene_work_to_disk(
        bpy.context,
        reason="transfer recovery must block save",
    ) is False


def _test_move_after_physical_success(package, root: Path) -> None:
    from bmanga_transfer_recovery_test.io import coma_io
    from bmanga_transfer_recovery_test.utils import layer_transfer_group, paths

    source_page = "p0001"
    target_page = "p0002"
    original_coma_dir = paths.coma_dir
    paths.coma_dir = (
        lambda work_dir, page_id, coma_id:
        Path(work_dir) / "physical" / page_id / coma_id
    )
    source = paths.coma_dir(root, source_page, "c01")
    target = paths.coma_dir(root, target_page, "c02")
    source.mkdir(parents=True, exist_ok=True)
    (source / "scene.blend").write_bytes(b"coma")
    original_record = coma_io.record_successful_tree_change

    def fail_after_move(*_args, **_kwargs):
        raise RuntimeError("injected post-move bookkeeping failure")

    completed = []
    coma_io.record_successful_tree_change = fail_after_move
    try:
        try:
            layer_transfer_group._move_coma_files(
                root,
                source_page,
                target_page,
                [layer_transfer_group._ComaMove("c01", "c02")],
                completed=completed,
            )
            raise AssertionError("post-move failure was not propagated")
        except coma_io.ComaFileMoveError as exc:
            assert exc.moved_to_destination
    finally:
        coma_io.record_successful_tree_change = original_record
    assert [(move.source_id, move.target_id) for move in completed] == [
        ("c01", "c02")
    ]
    assert not source.exists() and target.is_dir()
    assert layer_transfer_group._restore_manifest_comas(
        root,
        {
            "source_page_id": source_page,
            "target_page_id": target_page,
            "coma_moves": [
                {
                    "source_id": "c01",
                    "target_id": "c02",
                    "source_existed": True,
                }
            ],
        },
    )
    assert source.is_dir() and not target.exists()
    paths.coma_dir = original_coma_dir


def _make_journal(package, root: Path, stage_id: str, *, phase="preparing"):
    from bmanga_transfer_recovery_test.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
        layer_transfer_recovery_manifest,
        paths,
    )

    source_files = _source_files(paths, root)
    recovery_dir = (
        paths.page_dir(root, SOURCE_ID)
        / "_transfer_recovery"
        / stage_id
    )
    recovery_dir.mkdir(parents=True)
    backup = layer_transfer_group._backup_source_files(
        root,
        SOURCE_ID,
        recovery_dir / "rollback",
    )
    target_stage = cross_page_stage.asset_entry_identity(
        {
            "stage_id": stage_id,
            "target_page_id": TARGET_ID,
            "drop_local_xy_mm": [0.0, 0.0],
            "payload": {"entries": []},
            "state": "prepared",
        }
    )
    assert target_stage is not None
    manifest = layer_transfer_recovery_manifest.build(
        root,
        recovery_dir,
        SOURCE_ID,
        TARGET_ID,
        stage_id,
        [],
        backup,
        target_stage,
        phase=phase,
    )
    manifest_path = recovery_dir / "transaction.json"
    json_io.write_json(manifest_path, manifest)
    return recovery_dir, manifest_path, manifest, backup, source_files


def _expect_recovery_error(layer_transfer_group, root: Path) -> str:
    original_log = layer_transfer_group._logger.exception
    layer_transfer_group._logger.exception = lambda *_args, **_kwargs: None
    try:
        try:
            layer_transfer_group.recover_interrupted_transfers(root)
        except layer_transfer_group.LayerTransferRecoveryError as exc:
            return str(exc)
        raise AssertionError("unsafe recovery journal was accepted")
    finally:
        layer_transfer_group._logger.exception = original_log


def _test_manifest_schema_matrix(package) -> None:
    from bmanga_transfer_recovery_test.utils import (
        json_io,
        layer_transfer_group,
    )

    mutators = {
        "version_bad": lambda value: value.__setitem__("version", 99),
        "phase_bad": lambda value: value.__setitem__("phase", "unknown"),
        "files_missing": lambda value: value.pop("files"),
        "required_missing": lambda value: value["files"].pop(),
        "required_duplicate": lambda value: value["files"].__setitem__(
            2,
            copy.deepcopy(value["files"][0]),
        ),
        "coma_missing": lambda value: value.pop("coma_moves"),
        "coma_invalid": lambda value: value.__setitem__(
            "coma_moves",
            [{"source_id": "../c01", "target_id": "c02", "source_existed": True}],
        ),
    }
    with tempfile.TemporaryDirectory(prefix="bmanga_transfer_schema_") as tmp:
        root = Path(tmp)
        directories = []
        for stage_id, mutate in mutators.items():
            recovery_dir, manifest_path, manifest, _backup, _files = _make_journal(
                package,
                root,
                stage_id,
            )
            mutate(manifest)
            json_io.write_json(manifest_path, manifest)
            directories.append(recovery_dir)
        message = _expect_recovery_error(layer_transfer_group, root)
        assert all(stage_id in message for stage_id in mutators)
        assert all(directory.is_dir() for directory in directories)


def _patch_cleanup_once(layer_transfer_group, recovery_dir: Path):
    original = layer_transfer_group.shutil.rmtree
    failed = {"value": False}

    def fail_once(path, *args, **kwargs):
        candidate = Path(path)
        if (
            candidate.parent.resolve() == recovery_dir.parent.resolve()
            and recovery_dir.name in candidate.name
            and not failed["value"]
        ):
            failed["value"] = True
            raise PermissionError("injected recovery cleanup failure")
        return original(path, *args, **kwargs)

    layer_transfer_group.shutil.rmtree = fail_once
    return original


def _test_terminal_cleanup_retry(package) -> None:
    from bmanga_transfer_recovery_test.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
    )

    for ready in (False,):
        with tempfile.TemporaryDirectory(prefix="bmanga_transfer_terminal_") as tmp:
            root = Path(tmp)
            stage_id = "rollback_cleanup"
            recovery_dir, manifest_path, _manifest, _backup, source_files = (
                _make_journal(package, root, stage_id)
            )
            original_rmtree = _patch_cleanup_once(
                layer_transfer_group,
                recovery_dir,
            )
            try:
                _expect_recovery_error(layer_transfer_group, root)
            finally:
                layer_transfer_group.shutil.rmtree = original_rmtree
            tombstone = next(
                recovery_dir.parent.glob(f".cleanup-{stage_id}-*")
            )
            terminal = json_io.read_json(tombstone / "transaction.json")
            assert terminal["phase"] == "rollback_applied"
            source_files[0].write_bytes(b"external-after-terminal")
            layer_transfer_group.recover_interrupted_transfers(root)
            assert source_files[0].read_bytes() == b"external-after-terminal"
            assert not recovery_dir.exists()


def _test_external_source_replacement(package) -> None:
    from bmanga_transfer_recovery_test.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_transfer_external_") as tmp:
        root = Path(tmp)
        stage_id = "external_replacement"
        recovery_dir, _path, _manifest, _backup, source_files = _make_journal(
            package,
            root,
            stage_id,
            phase="prepared",
        )
        stage_path = cross_page_stage.staged_path(root, TARGET_ID)
        json_io.write_json(
            stage_path,
            {"asset_bundles": [{"stage_id": stage_id, "state": "prepared"}]},
        )
        replaced = (
            b"external-blend",
            b'{"external":"page"}',
            source_files[2].read_bytes().replace(b'"revision": 1', b'"revision": 9'),
        )
        for path, payload in zip(source_files, replaced, strict=True):
            path.write_bytes(payload)
        _expect_recovery_error(layer_transfer_group, root)
        assert tuple(path.read_bytes() for path in source_files) == replaced
        assert recovery_dir.is_dir()
        assert layer_transfer_group._recovery_stage_state(
            root,
            TARGET_ID,
            stage_id,
        ) == "prepared"


def _write_ready_stage(cross_page_stage, json_io, root, manifest) -> Path:
    entry = copy.deepcopy(manifest["target_stage"]["entry"])
    entry["state"] = "ready"
    stage_path = cross_page_stage.staged_path(root, TARGET_ID)
    json_io.write_json(stage_path, {"asset_bundles": [entry]})
    return stage_path


def _test_ready_identity_and_partial_terminal(package) -> None:
    from bmanga_transfer_recovery_test.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
        layer_transfer_recovery_manifest,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_ready_corrupt_") as tmp:
        root = Path(tmp)
        recovery_dir, _path, manifest, backup, source_files = _make_journal(
            package,
            root,
            "ready_corrupt",
        )
        stage_path = _write_ready_stage(
            cross_page_stage,
            json_io,
            root,
            manifest,
        )
        stage_data = json_io.read_json(stage_path)
        stage_data["asset_bundles"][0]["payload"]["injected"] = True
        json_io.write_json(stage_path, stage_data)
        before_sources = tuple(path.read_bytes() for path in source_files)
        before_stage = stage_path.read_bytes()
        _expect_recovery_error(layer_transfer_group, root)
        assert tuple(path.read_bytes() for path in source_files) == before_sources
        assert stage_path.read_bytes() == before_stage
        assert recovery_dir.is_dir()
        assert all(saved is None or saved.is_file() for saved in backup.values())

    with tempfile.TemporaryDirectory(prefix="bmanga_terminal_partial_") as tmp:
        root = Path(tmp)
        recovery_dir, manifest_path, manifest, backup, source_files = _make_journal(
            package,
            root,
            "terminal_partial",
        )
        layer_transfer_recovery_manifest.set_terminal(
            manifest_path,
            manifest,
            "rollback_applied",
        )
        next(saved for saved in backup.values() if saved is not None).unlink()
        source_files[0].write_bytes(b"terminal-partial-external")
        layer_transfer_group.recover_interrupted_transfers(root)
        assert source_files[0].read_bytes() == b"terminal-partial-external"
        assert not recovery_dir.exists()

    with tempfile.TemporaryDirectory(prefix="bmanga_ready_backup_corrupt_") as tmp:
        root = Path(tmp)
        recovery_dir, _path, manifest, backup, source_files = _make_journal(
            package,
            root,
            "ready_backup_corrupt",
        )
        _write_ready_stage(cross_page_stage, json_io, root, manifest)
        next(saved for saved in backup.values() if saved is not None).write_bytes(
            b"corrupt-backup"
        )
        source_files[0].write_bytes(b"ready-commit-source")
        _expect_recovery_error(layer_transfer_group, root)
        assert source_files[0].read_bytes() == b"ready-commit-source"
        assert recovery_dir.is_dir()


def _test_known_active_states_recover(package) -> None:
    from bmanga_transfer_recovery_test.utils import (
        layer_transfer_group,
        layer_transfer_recovery_manifest,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_transfer_preparing_") as tmp:
        root = Path(tmp)
        recovery_dir, manifest_path, manifest, _backup, source_files = (
            _make_journal(package, root, "known_preparing")
        )
        before = tuple(path.read_bytes() for path in source_files)
        source_files[0].write_bytes(b"known-owned-intermediate")
        layer_transfer_recovery_manifest.append_current_state(
            manifest_path,
            root,
            manifest,
        )
        layer_transfer_group.recover_interrupted_transfers(root)
        assert tuple(path.read_bytes() for path in source_files) == before
        assert not recovery_dir.exists()

    with tempfile.TemporaryDirectory(prefix="bmanga_transfer_prepared_") as tmp:
        root = Path(tmp)
        recovery_dir, manifest_path, manifest, rollback, source_files = (
            _make_journal(package, root, "known_prepared")
        )
        source_files[0].write_bytes(b"crash-generation")
        project = json.loads(source_files[2].read_text(encoding="utf-8"))
        project["revision"] = 7
        source_files[2].write_text(json.dumps(project), encoding="utf-8")
        layer_transfer_recovery_manifest.append_current_state(
            manifest_path,
            root,
            manifest,
        )
        crash = layer_transfer_group._backup_source_files(
            root,
            SOURCE_ID,
            recovery_dir / "crash",
        )
        layer_transfer_recovery_manifest.replace_backup(
            manifest_path,
            root,
            manifest,
            crash,
            phase="prepared",
        )
        expected = tuple(path.read_bytes() for path in source_files)
        assert layer_transfer_group._restore_source_files(root, rollback)
        layer_transfer_group.recover_interrupted_transfers(root)
        assert tuple(path.read_bytes() for path in source_files) == expected


def _test_history_redo_invalidation_cleanup(package) -> None:
    from bmanga_transfer_recovery_test.utils import (
        layer_transfer_group,
        layer_transfer_history,
    )

    with tempfile.TemporaryDirectory(prefix="bmanga_transfer_history_cleanup_") as tmp:
        root = Path(tmp)
        recovery_dir, manifest_path, manifest, _backup, source_files = (
            _make_journal(package, root, "history_cleanup")
        )
        token = "history_cleanup"
        layer_transfer_history._records[token] = (
            layer_transfer_history.TransferHistoryRecord(
                token=token,
                work_dir=root,
                source_page_id=SOURCE_ID,
                target_page_id=TARGET_ID,
                stage_id=token,
                recovery_dir=recovery_dir,
                manifest=copy.deepcopy(manifest),
                pre_files={},
                post_files={},
                pre_fingerprints={},
                post_fingerprints={},
                pre_backup_fingerprints={},
                post_backup_fingerprints={},
                stage_entry={},
            )
        )
        work = SimpleNamespace(loaded=True)
        context = SimpleNamespace(scene=SimpleNamespace(bmanga_work=work))
        original_rmtree = _patch_cleanup_once(
            layer_transfer_group,
            recovery_dir,
        )
        try:
            try:
                layer_transfer_history._discard_invalidated_redo(
                    (),
                    context=context,
                )
            except layer_transfer_group.LayerTransferCleanupError:
                pass
            else:
                raise AssertionError("history cleanup failure was hidden")
        finally:
            layer_transfer_group.shutil.rmtree = original_rmtree
        assert work.loaded is False
        assert token in layer_transfer_history._records
        tombstone = next(
            recovery_dir.parent.glob(f".cleanup-{token}-*")
        )
        assert json.loads(
            (tombstone / "transaction.json").read_text(encoding="utf-8")
        )["phase"] == (
            "rollback_applied"
        )
        replacement = b"history-external-replacement"
        source_files[0].write_bytes(replacement)
        layer_transfer_history._discard_invalidated_redo((), context=context)
        assert source_files[0].read_bytes() == replacement
        assert token not in layer_transfer_history._records
        assert not recovery_dir.exists()


def _history_record(package, root: Path, stage_id: str):
    from bmanga_transfer_recovery_test.utils import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
        layer_transfer_history,
    )

    recovery_dir, _path, manifest, pre_files, source_files = _make_journal(
        package,
        root,
        stage_id,
    )
    _write_ready_stage(cross_page_stage, json_io, root, manifest)
    source_files[0].write_bytes(b"post-generation")
    post_files = layer_transfer_group._backup_source_files(
        root,
        SOURCE_ID,
        recovery_dir / "history_post",
    )
    return (
        layer_transfer_history.TransferHistoryRecord(
            token=stage_id,
            work_dir=root,
            source_page_id=SOURCE_ID,
            target_page_id=TARGET_ID,
            stage_id=stage_id,
            recovery_dir=recovery_dir,
            manifest=copy.deepcopy(manifest),
            pre_files=pre_files,
            post_files=post_files,
            pre_fingerprints=layer_transfer_history._backup_generation(pre_files),
            post_fingerprints=layer_transfer_history._current_generation(post_files),
            pre_backup_fingerprints=layer_transfer_history._backup_generation(
                pre_files
            ),
            post_backup_fingerprints=layer_transfer_history._backup_generation(
                post_files
            ),
            stage_entry=copy.deepcopy(manifest["target_stage"]["entry"]),
        ),
        source_files,
    )


def _test_history_generation_guards(package) -> None:
    from bmanga_transfer_recovery_test.utils import (
        cross_page_stage,
        history_runtime,
        layer_command_runtime,
        layer_transfer_group,
        layer_transfer_history,
        shortcut_visibility,
    )

    for mode in ("external", "pre_backup", "post_backup"):
        with tempfile.TemporaryDirectory(prefix=f"bmanga_history_{mode}_") as tmp:
            root = Path(tmp)
            record, source_files = _history_record(package, root, mode)
            layer_transfer_history._records[record.token] = record
            work = SimpleNamespace(loaded=True)
            context = SimpleNamespace(scene=SimpleNamespace(bmanga_work=work))
            if mode == "external":
                source_files[0].write_bytes(b"external-history-change")
                undo = True
            elif mode == "pre_backup":
                next(
                    saved
                    for saved in record.pre_files.values()
                    if saved is not None
                ).write_bytes(b"damaged-pre-backup")
                undo = True
            else:
                assert layer_transfer_group._restore_source_files(
                    root,
                    record.pre_files,
                )
                layer_transfer_group._remove_stage(root, TARGET_ID, mode)
                next(
                    saved
                    for saved in record.post_files.values()
                    if saved is not None
                ).write_bytes(b"damaged-post-backup")
                undo = False
            before_sources = tuple(path.read_bytes() for path in source_files)
            stage_path = cross_page_stage.staged_path(root, TARGET_ID)
            before_stage = stage_path.read_bytes() if stage_path.is_file() else None
            try:
                layer_transfer_history._apply(
                    record.token,
                    undo=undo,
                    context=context,
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"history {mode} damage was accepted")
            assert tuple(path.read_bytes() for path in source_files) == before_sources
            assert (
                stage_path.read_bytes() if stage_path.is_file() else None
            ) == before_stage
            assert work.loaded is False

    work = SimpleNamespace(loaded=True)
    context = SimpleNamespace(scene=SimpleNamespace(bmanga_work=work))
    history_runtime._fail_closed(context, "injected history double failure")
    assert shortcut_visibility.shortcut_file_scope_allowed(context) is False
    transfer_writes = {"count": 0}
    original_execute = layer_transfer_group._execute_cross_page
    layer_transfer_group._execute_cross_page = (
        lambda *_args, **_kwargs: transfer_writes.__setitem__(
            "count",
            transfer_writes["count"] + 1,
        )
    )
    try:
        target = SimpleNamespace(
            kind="page",
            page=SimpleNamespace(id=TARGET_ID),
        )
        assert layer_transfer_group.transfer_group_to_page(context, target) == 0
        mutation_calls = {"count": 0}
        assert layer_command_runtime.execute(
            context,
            items=(),
            operation="history-block-probe",
            mutate=lambda: mutation_calls.__setitem__(
                "count",
                mutation_calls["count"] + 1,
            ),
        ) == 0
        assert transfer_writes["count"] == 0
        assert mutation_calls["count"] == 0
    finally:
        layer_transfer_group._execute_cross_page = original_execute
        history_runtime.reset_after_file_load()


def main() -> None:
    package = _load_addon()
    registered = False
    try:
        _test_manifest_schema_matrix(package)
        _test_terminal_cleanup_retry(package)
        _test_external_source_replacement(package)
        _test_ready_identity_and_partial_terminal(package)
        _test_known_active_states_recover(package)
        _test_history_redo_invalidation_cleanup(package)
        _test_history_generation_guards(package)
        with tempfile.TemporaryDirectory(prefix="bmanga_transfer_recovery_") as tmp:
            root = Path(tmp)
            _test_recovery_point_double_fault(package, root)
            _test_startup_recovery_aggregate(package, root)
            registered = True
            _test_move_after_physical_success(package, root)
        print("BMANGA_TRANSFER_RECOVERY_FAIL_CLOSED_OK")
    finally:
        if registered:
            package.unregister()


if __name__ == "__main__":
    main()
