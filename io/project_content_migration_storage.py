"""作品データ移行のジャーナルと原子的ファイル操作。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Mapping
import uuid

try:
    from .project_content_migration_model import (
        DETAIL_DATA_VERSION_KEY,
        JOURNAL_FILE_NAME,
        MIGRATION_VERSION,
        PAGE_BLEND_NAME,
        WORK_META_NAME,
        MigrationError,
        MigrationIssue,
        MigrationPlan,
        MigrationResult,
        PagePlan,
        RecoveryError,
    )
    from .project_content_version import (
        DetailDataVersionError,
        detail_data_version,
    )
except ImportError:  # 単体テストがこのモジュールをファイルとして読む場合
    from project_content_migration_model import (  # type: ignore[no-redef]
        DETAIL_DATA_VERSION_KEY,
        JOURNAL_FILE_NAME,
        MIGRATION_VERSION,
        PAGE_BLEND_NAME,
        WORK_META_NAME,
        MigrationError,
        MigrationIssue,
        MigrationPlan,
        MigrationResult,
        PagePlan,
        RecoveryError,
    )
    from project_content_version import (  # type: ignore[no-redef]
        DetailDataVersionError,
        detail_data_version,
    )


_PAGE_ID_RE = re.compile(r"^p\d{4}(?:-\d{4})?$")
_TRANSACTION_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
JOURNAL_VERSION = 1


def find_incomplete_journals(work_dir: str | os.PathLike[str]) -> tuple[Path, ...]:
    """既定退避先から未完了の移行ジャーナルだけを列挙する。"""
    work = Path(work_dir).resolve()
    base = transaction_base(work)
    if not base.is_dir():
        return ()
    found: list[Path] = []
    for path in base.glob(f"*/{JOURNAL_FILE_NAME}"):
        try:
            status = str(read_json_mapping(path).get("status", ""))
        except Exception:
            found.append(path)
            continue
        if status not in {"rolled_back", "verified_after_restart"}:
            found.append(path)
    return tuple(sorted(found))


def journal_from_plan(plan: MigrationPlan) -> dict[str, Any]:
    return {
        "journalVersion": JOURNAL_VERSION,
        "migrationVersion": MIGRATION_VERSION,
        "targetVersion": MIGRATION_VERSION,
        "transactionId": plan.transaction_id,
        "status": "prepared",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "workDir": str(plan.work_dir),
        "workMetaPath": str(plan.work_meta_path),
        "workMetaSha256": plan.work_meta_sha256,
        "workMetaBackup": "",
        "workMetaPlannedSha256": "",
        "markerBefore": plan.marker_before,
        "markerWritten": False,
        "backupDir": str(plan.backup_dir),
        "stageDir": str(plan.stage_dir),
        "folderManifest": [dict(item) for item in plan.folder_manifest],
        "pages": [journal_page(page) for page in plan.pages],
        "events": [],
    }


def journal_page(page: PagePlan) -> dict[str, Any]:
    return {
        "pageId": page.page_id,
        "sourcePath": str(page.source_path),
        "sourceSize": page.source_size,
        "sourceSha256": page.source_sha256,
        "backupPath": "",
        "stagePath": "",
        "stagedSha256": "",
        "state": "planned",
    }


def validate_journal_paths(
    journal_path: Path,
    journal: Mapping[str, Any],
    expected_work_dir: str | os.PathLike[str],
) -> None:
    """復旧対象とジャーナルの世代・配置・全パスを厳密に照合する。"""
    required = {
        "workDir", "workMetaPath", "backupDir", "stageDir", "transactionId", "pages"
    }
    missing = sorted(required.difference(journal))
    if missing:
        raise RecoveryError(f"ジャーナルの必須項目がありません: {', '.join(missing)}")
    work = Path(expected_work_dir).resolve(strict=True)
    recorded_work = Path(str(journal.get("workDir", ""))).resolve(strict=True)
    if recorded_work != work:
        raise RecoveryError("ジャーナルが別の作品を指しています")
    for key, expected in (
        ("journalVersion", JOURNAL_VERSION),
        ("migrationVersion", MIGRATION_VERSION),
        ("targetVersion", MIGRATION_VERSION),
    ):
        value = journal.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise RecoveryError(f"未対応のジャーナル版です: {key}={value!r}")
    transaction_id = str(journal.get("transactionId", ""))
    if not _TRANSACTION_ID_RE.fullmatch(transaction_id):
        raise RecoveryError("ジャーナルの取引IDが不正です")
    if journal_path.name != JOURNAL_FILE_NAME:
        raise RecoveryError("ジャーナルのファイル名が不正です")
    tx_dir = journal_path.parent.resolve(strict=True)
    expected_tx = Path(os.path.abspath(transaction_base(work) / transaction_id))
    if tx_dir != expected_tx:
        raise RecoveryError("ジャーナルが正規の退避先にありません")
    backup_dir = Path(str(journal.get("backupDir", ""))).resolve(strict=False)
    stage_dir = Path(str(journal.get("stageDir", ""))).resolve(strict=False)
    if backup_dir != tx_dir / "backup" or stage_dir != tx_dir / "stage":
        raise RecoveryError("ジャーナルの退避先または一時生成先が不正です")
    work_meta = Path(str(journal["workMetaPath"]))
    require_within(work_meta, work)
    if work_meta.resolve() != (work / WORK_META_NAME).resolve():
        raise RecoveryError("ジャーナルの work.json パスが不正です")
    if journal.get("workMetaBackup"):
        work_backup = Path(str(journal["workMetaBackup"])).resolve(strict=False)
        if work_backup != backup_dir / WORK_META_NAME:
            raise RecoveryError("work.json の退避パスが不正です")
    records = journal.get("pages", [])
    if not isinstance(records, list):
        raise RecoveryError("ジャーナルのページ一覧が不正です")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise RecoveryError("ジャーナルのページ情報が不正です")
        page_id = str(record.get("pageId", ""))
        if not {"sourcePath", "sourceSha256"}.issubset(record):
            raise RecoveryError("ジャーナルのページ必須項目がありません")
        if page_id in seen:
            raise RecoveryError(f"ジャーナルにページが重複しています: {page_id}")
        seen.add(page_id)
        source = Path(str(record["sourcePath"]))
        require_within(source, work)
        expected = (work / page_id / PAGE_BLEND_NAME).resolve()
        if (
            not _PAGE_ID_RE.fullmatch(page_id)
            or source.is_symlink()
            or source.parent.is_symlink()
            or source.resolve() != expected
        ):
            raise RecoveryError(f"ジャーナルのページパスが不正です: {page_id}")
        expected_paths = {
            "backupPath": backup_dir / page_id / PAGE_BLEND_NAME,
            "stagePath": stage_dir / page_id / PAGE_BLEND_NAME,
        }
        for key, expected_path in expected_paths.items():
            if record.get(key) and Path(str(record[key])).resolve(strict=False) != expected_path:
                raise RecoveryError(f"ジャーナルのページ退避パスが不正です: {page_id}")


def result_from_journal(
    path: Path,
    journal: Mapping[str, Any],
    status: str,
) -> MigrationResult:
    return MigrationResult(
        status=status,
        journal_path=path,
        page_count=len(journal.get("pages", [])),
        backup_dir=Path(str(journal["backupDir"])),
    )


def transaction_path(
    work: Path,
    transaction_id: str,
    requested: str | os.PathLike[str] | None,
) -> Path:
    base = transaction_base(work)
    if base.is_symlink():
        raise MigrationError("移行用フォルダーがシンボリックリンクです")
    path = (
        base / transaction_id
        if requested is None
        else Path(requested)
    ).resolve()
    if path == work or path.is_relative_to(work):
        raise MigrationError("退避先は作品フォルダーの外に指定してください")
    return path


def transaction_base(work: Path) -> Path:
    return work.parent / f".{work.name}.detail-data-migration-v{MIGRATION_VERSION}"


def unsafe_page_path_issue(page_id: str, path: Path) -> MigrationIssue:
    return MigrationIssue(
        code="unsafe_page_path",
        page_id=page_id,
        page_path=str(path),
        message="ページ用blendファイルまたはページフォルダーがシンボリックリンクです",
    )


def invalid_page_file_issue(page_id: str, path: Path) -> MigrationIssue:
    return MigrationIssue(
        code="invalid_page_blend",
        page_id=page_id,
        page_path=str(path),
        message="ページ用blendファイルの場所が通常ファイルではありません",
    )


def future_version_issue(path: Path, marker: int) -> MigrationIssue:
    return MigrationIssue(
        code="future_detail_data_version",
        page_id="",
        page_path=str(path),
        message=f"この版より新しい作品データです: {marker}",
    )


def invalid_version_issue(path: Path, message: str) -> MigrationIssue:
    return MigrationIssue(
        code="invalid_detail_data_version",
        page_id="",
        page_path=str(path),
        message=message,
    )


def existing_transaction_issue(path: Path) -> MigrationIssue:
    return MigrationIssue(
        code="transaction_path_exists",
        page_id="",
        page_path=str(path),
        message="同じ移行用フォルダーが既にあります",
    )


def append_event(journal: dict[str, Any], event: str, page_id: str, index: int) -> None:
    journal.setdefault("events", []).append({
        "at": utc_now(),
        "event": event,
        "pageId": page_id,
        "index": index,
    })


def copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    fsync_file(destination)
    if sha256(destination) != expected_sha256:
        raise MigrationError(f"退避コピーの検証に失敗しました: {source}")


def atomic_install(source: Path, destination: Path, transaction_id: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = f".{destination.name}.{transaction_id}.installing"
    temp_path = destination.with_name(temp_name)
    try:
        shutil.copy2(source, temp_path)
        fsync_file(temp_path)
        replace_with_retry(temp_path, destination)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass  # 次回実行時に同じ固定名を上書きできるため本体処理を優先する


def require_backup(backup: Path, expected: str, journal_path: Path) -> None:
    require_within(backup, journal_path.parent)
    if not backup.is_file() or sha256(backup) != expected:
        raise RecoveryError(f"退避ファイルが無いか破損しています: {backup}")


def require_original_unchanged(source: Path, expected: str, page_id: str) -> None:
    if not source.is_file() or sha256(source) != expected:
        raise RecoveryError(f"退避前に変更されたページを復旧できません: {page_id}")


def require_within(path: Path, root: Path) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise MigrationError(f"作品・移行フォルダー外のパスです: {resolved}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_mapping(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise MigrationError(f"JSONのルートがオブジェクトではありません: {path}")
    return value


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json_bytes(data))
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(Path(temp_name), path)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass  # 原子的置換の成否を一時ファイル掃除で上書きしない


def json_bytes(data: Mapping[str, Any]) -> bytes:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return text.encode("utf-8")


def write_journal(path: Path, journal: dict[str, Any]) -> None:
    journal["updatedAt"] = utc_now()
    atomic_write_json(path, journal)


def replace_with_retry(source: Path, destination: Path) -> None:
    delay = 0.05
    last_error: OSError | None = None
    for attempt in range(7):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            last_error = exc
            if attempt == 6:
                break
            time.sleep(delay)
            delay *= 2
    if last_error is not None:
        raise last_error


def fsync_file(path: Path) -> None:
    # Windows の FlushFileBuffers は読取専用ハンドルを拒否するため r+b を使う。
    with path.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def new_transaction_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
