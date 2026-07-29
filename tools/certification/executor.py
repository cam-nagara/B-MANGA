"""認定caseを実行し、終了コード・sentinel・skip・成果物を判定する。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .model import Case, Result


RUNNER_VERSION = "phase1-runner-v1"
DEFAULT_BLENDER = Path(
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)
FAILURE_MARKERS = (
    "Traceback (most recent call last):",
    "AssertionError:",
    "Error: Python:",
    "EXCEPTION_ACCESS_VIOLATION",
    "Fatal Python error:",
)
TRACEBACK_MARKER = FAILURE_MARKERS[0]
CRASH_MARKERS = (
    "EXCEPTION_ACCESS_VIOLATION",
    "Fatal Python error:",
    "Writing: C:\\Users\\",
)
WINDOWS_CRASH_CODES = {
    -1073741819,  # 0xC0000005 access violation (signed)
    3221225477,  # 0xC0000005 access violation (unsigned)
    -1073740791,  # 0xC0000409 stack buffer overrun (signed)
    3221226505,  # 0xC0000409 stack buffer overrun (unsigned)
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _case_directory(out: Path, case: Case) -> Path:
    digest = hashlib.sha256(case.test_id.encode("utf-8")).hexdigest()[:16]
    return out / "cases" / digest


def _wrapper_sentinel(case: Case) -> str:
    return f"BMANGA_CERT_CASE_OK {case.test_id}"


def _python_command(
    root: Path,
    case: Case,
    case_dir: Path,
) -> tuple[list[str], Path, dict[str, str]]:
    source = root / case.source
    if case.mode == "python_script":
        return [sys.executable, source.name, *case.args], root / "test", {}
    junit = case_dir / "junit.xml"
    basetemp = case_dir / "pytest_tmp"
    command = [
        sys.executable,
        "-m",
        "pytest",
        source.name,
        "-q",
        "-p",
        "no:cacheprovider",
        f"--basetemp={basetemp}",
        f"--junitxml={junit}",
        *case.args,
    ]
    return command, root / "test", {}


def _blender_command(
    root: Path,
    case: Case,
    blender: Path,
) -> tuple[list[str], Path, dict[str, str]]:
    source = root / case.source
    background = [] if case.mode == "blender_ui" else ["--background"]
    env: dict[str, str] = {}
    if case.mode == "blender_wrapper":
        script = root / "test" / "certification_blender_entry.py"
        env = {
            "BMANGA_CERT_TARGET": str(source),
            "BMANGA_CERT_SENTINEL": _wrapper_sentinel(case),
            "BMANGA_CERT_WRAPPED": "1",
        }
    else:
        script = source
    command = [
        str(blender),
        *background,
        "--factory-startup",
        *case.blender_args,
        "--python",
        str(script),
    ]
    if case.args:
        command.extend(("--", *case.args))
    return command, root, env


def _command(
    root: Path,
    case: Case,
    case_dir: Path,
    blender: Path,
) -> tuple[list[str], Path, dict[str, str]]:
    if case.mode.startswith("python_"):
        return _python_command(root, case, case_dir)
    command, cwd, env = _blender_command(root, case, blender)
    # テスト中のsave_userprefや設定callbackがユーザー実環境を上書きしないよう、
    # Blender caseごとに完全に独立したuser configを割り当てる。
    user_config = case_dir / "blender_user_config"
    user_config.mkdir(parents=True, exist_ok=True)
    env["BLENDER_USER_CONFIG"] = str(user_config)
    return command, cwd, env


def _junit_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    root = ET.parse(path).getroot()
    nodes = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        key: sum(int(node.attrib.get(key, 0)) for node in nodes)
        for key in ("tests", "failures", "errors", "skipped")
    }


def _completion_token(case: Case) -> str:
    if case.mode == "blender_wrapper":
        return _wrapper_sentinel(case)
    return case.completion_token


def _artifact_rows(root: Path, specs: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    rows = []
    for spec in specs:
        path = root / str(spec["path"])
        exists = path.is_file()
        sha256 = file_sha256(path) if exists else ""
        expected = str(spec.get("sha256", ""))
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "exists": exists,
                "sha256": sha256,
                "matches": exists and (not expected or sha256 == expected),
            }
        )
    return rows


_EXCEPTION_TERMINAL = re.compile(
    r"^(?:[\w.]+\.)?[A-Za-z_]\w*(?::(?: .*)?)?$"
)
_TRACEBACK_FRAME = re.compile(r"^\s+File [\"']")
_TRACEBACK_OUTPUT_BOUNDARY = re.compile(
    r"^(?:\[|Blender |\d{2}:\d{2}\.)"
)


def _traceback_terminals(
    output: str,
    *,
    completion_token: str = "",
) -> tuple[list[str], list[str]]:
    """Tracebackごとの終端例外行を抽出する。

    期待文言を出力全文から探すと、未知例外の後に同じ文言を通常ログへ
    出すだけで認定を通せる。各marker内の最後の実frameより後にある
    非インデント終端だけを候補にし、独自例外名も含めて一意性を要求する。
    """

    lines = output.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if TRACEBACK_MARKER in line
    ]
    terminals: list[str] = []
    errors: list[str] = []
    for block_index, start in enumerate(starts):
        stop = starts[block_index + 1] if block_index + 1 < len(starts) else len(lines)
        frame_indexes = [
            index
            for index in range(start + 1, stop)
            if _TRACEBACK_FRAME.match(lines[index])
        ]
        if not frame_indexes:
            errors.append(f"traceback block {block_index + 1} has no stack frame")
            continue
        candidates: list[str] = []
        for raw_line in lines[frame_indexes[-1] + 1:stop]:
            if raw_line[:1].isspace():
                continue
            candidate = raw_line.strip()
            if not candidate:
                continue
            if completion_token and candidate == completion_token:
                break
            if _TRACEBACK_OUTPUT_BOUNDARY.match(candidate):
                break
            if _EXCEPTION_TERMINAL.fullmatch(candidate):
                candidates.append(candidate)
        if len(candidates) == 1:
            terminals.append(candidates[0])
        elif candidates:
            errors.append(
                f"traceback block {block_index + 1} has ambiguous terminals: "
                + " | ".join(candidates)
            )
        else:
            errors.append(f"traceback block {block_index + 1} has no terminal exception")
    return terminals, errors


def _status(
    case: Case,
    returncode: int,
    output: str,
    counts: dict[str, int],
    artifacts: list[dict[str, Any]],
) -> tuple[str, str]:
    if returncode in WINDOWS_CRASH_CODES or any(
        marker in output for marker in CRASH_MARKERS
    ):
        return "crash", f"process crash detected (returncode {returncode})"
    if returncode != 0:
        return "failed", f"returncode {returncode}"
    traceback_count = output.count(TRACEBACK_MARKER)
    if case.expected_tracebacks:
        if case.mode != "blender_wrapper":
            return "failed", "expected tracebacks require blender_wrapper"
        terminals, parse_errors = _traceback_terminals(
            output,
            completion_token=_completion_token(case),
        )
        mismatches = list(parse_errors)
        expected_counts = [
            int(expected.get("count", 0) or 0)
            for expected in case.expected_tracebacks
        ]
        expected_total = sum(expected_counts)
        if traceback_count != expected_total:
            mismatches.append(f"traceback count {traceback_count} != {expected_total}")
        if len(terminals) != traceback_count:
            mismatches.append(
                f"parsed traceback terminals {len(terminals)} != {traceback_count}"
            )
        actual_counts = [0] * len(case.expected_tracebacks)
        for terminal in terminals:
            matching = [
                index
                for index, expected in enumerate(case.expected_tracebacks)
                if re.fullmatch(str(expected.get("pattern", "")), terminal)
            ]
            if not matching:
                mismatches.append(f"unexpected traceback terminal: {terminal}")
            elif len(matching) > 1:
                mismatches.append(f"ambiguous traceback terminal: {terminal}")
            else:
                actual_counts[matching[0]] += 1
        for index, expected in enumerate(case.expected_tracebacks):
            pattern = str(expected.get("pattern", ""))
            wanted = expected_counts[index]
            actual = actual_counts[index]
            if actual != wanted:
                mismatches.append(f"traceback pattern {pattern!r}: {actual} != {wanted}")
        if mismatches:
            return "failed", "; ".join(mismatches)
    elif traceback_count:
        return "failed", f"unexpected traceback count {traceback_count}"
    disallowed_markers = [
        marker
        for marker in FAILURE_MARKERS
        if marker != TRACEBACK_MARKER and marker in output
    ]
    if disallowed_markers:
        return "failed", "failure marker detected"
    if case.mode == "python_pytest":
        if not counts or counts.get("tests", 0) < 1:
            return "failed", "pytest collected no items"
        if counts.get("failures", 0) or counts.get("errors", 0):
            return "failed", "pytest failures/errors"
        if counts.get("skipped", 0):
            return "unexpected_skip", f"pytest skipped {counts['skipped']} item(s)"
    else:
        token = _completion_token(case)
        if token and token not in output:
            return "failed", f"completion token missing: {token}"
    if any(not row["matches"] for row in artifacts):
        return "failed", "artifact contract mismatch"
    return "passed", ""


def _write_logs(case_dir: Path, stdout: str, stderr: str) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (case_dir / "stderr.txt").write_text(stderr, encoding="utf-8")


def _non_executed(case: Case, source_hash: str) -> Result:
    return Result(
        test_id=case.test_id,
        source=case.source,
        mode=case.mode,
        required=False,
        status=case.mode,
        seconds=0.0,
        source_sha256=source_hash,
        reason=case.reason,
    )


def _write_result(case_dir: Path, result: Result) -> Result:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def run_case(
    root: Path,
    out: Path,
    case: Case,
    blender: Path = DEFAULT_BLENDER,
) -> Result:
    source = root / case.source
    source_hash = file_sha256(source)
    case_dir = _case_directory(out, case)
    if case.mode in {"support", "historical"}:
        return _write_result(case_dir, _non_executed(case, source_hash))
    case_dir.mkdir(parents=True, exist_ok=True)
    command, cwd, extra_env = _command(root, case, case_dir, blender)
    env = os.environ.copy()
    env.update(extra_env)
    started = time.perf_counter()
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=case.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")
        _write_logs(case_dir, stdout, stderr)
        return _write_result(
            case_dir,
            Result(
                case.test_id,
                case.source,
                case.mode,
                case.required,
                "timeout",
                round(time.perf_counter() - started, 3),
                source_hash,
                command,
                reason=f"timeout after {case.timeout_seconds}s",
            ),
        )
    stdout, stderr = done.stdout or "", done.stderr or ""
    _write_logs(case_dir, stdout, stderr)
    counts = _junit_counts(case_dir / "junit.xml")
    artifacts = _artifact_rows(root, case.artifacts)
    status, reason = _status(
        case,
        done.returncode,
        f"{stdout}\n{stderr}",
        counts,
        artifacts,
    )
    result = Result(
        case.test_id,
        case.source,
        case.mode,
        case.required,
        status,
        round(time.perf_counter() - started, 3),
        source_hash,
        command,
        done.returncode,
        reason,
        counts,
        artifacts,
    )
    return _write_result(case_dir, result)
