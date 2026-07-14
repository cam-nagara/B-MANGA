from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "io" / "project_content_migration.py"
    name = "_bmanga_project_content_migration_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_module()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _new_work(tmp_path: Path) -> Path:
    work = tmp_path / "BlankPages.bmanga"
    work.mkdir()
    _write_json(work / "work.json", {"schemaVersion": 9, "detailDataVersion": 0})
    _write_json(
        work / "pages.json",
        {"pages": [{"id": "p0001"}, {"id": "p0002"}]},
    )
    return work


def test_pages_not_opened_yet_do_not_block_the_work_migration(tmp_path):
    work = _new_work(tmp_path)
    (work / "p0001").mkdir()

    plan = MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, _path: MIGRATION.PageInspection(),
        transaction_dir=tmp_path / "transaction",
    )

    assert plan.page_count == 0
    assert plan.issues == ()


def test_existing_page_blends_are_still_the_only_conversion_targets(tmp_path):
    work = _new_work(tmp_path)
    page_dir = work / "p0001"
    page_dir.mkdir()
    page_file = page_dir / "page.blend"
    page_file.write_bytes(b"placeholder")

    plan = MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, path: MIGRATION.PageInspection(
            estimated_output_bytes=path.stat().st_size,
        ),
        transaction_dir=tmp_path / "transaction",
    )

    assert [page.page_id for page in plan.pages] == ["p0001"]
    assert plan.issues == ()


def test_non_file_page_blend_path_blocks_before_writing(tmp_path):
    work = _new_work(tmp_path)
    invalid = work / "p0001" / "page.blend"
    invalid.mkdir(parents=True)

    plan = MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, _path: MIGRATION.PageInspection(),
        transaction_dir=tmp_path / "transaction",
    )

    assert [issue.code for issue in plan.issues] == ["invalid_page_blend"]


def test_crash_after_marker_write_is_discovered_and_fully_rolled_back(tmp_path):
    work = _new_work(tmp_path)
    page = work / "p0001" / "page.blend"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"old-page")
    original_work = (work / "work.json").read_bytes()

    plan = MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, path: MIGRATION.PageInspection(
            estimated_output_bytes=path.stat().st_size + 4,
        ),
    )

    def convert(task):
        task.staged_path.write_bytes(task.staged_path.read_bytes() + b"-new")

    def crash_after_marker(event, _page_id, _index):
        if event == "after_marker_replace":
            raise SystemExit("simulated power loss")

    with pytest.raises(MIGRATION.MigrationExecutionError):
        MIGRATION.execute_migration(
            plan,
            confirmed=True,
            converter=convert,
            validator=lambda _page_id, _path: True,
            fault_hook=crash_after_marker,
            auto_rollback_on_error=False,
        )

    assert json.loads((work / "work.json").read_text(encoding="utf-8"))["detailDataVersion"] == 1
    pending = MIGRATION.find_incomplete_journals(work)
    assert pending == (plan.journal_path,)

    result = MIGRATION.recover_transaction(
        pending[0], expected_work_dir=work, force=True
    )

    assert result.status == "rolled_back"
    assert page.read_bytes() == b"old-page"
    assert (work / "work.json").read_bytes() == original_work


def test_restart_validation_failure_reports_successful_rollback(tmp_path):
    work = _new_work(tmp_path)
    page = work / "p0001" / "page.blend"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"old-page")
    original_work = (work / "work.json").read_bytes()
    plan = MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, path: MIGRATION.PageInspection(
            estimated_output_bytes=path.stat().st_size + 4,
        ),
    )

    def convert(task):
        task.staged_path.write_bytes(task.staged_path.read_bytes() + b"-new")

    result = MIGRATION.execute_migration(
        plan,
        confirmed=True,
        converter=convert,
        validator=lambda _page_id, _path: True,
    )
    with pytest.raises(MIGRATION.MigrationExecutionError) as caught:
        MIGRATION.verify_after_restart(
            result.journal_path,
            expected_work_dir=work,
            validator=lambda _page_id, _path: False,
            rollback_on_error=True,
        )

    assert caught.value.rollback is not None
    assert caught.value.rollback.status == "rolled_back"
    assert page.read_bytes() == b"old-page"
    assert (work / "work.json").read_bytes() == original_work


def test_work_marker_does_not_hide_one_restored_legacy_page(tmp_path):
    work = _new_work(tmp_path)
    _write_json(work / "work.json", {"schemaVersion": 9, "detailDataVersion": 1})
    for page_id in ("p0001", "p0002"):
        page = work / page_id / "page.blend"
        page.parent.mkdir(parents=True)
        page.write_bytes(page_id.encode())

    inspected: list[str] = []

    def inspect(page_id, path):
        inspected.append(page_id)
        version = 0 if page_id == "p0002" else 1
        return MIGRATION.PageInspection(
            estimated_output_bytes=path.stat().st_size,
            facts={"pageDetailDataVersion": version},
        )

    plan = MIGRATION.build_migration_plan(work, inspector=inspect)

    assert inspected == ["p0001", "p0002"]
    assert plan.marker_before == 1
    assert plan.already_current is False


def test_current_stamps_do_not_hide_inspection_issue(tmp_path):
    work = _new_work(tmp_path)
    _write_json(work / "work.json", {"detailDataVersion": 1})
    page = work / "p0001" / "page.blend"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"current-but-invalid")
    issue = MIGRATION.MigrationIssue(
        code="legacy_structure_remains",
        page_id="p0001",
        page_path=str(page),
        message="旧構造が残っています",
    )
    plan = MIGRATION.build_migration_plan(
        work,
        inspector=lambda _page_id, path: MIGRATION.PageInspection(
            estimated_output_bytes=path.stat().st_size,
            facts={"pageDetailDataVersion": 1},
            issues=(issue,),
        ),
    )

    assert plan.already_current is False
    with pytest.raises(MIGRATION.PreflightBlocked):
        MIGRATION.execute_migration(
            plan,
            confirmed=True,
            converter=lambda _task: None,
            validator=lambda _page_id, _path: True,
        )
