"""Phase 0用: 通常Pythonテストをファイル単位のpytestで全実行する。"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from tools.refactor_certification.ids import test_id
from tools.refactor_certification.test_scan import python_test_files


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


def _python_tests(root: Path) -> list[Path]:
    return python_test_files(root)


def _xml_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {}
    return {
        key: int(suite.attrib.get(key, "0"))
        for key in ("tests", "failures", "errors", "skipped")
    }


def _status(returncode: int, counts: dict[str, int], output: str) -> tuple[str, str]:
    if returncode == 0 and counts.get("tests", 0) > 0:
        if counts.get("skipped", 0) == counts["tests"]:
            return "skipped", "全test itemがskip"
        return "passed", ""
    if returncode == 0:
        return "no_tests", "pytestがtest itemを収集しなかった"
    if counts.get("errors", 0) > 0 or "ERROR collecting" in output:
        return "collection_error", "pytest収集/importエラー"
    return "failed", f"pytest returncode {returncode}"


def _is_script(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    has_pytest = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
    has_main = "__main__" in path.read_text(encoding="utf-8-sig")
    return has_main and not has_pytest


def _command(root: Path, path: Path, junit: Path) -> tuple[list[str], str]:
    if _is_script(path):
        return [sys.executable, path.name], "python_script"
    return (
        [sys.executable, "-m", "pytest", path.name, "-q", f"--junitxml={junit}"],
        "pytest_module",
    )


def _script_status(returncode: int, output: str) -> tuple[str, str, dict[str, int]]:
    if returncode == 0 and "_OK" in output:
        return "passed", "", {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    if returncode == 0:
        return "no_tests", "script完了sentinelがない", {}
    return "failed", f"python script returncode {returncode}", {}


def _write_logs(case_dir: Path, stdout: str, stderr: str) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (case_dir / "stderr.txt").write_text(stderr, encoding="utf-8")


def _run_one(root: Path, out: Path, path: Path, timeout: int) -> dict[str, Any]:
    source = path.relative_to(root).as_posix()
    case_dir = out / "cases" / path.stem
    junit = case_dir / "junit.xml"
    case_dir.mkdir(parents=True, exist_ok=True)
    command, runner = _command(root, path, junit)
    started = time.perf_counter()
    try:
        done = subprocess.run(
            command,
            cwd=root / "test",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _write_logs(case_dir, str(exc.stdout or ""), str(exc.stderr or ""))
        return _result(source, runner, "timeout", "timeout", started, command)
    stdout, stderr = done.stdout or "", done.stderr or ""
    _write_logs(case_dir, stdout, stderr)
    output = f"{stdout}\n{stderr}"
    if runner == "python_script":
        status, reason, counts = _script_status(done.returncode, output)
    else:
        counts = _xml_counts(junit)
        status, reason = _status(done.returncode, counts, output)
    return _result(
        source, runner, status, reason, started, command, done.returncode, counts
    )


def _result(
    source: str,
    runner: str,
    status: str,
    reason: str,
    started: float,
    command: list[str],
    returncode: int | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "test_id": test_id(source),
        "script": source,
        "runner": runner,
        "command": command,
        "status": status,
        "reason": reason,
        "returncode": returncode,
        "counts": counts or {},
        "seconds": round(time.perf_counter() - started, 3),
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for result in results:
        key = str(result["status"])
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "discovered_modules": len(results),
        "recorded_modules": len(results),
        "collected_test_items": sum(row["counts"].get("tests", 0) for row in results),
        "statuses": dict(sorted(statuses.items())),
    }


def main() -> int:
    args = _arguments()
    root, out = args.root.resolve(), args.out.resolve()
    results = [_run_one(root, out, path, args.timeout) for path in _python_tests(root)]
    payload = {
        "schema_version": 1,
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": _summary(results),
        "results": results,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "python_probe_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("PHASE0_PYTHON_PROBE_DONE " + json.dumps(payload["summary"]), flush=True)
    bad = {"timeout", "collection_error", "failed", "no_tests"}
    return 1 if any(row["status"] in bad for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
