"""Phase 0台帳から正規化FieldSpecと設定マトリクスを生成する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.refactor_certification.catalog import build_catalog

from .model import (
    SCHEMA_VERSION,
    cache_policy_for,
    category_for,
    classification_reason,
    codec_policy_for,
    dirty_policy_for,
    legacy_save_policy_for,
    save_policy_for,
    schema_decision_for,
    summarize,
    test_policy_for,
    unit_conversion_for,
)
from .preset_fields import preset_field_families
from .ui_scan import scan_detail_ui
from .wiring import (
    codec_bindings_for,
    declaration_wiring,
    field_test_ids,
)


def _digest(data: Any) -> str:
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _field_spec(
    root: Path,
    feature: dict[str, Any],
    presets: dict[tuple[str, str], tuple[str, ...]],
    historical_aliases: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    metadata = feature["metadata"]
    owner = str(metadata["owner_name"])
    name = str(feature["symbol"]).rsplit(".", 1)[-1]
    category = category_for(feature)
    families = presets.get((owner, name), ())
    test_ids = set(feature.get("test_ids", ())) | set(
        field_test_ids(feature["field_id"], category)
    )
    wiring = (
        declaration_wiring(root, feature)
        if feature["target"] == "bmanga"
        else {
            "update_callback": "",
            "dirty_bindings": (
                f"external_addon_owned:{owner}.{name}",
            ),
            "cache_dependencies": (),
            "accessor_bindings": (),
        }
    )
    cache_dependencies = tuple(wiring["cache_dependencies"])
    if cache_dependencies:
        test_ids.update(
            (
                "test:test.blender.settings.cache.signature."
                "characterization.check",
                f"cache-characterization:{feature['field_id']}",
            )
        )
    codec_bindings = codec_bindings_for(root, feature, category)
    if category in {"session_state", "derived_display"}:
        dirty_bindings = (
            (f"session:not_persistent:{owner}.{name}",)
            if category == "session_state"
            else (f"derived:source_owned:{owner}.{name}",)
        )
    else:
        dirty_bindings = tuple(wiring["dirty_bindings"])
    return {
        "field_id": feature["field_id"],
        "aliases": sorted(
            set(feature.get("field_aliases", ()))
            | set(historical_aliases.get(feature["field_id"], ()))
        ),
        "target": feature["target"],
        "source": feature["source"],
        "symbol": feature["symbol"],
        "owner_name": owner,
        "field_name": name,
        "property_type": feature["property_type"],
        "state_class": metadata["state_class"],
        "category": category,
        "classification_reason": classification_reason(feature),
        "schema_member": category in {"persistent_domain", "user_setting"},
        "schema_decision": schema_decision_for(category),
        "legacy_save_policy": legacy_save_policy_for(feature),
        "save_policy": save_policy_for(feature, category),
        "codec_policy": codec_policy_for(feature, category),
        "codec_bindings": list(codec_bindings),
        "preset_policy": "included" if families else "excluded",
        "preset_families": list(families),
        "dirty_policy": dirty_policy_for(category),
        "dirty_bindings": list(dirty_bindings),
        "cache_policy": cache_policy_for(category, cache_dependencies),
        "cache_dependencies": list(cache_dependencies),
        "test_policy": test_policy_for(category),
        "unit_conversion": unit_conversion_for(feature),
        "update_callback": wiring["update_callback"],
        "accessor_bindings": list(wiring["accessor_bindings"]),
        "ui_location": feature["ui_location"],
        "input_contract": feature["input_contract"],
        "cancel_contract": feature["cancel_contract"],
        "save_reload_contract": feature["save_reload_contract"],
        "test_ids": sorted(test_ids),
        "default": metadata.get("default"),
        "minimum": metadata.get("min"),
        "maximum": metadata.get("max"),
    }


def build_settings_contract(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    catalog = build_catalog(root)
    properties = [
        feature
        for feature in catalog["features"]
        if feature["kind"] == "property"
    ]
    presets = preset_field_families(root)
    id_registry = json.loads(
        (
            root / "docs" / "refactor" / "phase0" / "id_registry.json"
        ).read_text(encoding="utf-8")
    )
    aliases_by_id: dict[str, list[str]] = {}
    for alias, field_id in id_registry.get("field_aliases", {}).items():
        aliases_by_id.setdefault(str(field_id), []).append(str(alias))
    historical_aliases = {
        field_id: tuple(sorted(aliases))
        for field_id, aliases in aliases_by_id.items()
    }
    property_keys = {
        (
            str(feature["metadata"]["owner_name"]),
            str(feature["symbol"]).rsplit(".", 1)[-1],
        )
        for feature in properties
    }
    unknown_preset_fields = sorted(set(presets) - property_keys)
    if unknown_preset_fields:
        raise ValueError(
            f"preset codec references unregistered fields: "
            f"{unknown_preset_fields}"
        )
    specs = sorted(
        (
            _field_spec(root, feature, presets, historical_aliases)
            for feature in properties
        ),
        key=lambda item: item["field_id"],
    )
    summary = summarize(specs)
    phase0_path = (
        root / "docs" / "refactor" / "phase0" / "feature_catalog.json"
    )
    phase0 = json.loads(phase0_path.read_text(encoding="utf-8"))
    phase0_properties = {
        feature["field_id"]: feature
        for feature in phase0["features"]
        if feature["kind"] == "property"
    }
    current_ids = {spec["field_id"] for spec in specs}
    retired_fields = [
        {
            "field_id": field_id,
            "source": phase0_properties[field_id]["source"],
            "symbol": phase0_properties[field_id]["symbol"],
            "state_class": phase0_properties[field_id]["metadata"][
                "state_class"
            ],
            "aliases": list(historical_aliases.get(field_id, ())),
            "reason": "Phase 0後にProperty宣言を削除済み",
        }
        for field_id in sorted(set(phase0_properties) - current_ids)
    ]
    summary["phase0_property_binding_count"] = sum(
        feature["kind"] == "property" for feature in phase0["features"]
    )
    summary["retired_field_count"] = len(retired_fields)
    registry = {
        "schema_version": SCHEMA_VERSION,
        "generator": "tools.settings_contract",
        "phase0_schema_version": catalog["schema_version"],
        "phase0_field_id_digest": _digest(
            sorted(spec["field_id"] for spec in specs)
        ),
        "summary": summary,
        "retired_fields": retired_fields,
        "field_specs": specs,
    }
    ui_matrix = {
        "schema_version": SCHEMA_VERSION,
        "generator": "tools.settings_contract.ui_scan",
        "field_registry_digest": _digest(specs),
        **scan_detail_ui(root, specs),
    }
    return registry, ui_matrix


def render_markdown(registry: dict[str, Any]) -> str:
    summary = registry["summary"]
    lines = [
        "# Phase 2 Settings Contract Matrix",
        "",
        "この文書は `python -m tools.settings_contract` で自動生成する。",
        "手編集せず、FieldSpecまたは現行RNAを変更したら再生成すること。",
        "",
        f"- 全Property: {summary['field_count']}",
        f"- RNA投影: {summary['property_binding_count']}",
        f"- 新Domain/user settings schema候補: {summary['schema_field_count']}",
        f"- preset対象: {summary['preset_field_count']}",
        f"- Phase 0後の廃止field: {summary['retired_field_count']}",
        "",
        "| Field ID | Category / decision | RNA | New save / codec | Legacy | Preset | Cache | Test | Unit |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for spec in registry["field_specs"]:
        preset = (
            ",".join(spec["preset_families"])
            if spec["preset_families"]
            else "excluded"
        )
        lines.append(
            "| `{field_id}` | {category} / {schema_decision} | `{symbol}` | "
            "{save_policy} / {codec_policy} | {legacy_save_policy} | "
            "{preset} | {cache_policy} | {test_policy} | {unit_conversion} |"
            .format(preset=preset, **spec)
        )
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
