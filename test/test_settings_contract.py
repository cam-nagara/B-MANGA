from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bmanga_core.settings_contract import (
    FieldCategory,
    SettingsContractError,
    UnitConversion,
    load_settings_registry,
    require_rna_field,
    to_internal,
    to_ui,
)
from tools.settings_contract import build_settings_contract
from tools.settings_contract.build import render_markdown


REGISTRY_PATH = ROOT / "bmanga_core" / "settings_field_specs.json"
MATRIX_PATH = ROOT / "docs" / "refactor" / "phase2" / "settings_matrix.md"
UI_PATH = ROOT / "docs" / "refactor" / "phase2" / "detail_ui_matrix.json"
JSON_CODEC_PATH = ROOT / "tools" / "settings_contract" / "json_codec_fields.json"
CACHE_SIGNATURE_PATH = (
    ROOT / "tools" / "settings_contract" / "cache_signature_fields.json"
)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_generated_settings_contract_matches_current_source_exactly():
    registry, ui_matrix = build_settings_contract(ROOT)
    normalized_registry = json.loads(
        json.dumps(registry, ensure_ascii=False)
    )
    normalized_ui = json.loads(json.dumps(ui_matrix, ensure_ascii=False))
    assert _json(REGISTRY_PATH) == normalized_registry
    assert _json(UI_PATH) == normalized_ui
    assert MATRIX_PATH.read_text(encoding="utf-8") == render_markdown(registry)
    summary = registry["summary"]
    assert summary["property_binding_count"] == 1642
    assert summary["field_count"] == 1567
    assert summary["projection_count"] == 75
    assert summary["schema_field_count"] == 840
    assert summary["preset_field_count"] == 631
    assert summary["retired_field_count"] == 3


def test_every_property_projection_declares_all_phase2_policies():
    payload = _json(REGISTRY_PATH)
    required = {
        "classification_reason",
        "schema_decision",
        "legacy_save_policy",
        "save_policy",
        "codec_policy",
        "codec_bindings",
        "preset_policy",
        "dirty_policy",
        "dirty_bindings",
        "cache_policy",
        "cache_dependencies",
        "test_policy",
        "test_ids",
        "unit_conversion",
    }
    schema_categories = {"persistent_domain", "user_setting"}
    seen_bindings = set()
    for spec in payload["field_specs"]:
        assert required <= set(spec)
        assert all(str(spec[name]).strip() for name in required)
        assert bool(spec["schema_member"]) == (
            spec["category"] in schema_categories
        )
        assert bool(spec["preset_families"]) == (
            spec["preset_policy"] == "included"
        )
        assert spec["codec_bindings"]
        assert spec["dirty_bindings"]
        field_target = f"{spec['owner_name']}.{spec['field_name']}"
        assert all(field_target in binding for binding in spec["codec_bindings"])
        assert all(field_target in binding for binding in spec["dirty_bindings"])
        if spec["category"] in schema_categories:
            assert f"characterization:{spec['field_id']}" in spec["test_ids"]
        if spec["category"] == "persistent_domain":
            assert any(
                binding.startswith("blend-rna:")
                for binding in spec["codec_bindings"]
            )
        if spec["category"] == "user_setting":
            assert all(
                binding.startswith("userpref:")
                for binding in spec["codec_bindings"]
            )
        if spec["cache_policy"] == "invalidate_declared_dependents":
            assert spec["cache_dependencies"]
            assert all(
                dependency.startswith("signature:")
                for dependency in spec["cache_dependencies"]
            )
            assert f"cache-characterization:{spec['field_id']}" in (
                spec["test_ids"]
            )
        else:
            assert not spec["cache_dependencies"]
        binding = (spec["owner_name"], spec["field_name"])
        assert binding not in seen_bindings
        seen_bindings.add(binding)


def test_new_schema_explicitly_excludes_runtime_and_derived_legacy_fields():
    registry = load_settings_registry(REGISTRY_PATH)
    expected = {
        "BMangaBalloonEntry.corner_type_initialized": (
            FieldCategory.DERIVED_DISPLAY,
            "exclude_derived_display",
        ),
        "BMangaPageEntry.thumbnail_rel": (
            FieldCategory.DERIVED_DISPLAY,
            "exclude_derived_display",
        ),
        "BMangaPageEntry.coma_count": (
            FieldCategory.DERIVED_DISPLAY,
            "exclude_derived_display",
        ),
            "BMangaPageEntry.detail_loaded": (
                FieldCategory.SESSION_STATE,
                "exclude_session_state",
            ),
            "BMangaWorkData.loaded": (
                FieldCategory.SESSION_STATE,
                "exclude_session_state",
            ),
            "BMangaWorkData.active_page_index": (
                FieldCategory.SESSION_STATE,
                "exclude_session_state",
        ),
        "BMangaWorkData.work_dir": (
            FieldCategory.SESSION_STATE,
            "exclude_session_state",
        ),
    }
    by_symbol = {spec.symbol: spec for spec in registry.specs}
    for symbol, (category, decision) in expected.items():
        spec = by_symbol[symbol]
        assert spec.category is category
        assert spec.schema_decision == decision
        assert not spec.schema_member
        assert spec.save_policy == "not_saved"
        assert spec.legacy_save_policy
        assert spec.classification_reason
    skip_save = [
        spec
        for spec in registry.specs
        if spec.legacy_save_policy == "rna_skip_save"
    ]
    assert len(skip_save) == 11
    assert {
        "BMangaPageEntry.detail_loaded",
        "BMangaWorkData.loaded",
    } <= {spec.symbol for spec in skip_save}
    assert all(spec.category is FieldCategory.SESSION_STATE for spec in skip_save)


def test_phase0_aliases_are_preserved_or_retired_explicitly():
    payload = _json(REGISTRY_PATH)
    active = {
        alias: spec["field_id"]
        for spec in payload["field_specs"]
        for alias in spec["aliases"]
    }
    retired = {
        alias: row["field_id"]
        for row in payload["retired_fields"]
        for alias in row["aliases"]
    }
    normalized = _json(ROOT / "docs" / "refactor" / "phase0" / "id_registry.json")
    for alias, field_id in normalized["field_aliases"].items():
        assert active.get(alias, retired.get(alias)) == field_id


def test_detail_ui_matrix_has_explicit_resolution_and_visibility_contracts():
    payload = _json(UI_PATH)
    assert payload["binding_count"] == len(payload["bindings"])
    assert payload["enabled_rule_count"] == len(payload["enabled_rules"])
    contracts = {
        "field_spec",
        "custom_property",
        "blender_builtin_rna",
        "dynamic_runtime_resolved",
    }
    for binding in payload["bindings"]:
        assert binding["resolution_contract"] in contracts
        assert isinstance(binding["visibility_conditions"], list)
        if binding["resolution_contract"] == "field_spec":
            assert binding["candidate_field_ids"]
        if binding["resolution_contract"] == "custom_property":
            assert binding["custom_property"]
    assert payload["enabled_rules"]
    assert all(
        str(row["enabled_expression"]).strip()
        for row in payload["enabled_rules"]
    )


def test_registry_separates_schema_session_derived_and_external_fields():
    registry = load_settings_registry(REGISTRY_PATH)
    assert len(registry.specs) == 1642
    assert len(registry.canonical_specs) == 1567
    assert len(registry.schema_specs) == 840
    assert all(
        spec.category
        in {FieldCategory.USER_SETTING, FieldCategory.PERSISTENT_DOMAIN}
        for spec in registry.schema_specs
    )
    assert not any(
        spec.schema_member
        for spec in registry.canonical_specs
        if spec.category
        in {
            FieldCategory.SESSION_STATE,
            FieldCategory.DERIVED_DISPLAY,
            FieldCategory.EXTERNAL_INTEGRATION,
        }
    )


def test_detail_rna_lookup_rejects_unregistered_app_field_but_allows_blender_rna():
    app_owner = SimpleNamespace(
        bl_rna=SimpleNamespace(identifier="BMangaTextEntry")
    )
    spec = require_rna_field(app_owner, "font_size_value")
    assert spec is not None
    assert spec.symbol == "BMangaTextEntry.font_size_value"
    with pytest.raises(SettingsContractError, match="unregistered RNA field"):
        require_rna_field(app_owner, "new_field_without_contract")
    blender_owner = SimpleNamespace(
        bl_rna=SimpleNamespace(identifier="MaterialGPencilStyle")
    )
    assert require_rna_field(blender_owner, "show_stroke") is None


@pytest.mark.parametrize(
    ("conversion", "ui_value"),
    [
        (UnitConversion.IDENTITY, 12.5),
        (UnitConversion.UI_MM_INTERNAL_MM, 4.2),
        (UnitConversion.UI_PERCENT_INTERNAL_PERCENT, 75.0),
        (UnitConversion.UI_DEGREES_INTERNAL_RADIANS, 137.0),
        (UnitConversion.BLENDER_SCENE_LENGTH, 2.5),
    ],
)
def test_scalar_unit_conversions_roundtrip(conversion, ui_value):
    internal = to_internal(ui_value, conversion, scale_length=0.1)
    restored = to_ui(internal, conversion, scale_length=0.1)
    assert math.isclose(restored, ui_value, rel_tol=0.0, abs_tol=1.0e-9)


def test_color_unit_conversion_matches_blender_ui_linear_contract():
    ui = (0.7, 0.2, 0.0, 0.35)
    internal = to_internal(ui, UnitConversion.UI_SRGB_INTERNAL_LINEAR)
    assert math.isclose(internal[0], 0.447988, abs_tol=1.0e-5)
    restored = to_ui(internal, UnitConversion.UI_SRGB_INTERNAL_LINEAR)
    assert restored == pytest.approx(ui, abs=1.0e-9)
    with pytest.raises(SettingsContractError):
        to_internal((1.0, 0.0), UnitConversion.UI_SRGB_INTERNAL_LINEAR)


def test_percentage_named_fields_use_the_explicit_ui_percentage_contract():
    registry = load_settings_registry(REGISTRY_PATH)
    names = {
        "BMangaWorkData.page_preview_scale_percentage",
        "BMangaPreferences.coma_thumb_scale_percentage",
        "BMANGA_RENDER_OT_set_reduction_scale.percentage",
    }
    by_symbol = {spec.symbol: spec for spec in registry.specs}
    assert all(
        by_symbol[name].unit_conversion
        is UnitConversion.UI_PERCENT_INTERNAL_PERCENT
        for name in names
    )


def test_mm_named_fields_take_precedence_over_blender_length_metadata():
    registry = load_settings_registry(REGISTRY_PATH)
    by_symbol = {spec.symbol: spec for spec in registry.specs}
    assert (
        by_symbol["BMangaTextEntry.ruby_gap_mm"].unit_conversion
        is UnitConversion.UI_MM_INTERNAL_MM
    )
    assert (
        by_symbol["BMangaImagePathLayer.brush_size_mm"].unit_conversion
        is UnitConversion.UI_MM_INTERNAL_MM
    )


def test_json_and_cache_bindings_are_exact_characterization_artifacts():
    payload = _json(REGISTRY_PATH)
    json_bindings = _json(JSON_CODEC_PATH)["field_bindings"]
    cache_bindings = _json(CACHE_SIGNATURE_PATH)["field_bindings"]
    registry_json = {
        spec["field_id"]: sorted(
            binding.removeprefix("json-adapter:").removesuffix(
                f":{spec['owner_name']}.{spec['field_name']}"
            )
            for binding in spec["codec_bindings"]
            if binding.startswith("json-adapter:")
        )
        for spec in payload["field_specs"]
        if any(
            binding.startswith("json-adapter:")
            for binding in spec["codec_bindings"]
        )
    }
    registry_cache = {
        spec["field_id"]: spec["cache_dependencies"]
        for spec in payload["field_specs"]
        if spec["cache_dependencies"]
    }
    assert registry_json == json_bindings
    assert registry_cache == cache_bindings
    by_symbol = {spec["symbol"]: spec for spec in payload["field_specs"]}
    brush = by_symbol["BMangaBalloonEntry.brush_size_mm"]
    assert any(
        "balloon_flash_effect_line_mesh._mesh_signature[uni_flash]"
        in binding
        for binding in brush["cache_dependencies"]
    )
    for symbol in (
        "BMangaBalloonEntry.brush_size_mm",
        "BMangaBalloonEntry.bundle_line_count",
        "BMangaBalloonEntry.white_outline_angle_deg",
    ):
        assert any(
            binding.startswith(
                "json-adapter:io.schema.balloon_entry_to_dict/"
                "io.schema.balloon_entry_from_dict:"
            )
            for binding in by_symbol[symbol]["codec_bindings"]
        )
    assert not any(
        binding.startswith("json-adapter:")
        for symbol in (
            "BMangaBalloonEntry.title",
            "BMangaBalloonEntry.blend_mode",
        )
        for binding in by_symbol[symbol]["codec_bindings"]
    )
def test_preset_registry_includes_nested_and_coma_display_fields():
    registry = load_settings_registry(REGISTRY_PATH)
    by_symbol = {spec.symbol: spec for spec in registry.specs}
    expected = {
        "BMangaBalloonEntry.brush_size_mm": "balloon",
        "BMangaBalloonEntry.white_outline_black_out_easing_curve": "balloon",
        "BMangaBalloonShapeParams.cloud_bump_width_mm": "balloon",
        "BMangaComaEntry.paper_visible": "border",
        "BMangaComaEntry.background_color": "border",
        "BMangaDisplayItem.enabled": "paper",
        "BMangaLinePreset.outline_thickness": "line",
        "BMangaRenderCommand.command_type": "render",
    }
    for symbol, family in expected.items():
        assert family in by_symbol[symbol].preset_families
