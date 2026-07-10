from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace


def _load_schema():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "line_effect_schema",
        root / "utils" / "line_effect_schema.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_settings_ui():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "line_effect_settings_ui",
        root / "panels" / "line_effect_settings_ui.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_inout_curve():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "effect_inout_curve",
        root / "utils" / "effect_inout_curve.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_path_image_choices_are_shared_ui_contract():
    schema = _load_schema()
    assert [item[0] for item in schema.PATH_CONTENT_SOURCE_ITEMS] == ["image", "shape"]
    assert [item[0] for item in schema.PATH_GENERATED_SHAPE_ITEMS] == [
        "circle",
        "square",
        "polygon",
        "star",
        "heart",
    ]
    assert [item[0] for item in schema.PATH_IMAGE_DRAW_MODE_ITEMS] == ["stamp", "ribbon"]
    assert [item[0] for item in schema.PATH_IMAGE_STAMP_ANGLE_MODE_ITEMS] == [
        "fixed",
        "line",
        "object",
    ]
    assert [item[0] for item in schema.PATH_IMAGE_RIBBON_REPEAT_MODE_ITEMS] == [
        "repeat",
        "stretch",
    ]


def test_effect_param_fields_have_no_duplicates():
    schema = _load_schema()
    fields = list(schema.EFFECT_PARAM_FIELDS)
    assert len(fields) == len(set(fields))
    assert fields[0:2] == ["effect_type", "rotation_deg"]
    assert "white_outline_angle_deg" in fields


def test_path_image_fields_are_saved_and_linked():
    schema = _load_schema()
    saved = set(schema.EFFECT_PARAM_FIELDS)
    linked = set(schema.EFFECT_LINKED_SHAPE_FIELDS)
    for field in schema.EFFECT_PATH_IMAGE_FIELDS:
        assert field in saved
        assert field in linked
    for field in (
        "line_image_source",
        "line_image_shape_kind",
        "line_image_shape_sides",
        "line_image_color",
        "line_image_inout_size_enabled",
        "line_image_inout_opacity_enabled",
        "line_image_inout_color_enabled",
        "line_image_inout_start_color",
        "line_image_inout_end_color",
    ):
        assert field in schema.EFFECT_PATH_IMAGE_FIELDS


def test_linked_effect_fields_do_not_sync_uni_flash_offset():
    schema = _load_schema()
    assert "uni_flash_offset_percent" in schema.EFFECT_PARAM_FIELDS
    assert "uni_flash_offset_percent" not in schema.EFFECT_LINKED_SHAPE_FIELDS


def test_balloon_flash_fields_match_shared_effect_basics():
    schema = _load_schema()
    balloon_fields = set(schema.BALLOON_UNI_FLASH_PARAM_FIELDS)
    for field in (*schema.EFFECT_START_SHAPE_FIELDS, *schema.EFFECT_END_SHAPE_FIELDS):
        assert field in balloon_fields
    for field in schema.EFFECT_INOUT_FIELDS:
        assert field in balloon_fields


def test_white_outline_ui_maps_stay_in_shared_field_contract():
    schema = _load_schema()
    settings_ui = _load_settings_ui()
    effect_ui_fields = set(settings_ui.EFFECT_WHITE_OUTLINE_UI_FIELDS.values())
    balloon_ui_fields = set(settings_ui.BALLOON_WHITE_OUTLINE_UI_FIELDS.values())
    assert effect_ui_fields <= set(schema.EFFECT_WHITE_OUTLINE_FIELDS)
    assert not (balloon_ui_fields & set(schema.EFFECT_PATH_IMAGE_FIELDS))
    assert "white_outline_white_brush_mm" not in balloon_ui_fields
    assert "white_outline_white_inout_range_mode" not in balloon_ui_fields
    black_inout_fields = {
        "white_outline_black_in_percent",
        "white_outline_black_out_percent",
        "white_outline_black_inout_range_mode",
        "white_outline_black_in_range_percent",
        "white_outline_black_out_range_percent",
        "white_outline_black_in_range_mm",
        "white_outline_black_out_range_mm",
    }
    assert black_inout_fields <= effect_ui_fields
    assert black_inout_fields <= balloon_ui_fields
    assert black_inout_fields <= set(schema.EFFECT_WHITE_OUTLINE_FIELDS)
    assert black_inout_fields <= set(schema.BALLOON_UNI_FLASH_PARAM_FIELDS)
    assert black_inout_fields <= set(schema.EFFECT_LINKED_SHAPE_FIELDS)
    for field in (
        "white_outline_angle_deg",
        "white_outline_white_line_count_auto",
        "white_outline_black_direction",
        "white_outline_black_width_scale_percent",
    ):
        assert field in balloon_ui_fields


def test_inout_profile_graph_points_follow_numeric_values():
    curve = _load_inout_curve()
    params = SimpleNamespace(
        in_percent=25.0,
        out_percent=10.0,
        in_start_percent=40.0,
        out_start_percent=30.0,
        in_easing_curve=curve.DEFAULT_CURVE_TEXT,
        out_easing_curve=curve.DEFAULT_CURVE_TEXT,
    )
    points = curve.profile_points_from_params(params)
    assert math.isclose(points[0][0], 0.0, abs_tol=1e-6)
    assert math.isclose(points[0][1], 0.25, abs_tol=1e-6)
    assert any(math.isclose(x, 0.4, abs_tol=1e-6) and math.isclose(y, 1.0, abs_tol=1e-6) for x, y in points)
    assert any(math.isclose(x, 0.7, abs_tol=1e-6) and math.isclose(y, 1.0, abs_tol=1e-6) for x, y in points)
    assert math.isclose(points[-1][0], 1.0, abs_tol=1e-6)
    assert math.isclose(points[-1][1], 0.10, abs_tol=1e-6)


def test_inout_profile_graph_points_update_numeric_values():
    curve = _load_inout_curve()
    params = SimpleNamespace(
        in_percent=100.0,
        out_percent=100.0,
        in_start_percent=0.0,
        out_start_percent=0.0,
        in_easing_curve="",
        out_easing_curve="",
    )
    changed = curve.profile_points_to_params(params, ((0.0, 0.2), (0.35, 1.0), (0.75, 1.0), (1.0, 0.05)))
    assert changed
    assert math.isclose(params.in_percent, 20.0, abs_tol=1e-4)
    assert math.isclose(params.out_percent, 5.0, abs_tol=1e-4)
    assert math.isclose(params.in_start_percent, 35.0, abs_tol=1e-4)
    assert math.isclose(params.out_start_percent, 25.0, abs_tol=1e-4)
    assert params.in_easing_curve == curve.DEFAULT_CURVE_TEXT
    assert params.out_easing_curve == curve.DEFAULT_CURVE_TEXT


if __name__ == "__main__":
    test_path_image_choices_are_shared_ui_contract()
    test_effect_param_fields_have_no_duplicates()
    test_path_image_fields_are_saved_and_linked()
    test_linked_effect_fields_do_not_sync_uni_flash_offset()
    test_balloon_flash_fields_match_shared_effect_basics()
    test_white_outline_ui_maps_stay_in_shared_field_contract()
    test_inout_profile_graph_points_follow_numeric_values()
    test_inout_profile_graph_points_update_numeric_values()
