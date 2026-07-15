"""Blender本体のネイティブ保存を世代競合から復元するガード。

Blender は ``save_pre`` の例外を無視して保存を続ける。そのため、保存前から
保存後まで作品ロックを保持し、旧セッションなら既存blendを同じディレクトリへ
原子的に退避する。保存後は退避版を戻す。途中でプロセスが落ちても、外部
ジャーナルを次回ロード時に回収できる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any, Iterable, Mapping

try:
    from .project_content_migration_model import MIGRATION_VERSION
    from .project_content_migration_lock import find_work_root, work_lock
    from .project_content_save_baseline import (
        SaveBaselineConflictError,
        SaveBaselineUnavailableError,
        assert_existing_target_tracked,
        conflicting_paths,
        record_successful_write,
    )
    from . import project_content_sidecar_save_guard as _sidecar
    from .project_content_migration_storage import (
        atomic_write_json,
        new_transaction_id,
        read_json_mapping,
        sha256,
        utc_now,
    )
    from .project_content_version import (
        DetailDataVersionError,
        coerce_memory_version,
        read_work_detail_version,
    )
except ImportError:  # ファイル単体でロードする純Pythonテスト用
    from project_content_migration_model import MIGRATION_VERSION  # type: ignore
    from project_content_migration_lock import find_work_root, work_lock  # type: ignore
    from project_content_save_baseline import (  # type: ignore
        SaveBaselineConflictError,
        SaveBaselineUnavailableError,
        assert_existing_target_tracked,
        conflicting_paths,
        record_successful_write,
    )
    import project_content_sidecar_save_guard as _sidecar  # type: ignore
    from project_content_migration_storage import (  # type: ignore
        atomic_write_json,
        new_transaction_id,
        read_json_mapping,
        sha256,
        utc_now,
    )
    from project_content_version import (  # type: ignore
        DetailDataVersionError,
        coerce_memory_version,
        read_work_detail_version,
    )


NATIVE_JOURNAL_VERSION = 1
NATIVE_JOURNAL_NAME = "native-save-journal.json"
_TRANSACTION_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
# ジャーナル未到達 (=実ファイルへの書込み未着手) のディレクトリだけを掃除対象
# にする猶予期間。並行プロセスの進行中トランザクションやDropbox同期遅延を
# 誤って消さないよう安全側に長く取る。
_STALE_TRANSACTION_MAX_AGE = timedelta(hours=24)
_RECOVERY_NAME_RE = re.compile(
    r"^\.(?P<source>.+\.blend)\.(?P<tx>\d{8}T\d{6}Z-[0-9a-f]{12})\.native-recovery$"
)
_CREATED_NAME_RE = re.compile(
    r"^\.(?P<source>.+\.blend)\.(?P<tx>\d{8}T\d{6}Z-[0-9a-f]{12})\.native-created$"
)
_NATIVE_COPYING_NAME_RE = re.compile(r"^\..+\.blend\..+\.native-copying$")


class NativeSaveRecoveryError(RuntimeError):
    pass


@dataclass(slots=True)
class NativeSaveToken:
    source: Path
    work_dir: Path
    lock_context: Any
    requires_restore: bool = False
    original_existed: bool = False
    original_sha256: str = ""
    recovery_path: Path | None = None
    creation_marker: Path | None = None
    journal_path: Path | None = None
    memory_detail_version: int = -1
    disk_detail_version: int = -1
    metadata_saved: bool | None = None
    restore_reason: str = ""
    reload_after_restore: bool = False
    conflict_paths: tuple[str, ...] = ()
    transaction_id: str = ""
    sidecar_token: Any = None
    released: bool = False


@dataclass(frozen=True, slots=True)
class NativeSaveResult:
    restored: bool
    reload_required: bool
    journal_path: Path | None
    metadata_saved: bool
    native_save_succeeded: bool


_active_lock = threading.RLock()
_active_tokens: dict[str, NativeSaveToken] = {}


def _active_key(work: Path) -> str:
    return os.path.normcase(str(work.resolve(strict=False)))


def owns_active_native_save(work_dir: str | os.PathLike[str]) -> bool:
    with _active_lock:
        return _active_key(Path(work_dir)) in _active_tokens


def active_native_save_source(
    work_dir: str | os.PathLike[str],
) -> Path | None:
    with _active_lock:
        token = _active_tokens.get(_active_key(Path(work_dir)))
        return token.source if token is not None else None


def _base(work: Path) -> Path:
    base = work.parent / f".{work.name}.native-save-recovery-v1"
    if base.is_symlink():
        raise NativeSaveRecoveryError("ネイティブ保存復旧先がシンボリックリンクです")
    return base


def _validate_source(source: Path, work: Path) -> None:
    try:
        source.resolve(strict=False).relative_to(work.resolve(strict=True))
    except ValueError as exc:
        raise NativeSaveRecoveryError("保存先が作品フォルダー外です") from exc
    if source.suffix.casefold() != ".blend" or source.is_symlink():
        raise NativeSaveRecoveryError("B-MANGAの通常blendファイルではありません")


def begin_native_save(
    blend_path: str | os.PathLike[str],
    memory_detail_version: Any,
) -> NativeSaveToken | None:
    """save_pre用。blockingロックを取得し、save_postまで保持する。"""
    source = Path(blend_path).resolve(strict=False)
    work = find_work_root(source)
    if work is None or not work.is_dir() or not (work / "work.json").is_file():
        return None
    _validate_source(source, work)
    lock_context = work_lock(work, blocking=True)
    lock_context.__enter__()
    token = NativeSaveToken(source=source, work_dir=work, lock_context=lock_context)
    with _active_lock:
        _active_tokens[_active_key(work)] = token
    try:
        memory = coerce_memory_version(memory_detail_version)
        disk = read_work_detail_version(work)
        token.memory_detail_version = memory
        token.disk_detail_version = disk
        try:
            conflicts = list(conflicting_paths(work, source))
            assert_existing_target_tracked(work, source)
        except SaveBaselineConflictError as exc:
            conflicts.extend(exc.paths)
        except SaveBaselineUnavailableError:
            conflicts = (source,)
        if conflicts:
            token.conflict_paths = tuple(
                dict.fromkeys(str(path) for path in conflicts)
            )
            token.requires_restore = True
            token.reload_after_restore = True
            token.restore_reason = "別のBlender画面で作品データが更新されています"
            _arm_restore(token, error=token.restore_reason)
            return token
        if memory == disk and disk <= MIGRATION_VERSION:
            return token
        token.requires_restore = True
        token.reload_after_restore = True
        token.restore_reason = "詳細データ版が一致しません"
        _arm_restore(token, error=token.restore_reason)
        return token
    except DetailDataVersionError as exc:
        # 不正な版も保存を許さず、既存blendを必ず戻せる状態にする。
        token.requires_restore = True
        token.reload_after_restore = True
        token.restore_reason = str(exc)
        _arm_restore(token, error=token.restore_reason)
        return token
    except BaseException:
        _release(token)
        raise


def _verified_copy_to_recovery(source: Path, recovery: Path, expected_sha: str) -> None:
    """認識対象外tempを検証後だけ最終recovery名へ原子的に昇格する."""

    fd, temp_name = tempfile.mkstemp(
        prefix=f"{recovery.name}.",
        suffix=".native-copying",
        dir=str(source.parent),
    )
    temp_path = Path(temp_name)
    try:
        expected_size = source.stat().st_size
        with source.open("rb") as source_handle, os.fdopen(fd, "wb") as temp_handle:
            fd = -1
            shutil.copyfileobj(source_handle, temp_handle, length=1024 * 1024)
            temp_handle.flush()
            os.fsync(temp_handle.fileno())
        if temp_path.stat().st_size != expected_size or sha256(temp_path) != expected_sha:
            raise NativeSaveRecoveryError("ネイティブ保存の退避コピーに失敗しました")
        try:
            shutil.copystat(source, temp_path)
        except OSError:
            pass
        os.replace(temp_path, recovery)
    finally:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def _arm_restore(token: NativeSaveToken, *, error: str) -> None:
    if token.recovery_path is not None or token.creation_marker is not None:
        return
    tx_id = token.transaction_id or new_transaction_id()
    token.transaction_id = tx_id
    token.original_existed = token.source.is_file()
    if token.original_existed:
        token.original_sha256 = sha256(token.source)
        token.recovery_path = token.source.with_name(
            f".{token.source.name}.{tx_id}.native-recovery"
        )
        try:
            os.replace(token.source, token.recovery_path)
        except OSError:
            # 最終名への直接copyは、途中クラッシュで部分ファイルが正規の
            # recoveryに見える。非認識tempへfsyncし、hash/size検証後だけ昇格。
            _verified_copy_to_recovery(
                token.source,
                token.recovery_path,
                token.original_sha256,
            )
    else:
        token.creation_marker = token.source.with_name(
            f".{token.source.name}.{tx_id}.native-created"
        )
        token.creation_marker.touch(exist_ok=False)
    journal = {
        "journalVersion": NATIVE_JOURNAL_VERSION,
        "transactionId": tx_id,
        "status": "armed",
        "createdAt": utc_now(),
        "workDir": str(token.work_dir),
        "sourcePath": str(token.source),
        "originalExisted": token.original_existed,
        "originalSha256": token.original_sha256,
        "recoveryPath": str(token.recovery_path or ""),
        "creationMarker": str(token.creation_marker or ""),
        "memoryDetailDataVersion": token.memory_detail_version,
        "diskDetailDataVersion": token.disk_detail_version,
        "versionError": error,
        "conflictPaths": list(token.conflict_paths),
        "sidecarJournalPath": str(
            getattr(token.sidecar_token, "journal_path", "") or ""
        ),
    }
    # 退避そのものを先に成立させる。外部ジャーナルが書けなくてもsave_postは
    # tokenから復元でき、異常終了時は同一dirの名前から回収できる。
    tx_dir = None
    try:
        tx_dir = _base(token.work_dir) / tx_id
        tx_dir.mkdir(parents=True, exist_ok=False)
        token.journal_path = tx_dir / NATIVE_JOURNAL_NAME
        journal["status"] = "original_secured"
        atomic_write_json(token.journal_path, journal)
    except Exception:
        token.journal_path = None
        if tx_dir is not None:
            shutil.rmtree(tx_dir, ignore_errors=True)


def prepare_native_save_sidecars(
    token: NativeSaveToken | None,
    paths: Iterable[str | os.PathLike[str]],
) -> None:
    """JSON/PNG全件と元blendを、書込み開始前にrollback可能にする。"""

    if token is None:
        return
    if token.released or token.requires_restore:
        raise NativeSaveRecoveryError("保存トークンへ作品情報を追加できません")
    token.transaction_id = token.transaction_id or new_transaction_id()
    token.sidecar_token = _sidecar.begin_sidecar_save(
        token.work_dir,
        paths,
        transaction_id=token.transaction_id,
    )
    try:
        _arm_restore(token, error="保存完了まで元ファイルを保護します")
        if token.journal_path is None:
            raise NativeSaveRecoveryError("保存復旧記録を作成できませんでした")
        _sidecar.mark_sidecar_writes_started(token.sidecar_token)
    except BaseException:
        token.requires_restore = True
        try:
            _sidecar.restore_sidecars(token.sidecar_token)
        except Exception:
            pass
        raise


def mark_native_save_metadata_result(
    token: NativeSaveToken | None,
    succeeded: bool,
    *,
    error: str = "",
) -> None:
    """save_preの必須sidecar結果を記録し、失敗時は本体保存前に退避する."""

    if token is None:
        return
    if token.released:
        raise NativeSaveRecoveryError("解放済みの保存トークンです")
    token.metadata_saved = succeeded is True
    if succeeded is True:
        return
    sidecar_error = None
    try:
        _sidecar.restore_sidecars(token.sidecar_token)
    except Exception as exc:  # noqa: BLE001
        sidecar_error = exc
    token.requires_restore = True
    token.restore_reason = error or "作品情報の保存に失敗しました"
    _arm_restore(token, error=token.restore_reason)
    if sidecar_error is not None:
        raise NativeSaveRecoveryError("作品情報を保存前へ戻せませんでした") from sidecar_error


def force_native_save_restore(
    token: NativeSaveToken | None,
    *,
    reason: str,
) -> None:
    """今回のネイティブ保存結果を必ず破棄するよう再armする."""

    if token is None:
        return
    if token.released:
        raise NativeSaveRecoveryError("解放済みの保存トークンです")
    token.requires_restore = True
    token.reload_after_restore = True
    token.restore_reason = reason
    _arm_restore(token, error=reason)


def finish_native_save(
    token: NativeSaveToken | None,
    *,
    native_save_succeeded: bool = True,
) -> NativeSaveResult:
    """save_post/save_post_fail用。旧セッションの保存結果を捨てて元を戻す。"""
    if token is None:
        return NativeSaveResult(False, False, None, False, native_save_succeeded)
    restored = False
    try:
        rollback_required = (
            token.requires_restore
            or not native_save_succeeded
            or (token.sidecar_token is not None and token.metadata_saved is not True)
        )
        if rollback_required:
            restored = _rollback_transaction(token)
        elif token.sidecar_token is not None:
            _commit_transaction(token)
            record_successful_write(token.source)
        elif native_save_succeeded:
            record_successful_write(token.source)
        return NativeSaveResult(
            restored,
            restored and token.reload_after_restore,
            token.journal_path,
            token.metadata_saved is True,
            native_save_succeeded,
        )
    finally:
        _release(token)


def _rollback_transaction(token: NativeSaveToken) -> bool:
    restored = False
    first_error = None
    try:
        restored = _sidecar.restore_sidecars(token.sidecar_token) or restored
    except Exception as exc:  # noqa: BLE001
        first_error = exc
    try:
        if token.recovery_path is not None or token.creation_marker is not None:
            restored = _restore_token(token) or restored
    except Exception as exc:  # noqa: BLE001
        if first_error is None:
            first_error = exc
    if first_error is not None:
        raise NativeSaveRecoveryError("保存トランザクションを復元できませんでした") from first_error
    if restored:
        _record_rollback_baselines(token)
    return restored


def _record_rollback_baselines(token: NativeSaveToken) -> None:
    """物理復元後の状態を、同じ画面からの再保存用基準へ戻す."""

    paths_to_record = [token.source]
    sidecar_token = token.sidecar_token
    paths_to_record.extend(
        record.source for record in getattr(sidecar_token, "records", ())
    )
    for path in paths_to_record:
        try:
            record_successful_write(path)
        except Exception:
            # 復元済みファイルを基準記録だけの失敗で未復元扱いにしない。
            pass


def _write_native_status(token: NativeSaveToken, status: str) -> None:
    if token.journal_path is None:
        raise NativeSaveRecoveryError("ネイティブ保存復旧記録がありません")
    journal = read_json_mapping(token.journal_path)
    journal["status"] = status
    journal["updatedAt"] = utc_now()
    atomic_write_json(token.journal_path, journal)


def _commit_transaction(token: NativeSaveToken) -> None:
    if not token.source.is_file():
        raise NativeSaveRecoveryError("ネイティブ保存結果がありません")
    # この1書込みをcommit pointにする。異常終了後は同じtransaction IDの
    # blend/sidecar双方がこの決定を見て新しい組を維持する。
    _write_native_status(token, "commit_decided")
    _sidecar.commit_sidecars(token.sidecar_token)
    if token.recovery_path is not None:
        token.recovery_path.unlink(missing_ok=True)
    if token.creation_marker is not None:
        token.creation_marker.unlink(missing_ok=True)
    _write_native_status(token, "committed")
    if token.journal_path is not None:
        shutil.rmtree(token.journal_path.parent, ignore_errors=True)


def _restore_token(token: NativeSaveToken) -> bool:
    if token.original_existed:
        recovery = token.recovery_path
        if recovery is None or not recovery.is_file():
            if token.source.is_file() and sha256(token.source) == token.original_sha256:
                restored = False
            else:
                raise NativeSaveRecoveryError("ネイティブ保存の復旧ファイルがありません")
        else:
            if sha256(recovery) != token.original_sha256:
                raise NativeSaveRecoveryError("ネイティブ保存の復旧ファイルが破損しています")
            os.replace(recovery, token.source)
            restored = True
    else:
        token.source.unlink(missing_ok=True)
        if token.creation_marker is not None:
            token.creation_marker.unlink(missing_ok=True)
        restored = True
    if token.journal_path is not None:
        try:
            journal = read_json_mapping(token.journal_path)
            journal["status"] = "restored"
            journal["restoredAt"] = utc_now()
            atomic_write_json(token.journal_path, journal)
            shutil.rmtree(token.journal_path.parent, ignore_errors=True)
        except Exception:
            # 本体の物理復元を第一結果とする。残った記録は次回起動時に
            # 同じhashを確認して安全に再処理できる。
            pass
    return restored


def _release(token: NativeSaveToken) -> None:
    if token.released:
        return
    token.released = True
    with _active_lock:
        key = _active_key(token.work_dir)
        if _active_tokens.get(key) is token:
            del _active_tokens[key]
    token.lock_context.__exit__(None, None, None)


def cleanup_stale_transactions(
    work_dir: str | os.PathLike[str],
) -> tuple[Path, ...]:
    """ジャーナル未到達のまま残った古いトランザクションディレクトリを掃除する.

    ``_arm_restore`` は退避成立後にだけジャーナルを書くため、ジャーナルが
    無いディレクトリは実ファイルへの書込みが一切始まっていない (異常終了
    直後の rmtree 取りこぼし等)。誤って進行中トランザクションを消さない
    よう、この関数自体は例外を外へ出さず、個々のエントリで握って続行する。
    """
    try:
        base = _base(Path(work_dir))
    except Exception:  # noqa: BLE001
        return ()
    if not base.is_dir():
        return ()
    removed: list[Path] = []
    now = datetime.now(timezone.utc)
    try:
        entries = list(base.iterdir())
    except OSError:
        return ()
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if not _TRANSACTION_ID_RE.fullmatch(entry.name):
                continue
            stamp = datetime.strptime(
                entry.name.split("-", 1)[0], "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=timezone.utc)
            if now - stamp < _STALE_TRANSACTION_MAX_AGE:
                continue
            if (entry / NATIVE_JOURNAL_NAME).is_file():
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed.append(entry)
        except Exception:  # noqa: BLE001
            continue
    removed.extend(_cleanup_stale_copying_files(Path(work_dir), now))
    return tuple(removed)


def _cleanup_stale_copying_files(work: Path, now: datetime) -> list[Path]:
    """rename失敗後の検証copy中に異常終了した部分ファイルだけを掃除する."""

    removed: list[Path] = []
    try:
        candidates = list(work.rglob("*.native-copying"))
    except OSError:
        return removed
    for candidate in candidates:
        try:
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or not _NATIVE_COPYING_NAME_RE.fullmatch(candidate.name)
            ):
                continue
            age = now - datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
            if age < _STALE_TRANSACTION_MAX_AGE:
                continue
            candidate.unlink()
            if not candidate.exists():
                removed.append(candidate)
        except Exception:  # noqa: BLE001
            continue
    return removed


def recover_pending_native_saves(
    expected_work_dir: str | os.PathLike[str],
) -> tuple[Path, ...]:
    """load時用。異常終了で残った退避版をblockingロック下で戻す。"""
    work = Path(expected_work_dir).resolve(strict=True)
    restored: list[Path] = []
    with work_lock(work, blocking=True):
        # native側のcommit_decided記録が残っている間にsidecarの確定/復元を
        # 決める。先にnative journalを掃除すると確定済みsidecarを誤復元する。
        restored.extend(_sidecar.recover_pending_sidecar_saves(work))
        restored.extend(_restore_orphan_guards(work))
        base = _base(work)
        if base.is_dir():
            for journal_path in sorted(base.glob(f"*/{NATIVE_JOURNAL_NAME}")):
                journal = read_json_mapping(journal_path)
                _validate_journal(journal_path, journal, work)
                status = str(journal.get("status", ""))
                if status in {"restored", "committed"}:
                    shutil.rmtree(journal_path.parent, ignore_errors=True)
                    continue
                token = _token_from_journal(journal_path, journal, work)
                token.lock_context = _NoopContext()
                if status == "commit_decided":
                    if token.recovery_path is not None:
                        token.recovery_path.unlink(missing_ok=True)
                    if token.creation_marker is not None:
                        token.creation_marker.unlink(missing_ok=True)
                    _write_native_status(token, "committed")
                    shutil.rmtree(journal_path.parent, ignore_errors=True)
                    continue
                if _restore_token(token):
                    restored.append(token.source)
        # 復旧処理と同じロック下で、実書込み未着手の古いトランザクション
        # 残骸 (native/sidecar 双方) も一掃する。
        cleanup_stale_transactions(work)
        _sidecar.cleanup_stale_transactions(work)
    return tuple(restored)


def find_pending_native_save_journals(
    expected_work_dir: str | os.PathLike[str],
) -> tuple[Path, ...]:
    """書込み前ゲート用。壊れた記録も復旧待ちとして返す。"""
    work = Path(expected_work_dir).resolve(strict=True)
    orphans = tuple(_orphan_guards(work))
    base = _base(work)
    pending: list[Path] = list(orphans)
    if base.is_dir():
        for path in sorted(base.glob(f"*/{NATIVE_JOURNAL_NAME}")):
            try:
                status = str(read_json_mapping(path).get("status", ""))
            except Exception:
                pending.append(path)
                continue
            if status not in {"restored", "committed"}:
                pending.append(path)
    pending.extend(_sidecar.find_pending_sidecar_saves(work))
    return tuple(pending)


def _orphan_guards(work: Path):
    for path in work.rglob(".*.native-recovery"):
        if _RECOVERY_NAME_RE.fullmatch(path.name):
            yield path
    for path in work.rglob(".*.native-created"):
        if _CREATED_NAME_RE.fullmatch(path.name):
            yield path


def _restore_orphan_guards(work: Path) -> list[Path]:
    restored: list[Path] = []
    for guard in tuple(_orphan_guards(work)):
        match = _RECOVERY_NAME_RE.fullmatch(guard.name)
        created = False
        if match is None:
            match = _CREATED_NAME_RE.fullmatch(guard.name)
            created = True
        if match is None or guard.is_symlink():
            continue
        source = guard.with_name(match.group("source"))
        _validate_source(source, work)
        status = _native_transaction_status(work, match.group("tx"))
        if status in {"commit_decided", "committed"}:
            guard.unlink(missing_ok=True)
            continue
        if created:
            source.unlink(missing_ok=True)
            guard.unlink(missing_ok=True)
        else:
            os.replace(guard, source)
        restored.append(source)
    return restored


def _native_transaction_status(work: Path, tx_id: str) -> str:
    journal = _base(work) / tx_id / NATIVE_JOURNAL_NAME
    try:
        return str(read_json_mapping(journal).get("status", ""))
    except Exception:
        return ""


def _validate_journal(path: Path, journal: Mapping[str, Any], work: Path) -> None:
    version = journal.get("journalVersion")
    if type(version) is not int or version != NATIVE_JOURNAL_VERSION:
        raise NativeSaveRecoveryError("未対応のネイティブ保存復旧版です")
    tx_id = str(journal.get("transactionId", ""))
    if not _TRANSACTION_ID_RE.fullmatch(tx_id):
        raise NativeSaveRecoveryError("ネイティブ保存復旧IDが不正です")
    if path.parent.resolve(strict=False) != (_base(work) / tx_id).resolve(strict=False):
        raise NativeSaveRecoveryError("ネイティブ保存復旧の配置が不正です")
    if Path(str(journal.get("workDir", ""))).resolve(strict=True) != work:
        raise NativeSaveRecoveryError("ネイティブ保存復旧が別作品を指しています")
    source = Path(str(journal.get("sourcePath", ""))).resolve(strict=False)
    _validate_source(source, work)
    original_existed = journal.get("originalExisted")
    if type(original_existed) is not bool:
        raise NativeSaveRecoveryError("ネイティブ保存復旧の元ファイル情報が不正です")
    recovery_text = str(journal.get("recoveryPath", ""))
    creation_text = str(journal.get("creationMarker", ""))
    if original_existed:
        expected = source.with_name(f".{source.name}.{tx_id}.native-recovery")
        if Path(recovery_text).resolve(strict=False) != expected:
            raise NativeSaveRecoveryError("ネイティブ保存復旧パスが不正です")
        digest = str(journal.get("originalSha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise NativeSaveRecoveryError("ネイティブ保存復旧ハッシュが不正です")
    elif recovery_text or journal.get("originalSha256"):
        raise NativeSaveRecoveryError("存在しない元ファイルの復旧情報が不正です")
    if not original_existed:
        expected_marker = source.with_name(
            f".{source.name}.{tx_id}.native-created"
        )
        if Path(creation_text).resolve(strict=False) != expected_marker:
            raise NativeSaveRecoveryError("新規ネイティブ保存の復旧パスが不正です")
    elif creation_text:
        raise NativeSaveRecoveryError("既存ファイルに新規保存用復旧情報があります")


def _token_from_journal(
    path: Path,
    journal: Mapping[str, Any],
    work: Path,
) -> NativeSaveToken:
    recovery_text = str(journal.get("recoveryPath", ""))
    creation_text = str(journal.get("creationMarker", ""))
    return NativeSaveToken(
        source=Path(str(journal["sourcePath"])).resolve(strict=False),
        work_dir=work,
        lock_context=None,
        requires_restore=True,
        original_existed=bool(journal.get("originalExisted", False)),
        original_sha256=str(journal.get("originalSha256", "")),
        recovery_path=Path(recovery_text).resolve(strict=False) if recovery_text else None,
        creation_marker=(
            Path(creation_text).resolve(strict=False) if creation_text else None
        ),
        journal_path=path,
        transaction_id=str(journal.get("transactionId", "")),
    )


class _NoopContext:
    def __exit__(self, *_args) -> None:
        return None


__all__ = [
    "NativeSaveRecoveryError",
    "NativeSaveResult",
    "NativeSaveToken",
    "active_native_save_source",
    "begin_native_save",
    "cleanup_stale_transactions",
    "find_pending_native_save_journals",
    "finish_native_save",
    "force_native_save_restore",
    "mark_native_save_metadata_result",
    "owns_active_native_save",
    "prepare_native_save_sidecars",
    "recover_pending_native_saves",
]
