"""B-MANGA Line: default shell intersection lines avoid precise pair generation."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_lines,
    intersection_shell,
    outline_setup,
    presets,
)


THICKNESS_SOCKET = "線の太さ"
OFFSET_SOCKET = "オフセット"
MATERIAL_SOCKET = "マテリアル"


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    assert settings.intersection_method == "SHELL"
    settings.intersection_enabled = True
    settings.use_intersection_creation_limit = False
    return obj


def _make_surface_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return mat


def _make_source_slab(surface_mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_shell_contact_slab"
    obj.dimensions = (3.0, 3.0, 0.1)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(surface_mat)
    settings = obj.bmanga_line_settings
    settings.intersection_enabled = True
    settings.use_intersection_creation_limit = False
    settings.intersection_thickness = 0.03
    return obj


def _make_contact_cylinder(surface_mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48,
        radius=0.5,
        depth=1.0,
        location=(0.0, 0.0, 0.55),
    )
    obj = bpy.context.object
    obj.name = "BML_shell_contact_cylinder"
    obj.data.materials.append(surface_mat)
    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.03
    settings.intersection_enabled = True
    settings.use_intersection_creation_limit = False
    settings.intersection_thickness = 0.03
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _socket_id(tree: bpy.types.NodeTree, name: str) -> str:
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item.identifier
    raise AssertionError(f"socket not found: {name}")


def _socket_value(mod: bpy.types.Modifier, name: str):
    tree = getattr(mod, "node_group", None)
    if tree is None:
        raise AssertionError(f"node group missing: {mod.name}")
    return mod[_socket_id(tree, name)]


def _intersection_material_polygons(obj: bpy.types.Object) -> int:
    mat = outline_setup.get_line_material(obj, "intersection")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_index = None
        for index, item in enumerate(mesh.materials):
            if item is not None and item.name.startswith(mat.name):
                line_index = index
                break
        assert line_index is not None, "交差線素材が評価済みメッシュにありません"
        return sum(1 for poly in mesh.polygons if poly.material_index == line_index)
    finally:
        bpy.data.meshes.remove(mesh)


def _intersection_material_vertices(obj: bpy.types.Object) -> list:
    mat = outline_setup.get_line_material(obj, "intersection")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_index = None
        for index, item in enumerate(mesh.materials):
            if item is not None and item.name.startswith(mat.name):
                line_index = index
                break
        assert line_index is not None, "交差線素材が評価済みメッシュにありません"
        line_vertices = set()
        for poly in mesh.polygons:
            if poly.material_index == line_index:
                line_vertices.update(poly.vertices)
        matrix = obj.matrix_world.copy()
        return [matrix @ mesh.vertices[index].co for index in line_vertices]
    finally:
        bpy.data.meshes.remove(mesh)


def _assert_shell_contact_line_appears() -> None:
    _clear_scene()
    surface = _make_surface_material("BML_shell_contact_surface")
    source = _make_source_slab(surface)
    target = _make_contact_cylinder(surface)
    assert presets.apply_line_settings(target, bpy.context)
    assert presets.apply_line_settings(source, bpy.context)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)

    coords = _intersection_material_vertices(source) + _intersection_material_vertices(target)
    assert len(coords) > 80, f"接触部の交差線素材面が少なすぎます: {len(coords)}"
    min_x = min(co.x for co in coords)
    max_x = max(co.x for co in coords)
    min_y = min(co.y for co in coords)
    max_y = max(co.y for co in coords)
    min_z = min(co.z for co in coords)
    max_z = max(co.z for co in coords)
    assert min_x < -0.45 and max_x > 0.45, (min_x, max_x)
    assert min_y < -0.45 and max_y > 0.45, (min_y, max_y)
    assert -0.08 < min_z < 0.08, (min_z, max_z)
    assert -0.08 < max_z < 0.08, (min_z, max_z)


def _assert_non_intersecting_shell_stays_clean() -> None:
    _clear_scene()
    surface = _make_surface_material("BML_shell_far_surface")
    source = _make_source_slab(surface)
    target = _make_contact_cylinder(surface)
    target.location.z += 1.0
    assert presets.apply_line_settings(target, bpy.context)
    assert presets.apply_line_settings(source, bpy.context)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)

    coords = _intersection_material_vertices(source) + _intersection_material_vertices(target)
    assert not coords, f"離れたメッシュに交差線素材が出ています: {len(coords)}"


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        objects = [
            _make_cube("BML_shell_intersection_A", (-0.25, 0.0, 0.0)),
            _make_cube("BML_shell_intersection_B", (0.25, 0.0, 0.0)),
            _make_cube("BML_shell_intersection_C", (0.0, 0.25, 0.0)),
        ]
        _select(objects)

        real_auto_targets = intersection_lines._auto_targets

        def forbidden_auto_targets(*_args, **_kwargs):
            raise AssertionError("交差相手の候補列挙が呼ばれています")

        intersection_lines._auto_targets = forbidden_auto_targets
        try:
            for obj in objects:
                assert presets.apply_line_settings(
                    obj,
                    bpy.context,
                    refresh_scene=False,
                ), obj.name

            old_threshold = intersection_lines._DEFERRED_VIEWPORT_THRESHOLD
            intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = 0
            try:
                refreshed = intersection_lines.refresh_scene_intersections(bpy.context.scene)
            finally:
                intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = old_threshold
        finally:
            intersection_lines._auto_targets = real_auto_targets

        assert set(refreshed) == set(objects), [obj.name for obj in refreshed]
        for obj in objects:
            mods = list(core.iter_intersection_modifiers(obj))
            assert len(mods) == 1, (obj.name, [mod.name for mod in mods])
            mod = mods[0]
            assert mod.name == intersection_shell.SHELL_MODIFIER_NAME
            assert mod.node_group is not None
            assert not any(
                node.bl_idname == "GeometryNodeProximity"
                for node in mod.node_group.nodes
            )
            assert any(
                node.bl_idname == "GeometryNodeMeshBoolean"
                and getattr(node, "label", "") == "BML_IntersectionShellBoolean"
                for node in mod.node_group.nodes
            )
            assert intersection_lines._uses_named_attribute(
                mod.node_group,
                core.VG_INTERSECTION_LINE_WIDTH,
            )
            assert intersection_lines._modifier_target(mod) is None
            targets = intersection_shell.modifier_targets(mod)
            assert obj not in targets
            assert len(targets) == len(objects) - 1
            assert mod.show_viewport
            assert mod.show_render
            assert not intersection_lines.is_deferred_viewport_modifier(mod)
            assert _intersection_material_polygons(obj) > 0

        source = objects[0]
        mod = next(core.iter_intersection_modifiers(source))
        mat = outline_setup.get_line_material(source, "intersection")
        intersection_lines.update_parameters(
            source,
            thickness=0.0123,
            offset=-0.25,
            material=mat,
        )
        assert abs(float(_socket_value(mod, THICKNESS_SOCKET)) - 0.0123) < 1.0e-7
        assert abs(float(_socket_value(mod, OFFSET_SOCKET)) + 0.25) < 1.0e-7
        assert _socket_value(mod, MATERIAL_SOCKET) == mat

        _assert_shell_contact_line_appears()
        _assert_non_intersecting_shell_stays_clean()

        print("[PASS] default shell intersection lines work without precise pair generation")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
