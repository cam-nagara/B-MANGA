"""Persistent ID registry that survives field additions during refactoring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Feature


DEFAULT_RELATIVE_PATH = Path("docs/refactor/phase0/id_registry.json")


def load_registry(root: Path) -> dict[str, Any]:
    path = root / DEFAULT_RELATIVE_PATH
    if not path.is_file():
        return {"schema_version": 1, "feature_aliases": {}, "field_aliases": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported ID registry: {path}")
    return payload


def _registered_id(
    aliases: list[str],
    mapping: dict[str, str],
    fallback: str,
) -> str:
    matches = {mapping[alias] for alias in aliases if alias in mapping}
    if len(matches) > 1:
        raise ValueError(f"conflicting registered IDs: {sorted(matches)}")
    return next(iter(matches), fallback)


def apply_registry(features: list[Feature], registry: dict[str, Any]) -> None:
    feature_aliases = dict(registry.get("feature_aliases", {}))
    field_aliases = dict(registry.get("field_aliases", {}))
    for feature in features:
        feature.feature_id = _registered_id(
            feature.aliases,
            feature_aliases,
            feature.feature_id,
        )
        if feature.field_id:
            feature.field_id = _registered_id(
                feature.field_aliases,
                field_aliases,
                feature.field_id,
            )
    for feature in features:
        proxy_alias = str(feature.metadata.get("proxy_field_alias", ""))
        if proxy_alias and proxy_alias in field_aliases:
            feature.field_id = field_aliases[proxy_alias]
            feature.metadata["proxy_field_id"] = feature.field_id


def registry_from_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract": (
            "Phase 2以降も既存aliasへ同じIDを割り当てる。class/file移動時は"
            "旧aliasをFieldSpecの明示aliasとして引き継ぐ"
        ),
        "feature_aliases": catalog["feature_aliases"],
        "field_aliases": catalog["field_aliases"],
    }


def write_registry(catalog: dict[str, Any], path: Path) -> None:
    payload = registry_from_catalog(catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
