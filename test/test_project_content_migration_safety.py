"""作品移行の競合・復旧境界を純Pythonで検証する。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_migration():
    path = ROOT / "io" / "project_content_migration.py"
    name = "_bmanga_project_content_migration_safety_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


def _load_native_guard():
    path = ROOT / "io" / "project_content_native_save_guard.py"
    name = "_bmanga_native_save_guard_safety_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NATIVE_GUARD = _load_native_guard()
BASELINE = sys.modules["project_content_save_baseline"]


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _work(tmp_path: Path, name: str = "Safety", pages: int = 2) -> Path:
    work = tmp_path / f"{name}.bmanga"
    work.mkdir()
    _write_json(work / "work.json", {"detailDataVersion": 0})
    for index in range(1, pages + 1):
        page = work / f"p{index:04d}" / "page.blend"
        page.parent.mkdir()
        page.write_bytes(f"old-{index}".encode())
    return work


def _plan(work: Path):
    return MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, path: MIGRATION.PageInspection(
            estimated_output_bytes=path.stat().st_size + 4,
            facts={"pageDetailDataVersion": 0},
        ),
    )


def _convert(task) -> None:
    task.staged_path.write_bytes(task.staged_path.read_bytes() + b"-new")


def test_confirmation_refusal_creates_neither_lock_nor_transaction(tmp_path):
    work = _work(tmp_path, pages=1)
    plan = _plan(work)
    with pytest.raises(MIGRATION.ConfirmationRequired):
        MIGRATION.execute_migration(
            plan,
            confirmed=False,
            converter=_convert,
            validator=lambda _page_id, _path: True,
        )
    assert not MIGRATION._lock.lock_file_path(work).exists()
    assert not plan.transaction_dir.exists()


def test_unknown_concurrent_hash_stops_before_any_rollback_write(tmp_path):
    work = _work(tmp_path)
    first = work / "p0001" / "page.blend"
    second = work / "p0002" / "page.blend"
    plan = _plan(work)

    def conflict(event, _page_id, index):
        if event == "after_swap_replace" and index == 1:
            second.write_bytes(b"concurrent-save")
            raise RuntimeError("injected conflict")

    with pytest.raises(MIGRATION.RecoveryError):
        MIGRATION.execute_migration(
            plan,
            confirmed=True,
            converter=_convert,
            validator=lambda _page_id, _path: True,
            fault_hook=conflict,
        )

    # p0002 の未知変更を見つける前に p0001 を戻してはいけない。
    assert first.read_bytes() == b"old-1-new"
    assert second.read_bytes() == b"concurrent-save"
    assert json.loads((work / "work.json").read_text())["detailDataVersion"] == 0


def _interrupted_journal(work: Path) -> Path:
    plan = _plan(work)

    def stop(event, _page_id, index):
        if event == "after_backup" and index == 1:
            raise RuntimeError("stop after backup")

    with pytest.raises(MIGRATION.MigrationExecutionError):
        MIGRATION.execute_migration(
            plan,
            confirmed=True,
            converter=_convert,
            validator=lambda _page_id, _path: True,
            fault_hook=stop,
            auto_rollback_on_error=False,
        )
    return plan.journal_path


def test_mismatched_expected_work_is_rejected_before_status_shortcut(tmp_path):
    work = _work(tmp_path, "Expected", pages=1)
    other = _work(tmp_path, "Other", pages=0)
    journal = _interrupted_journal(work)
    before = (work / "p0001" / "page.blend").read_bytes()
    with pytest.raises(MIGRATION.RecoveryError):
        MIGRATION.recover_transaction(
            journal, expected_work_dir=other, force=True
        )
    assert (work / "p0001" / "page.blend").read_bytes() == before


def test_future_journal_version_is_rejected_even_when_status_says_done(tmp_path):
    work = _work(tmp_path, "FutureJournal", pages=1)
    journal = _interrupted_journal(work)
    data = json.loads(journal.read_text(encoding="utf-8"))
    data["journalVersion"] = 2
    data["status"] = "rolled_back"
    _write_json(journal, data)
    with pytest.raises(MIGRATION.RecoveryError):
        MIGRATION.recover_transaction(
            journal, expected_work_dir=work, force=True
        )


def test_forged_work_dir_in_journal_is_rejected(tmp_path):
    work = _work(tmp_path, "Forged", pages=1)
    other = _work(tmp_path, "ForgedOther", pages=0)
    journal = _interrupted_journal(work)
    data = json.loads(journal.read_text(encoding="utf-8"))
    data["workDir"] = str(other)
    _write_json(journal, data)
    with pytest.raises(MIGRATION.RecoveryError):
        MIGRATION.recover_transaction(
            journal, expected_work_dir=work, force=True
        )


def test_capacity_drop_after_staging_rolls_back_without_source_changes(tmp_path, monkeypatch):
    work = _work(tmp_path, "Capacity", pages=1)
    page = work / "p0001" / "page.blend"
    original_page = page.read_bytes()
    original_work = (work / "work.json").read_bytes()
    plan = _plan(work)
    calls = 0

    def shrinking_free(_path):
        nonlocal calls
        calls += 1
        return 1 << 40 if calls == 1 else 0

    monkeypatch.setattr(MIGRATION._capacity, "_disk_free", shrinking_free)
    with pytest.raises(MIGRATION.MigrationExecutionError):
        MIGRATION.execute_migration(
            plan,
            confirmed=True,
            converter=_convert,
            validator=lambda _page_id, _path: True,
        )
    assert page.read_bytes() == original_page
    assert (work / "work.json").read_bytes() == original_work


def test_invalid_and_future_work_markers_block_preflight(tmp_path):
    invalid = _work(tmp_path, "InvalidMarker", pages=0)
    _write_json(invalid / "work.json", {"detailDataVersion": True})
    invalid_plan = MIGRATION.build_migration_plan(invalid, inspector=lambda *_: {})
    assert [issue.code for issue in invalid_plan.issues] == ["invalid_detail_data_version"]

    future = _work(tmp_path, "FutureMarker", pages=0)
    _write_json(future / "work.json", {"detailDataVersion": 99})
    future_plan = MIGRATION.build_migration_plan(future, inspector=lambda *_: {})
    assert [issue.code for issue in future_plan.issues] == ["future_detail_data_version"]


def test_os_releases_work_lock_after_hard_process_crash(tmp_path):
    work = _work(tmp_path, "HardCrash", pages=0)
    BASELINE.capture_loaded_baseline(work, work / "work.blend")
    lock_module = ROOT / "io" / "project_content_migration_lock.py"
    code = (
        "import importlib.util,sys,time;"
        "s=importlib.util.spec_from_file_location('isolated_lock',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "c=m.work_lock(sys.argv[2]);c.__enter__();"
        "print('LOCKED',flush=True);time.sleep(60)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(lock_module), str(work)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "LOCKED"
        with pytest.raises(MIGRATION._lock.WorkLockError):
            with MIGRATION._lock.guard_path_write(work / "work.json"):
                pass
        proc.kill()  # OSレベルの強制終了を模擬する。
        proc.wait(timeout=10)
        with MIGRATION._lock.guard_path_write(work / "work.json"):
            pass
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_missing_baseline_never_adopts_existing_work_on_first_write(tmp_path):
    work = _work(tmp_path, "MissingBaseline", pages=0)
    with pytest.raises(MIGRATION._lock.WorkLockError):
        with MIGRATION._lock.guard_path_write(work / "work.json"):
            pass


def test_same_version_external_json_change_arms_native_restore(tmp_path):
    work = _work(tmp_path, "SameVersionConflict", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1, "title": "before"})
    _write_json(work / "pages.json", {"pages": [], "lastModified": "first"})
    page = work / "p0001" / "page.blend"
    original = page.read_bytes()
    BASELINE.capture_loaded_baseline(work, page)
    _write_json(work / "work.json", {"detailDataVersion": 1, "title": "external"})

    token = NATIVE_GUARD.begin_native_save(page, 1)
    assert token is not None and token.requires_restore
    assert str(work / "work.json").casefold() in {
        path.casefold() for path in token.conflict_paths
    }
    page.write_bytes(b"old-window-save")
    result = NATIVE_GUARD.finish_native_save(token)
    assert result.restored and page.read_bytes() == original


def test_timestamp_only_json_change_is_not_same_version_conflict(tmp_path):
    work = _work(tmp_path, "TimestampOnly", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1, "lastSaved": "first"})
    _write_json(work / "pages.json", {"pages": [], "lastModified": "first"})
    page = work / "p0001" / "page.blend"
    BASELINE.capture_loaded_baseline(work, page)
    _write_json(work / "work.json", {"detailDataVersion": 1, "lastSaved": "second"})
    _write_json(work / "pages.json", {"pages": [], "lastModified": "second"})

    token = NATIVE_GUARD.begin_native_save(page, 1)
    assert token is not None and not token.requires_restore
    NATIVE_GUARD.finish_native_save(token)


def test_immediate_json_write_rejects_external_change_before_overwrite(tmp_path):
    work = _work(tmp_path, "ImmediateJson", pages=0)
    target = work / "work.json"
    BASELINE.capture_loaded_baseline(work, work / "work.blend")
    external = b'{"detailDataVersion": 0, "external": true}'
    target.write_bytes(external)

    with pytest.raises(MIGRATION._lock.WorkLockError):
        with MIGRATION._lock.guard_path_write(target):
            target.write_bytes(b"old-window")
    assert target.read_bytes() == external


def test_external_raster_change_is_rejected_before_first_png_save(tmp_path):
    work = _work(tmp_path, "RasterConflict", pages=1)
    page = work / "p0001" / "page.blend"
    raster = work / "raster" / "ink.png"
    raster.parent.mkdir()
    raster.write_bytes(b"loaded-pixels")
    BASELINE.capture_loaded_baseline(work, page, content_paths=(raster,))
    raster.write_bytes(b"other-window-pixels")

    with pytest.raises(MIGRATION._lock.WorkLockError):
        with MIGRATION._lock.guard_path_write(raster):
            raster.write_bytes(b"old-window-pixels")
    assert raster.read_bytes() == b"other-window-pixels"


def test_derived_page_preview_change_does_not_block_user_data_save(tmp_path):
    work = _work(tmp_path, "DerivedPreview", pages=1)
    page = work / "p0001" / "page.blend"
    preview = work / "p0001" / "page_preview.png"
    preview.write_bytes(b"first-preview")
    BASELINE.capture_loaded_baseline(work, page, content_paths=(preview,))

    # 別画面の再生成を模擬。派生キャッシュだけなら作品保存は止めない。
    preview.write_bytes(b"other-window-preview")
    BASELINE.assert_no_external_changes(work)

    # 自画面で再生成した成功点は、追跡情報として引き続き記録できる。
    preview.write_bytes(b"current-window-preview")
    BASELINE.record_successful_write(preview)
    assert preview in BASELINE.tracked_paths(work)

    # 同じ作品のユーザーデータ変更は従来どおり競合になる。
    work_json = work / "work.json"
    work_json.write_bytes(b'{"detailDataVersion": 0, "external": true}')
    with pytest.raises(BASELINE.SaveBaselineConflictError):
        BASELINE.assert_no_external_changes(work)


def test_successful_tree_move_and_delete_refresh_old_and_new_paths(tmp_path):
    work = _work(tmp_path, "TreeMove", pages=2)
    source = work / "p0001" / "c01"
    destination = work / "p0002" / "c02"
    source.mkdir()
    source_json = source / "c01.json"
    source_blend = source / "c01.blend"
    source_json.write_bytes(b'{"value": "before"}')
    source_blend.write_bytes(b"before-blend")
    BASELINE.capture_loaded_baseline(work, work / "p0001" / "page.blend")
    BASELINE.record_observed_read(source_json)
    BASELINE.record_observed_read(source_blend)

    source.rename(destination)
    BASELINE.record_successful_tree_change(source, destination)
    BASELINE.assert_no_external_changes(work)
    BASELINE.assert_existing_target_tracked(work, destination / "c01.json")
    BASELINE.assert_existing_target_tracked(work, destination / "c01.blend")

    shutil.rmtree(destination)
    BASELINE.record_successful_tree_change(destination)
    BASELINE.assert_no_external_changes(work)


def test_explicit_blend_read_can_adopt_previously_untracked_page(tmp_path):
    work = _work(tmp_path, "ObservedPageBlend", pages=2)
    current = work / "p0001" / "page.blend"
    other = work / "p0002" / "page.blend"
    BASELINE.capture_loaded_baseline(work, current)

    with pytest.raises(BASELINE.SaveBaselineConflictError):
        BASELINE.assert_existing_target_tracked(work, other)
    BASELINE.record_observed_read(other)
    BASELINE.assert_existing_target_tracked(work, other)


def test_save_as_existing_untracked_blend_is_restored(tmp_path):
    work = _work(tmp_path, "SaveAsExisting", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    current = work / "p0001" / "page.blend"
    target = work / "p0001" / "copy.blend"
    target.write_bytes(b"other-window-existing-target")
    original = target.read_bytes()
    BASELINE.capture_loaded_baseline(work, current)

    token = NATIVE_GUARD.begin_native_save(target, 1)
    assert token is not None and token.requires_restore
    assert not target.exists()
    target.write_bytes(b"current-window-save-as")
    result = NATIVE_GUARD.finish_native_save(token)

    assert result.restored
    assert target.read_bytes() == original


def test_metadata_failure_arms_restore_before_native_save(tmp_path):
    work = _work(tmp_path, "MetadataFailure", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    page = work / "p0001" / "page.blend"
    original = page.read_bytes()
    BASELINE.capture_loaded_baseline(work, page)
    token = NATIVE_GUARD.begin_native_save(page, 1)
    assert token is not None and not token.requires_restore

    NATIVE_GUARD.mark_native_save_metadata_result(token, False, error="injected")
    assert token.requires_restore and not page.exists()
    page.write_bytes(b"native-save-after-metadata-failure")
    result = NATIVE_GUARD.finish_native_save(token)
    assert result.restored and not result.metadata_saved
    assert page.read_bytes() == original


def test_rename_fallback_promotes_only_verified_recovery(tmp_path, monkeypatch):
    work = _work(tmp_path, "VerifiedFallback", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    page = work / "p0001" / "page.blend"
    original = page.read_bytes()
    BASELINE.capture_loaded_baseline(work, page)
    real_replace = os.replace
    injected = False

    def fail_initial_rename(src, dst):
        nonlocal injected
        if Path(src) == page and str(dst).endswith(".native-recovery") and not injected:
            injected = True
            raise PermissionError("injected sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr(NATIVE_GUARD.os, "replace", fail_initial_rename)
    token = NATIVE_GUARD.begin_native_save(page, 0)
    assert token is not None and token.recovery_path is not None
    assert token.recovery_path.read_bytes() == original
    assert not tuple(page.parent.glob("*.native-copying"))
    page.write_bytes(b"stale")
    result = NATIVE_GUARD.finish_native_save(token)
    assert result.restored and page.read_bytes() == original


def test_metadata_failure_restores_blend_and_all_sidecars_byte_exact(tmp_path):
    work = _work(tmp_path, "SidecarRollback", pages=1)
    page = work / "p0001" / "page.blend"
    work_json = work / "work.json"
    pages_json = work / "pages.json"
    page_json = work / "p0001" / "page.json"
    raster = work / "raster" / "ink.png"
    _write_json(work_json, {"detailDataVersion": 1, "value": "old-work"})
    pages_json.write_bytes(b"old-pages")
    page_json.write_bytes(b"old-page-json")
    raster.parent.mkdir()
    raster.write_bytes(b"old-png")
    paths = (work_json, pages_json, page_json, raster)
    before = {path: path.read_bytes() for path in (page, *paths)}
    BASELINE.capture_loaded_baseline(
        work,
        page,
        page_json_paths=(page_json,),
        content_paths=(raster,),
    )
    token = NATIVE_GUARD.begin_native_save(page, 1)
    assert token is not None and not token.requires_restore
    NATIVE_GUARD.prepare_native_save_sidecars(token, paths)

    for index, path in enumerate(paths):
        path.write_bytes(f"partial-{index}".encode())
    NATIVE_GUARD.mark_native_save_metadata_result(token, False, error="injected")
    page.write_bytes(b"partial-blend")
    result = NATIVE_GUARD.finish_native_save(token)

    assert result.restored and not result.metadata_saved
    assert {path: path.read_bytes() for path in (page, *paths)} == before
    native_base = work.parent / f".{work.name}.native-save-recovery-v1"
    sidecar_base = work.parent / f".{work.name}.sidecar-save-recovery-v1"
    assert not tuple(native_base.glob("*/"))
    assert not tuple(sidecar_base.glob("*/"))


def test_repeated_successful_saves_do_not_accumulate_recovery_transactions(tmp_path):
    work = _work(tmp_path, "RepeatedCommit", pages=1)
    page = work / "p0001" / "page.blend"
    work_json = work / "work.json"
    pages_json = work / "pages.json"
    _write_json(work_json, {"detailDataVersion": 1, "value": 0})
    pages_json.write_bytes(b"pages-0")
    sidecars = (work_json, pages_json)
    BASELINE.capture_loaded_baseline(work, page)

    for index in range(1, 4):
        token = NATIVE_GUARD.begin_native_save(page, 1)
        assert token is not None and not token.requires_restore
        NATIVE_GUARD.prepare_native_save_sidecars(token, sidecars)
        _write_json(work_json, {"detailDataVersion": 1, "value": index})
        pages_json.write_bytes(f"pages-{index}".encode())
        BASELINE.record_successful_write(work_json)
        BASELINE.record_successful_write(pages_json)
        NATIVE_GUARD.mark_native_save_metadata_result(token, True)
        page.write_bytes(f"blend-{index}".encode())
        result = NATIVE_GUARD.finish_native_save(token)
        assert not result.restored and result.metadata_saved

        native_base = work.parent / f".{work.name}.native-save-recovery-v1"
        sidecar_base = work.parent / f".{work.name}.sidecar-save-recovery-v1"
        assert not tuple(native_base.glob("*/"))
        assert not tuple(sidecar_base.glob("*/"))


def test_native_stale_save_is_restored_after_save_post(tmp_path):
    work = _work(tmp_path, "NativePost", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    page = work / "p0001" / "page.blend"
    original = page.read_bytes()

    token = NATIVE_GUARD.begin_native_save(page, 0)
    assert token is not None and token.requires_restore
    assert not page.exists()
    page.write_bytes(b"stale-native-save")
    result = NATIVE_GUARD.finish_native_save(token)

    assert result.restored and result.reload_required
    assert page.read_bytes() == original


def test_native_stale_save_is_recovered_after_interrupted_post(tmp_path):
    work = _work(tmp_path, "NativeCrash", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    page = work / "p0001" / "page.blend"
    original = page.read_bytes()

    token = NATIVE_GUARD.begin_native_save(page, 0)
    assert token is not None
    page.write_bytes(b"stale-before-crash")
    # プロセス終了時のOS解放を、テスト内では復元なしのreleaseで模擬する。
    NATIVE_GUARD._release(token)
    restored = NATIVE_GUARD.recover_pending_native_saves(work)

    assert restored == (page,)
    assert page.read_bytes() == original


def test_native_restore_still_works_when_external_journal_write_fails(
    tmp_path, monkeypatch
):
    work = _work(tmp_path, "NativeNoJournal", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    page = work / "p0001" / "page.blend"
    original = page.read_bytes()

    monkeypatch.setattr(
        NATIVE_GUARD,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("journal denied")),
    )
    token = NATIVE_GUARD.begin_native_save(page, 0)
    assert token is not None and token.journal_path is None
    page.write_bytes(b"stale-without-journal")
    result = NATIVE_GUARD.finish_native_save(token)

    assert result.restored
    assert page.read_bytes() == original


def test_pending_native_recovery_blocks_project_migration(tmp_path):
    work = _work(tmp_path, "NativePendingBlocksMigration", pages=1)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    page = work / "p0001" / "page.blend"
    token = NATIVE_GUARD.begin_native_save(page, 0)
    assert token is not None
    page.write_bytes(b"stale-pending-native")
    NATIVE_GUARD._release(token)

    plan = MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, path: MIGRATION.PageInspection(
            estimated_output_bytes=path.stat().st_size,
            facts={"pageDetailDataVersion": 0},
        ),
    )
    with pytest.raises(MIGRATION.PreflightBlocked):
        MIGRATION.execute_migration(
            plan,
            confirmed=True,
            converter=_convert,
            validator=lambda _page_id, _path: True,
        )
    assert not plan.transaction_dir.exists()
    NATIVE_GUARD.recover_pending_native_saves(work)
