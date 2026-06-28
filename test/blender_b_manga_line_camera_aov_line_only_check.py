"""B-MANGA Line: camera selection, AOV, line-only display, and inner line safety."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, inner_lines, outline_setup, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def _make_camera(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.object.camera_add(location=location)
    camera = bpy.context.object
    camera.name = name
    return camera


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _assert_aov(scene: bpy.types.Scene) -> None:
    for view_layer in scene.view_layers:
        assert any(aov.name == core.AOV_NAME for aov in view_layer.aovs), view_layer.name


def _make_two_material_cube() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_two_material_cube"
    obj.data.materials.append(_make_material("BML_surface_a", (0.8, 0.2, 0.2, 1.0)))
    obj.data.materials.append(_make_material("BML_surface_b", (0.2, 0.8, 0.2, 1.0)))
    for poly in obj.data.polygons:
        poly.material_index = poly.index % 2
    return obj


def _test_camera_selection_and_aov() -> None:
    scene = bpy.context.scene
    near = _make_camera("BML_near_camera", (0.0, -2.0, 0.0))
    far = _make_camera("BML_far_camera", (0.0, -10.0, 0.0))
    scene.camera = far
    scene.bmanga_line_camera = None

    obj = _make_two_material_cube()
    _select(obj)
    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.01
    settings.use_camera_compensation = True
    settings.camera_compensation_influence = 1.0

    assert presets.apply_line_settings(obj, bpy.context)
    far_thickness = obj.modifiers[core.MODIFIER_NAME].thickness
    assert far_thickness > 0.08, far_thickness
    assert obj.get(core.PROP_REF_MODE) == core.REF_MODE_VIEW

    obj[core.PROP_REF_FOV_TAN] = 999.0
    camera_comp.refresh(bpy.context)
    old_fov_ignored_thickness = obj.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(
        old_fov_ignored_thickness,
        far_thickness,
        rel_tol=0.01,
    ), (old_fov_ignored_thickness, far_thickness)

    scene.camera = near
    camera_comp.refresh(bpy.context)
    near_thickness = obj.modifiers[core.MODIFIER_NAME].thickness
    assert near_thickness < far_thickness * 0.35, (near_thickness, far_thickness)

    scene.bmanga_line_camera = far
    camera_comp.refresh(bpy.context)
    override_thickness = obj.modifiers[core.MODIFIER_NAME].thickness
    assert math.isclose(override_thickness, far_thickness, rel_tol=0.01), (
        override_thickness,
        far_thickness,
    )
    scene.bmanga_line_camera = None
    _assert_aov(scene)


def _test_line_only_restore() -> None:
    obj = bpy.data.objects["BML_two_material_cube"]
    _select(obj)
    before = [mat.name if mat else "" for mat in obj.data.materials[:2]]
    assert bpy.ops.bmanga_line.set_line_only(line_only=True) == {"FINISHED"}
    hidden = [mat.name if mat else "" for mat in obj.data.materials[:2]]
    assert hidden == [outline_setup.LINE_ONLY_MATERIAL_NAME] * 2, hidden
    assert any(
        mat and mat.name.startswith(core.MATERIAL_NAME)
        for mat in obj.data.materials
    )
    assert bpy.ops.bmanga_line.set_line_only(line_only=False) == {"FINISHED"}
    restored = [mat.name if mat else "" for mat in obj.data.materials[:2]]
    assert restored == before, (restored, before)


def _test_inner_line_keeps_multimaterial_source() -> None:
    obj = bpy.data.objects["BML_two_material_cube"]
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.inner_line_angle = math.radians(10.0)
    settings.inner_line_thickness = 0.02
    mat = outline_setup.get_outline_material(obj)
    assert inner_lines.apply_inner_lines(
        obj,
        angle=settings.inner_line_angle,
        thickness=settings.inner_line_thickness,
        material=mat,
    )
    mod = obj.modifiers[core.GN_MODIFIER_NAME]
    sid = inner_lines._find_socket_id(mod.node_group, "ライン素材番号")
    assert sid is not None
    assert mod[sid] >= 2, mod[sid]

    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    material_indices = {poly.material_index for poly in mesh.polygons}
    bpy.data.meshes.remove(mesh)
    assert 0 in material_indices and 1 in material_indices, material_indices


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _assert_aov(bpy.context.scene)
    _test_camera_selection_and_aov()
    _test_line_only_restore()
    _test_inner_line_keeps_multimaterial_source()
    print("[PASS] B-MANGA Line camera/AOV/line-only/inner-line checks")


if __name__ == "__main__":
    main()
