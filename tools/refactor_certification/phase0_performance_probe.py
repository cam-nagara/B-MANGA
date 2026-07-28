"""Phase 0のドラッグ反復値とopen基準をBlender 5.2で採取する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
DRAG_CASES = {
    "layer_move": (
        "test/blender_layer_move_drag_transaction_check.py",
        re.compile(r"p95_ms=(?P<layer>[0-9.]+) object_p95_ms=(?P<object>[0-9.]+)"),
    ),
    "composition": (
        "test/blender_preview_composite_check.py",
        re.compile(r"drag_p95_ms=(?P<composite>[0-9.]+)"),
    ),
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--open-json",
        type=Path,
        help="既に単独実行した同一sourceのopen基準を再利用する",
    )
    return parser.parse_args()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1),
    )
    return ordered[index]


def _stats(values: list[float]) -> dict[str, Any]:
    measured = values[1:]
    return {
        "runs_total": len(values),
        "warmup_discarded": 1,
        "measured_runs": len(measured),
        "raw_ms": [round(value, 3) for value in values],
        "p50_ms": round(_percentile(measured, 0.50), 3),
        "p95_ms": round(_percentile(measured, 0.95), 3),
        "max_ms": round(max(measured), 3),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_blender(
    blender: Path,
    root: Path,
    script: str,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(root / script),
    ]
    return subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )


def _drag_results(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    values: dict[str, list[float]] = {}
    logs = args.out / "drag_logs"
    logs.mkdir(parents=True, exist_ok=True)
    for case_name, (script, pattern) in DRAG_CASES.items():
        for run in range(args.runs):
            completed = _run_blender(args.blender, root, script, args.timeout)
            prefix = logs / f"{case_name}_{run:02d}"
            prefix.with_suffix(".stdout.txt").write_text(
                completed.stdout,
                encoding="utf-8",
            )
            prefix.with_suffix(".stderr.txt").write_text(
                completed.stderr,
                encoding="utf-8",
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"{case_name} run {run} failed: returncode={completed.returncode}"
                )
            match = pattern.search(f"{completed.stdout}\n{completed.stderr}")
            if match is None:
                raise RuntimeError(f"{case_name} run {run}: metric sentinel missing")
            for metric, value in match.groupdict().items():
                values.setdefault(metric, []).append(float(value))
            print(f"PERF {case_name} run={run + 1}/{args.runs}", flush=True)
    return {metric: _stats(rows) for metric, rows in sorted(values.items())}


def _open_results(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    import os

    output = args.out / "open_performance.json"
    if args.open_json is not None:
        source = args.open_json.resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload
    env = dict(os.environ)
    env["BMANGA_PHASE0_OPEN_PERF_OUT"] = str(output)
    completed = _run_blender(
        args.blender,
        root,
        "test/blender_phase0_open_performance_check.py",
        args.timeout,
        env,
    )
    (args.out / "open.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (args.out / "open.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0 or not output.is_file():
        raise RuntimeError(f"open performance failed: returncode={completed.returncode}")
    return json.loads(output.read_text(encoding="utf-8"))


def main() -> int:
    args = _arguments()
    root = args.root.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "sources": {
            script: _sha256(root / script)
            for script in (
                *(item[0] for item in DRAG_CASES.values()),
                "test/blender_phase0_open_performance_check.py",
            )
        },
        "drag": _drag_results(args, root),
        "open": _open_results(args, root),
    }
    output = args.out / "performance_baseline.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"PHASE0_PERFORMANCE_PROBE_OK {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
