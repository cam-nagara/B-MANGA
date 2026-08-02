"""B-MANGA統一認定ランナーのCLI。"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .executor import DEFAULT_BLENDER, run_case
from .golden import verify as verify_golden
from .manifest import DEFAULT_MANIFEST, load_manifest
from .model import Case, Result
from .summary import build_summary, render_markdown


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--only", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--mode", default="")
    return parser.parse_args()


def _tokens(value: str) -> set[str]:
    return {token.strip() for token in value.split(",") if token.strip()}


def _selected(
    cases: list[Case],
    only: str,
    category: str = "",
    mode: str = "",
) -> list[Case]:
    tokens = {token.strip() for token in only.split(",") if token.strip()}
    categories = _tokens(category)
    modes = _tokens(mode)
    selected = [
        case
        for case in cases
        if (
            not tokens
            or case.test_id in tokens
            or case.source in tokens
            or Path(case.source).stem in tokens
        )
        and (not categories or case.phase0_category in categories)
        and (not modes or case.mode in modes)
    ]
    if (tokens or categories or modes) and not selected:
        raise ValueError("certification selector matched no cases")
    return selected


def _write_results(out: Path, results: list[Result], summary: dict[str, object]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "results": [result.to_dict() for result in sorted(results, key=lambda r: r.source)],
        "summary": summary,
    }
    (out / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "summary.md").write_text(render_markdown(summary), encoding="utf-8")


def _golden_gate_errors(
    root: Path,
    manifest: dict[str, object],
) -> list[str]:
    errors: list[str] = []
    for relative in manifest.get("golden_registries", ()):
        path = root / str(relative)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(
                f"{relative}: golden registry could not be read: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        errors.extend(
            f"{relative}: {error}"
            for error in verify_golden(root, payload)
        )
    return errors


def _run_all(
    root: Path,
    out: Path,
    cases: list[Case],
    blender: Path,
    jobs: int,
) -> list[Result]:
    def report(result: Result) -> None:
        print(
            "BMANGA_CERT_CASE_RESULT "
            f"status={result.status} seconds={result.seconds} source={result.source}",
            flush=True,
        )

    if jobs <= 1:
        results = []
        for case in cases:
            result = run_case(root, out, case, blender)
            results.append(result)
            report(result)
        return results
    parallel_cases = [case for case in cases if not case.mode.startswith("blender_")]
    blender_cases = sorted(
        (case for case in cases if case.mode.startswith("blender_")),
        key=lambda case: (case.run_order, case.source),
    )
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(run_case, root, out, case, blender): case
            for case in parallel_cases
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            report(result)
    # Blenderを複数起動するとUI focusだけでなく、共有設定・timer・高負荷の
    # 相互干渉でheadless/wrapperにも偽陰性が出る。Python群の完了後、
    # 全Blender実機ケースを必ず1件ずつ実行する。
    for case in blender_cases:
        result = run_case(root, out, case, blender)
        results.append(result)
        report(result)
    return results


def main() -> int:
    args = _arguments()
    if args.jobs < 1:
        raise ValueError("--jobs must be positive")
    root = args.root.resolve()
    manifest, all_cases = load_manifest(root, args.manifest)
    cases = _selected(all_cases, args.only, args.category, args.mode)
    results = _run_all(root, args.out.resolve(), cases, args.blender, args.jobs)
    summary = build_summary(
        cases,
        results,
        gate_errors=_golden_gate_errors(root, manifest),
    )
    _write_results(args.out.resolve(), results, summary)
    print(
        "BMANGA_CERTIFICATION_SUMMARY "
        f"gate={'PASS' if summary['gate_pass'] else 'FAIL'} "
        f"results={summary['result_count']} required={summary['required_passed']}/"
        f"{summary['required_count']}",
        flush=True,
    )
    return 0 if summary["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
