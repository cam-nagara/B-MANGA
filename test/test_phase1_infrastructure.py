from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bmanga_core.faults import (
    FaultInjectedError,
    FaultPoint,
    arm_fault,
    check_fault,
    configure_faults_from_environment,
    fault_snapshot,
    isolated_faults,
    reset_faults,
)
from bmanga_core.file_transaction import staged_export_write
from bmanga_core.observability import (
    observability_snapshot,
    observed_operation,
    operation_span,
    reset_observability,
    set_event_sink,
)
from tools.certification.executor import _command, _status
from tools.certification import cli as certification_cli
from tools.certification.golden import approve, propose, verify
from tools.certification.manifest import validate_manifest
from tools.certification.model import Case, Result
from tools.certification.summary import build_summary


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(path: Path, **updates) -> Case:
    values = {
        "test_id": "test:test.sample",
        "source": "test/sample.py",
        "source_sha256": _hash(path),
        "mode": "python_script",
        "required": True,
        "timeout_seconds": 10,
        "completion_token": "SAMPLE_OK",
    }
    values.update(updates)
    return Case(**values)


def test_blender_cases_use_case_local_user_preferences(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    case = _case(source, mode="blender_headless")
    first_dir = tmp_path / "case-a"
    second_dir = tmp_path / "case-b"
    _, _, first_env = _command(
        ROOT,
        case,
        first_dir,
        Path("blender.exe"),
    )
    _, _, second_env = _command(
        ROOT,
        case,
        second_dir,
        Path("blender.exe"),
    )
    assert first_env["BLENDER_USER_CONFIG"] == str(
        first_dir / "blender_user_config"
    )
    assert second_env["BLENDER_USER_CONFIG"] == str(
        second_dir / "blender_user_config"
    )
    assert first_env["BLENDER_USER_CONFIG"] != second_env["BLENDER_USER_CONFIG"]
    assert Path(first_env["BLENDER_USER_CONFIG"]).is_dir()


def test_manifest_rejects_unregistered_and_changed_sources(tmp_path: Path):
    test_dir = tmp_path / "test"
    test_dir.mkdir()
    source = test_dir / "sample.py"
    source.write_text("print('SAMPLE_OK')\n", encoding="utf-8")
    case = _case(source)
    validate_manifest(tmp_path, [case])
    (test_dir / "unregistered.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unregistered test sources"):
        validate_manifest(tmp_path, [case])
    (test_dir / "unregistered.py").unlink()
    source.write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source hash differs"):
        validate_manifest(tmp_path, [case])


@pytest.mark.parametrize(
    ("returncode", "output", "counts", "expected"),
    [
        (0, "SAMPLE_OK", {}, "passed"),
        (1, "SAMPLE_OK", {}, "failed"),
        (0, "Traceback (most recent call last):", {}, "failed"),
        (0, "", {}, "failed"),
    ],
)
def test_runner_requires_normal_exit_and_sentinel(
    tmp_path: Path,
    returncode: int,
    output: str,
    counts: dict[str, int],
    expected: str,
):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    case = _case(source)
    status, _ = _status(case, returncode, output, counts, [])
    assert status == expected


def test_runner_rejects_pytest_skip(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    case = _case(
        source,
        mode="python_pytest",
        completion_token="",
    )
    status, _ = _status(
        case,
        0,
        "",
        {"tests": 2, "failures": 0, "errors": 0, "skipped": 1},
        [],
    )
    assert status == "unexpected_skip"


def test_runner_accepts_only_reviewed_expected_traceback_contract(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    reviewed = _case(
        source,
        mode="blender_wrapper",
        reason="expected injection",
        review="review-1",
        expected_tracebacks=(
            {
                "pattern": r"^RuntimeError: expected injection$",
                "count": 1,
            },
        ),
    )
    wrapper_token = f"BMANGA_CERT_CASE_OK {reviewed.test_id}"
    status, _ = _status(
        reviewed,
        0,
        (
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 1\n"
            "RuntimeError: expected injection\n"
            f"{wrapper_token}"
        ),
        {},
        [],
    )
    assert status == "passed"
    status, _ = _status(
        reviewed,
        0,
        (
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 1\n"
            "RuntimeError: expected injection\n"
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 2\n"
            "KeyError: unexpected\n"
            f"{wrapper_token}"
        ),
        {},
        [],
    )
    assert status == "failed"
    spoofed = _case(
        source,
        mode="blender_wrapper",
        reason="expected injection",
        review="review-1",
        expected_tracebacks=(
            {
                "pattern": r"^RuntimeError: expected injection$",
                "count": 2,
            },
        ),
    )
    status, reason = _status(
        spoofed,
        0,
        (
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 1\n"
            "RuntimeError: expected injection\n"
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 2\n"
            "KeyError: unexpected\n"
            "RuntimeError: expected injection\n"
            f"{wrapper_token}"
        ),
        {},
        [],
    )
    assert status == "failed"
    assert "ambiguous terminals" in reason
    direct_case = _case(
        source,
        reason="expected injection",
        review="review-1",
        expected_tracebacks=reviewed.expected_tracebacks,
    )
    status, reason = _status(
        direct_case,
        0,
        (
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 1\n"
            "RuntimeError: expected injection\n"
            "SAMPLE_OK"
        ),
        {},
        [],
    )
    assert status == "failed"
    assert reason == "expected tracebacks require blender_wrapper"
    status, reason = _status(
        spoofed,
        0,
        (
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 1\n"
            "RuntimeError: expected injection\n"
            "KeyError: unexpected\n"
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 2\n"
            "RuntimeError: expected injection\n"
            f"{wrapper_token}"
        ),
        {},
        [],
    )
    assert status == "failed"
    assert "ambiguous terminals" in reason
    status, reason = _status(
        reviewed,
        0,
        (
            "Traceback (most recent call last):\n"
            "RuntimeError: expected injection\n"
            "  File \"sample.py\", line 1\n"
            "__main__.Boom: unexpected\n"
            f"{wrapper_token}"
        ),
        {},
        [],
    )
    assert status == "failed"
    assert "unexpected traceback terminal: __main__.Boom: unexpected" in reason
    status, reason = _status(
        reviewed,
        0,
        (
            "Traceback (most recent call last):\n"
            "  File \"sample.py\", line 1\n"
            "RuntimeError: expected injection\n"
            "Info: unexpected\n"
            f"{wrapper_token}"
        ),
        {},
        [],
    )
    assert status == "failed"
    assert "ambiguous terminals" in reason


def test_summary_fails_missing_and_required_failure(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    case = _case(source)
    assert not build_summary([case], [])["gate_pass"]
    result = Result(
        case.test_id,
        case.source,
        case.mode,
        True,
        "failed",
        0.1,
        case.source_sha256,
    )
    assert not build_summary([case], [result])["gate_pass"]


def test_faults_are_deterministic_and_counted():
    with isolated_faults():
        arm_fault(FaultPoint.JSON_WRITE, times=2)
        with pytest.raises(FaultInjectedError):
            check_fault(FaultPoint.JSON_WRITE, path="one.json")
        with pytest.raises(FaultInjectedError):
            check_fault(FaultPoint.JSON_WRITE, path="two.json")
        check_fault(FaultPoint.JSON_WRITE, path="three.json")
        snapshot = fault_snapshot()
        assert snapshot["hits"][FaultPoint.JSON_WRITE.value] == 3
        assert snapshot["injections"][FaultPoint.JSON_WRITE.value] == 2
        assert FaultPoint.JSON_WRITE.value not in snapshot["armed"]


def test_environment_faults_require_explicit_process_opt_in(monkeypatch):
    reset_faults()
    monkeypatch.setenv("BMANGA_FAULT_POINTS", FaultPoint.JSON_READ.value)
    monkeypatch.delenv("BMANGA_ENABLE_FAULT_INJECTION", raising=False)
    configure_faults_from_environment()
    assert fault_snapshot()["armed"] == {}

    monkeypatch.setenv("BMANGA_ENABLE_FAULT_INJECTION", "1")
    configure_faults_from_environment()
    assert fault_snapshot()["armed"] == {FaultPoint.JSON_READ.value: 1}
    reset_faults()


@pytest.mark.parametrize(
    "point",
    (
        FaultPoint.EXPORT_WRITE_AFTER_STAGE,
        FaultPoint.EXPORT_WRITE_AFTER_COMMIT,
    ),
)
def test_staged_export_restores_original_after_partial_failure(
    tmp_path: Path,
    point: FaultPoint,
):
    target = tmp_path / "page.png"
    target.write_bytes(b"original")
    with isolated_faults():
        arm_fault(point)
        with pytest.raises(FaultInjectedError):
            with staged_export_write(target, image_format="png") as staged:
                staged.write_bytes(b"replacement")
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.bmanga-*-*"))


def test_observability_records_success_failure_and_operation_id():
    reset_observability()
    events: list[dict[str, object]] = []
    previous = set_event_sink(events.append)
    try:
        with operation_span(
            "json.write",
            operation_id="operation-1",
            target_uid="page-1",
            transaction_phase="checkpoint",
            dirty_reason="text_changed",
            cache_status="miss",
        ):
            pass
        with pytest.raises(RuntimeError):
            with operation_span("json.write", operation_id="operation-2"):
                raise RuntimeError("expected")
    finally:
        set_event_sink(previous)
    snapshot = observability_snapshot()
    assert snapshot["counters"] == {
        "json.write.attempt": 2,
        "json.write.failure": 1,
        "json.write.success": 1,
    }
    finished = [event for event in events if event["event"] == "operation_finished"]
    assert [event["operation_id"] for event in finished] == [
        "operation-1",
        "operation-2",
    ]
    assert [event["outcome"] for event in finished] == ["success", "failure"]
    assert finished[0]["transaction_phase"] == "checkpoint"


def test_observed_operation_can_classify_false_result_as_failure():
    reset_observability()

    @observed_operation("open.mainfile", failure_result=lambda result: result is False)
    def _open_result(succeeded: bool) -> bool:
        return succeeded

    assert _open_result(True)
    assert not _open_result(False)
    counters = observability_snapshot()["counters"]
    assert counters == {
        "open.mainfile.attempt": 2,
        "open.mainfile.failure": 1,
        "open.mainfile.success": 1,
    }


def test_blender_wrapper_rejects_system_exit_zero(tmp_path: Path):
    target = tmp_path / "early_exit.py"
    target.write_text("raise SystemExit(0)\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "BMANGA_CERT_TARGET": str(target),
            "BMANGA_CERT_SENTINEL": "BMANGA_CERT_CASE_OK invalid-early-exit",
            "BMANGA_CERT_WRAPPED": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "test" / "certification_blender_entry.py")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode != 0
    assert '"event": "case_failed"' in completed.stdout
    assert '"event": "case_completed"' not in completed.stdout
    assert "BMANGA_CERT_CASE_OK invalid-early-exit" not in completed.stdout


def test_certification_serializes_all_blender_after_parallel_cases(
    tmp_path: Path,
    monkeypatch,
):
    active = {"python": 0, "blender": 0}
    overlap: list[str] = []
    blender_order: list[str] = []
    lock = threading.Lock()

    def _fake_run_case(_root, _out, case, _blender):
        kind = "blender" if case.mode.startswith("blender_") else "python"
        with lock:
            if kind == "blender" and (active["python"] or active["blender"]):
                overlap.append(case.source)
            if kind == "blender":
                blender_order.append(case.source)
            active[kind] += 1
        time.sleep(0.02)
        with lock:
            active[kind] -= 1
        return Result(
            case.test_id,
            case.source,
            case.mode,
            True,
            "passed",
            0.02,
            case.source_sha256,
        )

    monkeypatch.setattr(certification_cli, "run_case", _fake_run_case)
    for name in (
        "python-a.py",
        "python-b.py",
        "headless.py",
        "wrapper.py",
        "ui.py",
    ):
        (tmp_path / name).write_text("pass\n", encoding="utf-8")
    cases = [
        _case(tmp_path / "python-a.py", mode="python_script"),
        _case(tmp_path / "python-b.py", mode="python_pytest"),
        _case(
            tmp_path / "headless.py",
            mode="blender_headless",
            source="test/headless.py",
            run_order=100,
        ),
        _case(tmp_path / "wrapper.py", mode="blender_wrapper"),
        _case(
            tmp_path / "ui.py",
            mode="blender_ui",
            source="test/ui.py",
            run_order=0,
        ),
    ]
    results = certification_cli._run_all(
        tmp_path,
        tmp_path / "out",
        cases,
        Path("blender"),
        jobs=4,
    )
    assert len(results) == 5
    assert overlap == []
    assert blender_order == ["test/ui.py", "test/headless.py", "test/sample.py"]


def test_golden_requires_separate_approval_and_detects_changes(tmp_path: Path):
    artifact = tmp_path / "image.png"
    artifact.write_bytes(b"approved")
    proposal = propose(
        tmp_path,
        ["image.png"],
        requested_by="certification-runner",
        created_at="2026-07-29T00:00:00+09:00",
    )
    assert proposal["status"] == "pending"
    assert verify(tmp_path, proposal) == [
        "golden registry is not approved",
        "golden registry has no approval record",
    ]
    with pytest.raises(ValueError, match="approval_id"):
        approve(tmp_path, proposal, approval_id="", approved_at="2026-07-29")
    registry = approve(
        tmp_path,
        proposal,
        approval_id="user-approval-2026-07-29",
        approved_at="2026-07-29",
    )
    assert registry["status"] == "approved"
    assert verify(tmp_path, registry) == []
    artifact.write_bytes(b"changed")
    assert verify(tmp_path, registry) == [
        "golden size differs: image.png",
        "golden hash differs: image.png",
    ]
