"""新Domain作品のnative/sidecar保存復旧契約。"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_native_save_guard_test"


def _load_runtime():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules.setdefault(PACKAGE, package)
    io_name = f"{PACKAGE}.io"
    io_package = types.ModuleType(io_name)
    io_package.__path__ = [str(ROOT / "io")]
    sys.modules.setdefault(io_name, io_package)
    native = importlib.import_module(f"{io_name}.native_save_guard")
    sidecar = importlib.import_module(f"{io_name}.sidecar_save_guard")
    baseline = importlib.import_module(f"{io_name}.save_baseline")
    return native, sidecar, baseline


NATIVE, SIDECAR, BASELINE = _load_runtime()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _work(tmp_path: Path, name: str) -> tuple[Path, Path, Path]:
    work = tmp_path / f"{name}.bmanga"
    work.mkdir()
    project = work / "project.json"
    project.write_text(
        json.dumps(
            {
                "schema": "bmanga.project",
                "schemaVersion": 1,
                "projectUid": "project_0123456789abcdef0123456789abcdef",
                "revision": 0,
                "settings": {},
                "pageOrder": [],
                "pages": {},
            }
        ),
        encoding="utf-8",
    )
    blend = work / "work.blend"
    _write(blend, b"old-blend")
    BASELINE.capture_loaded_baseline(
        work,
        blend,
        content_paths=(project,),
    )
    return work, project, blend


def _begin_transaction(work: Path, project: Path, blend: Path):
    token = NATIVE.begin_native_save(blend)
    assert token is not None and not token.requires_restore
    NATIVE.prepare_native_save_sidecars(token, (project,))
    return token


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    else:
        os.symlink(target, link, target_is_directory=True)


def test_successful_checkpoint_keeps_one_new_generation(tmp_path):
    work, project, blend = _work(tmp_path, "Success")
    token = _begin_transaction(work, project, blend)
    NATIVE.mark_native_save_metadata_result(token, True)
    _write(blend, b"new-blend")
    _write(project, b'{"new":"project"}')

    result = NATIVE.finish_native_save(token)

    assert not result.restored
    assert result.metadata_saved
    assert blend.read_bytes() == b"new-blend"
    assert project.read_bytes() == b'{"new":"project"}'
    assert not (work / ".bmanga-save-recovery-v1").exists()


def test_metadata_failure_restores_blend_and_project_together(tmp_path):
    work, project, blend = _work(tmp_path, "MetadataFailure")
    before_project = project.read_bytes()
    token = _begin_transaction(work, project, blend)
    _write(blend, b"partial-blend")
    _write(project, b'{"partial":true}')

    NATIVE.mark_native_save_metadata_result(token, False, error="forced")
    result = NATIVE.finish_native_save(token)

    assert result.restored
    assert blend.read_bytes() == b"old-blend"
    assert project.read_bytes() == before_project
    assert not (work / ".bmanga-save-recovery-v1").exists()


def test_process_crash_before_commit_recovers_previous_generation(tmp_path):
    work, project, blend = _work(tmp_path, "CrashRollback")
    before_project = project.read_bytes()
    token = _begin_transaction(work, project, blend)
    NATIVE.mark_native_save_metadata_result(token, True)
    _write(blend, b"uncommitted-blend")
    _write(project, b'{"uncommitted":true}')
    NATIVE._release(token)

    restored = NATIVE.recover_pending_native_saves(work)

    assert set(restored) == {blend, project}
    assert blend.read_bytes() == b"old-blend"
    assert project.read_bytes() == before_project
    assert not (work / ".bmanga-save-recovery-v1").exists()


def test_commit_decision_survives_process_crash(tmp_path):
    work, project, blend = _work(tmp_path, "CrashAfterDecision")
    token = _begin_transaction(work, project, blend)
    NATIVE.mark_native_save_metadata_result(token, True)
    _write(blend, b"committed-blend")
    _write(project, b'{"committed":true}')
    NATIVE._write_native_status(token, "commit_decided")
    NATIVE._release(token)

    restored = NATIVE.recover_pending_native_saves(work)

    assert restored == ()
    assert blend.read_bytes() == b"committed-blend"
    assert project.read_bytes() == b'{"committed":true}'
    assert not (work / ".bmanga-save-recovery-v1").exists()


def test_unowned_short_guard_never_overwrites_valid_blend(tmp_path):
    work, _project, blend = _work(tmp_path, "UnownedGuard")
    guard = blend.with_name(".bmanga-r")
    _write(guard, b"unowned")

    with pytest.raises(
        NATIVE.NativeSaveRecoveryError,
        match="所有者を確認できない",
    ):
        NATIVE.recover_pending_native_saves(work)
    assert blend.read_bytes() == b"old-blend"
    assert guard.read_bytes() == b"unowned"


def test_corrupted_owned_guard_is_preserved_for_manual_recovery(tmp_path):
    work, project, blend = _work(tmp_path, "CorruptedGuard")
    token = _begin_transaction(work, project, blend)
    assert token.recovery_path is not None
    _write(blend, b"partial-new-blend")
    _write(token.recovery_path, b"corrupted-backup")
    NATIVE._release(token)

    with pytest.raises(
        NATIVE.NativeSaveRecoveryError,
        match="破損",
    ):
        NATIVE.recover_pending_native_saves(work)
    assert blend.read_bytes() == b"partial-new-blend"
    assert token.recovery_path.read_bytes() == b"corrupted-backup"
    assert token.journal_path is not None and token.journal_path.is_file()


def test_interrupted_first_page_save_deletes_uncommitted_native_file(tmp_path):
    work, project, blend = _work(tmp_path, "FirstPage")
    page_blend = (
        work
        / "pages"
        / "page_0123456789abcdef0123456789abcdef"
        / "page.blend"
    )
    page_blend.parent.mkdir(parents=True)
    token = NATIVE.begin_native_save(page_blend)
    assert token is not None and not token.requires_restore
    NATIVE.prepare_native_save_sidecars(token, (project,))
    NATIVE.mark_native_save_metadata_result(token, True)
    _write(page_blend, b"uncommitted-page")
    NATIVE._release(token)

    restored = NATIVE.recover_pending_native_saves(work)

    assert page_blend in restored
    assert not page_blend.exists()
    assert blend.read_bytes() == b"old-blend"


def test_first_nested_coma_save_arms_before_parent_exists(tmp_path):
    work, project, _blend = _work(tmp_path, "FirstNestedComa" + "x" * 64)
    coma_blend = (
        work
        / "pages"
        / "page_0123456789abcdef0123456789abcdef"
        / "comas"
        / "coma_0123456789abcdef0123456789abcdef"
        / "scene.blend"
    )
    assert not coma_blend.parent.exists()

    token = NATIVE.begin_native_save(coma_blend)
    assert token is not None and not token.requires_restore
    NATIVE.prepare_native_save_sidecars(token, (project,))
    assert coma_blend.parent.is_dir()
    assert token.creation_marker is not None
    assert token.creation_marker.is_file()

    NATIVE.mark_native_save_metadata_result(token, True)
    _write(coma_blend, b"new-coma")
    result = NATIVE.finish_native_save(token)

    assert not result.restored
    assert coma_blend.read_bytes() == b"new-coma"
    assert token.creation_marker is not None
    assert not token.creation_marker.exists()


def test_external_change_is_restored_and_requires_reload(tmp_path):
    work, project, blend = _work(tmp_path, "Conflict")
    _write(project, b'{"external":true}')

    token = NATIVE.begin_native_save(blend)

    assert token is not None and token.requires_restore
    assert token.reload_after_restore
    _write(blend, b"stale-screen-save")
    result = NATIVE.finish_native_save(token)
    assert result.restored and result.reload_required
    assert blend.read_bytes() == b"old-blend"
    assert project.read_bytes() == b'{"external":true}'


def test_sidecar_restore_is_physical_even_when_status_write_fails(
    tmp_path,
    monkeypatch,
):
    work, project, _blend = _work(tmp_path, "SidecarStatus")
    before = project.read_bytes()
    token = SIDECAR.begin_sidecar_save(work, (project,))
    SIDECAR.mark_sidecar_writes_started(token)
    _write(project, b'{"partial":true}')
    monkeypatch.setattr(
        SIDECAR,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")),
    )

    assert SIDECAR.restore_sidecars(token)
    assert project.read_bytes() == before


def test_sidecar_restore_prunes_only_empty_descendants_of_new_tree(tmp_path):
    work, _project, _blend = _work(tmp_path, "SidecarPruneTree")
    page = work / "pages" / "page_0123456789abcdef0123456789abcdef"
    page_json = page / "page.json"
    token = SIDECAR.begin_sidecar_save(
        work,
        (page_json,),
        prune_empty_dirs=(page,),
    )
    SIDECAR.mark_sidecar_writes_started(token)
    (page / "assets").mkdir(parents=True)
    (page / "comas").mkdir()
    _write(page_json, b'{"partial":true}')

    assert SIDECAR.restore_sidecars(token)
    assert not page.exists()

    kept_page = work / "pages" / "page_fedcba9876543210fedcba9876543210"
    kept_json = kept_page / "page.json"
    kept_token = SIDECAR.begin_sidecar_save(
        work,
        (kept_json,),
        prune_empty_dirs=(kept_page,),
    )
    SIDECAR.mark_sidecar_writes_started(kept_token)
    (kept_page / "assets").mkdir(parents=True)
    _write(kept_page / "assets" / "unknown.bin", b"keep")
    _write(kept_json, b'{"partial":true}')

    assert SIDECAR.restore_sidecars(kept_token)
    assert (kept_page / "assets" / "unknown.bin").read_bytes() == b"keep"


def test_recovery_rejects_linked_native_journal_hierarchy(
    tmp_path,
    monkeypatch,
):
    work, project, blend = _work(tmp_path, "LinkedJournal")
    token = _begin_transaction(work, project, blend)
    assert token.journal_path is not None
    journal = NATIVE.read_json_mapping(token.journal_path)
    linked = token.journal_path.parent
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked or original(path),
    )

    with pytest.raises(NATIVE.NativeSaveRecoveryError, match="配置が不正"):
        NATIVE._validate_journal(token.journal_path, journal, work)

    monkeypatch.setattr(Path, "is_symlink", original)
    NATIVE.finish_native_save(token, native_save_succeeded=False)


def test_cleanup_removes_only_expired_journal_less_transaction(tmp_path):
    work, _project, _blend = _work(tmp_path, "Cleanup")
    base = NATIVE._base(work)
    base.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    old_id = (
        (now - timedelta(hours=25)).strftime("%Y%m%dT%H%M%SZ")
        + "-aaaaaaaaaaaa"
    )
    recent_id = (
        (now - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
        + "-bbbbbbbbbbbb"
    )
    old = base / old_id
    recent = base / recent_id
    old.mkdir()
    recent.mkdir()
    _write(old / "partial.bin", b"x")

    removed = NATIVE.cleanup_stale_transactions(work)

    assert old in removed
    assert not old.exists()
    assert recent.is_dir()


def test_cleanup_preserves_copying_name_outside_native_save_directories(tmp_path):
    work, _project, _blend = _work(tmp_path, "CopyingScope")
    native_copy = work / NATIVE._NATIVE_COPYING_NAME
    unrelated_copy = work / "assets" / NATIVE._NATIVE_COPYING_NAME
    _write(native_copy, b"interrupted-native-copy")
    _write(unrelated_copy, b"user-data")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
    os.utime(native_copy, (old_timestamp, old_timestamp))
    os.utime(unrelated_copy, (old_timestamp, old_timestamp))

    removed = NATIVE.cleanup_stale_transactions(work)

    assert native_copy in removed
    assert not native_copy.exists()
    assert unrelated_copy.read_bytes() == b"user-data"


def test_cleanup_rejects_junction_recovery_base_outside_work(tmp_path):
    work, _project, _blend = _work(tmp_path, "JunctionBase")
    outside = tmp_path / "outside-native"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    old_id = "20000101T000000Z-aaaaaaaaaaaa"
    (outside / old_id).mkdir()
    _write(outside / old_id / "partial.bin", b"x")
    root = work / ".bmanga-save-recovery-v1"
    root.mkdir()
    _make_directory_link(root / "native", outside)

    assert NATIVE.cleanup_stale_transactions(work) == ()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (outside / old_id / "partial.bin").read_bytes() == b"x"


def test_cleanup_rejects_junction_transaction_outside_work(tmp_path):
    work, _project, _blend = _work(tmp_path, "JunctionEntry")
    outside = tmp_path / "outside-sidecar"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    base = SIDECAR._base(work)
    base.mkdir(parents=True)
    old_id = "20000101T000000Z-bbbbbbbbbbbb"
    _make_directory_link(base / old_id, outside)

    assert SIDECAR.cleanup_stale_transactions(work) == ()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_non_domain_directory_does_not_activate_native_guard(tmp_path):
    work = tmp_path / "OldLayout.bmanga"
    work.mkdir()
    _write(work / "work.json", b"{}")
    blend = work / "work.blend"
    _write(blend, b"legacy")

    assert NATIVE.begin_native_save(blend) is None
