"""作品データ移行で共有する不変データ型と例外。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


MIGRATION_VERSION = 1
DETAIL_DATA_VERSION_KEY = "detailDataVersion"
PAGE_DETAIL_DATA_VERSION_KEY = "pageDetailDataVersion"
JOURNAL_FILE_NAME = "migration-journal.json"
PAGE_BLEND_NAME = "page.blend"
WORK_META_NAME = "work.json"
JOURNAL_ALLOWANCE_BYTES = 16 * 1024 * 1024


class MigrationError(RuntimeError):
    """作品データ移行の基底例外。"""


class ConfirmationRequired(MigrationError):
    """明示確認が渡されていないため書込みを拒否した。"""


class PreflightBlocked(MigrationError):
    """事前検査で安全に変換できない項目が見つかった。"""


class RecoveryError(MigrationError):
    """退避データからの復旧を安全に完了できなかった。"""


class MigrationExecutionError(MigrationError):
    """変換または検証が失敗した。"""

    def __init__(self, message: str, *, rollback: "RecoveryResult | None" = None):
        super().__init__(message)
        self.rollback = rollback


@dataclass(frozen=True, slots=True)
class MigrationIssue:
    """事前検査で見つかった書込み禁止理由。"""

    code: str
    page_id: str
    page_path: str
    message: str
    raw_uid: str = ""
    link_group: str = ""


@dataclass(frozen=True, slots=True)
class PageInspection:
    """ページ変換コールバックが返す読取専用検査結果。"""

    estimated_output_bytes: int = 0
    issues: tuple[MigrationIssue, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PagePlan:
    """1ページ分の不変な移行計画。"""

    page_id: str
    source_path: Path
    source_size: int
    source_sha256: str
    estimated_output_bytes: int
    inspection_facts: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """確認画面へそのまま渡せる全作品の読取専用計画。"""

    transaction_id: str
    work_dir: Path
    transaction_dir: Path
    backup_dir: Path
    stage_dir: Path
    journal_path: Path
    work_meta_path: Path
    work_meta_sha256: str
    marker_before: int
    pages: tuple[PagePlan, ...]
    folder_manifest: tuple[Mapping[str, Any], ...]
    issues: tuple[MigrationIssue, ...]
    source_bytes: int
    estimated_stage_bytes: int
    required_bytes: int
    available_bytes: int
    created_at: str

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def already_current(self) -> bool:
        if self.issues or self.marker_before != MIGRATION_VERSION:
            return False
        return all(
            type(page.inspection_facts.get(PAGE_DETAIL_DATA_VERSION_KEY)) is int
            and page.inspection_facts.get(PAGE_DETAIL_DATA_VERSION_KEY) == MIGRATION_VERSION
            for page in self.pages
        )

    @property
    def capacity_ok(self) -> bool:
        return self.available_bytes >= self.required_bytes


@dataclass(frozen=True, slots=True)
class PageConversionTask:
    """具体的なBlender変換コールバックへ渡す固定対象。"""

    transaction_id: str
    page_id: str
    original_path: Path
    staged_path: Path
    backup_path: Path
    source_sha256: str
    inspection_facts: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: str
    journal_path: Path | None
    page_count: int
    backup_dir: Path | None


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    status: str
    journal_path: Path
    restored_pages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MigrationProgress:
    """画面表示へ渡す、段階・ページ・復旧結果の不変イベント。"""

    phase: str
    event: str
    page_id: str = ""
    index: int = 0
    total: int = 0
    message: str = ""
    rollback_status: str = ""


Inspector = Callable[[str, Path], PageInspection | Mapping[str, Any]]
Converter = Callable[[PageConversionTask], Any]
Validator = Callable[[str, Path], Any]
FaultHook = Callable[[str, str, int], Any]
ProgressCallback = Callable[[MigrationProgress], Any]


def unresolved_pointer_issue(
    page_id: str,
    page_path: Path,
    raw_uid: str,
    link_group: str,
) -> MigrationIssue:
    """逆引きできない旧ポインタUID用の標準エラーを作る。"""
    return MigrationIssue(
        code="unresolved_pointer_uid",
        page_id=page_id,
        page_path=str(page_path),
        raw_uid=str(raw_uid),
        link_group=str(link_group),
        message="保存後に逆引きできない旧リンクUIDがあります",
    )


def unsupported_gp_mask_issue(
    page_id: str,
    page_path: Path,
    description: str,
) -> MigrationIssue:
    """忠実な変換方法がないGPマスク用の標準エラーを作る。"""
    return MigrationIssue(
        code="unsupported_gp_mask",
        page_id=page_id,
        page_path=str(page_path),
        message=str(description or "対応できないGPマスクがあります"),
    )
