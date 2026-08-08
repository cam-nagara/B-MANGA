"""mainとNextの利用者向けUI契約差分を機械判定する。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


UI_KINDS = {"panel", "operator", "property", "shortcut", "preset"}
METADATA_FIELDS = (
    "default",
    "min",
    "max",
    "items",
    "options",
    "subtype",
    "unit",
    "type",
    "value",
    "alt",
    "ctrl",
    "shift",
    "bl_options",
    "preset_role",
    "source_kind",
)


def _fingerprint(feature: dict[str, Any]) -> dict[str, Any]:
    metadata = feature.get("metadata", {})
    return {
        "kind": feature.get("kind", ""),
        "label": feature.get("label", ""),
        "bl_idname": feature.get("bl_idname", ""),
        "property_type": feature.get("property_type", ""),
        "ui_location": feature.get("ui_location", ""),
        "metadata": {
            key: metadata[key]
            for key in METADATA_FIELDS
            if key in metadata
        },
    }


def _surface(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(feature["feature_id"]): feature
        for feature in catalog.get("features", ())
        if feature.get("target") == "bmanga"
        and feature.get("kind") in UI_KINDS
    }


def compare_catalogs(
    main_catalog: dict[str, Any],
    next_catalog: dict[str, Any],
) -> dict[str, Any]:
    main = _surface(main_catalog)
    next_ = _surface(next_catalog)
    added = sorted(set(next_) - set(main))
    removed = sorted(set(main) - set(next_))
    changed = sorted(
        feature_id
        for feature_id in set(main) & set(next_)
        if _fingerprint(main[feature_id]) != _fingerprint(next_[feature_id])
    )

    def identity(feature: dict[str, Any]) -> dict[str, str]:
        return {
            "kind": str(feature.get("kind", "")),
            "symbol": str(feature.get("symbol", "")),
            "label": str(feature.get("label", "")),
        }

    return {
        "counts": {
            "main": len(main),
            "next": len(next_),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "added": {
            feature_id: identity(next_[feature_id]) for feature_id in added
        },
        "removed": {
            feature_id: identity(main[feature_id]) for feature_id in removed
        },
        "changed": {
            feature_id: {
                **identity(next_[feature_id]),
                "main": _fingerprint(main[feature_id]),
                "next": _fingerprint(next_[feature_id]),
            }
            for feature_id in changed
        },
    }


def apply_approvals(
    differences: dict[str, Any],
    approvals: dict[str, Any],
) -> dict[str, Any]:
    unexpected: list[str] = []
    obsolete: list[str] = []
    for category in ("added", "removed", "changed"):
        actual = set(differences[category])
        approved = set(approvals.get(category, {}))
        unexpected.extend(f"{category}:{item}" for item in sorted(actual - approved))
        obsolete.extend(f"{category}:{item}" for item in sorted(approved - actual))
    return {
        **differences,
        "unexpected": unexpected,
        "obsolete_approvals": obsolete,
        "gate_pass": not unexpected and not obsolete,
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", type=Path, required=True)
    parser.add_argument("--next", type=Path, required=True)
    parser.add_argument("--approvals", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    approvals = _load(args.approvals)
    if approvals.get("schema_version") != 1:
        raise ValueError("unsupported UI contract approval schema")
    result = apply_approvals(
        compare_catalogs(_load(args.main), _load(args.next)),
        approvals,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        "BMANGA_UI_CONTRACT_AUDIT "
        f"gate={'PASS' if result['gate_pass'] else 'FAIL'} "
        f"added={result['counts']['added']} "
        f"removed={result['counts']['removed']} "
        f"changed={result['counts']['changed']}"
    )
    return 0 if result["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
