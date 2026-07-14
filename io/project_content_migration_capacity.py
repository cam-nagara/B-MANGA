"""作品移行の実行時空き容量再検査。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

try:
    from .project_content_migration_model import (
        JOURNAL_ALLOWANCE_BYTES,
        MigrationPlan,
        PagePlan,
        PreflightBlocked,
    )
except ImportError:  # ファイル単体でロードする純Pythonテスト用
    from project_content_migration_model import (  # type: ignore[no-redef]
        JOURNAL_ALLOWANCE_BYTES,
        MigrationPlan,
        PagePlan,
        PreflightBlocked,
    )


def _existing_ancestor(path: Path) -> Path:
    current = path.resolve(strict=False)
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _volume_key(path: Path) -> tuple[int, str]:
    existing = _existing_ancestor(path)
    try:
        return int(existing.stat().st_dev), existing.anchor.casefold()
    except OSError:
        return -1, existing.anchor.casefold()


def _disk_free(path: Path) -> int:
    return int(shutil.disk_usage(_existing_ancestor(path)).free)


def required_capacity(
    source_bytes: int,
    stage_bytes: int,
    pages: Sequence[PagePlan],
    work_meta: Path,
) -> int:
    largest_stage = max((page.estimated_output_bytes for page in pages), default=0)
    largest_source = max((page.source_size for page in pages), default=0)
    return (
        source_bytes + stage_bytes + largest_stage + largest_source
        + work_meta.stat().st_size + JOURNAL_ALLOWANCE_BYTES
    )


def available_capacity(transaction_dir: Path) -> int:
    return _disk_free(transaction_dir)


def _raise_shortage(label: str, required: int, free: int) -> None:
    if free < required:
        raise PreflightBlocked(
            f"{label}の空き容量が変換開始時より減っています: 必要 {required} / 空き {free}"
        )


def validate_before_writes(plan: MigrationPlan) -> None:
    """ロック取得後・退避作成前に両ボリュームを再検査する。"""
    tx_need = (
        plan.source_bytes
        + plan.estimated_stage_bytes
        + plan.work_meta_path.stat().st_size
        + JOURNAL_ALLOWANCE_BYTES
    )
    largest_stage = max((p.estimated_output_bytes for p in plan.pages), default=0)
    largest_source = max((p.source_size for p in plan.pages), default=0)
    work_need = largest_stage + largest_source + plan.work_meta_path.stat().st_size
    tx_key = _volume_key(plan.transaction_dir)
    work_key = _volume_key(plan.work_dir)
    if tx_key == work_key:
        free = _disk_free(plan.transaction_dir)
        _raise_shortage("作品保存先", tx_need + work_need, free)
        return
    _raise_shortage("退避先", tx_need, _disk_free(plan.transaction_dir))
    _raise_shortage("作品保存先", work_need, _disk_free(plan.work_dir))


def validate_after_stage(
    plan: MigrationPlan,
    records: Sequence[Mapping[str, Any]],
) -> None:
    """実際の一時生成サイズを使い、入替えと復旧の余力を再確認する。"""
    stage_sizes: list[int] = []
    backup_sizes: list[int] = []
    for record in records:
        stage = Path(str(record.get("stagePath", "")))
        backup = Path(str(record.get("backupPath", "")))
        if not stage.is_file():
            raise PreflightBlocked(f"変換済みページがありません: {record.get('pageId', '')}")
        if not backup.is_file():
            raise PreflightBlocked(f"退避済みページがありません: {record.get('pageId', '')}")
        stage_sizes.append(stage.stat().st_size)
        backup_sizes.append(backup.stat().st_size)
    largest_stage = max(stage_sizes, default=0)
    largest_source = max(backup_sizes, default=0)
    work_backup = plan.backup_dir / plan.work_meta_path.name
    if not work_backup.is_file():
        raise PreflightBlocked("work.json の退避ファイルがありません")
    # 入替え途中で異常終了し一時ファイルが残っても、退避版を原子的に戻せる分を残す。
    work_reserve = largest_stage + largest_source + work_backup.stat().st_size
    journal_reserve = JOURNAL_ALLOWANCE_BYTES
    tx_key = _volume_key(plan.transaction_dir)
    work_key = _volume_key(plan.work_dir)
    if tx_key == work_key:
        _raise_shortage(
            "作品保存先", work_reserve + journal_reserve, _disk_free(plan.work_dir)
        )
        return
    _raise_shortage("退避先", journal_reserve, _disk_free(plan.transaction_dir))
    _raise_shortage("作品保存先", work_reserve, _disk_free(plan.work_dir))


__all__ = [
    "available_capacity",
    "required_capacity",
    "validate_after_stage",
    "validate_before_writes",
]
