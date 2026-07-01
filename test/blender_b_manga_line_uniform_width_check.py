"""B-MANGA Line: uniform line width follows camera, DPI, and resolution."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, inner_lines, presets, scale_utils  # noqa: E402


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
        ],
        [],
        [(0, 1, 2, 3)],
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
    camera = scene.camera
    height = scene.render.resolution_y * scene.render.resolution_percentage / 100.0
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


def _evaluated_outline_world_width(obj: bpy.types.Object) -> float:
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
    settings.inner_line_enabled = True
    settings.inner_line_thickness_mm = 0.25
    settings.use_uniform_line_width = True
    assert presets.apply_line_settings(obj, bpy.context)
    mod = obj.modifiers[core.MODIFIER_NAME]
    near = _expected_world_width(scene, 2.0, 0.5)
    far = _expected_world_width(scene, 8.0, 0.5)
    assert math.isclose(mod.thickness, far, rel_tol=0.001), (mod.thickness, far)
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
    far_thick = _expected_world_width(scene, 8.0, 1.0)
    assert math.isclose(mod.thickness, far_thick, rel_tol=0.001), (
        mod.thickness,
        far_thick,
    )

    settings.use_uniform_line_width = False
    center_thick = _expected_world_width(scene, 2.0, 1.0)
    assert math.isclose(mod.thickness, center_thick, rel_tol=0.001), (
        mod.thickness,
        center_thick,
    )
    assert mod.vertex_group == ""
    assert math.isclose(_inner_thickness(obj), center_thick * 0.25, rel_tol=0.001)


def _test_uniform_width_saved_in_preset() -> None:
    scene = bpy.context.scene
    _clear_scene()
    _configure_scene(scene)

    source = _make_depth_quad()
    _select(source)
    source.bmanga_line_settings.outline_thickness_mm = 0.4
    source.bmanga_line_settings.use_uniform_line_width = True
    scene.bmanga_line_preset_name = "均一化テスト"
    assert bpy.ops.bmanga_line.preset_save() == {"FINISHED"}

    target = _make_depth_quad()
    target.name = "BML_uniform_preset_target"
    _select(target)
    scene.bmanga_line_preset_index = 0
    assert bpy.ops.bmanga_line.preset_apply_selected() == {"FINISHED"}
    assert target.bmanga_line_settings.use_uniform_line_width
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
    near.bmanga_line_settings.outline_thickness_mm = 0.6
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}

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
        obj.bmanga_line_settings.outline_thickness_mm = 0.5
        assert presets.apply_line_settings(obj, bpy.context)

    _select_many(source, [source, target])
    source.bmanga_line_settings.outline_thickness_mm = 1.2

    assert math.isclose(target.bmanga_line_settings.outline_thickness_mm, 1.2, rel_tol=0.001)
    expected = _expected_world_width(scene, 2.0, 1.2)
    actual = target.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(actual, expected, rel_tol=0.001), (actual, expected)

    source.bmanga_line_settings.line_width_reference_distance = 3.5
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
    source.bmanga_line_settings.outline_thickness_mm = 0.8
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
        obj.bmanga_line_settings.outline_thickness_mm = 0.6
        assert presets.apply_line_settings(obj, bpy.context)

    expected = _expected_world_width(scene, 2.0, 0.6)
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
    _select(obj)
    assert presets.apply_line_settings(obj, bpy.context)

    measured = (
        _evaluated_outline_world_width(obj)
        / (camera.data.ortho_scale / scene.render.resolution_y)
    )
    expected = _target_pixels(0.5)
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
    assert presets.apply_line_settings(source_obj, bpy.context)

    source_path = Path(tempfile.gettempdir()) / "bml_uniform_link_source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(source_path))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
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


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _test_uniform_width_depth_and_resolution()
    _test_uniform_width_saved_in_preset()
    _test_batch_apply_uses_reference_distance_not_object_distance()
    _test_multi_select_mm_change_updates_all_modifiers()
    _test_object_scale_compensates_modifier_width()
    _test_evaluated_orthographic_width()
    _test_linked_uniform_width_refresh_does_not_crash()
    print("[PASS] B-MANGA Line uniform width follows mm, DPI, and resolution")


if __name__ == "__main__":
    main()
