"""Blender 5.1 checks for the local-only B-MANGA Line outline subdivision."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

from b_manga_line import outline_local_subdivision as local_subdivision  # noqa: E402
from b_manga_line import line_visibility, modifier_stack, outline_setup  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    _ensure_camera()


_REALIZE_MODIFIER = "BML_TestRealizeLineInstances"
_REALIZE_TREE = "BML_TestRealizeLineInstances"


def _ensure_camera() -> None:
    bpy.ops.object.camera_add(location=(4.0, -6.0, 4.0))
    camera = bpy.context.object
    camera.rotation_euler = (
        Vector((0.0, 0.0, 0.0)) - camera.location
    ).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera


def _realize_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(_REALIZE_TREE)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(_REALIZE_TREE, "GeometryNodeTree")
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


def _evaluated_mesh(
    obj: bpy.types.Object, *, realize_instances: bool = False
) -> bpy.types.Mesh:
    if realize_instances:
        mod = obj.modifiers.new(_REALIZE_MODIFIER, "NODES")
        mod.node_group = _realize_tree()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(depsgraph).to_mesh()


def _release_mesh(obj: bpy.types.Object) -> None:
    obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
    mod = obj.modifiers.get(_REALIZE_MODIFIER)
    if mod is not None:
        obj.modifiers.remove(mod)


def _material_face_counts(
    mesh: bpy.types.Mesh,
    line_material: bpy.types.Material,
    *,
    allow_no_line: bool = False,
):
    del line_material
    generated = mesh.attributes.get(local_subdivision.GENERATED_LINE_ATTR)
    if generated is None and allow_no_line:
        return len(mesh.polygons), 0
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


def _generated_projected_bbox(mesh, obj, camera):
    generated = mesh.attributes.get(local_subdivision.GENERATED_LINE_ATTR)
    assert generated is not None
    indices: set[int] = set()
    for polygon in mesh.polygons:
        if generated.data[polygon.index].value:
            indices.update(polygon.vertices)
    projected = [
        world_to_camera_view(
            bpy.context.scene,
            camera,
            obj.matrix_world @ mesh.vertices[index].co,
        )
        for index in indices
    ]
    return tuple(
        (min(point[axis] for point in projected), max(point[axis] for point in projected))
        for axis in (0, 1)
    )


def _assert_cube_shell() -> None:
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = "BML_LocalSubdivision_Cube"
    surface = bpy.data.materials.new("BML_LocalSubdivision_Surface")
    line = bpy.data.materials.new("BML_LocalSubdivision_Line")
    obj.data.materials.append(surface)

    original_vertices = len(obj.data.vertices)
    original_edges = len(obj.data.edges)
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
    assert not any(
        node.bl_idname in {"GeometryNodeSubdivideMesh", "GeometryNodeSubdivisionSurface"}
        for node in mod.node_group.nodes
    ), "面を細分化するノードが生成ライン経路に残っています"
    assert any(
        node.bl_idname == "GeometryNodeGeometryToInstance"
        for node in mod.node_group.nodes
    )
    assert any(
        node.bl_idname == "GeometryNodeCurveSplineType"
        and node.spline_type == "BEZIER"
        for node in mod.node_group.nodes
    )

    mesh = _evaluated_mesh(obj)
    try:
        assert len(mesh.vertices) == original_vertices
        assert len(mesh.edges) == original_edges
        assert len(mesh.polygons) == original_faces
        assert mesh.attributes.get(local_subdivision.GENERATED_LINE_ATTR) is None
    finally:
        _release_mesh(obj)

    mesh = _evaluated_mesh(obj, realize_instances=True)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert surface_faces == 6, (surface_faces, line_faces)
        assert line_faces > 0, (surface_faces, line_faces)
        bounds = _bbox(mesh)
        assert any(maximum > 1.0 or minimum < -1.0 for minimum, maximum in bounds)
    finally:
        _release_mesh(obj)

    assert len(obj.data.vertices) == original_vertices
    assert len(obj.data.edges) == original_edges
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
    mesh = _evaluated_mesh(obj, realize_instances=True)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert surface_faces == 6 and line_faces > 0, (surface_faces, line_faces)
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
    mesh = _evaluated_mesh(obj, realize_instances=True)
    try:
        tapered_positions = tuple(tuple(vertex.co) for vertex in mesh.vertices)
        assert len(tapered_positions) == len(base_positions)
        assert any(
            sum((left[i] - right[i]) ** 2 for i in range(3)) > 1.0e-8
            for left, right in zip(base_positions, tapered_positions)
        ), "中間頂点の線幅調整が生成ラインへ反映されていません"
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
    mesh = _evaluated_mesh(obj, realize_instances=True)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert surface_faces == 6 and line_faces > 0, (surface_faces, line_faces)
        smooth_line_faces = line_faces
    finally:
        _release_mesh(obj)

    local_subdivision.sync(obj, settings={"line_subdivision": False})
    mesh = _evaluated_mesh(obj, realize_instances=True)
    try:
        surface_faces, line_faces = _material_face_counts(mesh, line)
        assert surface_faces == 6 and 0 < line_faces < smooth_line_faces, (
            surface_faces,
            line_faces,
            smooth_line_faces,
        )
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


def _assert_projection_and_negative_scale() -> None:
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.object
    obj.name = "BML_LocalSubdivision_NegativeScale"
    obj.scale = (-1.0, 1.5, 0.75)
    source_counts = (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons))
    line = bpy.data.materials.new("BML_LocalSubdivision_ProjectionLine")
    owned = local_subdivision.ensure(
        obj,
        local_thickness=0.05,
        offset=0.0,
        material=line,
        settings={"line_subdivision": True},
    )
    assert owned is not None

    camera = bpy.context.scene.camera
    assert camera is not None
    for camera_type in ("PERSP", "ORTHO"):
        camera.data.type = camera_type
        mesh = _evaluated_mesh(obj, realize_instances=True)
        try:
            surface_faces, line_faces = _material_face_counts(mesh, line)
            assert surface_faces == source_counts[2]
            assert line_faces > 0, camera_type
        finally:
            _release_mesh(obj)
    assert source_counts == (
        len(obj.data.vertices),
        len(obj.data.edges),
        len(obj.data.polygons),
    )


def _assert_offset_stays_on_projected_outline() -> None:
    camera = bpy.context.scene.camera
    assert camera is not None
    camera.data.type = "PERSP"
    camera.location = (0.0, -6.0, 0.0)
    camera.rotation_euler = (
        Vector((0.0, 0.0, 0.0)) - camera.location
    ).to_track_quat("-Z", "Y").to_euler()

    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    line = bpy.data.materials.new("BML_LocalSubdivision_OffsetLine")
    local_subdivision.ensure(
        obj,
        local_thickness=0.2,
        offset=0.0,
        material=line,
        settings={"line_subdivision": True},
    )
    base = _evaluated_mesh(obj, realize_instances=True)
    try:
        base_bounds = _generated_projected_bbox(base, obj, camera)
    finally:
        _release_mesh(obj)

    local_subdivision.sync(obj, offset=1.0)
    shifted = _evaluated_mesh(obj, realize_instances=True)
    try:
        shifted_bounds = _generated_projected_bbox(shifted, obj, camera)
    finally:
        _release_mesh(obj)
    for base_axis, shifted_axis in zip(base_bounds, shifted_bounds):
        base_center = sum(base_axis) * 0.5
        shifted_center = sum(shifted_axis) * 0.5
        assert abs(base_center - shifted_center) < 1.0e-5, (
            base_bounds,
            shifted_bounds,
        )
        base_extent = base_axis[1] - base_axis[0]
        shifted_extent = shifted_axis[1] - shifted_axis[0]
        assert abs(base_extent - shifted_extent) < 0.005, (
            base_bounds,
            shifted_bounds,
        )


def _assert_scene_camera_priority() -> None:
    scene_a = bpy.context.scene
    camera_a = scene_a.camera
    assert camera_a is not None
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.object
    scene_b = bpy.data.scenes.new("BML_LocalSubdivision_CameraScene")
    scene_b.collection.objects.link(obj)
    camera_data = bpy.data.cameras.new("BML_LocalSubdivision_CameraBData")
    camera_b = bpy.data.objects.new("BML_LocalSubdivision_CameraB", camera_data)
    scene_b.collection.objects.link(camera_b)
    scene_b.camera = camera_b
    try:
        assert local_subdivision.resolve_camera(obj, scene_a) == camera_a
        assert local_subdivision.resolve_camera(obj, scene_b) == camera_b
    finally:
        bpy.data.scenes.remove(scene_b)
        if camera_b.users == 0:
            bpy.data.objects.remove(camera_b)
        if camera_data.users == 0:
            bpy.data.cameras.remove(camera_data)


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
        assert evaluated_faces == source_faces, (source_faces, evaluated_faces)
        assert elapsed < 5.0, elapsed
    finally:
        evaluated.to_mesh_clear()
    realize_started = time.perf_counter()
    realized = _evaluated_mesh(obj, realize_instances=True)
    realize_elapsed = time.perf_counter() - realize_started
    try:
        surface_faces, line_faces = _material_face_counts(realized, line)
        assert surface_faces == source_faces, (surface_faces, source_faces)
        assert 0 < line_faces < source_faces * 8, (line_faces, source_faces)
        assert realize_elapsed < 5.0, realize_elapsed
    finally:
        _release_mesh(obj)
    print(
        f"BML_LOCAL_SUBDIVISION_PERF source_faces={source_faces} "
        f"evaluated_faces={evaluated_faces} line_faces={line_faces} "
        f"elapsed={elapsed:.6f}s realized={realize_elapsed:.6f}s"
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
            assert len(evaluated.vertices) == len(mesh.vertices)
            assert len(evaluated.edges) == len(mesh.edges)
            assert len(evaluated.polygons) == len(mesh.polygons)
            assert evaluated.attributes.get(local_subdivision.GENERATED_LINE_ATTR) is None
        finally:
            _release_mesh(obj)
        realized = _evaluated_mesh(obj, realize_instances=True)
        try:
            surface_faces, line_faces = _material_face_counts(
                realized, line, allow_no_line=True
            )
            assert surface_faces == len(mesh.polygons)
            assert line_faces < max(1, len(mesh.edges) * 128), (name, line_faces)
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
        _assert_projection_and_negative_scale()
        _clear_scene()
        _assert_offset_stays_on_projected_outline()
        _clear_scene()
        _assert_scene_camera_priority()
        _clear_scene()
        _assert_problem_meshes_stay_unchanged()
        _clear_scene()
        _assert_medium_triangle_mesh_stays_bounded()
        print("BMANGA_LINE_LOCAL_SUBDIVISION_NODE_OK")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
