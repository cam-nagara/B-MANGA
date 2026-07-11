"""Blender 5.1実機: 問題メッシュ自動修復・四角面化の構造と原子性."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _select_only(*objects) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _material(name: str, color) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    return material


def _triangulated_open_cylinder(name: str, segments: int = 10) -> bpy.types.Object:
    vertices = []
    for z_value in (-0.5, 0.5):
        for index in range(segments):
            angle = math.tau * index / segments
            vertices.append((math.cos(angle), math.sin(angle), z_value))
    faces = []
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.extend(
            (
                (index, nxt, nxt + segments),
                (index, nxt + segments, index + segments),
            )
        )
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(_material(f"{name}_A", (0.8, 0.2, 0.1, 1.0)))
    mesh.materials.append(_material(f"{name}_B", (0.1, 0.3, 0.8, 1.0)))
    uv = mesh.uv_layers.new(name="UVMap")
    uv_detail = mesh.uv_layers.new(name="UVDetail")
    color = mesh.color_attributes.new(
        name="SurfaceTint",
        type="BYTE_COLOR",
        domain="CORNER",
    )
    point_color = mesh.color_attributes.new(
        name="PointTint",
        type="FLOAT_COLOR",
        domain="POINT",
    )
    normals = []
    for polygon in mesh.polygons:
        wedge = polygon.index // 2
        polygon.material_index = 0 if wedge < segments // 2 else 1
        polygon.use_smooth = True
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            ring = vertex_index % segments
            seam_offset = 2.0 if wedge == 1 and ring == 1 else 0.0
            uv.data[loop_index].uv = (
                ring / segments + seam_offset,
                mesh.vertices[vertex_index].co.z + 0.5,
            )
            uv_detail.data[loop_index].uv = (
                (ring / segments + seam_offset) * 2.0,
                (mesh.vertices[vertex_index].co.z + 0.5) * 2.0,
            )
            color.data[loop_index].color = (
                (0.8, 0.1, 0.1, 1.0)
                if wedge != 1
                else (0.1, 0.2, 0.9, 1.0)
            )
            normal = mesh.vertices[vertex_index].co.copy()
            normal.z = 0.25 if wedge == 1 else 0.0
            normal.normalize()
            normals.append(tuple(normal))
    for vertex in mesh.vertices:
        point_color.data[vertex.index].color = (
            vertex.co.x * 0.25 + 0.5,
            vertex.co.y * 0.25 + 0.5,
            vertex.co.z * 0.25 + 0.5,
            1.0,
        )
    uv.active_clone = True
    uv.active_render = False
    uv_detail.active_clone = False
    uv_detail.active_render = True
    mesh.uv_layers.active_index = 1
    mesh.color_attributes.active_color_index = 1
    mesh.color_attributes.render_color_index = 0
    mesh.normals_split_custom_set(normals)
    mesh["asset_tag"] = f"{name}_source"
    crease = mesh.attributes.new("crease_edge", "FLOAT", "EDGE")
    for edge in mesh.edges:
        if set(int(index) for index in edge.vertices) == {1, segments + 1}:
            edge.use_seam = True
            edge.use_edge_sharp = True
            crease.data[edge.index].value = 0.75
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _problem_mesh(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, -1.0, 0.0),
            (0.0, 0.0, 1.0),
            (2.0, 0.0, 0.0),
            (2.0, 1.0, 0.0),
        ),
        ((5, 6),),
        (
            (0, 1, 2),
            (1, 0, 3),
            (0, 1, 4),
            (2, 1, 0),
            (0, 0, 1),
        ),
    )
    mesh.materials.append(_material(f"{name}_Material", (0.5, 0.7, 0.3, 1.0)))
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x, co.y)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _concentric_open_cylinders(name: str, segments=12) -> bpy.types.Object:
    vertices = []
    faces = []
    for radius in (1.0, 0.45):
        offset = len(vertices)
        for z_value in (-0.5, 0.5):
            for index in range(segments):
                angle = math.tau * index / segments
                vertices.append(
                    (radius * math.cos(angle), radius * math.sin(angle), z_value)
                )
        for index in range(segments):
            following = (index + 1) % segments
            faces.extend(
                (
                    (offset + index, offset + following, offset + following + segments),
                    (offset + index, offset + following + segments, offset + index + segments),
                )
            )
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _closed_nonmanifold_mesh(name: str, *, with_uv=False) -> bpy.types.Object:
    vertices = [
        (0.0, 0.0, -1.0),
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (-1.0, 0.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.8, 0.8, 0.0),
        (-0.8, 0.8, 0.0),
        (3.0, 0.0, 0.0),
        (3.0, 1.0, 0.0),
    ]
    faces = []
    for first, second in ((2, 3), (4, 5), (6, 7)):
        faces.extend(
            (
                (0, first, 1),
                (0, 1, second),
                (0, second, first),
                (1, first, second),
            )
        )
    faces.extend(((0, 2, 1), (0, 0, 1)))
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, ((8, 9),), faces)
    mesh.materials.append(_material(f"{name}_Material", (0.5, 0.7, 0.3, 1.0)))
    if with_uv:
        uv = mesh.uv_layers.new(name="UVMap")
        for loop in mesh.loops:
            co = mesh.vertices[loop.vertex_index].co
            uv.data[loop.index].uv = (co.x * 0.25 + 0.5, co.y * 0.25 + 0.5)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _conflicting_duplicate_mesh(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        [],
        ((0, 1, 2), (0, 1, 2)),
    )
    mesh.materials.append(_material(f"{name}_A", (0.8, 0.1, 0.1, 1.0)))
    mesh.materials.append(_material(f"{name}_B", (0.1, 0.1, 0.8, 1.0)))
    mesh.polygons[0].material_index = 0
    mesh.polygons[1].material_index = 1
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _mixed_duplicate_surface(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        (
            (-1.0, -1.0, 0.0),
            (1.0, -1.0, 0.0),
            (1.0, 1.0, 0.0),
            (-1.0, 1.0, 0.0),
        ),
        [],
        ((0, 1, 2, 3), (0, 1, 2), (0, 2, 3)),
    )
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _collinear_ngon(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (3.0, 1.0, 0.0),
            (2.0, 2.0, 0.0),
            (1.0, 2.0, 0.0),
            (0.0, 1.0, 0.0),
        ),
        [],
        ((0, 1, 2, 3, 4, 5, 6),),
    )
    mesh.materials.append(_material(f"{name}_Material", (0.3, 0.6, 0.8, 1.0)))
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        coordinate = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (coordinate.x / 3.0, coordinate.y / 2.0)
    mesh.normals_split_custom_set([(0.0, 0.0, 1.0)] * len(mesh.loops))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _plain_open_quad(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)),
        [],
        ((0, 1, 2, 3),),
    )
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _attributed_open_quad(name: str) -> bpy.types.Object:
    obj = _plain_open_quad(name)
    mesh = obj.data
    mesh.materials.append(_material(f"{name}_Material", (0.2, 0.7, 0.4, 1.0)))
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        coordinate = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (
            coordinate.x * 0.5 + 0.5,
            coordinate.y * 0.5 + 0.5,
        )
    mesh.normals_split_custom_set([(0.0, 0.0, 1.0)] * len(mesh.loops))
    return obj


def _closed_plain_quad_cube(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        (
            (-1.0, -1.0, -1.0),
            (1.0, -1.0, -1.0),
            (1.0, 1.0, -1.0),
            (-1.0, 1.0, -1.0),
            (-1.0, -1.0, 1.0),
            (1.0, -1.0, 1.0),
            (1.0, 1.0, 1.0),
            (-1.0, 1.0, 1.0),
        ),
        [],
        (
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ),
    )
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _closed_triangle_sphere(name: str) -> bpy.types.Object:
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = f"{name}_Mesh"
    mesh = obj.data
    mesh.materials.append(_material(f"{name}_A", (0.7, 0.2, 0.15, 1.0)))
    mesh.materials.append(_material(f"{name}_B", (0.1, 0.55, 0.75, 1.0)))
    uv = mesh.uv_layers.new(name="UVMap")
    color = mesh.color_attributes.new(
        name="SurfaceTint", type="FLOAT_COLOR", domain="POINT"
    )
    normals = []
    for vertex in mesh.vertices:
        color.data[vertex.index].color = (
            abs(vertex.co.x), abs(vertex.co.y), abs(vertex.co.z), 1.0
        )
    for polygon in mesh.polygons:
        polygon.material_index = polygon.index % 2
        polygon.use_smooth = True
        for loop_index in polygon.loop_indices:
            coordinate = mesh.vertices[mesh.loops[loop_index].vertex_index].co.normalized()
            uv.data[loop_index].uv = (
                math.atan2(coordinate.y, coordinate.x) / math.tau + 0.5,
                math.acos(max(-1.0, min(1.0, coordinate.z))) / math.pi,
            )
            normals.append(tuple(coordinate))
    mesh.normals_split_custom_set(normals)
    mesh.update()
    return obj


def _closed_plain_triangle_sphere(name: str) -> bpy.types.Object:
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0)
    obj = bpy.context.object
    obj.name = name
    generated = obj.data
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        [tuple(vertex.co) for vertex in generated.vertices],
        [],
        [tuple(polygon.vertices) for polygon in generated.polygons],
    )
    obj.data = mesh
    bpy.data.meshes.remove(generated)
    return obj


def _edge_usage(mesh) -> tuple[int, int, int]:
    counts = {tuple(sorted(edge.vertices)): 0 for edge in mesh.edges}
    for polygon in mesh.polygons:
        for pair in polygon.edge_keys:
            key = tuple(sorted(pair))
            counts[key] = counts.get(key, 0) + 1
    return (
        sum(value == 1 for value in counts.values()),
        sum(value > 2 for value in counts.values()),
        sum(value == 0 for value in counts.values()),
    )


def _boundary_component_count(mesh) -> int:
    counts = {tuple(sorted(edge.vertices)): 0 for edge in mesh.edges}
    for polygon in mesh.polygons:
        for pair in polygon.edge_keys:
            key = tuple(sorted(pair))
            counts[key] = counts.get(key, 0) + 1
    adjacency = {}
    for (first, second), count in counts.items():
        if count != 1:
            continue
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    remaining = set(adjacency)
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            for neighbor in adjacency.get(stack.pop(), ()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
    return components


def _assert_quad_mesh(obj) -> None:
    assert obj.data.polygons
    assert all(len(polygon.vertices) == 4 for polygon in obj.data.polygons)
    _boundary, non_manifold, loose = _edge_usage(obj.data)
    assert non_manifold == 0 and loose == 0


def _run_expected_cancel() -> None:
    try:
        result = bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT")
    except RuntimeError:
        return
    assert result == {"CANCELLED"}


def _run_optimizer_expected_cancel() -> None:
    try:
        result = bpy.ops.bmanga_line.optimize_purchased_mesh("EXEC_DEFAULT")
    except RuntimeError:
        return
    assert result == {"CANCELLED"}


def _assert_direct_repair(mod) -> None:
    obj = _triangulated_open_cylinder("DirectQuad")
    old_mesh = obj.data
    old_name = old_mesh.name
    obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
    _select_only(obj)
    result = bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT")
    assert result == {"FINISHED"}
    assert obj.data != old_mesh
    assert old_name not in bpy.data.meshes
    _assert_quad_mesh(obj)
    assert len(obj.data.materials) == 2
    assert {polygon.material_index for polygon in obj.data.polygons} == {0, 1}
    assert {layer.name for layer in obj.data.uv_layers} == {"UVMap", "UVDetail"}
    for layer in obj.data.uv_layers:
        values = [component for item in layer.data for component in item.uv]
        assert values and all(math.isfinite(value) for value in values)
        assert max(values) - min(values) > 0.25
    seam_values: dict[int, list[float]] = {}
    for loop in obj.data.loops:
        seam_values.setdefault(loop.vertex_index, []).append(
            obj.data.uv_layers["UVMap"].data[loop.index].uv.x
        )
    assert any(max(values) - min(values) > 1.0 for values in seam_values.values())
    assert obj.data.color_attributes.get("SurfaceTint") is not None
    colors = obj.data.color_attributes["SurfaceTint"].data
    assert colors and max(item.color[0] for item in colors) > 0.2
    color_values: dict[int, list[tuple[float, ...]]] = {}
    for loop in obj.data.loops:
        color_values.setdefault(loop.vertex_index, []).append(
            tuple(colors[loop.index].color)
        )
    assert any(
        max(item[2] for item in values) - min(item[2] for item in values) > 0.3
        for values in color_values.values()
    )
    assert obj.data.has_custom_normals
    normal_values: dict[int, list[float]] = {}
    for loop in obj.data.loops:
        normal_values.setdefault(loop.vertex_index, []).append(
            obj.data.corner_normals[loop.index].vector.z
        )
    assert any(max(values) - min(values) > 0.05 for values in normal_values.values())
    assert obj.data.get("asset_tag") == "DirectQuad_source"
    assert any(edge.use_seam for edge in obj.data.edges)
    assert any(edge.use_edge_sharp for edge in obj.data.edges)
    crease = obj.data.attributes.get("crease_edge")
    assert crease is not None and max(item.value for item in crease.data) >= 0.75
    assert obj.data.use_auto_texspace is False
    assert obj.get(mod.OPTIMIZED_PROP) is True
    assert obj.get(mod.OPTIMIZED_QUALITY_PROP) == "QUAD_STANDARD"
    assert not obj.bmanga_line_settings.auto_subdivision_for_midpoint
    assert "通常 1件" in bpy.context.scene.bmanga_line_quad_repair_result
    quad_mesh = obj.data
    _run_optimizer_expected_cancel()
    assert obj.data == quad_mesh
    assert "四角面化済み" in bpy.context.scene.bmanga_line_mesh_optimize_error
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_close_density(mod) -> None:
    obj = _triangulated_open_cylinder("CloseQuad")
    _select_only(obj)
    bpy.context.scene.bmanga_line_quad_repair_quality = "CLOSE"
    try:
        assert bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT") == {"FINISHED"}
    finally:
        bpy.context.scene.bmanga_line_quad_repair_quality = "STANDARD"
    _assert_quad_mesh(obj)
    assert len(obj.data.polygons) >= 40
    assert obj.get(mod.OPTIMIZED_QUALITY_PROP) == "QUAD_CLOSE"
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_concentric_boundaries() -> None:
    obj = _concentric_open_cylinders("ConcentricOpenQuad")
    assert _boundary_component_count(obj.data) == 4
    _select_only(obj)
    assert bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT") == {"FINISHED"}
    _assert_quad_mesh(obj)
    assert _boundary_component_count(obj.data) == 4
    boundary_vertices = set()
    counts = {tuple(sorted(edge.vertices)): 0 for edge in obj.data.edges}
    for polygon in obj.data.polygons:
        for pair in polygon.edge_keys:
            key = tuple(sorted(pair))
            counts[key] = counts.get(key, 0) + 1
    for edge, count in counts.items():
        if count == 1:
            boundary_vertices.update(edge)
    radii = [
        math.hypot(obj.data.vertices[index].co.x, obj.data.vertices[index].co.y)
        for index in boundary_vertices
    ]
    assert min(radii) < 0.5 and max(radii) > 0.95
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_strong_repair() -> bpy.types.Object:
    obj = _closed_nonmanifold_mesh("StrongQuad")
    _select_only(obj)
    result = bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT")
    assert result == {"FINISHED"}
    _assert_quad_mesh(obj)
    assert len(obj.data.materials) == 1
    assert "強力修復 1件" in bpy.context.scene.bmanga_line_quad_repair_result
    return obj


def _assert_attribute_nonmanifold_rejection() -> None:
    obj = _closed_nonmanifold_mesh("TexturedNonManifold", with_uv=True)
    original = obj.data
    _select_only(obj)
    _run_expected_cancel()
    assert obj.data == original
    assert "UV・素材・法線" in bpy.context.scene.bmanga_line_quad_repair_error
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_conflicting_duplicate_rejection() -> None:
    obj = _conflicting_duplicate_mesh("ConflictingDuplicate")
    original = obj.data
    _select_only(obj)
    _run_expected_cancel()
    assert obj.data == original
    assert "重複面" in bpy.context.scene.bmanga_line_quad_repair_error
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_open_nonmanifold_rejection() -> None:
    obj = _problem_mesh("OpenNonManifold")
    original = obj.data
    original_faces = len(original.polygons)
    _select_only(obj)
    _run_expected_cancel()
    assert obj.data == original and len(obj.data.polygons) == original_faces
    assert "開口部" in bpy.context.scene.bmanga_line_quad_repair_error
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_mixed_duplicate_rejection() -> None:
    obj = _mixed_duplicate_surface("MixedDuplicate")
    original = obj.data
    _select_only(obj)
    _run_expected_cancel()
    assert obj.data == original
    assert "重複面" in bpy.context.scene.bmanga_line_quad_repair_error
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_locked_rejection() -> None:
    obj = _triangulated_open_cylinder("LockedQuad")
    obj.bmanga_line_settings.settings_locked = True
    original = obj.data
    _select_only(obj)
    _run_expected_cancel()
    assert obj.data == original
    assert "ロック" in bpy.context.scene.bmanga_line_quad_repair_error
    _run_optimizer_expected_cancel()
    assert obj.data == original
    assert "ロック" in bpy.context.scene.bmanga_line_mesh_optimize_error
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_closed_reconstruction() -> None:
    obj = _closed_triangle_sphere("ClosedQuad")
    _select_only(obj)
    result = bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT")
    assert result == {"FINISHED"}
    _assert_quad_mesh(obj)
    boundary, _non_manifold, _loose = _edge_usage(obj.data)
    assert boundary == 0
    assert len(obj.data.materials) == 2
    assert {polygon.material_index for polygon in obj.data.polygons} == {0, 1}
    assert obj.data.uv_layers.get("UVMap") is not None
    assert obj.data.color_attributes.get("SurfaceTint") is not None
    assert obj.data.has_custom_normals
    assert "強力修復 0件" in bpy.context.scene.bmanga_line_quad_repair_result
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_collinear_ngon_repair(geometry) -> None:
    obj = _collinear_ngon("CollinearNgon")
    stats = geometry.QuadRepairStats()
    cleaned, boundary, non_manifold = geometry._sanitize_candidate(obj.data, stats)
    try:
        assert len(cleaned.polygons) == 1
        assert len(cleaned.polygons[0].vertices) == 7
        assert boundary == 7 and non_manifold == 0
    finally:
        bpy.data.meshes.remove(cleaned)
    _select_only(obj)
    assert bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT") == {"FINISHED"}
    _assert_quad_mesh(obj)
    assert len(obj.data.polygons) == 7
    assert min(polygon.area for polygon in obj.data.polygons) > 1.0e-6
    assert obj.data.uv_layers.get("UVMap") is not None
    assert obj.data.has_custom_normals
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_plain_open_quad_repair() -> None:
    obj = _plain_open_quad("PlainOpenQuad")
    _select_only(obj)
    assert bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT") == {"FINISHED"}
    _assert_quad_mesh(obj)
    assert len(obj.data.polygons) == 1
    assert len(obj.data.uv_layers) == 0
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_attributed_open_quad_preserved(geometry) -> None:
    obj = _attributed_open_quad("AttributedOpenQuad")
    candidate = geometry.build_candidate(
        bpy.context,
        obj,
        geometry.options_for_quality("STANDARD"),
    )
    try:
        assert candidate.stats.used_local_quadrangulation
        assert candidate.stats.output_faces == 1
        assert candidate.mesh.uv_layers.get("UVMap") is not None
        assert candidate.mesh.has_custom_normals
    finally:
        bpy.data.meshes.remove(candidate.mesh)
    close_candidate = geometry.build_candidate(
        bpy.context,
        obj,
        geometry.options_for_quality("CLOSE"),
    )
    try:
        assert close_candidate.stats.output_faces == 16
        assert close_candidate.mesh.uv_layers.get("UVMap") is not None
        assert close_candidate.mesh.has_custom_normals
    finally:
        bpy.data.meshes.remove(close_candidate.mesh)
        bpy.data.objects.remove(obj, do_unlink=True)


def _assert_closed_plain_quads_preserved(geometry) -> None:
    obj = _closed_plain_quad_cube("ClosedPlainQuad")
    candidate = geometry.build_candidate(
        bpy.context,
        obj,
        geometry.options_for_quality("STANDARD"),
    )
    try:
        assert candidate.stats.used_local_quadrangulation
        assert not candidate.stats.used_voxel_repair
        assert candidate.stats.output_faces == 6
        assert all(len(polygon.vertices) == 4 for polygon in candidate.mesh.polygons)
    finally:
        bpy.data.meshes.remove(candidate.mesh)
        bpy.data.objects.remove(obj, do_unlink=True)


def _assert_attribute_selection_matches(source, target) -> None:
    assert target.uv_layers.active.name == source.uv_layers.active.name
    assert [layer.name for layer in target.uv_layers if layer.active_clone] == [
        layer.name for layer in source.uv_layers if layer.active_clone
    ]
    assert [layer.name for layer in target.uv_layers if layer.active_render] == [
        layer.name for layer in source.uv_layers if layer.active_render
    ]
    assert (
        target.color_attributes.active_color.name
        == source.color_attributes.active_color.name
    )
    assert target.color_attributes[target.color_attributes.render_color_index].name == (
        source.color_attributes[source.color_attributes.render_color_index].name
    )


def _assert_direct_transfer_matches_generic(geometry, transfer) -> None:
    obj = _triangulated_open_cylinder("TransferParity")
    stats = geometry.QuadRepairStats()
    cleaned, boundary, _non_manifold = geometry._sanitize_candidate(obj.data, stats)
    _assert_attribute_selection_matches(obj.data, cleaned)
    options = geometry.options_for_quality("STANDARD")
    fast = geometry._run_local_quadrangulation(
        bpy.context,
        obj,
        cleaned,
        options,
        allow_triangle_join=False,
        max_attribute_loops=geometry._ATTRIBUTE_LOOP_LIMIT,
    )
    generic = geometry._run_local_quadrangulation(
        bpy.context,
        obj,
        cleaned,
        options,
        allow_triangle_join=False,
        max_attribute_loops=geometry._ATTRIBUTE_LOOP_LIMIT,
    )
    try:
        transfer.transfer_local_triangle_data(cleaned, fast, bool(boundary))
        transfer.transfer_surface_data(cleaned, generic, bool(boundary))
        _assert_attribute_selection_matches(cleaned, fast)
        _assert_attribute_selection_matches(cleaned, generic)
        assert [p.material_index for p in fast.polygons] == [
            p.material_index for p in generic.polygons
        ]
        assert len(fast.vertices) == len(generic.vertices)
        assert max(
            (first.co - second.co).length
            for first, second in zip(fast.vertices, generic.vertices, strict=True)
        ) < 1.0e-5
        for layer_name in ("UVMap", "UVDetail"):
            fast_uv = fast.uv_layers[layer_name].data
            generic_uv = generic.uv_layers[layer_name].data
            assert max(
                (first.uv - second.uv).length
                for first, second in zip(fast_uv, generic_uv, strict=True)
            ) < 1.0e-4
        for layer_name in ("SurfaceTint", "PointTint"):
            fast_colors = fast.color_attributes[layer_name].data
            generic_colors = generic.color_attributes[layer_name].data
            assert max(
                max(abs(a - b) for a, b in zip(first.color, second.color, strict=True))
                for first, second in zip(fast_colors, generic_colors, strict=True)
            ) < 1.0e-4
        assert min(
            first.vector.dot(second.vector)
            for first, second in zip(
                fast.corner_normals,
                generic.corner_normals,
                strict=True,
            )
        ) > 0.999
    finally:
        bpy.data.meshes.remove(fast)
        bpy.data.meshes.remove(generic)
        bpy.data.meshes.remove(cleaned)
        bpy.data.objects.remove(obj, do_unlink=True)


def _assert_plain_reconstruction(geometry) -> None:
    obj = _closed_plain_triangle_sphere("PlainClosedQuad")
    candidate = geometry.build_candidate(
        bpy.context,
        obj,
        geometry.options_for_quality("STANDARD"),
    )
    assert not candidate.stats.used_local_quadrangulation
    assert not candidate.stats.used_voxel_repair
    assert all(len(polygon.vertices) == 4 for polygon in candidate.mesh.polygons)
    bpy.data.meshes.remove(candidate.mesh)
    _select_only(obj)
    assert bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT") == {"FINISHED"}
    _assert_quad_mesh(obj)
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_manual_crease_reconstruction(geometry) -> None:
    obj = _closed_plain_triangle_sphere("ManualCreaseQuad")
    edge_crease = obj.data.attributes.new("crease_edge", "FLOAT", "EDGE")
    edge_crease.data[0].value = 0.8
    obj.data.edges[0].use_edge_sharp = True
    vertex_crease = obj.data.attributes.new("crease_vert", "FLOAT", "POINT")
    vertex_crease.data[0].value = 0.6
    candidate = geometry.build_candidate(
        bpy.context,
        obj,
        geometry.options_for_quality("STANDARD"),
    )
    assert candidate.stats.used_local_quadrangulation
    output_edge_crease = candidate.mesh.attributes.get("crease_edge")
    output_vertex_crease = candidate.mesh.attributes.get("crease_vert")
    assert output_edge_crease is not None
    assert max(item.value for item in output_edge_crease.data) >= 0.8
    assert output_vertex_crease is not None
    assert max(item.value for item in output_vertex_crease.data) >= 0.6
    bpy.data.meshes.remove(candidate.mesh)
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_atomic_rejection() -> None:
    good = _triangulated_open_cylinder("AtomicQuadGood")
    bad = _triangulated_open_cylinder("AtomicQuadBad")
    basis = bad.shape_key_add(name="Basis")
    assert basis is not None
    good_mesh = good.data
    bad_mesh = bad.data
    _select_only(good, bad)
    _run_expected_cancel()
    assert good.data == good_mesh and bad.data == bad_mesh
    assert "シェイプキー" in bpy.context.scene.bmanga_line_quad_repair_error
    assert not any("BML_QuadCandidate" in mesh.name for mesh in bpy.data.meshes)
    bpy.data.objects.remove(good, do_unlink=True)
    bpy.data.objects.remove(bad, do_unlink=True)


def _assert_roundtrip(obj) -> None:
    snapshot = (
        len(obj.data.vertices),
        len(obj.data.polygons),
        tuple(layer.name for layer in obj.data.uv_layers),
        tuple(material.name for material in obj.data.materials),
        tuple(len(polygon.vertices) for polygon in obj.data.polygons),
    )
    with tempfile.TemporaryDirectory(prefix="bml_quad_repair_") as temp_dir:
        path = str(Path(temp_dir) / "quad_repair.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        loaded = bpy.data.objects["StrongQuad"]
        restored = (
            len(loaded.data.vertices),
            len(loaded.data.polygons),
            tuple(layer.name for layer in loaded.data.uv_layers),
            tuple(material.name for material in loaded.data.materials),
            tuple(len(polygon.vertices) for polygon in loaded.data.polygons),
        )
        assert restored == snapshot


def main() -> None:
    with temporary_line_preset_store():
        import b_manga_line as addon

        addon.register()
        try:
            _clear_scene()
            assert hasattr(bpy.types.Scene, "bmanga_line_quad_repair_quality")
            assert getattr(bpy.types, "BMANGA_LINE_PT_auto_repair_quad_mesh", None)
            _assert_direct_repair(addon.mesh_optimizer)
            _assert_close_density(addon.mesh_optimizer)
            _assert_concentric_boundaries()
            _assert_closed_reconstruction()
            _assert_collinear_ngon_repair(addon.mesh_quad_repair_geometry)
            _assert_plain_open_quad_repair()
            _assert_attributed_open_quad_preserved(addon.mesh_quad_repair_geometry)
            _assert_closed_plain_quads_preserved(addon.mesh_quad_repair_geometry)
            _assert_direct_transfer_matches_generic(
                addon.mesh_quad_repair_geometry,
                addon.mesh_quad_repair_transfer,
            )
            _assert_plain_reconstruction(addon.mesh_quad_repair_geometry)
            _assert_manual_crease_reconstruction(addon.mesh_quad_repair_geometry)
            strong = _assert_strong_repair()
            _assert_attribute_nonmanifold_rejection()
            _assert_conflicting_duplicate_rejection()
            _assert_mixed_duplicate_rejection()
            _assert_open_nonmanifold_rejection()
            _assert_locked_rejection()
            _assert_atomic_rejection()
            _assert_roundtrip(strong)
        finally:
            addon.unregister()
    print("B-MANGA Liner auto quad repair check: PASS")


if __name__ == "__main__":
    main()
