"""Blender 5.1 checks for the local-only B-MANGA Line outline subdivision."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

from b_manga_line import outline_local_subdivision as local_subdivision  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _evaluated_mesh(obj: bpy.types.Object) -> bpy.types.Mesh:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(depsgraph).to_mesh()


def _release_mesh(obj: bpy.types.Object) -> None:
    obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()


def _material_face_counts(mesh: bpy.types.Mesh, line_material: bpy.types.Material):
    generated = mesh.attributes.get(local_subdivision.GENERATED_LINE_ATTR)
    assert generated is not None, "生成ライン属性がありません"
    assert generated.domain == "FACE", generated.domain
    surface = 0
    line = 0
    for polygon in mesh.polygons:
        if bool(generated.data[polygon.index].value):
            line += 1
        else:
            surface += 1
    return surface, line


def _bbox(mesh: bpy.types.Mesh):
    return tuple(
        (
            min(vertex.co[axis] for vertex in mesh.vertices),
            max(vertex.co[axis] for vertex in mesh.vertices),
        )
        for axis in range(3)
    )


def _assert_cube_shell() -> None:
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = "BML_LocalSubdivision_Cube"
    surface = bpy.data.materials.new("BML_LocalSubdivision_Surface")
    line = bpy.data.materials.new("BML_LocalSubdivision_Line")
    obj.data.materials.append(surface)

    original_vertices = len(obj.data.vertices)
    original_faces = len(obj.data.polygons)
    original_material_indices = [polygon.material_index for polygon in obj.data.polygons]
    settings = {
        "line_subdivision": True,
        "midpoint_width_scale": 0.0,
        "midpoint_jitter_percent": 0.0,
        "midpoint_angle": 1.7453292520,
        "width_curve_25": 0.25,
        "width_curve_50": 0.50,
        "width_curve_75": 0.75,
    }
    mod = local_subdivision.ensure(
        obj,
        local_thickness=0.2,
        offset=0.0,
        material=line,
        settings=settings,
    )
    assert mod is not None
    assert local_subdivision.is_modifier(mod)
    assert local_subdivision.has_active(obj)
    assert abs(local_subdivision.local_thickness(obj) - 0.2) < 1.0e-7

    mesh = _evaluated_mesh(obj)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert (surface_faces, line_faces) == (6, 96), (surface_faces, line_faces)
        bounds = _bbox(mesh)
        for minimum, maximum in bounds:
            assert abs(minimum + 1.1) < 1.0e-5, bounds
            assert abs(maximum - 1.1) < 1.0e-5, bounds
    finally:
        _release_mesh(obj)

    assert len(obj.data.vertices) == original_vertices
    assert len(obj.data.polygons) == original_faces
    assert [polygon.material_index for polygon in obj.data.polygons] == original_material_indices

    local_subdivision.sync(obj, settings={"line_subdivision": False})
    mesh = _evaluated_mesh(obj)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert (surface_faces, line_faces) == (6, 6), (surface_faces, line_faces)
    finally:
        _release_mesh(obj)

    assert local_subdivision.remove(obj)
    assert not local_subdivision.has_active(obj)
    assert obj.modifiers.get(local_subdivision.MODIFIER_NAME) is None


def _assert_foreign_modifier_is_protected() -> None:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3.0, 0.0, 0.0))
    obj = bpy.context.object
    foreign = obj.modifiers.new(local_subdivision.MODIFIER_NAME, "SOLIDIFY")
    line = bpy.data.materials.new("BML_LocalSubdivision_ProtectedLine")
    owned = local_subdivision.ensure(
        obj,
        local_thickness=0.1,
        offset=0.0,
        material=line,
        settings={"line_subdivision": True},
    )
    assert owned is not None
    assert foreign in obj.modifiers[:]
    assert foreign.type == "SOLIDIFY"
    assert owned != foreign
    assert local_subdivision.remove(obj)
    assert foreign in obj.modifiers[:]


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        _clear_scene()
        _assert_cube_shell()
        _clear_scene()
        _assert_foreign_modifier_is_protected()
        print("BMANGA_LINE_LOCAL_SUBDIVISION_NODE_OK")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
