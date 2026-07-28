from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.refactor_certification.phase0_result_classification import (
    _assert_coverage,
    _classify,
    _python_category,
)
from tools.refactor_certification.ids import test_id as stable_test_id
from tools.refactor_certification.phase0_python_test_probe import _status


def _result(status: str, returncode: int = 1) -> dict[str, object]:
    return {"status": status, "returncode": returncode, "reason": status}


def test_classifies_ui_and_external_cases_before_assertions() -> None:
    category, _evidence = _classify(
        _result("failed"),
        "",
        "RuntimeError: この検証はBlender通常画面で実行してください",
    )
    assert category == "ui_required"
    category, _evidence = _classify(
        _result("failed"),
        "",
        r"FileNotFoundError: D:\TM Dropbox\fixture.blend",
    )
    assert category == "external_fixture"


def test_classifies_expected_traceback_and_silent_failure() -> None:
    category, _evidence = _classify(
        _result("failed", returncode=0),
        "BMANGA_FAULT_INJECTION_OK",
        "Traceback (most recent call last): expected",
    )
    assert category == "expected_traceback_marker"
    category, _evidence = _classify(
        _result("failed", returncode=0),
        "",
        "Traceback (most recent call last): hidden",
    )
    assert category == "silent_failure"


def test_requires_completion_sentinel_for_clean_exit() -> None:
    category, evidence = _classify(
        _result("passed", returncode=0),
        "Blender quit normally",
        "",
    )
    assert category == "missing_sentinel"
    assert "合格を証明できない" in evidence
    category, _evidence = _classify(
        _result("passed", returncode=0),
        "BMANGA_CLEAN_EXIT_OK",
        "",
    )
    assert category == "baseline_pass"


def test_classifies_behavior_mismatch() -> None:
    category, evidence = _classify(
        _result("failed"),
        "",
        "AssertionError: expected layer was missing",
    )
    assert category == "behavior_mismatch"
    assert evidence == "expected layer was missing"


def test_classifies_python_collection_and_empty_collection_as_failure() -> None:
    category, _ = _python_category({"status": "collection_error"})
    assert category == "python_collection_error"
    category, _ = _python_category({"status": "no_tests"})
    assert category == "python_no_tests"
    category, _ = _python_category({"status": "passed"})
    assert category == "python_pass"


def test_coverage_rejects_duplicate_results(tmp_path: Path) -> None:
    test_dir = tmp_path / "test"
    test_dir.mkdir()
    (test_dir / "test_one.py").write_text("def test_one(): pass\n", encoding="utf-8")
    source = "test/test_one.py"
    row = {"test_id": stable_test_id(source), "script": source}
    try:
        _assert_coverage(tmp_path, [row, row], 1)
    except ValueError as exc:
        assert "coverage mismatch" in str(exc)
    else:
        raise AssertionError("duplicate results were accepted")


def test_python_probe_requires_items_and_reports_collection_errors() -> None:
    status, _ = _status(0, {"tests": 0}, "")
    assert status == "no_tests"
    status, _ = _status(2, {}, "ERROR collecting test_bad.py")
    assert status == "collection_error"
    status, _ = _status(0, {"tests": 3, "skipped": 3}, "")
    assert status == "skipped"
