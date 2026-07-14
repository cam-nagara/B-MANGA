"""作品内の全 ``pNNNN/page.blend`` を原子的に移行する基盤。

このモジュールは Blender データの具体的な変換内容を知らない。読取専用の
事前検査、1ページの変換、変換後検証をコールバックとして受け取り、退避、
一時生成、全件検証、入替え、版マーカー更新、復旧だけを担当する。

重要な契約:

* :func:`build_migration_plan` はファイルを書かない。
* :func:`execute_migration` は ``confirmed is True`` になるまで書かない。
* 全ページの一時生成と検証が終わるまで元の ``page.blend`` を触らない。
* ``detailDataVersion`` は全ページ入替え・再検証後の最後にだけ更新する。
* 未完了ジャーナルは :func:`recover_transaction` で全ページを元へ戻せる。

実作品向けのGP／効果線変換コールバックは、Blender APIを使う呼び出し側で
実装する。この分離により、確認画面を開く段階では一切の書込みを行わない。
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import hashlib
from typing import Any, Mapping, Sequence

_PAGE_ID_RE = re.compile(r"^p\d{4}(?:-\d{4})?$")

try:
    from .project_content_migration_model import (
        DETAIL_DATA_VERSION_KEY,
        JOURNAL_FILE_NAME,
        MIGRATION_VERSION,
        PAGE_BLEND_NAME,
        WORK_META_NAME,
        ConfirmationRequired,
        Converter,
        FaultHook,
        Inspector,
        MigrationError,
        MigrationExecutionError,
        MigrationIssue,
        MigrationPlan,
        MigrationProgress,
        MigrationResult,
        PageConversionTask,
        PageInspection,
        PagePlan,
        PreflightBlocked,
        ProgressCallback,
        RecoveryError,
        RecoveryResult,
        Validator,
        unsupported_gp_mask_issue,
        unresolved_pointer_issue,
    )
    from . import project_content_migration_manifest as _manifest
    from . import project_content_migration_capacity as _capacity
    from . import project_content_migration_lock as _lock
    from . import project_content_native_save_guard as _native_guard
    from . import project_content_migration_recovery as _recovery
    from . import project_content_migration_storage as _storage
    from . import project_content_version as _version
except ImportError:  # 単体テストがこのファイルを直接ロードする場合
    _module_dir = str(Path(__file__).resolve().parent)
    _inserted_path = _module_dir not in sys.path
    if _inserted_path:
        sys.path.insert(0, _module_dir)
    try:
        from project_content_migration_model import (  # type: ignore[no-redef]
            DETAIL_DATA_VERSION_KEY,
            JOURNAL_FILE_NAME,
            MIGRATION_VERSION,
            PAGE_BLEND_NAME,
            WORK_META_NAME,
            ConfirmationRequired,
            Converter,
            FaultHook,
            Inspector,
            MigrationError,
            MigrationExecutionError,
            MigrationIssue,
            MigrationPlan,
            MigrationProgress,
            MigrationResult,
            PageConversionTask,
            PageInspection,
            PagePlan,
            PreflightBlocked,
            ProgressCallback,
            RecoveryError,
            RecoveryResult,
            Validator,
            unsupported_gp_mask_issue,
            unresolved_pointer_issue,
        )
        import project_content_migration_manifest as _manifest  # type: ignore[no-redef]
        import project_content_migration_capacity as _capacity  # type: ignore[no-redef]
        import project_content_migration_lock as _lock  # type: ignore[no-redef]
        import project_content_native_save_guard as _native_guard  # type: ignore[no-redef]
        import project_content_migration_recovery as _recovery  # type: ignore[no-redef]
        import project_content_migration_storage as _storage  # type: ignore[no-redef]
        import project_content_version as _version  # type: ignore[no-redef]
    finally:
        if _inserted_path:
            sys.path.remove(_module_dir)


find_incomplete_journals = _storage.find_incomplete_journals
_append_event = _storage.append_event
_atomic_install = _storage.atomic_install
_atomic_write_json = _storage.atomic_write_json
_copy_verified = _storage.copy_verified
_detail_data_version = _storage.detail_data_version
_existing_transaction_issue = _storage.existing_transaction_issue
_future_version_issue = _storage.future_version_issue
_invalid_version_issue = _storage.invalid_version_issue
_invalid_page_file_issue = _storage.invalid_page_file_issue
_journal_from_plan = _storage.journal_from_plan
_new_transaction_id = _storage.new_transaction_id
_read_json_mapping = _storage.read_json_mapping
_require_backup = _storage.require_backup
_require_original_unchanged = _storage.require_original_unchanged
_require_within = _storage.require_within
_result_from_journal = _storage.result_from_journal
_sha256 = _storage.sha256
_transaction_path = _storage.transaction_path
_transaction_base = _storage.transaction_base
_unsafe_page_path_issue = _storage.unsafe_page_path_issue
_utc_now = _storage.utc_now
_validate_journal_paths = _storage.validate_journal_paths
_write_journal = _storage.write_journal
_json_bytes = _storage.json_bytes
_build_folder_manifest = _manifest.build_folder_manifest
_merge_folder_manifest = _manifest.merge_folder_manifest
_verify_folder_manifest = _manifest.verify_folder_manifest


def build_migration_plan(
    work_dir: str | os.PathLike[str],
    *,
    inspector: Inspector | None,
    transaction_dir: str | os.PathLike[str] | None = None,
) -> MigrationPlan:
    """全ページを読取専用で検査し、書込み前の確認情報を返す。"""
    work = _validated_work_dir(work_dir)
    work_meta = work / WORK_META_NAME
    work_data = _read_json_mapping(work_meta)
    marker_issues: tuple[MigrationIssue, ...] = ()
    try:
        marker = _detail_data_version(work_data)
    except _version.DetailDataVersionError as exc:
        marker = 0
        marker_issues = (_invalid_version_issue(work_meta, str(exc)),)
    pages, discovery_issues = _discover_page_files(work)
    tx_id = _new_transaction_id()
    tx_dir = _transaction_path(work, tx_id, transaction_dir)
    page_plans, inspection_issues = _inspect_pages(pages, inspector, marker)
    folder_manifest, folder_issues = _build_folder_manifest(
        work_data,
        page_plans,
        work_meta,
    )
    all_issues = tuple((
        *marker_issues,
        *discovery_issues,
        *inspection_issues,
        *folder_issues,
    ))
    source_bytes = sum(page.source_size for page in page_plans)
    stage_bytes = sum(page.estimated_output_bytes for page in page_plans)
    required = _capacity.required_capacity(source_bytes, stage_bytes, page_plans, work_meta)
    available = _capacity.available_capacity(tx_dir)
    if marker > MIGRATION_VERSION:
        all_issues += (_future_version_issue(work_meta, marker),)
    if tx_dir.exists():
        all_issues += (_existing_transaction_issue(tx_dir),)
    return MigrationPlan(
        transaction_id=tx_id,
        work_dir=work,
        transaction_dir=tx_dir,
        backup_dir=tx_dir / "backup",
        stage_dir=tx_dir / "stage",
        journal_path=tx_dir / JOURNAL_FILE_NAME,
        work_meta_path=work_meta,
        work_meta_sha256=_sha256(work_meta),
        marker_before=marker,
        pages=page_plans,
        folder_manifest=folder_manifest,
        issues=all_issues,
        source_bytes=source_bytes,
        estimated_stage_bytes=stage_bytes,
        required_bytes=required,
        available_bytes=available,
        created_at=_utc_now(),
    )


def _validated_work_dir(work_dir: str | os.PathLike[str]) -> Path:
    requested = Path(work_dir)
    if requested.is_symlink():
        raise MigrationError(f"シンボリックリンクの作品は移行できません: {requested}")
    work = requested.resolve(strict=True)
    if not work.is_dir():
        raise NotADirectoryError(f"作品フォルダーではありません: {work}")
    meta = work / WORK_META_NAME
    if not meta.is_file() or meta.is_symlink():
        raise FileNotFoundError(f"work.json がありません: {meta}")
    return work


def _discover_page_files(work: Path) -> tuple[list[tuple[str, Path]], tuple[MigrationIssue, ...]]:
    found: list[tuple[str, Path]] = []
    issues: list[MigrationIssue] = []
    for child in sorted(work.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or not _PAGE_ID_RE.fullmatch(child.name):
            continue
        page_file = child / PAGE_BLEND_NAME
        if child.is_symlink() or page_file.is_symlink():
            issues.append(_unsafe_page_path_issue(child.name, page_file))
            continue
        # 新規ページは、初めて「ページを開く」までディレクトリだけが存在する。
        # そのページには変換対象となる旧GP／効果線がまだ無いため、空ページとして
        # 安全に読み飛ばす。存在するのに通常ファイルではない場合だけ停止する。
        if not page_file.exists():
            continue
        if not page_file.is_file():
            issues.append(_invalid_page_file_issue(child.name, page_file))
            continue
        _require_within(page_file, work)
        found.append((child.name, page_file.resolve()))
    return found, tuple(issues)


def _inspect_pages(
    pages: Sequence[tuple[str, Path]],
    inspector: Inspector | None,
    marker: int,
) -> tuple[tuple[PagePlan, ...], tuple[MigrationIssue, ...]]:
    plans: list[PagePlan] = []
    issues: list[MigrationIssue] = []
    for page_id, page_path in pages:
        size = page_path.stat().st_size
        result = _inspection_result(inspector, page_id, page_path, size, marker)
        issues.extend(result.issues)
        plans.append(PagePlan(
            page_id=page_id,
            source_path=page_path,
            source_size=size,
            source_sha256=_sha256(page_path),
            estimated_output_bytes=max(size, int(result.estimated_output_bytes or size)),
            inspection_facts=dict(result.facts),
        ))
    return tuple(plans), tuple(issues)


def _inspection_result(
    inspector: Inspector | None,
    page_id: str,
    page_path: Path,
    size: int,
    marker: int,
) -> PageInspection:
    if inspector is None:
        issue = MigrationIssue(
            code="inspector_required",
            page_id=page_id,
            page_path=str(page_path),
            message="旧作品を検査するページ変換検査がありません",
        )
        return PageInspection(estimated_output_bytes=size, issues=(issue,))
    try:
        return _coerce_inspection(inspector(page_id, page_path), size)
    except Exception as exc:  # 読取専用検査の失敗をページ単位で報告する
        issue = MigrationIssue(
            code="inspection_failed",
            page_id=page_id,
            page_path=str(page_path),
            message=f"ページの事前検査に失敗しました: {exc}",
        )
        return PageInspection(estimated_output_bytes=size, issues=(issue,))


def _coerce_inspection(value: PageInspection | Mapping[str, Any], size: int) -> PageInspection:
    if isinstance(value, PageInspection):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("inspector は PageInspection または mapping を返す必要があります")
    raw_issues = value.get("issues", ())
    issues = tuple(_coerce_issue(item) for item in raw_issues)
    facts = value.get("facts", {})
    if not isinstance(facts, Mapping):
        raise TypeError("inspection facts は mapping である必要があります")
    return PageInspection(
        estimated_output_bytes=max(size, int(value.get("estimated_output_bytes", size))),
        issues=issues,
        facts=dict(facts),
    )


def _coerce_issue(value: MigrationIssue | Mapping[str, Any]) -> MigrationIssue:
    if isinstance(value, MigrationIssue):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("inspection issue は MigrationIssue または mapping が必要です")
    return MigrationIssue(**{key: str(value.get(key, "")) for key in (
        "code", "page_id", "page_path", "message", "raw_uid", "link_group"
    )})


def execute_migration(
    plan: MigrationPlan,
    *,
    confirmed: bool,
    converter: Converter,
    validator: Validator,
    fault_hook: FaultHook | None = None,
    auto_rollback_on_error: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> MigrationResult:
    """確認済み計画を実行し、版マーカーを最後に更新する。"""
    if plan.already_current:
        return MigrationResult("already_current", None, plan.page_count, None)
    if confirmed is not True:
        raise ConfirmationRequired("作品データ移行には明示確認が必要です")
    # 確認前にはロックファイルを含め、作品・退避先へ一切書き込まない。
    with _lock.work_lock(plan.work_dir):
        _validate_executable_plan(plan)
        _validate_plan_sources(plan)
        _capacity.validate_before_writes(plan)
        return _execute_migration_locked(
            plan,
            converter=converter,
            validator=validator,
            fault_hook=fault_hook,
            auto_rollback_on_error=auto_rollback_on_error,
            progress_callback=progress_callback,
        )


def _execute_migration_locked(
    plan: MigrationPlan,
    *,
    converter: Converter,
    validator: Validator,
    fault_hook: FaultHook | None,
    auto_rollback_on_error: bool,
    progress_callback: ProgressCallback | None,
) -> MigrationResult:
    try:
        _emit_progress(
            progress_callback,
            "preparing",
            "phase_started",
            total=plan.page_count,
            message="移行用の退避領域を準備しています",
        )
        journal = _create_transaction(plan)
        _backup_transaction(
            plan, journal, fault_hook, progress_callback=progress_callback
        )
        _stage_transaction(
            plan,
            journal,
            converter,
            validator,
            fault_hook,
            progress_callback=progress_callback,
        )
        _capacity.validate_after_stage(plan, tuple(journal["pages"]))
        _swap_transaction(
            plan, journal, fault_hook, progress_callback=progress_callback
        )
        _validate_installed(
            plan,
            journal,
            validator,
            fault_hook,
            progress_callback=progress_callback,
        )
        _commit_marker(
            plan, journal, fault_hook, progress_callback=progress_callback
        )
    except BaseException as exc:
        _emit_progress(
            progress_callback,
            "migration",
            "failed",
            total=plan.page_count,
            message=str(exc),
        )
        rollback = _handle_execution_failure(
            plan,
            exc,
            auto_rollback_on_error,
            progress_callback=progress_callback,
        )
        raise MigrationExecutionError(str(exc), rollback=rollback) from exc
    _emit_progress(
        progress_callback,
        "committed",
        "completed",
        total=plan.page_count,
        message="全ページの入替えと版情報の更新が完了しました",
    )
    return MigrationResult("committed", plan.journal_path, plan.page_count, plan.backup_dir)


def _validate_executable_plan(plan: MigrationPlan) -> None:
    if plan.issues:
        codes = ", ".join(sorted({issue.code for issue in plan.issues}))
        raise PreflightBlocked(f"事前検査で移行を停止しました: {codes}")
    if not plan.capacity_ok:
        raise PreflightBlocked(
            f"空き容量が不足しています: 必要 {plan.required_bytes} / 空き {plan.available_bytes}"
        )
    if plan.transaction_dir.exists():
        raise PreflightBlocked(f"移行用フォルダーが既にあります: {plan.transaction_dir}")
    if _native_guard.find_pending_native_save_journals(plan.work_dir):
        raise PreflightBlocked("前回のネイティブ保存復旧を完了してから移行してください")
    base = _transaction_base(plan.work_dir)
    if base.is_symlink():
        raise PreflightBlocked("移行用フォルダーがシンボリックリンクです")
    expected = Path(os.path.abspath(base / plan.transaction_id))
    if Path(os.path.abspath(plan.transaction_dir)) != expected:
        raise PreflightBlocked("退避先は作品ごとの正規の移行フォルダーを使用してください")


def _validate_plan_sources(plan: MigrationPlan) -> None:
    if _sha256(plan.work_meta_path) != plan.work_meta_sha256:
        raise PreflightBlocked("確認後に work.json が変更されました。計画を作り直してください")
    for page in plan.pages:
        if not page.source_path.is_file() or _sha256(page.source_path) != page.source_sha256:
            raise PreflightBlocked(
                f"確認後にページが変更されました。計画を作り直してください: {page.page_id}"
            )


def _create_transaction(plan: MigrationPlan) -> dict[str, Any]:
    plan.transaction_dir.mkdir(parents=True, exist_ok=False)
    journal = _journal_from_plan(plan)
    _write_journal(plan.journal_path, journal)
    plan.backup_dir.mkdir(exist_ok=False)
    plan.stage_dir.mkdir(exist_ok=False)
    return journal


def _backup_transaction(
    plan: MigrationPlan,
    journal: dict[str, Any],
    fault_hook: FaultHook | None,
    *,
    progress_callback: ProgressCallback | None,
) -> None:
    work_backup = plan.backup_dir / WORK_META_NAME
    _copy_verified(plan.work_meta_path, work_backup, plan.work_meta_sha256)
    journal["workMetaBackup"] = str(work_backup)
    journal["status"] = "backing_up"
    _write_journal(plan.journal_path, journal)
    _emit_progress(
        progress_callback,
        "backup",
        "phase_started",
        total=plan.page_count,
        message="元ページを退避しています",
    )
    for index, (page, record) in enumerate(zip(plan.pages, journal["pages"]), 1):
        _emit_page_progress(progress_callback, "backup", "page_started", page, index, plan)
        backup = plan.backup_dir / page.page_id / PAGE_BLEND_NAME
        _copy_verified(page.source_path, backup, page.source_sha256)
        record["backupPath"] = str(backup)
        record["state"] = "backed_up"
        _append_event(journal, "backed_up", page.page_id, index)
        _write_journal(plan.journal_path, journal)
        _call_fault(fault_hook, "after_backup", page.page_id, index)
        _emit_page_progress(progress_callback, "backup", "page_completed", page, index, plan)


def _stage_transaction(
    plan: MigrationPlan,
    journal: dict[str, Any],
    converter: Converter,
    validator: Validator,
    fault_hook: FaultHook | None,
    *,
    progress_callback: ProgressCallback | None,
) -> None:
    journal["status"] = "staging"
    _write_journal(plan.journal_path, journal)
    _emit_progress(
        progress_callback,
        "convert",
        "phase_started",
        total=plan.page_count,
        message="新しいページ形式へ変換して検証しています",
    )
    for index, (page, record) in enumerate(zip(plan.pages, journal["pages"]), 1):
        _emit_page_progress(progress_callback, "convert", "page_started", page, index, plan)
        staged = plan.stage_dir / page.page_id / PAGE_BLEND_NAME
        _copy_verified(page.source_path, staged, page.source_sha256)
        task = _conversion_task(plan, page, staged)
        converter(task)
        if not staged.is_file():
            raise MigrationError(f"変換後ページがありません: {page.page_id}")
        _call_validator(validator, page.page_id, staged)
        record["stagePath"] = str(staged)
        record["stagedSha256"] = _sha256(staged)
        record["state"] = "validated"
        _append_event(journal, "staged_and_validated", page.page_id, index)
        _write_journal(plan.journal_path, journal)
        _call_fault(fault_hook, "after_stage", page.page_id, index)
        _emit_page_progress(progress_callback, "convert", "page_completed", page, index, plan)


def _conversion_task(plan: MigrationPlan, page: PagePlan, staged: Path) -> PageConversionTask:
    return PageConversionTask(
        transaction_id=plan.transaction_id,
        page_id=page.page_id,
        original_path=page.source_path,
        staged_path=staged,
        backup_path=plan.backup_dir / page.page_id / PAGE_BLEND_NAME,
        source_sha256=page.source_sha256,
        inspection_facts=page.inspection_facts,
    )


def _swap_transaction(
    plan: MigrationPlan,
    journal: dict[str, Any],
    fault_hook: FaultHook | None,
    *,
    progress_callback: ProgressCallback | None,
) -> None:
    journal["status"] = "swapping"
    _write_journal(plan.journal_path, journal)
    _emit_progress(
        progress_callback,
        "install",
        "phase_started",
        total=plan.page_count,
        message="検証済みページへ入れ替えています",
    )
    for index, (page, record) in enumerate(zip(plan.pages, journal["pages"]), 1):
        _emit_page_progress(progress_callback, "install", "page_started", page, index, plan)
        if _sha256(page.source_path) != page.source_sha256:
            raise MigrationError(f"入替え直前に元ページが変更されました: {page.page_id}")
        staged = Path(record["stagePath"])
        _atomic_install(staged, page.source_path, plan.transaction_id)
        _call_fault(fault_hook, "after_swap_replace", page.page_id, index)
        record["state"] = "swapped"
        _append_event(journal, "swapped", page.page_id, index)
        _write_journal(plan.journal_path, journal)
        _call_fault(fault_hook, "after_swap", page.page_id, index)
        _emit_page_progress(progress_callback, "install", "page_completed", page, index, plan)


def _validate_installed(
    plan: MigrationPlan,
    journal: dict[str, Any],
    validator: Validator,
    fault_hook: FaultHook | None,
    *,
    progress_callback: ProgressCallback | None,
) -> None:
    journal["status"] = "validating_installed"
    _write_journal(plan.journal_path, journal)
    _emit_progress(
        progress_callback,
        "validate_installed",
        "phase_started",
        total=plan.page_count,
        message="入替え後の全ページを再検証しています",
    )
    for index, (page, record) in enumerate(zip(plan.pages, journal["pages"]), 1):
        _emit_page_progress(
            progress_callback,
            "validate_installed",
            "page_started",
            page,
            index,
            plan,
        )
        expected = str(record["stagedSha256"])
        if _sha256(page.source_path) != expected:
            raise MigrationError(f"入替え後のハッシュが一致しません: {page.page_id}")
        _call_validator(validator, page.page_id, page.source_path)
        record["state"] = "installed_validated"
        _append_event(journal, "installed_validated", page.page_id, index)
        _write_journal(plan.journal_path, journal)
        _call_fault(fault_hook, "after_installed_validation", page.page_id, index)
        _emit_page_progress(
            progress_callback,
            "validate_installed",
            "page_completed",
            page,
            index,
            plan,
        )


def _commit_marker(
    plan: MigrationPlan,
    journal: dict[str, Any],
    fault_hook: FaultHook | None,
    *,
    progress_callback: ProgressCallback | None,
) -> None:
    journal["status"] = "marking"
    _emit_progress(
        progress_callback,
        "marking",
        "phase_started",
        total=plan.page_count,
        message="作品データの版情報を更新しています",
    )
    _append_event(journal, "before_marker", "", 0)
    if _sha256(plan.work_meta_path) != plan.work_meta_sha256:
        raise MigrationError("版情報の更新前に work.json が変更されました")
    data = _read_json_mapping(plan.work_meta_path)
    _merge_folder_manifest(data, plan.folder_manifest)
    data[DETAIL_DATA_VERSION_KEY] = MIGRATION_VERSION
    planned = _json_bytes(data)
    journal["workMetaPlannedSha256"] = hashlib.sha256(planned).hexdigest()
    journal["workMetaPlannedSize"] = len(planned)
    _write_journal(plan.journal_path, journal)
    _call_fault(fault_hook, "before_marker", "", 0)
    # fault hook・外部処理を挟んだ後、原子的置換の直前にも再照合する。
    if _sha256(plan.work_meta_path) != plan.work_meta_sha256:
        raise MigrationError("版情報の更新直前に work.json が変更されました")
    _atomic_write_json(plan.work_meta_path, data)
    _call_fault(fault_hook, "after_marker_replace", "", 0)
    journal["status"] = "committed"
    journal["markerWritten"] = True
    _append_event(journal, "committed", "", 0)
    _write_journal(plan.journal_path, journal)
    _emit_progress(
        progress_callback,
        "marking",
        "completed",
        total=plan.page_count,
        message="作品データの版情報を更新しました",
    )


def _handle_execution_failure(
    plan: MigrationPlan,
    exc: BaseException,
    auto_rollback: bool,
    *,
    progress_callback: ProgressCallback | None,
) -> RecoveryResult | None:
    if not plan.journal_path.is_file():
        _emit_progress(
            progress_callback,
            "rollback",
            "not_needed",
            total=plan.page_count,
            message="書込み開始前に停止したため復旧は不要です",
            rollback_status="not_needed",
        )
        return None
    if not auto_rollback:
        journal = _read_json_mapping(plan.journal_path)
        journal["status"] = "interrupted"
        journal["error"] = f"{type(exc).__name__}: {exc}"
        _write_journal(plan.journal_path, journal)
        _emit_progress(
            progress_callback,
            "rollback",
            "interrupted",
            total=plan.page_count,
            message="自動復旧を行わず中断状態を記録しました",
            rollback_status="interrupted",
        )
        return None
    try:
        return recover_transaction(
            plan.journal_path,
            expected_work_dir=plan.work_dir,
            force=True,
            progress_callback=progress_callback,
        )
    except Exception as rollback_exc:
        raise RecoveryError(
            f"移行失敗後の復旧にも失敗しました: {rollback_exc}"
        ) from rollback_exc


def recover_transaction(
    journal_path: str | os.PathLike[str],
    *,
    expected_work_dir: str | os.PathLike[str],
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> RecoveryResult:
    """未完了ジャーナルの全ページと ``work.json`` を退避時点へ戻す。"""
    expected = Path(expected_work_dir).resolve(strict=True)
    with _lock.work_lock(expected):
        return _recover_transaction_locked(
            journal_path,
            expected,
            force=force,
            progress_callback=progress_callback,
        )


def _recover_transaction_locked(
    journal_path: str | os.PathLike[str],
    expected_work_dir: Path,
    *,
    force: bool,
    progress_callback: ProgressCallback | None,
) -> RecoveryResult:
    path = Path(journal_path).resolve(strict=True)
    journal = _read_json_mapping(path)
    # status を見る前に、別作品・偽造パス・未来版をすべて拒否する。
    _validate_journal_paths(path, journal, expected_work_dir)
    status = str(journal.get("status", ""))
    total = len(journal.get("pages", []))
    if status in {"rolled_back", "verified_after_restart"}:
        return RecoveryResult(status, path)
    if status == "committed" and not force:
        return RecoveryResult("committed", path)

    # 全件を先に分類・ハッシュ検証する。不明な変更が1件でもあれば何も戻さない。
    page_actions, work_action = _recovery.classify_rollback(journal, path)
    journal["status"] = "rolling_back"
    _write_journal(path, journal)
    _emit_progress(
        progress_callback, "rollback", "phase_started", total=total,
        message="退避した元ページへ戻しています",
    )
    restored: list[str] = []
    try:
        transaction_id = str(journal["transactionId"])
        for index, action in enumerate(page_actions, 1):
            _emit_progress(
                progress_callback, "rollback", "page_started",
                page_id=action.page_id, index=index, total=total,
                message=f"{action.page_id} を確認しています",
            )
            if _recovery.apply_action(action, transaction_id):
                restored.append(action.page_id)
            _emit_progress(
                progress_callback, "rollback", "page_completed",
                page_id=action.page_id, index=index, total=total,
                message=f"{action.page_id} の復旧を確認しました",
            )
        _recovery.apply_action(work_action, transaction_id)
        _recovery.verify_originals(page_actions, work_action)
    except Exception as exc:
        journal["status"] = "rollback_failed"
        journal["rollbackError"] = f"{type(exc).__name__}: {exc}"
        _write_journal(path, journal)
        raise RecoveryError(str(exc)) from exc
    journal["status"] = "rolled_back"
    journal["restoredPages"] = restored
    _append_event(journal, "rolled_back", "", 0)
    _write_journal(path, journal)
    _emit_progress(
        progress_callback, "rollback", "completed", total=total,
        message=f"元の状態へ戻しました（{len(restored)}ページ）",
        rollback_status="rolled_back",
    )
    return RecoveryResult("rolled_back", path, tuple(restored))


def verify_after_restart(
    journal_path: str | os.PathLike[str],
    *,
    expected_work_dir: str | os.PathLike[str],
    validator: Validator,
    rollback_on_error: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> MigrationResult:
    """Blender再起動後に全ページを再検証し、退避保持状態を確定する。"""
    expected = Path(expected_work_dir).resolve(strict=True)
    with _lock.work_lock(expected):
        return _verify_after_restart_locked(
            journal_path,
            expected,
            validator=validator,
            rollback_on_error=rollback_on_error,
            progress_callback=progress_callback,
        )


def _verify_after_restart_locked(
    journal_path: str | os.PathLike[str],
    expected_work_dir: Path,
    *,
    validator: Validator,
    rollback_on_error: bool,
    progress_callback: ProgressCallback | None,
) -> MigrationResult:
    path = Path(journal_path).resolve(strict=True)
    journal = _read_json_mapping(path)
    _validate_journal_paths(path, journal, expected_work_dir)
    if str(journal.get("status", "")) == "verified_after_restart":
        return _result_from_journal(path, journal, "verified_after_restart")
    if str(journal.get("status", "")) != "committed":
        raise MigrationError("コミット済みの移行だけを再読込検証できます")
    total = len(journal.get("pages", []))
    _emit_progress(
        progress_callback,
        "restart_validation",
        "phase_started",
        total=total,
        message="再読込後の全ページを検証しています",
    )
    try:
        _verify_marker(journal)
        _verify_committed_pages(
            journal,
            validator,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        _emit_progress(
            progress_callback,
            "restart_validation",
            "failed",
            total=total,
            message=str(exc),
        )
        rollback = None
        if rollback_on_error:
            rollback = recover_transaction(
                path,
                expected_work_dir=expected_work_dir,
                force=True,
                progress_callback=progress_callback,
            )
        raise MigrationExecutionError(
            f"再起動後の全ページ検証に失敗しました: {exc}",
            rollback=rollback,
        ) from exc
    journal["status"] = "verified_after_restart"
    journal["restartVerifiedAt"] = _utc_now()
    _append_event(journal, "verified_after_restart", "", 0)
    _write_journal(path, journal)
    _emit_progress(
        progress_callback,
        "restart_validation",
        "completed",
        total=total,
        message="再読込後の全ページ検証が完了しました",
    )
    return _result_from_journal(path, journal, "verified_after_restart")


def _verify_marker(journal: Mapping[str, Any]) -> None:
    data = _read_json_mapping(Path(str(journal["workMetaPath"])))
    if _detail_data_version(data) != int(journal["targetVersion"]):
        raise MigrationError("作品データの版マーカーが一致しません")
    _verify_folder_manifest(data, journal.get("folderManifest", ()))


def _verify_committed_pages(
    journal: Mapping[str, Any],
    validator: Validator,
    *,
    progress_callback: ProgressCallback | None,
) -> None:
    records = tuple(journal.get("pages", []))
    for index, record in enumerate(records, 1):
        page_id = str(record["pageId"])
        _emit_progress(
            progress_callback,
            "restart_validation",
            "page_started",
            page_id=page_id,
            index=index,
            total=len(records),
            message=f"{page_id} を再読込検証しています",
        )
        source = Path(str(record["sourcePath"]))
        expected = str(record.get("stagedSha256", ""))
        if not expected or _sha256(source) != expected:
            raise MigrationError(f"再読込前のページが一致しません: {page_id}")
        _call_validator(validator, page_id, source)
        _emit_progress(
            progress_callback,
            "restart_validation",
            "page_completed",
            page_id=page_id,
            index=index,
            total=len(records),
            message=f"{page_id} の再読込検証が完了しました",
        )


def _call_validator(validator: Validator, page_id: str, path: Path) -> None:
    result = validator(page_id, path)
    if result is False:
        raise MigrationError(f"変換後検証が不合格でした: {page_id}")


def _call_fault(hook: FaultHook | None, event: str, page_id: str, index: int) -> None:
    if hook is not None:
        hook(event, page_id, index)


def _emit_page_progress(
    callback: ProgressCallback | None,
    phase: str,
    event: str,
    page: PagePlan,
    index: int,
    plan: MigrationPlan,
) -> None:
    action = "処理中" if event == "page_started" else "完了"
    _emit_progress(
        callback,
        phase,
        event,
        page_id=page.page_id,
        index=index,
        total=plan.page_count,
        message=f"{page.page_id}（{index}/{plan.page_count}）: {action}",
    )


def _emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    event: str,
    *,
    page_id: str = "",
    index: int = 0,
    total: int = 0,
    message: str = "",
    rollback_status: str = "",
) -> None:
    if callback is None:
        return
    callback(
        MigrationProgress(
            phase=str(phase),
            event=str(event),
            page_id=str(page_id),
            index=max(0, int(index)),
            total=max(0, int(total)),
            message=str(message),
            rollback_status=str(rollback_status),
        )
    )


__all__ = [
    "ConfirmationRequired",
    "MigrationError",
    "MigrationExecutionError",
    "MigrationIssue",
    "MigrationPlan",
    "MigrationProgress",
    "MigrationResult",
    "PageConversionTask",
    "PageInspection",
    "PreflightBlocked",
    "ProgressCallback",
    "RecoveryError",
    "RecoveryResult",
    "build_migration_plan",
    "execute_migration",
    "find_incomplete_journals",
    "recover_transaction",
    "unsupported_gp_mask_issue",
    "unresolved_pointer_issue",
    "verify_after_restart",
]
