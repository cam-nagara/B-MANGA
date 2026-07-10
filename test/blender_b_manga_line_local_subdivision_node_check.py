"""Blender 5.1 checks for the local-only B-MANGA Line outline subdivision."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import bmesh
import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

from b_manga_line import outline_local_subdivision as local_subdivision  # noqa: E402
from b_manga_line import line_visibility, modifier_stack, outline_setup  # noqa: E402


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
        assert (surface_faces, line_faces) == (6, 24), (surface_faces, line_faces)
        bounds = _bbox(mesh)
        for minimum, maximum in bounds:
            assert abs(minimum + 1.1) < 1.0e-5, bounds
            assert abs(maximum - 1.1) < 1.0e-5, bounds
    finally:
        _release_mesh(obj)

    assert len(obj.data.vertices) == original_vertices
    assert len(obj.data.polygons) == original_faces
    assert [polygon.material_index for polygon in obj.data.polygons] == original_material_indices

    local_subdivision.sync(
        obj,
        settings={
            "line_subdivision": True,
            "midpoint_width_scale": -0.25,
            "midpoint_angle": 1.7453292520,
            "width_curve_25": 0.25,
            "width_curve_50": 0.50,
            "width_curve_75": 0.75,
        },
    )
    mesh = _evaluated_mesh(obj)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert (surface_faces, line_faces) == (6, 96), (surface_faces, line_faces)
        base_positions = tuple(tuple(vertex.co) for vertex in mesh.vertices)
    finally:
        _release_mesh(obj)

    local_subdivision.sync(
        obj,
        settings={
            "line_subdivision": True,
            "midpoint_width_scale": -0.5,
            "midpoint_angle": 1.7453292520,
            "width_curve_25": 0.25,
            "width_curve_50": 0.50,
            "width_curve_75": 0.75,
        },
    )
    mesh = _evaluated_mesh(obj)
    try:
        tapered_positions = tuple(tuple(vertex.co) for vertex in mesh.vertices)
        assert len(tapered_positions) == len(base_positions)
        assert any(
            sum((left[i] - right[i]) ** 2 for i in range(3)) > 1.0e-8
            for left, right in zip(base_positions, tapered_positions)
        ), "中間頂点の線幅調整がライン殻へ反映されていません"
    finally:
        _release_mesh(obj)

    local_subdivision.sync(
        obj,
        settings={
            "line_subdivision": True,
            "midpoint_width_scale": 0.0,
            "midpoint_jitter_percent": 0.0,
        },
    )
    mesh = _evaluated_mesh(obj)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert (surface_faces, line_faces) == (6, 24), (surface_faces, line_faces)
    finally:
        _release_mesh(obj)

    local_subdivision.sync(obj, settings={"line_subdivision": False})
    mesh = _evaluated_mesh(obj)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert (surface_faces, line_faces) == (6, 24), (surface_faces, line_faces)
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
    assert owned.name != foreign.name
    foreign.show_viewport = True
    foreign.show_render = True
    assert line_visibility.set_line_visibility(obj, False)
    assert not owned.show_viewport and not owned.show_render
    assert foreign.show_viewport and foreign.show_render
    line_visibility.set_line_visibility(obj, True)
    assert owned.show_viewport and owned.show_render
    assert foreign.show_viewport and foreign.show_render
    modifier_stack.reorder_line_modifiers(obj)
    assert foreign in obj.modifiers[:]
    assert outline_setup.remove_outline_geometry(obj)
    assert foreign in obj.modifiers[:]
    assert local_subdivision.get_modifier(obj) is None
    assert foreign.show_viewport and foreign.show_render


def _assert_medium_triangle_mesh_stays_bounded() -> None:
    mesh = bpy.data.meshes.new("BML_LocalSubdivision_MediumMesh")
    edit = bmesh.new()
    bmesh.ops.create_grid(edit, x_segments=80, y_segments=80, size=5.0)
    bmesh.ops.triangulate(edit, faces=list(edit.faces))
    edit.to_mesh(mesh)
    edit.free()
    obj = bpy.data.objects.new("BML_LocalSubdivision_Medium", mesh)
    bpy.context.collection.objects.link(obj)
    source_faces = len(mesh.polygons)
    line = bpy.data.materials.new("BML_LocalSubdivision_MediumLine")

    started = time.perf_counter()
    local_subdivision.ensure(
        obj,
        local_thickness=0.01,
        offset=0.0,
        material=line,
        settings={
            "line_subdivision": True,
            "midpoint_width_scale": -0.5,
            "midpoint_angle": 1.7453292520,
        },
    )
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = evaluated.to_mesh()
    elapsed = time.perf_counter() - started
    try:
        assert len(mesh.polygons) == source_faces
        evaluated_faces = len(result.polygons)
        assert source_faces < evaluated_faces <= source_faces * 17, (
            source_faces,
            evaluated_faces,
        )
        assert elapsed < 5.0, elapsed
    finally:
        evaluated.to_mesh_clear()
    print(
        f"BML_LOCAL_SUBDIVISION_PERF source_faces={source_faces} "
        f"evaluated_faces={evaluated_faces} elapsed={elapsed:.6f}s"
    )
    assert local_subdivision.remove(obj)


def _assert_problem_meshes_stay_unchanged() -> None:
    cases = {
        "OpenTriangles": (
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [(0, 1, 2), (0, 2, 3)],
        ),
        "NonManifoldEdge": (
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)],
            [(0, 1, 2), (1, 0, 3), (0, 1, 4)],
        ),
        "DuplicateFace": (
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            [(0, 1, 2), (0, 1, 2)],
        ),
    }
    line = bpy.data.materials.new("BML_LocalSubdivision_ProblemLine")
    for index, (name, (vertices, faces)) in enumerate(cases.items()):
        mesh = bpy.data.meshes.new("BML_" + name)
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new("BML_" + name, mesh)
        obj.location.x = float(index * 2)
        bpy.context.collection.objects.link(obj)
        before = (
            tuple(tuple(vertex.co) for vertex in mesh.vertices),
            tuple(tuple(polygon.vertices) for polygon in mesh.polygons),
            tuple(polygon.material_index for polygon in mesh.polygons),
        )
        owned = local_subdivision.ensure(
            obj,
            local_thickness=0.02,
            offset=0.0,
            material=line,
            settings={"line_subdivision": True, "midpoint_width_scale": -0.25},
        )
        assert owned is not None
        evaluated = _evaluated_mesh(obj)
        try:
            assert len(evaluated.polygons) >= len(mesh.polygons)
        finally:
            _release_mesh(obj)
        after = (
            tuple(tuple(vertex.co) for vertex in mesh.vertices),
            tuple(tuple(polygon.vertices) for polygon in mesh.polygons),
            tuple(polygon.material_index for polygon in mesh.polygons),
        )
        assert after == before, name
        assert local_subdivision.remove(obj)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        _clear_scene()
        _assert_cube_shell()
        _clear_scene()
        _assert_foreign_modifier_is_protected()
        _clear_scene()
        _assert_problem_meshes_stay_unchanged()
        _clear_scene()
        _assert_medium_triangle_mesh_stays_bounded()
        print("BMANGA_LINE_LOCAL_SUBDIVISION_NODE_OK")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
