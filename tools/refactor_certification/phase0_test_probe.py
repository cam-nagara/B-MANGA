"""Phase 0用: 既存Blender検査を全自動発見して現状を分類する。

これは最終認定ランナーではない。Phase 1でsentinel、必須依存、UI実行、
artifact検査を厳密化する前に、現行381本の赤・timeout・crashを把握する
ための再開可能なprobeである。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.refactor_certification.test_scan import blender_test_files


DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
FAILURE_MARKERS = (
    "Traceback (most recent call last):",
    "AssertionError:",
    "Error: Python:",
    "EXCEPTION_ACCESS_VIOLATION",
    "Fatal Python error:",
)
SUCCESS_PATTERN = re.compile(
    r"(?:^|[\s\[])BMANGA[A-Z0-9_]*_(?:OK|DONE)(?:[\s\]]|$)"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--match", default="")
    parser.add_argument("--rerun", default="", help="comma separated statuses")
    return parser.parse_args()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _discover(root: Path, pattern: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in blender_test_files(root):
        if pattern and pattern not in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rows.append(
            {
                "test_id": f"blender::{path.stem}",
                "script": path.relative_to(root).as_posix(),
                "sha256": _hash_file(path),
                "has_main_guard": "__main__" in text,
                "mentions_external_fixture": any(
                    token in text for token in ("D:\\TM Dropbox", "D:/TM Dropbox")
                ),
                "mentions_ui": any(
                    token in text
                    for token in (
                        "bpy.ops.screen.screenshot",
                        "bpy.ops.render.opengl",
                        "WINDOW_DEACTIVATE",
                        "event_simulate",
                    )
                ),
            }
        )
    return rows


def _load_previous(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        str(item["test_id"]): item
        for item in payload.get("results", [])
        if "test_id" in item
    }


def _command(args: argparse.Namespace, root: Path, row: dict[str, Any]) -> list[str]:
    return [
        str(args.blender),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(root / row["script"]),
    ]


def _status(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    combined = f"{stdout}\n{stderr}"
    marker = next((item for item in FAILURE_MARKERS if item in combined), "")
    if returncode < 0:
        return "crashed", f"signal {-returncode}"
    if returncode != 0:
        return "failed", f"returncode {returncode}"
    if marker:
        return "failed", f"output marker: {marker}"
    if SUCCESS_PATTERN.search(combined) is None:
        return "failed", "completion sentinel missing"
    return "passed", ""


def _write_case_logs(case_dir: Path, stdout: str, stderr: str) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "stdout.txt").write_text(stdout, encoding="utf-8", errors="replace")
    (case_dir / "stderr.txt").write_text(stderr, encoding="utf-8", errors="replace")


def _run_one(
    args: argparse.Namespace,
    root: Path,
    row: dict[str, Any],
) -> dict[str, Any]:
    case_dir = args.out / "cases" / Path(row["script"]).stem
    command = _command(args, root, row)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.perf_counter() - started, 3)
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")
        _write_case_logs(case_dir, stdout, stderr)
        return {**row, "status": "timeout", "reason": "timeout", "seconds": elapsed}
    elapsed = round(time.perf_counter() - started, 3)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    _write_case_logs(case_dir, stdout, stderr)
    status, reason = _status(completed.returncode, stdout, stderr)
    return {
        **row,
        "status": status,
        "reason": reason,
        "returncode": completed.returncode,
        "seconds": elapsed,
    }


def _summary(results: list[dict[str, Any]], discovered: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return {
        "discovered": discovered,
        "recorded": len(results),
        "counts": dict(sorted(counts.items())),
        "all_recorded": len(results) == discovered,
    }


def _write_results(
    path: Path,
    results_by_id: dict[str, dict[str, Any]],
    discovered: int,
) -> None:
    results = sorted(results_by_id.values(), key=lambda item: str(item["script"]))
    payload = {
        "schema_version": 1,
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": _summary(results, discovered),
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _pending(
    discovered: list[dict[str, Any]],
    previous: dict[str, dict[str, Any]],
    rerun: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in discovered:
        old = previous.get(str(row["test_id"]))
        same = old and old.get("sha256") == row["sha256"]
        if same and str(old.get("status")) not in rerun:
            continue
        rows.append(row)
    return rows


def main() -> int:
    args = _arguments()
    root = args.root.resolve()
    args.out = args.out.resolve()
    results_path = args.out / "probe_results.json"
    discovered = _discover(root, "")
    eligible = [
        row
        for row in discovered
        if not args.match or args.match in Path(str(row["script"])).name
    ]
    previous = _load_previous(results_path)
    rerun = {item.strip() for item in args.rerun.split(",") if item.strip()}
    pending = _pending(eligible, previous, rerun)
    if args.limit > 0:
        pending = pending[: args.limit]
    _write_results(results_path, previous, len(discovered))
    print(
        f"PHASE0_PROBE_START discovered={len(discovered)} pending={len(pending)}",
        flush=True,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(_run_one, args, root, row): row for row in pending}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            previous[str(result["test_id"])] = result
            _write_results(results_path, previous, len(discovered))
            print(
                f"{result['status'].upper()} {result['script']} "
                f"{result.get('seconds', '')}",
                flush=True,
            )
    summary = _summary(list(previous.values()), len(discovered))
    print(f"PHASE0_PROBE_DONE {json.dumps(summary, ensure_ascii=False)}", flush=True)
    return 0 if summary["all_recorded"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
