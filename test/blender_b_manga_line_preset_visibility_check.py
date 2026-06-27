"""B-MANGA Line: preset management and line visibility/delete checks."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(name: str, location=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _select(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _line_mods(obj: bpy.types.Object):
    return {mod.name: mod for mod in core.iter_line_modifiers(obj)}


def _assert_line_state(obj: bpy.types.Object, *, visible: bool) -> None:
    mods = _line_mods(obj)
    assert core.MODIFIER_NAME in mods, f"{obj.name}: アウトラインがありません"
    assert core.GN_MODIFIER_NAME in mods, f"{obj.name}: 内部線がありません"
    assert core.INTERSECTION_MODIFIER_NAME in mods, f"{obj.name}: 交差線がありません"
    assert all(mod.show_viewport == visible for mod in mods.values()), mods
    assert all(mod.show_render == visible for mod in mods.values()), mods


def main() -> None:
    b_manga_line.register()
    _clear_scene()

    source = _make_cube("BML_プリセット元", (-2.0, 0.0, 0.0))
    target = _make_cube("BML_交差対象", (0.0, 0.0, 0.0))
    settings = source.bmanga_line_settings
    settings.outline_thickness = 0.012
    settings.outline_color = (0.1, 0.2, 0.3, 1.0)
    settings.use_rim = False
    settings.inner_line_enabled = True
    settings.inner_line_angle = math.radians(12.0)
    settings.inner_line_thickness = 0.021
    settings.intersection_enabled = True
    settings.intersection_method = "SDF"
    settings.intersection_target = target
    settings.intersection_thickness = 0.017
    settings.edge_smooth_factor = -1.0
    settings.edge_midpoint_jitter_percent = 20.0
    settings.edge_width_curve_25 = 0.1
    settings.edge_width_curve_50 = 0.4
    settings.edge_width_curve_75 = 0.8

    scene = bpy.context.scene
    scene.bmanga_line_preset_name = "太線テスト"
    _select(source, [source])
    assert bpy.ops.bmanga_line.preset_save() == {"FINISHED"}
    assert len(scene.bmanga_line_presets) == 1

    settings.outline_thickness = 0.018
    assert bpy.ops.bmanga_line.preset_save() == {"FINISHED"}
    assert len(scene.bmanga_line_presets) == 1
    assert abs(scene.bmanga_line_presets[0].outline_thickness - 0.018) < 1.0e-7

    first = _make_cube("BML_適用先A", (2.0, 0.0, 0.0))
    second = _make_cube("BML_適用先B", (4.0, 0.0, 0.0))
    _select(first, [first, second])
    assert bpy.ops.bmanga_line.preset_apply_selected() == {"FINISHED"}
    for obj in (first, second):
        applied = obj.bmanga_line_settings
        assert abs(applied.outline_thickness - 0.018) < 1.0e-7
        assert tuple(round(v, 3) for v in applied.outline_color) == (0.1, 0.2, 0.3, 1.0)
        assert applied.inner_line_enabled
        assert applied.intersection_enabled
        assert applied.intersection_method == "SDF"
        assert applied.intersection_target == target
        assert abs(applied.edge_width_curve_50 - 0.4) < 1.0e-7
        _assert_line_state(obj, visible=True)

    assert bpy.ops.bmanga_line.set_visibility(visible=False) == {"FINISHED"}
    for obj in (first, second):
        _assert_line_state(obj, visible=False)
        assert bool(obj.get(core.PROP_LINES_HIDDEN, False))

    first.bmanga_line_settings.use_camera_culling = True
    first.bmanga_line_settings.use_camera_culling = False
    first.bmanga_line_settings.use_inner_line_distance_limit = True
    first.bmanga_line_settings.use_inner_line_distance_limit = False
    for obj in (first, second):
        _assert_line_state(obj, visible=False)

    assert bpy.ops.bmanga_line.set_visibility(visible=True) == {"FINISHED"}
    for obj in (first, second):
        _assert_line_state(obj, visible=True)
        assert not bool(obj.get(core.PROP_LINES_HIDDEN, False))

    assert bpy.ops.bmanga_line.remove() == {"FINISHED"}
    for obj in (first, second):
        assert not core.has_line(obj), f"{obj.name}: ラインが残っています"
        assert core.PROP_LINES_HIDDEN not in obj

    assert bpy.ops.bmanga_line.preset_delete() == {"FINISHED"}
    assert len(scene.bmanga_line_presets) == 0

    print("[PASS] line presets apply to selected objects and visibility/delete work")


if __name__ == "__main__":
    main()
