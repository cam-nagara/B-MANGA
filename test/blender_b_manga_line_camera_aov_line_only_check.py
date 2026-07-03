"""B-MANGA Line: camera selection, AOV, line-only display, and inner line safety."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    inner_lines,
    outline_setup,
    presets,
)


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
        names = {aov.name for aov in view_layer.aovs}
        missing = set(core.AOV_NAMES) - names
        assert not missing, (view_layer.name, sorted(missing))


def _outline_material(obj: bpy.types.Object) -> bpy.types.Material:
    mat = outline_setup.get_outline_material(obj)
    assert mat is not None, "ライン用マテリアルがありません"
    return mat


def _has_line_aov_node(mat: bpy.types.Material) -> bool:
    assert mat.use_nodes, "ライン用マテリアルがノード化されていません"
    names = {
        getattr(node, "aov_name", "")
        for node in mat.node_tree.nodes
    }
    return {core.AOV_NAME, core.AOV_OUTLINE_RAW_NAME}.issubset(names)


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
    near = _make_camera("BML_near_camera", (0.0, 0.0, 2.0))
    far = _make_camera("BML_far_camera", (0.0, 0.0, 10.0))
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
    assert far_thickness > 0.01, far_thickness
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
    line_mods = list(core.iter_line_modifiers(obj))
    assert any(mod.name == core.MODIFIER_NAME for mod in line_mods), line_mods
    assert bpy.ops.bmanga_line.set_visibility(visible=False) == {"FINISHED"}
    for mod in line_mods:
        assert not mod.show_viewport and not mod.show_render
    assert bpy.ops.bmanga_line.set_line_only(line_only=True) == {"FINISHED"}
    for mod in line_mods:
        assert mod.show_viewport and mod.show_render
    hidden = [mat.name if mat else "" for mat in obj.data.materials[:2]]
    if not bool(obj.get(core.PROP_LINE_ONLY, False)):
        assert hidden == before, hidden
        assert obj.modifiers.get(outline_setup.LINE_ONLY_WIREFRAME_NAME) is None
    else:
        assert hidden == [outline_setup.LINE_ONLY_MATERIAL_NAME] * 2, hidden
        assert obj.modifiers.get(outline_setup.LINE_ONLY_WIREFRAME_NAME) is None
    assert any(
        mat and mat.name.startswith(core.MATERIAL_NAME)
        for mat in obj.data.materials
    )
    assert bpy.ops.bmanga_line.set_line_only(line_only=False) == {"FINISHED"}
    restored = [mat.name if mat else "" for mat in obj.data.materials[:2]]
    assert restored == before, (restored, before)
    assert obj.modifiers.get(outline_setup.LINE_ONLY_WIREFRAME_NAME) is None


def _test_outline_material_aov_repair() -> None:
    obj = bpy.data.objects["BML_two_material_cube"]
    mat = _outline_material(obj)
    for node in list(mat.node_tree.nodes):
        if getattr(node, "aov_name", "") in {core.AOV_NAME, core.AOV_OUTLINE_RAW_NAME}:
            mat.node_tree.nodes.remove(node)
    assert not _has_line_aov_node(mat), "テスト用にAOVノードを削除できていません"

    mat = outline_setup.get_outline_material(obj)
    assert mat is not None
    assert _has_line_aov_node(mat), "ライン用マテリアルのAOVノードが自動復旧しません"

    for node in list(mat.node_tree.nodes):
        if getattr(node, "aov_name", "") in {core.AOV_NAME, core.AOV_OUTLINE_RAW_NAME}:
            mat.node_tree.nodes.remove(node)
    outline_setup.update_material_color(obj, (0.1, 0.2, 0.3, 1.0))
    assert _has_line_aov_node(mat), "線色更新時にAOVノードが自動復旧しません"


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
    _test_outline_material_aov_repair()
    _test_inner_line_keeps_multimaterial_source()
    print("[PASS] B-MANGA Line camera/AOV/line-only/inner-line checks")


if __name__ == "__main__":
    main()
