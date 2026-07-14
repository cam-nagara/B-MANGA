"""作品移行の復旧前分類と一括復旧。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    from .project_content_migration_model import RecoveryError, WORK_META_NAME
    from . import project_content_migration_storage as _storage
except ImportError:  # ファイル単体でロードする純Pythonテスト用
    from project_content_migration_model import (  # type: ignore[no-redef]
        RecoveryError,
        WORK_META_NAME,
    )
    import project_content_migration_storage as _storage  # type: ignore[no-redef]


@dataclass(frozen=True, slots=True)
class RollbackAction:
    page_id: str
    source: Path
    backup: Path | None
    expected_original: str
    restore: bool


def _current_hash(path: Path, label: str) -> str:
    if not path.is_file():
        raise RecoveryError(f"復旧対象が見つかりません: {label}")
    return _storage.sha256(path)


def _classify(
    *,
    source: Path,
    original: str,
    committed: str,
    backup_text: str,
    label: str,
    journal_path: Path,
) -> RollbackAction:
    current = _current_hash(source, label)
    if current == original:
        return RollbackAction(label, source, None, original, False)
    if committed and current == committed:
        if not backup_text:
            raise RecoveryError(f"復旧用の退避ファイルがありません: {label}")
        backup = Path(backup_text)
        _storage.require_backup(backup, original, journal_path)
        return RollbackAction(label, source, backup, original, True)
    raise RecoveryError(
        f"移行後に別の変更が保存されたため自動復旧を停止しました: {label}"
    )


def classify_rollback(
    journal: Mapping[str, Any],
    journal_path: Path,
) -> tuple[tuple[RollbackAction, ...], RollbackAction]:
    """全対象を先に分類・検証し、1件でも不明なら書込み前に停止する。"""
    page_actions: list[RollbackAction] = []
    for record in journal.get("pages", []):
        page_id = str(record["pageId"])
        page_actions.append(_classify(
            source=Path(str(record["sourcePath"])),
            original=str(record["sourceSha256"]),
            committed=str(record.get("stagedSha256", "")),
            backup_text=str(record.get("backupPath", "")),
            label=page_id,
            journal_path=journal_path,
        ))
    work_action = _classify(
        source=Path(str(journal["workMetaPath"])),
        original=str(journal["workMetaSha256"]),
        committed=str(journal.get("workMetaPlannedSha256", "")),
        backup_text=str(journal.get("workMetaBackup", "")),
        label=WORK_META_NAME,
        journal_path=journal_path,
    )
    return tuple(page_actions), work_action


def apply_action(action: RollbackAction, transaction_id: str) -> bool:
    if not action.restore:
        return False
    assert action.backup is not None
    _storage.atomic_install(action.backup, action.source, transaction_id)
    return True


def verify_originals(actions: tuple[RollbackAction, ...], work: RollbackAction) -> None:
    for action in (*actions, work):
        if _current_hash(action.source, action.page_id) != action.expected_original:
            raise RecoveryError(f"退避時点へ戻せませんでした: {action.page_id}")


__all__ = [
    "RollbackAction",
    "apply_action",
    "classify_rollback",
    "verify_originals",
]
