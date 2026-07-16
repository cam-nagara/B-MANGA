"""ネイティブ保存に伴うJSON/PNG sidecarを全件commit/rollbackする。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Iterable

try:
    from . import project_content_save_recovery_paths as _recovery_paths
    from .project_content_migration_storage import (
        atomic_write_json,
        new_transaction_id,
        read_json_mapping,
        sha256,
        utc_now,
    )
except ImportError:  # ファイル単体でロードする純Pythonテスト用
    import project_content_save_recovery_paths as _recovery_paths  # type: ignore
    from project_content_migration_storage import (  # type: ignore
        atomic_write_json,
        new_transaction_id,
        read_json_mapping,
        sha256,
        utc_now,
    )


SIDECAR_JOURNAL_VERSION = 1
SIDECAR_JOURNAL_NAME = "sidecar-save-journal.json"
_DONE_STATUSES = {"committed", "restored"}
_TRANSACTION_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
_VALID_STATUSES = {"secured", "writing", *_DONE_STATUSES}
# native側と同じ猶予期間。ジャーナル未到達 (=退避コピー未完了) のディレクト
# リだけを対象にし、進行中トランザクションやDropbox同期遅延は消さない。
_STALE_TRANSACTION_MAX_AGE = timedelta(hours=24)


class SidecarSaveError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SidecarRecord:
    source: Path
    existed: bool
    backup: Path | None
    original_sha256: str


@dataclass(slots=True)
class SidecarSaveToken:
    work_dir: Path
    transaction_id: str
    transaction_dir: Path
    journal_path: Path
    records: tuple[SidecarRecord, ...]
    prune_empty_dirs: tuple[Path, ...] = ()
    status: str = "secured"


def _base(work: Path) -> Path:
    try:
        return _recovery_paths.sidecar_base(work)
    except _recovery_paths.SaveRecoveryPathError as exc:
        raise SidecarSaveError(str(exc)) from exc


def _bases(work: Path) -> tuple[Path, ...]:
    try:
        return _recovery_paths.sidecar_bases(work)
    except _recovery_paths.SaveRecoveryPathError as exc:
        raise SidecarSaveError(str(exc)) from exc


def _prune_base(work: Path, base: Path) -> None:
    try:
        _recovery_paths.prune_empty_base(work, base)
    except _recovery_paths.SaveRecoveryPathError:
        pass


def _validate_source(work: Path, source: Path) -> Path:
    root = work.resolve(strict=True)
    resolved = source.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SidecarSaveError("作品情報の保存先が作品フォルダー外です") from exc
    if resolved.is_symlink() or (resolved.exists() and not resolved.is_file()):
        raise SidecarSaveError(f"作品情報の保存先が通常ファイルではありません: {resolved}")
    return resolved


def _validate_prune_dir(work: Path, directory: Path) -> Path:
    """復元後に空なら除去してよい、作品内の新規ディレクトリを検証する。"""

    root = work.resolve(strict=True)
    if directory.is_symlink():
        raise SidecarSaveError("復元後の削除対象がリンクです")
    resolved = directory.resolve(strict=False)
    if resolved == root:
        raise SidecarSaveError("作品フォルダー自体は復元後の削除対象にできません")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SidecarSaveError("復元後の削除対象が作品フォルダー外です") from exc
    if resolved.is_symlink() or (resolved.exists() and not resolved.is_dir()):
        raise SidecarSaveError(f"復元後の削除対象が通常フォルダーではありません: {resolved}")
    return resolved


def _copy_verified(source: Path, target: Path, expected_sha: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".copying",
        dir=str(target.parent),
    )
    temp = Path(temp_name)
    try:
        expected_size = source.stat().st_size
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            fd = -1
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if temp.stat().st_size != expected_size or sha256(temp) != expected_sha:
            raise SidecarSaveError(f"作品情報を正しく退避できませんでした: {source}")
        os.replace(temp, target)
    finally:
        if fd >= 0:
            os.close(fd)
        temp.unlink(missing_ok=True)


def _journal_value(token: SidecarSaveToken, status: str) -> dict:
    return {
        "journalVersion": SIDECAR_JOURNAL_VERSION,
        "transactionId": token.transaction_id,
        "status": status,
        "updatedAt": utc_now(),
        "workDir": str(token.work_dir),
        "pruneEmptyDirs": [str(path) for path in token.prune_empty_dirs],
        "records": [
            {
                "sourcePath": str(record.source),
                "existed": record.existed,
                "backupPath": str(record.backup or ""),
                "originalSha256": record.original_sha256,
            }
            for record in token.records
        ],
    }


def begin_sidecar_save(
    work_dir: str | os.PathLike[str],
    paths: Iterable[str | os.PathLike[str]],
    *,
    transaction_id: str = "",
    prune_empty_dirs: Iterable[str | os.PathLike[str]] = (),
) -> SidecarSaveToken:
    """全書込み対象を先に退避する。戻った時点では全件復元可能。"""

    work = Path(work_dir).resolve(strict=True)
    sources = sorted(
        {_validate_source(work, Path(path)) for path in paths},
        key=lambda path: os.path.normcase(str(path)),
    )
    prune_dirs = tuple(sorted(
        {_validate_prune_dir(work, Path(path)) for path in prune_empty_dirs},
        key=lambda path: (-len(path.parts), os.path.normcase(str(path))),
    ))
    if any(directory.exists() for directory in prune_dirs):
        raise SidecarSaveError("復元後の削除対象は取引開始前から存在しています")
    for directory in prune_dirs:
        if not any(
            not source.exists() and source.is_relative_to(directory)
            for source in sources
        ):
            raise SidecarSaveError("復元後の削除対象が新規作品情報と対応していません")
    tx_id = transaction_id or new_transaction_id()
    tx_dir = _base(work) / tx_id
    tx_dir.mkdir(parents=True, exist_ok=False)
    journal_path = tx_dir / SIDECAR_JOURNAL_NAME
    records: list[SidecarRecord] = []
    try:
        for index, source in enumerate(sources):
            if source.is_file():
                digest = sha256(source)
                backup = tx_dir / "backup" / f"{index:04d}.bin"
                _copy_verified(source, backup, digest)
                records.append(SidecarRecord(source, True, backup, digest))
            else:
                records.append(SidecarRecord(source, False, None, ""))
        token = SidecarSaveToken(
            work_dir=work,
            transaction_id=tx_id,
            transaction_dir=tx_dir,
            journal_path=journal_path,
            records=tuple(records),
            prune_empty_dirs=prune_dirs,
        )
        atomic_write_json(journal_path, _journal_value(token, "secured"))
        return token
    except BaseException:
        shutil.rmtree(tx_dir, ignore_errors=True)
        _prune_base(work, tx_dir.parent)
        raise


def mark_sidecar_writes_started(token: SidecarSaveToken | None) -> None:
    if token is None or token.status != "secured":
        return
    token.status = "writing"
    atomic_write_json(token.journal_path, _journal_value(token, token.status))


def _verify_backups(records: Iterable[SidecarRecord]) -> None:
    for record in records:
        if not record.existed:
            continue
        if (
            record.backup is None
            or not record.backup.is_file()
            or sha256(record.backup) != record.original_sha256
        ):
            raise SidecarSaveError(f"作品情報の退避ファイルが破損しています: {record.source}")


def restore_sidecars(token: SidecarSaveToken | None) -> bool:
    if token is None or token.status == "restored":
        return False
    if token.status == "committed":
        raise SidecarSaveError("確定済みの作品情報は復元できません")
    _verify_backups(token.records)
    for record in token.records:
        if record.existed:
            assert record.backup is not None
            _copy_verified(record.backup, record.source, record.original_sha256)
        else:
            record.source.unlink(missing_ok=True)
    for directory in token.prune_empty_dirs:
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # 未知のファイルが残るフォルダーは削除しない。復元対象外データを
            # 巻き込まないため、再帰削除にはしない。
            continue
    token.status = "restored"
    try:
        atomic_write_json(token.journal_path, _journal_value(token, token.status))
        shutil.rmtree(token.transaction_dir, ignore_errors=True)
        _prune_base(token.work_dir, token.transaction_dir.parent)
    except Exception:
        # ファイル群の物理復元は完了済み。記録は次回起動時の再処理用に残す。
        pass
    return True


def commit_sidecars(token: SidecarSaveToken | None) -> None:
    if token is None or token.status == "committed":
        return
    if token.status == "restored":
        raise SidecarSaveError("復元済みの作品情報は確定できません")
    token.status = "committed"
    atomic_write_json(token.journal_path, _journal_value(token, token.status))
    shutil.rmtree(token.transaction_dir, ignore_errors=True)
    _prune_base(token.work_dir, token.transaction_dir.parent)


def _token_from_journal(path: Path, data: dict, work: Path) -> SidecarSaveToken:
    if (
        type(data.get("journalVersion")) is not int
        or data.get("journalVersion") != SIDECAR_JOURNAL_VERSION
    ):
        raise SidecarSaveError("未対応の作品情報復旧版です")
    if Path(str(data.get("workDir", ""))).resolve(strict=True) != work:
        raise SidecarSaveError("作品情報復旧が別作品を指しています")
    tx_id = str(data.get("transactionId", ""))
    if not _TRANSACTION_ID_RE.fullmatch(tx_id):
        raise SidecarSaveError("作品情報復旧IDが不正です")
    if not _recovery_paths.is_safe_transaction_journal(
        path,
        tx_id,
        _bases(work),
    ):
        raise SidecarSaveError("作品情報復旧の配置が不正です")
    status = str(data.get("status", ""))
    if status not in _VALID_STATUSES:
        raise SidecarSaveError("作品情報復旧状態が不正です")
    source_keys = set()
    records = []
    raw_prune_dirs = data.get("pruneEmptyDirs", [])
    if not isinstance(raw_prune_dirs, list) or not all(
        isinstance(value, str) for value in raw_prune_dirs
    ):
        raise SidecarSaveError("復元後の削除対象が不正です")
    prune_dirs = tuple(sorted(
        {_validate_prune_dir(work, Path(value)) for value in raw_prune_dirs},
        key=lambda directory: (-len(directory.parts), os.path.normcase(str(directory))),
    ))
    raw_records = data.get("records", [])
    if not isinstance(raw_records, list):
        raise SidecarSaveError("作品情報復旧レコードが不正です")
    for item in raw_records:
        if not isinstance(item, dict) or type(item.get("existed")) is not bool:
            raise SidecarSaveError("作品情報復旧レコードが不正です")
        source = _validate_source(work, Path(str(item.get("sourcePath", ""))))
        source_key = os.path.normcase(str(source))
        if source_key in source_keys:
            raise SidecarSaveError("作品情報復旧レコードが重複しています")
        source_keys.add(source_key)
        backup_text = str(item.get("backupPath", ""))
        backup = Path(backup_text).resolve(strict=False) if backup_text else None
        if backup is not None:
            try:
                backup.relative_to(path.parent.resolve(strict=True))
            except ValueError as exc:
                raise SidecarSaveError("作品情報の退避パスが不正です") from exc
        digest = str(item.get("originalSha256", ""))
        if item["existed"]:
            if backup is None or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SidecarSaveError("作品情報の退避内容が不正です")
        elif backup is not None or digest:
            raise SidecarSaveError("新規作品情報に不要な退避内容があります")
        records.append(
            SidecarRecord(
                source=source,
                existed=item["existed"],
                backup=backup,
                original_sha256=digest,
            )
        )
    for directory in prune_dirs:
        if not any(
            not record.existed and record.source.is_relative_to(directory)
            for record in records
        ):
            raise SidecarSaveError("復元後の削除対象が新規作品情報と対応していません")
    return SidecarSaveToken(
        work_dir=work,
        transaction_id=tx_id,
        transaction_dir=path.parent,
        journal_path=path,
        records=tuple(records),
        prune_empty_dirs=prune_dirs,
        status=status,
    )


def cleanup_stale_transactions(
    work_dir: str | os.PathLike[str],
) -> tuple[Path, ...]:
    """ジャーナル未到達のまま残った古いトランザクションディレクトリを掃除する.

    ``begin_sidecar_save`` は全件の退避コピー完了後にだけジャーナルを書く
    ため、ジャーナルが無いディレクトリは実ファイルへの書込みが一切始まって
    いない。誤って進行中トランザクションを消さないよう、この関数自体は
    例外を外へ出さず、個々のエントリで握って続行する。
    """
    work = Path(work_dir)
    removed: list[Path] = []
    now = datetime.now(timezone.utc)
    try:
        bases = _bases(work)
    except Exception:  # noqa: BLE001
        return ()
    for base in bases:
        removed.extend(_cleanup_transaction_base(base, now))
        _prune_base(work, base)
    return tuple(removed)


def _cleanup_transaction_base(base: Path, now: datetime) -> list[Path]:
    if not base.is_dir():
        return []
    removed: list[Path] = []
    try:
        entries = list(base.iterdir())
    except OSError:
        return removed
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if not _TRANSACTION_ID_RE.fullmatch(entry.name):
                continue
            journal_path = entry / SIDECAR_JOURNAL_NAME
            if journal_path.is_file():
                status = str(read_json_mapping(journal_path).get("status", ""))
                if status not in _DONE_STATUSES:
                    continue
            else:
                stamp = datetime.strptime(
                    entry.name.split("-", 1)[0], "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=timezone.utc)
                if now - stamp < _STALE_TRANSACTION_MAX_AGE:
                    continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed.append(entry)
        except Exception:  # noqa: BLE001
            continue
    return removed


def find_pending_sidecar_saves(work_dir: str | os.PathLike[str]) -> tuple[Path, ...]:
    work = Path(work_dir).resolve(strict=True)
    pending = []
    for base in _bases(work):
        if not base.is_dir():
            continue
        for path in sorted(base.glob(f"*/{SIDECAR_JOURNAL_NAME}")):
            try:
                status = str(read_json_mapping(path).get("status", ""))
            except Exception:
                pending.append(path)
                continue
            if status not in _DONE_STATUSES:
                pending.append(path)
    return tuple(pending)


def recover_pending_sidecar_saves(work_dir: str | os.PathLike[str]) -> tuple[Path, ...]:
    work = Path(work_dir).resolve(strict=True)
    restored = []
    for path in find_pending_sidecar_saves(work):
        data = dict(read_json_mapping(path))
        token = _token_from_journal(path, data, work)
        native_status = _native_transaction_status(work, token.transaction_id)
        if native_status in {"commit_decided", "committed"}:
            commit_sidecars(token)
            continue
        if token.status == "preparing":
            continue
        if restore_sidecars(token):
            restored.extend(record.source for record in token.records)
    return tuple(restored)


def _native_transaction_status(work: Path, transaction_id: str) -> str:
    try:
        bases = _recovery_paths.native_bases(work)
    except _recovery_paths.SaveRecoveryPathError as exc:
        raise SidecarSaveError(str(exc)) from exc
    for base in bases:
        journal = base / transaction_id / "native-save-journal.json"
        try:
            return str(read_json_mapping(journal).get("status", ""))
        except Exception:
            continue
    return ""


__all__ = [
    "SidecarSaveError",
    "SidecarSaveToken",
    "begin_sidecar_save",
    "cleanup_stale_transactions",
    "commit_sidecars",
    "find_pending_sidecar_saves",
    "mark_sidecar_writes_started",
    "recover_pending_sidecar_saves",
    "restore_sidecars",
]
