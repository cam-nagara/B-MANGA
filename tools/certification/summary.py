"""全runner種別を一つの認定summaryへ統合する。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .model import Case, Result


PASS_STATUS = "passed"
ACKNOWLEDGED_STATUS = {"support", "historical"}


def build_summary(
    cases: list[Case],
    results: list[Result],
    *,
    gate_errors: list[str] | None = None,
) -> dict[str, Any]:
    gate_errors = list(gate_errors or ())
    by_id = {result.test_id: result for result in results}
    missing_results = sorted(case.source for case in cases if case.test_id not in by_id)
    status_counts = Counter(result.status for result in results)
    required = [case for case in cases if case.required]
    required_failed = [
        by_id[case.test_id]
        for case in required
        if case.test_id in by_id and by_id[case.test_id].status != PASS_STATUS
    ]
    unexpected_nonexecuted = [
        result
        for result in results
        if result.status in ACKNOWLEDGED_STATUS and result.required
    ]
    gate_pass = not (
        missing_results
        or required_failed
        or unexpected_nonexecuted
        or status_counts.get("timeout", 0)
        or status_counts.get("crash", 0)
        or status_counts.get("unexpected_skip", 0)
        or gate_errors
    )
    return {
        "schema_version": 1,
        "gate_pass": gate_pass,
        "case_count": len(cases),
        "result_count": len(results),
        "required_count": len(required),
        "required_passed": len(required) - len(required_failed),
        "required_failed": [result.source for result in required_failed],
        "missing_results": missing_results,
        "gate_errors": gate_errors,
        "status_counts": dict(sorted(status_counts.items())),
        "total_seconds": round(sum(result.seconds for result in results), 3),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# B-MANGA 認定summary",
        "",
        f"- Gate: {'PASS' if summary['gate_pass'] else 'FAIL'}",
        f"- Cases / Results: {summary['case_count']} / {summary['result_count']}",
        f"- Required: {summary['required_passed']} / {summary['required_count']}",
        f"- Seconds: {summary['total_seconds']}",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{status}` | {count} |"
        for status, count in summary["status_counts"].items()
    )
    if summary["required_failed"]:
        lines.extend(("", "## Required failures", ""))
        lines.extend(f"- `{source}`" for source in summary["required_failed"])
    if summary["gate_errors"]:
        lines.extend(("", "## Gate errors", ""))
        lines.extend(f"- {error}" for error in summary["gate_errors"])
    return "\n".join(lines) + "\n"
