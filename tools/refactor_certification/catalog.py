"""Compose feature extraction, test discovery, and coverage evidence."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .contracts import expected_state_class, freeze_contracts
from .model import Feature
from .registry import apply_registry, load_registry
from .source_scan import scan_product_features
from .test_scan import scan_tests


SCHEMA_VERSION = 4
CONTRACT_FIELDS = (
    "prerequisite_file_role",
    "input_contract",
    "success_contract",
    "cancel_contract",
    "undo_redo_contract",
    "save_reload_contract",
    "visual_expectation",
    "artifact_expectation",
    "performance_probe",
)
GENERIC_EVIDENCE_TOKENS = {
    "draw",
    "execute",
    "export",
    "invoke",
    "load",
    "poll",
    "register",
    "save",
    "unregister",
}


def _evidence_tokens(feature: Feature) -> list[str]:
    tokens = [feature.bl_idname, feature.symbol]
    if "." in feature.symbol:
        owner, field = feature.symbol.rsplit(".", 1)
        tokens.append(f"{owner}.{field}")
    return sorted(
        {
            token
            for token in tokens
            if len(token) >= 5 and token not in GENERIC_EVIDENCE_TOKENS
        }
    )


def _test_token_index(test_texts: dict[str, str]) -> dict[str, list[str]]:
    index: dict[str, set[str]] = {}
    token_pattern = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
    for identifier, text in test_texts.items():
        for token in set(token_pattern.findall(text)):
            index.setdefault(token, set()).add(identifier)
    return {token: sorted(identifiers) for token, identifiers in index.items()}


def _match_tests(
    feature: Feature,
    index: dict[str, list[str]],
) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {}
    for token in _evidence_tokens(feature):
        for identifier in index.get(token, ()):
            evidence.setdefault(identifier, []).append(token)
    return {
        identifier: sorted(tokens)
        for identifier, tokens in sorted(evidence.items())
    }


def _ownership_violation(feature: Feature) -> bool:
    state = str(feature.metadata.get("state_class", ""))
    return state != expected_state_class(feature)


def _summary(features: list[Feature], tests: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = Counter(feature.kind for feature in features)
    targets = Counter(feature.target for feature in features)
    state_classes = Counter(
        str(feature.metadata.get("state_class", "unclassified"))
        for feature in features
    )
    untested = sum(feature.untested for feature in features)
    unclassified_contracts = {
        name: sum(getattr(feature, name) == "unclassified" for feature in features)
        for name in CONTRACT_FIELDS
    }
    return {
        "feature_count": len(features),
        "feature_alias_count": sum(len(feature.aliases) for feature in features),
        "field_alias_count": sum(len(feature.field_aliases) for feature in features),
        "untested_feature_count": untested,
        "tested_feature_count": len(features) - untested,
        "test_count": len(tests),
        "tests_with_entrypoint": sum(bool(test["entrypoint"]) for test in tests),
        "tests_audit_registered": sum(bool(test["audit_registered"]) for test in tests),
        "unclassified_contracts": unclassified_contracts,
        "contract_state_classes": dict(sorted(state_classes.items())),
        "contract_basis_mismatches": sum(
            feature.metadata.get("contract_basis") != "phase0_ownership_v5"
            for feature in features
        ),
        "contract_ownership_violations": sum(
            _ownership_violation(feature) for feature in features
        ),
        "unverified_property_group_ownership": sum(
            feature.metadata.get("ownership_evidence")
            == "unverified PropertyGroup ownership"
            for feature in features
        ),
        "feature_counts_by_kind": dict(sorted(kinds.items())),
        "feature_counts_by_target": dict(sorted(targets.items())),
    }


def _alias_map(
    features: list[Feature],
    alias_attribute: str,
    id_attribute: str,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for feature in features:
        identifier = str(getattr(feature, id_attribute))
        for alias in getattr(feature, alias_attribute):
            existing = mapping.get(alias)
            if existing is not None and existing != identifier:
                raise ValueError(f"alias collision: {alias}")
            mapping[alias] = identifier
    return dict(sorted(mapping.items()))


def build_catalog(root: Path) -> dict[str, Any]:
    root = root.resolve()
    features = scan_product_features(root)
    apply_registry(features, load_registry(root))
    freeze_contracts(features)
    test_cases, test_texts = scan_tests(root)
    test_index = _test_token_index(test_texts)
    for feature in features:
        feature.test_evidence = _match_tests(feature, test_index)
        feature.test_ids = sorted(feature.test_evidence)
        feature.untested = not feature.test_ids
    feature_dicts = [feature.to_dict() for feature in features]
    test_dicts = [case.to_dict() for case in test_cases]
    feature_aliases = _alias_map(features, "aliases", "feature_id")
    field_aliases = _alias_map(features, "field_aliases", "field_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "tools.refactor_certification",
        "root": ".",
        "summary": _summary(features, test_dicts),
        "feature_aliases": feature_aliases,
        "field_aliases": field_aliases,
        "features": feature_dicts,
        "tests": test_dicts,
    }
