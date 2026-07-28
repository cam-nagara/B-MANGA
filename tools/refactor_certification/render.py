"""Deterministic JSON and Markdown serialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(catalog: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _table_row(values: list[Any]) -> str:
    escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
    return "| " + " | ".join(escaped) + " |"


def _feature_lines(catalog: dict[str, Any]) -> list[str]:
    lines = [
        "## Feature inventory",
        "",
        _table_row(
            ["Feature ID", "Aliases", "Kind", "Target", "Symbol", "Field ID", "Tests", "Untested"]
        ),
        _table_row(["---", "---", "---", "---", "---", "---", "---", "---"]),
    ]
    for feature in catalog["features"]:
        lines.append(
            _table_row(
                [
                    feature["feature_id"],
                    ", ".join(feature["aliases"]),
                    feature["kind"],
                    feature["target"],
                    f"{feature['source']}:{feature['line']} {feature['symbol']}",
                    feature["field_id"],
                    ", ".join(feature["test_ids"]),
                    "YES" if feature["untested"] else "",
                ]
            )
        )
    return lines


def _contract_lines(summary: dict[str, Any]) -> list[str]:
    lines = [
        "## Contract classification debt",
        "",
        _table_row(["Contract field", "Unclassified features"]),
        _table_row(["---", "---"]),
    ]
    for field, count in summary["unclassified_contracts"].items():
        lines.append(_table_row([field, count]))
    return lines


def _test_lines(catalog: dict[str, Any]) -> list[str]:
    lines = [
        "## Test inventory",
        "",
        _table_row(["Test ID", "Source", "Entrypoint", "AI-audit registration"]),
        _table_row(["---", "---", "---", "---"]),
    ]
    for test in catalog["tests"]:
        lines.append(
            _table_row(
                [
                    test["test_id"],
                    test["source"],
                    test["entrypoint_kind"] if test["entrypoint"] else "MISSING",
                    ", ".join(test["audit_keys"]) if test["audit_registered"] else "not registered",
                ]
            )
        )
    return lines


def markdown(catalog: dict[str, Any]) -> str:
    summary = catalog["summary"]
    lines = [
        "# B-MANGA Phase 0 Feature Contract Catalog",
        "",
        f"- Features: {summary['feature_count']}",
        f"- Untested features: {summary['untested_feature_count']}",
        f"- Tests: {summary['test_count']}",
        f"- Legacy AI-audit registered tests: {summary['tests_audit_registered']}",
        "- Unclassified contracts are explicit migration debt; they are never treated as pass.",
        "",
    ]
    for section in (
        _feature_lines(catalog),
        _contract_lines(summary),
        _test_lines(catalog),
    ):
        lines.extend(section)
        lines.append("")
    return "\n".join(lines)


def write_markdown(catalog: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown(catalog), encoding="utf-8")
