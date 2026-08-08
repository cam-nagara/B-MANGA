from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.certification.ui_contract import apply_approvals, compare_catalogs


def _feature(feature_id: str, *, label: str = "項目") -> dict[str, object]:
    return {
        "feature_id": feature_id,
        "target": "bmanga",
        "kind": "property",
        "symbol": "Settings.value",
        "label": label,
        "bl_idname": "",
        "property_type": "BoolProperty",
        "ui_location": "Settings / 項目",
        "metadata": {"default": False, "options": []},
    }


def test_ui_contract_requires_every_difference_to_be_approved() -> None:
    main = {"features": [_feature("stable"), _feature("removed")]}
    next_ = {"features": [_feature("stable", label="変更"), _feature("added")]}
    differences = compare_catalogs(main, next_)
    assert differences["counts"] == {
        "main": 2,
        "next": 2,
        "added": 1,
        "removed": 1,
        "changed": 1,
    }
    failed = apply_approvals(differences, {"added": {}, "removed": {}, "changed": {}})
    assert failed["gate_pass"] is False
    assert len(failed["unexpected"]) == 3

    passed = apply_approvals(
        differences,
        {
            "added": {"added": "approved"},
            "removed": {"removed": "approved"},
            "changed": {"stable": "approved"},
        },
    )
    assert passed["gate_pass"] is True
    assert passed["unexpected"] == []
    assert passed["obsolete_approvals"] == []


def test_ui_contract_rejects_obsolete_approval() -> None:
    differences = compare_catalogs({"features": []}, {"features": []})
    result = apply_approvals(
        differences,
        {"added": {"stale": "old"}, "removed": {}, "changed": {}},
    )
    assert result["gate_pass"] is False
    assert result["obsolete_approvals"] == ["added:stale"]
