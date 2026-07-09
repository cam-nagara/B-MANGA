"""B-MANGA Line: settings, preset storage, and roundtrip contract."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

import b_manga_line  # noqa: E402
from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402
from b_manga_line import core, presets  # noqa: E402


DISPLAY_ALIAS_FIELDS = {
    "outline_thickness_mm",
    "inner_line_thickness_mm",
    "intersection_thickness_mm",
    "selection_line_thickness_mm",
}
PRESET_COMPAT_FIELDS = {"line_only_visible"}
RUNTIME_ONLY_FIELDS = {"settings_locked"}


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _annotation_names(cls) -> tuple[str, ...]:
    return tuple(getattr(cls, "__annotations__", {}).keys())


def _assert_field_contract() -> None:
    settings_fields = set(_annotation_names(core.BMangaLineSettings))
    preset_fields = tuple(_annotation_names(presets.BMangaLinePreset))
    saved_preset_fields = tuple(
        name for name in preset_fields if name not in PRESET_COMPAT_FIELDS
    )
    stored_fields = tuple(presets._SETTING_FIELDS)

    assert stored_fields == saved_preset_fields, (
        "プリセットPropertyGroupと保存対象の順序が一致していません",
        stored_fields,
        saved_preset_fields,
    )
    expected_not_stored = (
        DISPLAY_ALIAS_FIELDS | PRESET_COMPAT_FIELDS | RUNTIME_ONLY_FIELDS
    )
    missing = settings_fields - set(stored_fields) - expected_not_stored
    extra = set(stored_fields) - settings_fields
    assert not missing, f"保存対象から漏れた設定: {sorted(missing)}"
    assert not extra, f"存在しない設定が保存対象です: {sorted(extra)}"
    assert settings_fields - set(stored_fields) == expected_not_stored


def _set_without_updates(settings, values: dict[str, object]) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for name, value in values.items():
            setattr(settings, name, value)
    finally:
        core._propagating = old


def _sample_value(settings, name: str):
    current = getattr(settings, name)
    if name in presets._COLOR_FIELDS:
        return (0.13, 0.27, 0.41, 1.0)
    if name == "intersection_method":
        return "BOOLEAN"
    if isinstance(current, bool):
        return not current
    if "creation_max_distance" in name or name.endswith("_max_distance"):
        return 7.25
    if name == "line_width_reference_distance":
        return 3.5
    if "angle" in name or name == "culling_margin":
        return 0.44
    if "jitter_percent" in name:
        return 12.5
    if "width_curve" in name:
        return 0.31
    if "offset" in name:
        return 0.37
    if name == "bump_line_thickness":
        return 0.2
    if "thickness" in name:
        return 0.002
    if "influence" in name:
        return 0.42
    if "smooth_factor" in name:
        return 0.35
    return 0.23


def _assert_values_match(expected, actual, name: str, label: str) -> None:
    if name in presets._COLOR_FIELDS:
        assert all(
            math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1.0e-7)
            for a, b in zip(expected, actual)
        ), (label, expected, actual)
        return
    if isinstance(expected, float):
        assert math.isclose(
            float(actual),
            expected,
            rel_tol=0.0,
            abs_tol=1.0e-7,
        ), (label, expected, actual)
        return
    assert actual == expected, (label, expected, actual)


def _assert_preset_roundtrip() -> None:
    source = _make_cube("BML_contract_source", (0.0, 0.0, 0.0))
    target = _make_cube("BML_contract_target", (2.0, 0.0, 0.0))
    sample_values = {
        name: _sample_value(source.bmanga_line_settings, name)
        for name in presets._SETTING_FIELDS
    }
    _set_without_updates(source.bmanga_line_settings, sample_values)

    preset = bpy.context.scene.bmanga_line_presets.add()
    assert preset.use_uniform_line_width is True
    assert math.isclose(
        preset.line_width_distance_falloff,
        1.0,
        rel_tol=0.0,
        abs_tol=1.0e-7,
    )
    preset.name = "契約テスト"
    presets.copy_settings_to_preset(source.bmanga_line_settings, preset)
    presets.copy_preset_to_settings(preset, target.bmanga_line_settings)

    for name, expected in sample_values.items():
        _assert_values_match(
            expected,
            getattr(preset, name),
            name,
            f"preset.{name}",
        )
        _assert_values_match(
            expected,
            getattr(target.bmanga_line_settings, name),
            name,
            f"settings.{name}",
        )


def _run() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        _assert_field_contract()
        _assert_preset_roundtrip()
        print("BMANGA_LINE_SETTING_CONTRACT_OK")
        print("保存対象:", ", ".join(presets._SETTING_FIELDS))
        print("表示用別名:", ", ".join(sorted(DISPLAY_ALIAS_FIELDS)))
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


def main() -> None:
    with temporary_line_preset_store():
        _run()


if __name__ == "__main__":
    main()
