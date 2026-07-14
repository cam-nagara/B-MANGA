"""ネイティブ保存に伴うJSON/PNG sidecarを全件commit/rollbackする。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Iterable

try:
    from .project_content_migration_storage import (
        atomic_write_json,
        new_transaction_id,
        read_json_mapping,
        sha256,
        utc_now,
    )
except ImportError:  # ファイル単体でロードする純Pythonテスト用
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
    status: str = "secured"


def _base(work: Path) -> Path:
    base = work.parent / f".{work.name}.sidecar-save-recovery-v1"
    if base.is_symlink():
        raise SidecarSaveError("作品情報の退避先がシンボリックリンクです")
    return base


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
) -> SidecarSaveToken:
    """全書込み対象を先に退避する。戻った時点では全件復元可能。"""

    work = Path(work_dir).resolve(strict=True)
    sources = sorted(
        {_validate_source(work, Path(path)) for path in paths},
        key=lambda path: os.path.normcase(str(path)),
    )
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
        )
        atomic_write_json(journal_path, _journal_value(token, "secured"))
        return token
    except BaseException:
        shutil.rmtree(tx_dir, ignore_errors=True)
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
    token.status = "restored"
    atomic_write_json(token.journal_path, _journal_value(token, token.status))
    shutil.rmtree(token.transaction_dir, ignore_errors=True)
    return True


def commit_sidecars(token: SidecarSaveToken | None) -> None:
    if token is None or token.status == "committed":
        return
    if token.status == "restored":
        raise SidecarSaveError("復元済みの作品情報は確定できません")
    token.status = "committed"
    atomic_write_json(token.journal_path, _journal_value(token, token.status))
    shutil.rmtree(token.transaction_dir, ignore_errors=True)


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
    if path.parent.resolve(strict=False) != (_base(work) / tx_id).resolve(strict=False):
        raise SidecarSaveError("作品情報復旧の配置が不正です")
    status = str(data.get("status", ""))
    if status not in _VALID_STATUSES:
        raise SidecarSaveError("作品情報復旧状態が不正です")
    source_keys = set()
    records = []
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
    return SidecarSaveToken(
        work_dir=work,
        transaction_id=tx_id,
        transaction_dir=path.parent,
        journal_path=path,
        records=tuple(records),
        status=status,
    )


def find_pending_sidecar_saves(work_dir: str | os.PathLike[str]) -> tuple[Path, ...]:
    work = Path(work_dir).resolve(strict=True)
    base = _base(work)
    if not base.is_dir():
        return ()
    pending = []
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
        native_journal = (
            work.parent
            / f".{work.name}.native-save-recovery-v1"
            / token.transaction_id
            / "native-save-journal.json"
        )
        try:
            native_status = str(read_json_mapping(native_journal).get("status", ""))
        except Exception:
            native_status = ""
        if native_status in {"commit_decided", "committed"}:
            commit_sidecars(token)
            continue
        if token.status == "preparing":
            continue
        if restore_sidecars(token):
            restored.extend(record.source for record in token.records)
    return tuple(restored)


__all__ = [
    "SidecarSaveError",
    "SidecarSaveToken",
    "begin_sidecar_save",
    "commit_sidecars",
    "find_pending_sidecar_saves",
    "mark_sidecar_writes_started",
    "recover_pending_sidecar_saves",
    "restore_sidecars",
]
