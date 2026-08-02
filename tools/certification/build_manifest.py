"""Phase 0 inventoryと明示overrideから静的認定manifestを生成する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from tools.refactor_certification.ids import test_id
from tools.refactor_certification.test_scan import scan_tests


TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]*(?:_OK|_DONE|_PASS)\b")
REVIEW_ID = "phase1-manifest-audit-2026-07-29"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _phase0_maps(root: Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    phase0 = root / "docs" / "refactor" / "phase0"
    classification = _load_json(phase0 / "test_classification.json", {})
    python_probe = _load_json(phase0 / "python_test_probe_results.json", {})
    categories = {
        str(row["script"]): str(row["category"])
        for row in classification.get("results", ())
    }
    python_rows = {
        str(row["script"]): row
        for row in python_probe.get("results", ())
    }
    return categories, python_rows


def _completion_tokens(text: str) -> list[str]:
    seen: set[str] = set()
    result = []
    for token in TOKEN_PATTERN.findall(text):
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _requires_ui_event_loop(text: str) -> bool:
    """script終了後もBlenderのイベントループを必要とする検査か."""

    markers = (
        "bpy.app.timers.register",
        "draw_handler_add(",
    )
    return any(marker in text for marker in markers)


def _timeout(previous: dict[str, Any] | None, source: str) -> int:
    seconds = float((previous or {}).get("seconds", 0.0) or 0.0)
    value = max(180, math.ceil(seconds * 4.0 + 30.0))
    if any(token in source for token in ("performance", "full_visual", "large_audit")):
        value = max(value, 900)
    return min(value, 1800)


def _python_mode(
    source: str,
    python_rows: dict[str, dict[str, Any]],
    text: str,
) -> tuple[str, str]:
    row = python_rows.get(source, {})
    runner = str(row.get("runner", ""))
    if runner == "python_script":
        tokens = _completion_tokens(text)
        return "python_script", tokens[-1] if tokens else ""
    return "python_pytest", ""


def _blender_mode(text: str) -> tuple[str, str]:
    tokens = _completion_tokens(text)
    if _requires_ui_event_loop(text):
        return "blender_ui", tokens[-1] if tokens else ""
    if tokens:
        return "blender_headless", tokens[-1]
    return "blender_wrapper", ""


def _default_case(
    root: Path,
    scanned,
    category: str,
    python_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    path = root / scanned.source
    text = path.read_text(encoding="utf-8-sig")
    if scanned.execution_kind == "support" or not scanned.entrypoint:
        mode, token, required = "support", "", False
    elif scanned.execution_kind == "python":
        mode, token = _python_mode(scanned.source, python_rows, text)
        required = True
    else:
        mode, token = _blender_mode(text)
        required = True
        if category == "ui_required":
            mode = "blender_ui"
    row = {
        "test_id": test_id(scanned.source),
        "source": scanned.source,
        "source_sha256": _sha256(path),
        "mode": mode,
        "required": required,
        "timeout_seconds": _timeout(python_rows.get(scanned.source), scanned.source),
        "run_order": 100,
        "completion_token": token,
        "args": [],
        "blender_args": (
            ["--enable-event-simulate"] if ".event_simulate" in text else []
        ),
        "reason": "",
        "review": "",
        "phase0_category": category or "new",
        "artifacts": [],
        "expected_tracebacks": [],
    }
    if category == "expected_traceback_marker":
        row["reason"] = (
            "Phase 0で意図的な障害注入tracebackを含む負系テストとして確認済み"
        )
        row["review"] = REVIEW_ID
    if not required:
        row["reason"] = "test支援・runner moduleであり、単独の製品検査ではない"
        row["review"] = REVIEW_ID
    return row


def _apply_overrides(
    rows: list[dict[str, Any]],
    overrides: dict[str, Any],
) -> None:
    by_source = {row["source"]: row for row in rows}
    unknown = sorted(set(overrides) - set(by_source))
    if unknown:
        raise ValueError(f"unknown certification overrides: {unknown}")
    for source, values in overrides.items():
        by_source[source].update(values)


def build_manifest(root: Path, override_path: Path) -> dict[str, Any]:
    categories, python_rows = _phase0_maps(root)
    scanned, _ = scan_tests(root)
    rows = [
        _default_case(
            root,
            case,
            categories.get(case.source, ""),
            python_rows,
        )
        for case in scanned
    ]
    override_payload = _load_json(override_path, {"schema_version": 1, "cases": {}})
    if override_payload.get("schema_version") != 1:
        raise ValueError("unsupported certification override schema")
    _apply_overrides(rows, dict(override_payload.get("cases", {})))
    golden_registries = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "docs" / "refactor").glob(
            "**/golden_registry*.json"
        )
        if path.is_file()
    )
    if not golden_registries:
        raise ValueError("approved golden registry is required")
    return {
        "schema_version": 1,
        "review": REVIEW_ID,
        "golden_registries": golden_registries,
        "cases": sorted(rows, key=lambda row: row["source"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("test/certification_overrides.json"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("test/certification_manifest.json"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    payload = build_manifest(root, root / args.overrides)
    output = root / args.out
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"BMANGA_CERT_MANIFEST_BUILT cases={len(payload['cases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
