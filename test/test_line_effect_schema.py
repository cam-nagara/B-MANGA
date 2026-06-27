from __future__ import annotations

import importlib.util
from pathlib import Path


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


def test_path_image_choices_are_shared_ui_contract():
    schema = _load_schema()
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


if __name__ == "__main__":
    test_path_image_choices_are_shared_ui_contract()
    test_effect_param_fields_have_no_duplicates()
    test_path_image_fields_are_saved_and_linked()
    test_linked_effect_fields_do_not_sync_uni_flash_offset()
    test_balloon_flash_fields_match_shared_effect_basics()
