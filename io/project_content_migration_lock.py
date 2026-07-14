"""作品単位の協調排他ロック。

移行処理と通常保存が同じ作品を同時に書き換えないための小さな基盤。
Windows では ``msvcrt.locking``、それ以外では ``flock`` を使う。OS が
ファイルハンドルを閉じるため、プロセスが異常終了してもロック自体は残らない。
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
from typing import BinaryIO, Iterator


LOCK_FILE_SUFFIX = ".detail-data-migration.lock"


class WorkLockError(RuntimeError):
    """別プロセスが作品を書込み中のため操作を拒否した。"""


_registry_lock = threading.RLock()
_registry_changed = threading.Condition(_registry_lock)
_owned: dict[str, tuple[BinaryIO, int, int]] = {}
_pending_write_local = threading.local()


def _key(work_dir: Path) -> str:
    return os.path.normcase(str(work_dir.resolve()))


def _lock_handle(handle: BinaryIO, *, blocking: bool) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        if not blocking:
            msvcrt.locking(handle.fileno(), mode, 1)
            return
        while True:
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
                return
            except OSError:
                # LK_LOCK の有限リトライ後も相手が処理中なら待ち続ける。
                time.sleep(0.1)
    else:  # pragma: no cover - Windows が本番環境
        import fcntl

        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(handle.fileno(), flags)


def _unlock_handle(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - Windows が本番環境
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _open_and_lock(work: Path, *, blocking: bool) -> BinaryIO:
    if not work.is_dir():
        raise WorkLockError(f"作品フォルダーがありません: {work}")
    lock_path = lock_file_path(work)
    if lock_path.is_symlink():
        raise WorkLockError("作品ロックファイルがシンボリックリンクです")
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        _lock_handle(handle, blocking=blocking)
        return handle
    except BaseException:
        handle.close()
        raise


@contextmanager
def work_lock(
    work_dir: str | os.PathLike[str],
    *,
    blocking: bool = False,
) -> Iterator[Path]:
    """作品の排他ロックを取得する。同一プロセス内では再入可能。"""
    work = Path(work_dir).resolve(strict=True)
    key = _key(work)
    thread_id = threading.get_ident()
    with _registry_changed:
        entry = _owned.get(key)
        while entry is not None and entry[2] != thread_id:
            if not blocking:
                raise WorkLockError("この作品は別の保存処理が書込み中です")
            _registry_changed.wait()
            entry = _owned.get(key)
        if entry is not None:
            _owned[key] = (entry[0], entry[1] + 1, thread_id)
        else:
            try:
                handle = _open_and_lock(work, blocking=blocking)
            except (OSError, BlockingIOError) as exc:
                raise WorkLockError(
                    "この作品は別のB-MANGA処理が保存中です。完了後に再実行してください"
                ) from exc
            _owned[key] = (handle, 1, thread_id)
    try:
        yield work
    finally:
        with _registry_changed:
            handle, count, owner_thread = _owned[key]
            if count > 1:
                _owned[key] = (handle, count - 1, owner_thread)
            else:
                del _owned[key]
                try:
                    _unlock_handle(handle)
                finally:
                    handle.close()
                    _registry_changed.notify_all()


def find_work_root(path: str | os.PathLike[str]) -> Path | None:
    """パス自身または祖先にある ``*.bmanga`` 作品フォルダーを返す。"""
    current = Path(path).resolve(strict=False)
    if current.exists() and current.is_file():
        current = current.parent
    elif not current.exists() and current.suffix:
        current = current.parent
    for candidate in (current, *current.parents):
        if candidate.name.casefold().endswith(".bmanga"):
            return candidate
    return None


@contextmanager
def allow_owned_recovery_journal(path: str | os.PathLike[str]) -> Iterator[Path]:
    """現在の処理が作成した復旧記録だけを、同じスレッドの書込みに許可する。"""
    journal = Path(path).resolve(strict=False)
    key = os.path.normcase(str(journal))
    allowed = getattr(_pending_write_local, "journals", None)
    if allowed is None:
        allowed = {}
        _pending_write_local.journals = allowed
    allowed[key] = int(allowed.get(key, 0)) + 1
    try:
        yield journal
    finally:
        count = int(allowed.get(key, 0))
        if count <= 1:
            allowed.pop(key, None)
        else:
            allowed[key] = count - 1


def _owns_pending_journals(paths: tuple[Path, ...]) -> bool:
    allowed = getattr(_pending_write_local, "journals", {})
    return bool(paths) and all(
        os.path.normcase(str(path.resolve(strict=False))) in allowed
        for path in paths
    )


@contextmanager
def guard_path_write(path: str | os.PathLike[str]) -> Iterator[Path | None]:
    """作品内書込みの全期間、移行と共通の排他ロックを保持する。"""
    work = find_work_root(path)
    if work is None or not work.is_dir():
        yield work
        return
    with work_lock(work):
        try:
            from .project_content_native_save_guard import (
                active_native_save_source,
                find_pending_native_save_journals,
                owns_active_native_save,
            )
        except ImportError:  # ファイル単体でロードする純Pythonテスト用
            from project_content_native_save_guard import (  # type: ignore
                active_native_save_source,
                find_pending_native_save_journals,
                owns_active_native_save,
            )
        pending = find_pending_native_save_journals(work)
        if pending and not owns_active_native_save(work) and not _owns_pending_journals(pending):
            raise WorkLockError(
                "前回の保存復旧が残っています。作品を開き直してから保存してください"
            )
        try:
            from .project_content_save_baseline import (
                SaveBaselineConflictError,
                SaveBaselineUnavailableError,
                assert_no_external_changes,
                assert_existing_target_tracked,
                initialize_new_work_baseline,
            )
        except ImportError:  # ファイル単体でロードする純Pythonテスト用
            from project_content_save_baseline import (  # type: ignore
                SaveBaselineConflictError,
                SaveBaselineUnavailableError,
                assert_no_external_changes,
                assert_existing_target_tracked,
                initialize_new_work_baseline,
            )
        try:
            active_source = active_native_save_source(work)
            if active_source is None:
                assert_no_external_changes(work)
        except SaveBaselineUnavailableError as exc:
            if (work / "work.json").exists():
                raise WorkLockError(
                    "作品の読込基準がありません。作品を開き直してから保存してください"
                ) from exc
            initialize_new_work_baseline(work)
        except SaveBaselineConflictError as exc:
            raise WorkLockError(str(exc)) from exc
        try:
            assert_existing_target_tracked(work, path)
        except (SaveBaselineConflictError, SaveBaselineUnavailableError) as exc:
            raise WorkLockError(str(exc)) from exc
        yield work


def lock_file_path(work_dir: str | os.PathLike[str]) -> Path:
    work = Path(work_dir).resolve(strict=False)
    # 作品全体のハッシュ・バックアップ・ワーカー所有権検査へロック残骸を
    # 混ぜない。作品と同じボリュームの親ディレクトリに置けば、プロセスが
    # 異常終了しても OS ロックは解放され、作品内容のバイト列は不変となる。
    return work.parent / f".{work.name}{LOCK_FILE_SUFFIX}"


def owns_work_lock(work_dir: str | os.PathLike[str]) -> bool:
    work = Path(work_dir).resolve(strict=False)
    with _registry_lock:
        return _key(work) in _owned


__all__ = [
    "LOCK_FILE_SUFFIX",
    "WorkLockError",
    "allow_owned_recovery_journal",
    "find_work_root",
    "guard_path_write",
    "lock_file_path",
    "owns_work_lock",
    "work_lock",
]
