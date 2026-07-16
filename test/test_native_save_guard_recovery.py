"""保存復旧の再読込フォールバックが依存する既存挙動を純Pythonで固定する。

対象:

* 新規ページ初回保存が中断された場合の復旧が ``page.blend`` を削除し、
  戻り値へ含めること (``utils/handlers.py`` の再読込フォールバックが
  「対象が消えている」ケースへ正しく分岐できる前提)。
* native / sidecar 両保存ガードの ``cleanup_stale_transactions`` が、
  ジャーナル未到達のまま24時間以上放置されたトランザクションだけを
  掃除し、若い/ジャーナル有り/名前不一致のものは残すこと。
* ``recover_pending_native_saves`` の末尾から両方の掃除が実際に呼ばれる
  こと。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_migration():
    path = ROOT / "io" / "project_content_migration.py"
    name = "_bmanga_native_save_guard_recovery_migration_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# project_content_migration.py を先にロードすることで、native/sidecarガード
# が単体ロード時に使う裸importの依存モジュール群 (project_content_migration_
# lock 等) が sys.modules へ登録される。test_project_content_migration_safety.
# py と同じ順序を踏襲する。
_MIGRATION = _load_migration()


def _load_native_guard():
    path = ROOT / "io" / "project_content_native_save_guard.py"
    name = "_bmanga_native_save_guard_recovery_native_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NATIVE_GUARD = _load_native_guard()
SIDECAR_GUARD = sys.modules["project_content_sidecar_save_guard"]
BASELINE = sys.modules["project_content_save_baseline"]


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _work(tmp_path: Path, name: str = "Recovery") -> Path:
    work = tmp_path / f"{name}.bmanga"
    work.mkdir()
    _write_json(work / "work.json", {"detailDataVersion": 0})
    return work


def _tx_id(hours_ago: float, suffix: str) -> str:
    """``_TRANSACTION_ID_RE`` に一致するトランザクションID文字列を作る。"""

    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    return f"{stamp}-{suffix}"


def _make_tx_dir(base: Path, tx_id: str) -> Path:
    tx_dir = base / tx_id
    tx_dir.mkdir(parents=True)
    return tx_dir


def _move_sidecar_transaction(token, destination_base: Path) -> Path:
    current_tx = token.transaction_dir
    destination_base.mkdir(parents=True)
    destination_tx = destination_base / current_tx.name
    shutil.move(str(current_tx), str(destination_tx))
    journal_path = destination_tx / SIDECAR_GUARD.SIDECAR_JOURNAL_NAME
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    for record in journal["records"]:
        backup = str(record.get("backupPath", ""))
        if backup:
            relative = Path(backup).relative_to(current_tx)
            record["backupPath"] = str(destination_tx / relative)
    _write_json(journal_path, journal)
    return destination_tx


# --- (a) 新規ページ初回保存の中断復旧 ---------------------------------


def test_interrupted_new_page_first_save_rollback_deletes_created_file(tmp_path):
    """新規ページ初回保存中の異常終了は、復旧時にpage.blendを削除して
    戻り値へ含める。再読込フォールバックが依存する既存挙動の固定化。"""

    work = _work(tmp_path, "NewPageRollback")
    page = work / "p0001" / "page.blend"
    page.parent.mkdir()
    assert not page.exists()

    # 読込基準が無い作品への保存は、既存ファイルの有無に関わらず
    # 「別画面が更新した」扱いで即座にrequires_restoreが立つ
    # (SaveBaselineUnavailableError経由)。既存テストと同じ前提。
    token = NATIVE_GUARD.begin_native_save(page, 0)
    assert token is not None and token.requires_restore
    assert not token.original_existed
    assert token.creation_marker is not None and token.creation_marker.is_file()
    assert token.journal_path is not None
    journal = NATIVE_GUARD.read_json_mapping(token.journal_path)
    assert journal["status"] == "original_secured"
    assert journal["originalExisted"] is False
    assert journal["transactionId"] == token.transaction_id

    # Blenderが新規ファイルとして書込みを完了させたことにする。
    page.write_bytes(b"new-page-content")
    # プロセスがここでクラッシュした想定 (finish_native_saveへ届かない)。
    NATIVE_GUARD._release(token)

    restored = NATIVE_GUARD.recover_pending_native_saves(work)

    assert page in restored
    assert not page.exists()
    assert not token.creation_marker.exists()


# --- (b) native側 cleanup_stale_transactions --------------------------


def test_native_cleanup_stale_transactions_removes_only_old_journal_less_dirs(tmp_path):
    """ジャーナル未到達のまま24時間以上放置されたトランザクションだけを
    掃除し、若い/ジャーナル有り/名前不一致/空ディレクトリの扱いを確認する。"""

    work = tmp_path / "NativeCleanup.bmanga"
    work.mkdir()
    base = NATIVE_GUARD._base(work)
    base.mkdir(parents=True)

    old_no_journal = _tx_id(25.0, "aaaaaaaaaaaa")
    old_dir = _make_tx_dir(base, old_no_journal)
    (old_dir / "backup").mkdir()
    (old_dir / "backup" / "0000.bin").write_bytes(b"dummy-backup")
    (old_dir / ".page.blend.9f2b8e3f1a5c.native-copying").write_bytes(b"copying-residue")

    recent_no_journal = _tx_id(1.0, "bbbbbbbbbbbb")
    _make_tx_dir(base, recent_no_journal)

    old_with_journal = _tx_id(25.0, "cccccccccccc")
    old_journal_dir = _make_tx_dir(base, old_with_journal)
    (old_journal_dir / NATIVE_GUARD.NATIVE_JOURNAL_NAME).write_text("{}", encoding="utf-8")

    invalid_name_dir = base / "not-a-transaction"
    invalid_name_dir.mkdir()

    finished = _tx_id(1.0, "abababababab")
    finished_dir = _make_tx_dir(base, finished)
    _write_json(
        finished_dir / NATIVE_GUARD.NATIVE_JOURNAL_NAME,
        {"status": "committed"},
    )

    old_empty = _tx_id(25.0, "dddddddddddd")
    _make_tx_dir(base, old_empty)

    removed = NATIVE_GUARD.cleanup_stale_transactions(work)

    removed_names = {path.name for path in removed}
    assert removed_names == {old_no_journal, old_empty, finished}
    assert not (base / old_no_journal).exists()
    assert not (base / old_empty).exists()
    assert (base / recent_no_journal).is_dir()
    assert (base / old_with_journal).is_dir()
    assert invalid_name_dir.is_dir()
    assert not (base / finished).exists()


def test_native_cleanup_removes_only_old_copying_files_from_actual_source_tree(tmp_path):
    work = _work(tmp_path, "CopyingCleanup")
    source_dir = work / "p0001"
    source_dir.mkdir()
    old_copy = source_dir / ".page.blend.random.native-copying"
    recent_copy = source_dir / ".page.blend.recent.native-copying"
    invalid = source_dir / "page.blend.random.native-copying"
    for path in (old_copy, recent_copy, invalid):
        path.write_bytes(b"partial")
    old_stamp = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
    os.utime(old_copy, (old_stamp, old_stamp))
    os.utime(invalid, (old_stamp, old_stamp))

    removed = NATIVE_GUARD.cleanup_stale_transactions(work)

    assert old_copy in removed
    assert not old_copy.exists()
    assert recent_copy.is_file()
    assert invalid.is_file()


# --- (c) sidecar側 cleanup_stale_transactions --------------------------


def test_sidecar_cleanup_stale_transactions_removes_only_old_journal_less_dirs(tmp_path):
    """sidecar側も同じ基準 (ジャーナル未到達 + 24時間超) だけを掃除すること。"""

    work = tmp_path / "SidecarCleanup.bmanga"
    work.mkdir()
    base = SIDECAR_GUARD._base(work)
    base.mkdir(parents=True)

    old_no_journal = _tx_id(25.0, "eeeeeeeeeeee")
    old_dir = _make_tx_dir(base, old_no_journal)
    (old_dir / "backup").mkdir()
    (old_dir / "backup" / "0000.bin").write_bytes(b"dummy-backup")

    old_with_journal = _tx_id(25.0, "ffffffffffff")
    old_journal_dir = _make_tx_dir(base, old_with_journal)
    (old_journal_dir / SIDECAR_GUARD.SIDECAR_JOURNAL_NAME).write_text("{}", encoding="utf-8")

    finished = _tx_id(1.0, "cdcdcdcdcdcd")
    finished_dir = _make_tx_dir(base, finished)
    _write_json(
        finished_dir / SIDECAR_GUARD.SIDECAR_JOURNAL_NAME,
        {"status": "restored"},
    )

    removed = SIDECAR_GUARD.cleanup_stale_transactions(work)

    removed_names = {path.name for path in removed}
    assert removed_names == {old_no_journal, finished}
    assert not (base / old_no_journal).exists()
    assert (base / old_with_journal).is_dir()
    assert not (base / finished).exists()


# --- (d) recover_pending_native_saves 経由の掃除 ------------------------


def test_recover_pending_native_saves_also_cleans_up_both_stale_bases(tmp_path):
    """recover_pending_native_savesの末尾でnative/sidecar両方の期限切れ
    トランザクション残骸が実際に掃除されること。"""

    work = _work(tmp_path, "RecoverCleanup")
    native_base = NATIVE_GUARD._base(work)
    sidecar_base = SIDECAR_GUARD._base(work)
    native_base.mkdir(parents=True)
    sidecar_base.mkdir(parents=True)

    native_old = _tx_id(25.0, "111111111111")
    _make_tx_dir(native_base, native_old)
    sidecar_old = _tx_id(25.0, "222222222222")
    _make_tx_dir(sidecar_base, sidecar_old)

    NATIVE_GUARD.recover_pending_native_saves(work)

    assert not (native_base / native_old).exists()
    assert not (sidecar_base / sidecar_old).exists()
    assert not (work / ".bmanga-save-recovery-v1").exists()


def test_successful_save_uses_work_internal_recovery_and_prunes_it(tmp_path):
    work = _work(tmp_path, "InternalRecovery")
    source = work / "work.blend"
    source.write_bytes(b"old-blend")
    BASELINE.capture_loaded_baseline(work, source)

    token = NATIVE_GUARD.begin_native_save(source, 0)
    assert token is not None and not token.requires_restore
    NATIVE_GUARD.prepare_native_save_sidecars(token, (work / "work.json",))
    root = work / ".bmanga-save-recovery-v1"
    assert token.journal_path is not None
    token.journal_path.relative_to(root)
    token.sidecar_token.journal_path.relative_to(root)
    assert not NATIVE_GUARD._recovery_paths.legacy_native_base(work).exists()
    assert not NATIVE_GUARD._recovery_paths.legacy_sidecar_base(work).exists()

    NATIVE_GUARD.mark_native_save_metadata_result(token, True)
    source.write_bytes(b"new-blend")
    result = NATIVE_GUARD.finish_native_save(token)

    assert not result.restored and result.metadata_saved
    assert source.read_bytes() == b"new-blend"
    assert not root.exists()


@pytest.mark.parametrize("linked_part", ["transaction", "journal"])
def test_native_recovery_rejects_linked_journal_hierarchy(
    monkeypatch,
    tmp_path,
    linked_part,
):
    work = _work(tmp_path, "LinkedNativeRecovery")
    source = work / "work.blend"
    source.write_bytes(b"latest-work")
    token = NATIVE_GUARD.begin_native_save(source, 0)
    assert token is not None and token.journal_path is not None
    journal = NATIVE_GUARD.read_json_mapping(token.journal_path)
    linked_path = (
        token.journal_path.parent
        if linked_part == "transaction"
        else token.journal_path
    )
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked_path or original(path),
    )

    with pytest.raises(NATIVE_GUARD.NativeSaveRecoveryError, match="配置が不正"):
        NATIVE_GUARD._validate_journal(token.journal_path, journal, work)

    NATIVE_GUARD.finish_native_save(token, native_save_succeeded=False)


@pytest.mark.parametrize("linked_part", ["transaction", "journal"])
def test_sidecar_recovery_rejects_linked_journal_hierarchy(
    monkeypatch,
    tmp_path,
    linked_part,
):
    work = _work(tmp_path, "LinkedSidecarRecovery")
    source = work / "pages.json"
    source.write_bytes(b"latest-pages")
    token = SIDECAR_GUARD.begin_sidecar_save(work, (source,))
    journal = dict(SIDECAR_GUARD.read_json_mapping(token.journal_path))
    linked_path = (
        token.transaction_dir
        if linked_part == "transaction"
        else token.journal_path
    )
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked_path or original(path),
    )

    with pytest.raises(SIDECAR_GUARD.SidecarSaveError, match="配置が不正"):
        SIDECAR_GUARD._token_from_journal(token.journal_path, journal, work)

    SIDECAR_GUARD.restore_sidecars(token)


def test_legacy_external_native_journal_is_recovered_and_pruned(tmp_path):
    work = _work(tmp_path, "LegacyRecovery")
    source = work / "work.blend"
    source.write_bytes(b"latest-work")
    token = NATIVE_GUARD.begin_native_save(source, 0)
    assert token is not None and token.journal_path is not None
    source.write_bytes(b"stale-save")
    NATIVE_GUARD._release(token)

    current_tx = token.journal_path.parent
    legacy_base = NATIVE_GUARD._recovery_paths.legacy_native_base(work)
    legacy_base.mkdir()
    shutil.move(str(current_tx), str(legacy_base / current_tx.name))

    restored = NATIVE_GUARD.recover_pending_native_saves(work)

    assert restored == (source,)
    assert source.read_bytes() == b"latest-work"
    assert not legacy_base.exists()
    assert not (work / ".bmanga-save-recovery-v1").exists()


def test_legacy_external_sidecar_journal_is_recovered_and_pruned(tmp_path):
    work = _work(tmp_path, "LegacySidecarRecovery")
    source = work / "pages.json"
    source.write_bytes(b"latest-pages")
    token = SIDECAR_GUARD.begin_sidecar_save(work, (source,))
    SIDECAR_GUARD.mark_sidecar_writes_started(token)
    source.write_bytes(b"stale-pages")

    legacy_base = NATIVE_GUARD._recovery_paths.legacy_sidecar_base(work)
    _move_sidecar_transaction(token, legacy_base)

    restored = NATIVE_GUARD.recover_pending_native_saves(work)

    assert restored == (source,)
    assert source.read_bytes() == b"latest-pages"
    assert not legacy_base.exists()
    assert not (work / ".bmanga-save-recovery-v1").exists()


@pytest.mark.parametrize("legacy_part", ["native", "sidecar"])
def test_commit_decision_survives_mixed_current_and_legacy_layouts(
    tmp_path,
    legacy_part,
):
    work = _work(tmp_path, f"MixedCommit{legacy_part.title()}")
    blend = work / "work.blend"
    sidecar = work / "pages.json"
    blend.write_bytes(b"old-blend")
    sidecar.write_bytes(b"old-pages")
    BASELINE.capture_loaded_baseline(work, blend, content_paths=(sidecar,))

    token = NATIVE_GUARD.begin_native_save(blend, 0)
    assert token is not None
    NATIVE_GUARD.prepare_native_save_sidecars(token, (sidecar,))
    assert token.journal_path is not None
    NATIVE_GUARD.mark_native_save_metadata_result(token, True)
    blend.write_bytes(b"new-blend")
    sidecar.write_bytes(b"new-pages")
    NATIVE_GUARD._write_native_status(token, "commit_decided")

    if legacy_part == "native":
        current_tx = token.journal_path.parent
        legacy_base = NATIVE_GUARD._recovery_paths.legacy_native_base(work)
        legacy_base.mkdir()
        shutil.move(str(current_tx), str(legacy_base / current_tx.name))
    else:
        legacy_base = NATIVE_GUARD._recovery_paths.legacy_sidecar_base(work)
        _move_sidecar_transaction(token.sidecar_token, legacy_base)
    NATIVE_GUARD._release(token)

    restored = NATIVE_GUARD.recover_pending_native_saves(work)

    assert restored == ()
    assert blend.read_bytes() == b"new-blend"
    assert sidecar.read_bytes() == b"new-pages"
    assert not (work / ".bmanga-save-recovery-v1").exists()
    assert not legacy_base.exists()


def test_cleanup_prunes_old_external_empty_and_journal_less_bases(tmp_path):
    work = _work(tmp_path, "LegacyCleanup")
    native_base = NATIVE_GUARD._recovery_paths.legacy_native_base(work)
    sidecar_base = NATIVE_GUARD._recovery_paths.legacy_sidecar_base(work)
    native_base.mkdir()
    sidecar_base.mkdir()
    _make_tx_dir(native_base, _tx_id(25.0, "333333333333"))
    _make_tx_dir(sidecar_base, _tx_id(25.0, "444444444444"))

    NATIVE_GUARD.cleanup_stale_transactions(work)
    SIDECAR_GUARD.cleanup_stale_transactions(work)

    assert not native_base.exists()
    assert not sidecar_base.exists()


def test_interrupted_existing_work_blend_can_be_restored_before_open(tmp_path):
    work = _work(tmp_path, "MissingWorkBlend")
    source = work / "work.blend"
    source.write_bytes(b"latest-work")
    token = NATIVE_GUARD.begin_native_save(source, 0)
    assert token is not None and token.requires_restore
    assert not source.exists()
    NATIVE_GUARD._release(token)

    restored = NATIVE_GUARD.recover_pending_native_saves(work)

    assert source in restored
    assert source.read_bytes() == b"latest-work"


def test_native_save_io_failure_restores_disk_without_reloading_memory(tmp_path):
    work = _work(tmp_path, "LocalSaveFailure")
    source = work / "work.blend"
    source.write_bytes(b"old-blend")
    BASELINE.capture_loaded_baseline(work, source)
    token = NATIVE_GUARD.begin_native_save(source, 0)
    assert token is not None and not token.requires_restore
    NATIVE_GUARD.prepare_native_save_sidecars(token, (work / "work.json",))
    source.write_bytes(b"partial-new-blend")
    (work / "work.json").write_text('{"detailDataVersion": 1}', encoding="utf-8")

    result = NATIVE_GUARD.finish_native_save(token, native_save_succeeded=False)

    assert result.restored is True
    assert result.reload_required is False
    assert source.read_bytes() == b"old-blend"
    retry = NATIVE_GUARD.begin_native_save(source, 0)
    assert retry is not None and not retry.requires_restore
    NATIVE_GUARD.finish_native_save(retry, native_save_succeeded=False)


def test_external_conflict_restore_still_requires_reload_when_status_write_fails(tmp_path, monkeypatch):
    work = _work(tmp_path, "ExternalConflict")
    source = work / "work.blend"
    source.write_bytes(b"loaded")
    BASELINE.capture_loaded_baseline(work, source)
    source.write_bytes(b"newest-external")
    token = NATIVE_GUARD.begin_native_save(source, 0)
    assert token is not None and token.reload_after_restore
    source.write_bytes(b"stale-memory-save")
    monkeypatch.setattr(
        NATIVE_GUARD,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("journal locked")),
    )

    result = NATIVE_GUARD.finish_native_save(token, native_save_succeeded=True)

    assert result.restored is True
    assert result.reload_required is True
    assert source.read_bytes() == b"newest-external"


def test_sidecar_restore_prioritizes_physical_files_when_status_write_fails(tmp_path, monkeypatch):
    work = _work(tmp_path, "SidecarStatusFailure")
    source = work / "page.json"
    source.write_bytes(b"old-sidecar")
    token = SIDECAR_GUARD.begin_sidecar_save(work, (source,))
    SIDECAR_GUARD.mark_sidecar_writes_started(token)
    source.write_bytes(b"new-sidecar")
    monkeypatch.setattr(
        SIDECAR_GUARD,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("journal locked")),
    )

    assert SIDECAR_GUARD.restore_sidecars(token) is True
    assert source.read_bytes() == b"old-sidecar"
