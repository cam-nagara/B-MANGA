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
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


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
    base = work.parent / f".{work.name}.native-save-recovery-v1"
    base.mkdir()

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

    old_empty = _tx_id(25.0, "dddddddddddd")
    _make_tx_dir(base, old_empty)

    removed = NATIVE_GUARD.cleanup_stale_transactions(work)

    removed_names = {path.name for path in removed}
    assert removed_names == {old_no_journal, old_empty}
    assert not (base / old_no_journal).exists()
    assert not (base / old_empty).exists()
    assert (base / recent_no_journal).is_dir()
    assert (base / old_with_journal).is_dir()
    assert invalid_name_dir.is_dir()


# --- (c) sidecar側 cleanup_stale_transactions --------------------------


def test_sidecar_cleanup_stale_transactions_removes_only_old_journal_less_dirs(tmp_path):
    """sidecar側も同じ基準 (ジャーナル未到達 + 24時間超) だけを掃除すること。"""

    work = tmp_path / "SidecarCleanup.bmanga"
    work.mkdir()
    base = work.parent / f".{work.name}.sidecar-save-recovery-v1"
    base.mkdir()

    old_no_journal = _tx_id(25.0, "eeeeeeeeeeee")
    old_dir = _make_tx_dir(base, old_no_journal)
    (old_dir / "backup").mkdir()
    (old_dir / "backup" / "0000.bin").write_bytes(b"dummy-backup")

    old_with_journal = _tx_id(25.0, "ffffffffffff")
    old_journal_dir = _make_tx_dir(base, old_with_journal)
    (old_journal_dir / SIDECAR_GUARD.SIDECAR_JOURNAL_NAME).write_text("{}", encoding="utf-8")

    removed = SIDECAR_GUARD.cleanup_stale_transactions(work)

    removed_names = {path.name for path in removed}
    assert removed_names == {old_no_journal}
    assert not (base / old_no_journal).exists()
    assert (base / old_with_journal).is_dir()


# --- (d) recover_pending_native_saves 経由の掃除 ------------------------


def test_recover_pending_native_saves_also_cleans_up_both_stale_bases(tmp_path):
    """recover_pending_native_savesの末尾でnative/sidecar両方の期限切れ
    トランザクション残骸が実際に掃除されること。"""

    work = _work(tmp_path, "RecoverCleanup")
    native_base = work.parent / f".{work.name}.native-save-recovery-v1"
    sidecar_base = work.parent / f".{work.name}.sidecar-save-recovery-v1"
    native_base.mkdir()
    sidecar_base.mkdir()

    native_old = _tx_id(25.0, "111111111111")
    _make_tx_dir(native_base, native_old)
    sidecar_old = _tx_id(25.0, "222222222222")
    _make_tx_dir(sidecar_base, sidecar_old)

    NATIVE_GUARD.recover_pending_native_saves(work)

    assert not (native_base / native_old).exists()
    assert not (sidecar_base / sidecar_old).exists()
