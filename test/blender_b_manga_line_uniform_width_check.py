"""B-MANGA Line: uniform line width follows camera, DPI, and resolution.

ボタン再編（docs/bml_reflect_button_reorg_plan_2026-07-09.md）により、旧
bmanga_line.apply / update_target / update_visual_target は
reflect_all / reflect_target(target=...) へ統合された。本ファイルの各呼び出しは
すべて実プロパティ代入（update ハンドラ経由）の直後に置かれており、その代入が
反映の待ち印を自動的に付けるため、置換後も同じ反映結果になる。
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

import b_manga_line  # noqa: E402
from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    inner_lines,
    intersection_lines,
    presets,
    scale_utils,
    width_math,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.name = "BML_uniform_camera"
    bpy.context.scene.camera = camera
    camera.data.type = "PERSP"
    camera.data.angle = math.radians(50.0)
    return camera


def _make_depth_quad() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_uniform_depth_quad_mesh")
    mesh.from_pydata(
        [
            (-1.0, -0.5, -2.0),
            (1.0, -0.5, -2.0),
            (1.0, 0.5, -8.0),
            (-1.0, 0.5, -8.0),
            (-0.95, -0.5, -2.0),
            (1.05, -0.5, -2.0),
            (1.05, 0.5, -8.0),
            (-0.95, 0.5, -8.0),
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
    obj = bpy.data.objects.new("BML_uniform_depth_quad", mesh)
    bpy.context.collection.objects.link(obj)
    mat = bpy.data.materials.new("BML_uniform_surface")
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    obj.data.materials.append(mat)
    return obj


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _select_many(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _target_pixels(width_mm: float) -> float:
    return width_mm * 600.0 / 25.4


def _expected_world_width(scene: bpy.types.Scene, depth: float, width_mm: float) -> float:
    # 線幅は印刷 mm 一致が正: 解像度パーセンテージに影響されないフル解像度基準
    camera = scene.camera
    height = float(scene.render.resolution_y)
    view_height = 2.0 * depth * math.tan(camera.data.angle_y * 0.5)
    return _target_pixels(width_mm) * view_height / height


def _weights(obj: bpy.types.Object) -> list[float]:
    vg = obj.vertex_groups[core.VG_LINE_WIDTH]
    return [vg.weight(i) for i in range(len(obj.data.vertices))]


def _inner_thickness(obj: bpy.types.Object) -> float:
    mod = obj.modifiers[core.GN_MODIFIER_NAME]
    sid = inner_lines._find_socket_id(mod.node_group, "線の太さ")
    assert sid is not None
    return float(mod[sid])


def _line_world_width(obj: bpy.types.Object) -> float:
    return scale_utils.world_width_from_modifier(
        obj,
        obj.modifiers[core.MODIFIER_NAME].thickness,
    )


def _socket_world_width(obj: bpy.types.Object, thickness: float) -> float:
    return abs(float(thickness)) * scale_utils.object_width_scale(obj)


def _intersection_thickness(obj: bpy.types.Object) -> float:
    mod = _intersection_modifier(obj)
    assert mod is not None
    sid = intersection_lines._find_socket_id(mod.node_group, "線の太さ")
    assert sid is not None
    return float(mod[sid])


def _intersection_modifier(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    return next(core.iter_intersection_modifiers(obj), None)


def _intersection_owner(*objects: bpy.types.Object) -> bpy.types.Object:
    for obj in objects:
        if _intersection_modifier(obj) is not None:
            return obj
    raise AssertionError("No intersection line modifier was created.")


def _selection_thickness(obj: bpy.types.Object) -> float:
    mod = obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME)
    assert mod is not None
    sid = inner_lines._find_socket_id(mod.node_group, "線の太さ")
    assert sid is not None
    return float(mod[sid])


def _mark_all_freestyle_edges(obj: bpy.types.Object) -> None:
    mesh = obj.data
    attr = mesh.attributes.get("freestyle_edge")
    if attr is None:
        attr = mesh.attributes.new("freestyle_edge", "BOOLEAN", "EDGE")
    for item in attr.data:
        item.value = True
    mesh.update()


def _realize_instances_tree() -> bpy.types.NodeTree:
    name = "BML_TestUniformWidthRealizeInstances"
    tree = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    group_in = tree.nodes.new("NodeGroupInput")
    realize = tree.nodes.new("GeometryNodeRealizeInstances")
    group_out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(group_in.outputs["Geometry"], realize.inputs["Geometry"])
    tree.links.new(realize.outputs["Geometry"], group_out.inputs["Geometry"])
    return tree


def _evaluated_outline_world_width(obj: bpy.types.Object) -> float:
    realize_mod = obj.modifiers.new("BML_TestUniformWidthRealize", "NODES")
    realize_mod.node_group = _realize_instances_tree()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_slot_start = min(
            i for i, mat in enumerate(mesh.materials)
            if mat and mat.name.startswith(core.MATERIAL_NAME)
        )
        line_x = [
            (obj.matrix_world @ mesh.vertices[vi].co).x
            for poly in mesh.polygons
            if poly.material_index >= line_slot_start
            for vi in poly.vertices
        ]
        assert line_x, "評価済みメッシュからアウトライン面を検出できませんでした"
        original_left = min(
            (obj.matrix_world @ vertex.co).x
            for vertex in obj.data.vertices
        )
        shell_left = min(line_x)
        return abs(original_left - shell_left)
    finally:
        bpy.data.meshes.remove(mesh)
        obj.modifiers.remove(realize_mod)


def _configure_scene(scene: bpy.types.Scene) -> None:
    scene.render.resolution_x = 1000
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    _make_camera()


def _test_uniform_width_depth_and_resolution() -> None:
    scene = bpy.context.scene
    _configure_scene(scene)
    obj = _make_depth_quad()
    _select(obj)

    settings = obj.bmanga_line_settings
    settings.outline_thickness_mm = 0.5
    # 板ポリ除外（初期値オン）の対象になる薄板なので、内部線の線幅検証用に除外を切る
    settings.exclude_sheet_meshes = False
    settings.inner_line_enabled = True
    settings.inner_line_thickness_mm = 0.25
    settings.use_uniform_line_width = True
    settings.line_width_distance_falloff = 0.0
    assert presets.apply_line_settings(obj, bpy.context)
    mod = obj.modifiers[core.MODIFIER_NAME]
    near = _expected_world_width(scene, 2.0, 0.5)
    far = _expected_world_width(scene, 8.0, 0.5)
    assert math.isclose(mod.thickness, far, rel_tol=0.001), (mod.thickness, far)
    assert mod.vertex_group == core.VG_LINE_WIDTH
    values = _weights(obj)
    assert math.isclose(values[0], near / far, rel_tol=0.001), values
    assert math.isclose(values[2], 1.0, rel_tol=0.001), values
    assert math.isclose(_inner_thickness(obj), far * 0.5, rel_tol=0.001)

    scene.render.resolution_y = 2000
    camera_comp.refresh(bpy.context)
    far_high_res = _expected_world_width(scene, 8.0, 0.5)
    assert math.isclose(mod.thickness, far_high_res, rel_tol=0.001), (
        mod.thickness,
        far_high_res,
    )
    assert far_high_res < far * 0.51

    settings.outline_thickness_mm = 1.0
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    far_thick = _expected_world_width(scene, 8.0, 1.0)
    assert math.isclose(mod.thickness, far_thick, rel_tol=0.001), (
        mod.thickness,
        far_thick,
    )

    settings.outline_thickness_mm = 0.8
    assert bpy.ops.bmanga_line.reflect_target(
        "EXEC_DEFAULT",
        target="outline",
    ) == {"FINISHED"}
    far_visual = _expected_world_width(scene, 8.0, 0.8)
    assert math.isclose(mod.thickness, far_visual, rel_tol=0.001), (
        mod.thickness,
        far_visual,
    )

    settings.use_uniform_line_width = False
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}
    center_thick = _expected_world_width(scene, 2.0, 0.8)
    assert math.isclose(mod.thickness, center_thick, rel_tol=0.001), (
        mod.thickness,
        center_thick,
    )
    assert mod.vertex_group == ""
    center_inner = _expected_world_width(scene, 2.0, 0.25)
    assert math.isclose(_inner_thickness(obj), center_inner, rel_tol=0.001)


def _test_uniform_width_saved_in_preset() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    source = _make_depth_quad()
    _select(source)
    source.bmanga_line_settings.outline_thickness_mm = 0.4
    source.bmanga_line_settings.use_uniform_line_width = True
    source.bmanga_line_settings.line_width_distance_falloff = 0.0
    scene.bmanga_line_preset_name = "均一化テスト"
    assert bpy.ops.bmanga_line.preset_save() == {"FINISHED"}

    target = _make_depth_quad()
    target.name = "BML_uniform_preset_target"
    _select(target)
    scene.bmanga_line_preset_index = 0
    assert bpy.ops.bmanga_line.preset_apply_selected() == {"FINISHED"}
    assert target.bmanga_line_settings.use_uniform_line_width
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    expected = _expected_world_width(scene, 8.0, 0.4)
    actual = target.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(actual, expected, rel_tol=0.001), (actual, expected)


def _test_batch_apply_uses_reference_distance_not_object_distance() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-1.0, 0.0, -2.0))
    near = bpy.context.object
    near.name = "BML_reference_near"
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.0, 0.0, -8.0))
    far = bpy.context.object
    far.name = "BML_reference_far"

    _select_many(near, [near, far])
    near.bmanga_line_settings.use_uniform_line_width = False
    near.bmanga_line_settings.outline_thickness_mm = 0.6
    assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {"FINISHED"}

    near_width = near.modifiers[core.MODIFIER_NAME].thickness
    far_width = far.modifiers[core.MODIFIER_NAME].thickness
    expected = _expected_world_width(scene, 2.0, 0.6)
    assert math.isclose(near_width, far_width, rel_tol=0.001), (
        near_width,
        far_width,
    )
    assert math.isclose(near_width, expected, rel_tol=0.001), (near_width, expected)
    assert math.isclose(
        near.bmanga_line_settings.line_width_reference_distance,
        2.0,
        rel_tol=0.001,
    )

    near.bmanga_line_settings.line_width_reference_distance = 4.0
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    near_width_4m = near.modifiers[core.MODIFIER_NAME].thickness
    far_width_4m = far.modifiers[core.MODIFIER_NAME].thickness
    expected_4m = _expected_world_width(scene, 4.0, 0.6)
    assert math.isclose(near_width_4m, far_width_4m, rel_tol=0.001), (
        near_width_4m,
        far_width_4m,
    )
    assert math.isclose(near_width_4m, expected_4m, rel_tol=0.001), (
        near_width_4m,
        expected_4m,
    )


def _test_multi_select_mm_change_updates_all_modifiers() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-1.5, 0.0, -4.0))
    source = bpy.context.object
    source.name = "BML_multi_width_source"
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.5, 0.0, -4.0))
    target = bpy.context.object
    target.name = "BML_multi_width_target"

    for obj in (source, target):
        _select(obj)
        obj.bmanga_line_settings.use_uniform_line_width = False
        obj.bmanga_line_settings.outline_thickness_mm = 0.5
        assert presets.apply_line_settings(obj, bpy.context)

    _select_many(source, [source, target])
    source.bmanga_line_settings.outline_thickness_mm = 1.2
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}

    assert math.isclose(target.bmanga_line_settings.outline_thickness_mm, 1.2, rel_tol=0.001)
    expected = _expected_world_width(scene, 2.0, 1.2)
    actual = target.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(actual, expected, rel_tol=0.001), (actual, expected)

    source.bmanga_line_settings.line_width_reference_distance = 3.5
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    assert math.isclose(
        target.bmanga_line_settings.line_width_reference_distance,
        3.5,
        rel_tol=0.001,
    )
    expected_reference = _expected_world_width(scene, 3.5, 1.2)
    actual_reference = target.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(actual_reference, expected_reference, rel_tol=0.001), (
        actual_reference,
        expected_reference,
    )

    source.bmanga_line_settings.use_uniform_line_width = True
    source.bmanga_line_settings.line_width_distance_falloff = 0.0
    source.bmanga_line_settings.outline_thickness_mm = 0.8
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    assert target.bmanga_line_settings.use_uniform_line_width
    assert math.isclose(target.bmanga_line_settings.outline_thickness_mm, 0.8, rel_tol=0.001)
    expected_uniform = _expected_world_width(scene, 4.5, 0.8)
    actual_uniform = target.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(actual_uniform, expected_uniform, rel_tol=0.001), (
        actual_uniform,
        expected_uniform,
    )


def _test_object_scale_compensates_modifier_width() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-1.5, 0.0, -4.0))
    normal = bpy.context.object
    normal.name = "BML_scale_normal_cube"
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.5, 0.0, -4.0))
    scaled = bpy.context.object
    scaled.name = "BML_scale_0254_cube"
    scaled.scale = (0.0254, 0.0254, 0.0254)

    for obj in (normal, scaled):
        _select(obj)
        obj.bmanga_line_settings.use_uniform_line_width = False
        obj.bmanga_line_settings.outline_thickness_mm = 0.6
        obj.bmanga_line_settings.inner_line_enabled = True
        obj.bmanga_line_settings.inner_line_thickness_mm = 0.3
        assert presets.apply_line_settings(obj, bpy.context)

    expected = _expected_world_width(scene, 2.0, 0.6)
    expected_inner = _expected_world_width(scene, 2.0, 0.3)
    normal_mod = normal.modifiers[core.MODIFIER_NAME].thickness
    scaled_mod = scaled.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(normal_mod, expected, rel_tol=0.001), (normal_mod, expected)
    assert math.isclose(scaled_mod, expected / 0.0254, rel_tol=0.001), (
        scaled_mod,
        expected / 0.0254,
    )
    assert math.isclose(_line_world_width(normal), expected, rel_tol=0.001), (
        _line_world_width(normal),
        expected,
    )
    assert math.isclose(_line_world_width(scaled), expected, rel_tol=0.001), (
        _line_world_width(scaled),
        expected,
    )
    assert math.isclose(
        _socket_world_width(normal, _inner_thickness(normal)),
        expected_inner,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(scaled, _inner_thickness(scaled)),
        expected_inner,
        rel_tol=0.001,
    )

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-1.5, 0.0, -4.0))
    normal_target = bpy.context.object
    normal_target.name = "BML_scale_normal_target"
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.5, 0.0, -4.0))
    scaled_target = bpy.context.object
    scaled_target.name = "BML_scale_0254_target"
    scaled_target.scale = (0.0254, 0.0254, 0.0254)

    for obj in (normal_target, scaled_target):
        _select(obj)
        obj.bmanga_line_settings.use_uniform_line_width = False
        obj.bmanga_line_settings.outline_thickness_mm = 0.6
        assert presets.apply_line_settings(obj, bpy.context)

    normal.bmanga_line_settings.intersection_enabled = True
    normal.bmanga_line_settings.intersection_method = "BOOLEAN"
    normal.bmanga_line_settings.intersection_thickness_mm = 0.2
    _select_many(normal, [normal, normal_target])
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}

    scaled.bmanga_line_settings.intersection_enabled = True
    scaled.bmanga_line_settings.intersection_method = "BOOLEAN"
    scaled.bmanga_line_settings.intersection_thickness_mm = 0.2
    _select_many(scaled, [scaled, scaled_target])
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}

    expected_intersection = _expected_world_width(scene, 2.0, 0.2)
    normal_owner = _intersection_owner(normal, normal_target)
    scaled_owner = _intersection_owner(scaled, scaled_target)
    assert math.isclose(
        _socket_world_width(normal_owner, _intersection_thickness(normal_owner)),
        expected_intersection,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(scaled_owner, _intersection_thickness(scaled_owner)),
        expected_intersection,
        rel_tol=0.001,
    )


def _make_scaled_cube(name: str, scale: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, -4.0))
    obj = bpy.context.object
    obj.name = name
    obj.scale = (scale, scale, scale)
    return obj


def _test_intersection_target_scale_conversion() -> None:
    scene = bpy.context.scene

    for source_scale, target_scale in ((1.0, 0.0254), (0.0254, 1.0)):
        _clear_scene()
        _configure_scene(scene)

        target = _make_scaled_cube("BML_mixed_target", target_scale)
        _select(target)
        target.bmanga_line_settings.use_uniform_line_width = False
        target.bmanga_line_settings.outline_thickness_mm = 0.6
        assert presets.apply_line_settings(target, bpy.context)

        source = _make_scaled_cube("BML_mixed_source", source_scale)
        source.location.x = max(source_scale, target_scale) * 0.5
        _select(source)
        source.bmanga_line_settings.use_uniform_line_width = False
        source.bmanga_line_settings.outline_thickness_mm = 0.6
        source.bmanga_line_settings.intersection_enabled = True
        source.bmanga_line_settings.intersection_method = "BOOLEAN"
        source.bmanga_line_settings.intersection_thickness_mm = 0.2
        assert presets.apply_line_settings(source, bpy.context)
        _select_many(source, [source, target])
        assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}

        expected_outline = _expected_world_width(scene, 2.0, 0.6)
        expected_intersection = _expected_world_width(scene, 2.0, 0.2)

        owner = _intersection_owner(source, target)
        assert math.isclose(
            _socket_world_width(owner, _intersection_thickness(owner)),
            expected_intersection,
            rel_tol=0.001,
        )
        margin = intersection_lines._intersection_margin(
            source,
            target,
            scale_utils.modifier_thickness_for_world_width(
                source,
                _socket_world_width(owner, _intersection_thickness(owner)),
            ),
        )
        assert math.isclose(margin, expected_outline, rel_tol=0.001), (
            margin,
            expected_outline,
        )


def _make_offset_origin_quad(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(
        [
            (-0.5, -0.5, -7.5),
            (0.5, -0.5, -7.5),
            (0.5, 0.5, -6.5),
            (-0.5, 0.5, -6.5),
            (-0.45, -0.5, -7.5),
            (0.55, -0.5, -7.5),
            (0.55, 0.5, -6.5),
            (-0.45, 0.5, -6.5),
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
    obj.location = (0.0, 0.0, -1.0)
    mat = bpy.data.materials.new(name + "_mat")
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    obj.data.materials.append(mat)
    return obj


def _test_camera_compensation_uses_mesh_position_not_origin() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    obj = _make_offset_origin_quad("BML_origin_offset_visible_mesh")
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.use_uniform_line_width = False
    settings.outline_thickness_mm = 0.6
    settings.use_camera_compensation = True
    settings.camera_compensation_influence = 1.0
    assert presets.apply_line_settings(obj, bpy.context)

    expected = _expected_world_width(scene, 8.0, 0.6)
    actual = _line_world_width(obj)
    assert math.isclose(actual, expected, rel_tol=0.001), (actual, expected)


def _test_low_influence_keeps_configured_line_widths() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0.25, 0.0, -1.2))
    target = bpy.context.object
    target.name = "BML_low_influence_target"
    _select(target)
    target.bmanga_line_settings.use_uniform_line_width = False
    target.bmanga_line_settings.outline_thickness_mm = 0.35
    assert presets.apply_line_settings(target, bpy.context)

    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0.0, 0.0, -1.2))
    source = bpy.context.object
    source.name = "BML_low_influence_source"
    _mark_all_freestyle_edges(source)
    _select(source)
    settings = source.bmanga_line_settings
    settings.use_uniform_line_width = False
    settings.outline_thickness_mm = 0.40
    settings.inner_line_enabled = True
    settings.inner_line_thickness_mm = 0.20
    settings.intersection_enabled = True
    settings.intersection_method = "BOOLEAN"
    settings.intersection_thickness_mm = 0.15
    settings.selection_line_enabled = True
    settings.selection_line_thickness_mm = 0.10
    settings.use_camera_compensation = True
    settings.camera_compensation_influence = 0.0
    settings.line_width_reference_distance = 6.0
    assert presets.apply_line_settings(source, bpy.context)
    _select_many(source, [source, target])
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}
    assert source.modifiers.get(core.GN_MODIFIER_NAME) is not None
    assert _intersection_modifier(source) is not None
    assert source.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME) is not None

    expected_outline = _expected_world_width(scene, 1.2, 0.40)
    expected_inner = _expected_world_width(scene, 1.2, 0.20)
    expected_intersection = _expected_world_width(scene, 1.2, 0.15)
    expected_selection = _expected_world_width(scene, 1.2, 0.10)

    assert math.isclose(_line_world_width(source), expected_outline, rel_tol=0.001), (
        _line_world_width(source),
        expected_outline,
    )
    assert math.isclose(
        _socket_world_width(source, _inner_thickness(source)),
        expected_inner,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(source, _intersection_thickness(source)),
        expected_intersection,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(source, _selection_thickness(source)),
        expected_selection,
        rel_tol=0.001,
    )

    settings.use_uniform_line_width = True
    settings.line_width_distance_falloff = 0.0
    camera_comp.refresh_objects(bpy.context, [source])
    expected_outline_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.outline_thickness,
        )
    )
    expected_inner_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.inner_line_thickness,
        )
    )
    expected_intersection_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.intersection_thickness,
        )
    )
    expected_selection_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.selection_line_thickness,
        )
    )
    assert math.isclose(_line_world_width(source), expected_outline_uniform, rel_tol=0.001)
    assert math.isclose(
        _socket_world_width(source, _inner_thickness(source)),
        expected_inner_uniform,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(source, _intersection_thickness(source)),
        expected_intersection_uniform,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(source, _selection_thickness(source)),
        expected_selection_uniform,
        rel_tol=0.001,
    )


def _test_uniform_width_reuses_camera_depths_across_targets() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0.25, 0.0, -1.2))
    target = bpy.context.object
    target.name = "BML_uniform_reuse_target"
    _select(target)
    target.bmanga_line_settings.use_uniform_line_width = False
    target.bmanga_line_settings.outline_thickness_mm = 0.35
    assert presets.apply_line_settings(target, bpy.context)

    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0.0, 0.0, -1.2))
    source = bpy.context.object
    source.name = "BML_uniform_reuse_source"
    _mark_all_freestyle_edges(source)
    _select(source)
    settings = source.bmanga_line_settings
    settings.outline_thickness_mm = 0.40
    settings.inner_line_enabled = True
    settings.inner_line_thickness_mm = 0.20
    settings.intersection_enabled = True
    settings.intersection_method = "BOOLEAN"
    settings.intersection_thickness_mm = 0.15
    settings.selection_line_enabled = True
    settings.selection_line_thickness_mm = 0.10
    settings.use_uniform_line_width = True
    settings.line_width_distance_falloff = 0.0
    assert presets.apply_line_settings(source, bpy.context)
    _select_many(source, [source, target])
    assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}
    assert source.modifiers.get(core.GN_MODIFIER_NAME) is not None
    assert _intersection_modifier(source) is not None
    assert source.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME) is not None

    original = width_math.vertex_widths_and_depths
    calls = {"count": 0}

    def _counted(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    width_math.vertex_widths_and_depths = _counted
    try:
        camera_comp.refresh_objects(
            bpy.context,
            [source],
            width_targets=("outline", "inner", "intersection", "selection"),
        )
    finally:
        width_math.vertex_widths_and_depths = original

    assert calls["count"] == 1, (
        "頂点単位の線幅計算が線種ごとに重複しています",
        calls["count"],
    )

    expected_outline_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.outline_thickness,
        )
    )
    expected_inner_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.inner_line_thickness,
        )
    )
    expected_intersection_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.intersection_thickness,
        )
    )
    expected_selection_uniform = max(
        camera_comp._uniform_widths_for_mesh(
            scene, scene.camera, source, settings.selection_line_thickness,
        )
    )
    assert math.isclose(_line_world_width(source), expected_outline_uniform, rel_tol=0.001)
    assert math.isclose(
        _socket_world_width(source, _inner_thickness(source)),
        expected_inner_uniform,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(source, _intersection_thickness(source)),
        expected_intersection_uniform,
        rel_tol=0.001,
    )
    assert math.isclose(
        _socket_world_width(source, _selection_thickness(source)),
        expected_selection_uniform,
        rel_tol=0.001,
    )


def _test_camera_compensation_influence_blends_far_width() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0.0, 0.0, -6.0))
    obj = bpy.context.object
    obj.name = "BML_influence_far_source"
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.use_uniform_line_width = False
    settings.outline_thickness_mm = 0.40
    settings.use_camera_compensation = True
    settings.line_width_reference_distance = 1.2
    settings.camera_compensation_influence = 0.0
    assert presets.apply_line_settings(obj, bpy.context)
    low = _line_world_width(obj)
    expected_low = _expected_world_width(scene, 1.2, 0.40)
    assert math.isclose(low, expected_low, rel_tol=0.001), (low, expected_low)

    settings.camera_compensation_influence = 1.0
    camera_comp.refresh_objects(bpy.context, [obj])
    high = _line_world_width(obj)
    expected_high = _expected_world_width(scene, 6.0, 0.40)
    assert math.isclose(high, expected_high, rel_tol=0.001), (high, expected_high)
    assert high > low * 3.0, (low, high)


def _test_resolution_percentage_does_not_change_width() -> None:
    """プレビュー縮小（解像度%）でも線幅の実体は印刷 mm 基準のまま変わらない."""
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)
    scene.render.resolution_y = 8000

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, -5.0))
    obj = bpy.context.object
    obj.name = "BML_resolution_percentage_cube"
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.use_uniform_line_width = False
    settings.outline_thickness_mm = 0.5
    settings.use_camera_compensation = True
    settings.line_width_reference_distance = 5.0
    assert presets.apply_line_settings(obj, bpy.context)

    full_width = _line_world_width(obj)
    expected = _target_pixels(0.5) * (
        2.0 * 5.0 * math.tan(scene.camera.data.angle_y * 0.5) / 8000.0
    )
    assert math.isclose(full_width, expected, rel_tol=0.001), (full_width, expected)

    scene.render.resolution_percentage = 13
    try:
        camera_comp._update_camera_compensation(scene, scene.camera, [obj])
        preview_width = _line_world_width(obj)
        assert math.isclose(preview_width, full_width, rel_tol=0.001), (
            preview_width,
            full_width,
        )
    finally:
        scene.render.resolution_percentage = 100


def _test_evaluated_orthographic_width() -> None:
    scene = bpy.context.scene
    _clear_scene()
    scene.render.resolution_x = 420
    scene.render.resolution_y = 420
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if scene.world:
        scene.world.color = (1.0, 1.0, 1.0)

    bpy.ops.object.camera_add(location=(0.0, 0.0, 5.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.0
    scene.camera = camera

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_uniform_render_cube"
    mat = bpy.data.materials.new("BML_uniform_white")
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    obj.data.materials.append(mat)
    settings = obj.bmanga_line_settings
    settings.outline_thickness_mm = 0.5
    settings.even_thickness = True
    settings.use_rim = True
    settings.use_uniform_line_width = True
    settings.line_width_distance_falloff = 0.0
    _select(obj)
    assert presets.apply_line_settings(obj, bpy.context)

    measured = (
        _evaluated_outline_world_width(obj)
        / (camera.data.ortho_scale / scene.render.resolution_y)
    )
    # アウトラインのオフセット初期値は0なので、評価済みメッシュ上で
    # 元面から外側へ見える張り出しは線幅の半分になる。
    expected = _target_pixels(0.5) * 0.5
    assert abs(measured - expected) <= 1.0, (measured, expected)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.0, 0.0, 0.0))
    scaled = bpy.context.object
    scaled.name = "BML_uniform_render_scaled_cube"
    scaled.scale = (0.0254, 0.0254, 0.0254)
    scaled.data.materials.append(mat)
    scaled_settings = scaled.bmanga_line_settings
    scaled_settings.outline_thickness_mm = 0.5
    scaled_settings.even_thickness = True
    scaled_settings.use_rim = True
    scaled_settings.use_uniform_line_width = True
    scaled_settings.line_width_distance_falloff = 0.0
    _select(scaled)
    assert presets.apply_line_settings(scaled, bpy.context)
    scaled_measured = (
        _evaluated_outline_world_width(scaled)
        / (camera.data.ortho_scale / scene.render.resolution_y)
    )
    assert abs(scaled_measured - expected) <= 1.0, (scaled_measured, expected)


def _test_linked_uniform_width_refresh_does_not_crash() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)
    source_obj = _make_depth_quad()
    _select(source_obj)
    source_obj.bmanga_line_settings.outline_thickness_mm = 0.5
    source_obj.bmanga_line_settings.use_uniform_line_width = True
    source_obj.bmanga_line_settings.line_width_distance_falloff = 0.0
    assert presets.apply_line_settings(source_obj, bpy.context)

    source_path = Path(tempfile.gettempdir()) / "bml_uniform_link_source.blend"
    try:
        source_path.unlink()
    except OSError:
        pass
    # Replacing Blender's entire Main database adds unrelated GN teardown to
    # this linked-width check. Write only the source datablock so the test stays
    # focused on the linked-library refresh path in Blender 5.1.2.
    bpy.data.libraries.write(str(source_path), {source_obj})
    bpy.data.objects.remove(source_obj, do_unlink=True)
    _clear_scene()
    _configure_scene(scene)
    with bpy.data.libraries.load(str(source_path), link=True) as (data_from, data_to):
        assert "BML_uniform_depth_quad" in data_from.objects
        data_to.objects = ["BML_uniform_depth_quad"]
    linked = data_to.objects[0]
    scene.collection.objects.link(linked)
    scene.view_layers[0].objects.active = linked
    linked.select_set(True)
    camera_comp.refresh(bpy.context)
    try:
        source_path.unlink()
    except OSError:
        pass


def _run() -> None:
    b_manga_line.register()
    _clear_scene()
    tests = (
        _test_uniform_width_depth_and_resolution,
        _test_uniform_width_saved_in_preset,
        _test_batch_apply_uses_reference_distance_not_object_distance,
        _test_multi_select_mm_change_updates_all_modifiers,
        _test_object_scale_compensates_modifier_width,
        _test_intersection_target_scale_conversion,
        _test_camera_compensation_uses_mesh_position_not_origin,
        _test_low_influence_keeps_configured_line_widths,
        _test_uniform_width_reuses_camera_depths_across_targets,
        _test_camera_compensation_influence_blends_far_width,
        _test_resolution_percentage_does_not_change_width,
        _test_evaluated_orthographic_width,
        _test_linked_uniform_width_refresh_does_not_crash,
    )
    marker = "--bml-uniform-case"
    if marker in sys.argv:
        selected = int(sys.argv[sys.argv.index(marker) + 1])
        tests = (tests[selected - 1],)
    for index, test in enumerate(tests, start=1):
        print(f"BML_UNIFORM_STEP {index}: {test.__name__}", flush=True)
        test()
    print("[PASS] B-MANGA Line uniform width follows mm, DPI, and resolution")


def main() -> None:
    with temporary_line_preset_store():
        _run()


if __name__ == "__main__":
    main()
