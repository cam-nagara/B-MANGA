"""作品移行の段階・ページ・ロールバック通知を純Pythonで検証する。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "io" / "project_content_migration.py"


def _load_module():
    name = "bmanga_project_content_migration_progress_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _make_work(root: Path, name: str, page_count: int = 2) -> Path:
    work = root / f"{name}.bmanga"
    work.mkdir()
    _write_json(work / "work.json", {"detailDataVersion": 0})
    for index in range(1, page_count + 1):
        page = work / f"p{index:04d}" / "page.blend"
        page.parent.mkdir()
        page.write_bytes(f"old-{index}".encode())
    return work


def _plan(module, work: Path):
    return module.build_migration_plan(
        work,
        inspector=lambda _page_id, path: module.PageInspection(
            estimated_output_bytes=path.stat().st_size + 4
        ),
    )


def _convert(task) -> None:
    task.staged_path.write_bytes(task.staged_path.read_bytes() + b"-new")


def _assert_success_progress(module, root: Path) -> None:
    work = _make_work(root, "ProgressSuccess")
    plan = _plan(module, work)
    events = []
    result = module.execute_migration(
        plan,
        confirmed=True,
        converter=_convert,
        validator=lambda _page_id, _path: True,
        progress_callback=events.append,
    )
    module.verify_after_restart(
        result.journal_path,
        expected_work_dir=work,
        validator=lambda _page_id, _path: True,
        progress_callback=events.append,
    )
    phases = {event.phase for event in events}
    assert {
        "preparing",
        "backup",
        "convert",
        "install",
        "validate_installed",
        "marking",
        "committed",
        "restart_validation",
    } <= phases
    for phase in ("backup", "convert", "install", "validate_installed", "restart_validation"):
        completed = [
            event for event in events
            if event.phase == phase and event.event == "page_completed"
        ]
        assert [event.page_id for event in completed] == ["p0001", "p0002"]
        assert [event.index for event in completed] == [1, 2]
        assert all(event.total == 2 for event in completed)
    assert events[-1].phase == "restart_validation"
    assert events[-1].event == "completed"


def _assert_failure_progress(module, root: Path) -> None:
    work = _make_work(root, "ProgressRollback")
    original = {
        page.parent.name: page.read_bytes()
        for page in work.glob("p*/page.blend")
    }
    plan = _plan(module, work)
    events = []

    def _fail(event: str, _page_id: str, index: int) -> None:
        if event == "after_swap_replace" and index == 1:
            raise RuntimeError("generated swap failure")

    try:
        module.execute_migration(
            plan,
            confirmed=True,
            converter=_convert,
            validator=lambda _page_id, _path: True,
            fault_hook=_fail,
            progress_callback=events.append,
        )
    except module.MigrationExecutionError as exc:
        assert exc.rollback is not None
        assert exc.rollback.status == "rolled_back"
    else:
        raise AssertionError("失敗注入がMigrationExecutionErrorになりませんでした")

    assert any(event.event == "failed" for event in events)
    rollback = [event for event in events if event.phase == "rollback"]
    assert rollback[0].event == "phase_started"
    assert rollback[-1].event == "completed"
    assert rollback[-1].rollback_status == "rolled_back"
    assert {
        page.parent.name: page.read_bytes()
        for page in work.glob("p*/page.blend")
    } == original


def main() -> None:
    module = _load_module()
    root = Path(tempfile.mkdtemp(prefix="bmanga_migration_progress_"))
    succeeded = False
    try:
        _assert_success_progress(module, root)
        _assert_failure_progress(module, root)
        succeeded = True
        print("PROJECT_CONTENT_MIGRATION_PROGRESS_OK")
    finally:
        if succeeded:
            shutil.rmtree(root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={root}")


if __name__ == "__main__":
    main()
