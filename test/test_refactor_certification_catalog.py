"""Pure-Python checks for the deterministic Phase 0 catalog generator."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test" / "fixtures" / "refactor_certification" / "repository"
sys.path.insert(0, str(ROOT))

from tools.refactor_certification import build_catalog
from tools.refactor_certification.catalog import _ownership_violation
from tools.refactor_certification.contracts import OWNER_CONTRACTS, freeze_contract
from tools.refactor_certification.ids import canonical_feature_id, feature_id
from tools.refactor_certification.model import Feature
from tools.refactor_certification.test_scan import (
    _audit_registrations,
    execution_kind,
    test_files as discovered_test_files,
)
from tools.refactor_certification.registry import write_registry


def test_fixture_catalog_extracts_all_required_feature_kinds() -> None:
    catalog = build_catalog(FIXTURE)
    kinds = {feature["kind"] for feature in catalog["features"]}
    assert {"operator", "panel", "property_group", "property", "preset", "shortcut", "export"} <= kinds
    targets = {feature["target"] for feature in catalog["features"]}
    assert {"bmanga", "render", "line"} <= targets
    symbols = {feature["symbol"] for feature in catalog["features"]}
    assert "_preset_private_helper" not in symbols
    exports = [
        feature for feature in catalog["features"] if feature["kind"] == "export"
    ]
    formats = {
        feature["metadata"].get("format")
        for feature in exports
        if feature["metadata"].get("export_role") == "format"
    }
    assert formats == {"PNG", "JPEG", "TIFF", "PSD", "PDF"}
    assert all("exports_dir" not in feature["symbol"] for feature in exports)
    assert any(
        feature["bl_idname"] == "bmanga_render.export_fixture"
        and feature["metadata"].get("export_role") == "entry"
        for feature in exports
    )


def _assert_contracts(features: list[dict[str, object]]) -> None:
    required_contracts = {
        "prerequisite_file_role",
        "input_contract",
        "success_contract",
        "cancel_contract",
        "undo_redo_contract",
        "save_reload_contract",
        "visual_expectation",
        "artifact_expectation",
        "performance_probe",
    }
    assert all(required_contracts <= feature.keys() for feature in features)
    assert all(
        feature["ui_location"]
        and all(feature[name] != "unclassified" for name in required_contracts)
        for feature in features
    )
    assert all(
        feature["metadata"].get("contract_basis") == "phase0_ownership_v5"
        and feature["metadata"].get("state_class")
        and feature["metadata"].get("contract_owner")
        for feature in features
    )


def test_real_codec_ownership_overrides_do_not_use_name_tokens() -> None:
    catalog = build_catalog(ROOT)
    by_symbol = {feature["symbol"]: feature for feature in catalog["features"]}

    display_group = by_symbol["BMangaDisplayItem"]
    assert display_group["metadata"]["state_class"] == "persistent_domain"
    assert (
        display_group["metadata"]["ownership_evidence"]
        == "io/schema.py:display_item_to_dict/display_item_from_dict"
    )
    for symbol in (
        "BMangaDisplayItem.enabled",
        "BMangaDisplayItem.position",
        "BMangaDisplayItem.font_size_q",
        "BMangaDisplayItem.font_size_pt",
        "BMangaDisplayItem.font_size_unit",
        "BMangaDisplayItem.color",
    ):
        feature = by_symbol[symbol]
        assert feature["metadata"]["state_class"] == "persistent_domain"
        assert "保存後の再読込" in feature["save_reload_contract"]

    proxy = by_symbol["BMangaDisplayItem.font_size_value"]
    assert proxy["metadata"]["state_class"] == "derived_value_proxy"
    assert "保存対象field" in proxy["save_reload_contract"]

    for symbol in (
        "BMangaDetailPresetDraft",
        "_BMangaPresetScratchBalloon",
        "_BMangaPresetScratchComa",
    ):
        feature = by_symbol[symbol]
        assert feature["metadata"]["state_class"] == "window_manager_transient"

    assert catalog["summary"]["unverified_property_group_ownership"] == 0


def test_real_nested_and_registered_owners_have_exact_state() -> None:
    catalog = build_catalog(ROOT)
    by_symbol = {feature["symbol"]: feature for feature in catalog["features"]}
    groups = {
        (feature["source"], feature["symbol"])
        for feature in catalog["features"]
        if feature["kind"] == "property_group"
    }
    assert groups == set(OWNER_CONTRACTS)
    expected = {
        "BMangaLinePreset": "user_preset",
        "BMANGA_PresetListItem": "window_manager_transient",
        "BMANGA_DetailPresetListItem": "operator_input",
        "BMangaRubyDictEntry": "user_preferences",
        "BMangaLayerStackItem": "derived_display",
        "BMangaGpToolSettings": "window_manager_transient_proxy",
        "BMangaComaBorder": "multi_context_projection",
        "BMangaLineSettings": "persistent_blender_property",
    }
    for symbol, state in expected.items():
        assert by_symbol[symbol]["metadata"]["state_class"] == state
    assert (
        by_symbol["BMangaLineSettings"]["metadata"]["ownership_evidence"]
        == "Object.bmanga_line_settings"
    )
    owner_states = {
        "BMangaLinePreset": "user_preset",
        "BMangaRubyDictEntry": "user_preferences",
        "BMANGA_DetailPresetListItem": "operator_input",
    }
    for owner, state in owner_states.items():
        fields = [
            feature
            for feature in catalog["features"]
            if feature["kind"] == "property"
            and feature["metadata"].get("owner_name") == owner
        ]
        assert fields
        assert {feature["metadata"]["state_class"] for feature in fields} == {state}


def test_ownership_violation_rejects_a_wrong_but_known_state() -> None:
    feature = Feature(
        feature_id="feature:test",
        kind="property_group",
        target="bmanga",
        source="core/work_info.py",
        symbol="BMangaDisplayItem",
        line=1,
        metadata={"bases": ["PropertyGroup"]},
    )
    freeze_contract(feature)
    assert not _ownership_violation(feature)
    feature.metadata["state_class"] = "derived_display"
    assert _ownership_violation(feature)


def test_ownership_evidence_matches_registration_and_codec_sources() -> None:
    schema = (ROOT / "io" / "schema.py").read_text(encoding="utf-8")
    assert "def display_item_to_dict" in schema
    assert "def display_item_from_dict" in schema
    line_presets = (
        ROOT / "addons" / "b_manga_line" / "presets.py"
    ).read_text(encoding="utf-8")
    assert "bmanga_line_presets = CollectionProperty(" in line_presets
    assert 'options={"SKIP_SAVE"}' in line_presets
    preset_list = (
        ROOT / "panels" / "preset_list_ui.py"
    ).read_text(encoding="utf-8")
    assert "bpy.types.WindowManager" in preset_list
    assert "CollectionProperty(type=BMANGA_PresetListItem)" in preset_list
    detail = (
        ROOT / "operators" / "layer_detail_op.py"
    ).read_text(encoding="utf-8")
    assert "CollectionProperty(" in detail
    assert "type=BMANGA_DetailPresetListItem" in detail
    preferences = (ROOT / "preferences.py").read_text(encoding="utf-8")
    assert "ruby_dictionaries: CollectionProperty(" in preferences
    assert "type=BMangaRubyDictEntry" in preferences
    line_core = (
        ROOT / "addons" / "b_manga_line" / "core.py"
    ).read_text(encoding="utf-8")
    assert "bpy.types.Object.bmanga_line_settings = PointerProperty(" in line_core
    assert "bpy.types.Scene.bmanga_line_settings" not in line_core


def test_getter_setter_properties_are_derived_value_proxies() -> None:
    catalog = build_catalog(ROOT)
    properties = [
        feature
        for feature in catalog["features"]
        if feature["kind"] == "property"
        and (
            feature["metadata"].get("has_get")
            or feature["metadata"].get("has_set")
        )
    ]
    explicit_wm_proxies = {
        "BMangaRenderPreset.active_command_index",
        "BMangaRenderState.active_preset_index",
    }
    assert properties
    for feature in properties:
        if "Operator" in feature["metadata"].get("owner_bases", []):
            expected = "operator_input"
        elif feature["symbol"] in explicit_wm_proxies:
            expected = "window_manager_transient_proxy"
        else:
            expected = "derived_value_proxy"
        assert feature["metadata"]["state_class"] == expected


def test_ids_and_output_are_stable_across_runs() -> None:
    first = build_catalog(FIXTURE)
    second = build_catalog(FIXTURE)
    assert first == second
    feature_ids = [feature["feature_id"] for feature in first["features"]]
    field_ids = [
        feature["field_id"]
        for feature in first["features"]
        if feature["kind"] == "property"
    ]
    assert len(feature_ids) == len(set(feature_ids))
    assert field_ids and all(field_ids)
    _assert_contracts(first["features"])
    assert all(
        count == 0
        for count in first["summary"]["unclassified_contracts"].values()
    )
    assert first["summary"]["contract_basis_mismatches"] == 0
    assert first["summary"]["contract_ownership_violations"] == 0
    assert all(feature["aliases"] for feature in first["features"])
    assert all(
        first["feature_aliases"][alias] == feature["feature_id"]
        for feature in first["features"]
        for alias in feature["aliases"]
    )
    assert all(
        first["field_aliases"][alias] == feature["field_id"]
        for feature in first["features"]
        for alias in feature["field_aliases"]
    )


def test_canonical_ids_do_not_collapse_distinct_japanese_names() -> None:
    first = canonical_feature_id("preset", "bmanga", "item.balloon_tail.ペン線")
    second = canonical_feature_id("preset", "bmanga", "item.balloon_tail.標準")
    assert first != second
    first_alias = feature_id("preset", "bmanga", "presets/borders/a.json", "極太")
    second_alias = feature_id("preset", "bmanga", "presets/borders/a.json", "標準")
    assert first_alias != second_alias


def test_feature_and_field_ids_survive_file_and_class_moves(tmp_path: Path) -> None:
    moved = tmp_path / "repository"
    shutil.copytree(FIXTURE, moved)
    original = moved / "__init__.py"
    relocated = moved / "core" / "relocated.py"
    relocated.parent.mkdir()
    text = original.read_text(encoding="utf-8")
    text = text.replace(
        "class BMANGA_OT_fixture_export", "class RENAMED_OT_fixture_export"
    )
    text = text.replace("class BMANGA_PT_fixture", "class RENAMED_PT_fixture")
    text = text.replace(
        "class BMangaFixtureSettings", "class RenamedFixtureSettings"
    )
    relocated.write_text(text, encoding="utf-8")
    original.write_text("", encoding="utf-8")

    before = build_catalog(FIXTURE)
    after = build_catalog(moved)
    before_ids = {feature["feature_id"] for feature in before["features"]}
    after_ids = {feature["feature_id"] for feature in after["features"]}
    assert before_ids == after_ids
    before_fields = {
        feature["field_id"] for feature in before["features"] if feature["field_id"]
    }
    after_fields = {
        feature["field_id"] for feature in after["features"] if feature["field_id"]
    }
    assert before_fields == after_fields
    assert {
        alias
        for feature in before["features"]
        for alias in feature["aliases"]
    } != {
        alias
        for feature in after["features"]
        for alias in feature["aliases"]
    }


def test_registry_preserves_existing_ids_when_field_is_added(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURE, repository)
    before = build_catalog(repository)
    registry = repository / "docs" / "refactor" / "phase0" / "id_registry.json"
    write_registry(before, registry)
    source = repository / "__init__.py"
    text = source.read_text(encoding="utf-8")
    text = text.replace(
        'enabled: BoolProperty(name="Enabled", default=True)',
        (
            'enabled: BoolProperty(name="Enabled", default=True)\n'
            '    added: BoolProperty(name="Added", default=False)'
        ),
    )
    source.write_text(text, encoding="utf-8")
    after = build_catalog(repository)
    before_by_alias = before["field_aliases"]
    after_by_alias = after["field_aliases"]
    assert all(
        after_by_alias[alias] == identifier
        for alias, identifier in before_by_alias.items()
    )


def test_tests_record_entrypoint_audit_registration_and_untested() -> None:
    catalog = build_catalog(FIXTURE)
    by_source = {test["source"]: test for test in catalog["tests"]}
    blender_test = by_source["test/blender_fixture_check.py"]
    assert blender_test["entrypoint"] is True
    assert blender_test["audit_registered"] is True
    assert blender_test["audit_keys"] == ["fixture_audit"]
    assert any(feature["untested"] for feature in catalog["features"])
    export = next(
        feature
        for feature in catalog["features"]
        if feature["bl_idname"] == "bmanga.fixture_export"
        and feature["kind"] == "operator"
    )
    assert export["test_ids"] == ["test:test.blender.fixture.check"]
    assert export["test_evidence"] == {
        "test:test.blender.fixture.check": ["bmanga.fixture_export"]
    }
    assert export["untested"] is False


def test_repository_inventory_includes_registered_and_suffix_tests() -> None:
    sources = {
        path.relative_to(ROOT).as_posix()
        for path in discovered_test_files(ROOT)
    }
    assert set(_audit_registrations(ROOT)) <= sources
    render_batch = ROOT / "test" / "render_batch_logic_test.py"
    assert render_batch.relative_to(ROOT).as_posix() in sources
    assert execution_kind(render_batch) == "python"
    support = ROOT / "test" / "b_manga_line_test_utils.py"
    assert execution_kind(support) == "support"


def test_product_presets_ignore_test_temporary_directories(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURE, repository)
    leaked = repository / ".phase_tmp" / "presets" / "leaked.json"
    leaked.parent.mkdir(parents=True)
    leaked.write_text(
        '{"presetName": "Temporary Leak"}',
        encoding="utf-8",
    )
    catalog = build_catalog(repository)
    labels = {
        feature["label"]
        for feature in catalog["features"]
        if feature["kind"] == "preset"
    }
    assert "Temporary Leak" not in labels


def test_render_builtin_presets_are_items_not_crud_entries() -> None:
    catalog = build_catalog(ROOT)
    presets = [
        feature
        for feature in catalog["features"]
        if feature["kind"] == "preset" and feature["target"] == "render"
    ]
    builtins = [
        feature
        for feature in presets
        if feature["metadata"].get("source_kind") == "python_builtin"
    ]
    assert len(builtins) == 39
    assert all(
        feature["metadata"].get("preset_role") != "entry"
        for feature in presets
    )


def test_contracts_distinguish_persistent_transient_and_proxy_state() -> None:
    catalog = build_catalog(ROOT)
    features = catalog["features"]
    scratch = next(
        feature
        for feature in features
        if feature["kind"] == "property"
        and "_BMangaPresetScratchBalloon." in feature["symbol"]
    )
    assert scratch["metadata"]["state_class"] == "window_manager_transient"
    assert "保存しない" in scratch["save_reload_contract"]

    base = next(
        feature
        for feature in features
        if feature["symbol"] == "BMangaLineSettings.outline_color"
    )
    assert base["metadata"]["state_class"] == "persistent_blender_property"
    assert "blend保存・再読込" in base["save_reload_contract"]

    draft = next(
        feature
        for feature in features
        if feature["symbol"] == "BMangaLineSettingsDraft.outline_color"
    )
    assert draft["metadata"]["state_class"] == "window_manager_transient_proxy"
    assert draft["field_id"] == base["field_id"]
    assert draft["metadata"]["proxy_symbol"] == base["symbol"]
    assert "一時field自体は保存しない" in draft["save_reload_contract"]

    preferences = next(
        feature
        for feature in features
        if feature["kind"] == "property"
        and "AddonPreferences" in feature["metadata"].get("owner_bases", [])
    )
    assert preferences["metadata"]["state_class"] == "user_preferences"
    assert "user config" in preferences["save_reload_contract"]

    operator_input = next(
        feature
        for feature in features
        if feature["kind"] == "property"
        and "Operator" in feature["metadata"].get("owner_bases", [])
    )
    assert operator_input["metadata"]["state_class"] == "operator_input"
    assert "作品・user configへ保存しない" in operator_input["save_reload_contract"]


def test_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    json_out = tmp_path / "catalog.json"
    markdown_out = tmp_path / "catalog.md"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.refactor_certification",
            "--root",
            str(FIXTURE),
            "--json-out",
            str(json_out),
                "--markdown-out",
                str(markdown_out),
                "--allow-unverified-ownership",
            ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["summary"]["feature_count"] > 0
    markdown = markdown_out.read_text(encoding="utf-8")
    assert "Feature Contract Catalog" in markdown
    assert "Untested features:" in markdown
