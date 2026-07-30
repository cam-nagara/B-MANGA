"""Settings Contract generatorの分類規則。"""

from __future__ import annotations

from collections import Counter
from typing import Any


SCHEMA_VERSION = 1

STATE_CATEGORY = {
    "persistent_domain": "persistent_domain",
    "persistent_blender_property": "persistent_domain",
    "multi_context_projection": "persistent_domain",
    "user_preferences": "user_setting",
    "operator_input": "session_state",
    "window_manager_transient": "session_state",
    "window_manager_transient_proxy": "session_state",
    "derived_value_proxy": "derived_display",
    "derived_display": "derived_display",
}

SAVE_POLICIES = {
    "persistent_domain": "domain_codec",
    "persistent_blender_property": "blender_datablock",
    "multi_context_projection": "domain_or_preset_context",
    "user_preferences": "user_config",
    "operator_input": "not_saved",
    "window_manager_transient": "not_saved",
    "window_manager_transient_proxy": "not_saved",
    "derived_value_proxy": "not_saved",
    "derived_display": "not_saved",
}

CODEC_POLICIES = {
    "persistent_domain": "json_or_blend_codec",
    "persistent_blender_property": "blend_codec",
    "multi_context_projection": "context_selected_codec",
    "user_preferences": "blender_preferences_codec",
    "operator_input": "none",
    "window_manager_transient": "none",
    "window_manager_transient_proxy": "none",
    "derived_value_proxy": "source_field_codec",
    "derived_display": "none",
}

SESSION_FIELD_REASONS = {
    "BMangaLayerFolder.expanded": "レイヤー一覧の開閉状態",
    "BMangaPageEntry.active_balloon_index": "現在選択中のフキダシ",
    "BMangaPageEntry.active_coma_index": "現在選択中のコマ",
    "BMangaPageEntry.active_text_index": "現在選択中のテキスト",
    "BMangaPageEntry.detail_loaded": "ページ詳細の実行時読込状態",
    "BMangaPageEntry.stack_expanded": "統合レイヤー一覧の開閉状態",
    "BMangaComaCameraSettings.camera_angles_index": "カメラ一覧の選択行",
    "BMangaComaCameraSettings.prev_render_engine": "一時切替前の復元用値",
    "BMangaWorkData.active_page_index": "現在選択中のページ",
    "BMangaWorkData.loaded": "作品の実行時読込状態",
    "BMangaWorkData.work_dir": "開いているRepositoryの実行時locator",
}

DERIVED_FIELD_REASONS = {
    "BMangaBalloonEntry.corner_type_initialized": "旧データ既定値補正の初期化印",
    "BMangaBalloonEntry.end_rounded_corner_enabled": "end_corner_typeから再計算する旧互換投影",
    "BMangaBalloonEntry.rounded_corner_enabled": "corner_typeから再計算する旧互換投影",
    "BMangaBalloonEntry.start_rounded_corner_enabled": "start_corner_typeから再計算する旧互換投影",
    "BMangaPageEntry.coma_count": "ページ内容から再計算できる件数cache",
    "BMangaPageEntry.in_page_range": "作品のページ範囲から再計算する表示値",
    "BMangaPageEntry.thumbnail_rel": "再生成可能なthumbnail cache locator",
}


def _options(feature: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str(value)
        for value in feature.get("metadata", {}).get("options", ())
    )


def classification_reason(feature: dict[str, Any]) -> str:
    symbol = str(feature["symbol"])
    if feature["target"] in {"line", "render"}:
        return "独立アドオンが所有する統合境界"
    if "SKIP_SAVE" in _options(feature):
        return "RNA SKIP_SAVEで宣言された一時状態"
    if symbol in SESSION_FIELD_REASONS:
        return SESSION_FIELD_REASONS[symbol]
    if symbol in DERIVED_FIELD_REASONS:
        return DERIVED_FIELD_REASONS[symbol]
    state = str(feature["metadata"]["state_class"])
    return f"Phase 0 ownership分類を維持: {state}"


def category_for(feature: dict[str, Any]) -> str:
    if feature["target"] in {"line", "render"}:
        return "external_integration"
    if "SKIP_SAVE" in _options(feature):
        return "session_state"
    symbol = str(feature["symbol"])
    if symbol in SESSION_FIELD_REASONS:
        return "session_state"
    if symbol in DERIVED_FIELD_REASONS:
        return "derived_display"
    state = str(feature["metadata"]["state_class"])
    try:
        return STATE_CATEGORY[state]
    except KeyError as exc:
        raise ValueError(f"unclassified property state: {state}") from exc


def save_policy_for(feature: dict[str, Any], category: str) -> str:
    if category == "external_integration":
        return "external_addon_owned"
    if category in {"session_state", "derived_display"}:
        return "not_saved"
    return SAVE_POLICIES[str(feature["metadata"]["state_class"])]


def codec_policy_for(feature: dict[str, Any], category: str) -> str:
    if category == "external_integration":
        return "external_addon_codec"
    if category == "session_state":
        return "none"
    if category == "derived_display":
        return "source_field_codec"
    return CODEC_POLICIES[str(feature["metadata"]["state_class"])]


def legacy_save_policy_for(feature: dict[str, Any]) -> str:
    if feature["target"] in {"line", "render"}:
        return "external_addon_owned"
    if "SKIP_SAVE" in _options(feature):
        return "rna_skip_save"
    return SAVE_POLICIES[str(feature["metadata"]["state_class"])]


def schema_decision_for(category: str) -> str:
    return {
        "persistent_domain": "include_persistent_domain",
        "user_setting": "include_user_setting",
        "session_state": "exclude_session_state",
        "derived_display": "exclude_derived_display",
        "external_integration": "exclude_external_addon_owned",
    }[category]


def dirty_policy_for(category: str) -> str:
    return {
        "persistent_domain": "mark_domain_dirty",
        "user_setting": "mark_user_config_dirty",
        "session_state": "session_only",
        "derived_display": "never_dirty",
        "external_integration": "external_addon_owned",
    }[category]


def cache_policy_for(
    category: str,
    cache_dependencies: tuple[str, ...],
) -> str:
    if cache_dependencies:
        return "invalidate_declared_dependents"
    return {
        "persistent_domain": "no_cache_dependencies",
        "user_setting": "no_cache_dependencies",
        "session_state": "no_persistent_cache",
        "derived_display": "derived_cache_only",
        "external_integration": "external_addon_owned",
    }[category]


def test_policy_for(category: str) -> str:
    return {
        "persistent_domain": "load_change_cancel_save_characterization",
        "user_setting": "load_change_cancel_save_characterization",
        "session_state": "session_lifetime_characterization",
        "derived_display": "recompute_characterization",
        "external_integration": "external_addon_contract",
    }[category]


def unit_conversion_for(feature: dict[str, Any]) -> str:
    metadata = feature["metadata"]
    unit = str(metadata.get("unit", "") or "").upper()
    subtype = str(metadata.get("subtype", "") or "").upper()
    name = str(feature["symbol"]).rsplit(".", 1)[-1].lower()
    if unit == "ROTATION":
        return "ui_degrees_internal_radians"
    if subtype == "COLOR":
        return "ui_srgb_internal_linear"
    # B-MANGAの`*_mm`はJSON・geometry・テストの全経路でmm値そのものを
    # 保持する。Blenderのunit表示指定より先に製品の保存単位を優先する。
    if name.endswith("_mm"):
        return "ui_mm_internal_mm"
    if unit == "LENGTH":
        return "blender_scene_length"
    if name == "percentage" or name.endswith(
        ("_percent", "_percentage", "_pct")
    ):
        return "ui_percent_internal_percent"
    return "identity"


def summarize(specs: list[dict[str, Any]]) -> dict[str, Any]:
    categories = Counter(spec["category"] for spec in specs)
    states = Counter(spec["state_class"] for spec in specs)
    targets = Counter(spec["target"] for spec in specs)
    canonical_ids = {spec["field_id"] for spec in specs}
    schema_ids = {
        spec["field_id"] for spec in specs if bool(spec["schema_member"])
    }
    preset_ids = {
        spec["field_id"]
        for spec in specs
        if spec["preset_policy"] == "included"
    }
    return {
        "property_binding_count": len(specs),
        "field_count": len(canonical_ids),
        "projection_count": len(specs) - len(canonical_ids),
        "schema_field_count": len(schema_ids),
        "preset_field_count": len(preset_ids),
        "counts_by_category": dict(sorted(categories.items())),
        "counts_by_state_class": dict(sorted(states.items())),
        "counts_by_target": dict(sorted(targets.items())),
    }
