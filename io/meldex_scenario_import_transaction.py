"""Meldexシナリオ取込をページJSON群ごとの一括取引として保護する。"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..utils import page_detail, paths
from . import page_io, schema
from .project_content_migration_lock import (
    allow_owned_recovery_journal,
    guard_path_write,
    work_lock,
)
from .project_content_migration_storage import read_json_mapping
from .project_content_save_baseline import (
    record_observed_read,
    record_successful_write,
)
from .project_content_sidecar_save_guard import (
    begin_sidecar_save,
    commit_sidecars,
    mark_sidecar_writes_started,
    restore_sidecars,
)


class ScenarioImportTransactionError(RuntimeError):
    """作品を変更せず中止できた、ユーザーへそのまま提示可能な取込エラー。"""


@dataclass(frozen=True, slots=True)
class ScenarioImportPlan:
    work_dir: Path
    new_page_ids: tuple[str, ...]
    page_json_paths: tuple[Path, ...]


@dataclass(slots=True)
class _MemorySnapshot:
    work_data: dict
    pages_data: dict
    page_details: dict[str, dict]
    detail_loaded: dict[str, bool]
    coma_count: dict[str, int]
    stack_expanded: dict[str, bool]
    selected: dict[str, bool]
    work_dir: str
    loaded: bool


def _capture_memory(work) -> _MemorySnapshot:
    details: dict[str, dict] = {}
    loaded: dict[str, bool] = {}
    coma_count: dict[str, int] = {}
    stack_expanded: dict[str, bool] = {}
    selected: dict[str, bool] = {}
    for page in work.pages:
        page_id = str(getattr(page, "id", "") or "")
        details[page_id] = copy.deepcopy(schema.page_to_dict(page))
        loaded[page_id] = bool(getattr(page, "detail_loaded", False))
        coma_count[page_id] = int(getattr(page, "coma_count", 0) or 0)
        stack_expanded[page_id] = bool(getattr(page, "stack_expanded", True))
        selected[page_id] = bool(getattr(page, "selected", False))
    return _MemorySnapshot(
        work_data=copy.deepcopy(schema.work_to_dict(work)),
        pages_data=copy.deepcopy(schema.pages_to_dict(work)),
        page_details=details,
        detail_loaded=loaded,
        coma_count=coma_count,
        stack_expanded=stack_expanded,
        selected=selected,
        work_dir=str(getattr(work, "work_dir", "") or ""),
        loaded=bool(getattr(work, "loaded", False)),
    )


def _restore_memory(work, snapshot: _MemorySnapshot) -> None:
    work.loaded = False
    schema.work_from_dict(work, copy.deepcopy(snapshot.work_data))
    schema.pages_from_dict(work, copy.deepcopy(snapshot.pages_data))
    for page in work.pages:
        page_id = str(getattr(page, "id", "") or "")
        detail = snapshot.page_details.get(page_id)
        if detail is not None:
            schema.page_from_dict(page, copy.deepcopy(detail))
        page.detail_loaded = bool(snapshot.detail_loaded.get(page_id, False))
        page.coma_count = int(snapshot.coma_count.get(page_id, len(page.comas)))
        page.stack_expanded = bool(snapshot.stack_expanded.get(page_id, True))
        page.selected = bool(snapshot.selected.get(page_id, False))
    work.work_dir = snapshot.work_dir
    work.loaded = snapshot.loaded


def _planned_new_page_ids(work, required_pages: int) -> tuple[str, ...]:
    missing = max(0, int(required_pages) - len(work.pages))
    used = [str(page.id) for page in work.pages]
    result = []
    for _index in range(missing):
        page_id = paths.format_page_id(paths.next_available_page_index(used))
        used.append(page_id)
        result.append(page_id)
    return tuple(result)


def _observe_existing_import_copy(path: Path) -> None:
    """再取込時の保存コピーを読込時点の競合基準へ加える。"""

    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ScenarioImportTransactionError(
            f"前回のシナリオ保存先が通常ファイルではありません: {path}"
        )
    if not path.is_file():
        return
    with path.open("rb") as handle:
        handle.read(1)
    record_observed_read(path)


def _preload_affected_pages(work, required_pages: int) -> None:
    for page in list(work.pages)[: min(len(work.pages), int(required_pages))]:
        if bool(getattr(page, "detail_loaded", False)):
            continue
        page_path = paths.page_meta_path(Path(str(work.work_dir)), str(page.id))
        if not page_path.is_file():
            raise ScenarioImportTransactionError(
                f"{page.id} のページ情報ファイルがないため、シナリオ取込を中止しました"
            )
        page_detail.ensure_page_detail(work, page)
        if not bool(getattr(page, "detail_loaded", False)):
            raise ScenarioImportTransactionError(
                f"{page.id} のページ情報を読み込めないため、シナリオ取込を中止しました"
            )


def _prepare_plan(work, work_dir: Path, required_pages: int) -> ScenarioImportPlan:
    new_page_ids = _planned_new_page_ids(work, required_pages)
    for page_id in new_page_ids:
        page_dir = paths.page_dir(work_dir, page_id)
        if page_dir.is_symlink() or page_dir.exists():
            raise ScenarioImportTransactionError(
                f"未登録のページフォルダーが既にあるため、安全のため取込を中止しました: {page_dir}"
            )
    existing_ids = [str(page.id) for page in work.pages]
    affected_ids = tuple((existing_ids + list(new_page_ids))[: int(required_pages)])
    return ScenarioImportPlan(
        work_dir=work_dir,
        new_page_ids=new_page_ids,
        page_json_paths=tuple(paths.page_meta_path(work_dir, page_id) for page_id in affected_ids),
    )


def _sidecar_targets(plan: ScenarioImportPlan, *, save_payload_copy: bool) -> tuple[Path, ...]:
    targets = list(plan.page_json_paths)
    targets.append(paths.pages_meta_path(plan.work_dir))
    # フキダシIDは作品全体の単調増加カウンターを使うため、ページ追加の
    # 有無にかかわらず work.json も同じ取引で永続化する。
    targets.append(paths.work_meta_path(plan.work_dir))
    if plan.new_page_ids:
        targets.extend(
            paths.coma_json_path(plan.work_dir, page_id, "c01")
            for page_id in plan.new_page_ids
        )
    if save_payload_copy:
        targets.append(paths.scenario_file(plan.work_dir))
    return tuple(targets)


def _commit_sidecars_checked(token) -> None:
    try:
        commit_sidecars(token)
    except BaseException:
        try:
            durable_status = str(read_json_mapping(token.journal_path).get("status", ""))
        except BaseException:
            durable_status = ""
        if durable_status == "committed":
            return
        if durable_status in {"secured", "writing", "restored"}:
            token.status = durable_status
        raise


def _rollback(work, snapshot: _MemorySnapshot, token, targets: tuple[Path, ...]) -> None:
    first_error: BaseException | None = None
    restored = False
    if token is not None:
        try:
            restore_sidecars(token)
            restored = True
        except BaseException as exc:
            first_error = exc
    page_io.invalidate_page_json_write_cache(
        tuple(path for path in targets if path.name == paths.PAGE_META_NAME)
    )
    if restored:
        for path in targets:
            try:
                record_successful_write(path)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
    try:
        _restore_memory(work, snapshot)
    except BaseException as exc:
        if first_error is None:
            first_error = exc
    if first_error is not None:
        raise ScenarioImportTransactionError(
            "シナリオ取込を完全には復元できませんでした。作品を開き直してください"
        ) from first_error


@contextmanager
def scenario_import_transaction(
    work,
    required_pages: int,
    *,
    save_payload_copy: bool,
) -> Iterator[ScenarioImportPlan]:
    """全対象を先に退避し、例外時はファイルとメモリを取込前へ戻す。"""

    work_dir = Path(str(getattr(work, "work_dir", "") or "")).resolve(strict=True)
    with work_lock(work_dir, blocking=True):
        snapshot = _capture_memory(work)
        token = None
        targets: tuple[Path, ...] = ()
        try:
            # 作品全体のDropbox/別画面競合を、何も変更しない段階で検査する。
            with guard_path_write(paths.pages_meta_path(work_dir)):
                pass
            plan = _prepare_plan(work, work_dir, required_pages)
            _preload_affected_pages(work, required_pages)
            if save_payload_copy:
                _observe_existing_import_copy(paths.scenario_file(work_dir))
            targets = _sidecar_targets(plan, save_payload_copy=save_payload_copy)
            prune_dirs = tuple(
                directory
                for page_id in plan.new_page_ids
                for directory in (
                    paths.coma_dir(work_dir, page_id, "c01"),
                    paths.page_dir(work_dir, page_id),
                )
            )
            scenario_dir = paths.scenario_dir(work_dir)
            if save_payload_copy and not scenario_dir.exists():
                prune_dirs += (scenario_dir,)
            token = begin_sidecar_save(
                work_dir,
                targets,
                prune_empty_dirs=prune_dirs,
            )
            with allow_owned_recovery_journal(token.journal_path):
                mark_sidecar_writes_started(token)
                yield plan
                _commit_sidecars_checked(token)
        except BaseException:
            _rollback(work, snapshot, token, targets)
            raise


__all__ = [
    "ScenarioImportPlan",
    "ScenarioImportTransactionError",
    "scenario_import_transaction",
]
