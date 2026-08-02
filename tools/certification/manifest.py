"""静的manifestを読み、test source inventoryとの完全一致を検証する。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tools.refactor_certification.ids import test_id

from .model import RUN_MODES, Case


DEFAULT_MANIFEST = Path("test/certification_manifest.json")
NON_EXECUTED_MODES = {"support", "historical"}


def discovered_sources(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in (root / "test").glob("*.py")
        if path.is_file()
    )


def _validate_case(case: Case) -> list[str]:
    errors: list[str] = []
    if case.mode not in RUN_MODES:
        errors.append(f"{case.source}: unknown mode {case.mode}")
    if case.test_id != test_id(case.source):
        errors.append(f"{case.source}: unstable test_id")
    if not 1 <= case.timeout_seconds <= 3600:
        errors.append(f"{case.source}: invalid timeout {case.timeout_seconds}")
    if not 0 <= case.run_order <= 1000:
        errors.append(f"{case.source}: invalid run order {case.run_order}")
    if case.mode in NON_EXECUTED_MODES:
        if case.required:
            errors.append(f"{case.source}: non-executed case cannot be required")
        if not case.reason or not case.review:
            errors.append(f"{case.source}: support/historical needs reason and review")
    elif not case.required:
        errors.append(f"{case.source}: executable case must be required")
    if case.mode in {"blender_headless", "blender_ui", "python_script"}:
        if not case.completion_token:
            errors.append(f"{case.source}: direct case needs completion_token")
    if case.expected_tracebacks:
        if case.mode != "blender_wrapper":
            errors.append(
                f"{case.source}: expected tracebacks require blender_wrapper"
            )
        if not case.required or not case.completion_token:
            errors.append(
                f"{case.source}: expected tracebacks need a required sentinel case"
            )
        if not case.reason or not case.review:
            errors.append(
                f"{case.source}: expected tracebacks need reason and review"
            )
        for expected in case.expected_tracebacks:
            pattern = str(expected.get("pattern", ""))
            count = int(expected.get("count", 0) or 0)
            if not pattern or count < 1:
                errors.append(
                    f"{case.source}: expected traceback needs pattern and positive count"
                )
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(
                    f"{case.source}: invalid traceback pattern {pattern!r}: {exc}"
                )
    return errors


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_manifest(root: Path, cases: list[Case]) -> None:
    errors: list[str] = []
    sources = [case.source for case in cases]
    ids = [case.test_id for case in cases]
    if len(sources) != len(set(sources)):
        errors.append("duplicate manifest source")
    if len(ids) != len(set(ids)):
        errors.append("duplicate manifest test_id")
    discovered = set(discovered_sources(root))
    registered = set(sources)
    missing = sorted(discovered - registered)
    stale = sorted(registered - discovered)
    if missing:
        errors.append(f"unregistered test sources: {missing}")
    if stale:
        errors.append(f"stale manifest sources: {stale}")
    for case in cases:
        errors.extend(_validate_case(case))
        source_path = root / case.source
        if source_path.is_file() and _source_sha256(source_path) != case.source_sha256:
            errors.append(f"{case.source}: source hash differs from manifest")
    if errors:
        raise ValueError("\n".join(errors))


def load_manifest(
    root: Path,
    path: Path | None = None,
) -> tuple[dict[str, object], list[Case]]:
    manifest_path = root / (path or DEFAULT_MANIFEST)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("unsupported certification manifest schema")
    golden_registries = raw.get("golden_registries")
    if not isinstance(golden_registries, list) or not golden_registries:
        raise ValueError("certification manifest needs golden registries")
    if any(not isinstance(path, str) or not path for path in golden_registries):
        raise ValueError("invalid golden registry path")
    cases = [Case.from_dict(item) for item in raw.get("cases", ())]
    validate_manifest(root, cases)
    return raw, cases
