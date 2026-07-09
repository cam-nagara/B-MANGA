"""B-MANGA Liner: 頂点単位の線幅遠近減衰とnumpy線幅計算を検証する."""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    inner_lines,
    presets,
    scale_utils,
    update_state,
    vertex_analysis,
    width_math,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera(kind: str = "PERSP") -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 900
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.name = f"BML_width_falloff_{kind}_camera"
    camera.data.clip_start = 0.01
    camera.data.clip_end = 500.0
    if kind == "ORTHO":
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = 8.0
    elif kind == "PANO":
        camera.data.type = "PANO"
        camera.data.panorama_type = "FISHEYE_EQUIDISTANT"
        camera.data.fisheye_fov = math.radians(180.0)
    else:
        camera.data.type = "PERSP"
        camera.data.angle = math.radians(50.0)
    scene.camera = camera
    return camera


def _make_depth_box(name: str = "BML_width_depth_box") -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(
        [
            (-0.8, -0.5, -2.0),
            (0.8, -0.5, -2.0),
            (0.8, 0.5, -2.0),
            (-0.8, 0.5, -2.0),
            (-0.8, -0.5, -60.0),
            (0.8, -0.5, -60.0),
            (0.8, 0.5, -60.0),
            (-0.8, 0.5, -60.0),
        ],
        [],
        [
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (3, 7, 4, 0),
        ],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _make_ratio_quad() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_width_ratio_quad_mesh")
    mesh.from_pydata(
        [
            (-0.5, -0.5, -2.0),
            (0.5, -0.5, -2.0),
            (0.5, 0.5, -2.0),
            (-0.5, 0.5, -2.0),
            (-0.5, -0.5, -4.0),
            (0.5, -0.5, -4.0),
            (0.5, 0.5, -4.0),
            (-0.5, 0.5, -4.0),
        ],
        [],
        [
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (3, 7, 4, 0),
        ],
    )
    mesh.update()
    obj = bpy.data.objects.new("BML_width_ratio_quad", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _line_world_width(obj: bpy.types.Object) -> float:
    return scale_utils.world_width_from_modifier(
        obj,
        obj.modifiers[core.MODIFIER_NAME].thickness,
    )


def _inner_world_width(obj: bpy.types.Object) -> float:
    mod = obj.modifiers[core.GN_MODIFIER_NAME]
    sid = inner_lines._find_socket_id(mod.node_group, "線の太さ")
    assert sid is not None
    return abs(float(mod[sid])) * scale_utils.object_width_scale(obj)


def _weights(obj: bpy.types.Object, target: str = "outline") -> list[float]:
    return vertex_analysis.stored_width_weights(obj, target)


def _actual_widths(obj: bpy.types.Object, target: str = "outline") -> list[float]:
    max_width = _inner_world_width(obj) if target == "inner" else _line_world_width(obj)
    return [value * max_width for value in _weights(obj, target)]


def _reference_widths(
    scene,
    camera,
    obj,
    width_m: float,
    *,
    falloff: float,
    reference_distance: float,
) -> tuple[list[float], list[float]]:
    target_px = camera_comp._target_pixels(scene, width_m)
    widths = []
    depths = []
    inv = camera.matrix_world.inverted()
    for vertex in obj.data.vertices:
        world = obj.matrix_world @ vertex.co
        local = inv @ world
        if camera.data.type == "PANO":
            depth = max(0.001, float(local.length))
        else:
            depth = max(0.001, -float(local.z))
        wpp = camera_comp._world_per_pixel(scene, camera, world)
        value = target_px * wpp
        if falloff > 0.0:
            value *= (reference_distance / depth) ** falloff
        widths.append(value)
        depths.append(depth)
    return widths, depths


def _assert_numpy_matches_reference() -> None:
    for kind in ("PERSP", "ORTHO", "PANO"):
        _clear_scene()
        camera = _make_camera(kind)
        obj = _make_depth_box("BML_width_math_" + kind)
        expected, expected_depths = _reference_widths(
            bpy.context.scene,
            camera,
            obj,
            0.0004,
            falloff=1.25,
            reference_distance=3.0,
        )
        actual, depths = width_math.vertex_widths_and_depths(
            bpy.context.scene,
            camera,
            obj,
            0.0004,
            distance_falloff=1.25,
            reference_distance=3.0,
        )
        assert len(actual) == len(expected)
        for a, e in zip(actual.tolist(), expected):
            assert math.isclose(a, e, rel_tol=1.0e-6, abs_tol=1.0e-12), (kind, a, e)
        for a, e in zip(depths.tolist(), expected_depths):
            assert math.isclose(a, e, rel_tol=1.0e-6, abs_tol=1.0e-12), (kind, a, e)


def _apply_outline(obj: bpy.types.Object, *, falloff: float) -> None:
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.outline_thickness_mm = 0.5
    settings.exclude_sheet_meshes = False
    settings.use_uniform_line_width = True
    settings.line_width_reference_distance = 2.0
    settings.line_width_distance_falloff = falloff
    assert presets.apply_line_settings(obj, bpy.context)
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}


def _test_p0_and_p1_width_shape() -> None:
    _clear_scene()
    camera = _make_camera("PERSP")
    obj = _make_depth_box()
    _apply_outline(obj, falloff=0.0)
    actual = _actual_widths(obj)
    _expected, depths = _reference_widths(
        bpy.context.scene,
        camera,
        obj,
        obj.bmanga_line_settings.outline_thickness,
        falloff=0.0,
        reference_distance=2.0,
    )
    screen_widths = [
        width / camera_comp._world_per_pixel(bpy.context.scene, camera, obj.matrix_world @ obj.data.vertices[i].co)
        for i, width in enumerate(actual)
    ]
    base = screen_widths[0]
    assert all(math.isclose(value, base, rel_tol=1.0e-4) for value in screen_widths), screen_widths

    obj.bmanga_line_settings.line_width_distance_falloff = 1.0
    before = _weights(obj)
    assert "outline" in update_state.pending_visual_targets(obj)
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    after = _weights(obj)
    assert before != after, "反映前に線幅が変わっていないこと、反映後に変わることを検出できません"
    actual_p1 = _actual_widths(obj)
    assert max(actual_p1) - min(actual_p1) < max(actual_p1) * 1.0e-4, (actual_p1, depths)


def _test_p2_ratio() -> None:
    _clear_scene()
    camera = _make_camera("PERSP")
    obj = _make_ratio_quad()
    _apply_outline(obj, falloff=2.0)
    actual = _actual_widths(obj)
    screen = [
        width / camera_comp._world_per_pixel(bpy.context.scene, camera, obj.matrix_world @ obj.data.vertices[i].co)
        for i, width in enumerate(actual)
    ]
    ratio = screen[4] / screen[0]
    assert math.isclose(ratio, 0.25, rel_tol=1.0e-4), screen


def _test_inner_line_and_style_weights() -> None:
    _clear_scene()
    camera = _make_camera("PERSP")
    obj = _make_depth_box("BML_width_style_box")
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.outline_thickness_mm = 0.5
    settings.inner_line_enabled = True
    settings.inner_line_thickness_mm = 0.25
    settings.exclude_sheet_meshes = False
    settings.use_uniform_line_width = True
    settings.line_width_reference_distance = 2.0
    settings.line_width_distance_falloff = 0.0
    settings.edge_smooth_factor = -0.5
    settings.inner_edge_smooth_factor = -0.25
    assert presets.apply_line_settings(obj, bpy.context)
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}

    outline_expected, _ = width_math.vertex_widths_and_depths(
        bpy.context.scene,
        camera,
        obj,
        settings.outline_thickness,
        distance_falloff=0.0,
        reference_distance=2.0,
    )
    outline_norm = outline_expected / float(outline_expected.max())
    style = vertex_analysis.compute_weights(obj, settings, "outline")
    assert style is not None
    for actual, expected_style, expected_norm in zip(_weights(obj), style, outline_norm.tolist()):
        assert math.isclose(actual, expected_style * expected_norm, rel_tol=1.0e-4), (
            actual,
            expected_style,
            expected_norm,
        )

    inner_actual = _actual_widths(obj, "inner")
    inner_expected, _ = _reference_widths(
        bpy.context.scene,
        camera,
        obj,
        settings.inner_line_thickness,
        falloff=0.0,
        reference_distance=2.0,
    )
    assert math.isclose(max(inner_actual), max(inner_expected), rel_tol=1.0e-3)
    assert obj.data.attributes.get(vertex_analysis.width_group_name("inner")) is not None
    assert obj.vertex_groups.get(vertex_analysis.width_group_name("inner")) is None


def _test_generated_width_group_migrates_to_attribute() -> None:
    _clear_scene()
    _make_camera("PERSP")
    obj = _make_depth_box("BML_width_migration_box")
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.exclude_sheet_meshes = False
    settings.use_uniform_line_width = True
    settings.line_width_reference_distance = 2.0
    settings.line_width_distance_falloff = 1.0
    settings.inner_edge_smooth_factor = 0.0
    assert presets.apply_line_settings(obj, bpy.context)
    group_name = vertex_analysis.width_group_name("inner")
    old_group = obj.vertex_groups.new(name=group_name)
    old_group.add([0], 0.25, "REPLACE")
    old_group.add(list(range(1, len(obj.data.vertices))), 1.0, "REPLACE")

    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}
    attr = obj.data.attributes.get(group_name)
    assert attr is not None, "生成線幅属性へ移行されていません"
    assert obj.vertex_groups.get(group_name) is None, "旧頂点グループが残っています"


def _test_preset_round_trip() -> None:
    _clear_scene()
    _make_camera("PERSP")
    obj = _make_depth_box("BML_width_preset_box")
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.use_uniform_line_width = True
    settings.line_width_distance_falloff = 1.5
    preset = bpy.context.scene.bmanga_line_presets.add()
    presets.copy_settings_to_preset(settings, preset)
    settings.line_width_distance_falloff = 0.0
    presets.copy_preset_to_settings(preset, settings)
    assert math.isclose(settings.line_width_distance_falloff, 1.5, rel_tol=0.0, abs_tol=1.0e-6)


def _legacy_width_loop(scene, camera, obj, width_m: float) -> list[float]:
    target_px = camera_comp._target_pixels(scene, width_m)
    matrix = obj.matrix_world
    return [
        target_px * camera_comp._world_per_pixel(scene, camera, matrix @ vertex.co)
        for vertex in obj.data.vertices
    ]


def _make_large_vertex_mesh(count: int = 100_000) -> bpy.types.Object:
    verts = []
    for i in range(count):
        x = (i % 400) * 0.01 - 2.0
        y = ((i // 400) % 250) * 0.01 - 1.25
        z = -2.0 - 58.0 * (i / max(1, count - 1))
        verts.append((x, y, z))
    mesh = bpy.data.meshes.new("BML_width_perf_vertices_mesh")
    mesh.from_pydata(verts, [], [])
    mesh.update()
    obj = bpy.data.objects.new("BML_width_perf_vertices", mesh)
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new(core.MODIFIER_NAME, "SOLIDIFY")
    mod.thickness = 0.001
    return obj


def _test_performance_smoke() -> None:
    _clear_scene()
    camera = _make_camera("PERSP")
    obj = _make_large_vertex_mesh()
    settings = obj.bmanga_line_settings
    settings.use_uniform_line_width = True
    settings.line_width_distance_falloff = 0.0
    settings.outline_thickness_mm = 0.3

    start = time.perf_counter()
    legacy = _legacy_width_loop(bpy.context.scene, camera, obj, settings.outline_thickness)
    legacy_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    widths, _ = width_math.vertex_widths_and_depths(
        bpy.context.scene,
        camera,
        obj,
        settings.outline_thickness,
    )
    numpy_elapsed = time.perf_counter() - start
    assert math.isclose(max(legacy), float(widths.max()), rel_tol=1.0e-6)

    start = time.perf_counter()
    ok = camera_comp.refresh_objects(bpy.context, [obj], width_targets=("outline",))
    refresh_elapsed = time.perf_counter() - start
    assert ok
    print(
        "BML_WIDTH_FALLOFF_PERF "
        f"vertices={len(obj.data.vertices)} "
        f"legacy_width_loop={legacy_elapsed:.6f}s "
        f"numpy_widths={numpy_elapsed:.6f}s "
        f"refresh={refresh_elapsed:.6f}s"
    )


def main() -> None:
    b_manga_line.register()
    try:
        _assert_numpy_matches_reference()
        _test_p0_and_p1_width_shape()
        _test_p2_ratio()
        _test_inner_line_and_style_weights()
        _test_generated_width_group_migrates_to_attribute()
        _test_preset_round_trip()
        _test_performance_smoke()
    finally:
        b_manga_line.unregister()
    print("blender_b_manga_line_width_falloff_check: PASS")


if __name__ == "__main__":
    main()
