"""B-MANGA Line: preset management and line visibility/delete checks."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    intersection_lines,
    outline_setup,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(name: str, location=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    return camera


def _select(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _line_mods(obj: bpy.types.Object):
    return {mod.name: mod for mod in core.iter_line_modifiers(obj)}


def _intersection_mods(obj: bpy.types.Object):
    return list(core.iter_intersection_modifiers(obj))


def _assert_line_state(obj: bpy.types.Object, *, visible: bool) -> None:
    mods = _line_mods(obj)
    assert core.MODIFIER_NAME in mods, f"{obj.name}: アウトラインがありません"
    assert core.GN_MODIFIER_NAME in mods, f"{obj.name}: 内部線がありません"
    assert all(mod.show_viewport == visible for mod in mods.values()), mods
    assert all(mod.show_render == visible for mod in mods.values()), mods


def _assert_distance_limited_inner(obj: bpy.types.Object) -> None:
    mods = _line_mods(obj)
    assert mods[core.MODIFIER_NAME].show_viewport
    assert mods[core.MODIFIER_NAME].show_render
    assert all(mod.show_viewport for mod in _intersection_mods(obj))
    assert all(mod.show_render for mod in _intersection_mods(obj))
    assert not mods[core.GN_MODIFIER_NAME].show_viewport
    assert not mods[core.GN_MODIFIER_NAME].show_render
    assert not bool(obj.get(core.PROP_LINES_HIDDEN, False))


def _assert_distance_limited_outline_and_intersection(obj: bpy.types.Object) -> None:
    mods = _line_mods(obj)
    assert not mods[core.MODIFIER_NAME].show_viewport
    assert not mods[core.MODIFIER_NAME].show_render
    assert mods[core.GN_MODIFIER_NAME].show_viewport
    assert mods[core.GN_MODIFIER_NAME].show_render
    assert all(not mod.show_viewport for mod in _intersection_mods(obj))
    assert all(not mod.show_render for mod in _intersection_mods(obj))
    assert not bool(obj.get(core.PROP_LINES_HIDDEN, False))


def _assert_camera_culled_line(obj: bpy.types.Object) -> None:
    _assert_line_state(obj, visible=False)
    assert not bool(obj.get(core.PROP_LINES_HIDDEN, False))


def _assert_distance_threshold_hides_at_exact_distance(target: bpy.types.Object) -> None:
    obj = _make_cube("BML_距離境界", (0.5, 0.0, 0.0))
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    settings.use_outline_distance_limit = True
    settings.outline_max_distance = 0.5
    settings.use_inner_line_distance_limit = True
    settings.inner_line_max_distance = 0.5
    settings.use_intersection_distance_limit = True
    settings.intersection_max_distance = 0.5
    assert presets.apply_line_settings(obj, bpy.context)
    _assert_line_state(obj, visible=False)
    bpy.data.objects.remove(obj, do_unlink=True)


def _modifier_input(mod, socket_name: str):
    tree = mod.node_group
    assert tree is not None
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == socket_name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return mod[item.identifier]
    raise AssertionError(f"{mod.name}: {socket_name} socket not found")


def _assert_line_color(obj: bpy.types.Object, color: tuple[float, float, float, float]) -> None:
    mat = outline_setup.get_outline_material(obj)
    assert mat is not None
    assert mat.node_tree is not None
    for node in mat.node_tree.nodes:
        if node.type == "RGB" and node.label == "BML_Color":
            actual = tuple(round(v, 3) for v in node.outputs[0].default_value)
            assert actual == tuple(round(v, 3) for v in color)
            return
    raise AssertionError(f"{obj.name}: line color node not found")


def _assert_multi_select_manual_setting_propagation(
    scene: bpy.types.Scene,
    first: bpy.types.Object,
    second: bpy.types.Object,
) -> None:
    if scene.camera is None:
        _make_camera()
    _select(first, [first, second])
    source = first.bmanga_line_settings
    target = second.bmanga_line_settings

    source.outline_thickness_mm = 0.9
    assert math.isclose(target.outline_thickness_mm, 0.9, rel_tol=0.001)
    expected = camera_comp._reference_width_for_mesh(
        scene,
        scene.camera,
        second,
        target.outline_thickness,
    )
    assert math.isclose(
        second.modifiers[core.MODIFIER_NAME].thickness,
        expected,
        rel_tol=0.001,
    )

    source.outline_color = (0.4, 0.5, 0.6, 1.0)
    assert tuple(round(v, 3) for v in target.outline_color) == (0.4, 0.5, 0.6, 1.0)
    _assert_line_color(second, (0.4, 0.5, 0.6, 1.0))

    source.hide_through_transparent = True
    mat = outline_setup.get_outline_material(second)
    assert mat is not None
    assert bool(mat.get(outline_setup.PROP_HIDE_THROUGH_TRANSPARENT, False))
    source.hide_through_transparent = False
    mat = outline_setup.get_outline_material(second)
    assert mat is not None
    assert not bool(mat.get(outline_setup.PROP_HIDE_THROUGH_TRANSPARENT, False))

    source.use_rim = True
    assert target.use_rim is True
    assert second.modifiers[core.MODIFIER_NAME].use_rim is True

    source.even_thickness = False
    assert target.even_thickness is False
    assert second.modifiers[core.MODIFIER_NAME].use_even_offset is False
    source.even_thickness = True
    assert target.even_thickness is True
    assert second.modifiers[core.MODIFIER_NAME].use_even_offset is True

    source.use_vertex_color = True
    assert target.use_vertex_color is True
    assert second.modifiers[core.MODIFIER_NAME].vertex_group == core.VG_LINE_WIDTH
    source.use_ao_influence = True
    source.ao_influence_strength = 0.8
    assert target.use_ao_influence is True
    assert math.isclose(target.ao_influence_strength, 0.8, rel_tol=0.001)

    source.use_uniform_line_width = True
    assert target.use_uniform_line_width is True
    assert second.modifiers[core.MODIFIER_NAME].vertex_group == core.VG_LINE_WIDTH

    source.edge_smooth_factor = -0.75
    source.edge_midpoint_jitter_percent = 12.0
    assert math.isclose(target.edge_smooth_factor, -0.75, rel_tol=0.001)
    assert math.isclose(target.edge_midpoint_jitter_percent, 12.0, rel_tol=0.001)

    source.edge_width_curve_25 = 0.2
    source.edge_width_curve_50 = 0.3
    source.edge_width_curve_75 = 0.4
    assert math.isclose(target.edge_width_curve_25, 0.2, rel_tol=0.001)
    assert math.isclose(target.edge_width_curve_50, 0.3, rel_tol=0.001)
    assert math.isclose(target.edge_width_curve_75, 0.4, rel_tol=0.001)

    source.inner_edge_smooth_factor = -0.35
    source.inner_edge_midpoint_jitter_percent = 8.0
    source.inner_edge_width_curve_25 = 0.15
    source.inner_edge_width_curve_50 = 0.25
    source.inner_edge_width_curve_75 = 0.35
    assert math.isclose(target.inner_edge_smooth_factor, -0.35, rel_tol=0.001)
    assert math.isclose(target.inner_edge_midpoint_jitter_percent, 8.0, rel_tol=0.001)
    assert math.isclose(target.inner_edge_width_curve_25, 0.15, rel_tol=0.001)
    assert math.isclose(target.inner_edge_width_curve_50, 0.25, rel_tol=0.001)
    assert math.isclose(target.inner_edge_width_curve_75, 0.35, rel_tol=0.001)

    source.intersection_edge_smooth_factor = -0.45
    source.intersection_edge_midpoint_jitter_percent = 9.0
    source.intersection_edge_width_curve_25 = 0.18
    source.intersection_edge_width_curve_50 = 0.28
    source.intersection_edge_width_curve_75 = 0.38
    assert math.isclose(target.intersection_edge_smooth_factor, -0.45, rel_tol=0.001)
    assert math.isclose(target.intersection_edge_midpoint_jitter_percent, 9.0, rel_tol=0.001)
    assert math.isclose(target.intersection_edge_width_curve_25, 0.18, rel_tol=0.001)
    assert math.isclose(target.intersection_edge_width_curve_50, 0.28, rel_tol=0.001)
    assert math.isclose(target.intersection_edge_width_curve_75, 0.38, rel_tol=0.001)

    source.use_uniform_line_width = False
    assert target.use_uniform_line_width is False

    source.inner_line_enabled = False
    assert target.inner_line_enabled is False
    assert core.GN_MODIFIER_NAME not in second.modifiers
    source.inner_line_enabled = True
    source.inner_line_angle = math.radians(45.0)
    source.use_marked_inner_edges = True
    source.inner_line_thickness_mm = 1.2
    assert target.inner_line_enabled is True
    assert target.use_marked_inner_edges is True
    inner_mod = second.modifiers.get(core.GN_MODIFIER_NAME)
    assert inner_mod is not None
    assert math.isclose(
        _modifier_input(inner_mod, "検出角度"),
        math.radians(45.0),
        rel_tol=0.001,
    )
    assert _modifier_input(inner_mod, "指定済みの辺だけ線にする") is True
    assert math.isclose(target.inner_line_thickness_mm, 1.2, rel_tol=0.001)

    source.inner_line_thickness_mm = 70.0
    assert math.isclose(target.inner_line_thickness_mm, 70.0, rel_tol=0.001)

    source.inner_line_creation_max_distance = 6.5
    assert math.isclose(target.inner_line_creation_max_distance, 6.5, rel_tol=0.001)
    source.use_inner_line_creation_limit = False
    assert target.use_inner_line_creation_limit is False
    source.use_inner_line_creation_limit = True
    assert target.use_inner_line_creation_limit is True

    source.intersection_enabled = False
    assert target.intersection_enabled is False
    assert not _intersection_mods(second)
    source.intersection_enabled = True
    source.intersection_method = "BOOLEAN"
    source.intersection_thickness_mm = 1.4
    assert target.intersection_enabled is True
    assert target.intersection_method == "BOOLEAN"
    intersection_mods = _intersection_mods(first) + _intersection_mods(second)
    assert intersection_mods
    assert any(
        mod.node_group is not None
        and mod.node_group.name.startswith(intersection_lines.INTERSECTION_TREE_BOOLEAN)
        for mod in intersection_mods
    )
    assert math.isclose(target.intersection_thickness_mm, 1.4, rel_tol=0.001)

    source.intersection_thickness_mm = 80.0
    assert math.isclose(target.intersection_thickness_mm, 80.0, rel_tol=0.001)

    source.intersection_creation_max_distance = 7.5
    assert math.isclose(target.intersection_creation_max_distance, 7.5, rel_tol=0.001)
    source.use_intersection_creation_limit = False
    assert target.use_intersection_creation_limit is False
    source.use_intersection_creation_limit = True
    assert target.use_intersection_creation_limit is True

    source.use_camera_compensation = True
    source.camera_compensation_influence = 0.35
    assert target.use_camera_compensation is True
    assert math.isclose(target.camera_compensation_influence, 0.35, rel_tol=0.001)
    assert core.PROP_REF_DISTANCE in second

    source.use_camera_culling = True
    source.culling_margin = 0.0
    assert target.use_camera_culling is True
    assert math.isclose(target.culling_margin, 0.0, rel_tol=0.001)

    source.use_outline_distance_limit = True
    source.outline_max_distance = 0.25
    assert target.use_outline_distance_limit is True
    assert math.isclose(target.outline_max_distance, 0.25, rel_tol=0.001)
    source.use_inner_line_distance_limit = True
    source.inner_line_max_distance = 0.35
    assert target.use_inner_line_distance_limit is True
    assert math.isclose(target.inner_line_max_distance, 0.35, rel_tol=0.001)
    source.use_intersection_distance_limit = True
    source.intersection_max_distance = 0.45
    assert target.use_intersection_distance_limit is True
    assert math.isclose(target.intersection_max_distance, 0.45, rel_tol=0.001)

    old = core._propagating
    core._propagating = True
    try:
        source.outline_thickness = 0.006
        target.outline_thickness = 0.002
    finally:
        core._propagating = old
    fake_context = SimpleNamespace(selected_objects=[first], scene=scene)
    core._propagate(source, fake_context, "outline_thickness")
    assert math.isclose(target.outline_thickness, 0.006, rel_tol=0.001)

    source.use_camera_culling = False
    source.use_outline_distance_limit = False
    source.use_inner_line_distance_limit = False
    source.use_intersection_distance_limit = False
    source.use_inner_line_creation_limit = True
    source.inner_line_creation_max_distance = 10.0
    source.use_intersection_creation_limit = True
    source.intersection_creation_max_distance = 10.0
    source.use_camera_compensation = False
    source.use_uniform_line_width = False
    source.use_vertex_color = False
    source.use_ao_influence = False
    source.edge_smooth_factor = 0.0
    source.inner_line_enabled = True
    source.use_marked_inner_edges = False
    source.inner_line_thickness_mm = 1.2
    source.intersection_enabled = True
    source.intersection_thickness_mm = 1.4
    source.intersection_method = "SDF"
    for obj in (first, second):
        core.set_line_visibility(obj, True)
        _assert_line_state(obj, visible=True)


def main() -> None:
    b_manga_line.register()
    _clear_scene()

    source = _make_cube("BML_プリセット元", (-2.0, 0.0, 0.0))
    target = _make_cube("BML_交差対象", (0.0, 0.0, 0.0))
    assert presets.apply_line_settings(target, bpy.context)
    settings = source.bmanga_line_settings
    settings.outline_thickness = 0.012
    settings.outline_color = (0.1, 0.2, 0.3, 1.0)
    settings.use_rim = False
    settings.inner_line_enabled = True
    settings.inner_line_angle = math.radians(12.0)
    settings.inner_line_thickness = 0.021
    settings.intersection_enabled = True
    settings.intersection_method = "SDF"
    settings.intersection_thickness = 0.017
    settings.edge_smooth_factor = -1.0
    settings.edge_midpoint_jitter_percent = 20.0
    settings.edge_width_curve_25 = 0.1
    settings.edge_width_curve_50 = 0.4
    settings.edge_width_curve_75 = 0.8
    settings.inner_edge_smooth_factor = -0.55
    settings.inner_edge_width_curve_50 = 0.45
    settings.intersection_edge_smooth_factor = -0.65
    settings.intersection_edge_width_curve_50 = 0.35

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
    second = _make_cube("BML_適用先B", (2.35, 0.0, 0.0))
    _select(first, [first, second])
    assert bpy.ops.bmanga_line.preset_apply_selected() == {"FINISHED"}
    for obj in (first, second):
        applied = obj.bmanga_line_settings
        assert abs(applied.outline_thickness - 0.018) < 1.0e-7
        assert tuple(round(v, 3) for v in applied.outline_color) == (0.1, 0.2, 0.3, 1.0)
        assert applied.inner_line_enabled
        assert applied.intersection_enabled
        assert applied.intersection_method == "SDF"
        assert abs(applied.edge_width_curve_50 - 0.4) < 1.0e-7
        assert abs(applied.inner_edge_smooth_factor + 0.55) < 1.0e-7
        assert abs(applied.inner_edge_width_curve_50 - 0.45) < 1.0e-7
        assert abs(applied.intersection_edge_smooth_factor + 0.65) < 1.0e-7
        assert abs(applied.intersection_edge_width_curve_50 - 0.35) < 1.0e-7
        _assert_line_state(obj, visible=True)

    _assert_multi_select_manual_setting_propagation(scene, first, second)

    assert bpy.ops.bmanga_line.set_visibility(visible=False) == {"FINISHED"}
    for obj in (first, second):
        _assert_line_state(obj, visible=False)
        assert bool(obj.get(core.PROP_LINES_HIDDEN, False))

    first.bmanga_line_settings.use_camera_culling = True
    first.bmanga_line_settings.use_camera_culling = False
    first.bmanga_line_settings.use_outline_distance_limit = True
    first.bmanga_line_settings.use_outline_distance_limit = False
    first.bmanga_line_settings.use_inner_line_distance_limit = True
    first.bmanga_line_settings.use_inner_line_distance_limit = False
    first.bmanga_line_settings.use_intersection_distance_limit = True
    first.bmanga_line_settings.use_intersection_distance_limit = False
    for obj in (first, second):
        _assert_line_state(obj, visible=False)

    assert bpy.ops.bmanga_line.set_visibility(visible=True) == {"FINISHED"}
    for obj in (first, second):
        _assert_line_state(obj, visible=True)
        assert not bool(obj.get(core.PROP_LINES_HIDDEN, False))

    if scene.camera is None:
        _make_camera()
    _assert_distance_threshold_hides_at_exact_distance(target)
    _select(source, [source])
    settings.use_inner_line_distance_limit = True
    settings.inner_line_max_distance = 0.5
    scene.bmanga_line_preset_name = "距離制限テスト"
    assert bpy.ops.bmanga_line.preset_save() == {"FINISHED"}

    _select(first, [first, second])
    assert bpy.ops.bmanga_line.preset_apply_selected() == {"FINISHED"}
    for obj in (first, second):
        assert obj.bmanga_line_settings.use_inner_line_distance_limit
        assert abs(obj.bmanga_line_settings.inner_line_max_distance - 0.5) < 1.0e-7
        _assert_distance_limited_inner(obj)

    _select(source, [source])
    settings.use_inner_line_distance_limit = False
    settings.use_outline_distance_limit = True
    settings.outline_max_distance = 0.5
    settings.use_intersection_distance_limit = True
    settings.intersection_max_distance = 0.5
    scene.bmanga_line_preset_name = "線種別距離制限テスト"
    assert bpy.ops.bmanga_line.preset_save() == {"FINISHED"}

    _select(first, [first, second])
    assert bpy.ops.bmanga_line.preset_apply_selected() == {"FINISHED"}
    for obj in (first, second):
        applied = obj.bmanga_line_settings
        assert applied.use_outline_distance_limit
        assert abs(applied.outline_max_distance - 0.5) < 1.0e-7
        assert applied.use_intersection_distance_limit
        assert abs(applied.intersection_max_distance - 0.5) < 1.0e-7
        _assert_distance_limited_outline_and_intersection(obj)

    assert bpy.ops.bmanga_line.set_visibility(visible=True) == {"FINISHED"}
    for obj in (first, second):
        _assert_distance_limited_outline_and_intersection(obj)

    _select(source, [source])
    settings.use_outline_distance_limit = False
    settings.use_intersection_distance_limit = False
    settings.use_camera_culling = True
    settings.culling_margin = 0.0
    scene.bmanga_line_preset_name = "範囲外テスト"
    assert bpy.ops.bmanga_line.preset_save() == {"FINISHED"}

    _select(first, [first, second])
    assert bpy.ops.bmanga_line.preset_apply_selected() == {"FINISHED"}
    for obj in (first, second):
        assert obj.bmanga_line_settings.use_camera_culling
        _assert_camera_culled_line(obj)

    assert bpy.ops.bmanga_line.remove() == {"FINISHED"}
    for obj in (first, second):
        assert not core.has_line(obj), f"{obj.name}: ラインが残っています"
        assert core.PROP_LINES_HIDDEN not in obj

    assert bpy.ops.bmanga_line.preset_delete() == {"FINISHED"}
    assert len(scene.bmanga_line_presets) == 3
    assert bpy.ops.bmanga_line.preset_delete() == {"FINISHED"}
    assert len(scene.bmanga_line_presets) == 2
    assert bpy.ops.bmanga_line.preset_delete() == {"FINISHED"}
    assert len(scene.bmanga_line_presets) == 1
    assert bpy.ops.bmanga_line.preset_delete() == {"FINISHED"}
    assert len(scene.bmanga_line_presets) == 0

    print("[PASS] line presets apply to selected objects and visibility/delete work")


if __name__ == "__main__":
    main()
