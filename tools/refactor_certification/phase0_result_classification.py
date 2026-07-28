"""Phase 0 probe結果を、Phase 1で解消すべき原因区分へ変換する。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from tools.refactor_certification.ids import test_id
from tools.refactor_certification.test_scan import (
    assert_unique_test_sources,
    execution_kind,
    test_files,
)


UI_MARKERS = (
    "Blender通常画面で実行してください",
    "--background なしで実行してください",
    "must run without ``--background``",
)
EXTERNAL_MARKERS = (
    "D:\\TM Dropbox",
    "D:/TM Dropbox",
    "requires_c00",
)
SUCCESS_PATTERN = re.compile(r"(?:^|[\s\[])BMANGA[A-Z0-9_]*_(?:OK|DONE)(?:[\s\]]|$)")
ASSERTION_PATTERN = re.compile(r"AssertionError(?::\s*(.+))?")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--probe", type=Path, action="append", required=True)
    parser.add_argument("--python-probe", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-markdown", type=Path, required=True)
    parser.add_argument("--expected-sources", type=int, required=True)
    return parser.parse_args()


def _logs(probe_dir: Path, script: str) -> tuple[str, str]:
    case = probe_dir / "cases" / Path(script).stem
    stdout = (case / "stdout.txt").read_text(
        encoding="utf-8",
        errors="replace",
    )
    stderr = (case / "stderr.txt").read_text(
        encoding="utf-8",
        errors="replace",
    )
    return stdout, stderr


def _assertion_summary(text: str) -> str:
    matches = ASSERTION_PATTERN.findall(text)
    for match in reversed(matches):
        summary = str(match).strip()
        if summary:
            return summary[:240]
    return "assertion mismatch"


def _classify(result: dict[str, Any], stdout: str, stderr: str) -> tuple[str, str]:
    combined = f"{stdout}\n{stderr}"
    if result["status"] == "timeout":
        return "timeout", "制限時間内に完了しない"
    if result["status"] == "crashed":
        return "crash", str(result.get("reason", "process crash"))
    if any(marker in combined for marker in UI_MARKERS):
        return "ui_required", "background実行では成立しないUIケース"
    if any(marker in combined for marker in EXTERNAL_MARKERS):
        return "external_fixture", "リポジトリ外c00/Dropbox fixtureへ依存"
    returncode = int(result.get("returncode", 0))
    has_success = SUCCESS_PATTERN.search(combined) is not None
    if returncode == 0 and has_success:
        if result["status"] == "passed":
            return "baseline_pass", ""
        return "expected_traceback_marker", "失敗注入の期待tracebackをprobeが赤判定"
    if returncode == 0:
        if "Traceback (most recent call last):" in combined:
            return "silent_failure", "終了コード0だが完了sentinelがなく例外出力を含む"
        return "missing_sentinel", "終了コード0だが完了sentinelがなく、合格を証明できない"
    if "AssertionError" in combined:
        return "behavior_mismatch", _assertion_summary(combined)
    return "runtime_failure", str(result.get("reason", "runtime failure"))


def _row(probe_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    stdout, stderr = _logs(probe_dir, str(result["script"]))
    category, evidence = _classify(result, stdout, stderr)
    return {
        "test_id": test_id(str(result["script"])),
        "script": result["script"],
        "runner": "blender_background",
        "probe_status": result["status"],
        "category": category,
        "evidence": evidence,
        "seconds": result.get("seconds"),
    }


def _python_category(result: dict[str, Any]) -> tuple[str, str]:
    status = str(result["status"])
    if status == "passed":
        return "python_pass", ""
    if status == "skipped":
        return "python_skipped", str(result.get("reason", "pytest skip"))
    if status == "collection_error":
        return "python_collection_error", str(result.get("reason", "collection error"))
    if status == "timeout":
        return "python_timeout", "制限時間内に完了しない"
    if status == "no_tests":
        return "python_no_tests", "test itemが収集されず合格を証明できない"
    return "python_failure", str(result.get("reason", "pytest failure"))


def _python_row(result: dict[str, Any]) -> dict[str, Any]:
    category, evidence = _python_category(result)
    script_name = Path(str(result["script"])).name
    runner = str(result.get("runner", "pytest_module"))
    command = ["python", script_name]
    if runner == "pytest_module":
        command = [
            "python", "-m", "pytest", script_name, "-q",
            "--junitxml=<case>/junit.xml",
        ]
    return {
        "test_id": test_id(str(result["script"])),
        "script": result["script"],
        "runner": runner,
        "probe_status": result["status"],
        "category": category,
        "evidence": evidence,
        "seconds": result.get("seconds"),
        "test_item_counts": result.get("counts", {}),
        "command": command,
    }


def _support_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in test_files(root):
        if execution_kind(path) != "support":
            continue
        source = path.relative_to(root).as_posix()
        rows.append(
            {
                "test_id": test_id(source),
                "script": source,
                "runner": "inventory_only",
                "probe_status": "not_executable",
                "category": "support_module",
                "evidence": "test支援・監査ツール。単独test itemではない",
                "seconds": 0.0,
            }
        )
    return rows


def _assert_coverage(root: Path, rows: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    sources = assert_unique_test_sources(root)
    ids = [str(row["test_id"]) for row in rows]
    duplicate_ids = sorted(key for key, count in Counter(ids).items() if count > 1)
    expected_ids = {test_id(source) for source in sources}
    actual_ids = set(ids)
    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)
    coverage = {
        "discovered_sources": len(sources),
        "recorded_sources": len(rows),
        "expected_sources": expected,
        "unregistered": missing,
        "unexpected": unexpected,
        "duplicate_ids": duplicate_ids,
    }
    if len(sources) != expected:
        raise ValueError(f"test source count mismatch: {len(sources)} != {expected}")
    if missing or unexpected or duplicate_ids or len(rows) != len(sources):
        raise ValueError(f"test coverage mismatch: {coverage}")
    return coverage


def _category_contract() -> list[str]:
    return [
        "",
        "### Category contract",
        "",
        "- `baseline_pass`: Blender終了コード0、失敗markerなし、完了sentinelあり。",
        "- `expected_traceback_marker`: 期待traceback、完了sentinel、終了コード0。Phase 1で正式対応する。",
        "- `behavior_mismatch`: AssertionError。Phase 1で実不具合か期待値かを判定する。",
        "- `runtime_failure`: 非0終了でAssertionError以外。Phase 1で解消する。",
        "- `silent_failure`: 終了コード0でもsentinelなしで例外を含む。合格ではない。",
        "- `missing_sentinel`: 終了コード0でもsentinelなし。合格ではない。",
        "- `ui_required`: headless非対応。UI必須metadataとして記録する。",
        "- `external_fixture`: リポジトリ外fixture依存として記録する。",
        "- `python_pass`: ファイル単位pytest/scriptが1件以上を実行し成功。",
        "- `python_skipped`: 全itemが明示skip。合格ではない。",
        "- `python_collection_error`: pytest収集/importエラー。合格ではない。",
        "- `python_failure` / `python_timeout` / `python_no_tests`: 合格を証明できない。",
        "- `support_module`: test配下の支援・監査ツール。source inventoryには含め、単独実行対象からは除外する。",
        "",
        "## Non-pass cases",
        "",
        "| Script | Category | Evidence |",
        "|---|---|---|",
    ]


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Phase 0 全テスト実行・分類",
        "",
        "Blender検査と通常Pythonテストを一意に統合した。silent pass、収集失敗、未登録、重複は合格扱いにしない。",
        "",
        "## Coverage",
        "",
        f"- 発見: {payload['coverage']['discovered_sources']}",
        f"- 記録: {payload['coverage']['recorded_sources']}",
        f"- 未登録: {len(payload['coverage']['unregistered'])}",
        f"- 重複: {len(payload['coverage']['duplicate_ids'])}",
        f"- 通常Python test item: {payload['python_test_items']}",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    for category, count in payload["summary"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend(_category_contract())
    for row in payload["results"]:
        if row["category"] in {"baseline_pass", "python_pass"}:
            continue
        evidence = str(row["evidence"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{row['script']}` | {row['category']} | {evidence} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = _arguments()
    root = args.root.resolve()
    probe_paths = [path.resolve() for path in args.probe]
    python_source = json.loads(args.python_probe.resolve().read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for probe_path in probe_paths:
        source = json.loads(probe_path.read_text(encoding="utf-8"))
        rows.extend(_row(probe_path.parent, result) for result in source["results"])
    rows.extend(_python_row(result) for result in python_source["results"])
    rows.extend(_support_rows(root))
    rows.sort(key=lambda row: str(row["script"]))
    coverage = _assert_coverage(root, rows, args.expected_sources)
    summary = Counter(row["category"] for row in rows)
    payload = {
        "schema_version": 2,
        "sources": [
            f"{path.parent.name}/{path.name}" for path in probe_paths
        ] + [f"{args.python_probe.parent.name}/{args.python_probe.name}"],
        "coverage": coverage,
        "python_test_items": sum(
            int(row.get("test_item_counts", {}).get("tests", 0))
            for row in rows
        ),
        "summary": dict(sorted(summary.items())),
        "results": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.out_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.out_markdown.write_text(_markdown(payload), encoding="utf-8")
    print(
        "PHASE0_RESULT_CLASSIFICATION_OK "
        + json.dumps(payload["summary"], ensure_ascii=False),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
