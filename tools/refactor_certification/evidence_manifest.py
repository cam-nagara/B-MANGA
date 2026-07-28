"""Write the single authoritative Phase 0 evidence manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CANONICAL_FILES = (
    ("feature_catalog", "docs/refactor/phase0/feature_catalog.json"),
    ("feature_catalog_markdown", "docs/refactor/phase0/feature_catalog.md"),
    ("id_registry", "docs/refactor/phase0/id_registry.json"),
    ("runtime_catalog", "docs/refactor/phase0/runtime_catalog.json"),
    ("test_classification", "docs/refactor/phase0/test_classification.json"),
    ("test_classification_markdown", "docs/refactor/phase0/test_classification.md"),
    ("blender_probe", "docs/refactor/phase0/test_probe_results.json"),
    ("python_probe", "docs/refactor/phase0/python_test_probe_results.json"),
    ("performance_baseline", "docs/refactor/phase0/performance_baseline.json"),
    ("visual_thresholds", "docs/refactor/phase0/visual_thresholds.json"),
    (
        "open_performance_raw",
        "_verify/2026-07-28_full_refactor_phase0/open_performance.json",
    ),
    (
        "gpu_golden",
        "_verify/2026-07-28_full_refactor_phase0/"
        "visual_probe/gpu/page_preview_00.png",
    ),
    (
        "jpeg_golden",
        "_verify/2026-07-28_full_refactor_phase0/"
        "visual_probe/product_jpeg/product_page_q95_00.jpg",
    ),
)
MIRRORS = (
    ("docs/refactor/phase0/feature_catalog.json", "feature_catalog.json"),
    ("docs/refactor/phase0/feature_catalog.md", "feature_catalog.md"),
    ("docs/refactor/phase0/runtime_catalog.json", "runtime_catalog.json"),
    ("docs/refactor/phase0/test_classification.json", "test_classification.json"),
    ("docs/refactor/phase0/performance_baseline.json", "performance_baseline.json"),
    ("docs/refactor/phase0/visual_thresholds.json", "visual_thresholds.json"),
)
VERIFY_ROOT = Path("_verify/2026-07-28_full_refactor_phase0")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(root: Path, role: str, relative: str) -> dict[str, object]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "role": role,
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _mirror_records(root: Path) -> list[dict[str, object]]:
    rows = []
    for canonical, mirror_name in MIRRORS:
        mirror = (VERIFY_ROOT / mirror_name).as_posix()
        canonical_hash = _sha256(root / canonical)
        mirror_hash = _sha256(root / mirror)
        rows.append(
            {
                "canonical": canonical,
                "mirror": mirror,
                "sha256": canonical_hash,
                "matches": canonical_hash == mirror_hash,
            }
        )
    return rows


def _markdown(payload: dict[str, object]) -> str:
    lines = [
        "# Phase 0 証跡manifest",
        "",
        "この表にあるSHA-256をPhase 0最終世代の正本とする。",
        "",
        "| role | path | bytes | SHA-256 |",
        "|---|---|---:|---|",
    ]
    for row in payload["files"]:
        lines.append(
            f"| {row['role']} | `{row['path']}` | {row['bytes']} | "
            f"`{row['sha256']}` |"
        )
    lines.extend(
        [
            "",
            "## docs / _verify mirror",
            "",
            "| canonical | mirror | 一致 |",
            "|---|---|---|",
        ]
    )
    for row in payload["mirrors"]:
        lines.append(
            f"| `{row['canonical']}` | `{row['mirror']}` | "
            f"{'yes' if row['matches'] else 'NO'} |"
        )
    return "\n".join(lines) + "\n"


def write_manifest(root: Path) -> dict[str, object]:
    files = [_record(root, role, path) for role, path in CANONICAL_FILES]
    mirrors = _mirror_records(root)
    if not all(row["matches"] for row in mirrors):
        raise AssertionError("docs/_verify evidence generation mismatch")
    payload = {
        "schema_version": 1,
        "evidence_generation": "phase0-final",
        "baseline_commit": "c8ad0d70",
        "files": files,
        "mirrors": mirrors,
    }
    output = root / "docs" / "refactor" / "phase0" / "evidence_manifest.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    output.with_suffix(".md").write_text(_markdown(payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    payload = write_manifest(args.root.resolve())
    print(
        "PHASE0_EVIDENCE_MANIFEST_OK "
        f"files={len(payload['files'])} mirrors={len(payload['mirrors'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
