"""問題メッシュをCatmull-Clark向け四角面候補へ再構成する."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .mesh_quad_repair_transfer import (
    SurfaceTransferError,
    copy_color_attribute_selection,
    mark_catmull_features,
    transfer_local_triangle_data,
    transfer_surface_data,
)


class QuadRepairError(RuntimeError):
    """安全に四角面候補を確定できない場合に送出する."""


@dataclass(frozen=True)
class QuadRepairOptions:
    quality: str = "STANDARD"
    max_output_faces: int = 250_000
    max_deviation_ratio: float = 0.12
    catmull_level: int = 1


@dataclass
class QuadRepairStats:
    source_vertices: int = 0
    source_faces: int = 0
    output_vertices: int = 0
    output_faces: int = 0
    welded_vertices: int = 0
    removed_faces: int = 0
    removed_loose_elements: int = 0
    used_local_quadrangulation: bool = False
    used_voxel_repair: bool = False
    max_deviation_ratio: float = 0.0


@dataclass
class QuadRepairCandidate:
    mesh: bpy.types.Mesh
    stats: QuadRepairStats


_STRUCTURAL_ATTRIBUTES = {
    "position",
    ".edge_verts",
    ".corner_vert",
    ".corner_edge",
    "sharp_face",
    "sharp_edge",
    "custom_normal",
    "material_index",
    "uv_seam",
    "crease_edge",
    "crease_vert",
}
_ATTRIBUTE_LOOP_LIMIT = 1_000_000
_CATMULL_INPUT_FACE_LIMIT = 250_000


def options_for_quality(quality: str) -> QuadRepairOptions:
    if quality == "CLOSE":
        return QuadRepairOptions(
            quality="CLOSE",
            max_output_faces=250_000,
            max_deviation_ratio=0.07,
            catmull_level=1,
        )
    return QuadRepairOptions()


def validate_source_object(obj: bpy.types.Object) -> None:
    if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
        raise QuadRepairError("面を持つメッシュではありません")
    if any(
        not math.isfinite(value)
        for vertex in obj.data.vertices
        for value in vertex.co
    ):
        raise QuadRepairError("有限でない頂点座標が含まれています")
    if obj.library is not None or obj.data.library is not None:
        raise QuadRepairError("リンク素材は直接変更できません")
    if getattr(obj, "override_library", None) is not None:
        raise QuadRepairError("ライブラリオーバーライドは直接変更できません")
    if obj.data.shape_keys is not None:
        raise QuadRepairError("シェイプキー付きメッシュには対応していません")
    if obj.vertex_groups:
        raise QuadRepairError("変形用頂点グループ付きメッシュには対応していません")
    if obj.modifiers:
        raise QuadRepairError("モディファイアを適用または削除してから実行してください")
    uv_names = {layer.name for layer in obj.data.uv_layers}
    color_names = {attribute.name for attribute in obj.data.color_attributes}
    allowed = _STRUCTURAL_ATTRIBUTES | uv_names | color_names
    unsupported = sorted(
        attribute.name
        for attribute in obj.data.attributes
        if not attribute.name.startswith(".") and attribute.name not in allowed
    )
    if unsupported:
        raise QuadRepairError(f"未対応のメッシュ属性があります: {', '.join(unsupported[:3])}")


def _mesh_diagonal(mesh: bpy.types.Mesh) -> float:
    if not mesh.vertices:
        return 0.0
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    for vertex in mesh.vertices:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], vertex.co[axis])
            maximum[axis] = max(maximum[axis], vertex.co[axis])
    return (maximum - minimum).length


def _coordinate_key(co: Vector, distance: float) -> tuple[int, int, int]:
    return tuple(int(round(float(value) / distance)) for value in co)


def _face_area(positions, face) -> float:
    area_vector = Vector((0.0, 0.0, 0.0))
    origin = positions[face[0]]
    for index, vertex in enumerate(face):
        following = face[(index + 1) % len(face)]
        current_offset = positions[vertex] - origin
        following_offset = positions[following] - origin
        area_vector += current_offset.cross(following_offset)
    return area_vector.length * 0.5


def _orient_face_data(faces, loop_sources) -> bool:
    occurrences: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for face_index, face in enumerate(faces):
        for index in range(len(face)):
            v0 = face[index]
            v1 = face[(index + 1) % len(face)]
            pair = tuple(sorted((v0, v1)))
            occurrences.setdefault(pair, []).append((face_index, v0 < v1))
    if any(len(items) > 2 for items in occurrences.values()):
        return False
    adjacency: dict[int, list[tuple[int, bool]]] = {}
    for items in occurrences.values():
        if len(items) != 2:
            continue
        (face_a, direction_a), (face_b, direction_b) = items
        toggle = direction_a == direction_b
        adjacency.setdefault(face_a, []).append((face_b, toggle))
        adjacency.setdefault(face_b, []).append((face_a, toggle))
    flips: dict[int, bool] = {}
    for start in range(len(faces)):
        if start in flips:
            continue
        flips[start] = False
        queue = deque((start,))
        while queue:
            current = queue.popleft()
            for neighbor, toggle in adjacency.get(current, ()):
                expected = flips[current] ^ toggle
                if neighbor in flips:
                    if flips[neighbor] != expected:
                        return False
                    continue
                flips[neighbor] = expected
                queue.append(neighbor)
    for index, should_flip in flips.items():
        if should_flip:
            faces[index] = tuple(reversed(faces[index]))
            loop_sources[index] = tuple(reversed(loop_sources[index]))
    return True


def _copy_sanitized_attributes(
    source,
    target,
    polygon_sources,
    loop_sources,
    output_source_vertices,
) -> None:
    for material in source.materials:
        target.materials.append(material)
    flat_loop_sources = [loop for face in loop_sources for loop in face]
    for polygon, source_index in zip(target.polygons, polygon_sources, strict=True):
        source_polygon = source.polygons[source_index]
        polygon.material_index = int(source_polygon.material_index)
        polygon.use_smooth = bool(source_polygon.use_smooth)
    for source_layer in source.uv_layers:
        target_layer = target.uv_layers.new(name=source_layer.name, do_init=False)
        for item, loop_index in zip(target_layer.data, flat_loop_sources, strict=True):
            item.uv = source_layer.data[loop_index].uv
        target_layer.active_clone = bool(source_layer.active_clone)
        target_layer.active_render = bool(source_layer.active_render)
    if source.uv_layers:
        target.uv_layers.active_index = min(
            int(source.uv_layers.active_index),
            len(target.uv_layers) - 1,
        )
    for source_attr in source.color_attributes:
        target_attr = target.color_attributes.new(
            name=source_attr.name,
            type=source_attr.data_type,
            domain=source_attr.domain,
        )
        source_indices = (
            flat_loop_sources
            if source_attr.domain == "CORNER"
            else output_source_vertices
        )
        for item, source_index in zip(target_attr.data, source_indices, strict=True):
            item.color = source_attr.data[source_index].color
    copy_color_attribute_selection(source, target)
    if source.has_custom_normals:
        normals = [
            tuple(source.corner_normals[loop_index].vector)
            for loop_index in flat_loop_sources
        ]
        target.normals_split_custom_set(normals)
    for key in source.keys():
        if key == "_RNA_UI":
            continue
        try:
            target[key] = source[key]
        except (TypeError, ValueError):
            continue


def _copy_sanitized_edge_data(source, target, source_to_output) -> None:
    target_edges = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): edge
        for edge in target.edges
    }
    source_crease = source.attributes.get("crease_edge")
    target_crease = None
    if source_crease is not None and source_crease.domain == "EDGE":
        target_crease = target.attributes.new("crease_edge", "FLOAT", "EDGE")
    for source_edge in source.edges:
        v0 = source_to_output.get(int(source_edge.vertices[0]))
        v1 = source_to_output.get(int(source_edge.vertices[1]))
        if v0 is None or v1 is None or v0 == v1:
            continue
        target_edge = target_edges.get(tuple(sorted((v0, v1))))
        if target_edge is None:
            continue
        target_edge.use_seam = target_edge.use_seam or bool(source_edge.use_seam)
        target_edge.use_edge_sharp = (
            target_edge.use_edge_sharp or bool(source_edge.use_edge_sharp)
        )
        if target_crease is not None:
            target_crease.data[target_edge.index].value = max(
                target_crease.data[target_edge.index].value,
                source_crease.data[source_edge.index].value,
            )
    source_vertex_crease = source.attributes.get("crease_vert")
    if source_vertex_crease is None or source_vertex_crease.domain != "POINT":
        return
    target_vertex_crease = target.attributes.new("crease_vert", "FLOAT", "POINT")
    for source_index, target_index in source_to_output.items():
        target_vertex_crease.data[target_index].value = max(
            target_vertex_crease.data[target_index].value,
            source_vertex_crease.data[source_index].value,
        )


def _rounded_values(values):
    return tuple(round(float(value), 10) for value in values)


def _face_display_signature(source, face, loop_indices, polygon_index):
    polygon = source.polygons[polygon_index]
    corners = []
    for output_vertex, loop_index in zip(face, loop_indices, strict=True):
        source_vertex = int(source.loops[loop_index].vertex_index)
        values = []
        for layer in source.uv_layers:
            values.append(("UV", layer.name, _rounded_values(layer.data[loop_index].uv)))
        for attribute in source.color_attributes:
            item_index = loop_index if attribute.domain == "CORNER" else source_vertex
            values.append(
                (
                    "COLOR",
                    attribute.name,
                    _rounded_values(attribute.data[item_index].color),
                )
            )
        if source.has_custom_normals:
            values.append(
                (
                    "NORMAL",
                    _rounded_values(source.corner_normals[loop_index].vector),
                )
            )
        corners.append((int(output_vertex), tuple(values)))
    return (
        int(polygon.material_index),
        bool(polygon.use_smooth),
        tuple(sorted(corners)),
    )


def _reject_overlapping_retained_faces(
    source,
    positions,
    source_to_output,
    retained_polygon_indices,
    area_epsilon,
) -> None:
    source.calc_loop_triangles()
    retained = set(retained_polygon_indices)
    seen: dict[tuple[int, int, int], tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
    for triangle in source.loop_triangles:
        polygon_index = int(triangle.polygon_index)
        if polygon_index not in retained:
            continue
        face = tuple(source_to_output[int(index)] for index in triangle.vertices)
        if len(set(face)) != 3 or _face_area(positions, face) <= area_epsilon:
            continue
        loops = tuple(int(index) for index in triangle.loops)
        key = tuple(sorted(face))
        previous = seen.get(key)
        if previous is None:
            seen[key] = (polygon_index, face, loops)
            continue
        previous_polygon, previous_face, previous_loops = previous
        if previous_polygon == polygon_index:
            continue
        previous_signature = _face_display_signature(
            source,
            previous_face,
            previous_loops,
            previous_polygon,
        )
        signature = _face_display_signature(source, face, loops, polygon_index)
        if previous_signature != signature:
            raise QuadRepairError(
                "重複面に異なるUV・素材・法線があり、残す面を決められません"
            )
        raise QuadRepairError(
            "重複面が異なる面構成にまたがり、残す面を決められません"
        )


def _sanitize_candidate(source, stats: QuadRepairStats) -> tuple[bpy.types.Mesh, int, int]:
    diagonal = max(_mesh_diagonal(source), 1.0e-9)
    weld_distance = max(1.0e-9, diagonal * 1.0e-8)
    area_epsilon = max(1.0e-16, diagonal * diagonal * 1.0e-14)
    positions: list[Vector] = []
    output_source_vertices: list[int] = []
    source_to_output: dict[int, int] = {}
    coordinate_to_output: dict[tuple[int, int, int], int] = {}
    for vertex in source.vertices:
        key = _coordinate_key(vertex.co, weld_distance)
        output_index = coordinate_to_output.get(key)
        if output_index is None:
            output_index = len(positions)
            coordinate_to_output[key] = output_index
            positions.append(vertex.co.copy())
            output_source_vertices.append(vertex.index)
        source_to_output[vertex.index] = output_index
    stats.welded_vertices = len(source.vertices) - len(positions)

    faces: list[tuple[int, ...]] = []
    polygon_sources: list[int] = []
    loop_sources: list[tuple[int, ...]] = []
    seen: dict[tuple[int, ...], tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
    for polygon in source.polygons:
        mapped = [source_to_output[int(index)] for index in polygon.vertices]
        source_loops = [int(index) for index in polygon.loop_indices]
        face = []
        face_loops = []
        for vertex, loop_index in zip(mapped, source_loops, strict=True):
            if face and vertex == face[-1]:
                continue
            face.append(vertex)
            face_loops.append(loop_index)
        if len(face) > 1 and face[0] == face[-1]:
            face.pop()
            face_loops.pop()
        if len(face) < 3 or len(set(face)) != len(face):
            stats.removed_faces += 1
            continue
        face_tuple = tuple(face)
        loop_tuple = tuple(face_loops)
        if _face_area(positions, face_tuple) <= area_epsilon:
            stats.removed_faces += 1
            continue
        key = tuple(sorted(face_tuple))
        previous = seen.get(key)
        if previous is not None:
            previous_polygon, previous_face, previous_loops = previous
            previous_signature = _face_display_signature(
                source,
                previous_face,
                previous_loops,
                previous_polygon,
            )
            signature = _face_display_signature(
                source,
                face_tuple,
                loop_tuple,
                int(polygon.index),
            )
            if previous_signature != signature:
                raise QuadRepairError(
                    "重複面に異なるUV・素材・法線があり、残す面を決められません"
                )
            stats.removed_faces += 1
            continue
        seen[key] = (int(polygon.index), face_tuple, loop_tuple)
        faces.append(face_tuple)
        polygon_sources.append(int(polygon.index))
        loop_sources.append(loop_tuple)
    if not faces:
        raise QuadRepairError("修復後に有効な面が残りません")
    _reject_overlapping_retained_faces(
        source,
        positions,
        source_to_output,
        polygon_sources,
        area_epsilon,
    )
    oriented = _orient_face_data(faces, loop_sources)
    used_vertices = {index for face in faces for index in face}
    stats.removed_loose_elements = len(positions) - len(used_vertices)
    ordered_vertices = sorted(used_vertices)
    compact = {old_index: new_index for new_index, old_index in enumerate(ordered_vertices)}
    positions = [positions[index] for index in ordered_vertices]
    output_source_vertices = [output_source_vertices[index] for index in ordered_vertices]
    faces = [tuple(compact[index] for index in face) for face in faces]
    source_to_compact = {
        source_index: compact[output_index]
        for source_index, output_index in source_to_output.items()
        if output_index in compact
    }

    target = bpy.data.meshes.new(f"{source.name}_BML_Sanitized")
    try:
        target.from_pydata([tuple(position) for position in positions], [], faces)
        target.update(calc_edges=True)
        _copy_sanitized_attributes(
            source,
            target,
            polygon_sources,
            loop_sources,
            output_source_vertices,
        )
        _copy_sanitized_edge_data(source, target, source_to_compact)
        target.use_auto_texspace = False
        target.texspace_location = tuple(source.texspace_location)
        target.texspace_size = tuple(source.texspace_size)
        target.update()
        mark_catmull_features(target)
        boundary, non_manifold, _loose = _edge_usage(target)
        if not oriented:
            non_manifold = max(1, non_manifold)
        return target, boundary, non_manifold
    except Exception:
        bpy.data.meshes.remove(target)
        raise


def _restore_selection(context, selected, active) -> None:
    for current in context.view_layer.objects:
        if current is not None:
            current.select_set(False)
    for current in selected:
        if current is not None and current.name in context.view_layer.objects:
            current.select_set(True)
    if active is not None and active.name in context.view_layer.objects:
        context.view_layer.objects.active = active


def _run_mesh_operation(context, source_obj, mesh, operation):
    selected = list(context.selected_objects)
    active = context.view_layer.objects.active
    temp = bpy.data.objects.new("BML_QuadRepairCandidate", mesh)
    context.scene.collection.objects.link(temp)
    temp.matrix_world = source_obj.matrix_world.copy()
    result_mesh = None
    try:
        for current in context.view_layer.objects:
            if current is not None:
                current.select_set(False)
        temp.select_set(True)
        context.view_layer.objects.active = temp
        result = operation(temp)
        if result == {"FINISHED"}:
            result_mesh = temp.data
    finally:
        bpy.data.objects.remove(temp, do_unlink=True)
        _restore_selection(context, selected, active)
    if result_mesh is not mesh and mesh.name in bpy.data.meshes and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return result_mesh


def _target_faces(mesh: bpy.types.Mesh, options: QuadRepairOptions) -> int:
    mesh.calc_loop_triangles()
    triangle_count = len(mesh.loop_triangles)
    multiplier = 2 if options.quality == "CLOSE" else 1
    minimum = 128 if options.quality == "CLOSE" else 64
    return min(options.max_output_faces, max(minimum, triangle_count * multiplier))


def _run_quadriflow(context, source_obj, cleaned, options):
    working = cleaned.copy()

    def operation(_temp):
        return bpy.ops.object.quadriflow_remesh(
            use_mesh_symmetry=False,
            use_preserve_sharp=True,
            use_preserve_boundary=True,
            preserve_attributes=True,
            smooth_normals=True,
            mode="FACES",
            target_faces=_target_faces(cleaned, options),
            seed=0,
        )

    try:
        result = _run_mesh_operation(context, source_obj, working, operation)
        if result is None and working.name in bpy.data.meshes and working.users == 0:
            bpy.data.meshes.remove(working)
        return result
    except Exception:
        _remove_mesh(working)
        raise


def _subdivide_faces_to_quads(mesh: bpy.types.Mesh) -> bpy.types.Mesh:
    positions = [vertex.co.copy() for vertex in mesh.vertices]
    edge_midpoints: dict[tuple[int, int], int] = {}
    for edge in mesh.edges:
        first, second = (int(index) for index in edge.vertices)
        key = tuple(sorted((first, second)))
        edge_midpoints[key] = len(positions)
        positions.append((mesh.vertices[first].co + mesh.vertices[second].co) * 0.5)
    faces = []
    for polygon in mesh.polygons:
        vertices = [int(index) for index in polygon.vertices]
        center_index = len(positions)
        positions.append(
            sum(
                (mesh.vertices[index].co for index in vertices),
                Vector((0.0, 0.0, 0.0)),
            )
            / len(vertices)
        )
        for index, vertex in enumerate(vertices):
            previous = vertices[index - 1]
            following = vertices[(index + 1) % len(vertices)]
            faces.append(
                (
                    vertex,
                    edge_midpoints[tuple(sorted((vertex, following)))],
                    center_index,
                    edge_midpoints[tuple(sorted((previous, vertex)))],
                )
            )
    result = bpy.data.meshes.new(f"{mesh.name}_BML_LocalQuad")
    try:
        result.from_pydata([tuple(position) for position in positions], [], faces)
        result.update(calc_edges=True)
        return result
    except Exception:
        bpy.data.meshes.remove(result)
        raise


def _run_local_quadrangulation(
    context,
    source_obj,
    cleaned,
    options,
    *,
    allow_triangle_join,
    max_attribute_loops=None,
):
    working = cleaned.copy()
    already_quads = all(len(polygon.vertices) == 4 for polygon in working.polygons)
    if already_quads and options.quality != "CLOSE":
        return working

    if not allow_triangle_join:
        output_faces = sum(len(polygon.vertices) for polygon in working.polygons)
        if max_attribute_loops is not None and output_faces * 4 > max_attribute_loops:
            _remove_mesh(working)
            raise QuadRepairError(
                f"UV・法線を安全に転送できる上限の{max_attribute_loops:,}ループを超えます"
            )
        if output_faces > options.max_output_faces:
            _remove_mesh(working)
            raise QuadRepairError(
                f"局所四角面化の候補が上限を超えます（{output_faces:,}面）"
            )
        try:
            quadrangulated = _subdivide_faces_to_quads(working)
        finally:
            _remove_mesh(working)
        if options.quality != "CLOSE":
            return quadrangulated
        denser_faces = sum(len(polygon.vertices) for polygon in quadrangulated.polygons)
        if max_attribute_loops is not None and denser_faces * 4 > max_attribute_loops:
            _remove_mesh(quadrangulated)
            raise QuadRepairError(
                f"UV・法線を安全に転送できる上限の{max_attribute_loops:,}ループを超えます"
            )
        if denser_faces > options.max_output_faces:
            _remove_mesh(quadrangulated)
            raise QuadRepairError(
                f"近接用候補が上限を超えます（{denser_faces:,}面）"
            )
        try:
            denser = _subdivide_faces_to_quads(quadrangulated)
        finally:
            _remove_mesh(quadrangulated)
        return denser

    def operation(_temp):
        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            result = bpy.ops.mesh.tris_convert_to_quads(
                face_threshold=math.radians(1.0),
                shape_threshold=math.radians(60.0),
                topology_influence=0.0,
                uvs=True,
                vcols=True,
                seam=True,
                sharp=True,
                materials=True,
                deselect_joined=False,
            )
        finally:
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        return result

    try:
        result = _run_mesh_operation(context, source_obj, working, operation)
    except Exception:
        _remove_mesh(working)
        raise
    if result is None:
        result = cleaned.copy()
    all_quads = all(len(polygon.vertices) == 4 for polygon in result.polygons)
    if all_quads and options.quality != "CLOSE":
        return result
    output_faces = sum(len(polygon.vertices) for polygon in result.polygons)
    if output_faces > options.max_output_faces:
        _remove_mesh(result)
        raise QuadRepairError(
            f"局所四角面化の候補が上限を超えます（{output_faces:,}面）"
        )
    try:
        quadrangulated = _subdivide_faces_to_quads(result)
    finally:
        _remove_mesh(result)
    if options.quality == "CLOSE" and not all_quads:
        output_faces = sum(len(polygon.vertices) for polygon in quadrangulated.polygons)
        if output_faces > options.max_output_faces:
            _remove_mesh(quadrangulated)
            raise QuadRepairError(
                f"近接用候補が上限を超えます（{output_faces:,}面）"
            )
        try:
            denser = _subdivide_faces_to_quads(quadrangulated)
        finally:
            _remove_mesh(quadrangulated)
        return denser
    return quadrangulated


def _voxel_size(mesh: bpy.types.Mesh, options: QuadRepairOptions) -> float:
    diagonal = max(_mesh_diagonal(mesh), 1.0e-9)
    lengths = []
    for edge in mesh.edges:
        v0, v1 = (mesh.vertices[index].co for index in edge.vertices)
        length = (v1 - v0).length
        if length > 1.0e-12:
            lengths.append(length)
    lengths.sort()
    median = statistics.median(lengths) if lengths else diagonal / 32.0
    if options.quality == "CLOSE":
        return max(diagonal / 224.0, min(median * 0.20, diagonal / 80.0))
    return max(diagonal / 128.0, min(median * 0.35, diagonal / 48.0))


def _run_voxel_repair(context, source_obj, cleaned, options):
    working = cleaned.copy()
    working.remesh_voxel_size = _voxel_size(cleaned, options)
    working.remesh_voxel_adaptivity = 0.0
    working.use_remesh_fix_poles = True
    working.use_remesh_preserve_volume = True
    working.use_remesh_preserve_attributes = True

    def operation(_temp):
        return bpy.ops.object.voxel_remesh()

    try:
        result = _run_mesh_operation(context, source_obj, working, operation)
        if result is None and working.name in bpy.data.meshes and working.users == 0:
            bpy.data.meshes.remove(working)
        return result
    except Exception:
        _remove_mesh(working)
        raise


def _edge_usage(mesh: bpy.types.Mesh) -> tuple[int, int, int]:
    counts = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): 0
        for edge in mesh.edges
    }
    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            pair = tuple(sorted((int(edge_key[0]), int(edge_key[1]))))
            counts[pair] = counts.get(pair, 0) + 1
    return (
        sum(value == 1 for value in counts.values()),
        sum(value > 2 for value in counts.values()),
        sum(value == 0 for value in counts.values()),
    )


def _boundary_component_count(mesh: bpy.types.Mesh) -> int:
    counts = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): 0
        for edge in mesh.edges
    }
    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            key = tuple(sorted((int(edge_key[0]), int(edge_key[1]))))
            counts[key] = counts.get(key, 0) + 1
    adjacency: dict[int, set[int]] = {}
    for (first, second), count in counts.items():
        if count != 1:
            continue
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    components = 0
    remaining = set(adjacency)
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            for neighbor in adjacency.get(stack.pop(), ()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
    return components


def _build_bvh(mesh: bpy.types.Mesh) -> BVHTree:
    mesh.calc_loop_triangles()
    vertices = [vertex.co.copy() for vertex in mesh.vertices]
    faces = [tuple(int(index) for index in triangle.vertices) for triangle in mesh.loop_triangles]
    if not faces:
        raise QuadRepairError("形状誤差を測定できる面がありません")
    return BVHTree.FromPolygons(vertices, faces, all_triangles=True)


def _sample_coordinates(mesh: bpy.types.Mesh, limit: int = 4096):
    mesh.calc_loop_triangles()
    per_group = max(1, limit // 3)
    vertex_step = max(1, math.ceil(len(mesh.vertices) / per_group))
    for index in range(0, len(mesh.vertices), vertex_step):
        yield mesh.vertices[index].co
    polygon_step = max(1, math.ceil(len(mesh.polygons) / per_group))
    for index in range(0, len(mesh.polygons), polygon_step):
        yield mesh.polygons[index].center
    triangle_step = max(1, math.ceil(len(mesh.loop_triangles) / per_group))
    for index in range(0, len(mesh.loop_triangles), triangle_step):
        triangle = mesh.loop_triangles[index]
        yield sum(
            (mesh.vertices[vertex].co for vertex in triangle.vertices),
            Vector((0.0, 0.0, 0.0)),
        ) / 3.0


def _directed_distance(source_bvh: BVHTree, target: bpy.types.Mesh) -> float:
    maximum = 0.0
    for coordinate in _sample_coordinates(target):
        nearest = source_bvh.find_nearest(coordinate)
        if nearest is None or nearest[0] is None:
            raise QuadRepairError("形状誤差を測定できません")
        maximum = max(maximum, (coordinate - nearest[0]).length)
    return maximum


def _validate_attributes(source, candidate) -> None:
    source_uvs = {layer.name for layer in source.uv_layers}
    candidate_uvs = {layer.name for layer in candidate.uv_layers}
    if not source_uvs.issubset(candidate_uvs):
        raise QuadRepairError("UVマップを保持できませんでした")
    source_colors = {attribute.name for attribute in source.color_attributes}
    candidate_colors = {attribute.name for attribute in candidate.color_attributes}
    if not source_colors.issubset(candidate_colors):
        raise QuadRepairError("カラー属性を保持できませんでした")
    if len(candidate.materials) != len(source.materials):
        raise QuadRepairError("素材スロットを保持できませんでした")
    if source.has_custom_normals and not candidate.has_custom_normals:
        raise QuadRepairError("分割法線を保持できませんでした")
    if candidate.materials and any(
        polygon.material_index >= len(candidate.materials)
        for polygon in candidate.polygons
    ):
        raise QuadRepairError("素材番号が素材スロット範囲を超えました")


def _catmull_check(context, source_obj, geometry_reference, candidate, options) -> None:
    if len(candidate.polygons) > _CATMULL_INPUT_FACE_LIMIT:
        raise QuadRepairError(
            "Catmull-Clark安全検証の上限250,000面を超えます"
        )
    temp = bpy.data.objects.new("BML_CatmullCheck", candidate)
    context.scene.collection.objects.link(temp)
    temp.matrix_world = source_obj.matrix_world.copy()
    evaluated_mesh = None
    try:
        modifier = temp.modifiers.new("BML_CatmullCheck", "SUBSURF")
        modifier.subdivision_type = "CATMULL_CLARK"
        modifier.levels = options.catmull_level
        modifier.render_levels = options.catmull_level
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = temp.evaluated_get(depsgraph)
        evaluated_mesh = bpy.data.meshes.new_from_object(
            evaluated,
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        if not evaluated_mesh.polygons:
            raise QuadRepairError("Catmull-Clark評価後に面が残りません")
        if any(
            not math.isfinite(value)
            for vertex in evaluated_mesh.vertices
            for value in vertex.co
        ):
            raise QuadRepairError("Catmull-Clark評価後に有限でない座標があります")
        base_diagonal = max(_mesh_diagonal(candidate), 1.0e-9)
        if _mesh_diagonal(evaluated_mesh) > base_diagonal * 1.8:
            raise QuadRepairError("Catmull-Clark評価後に形状が異常に膨張します")
        if _boundary_component_count(evaluated_mesh) != _boundary_component_count(candidate):
            raise QuadRepairError("Catmull-Clark評価後に開口部の数が変わります")
        reference_diagonal = max(_mesh_diagonal(geometry_reference), 1.0e-9)
        reference_bvh = _build_bvh(geometry_reference)
        evaluated_bvh = _build_bvh(evaluated_mesh)
        deviation = max(
            _directed_distance(reference_bvh, evaluated_mesh),
            _directed_distance(evaluated_bvh, geometry_reference),
        ) / reference_diagonal
        allowed = max(0.10, options.max_deviation_ratio * 1.5)
        if deviation > allowed:
            raise QuadRepairError(
                f"Catmull-Clark後の形状差が大きすぎます（{deviation * 100.0:.1f}%）"
            )
    finally:
        if evaluated_mesh is not None:
            bpy.data.meshes.remove(evaluated_mesh)
        bpy.data.objects.remove(temp, do_unlink=True)


def _validate_candidate(
    context,
    source_obj,
    source,
    geometry_reference,
    candidate,
    stats,
    options,
) -> None:
    if len(candidate.polygons) > options.max_output_faces:
        raise QuadRepairError(f"候補面数が上限を超えます（{len(candidate.polygons):,}面）")
    if not candidate.polygons or any(len(polygon.vertices) != 4 for polygon in candidate.polygons):
        raise QuadRepairError("全面を四角形へ再構成できませんでした")
    _boundary, non_manifold, loose = _edge_usage(candidate)
    if non_manifold or loose:
        raise QuadRepairError("四角面化後も非多様体または孤立辺が残っています")
    if any(
        not math.isfinite(value)
        for vertex in candidate.vertices
        for value in vertex.co
    ):
        raise QuadRepairError("候補に有限でない座標があります")
    diagonal = max(_mesh_diagonal(geometry_reference), 1.0e-9)
    area_epsilon = max(1.0e-16, diagonal * diagonal * 1.0e-14)
    if any(polygon.area <= area_epsilon for polygon in candidate.polygons):
        raise QuadRepairError("面積が失われた四角面が残っています")
    _validate_attributes(source, candidate)
    source_bvh = _build_bvh(geometry_reference)
    candidate_bvh = _build_bvh(candidate)
    forward = _directed_distance(source_bvh, candidate)
    reverse = _directed_distance(candidate_bvh, geometry_reference)
    stats.max_deviation_ratio = max(forward, reverse) / diagonal
    if stats.max_deviation_ratio > options.max_deviation_ratio:
        raise QuadRepairError(
            f"元形状との差が許容範囲を超えます（{stats.max_deviation_ratio * 100.0:.1f}%）"
        )
    candidate.use_auto_texspace = False
    candidate.texspace_location = tuple(source.texspace_location)
    candidate.texspace_size = tuple(source.texspace_size)
    _catmull_check(context, source_obj, geometry_reference, candidate, options)


def _remove_mesh(mesh: bpy.types.Mesh | None) -> None:
    if mesh is not None and mesh.name in bpy.data.meshes and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _requires_local_quadrangulation(mesh: bpy.types.Mesh) -> bool:
    used_materials = {int(polygon.material_index) for polygon in mesh.polygons}
    edge_crease = mesh.attributes.get("crease_edge")
    vertex_crease = mesh.attributes.get("crease_vert")
    return bool(
        mesh.uv_layers
        or mesh.color_attributes
        or mesh.has_custom_normals
        or len(used_materials) > 1
        or any(edge.use_seam for edge in mesh.edges)
        or any(edge.use_edge_sharp for edge in mesh.edges)
        or (
            edge_crease is not None
            and edge_crease.domain == "EDGE"
            and any(item.value > 0.0 for item in edge_crease.data)
        )
        or (
            vertex_crease is not None
            and vertex_crease.domain == "POINT"
            and any(item.value > 0.0 for item in vertex_crease.data)
        )
    )


def build_candidate(context, obj, options: QuadRepairOptions) -> QuadRepairCandidate:
    validate_source_object(obj)
    source = obj.data
    stats = QuadRepairStats(
        source_vertices=len(source.vertices),
        source_faces=len(source.polygons),
    )
    cleaned = None
    candidate = None
    try:
        cleaned, boundary, non_manifold = _sanitize_candidate(source, stats)
        attribute_sensitive = _requires_local_quadrangulation(source)
        cleaned_all_quads = all(
            len(polygon.vertices) == 4 for polygon in cleaned.polygons
        )
        direct_triangle_transfer = bool(
            attribute_sensitive
            and options.quality == "STANDARD"
            and all(len(polygon.vertices) == 3 for polygon in cleaned.polygons)
        )
        if non_manifold and boundary:
            raise QuadRepairError(
                "非多様体と開口部が併存するため、穴を塞がずに自動修復できません"
            )
        if non_manifold and attribute_sensitive:
            raise QuadRepairError(
                "非多様体の強力修復ではUV・素材・法線を安全に保持できません"
            )
        use_local = bool(boundary) or attribute_sensitive or cleaned_all_quads
        reuse_cleaned_quads = bool(
            non_manifold == 0
            and use_local
            and cleaned_all_quads
            and options.quality == "STANDARD"
        )
        if non_manifold == 0 and use_local:
            stats.used_local_quadrangulation = True
            candidate = _run_local_quadrangulation(
                context,
                obj,
                cleaned,
                options,
                allow_triangle_join=not attribute_sensitive,
                max_attribute_loops=(
                    _ATTRIBUTE_LOOP_LIMIT if attribute_sensitive else None
                ),
            )
        elif non_manifold == 0:
            candidate = _run_quadriflow(context, obj, cleaned, options)
        preserve_boundaries = candidate is not None and bool(boundary)
        if candidate is None:
            if boundary:
                raise QuadRepairError("開口部を保持した四角面化を完了できませんでした")
            stats.used_voxel_repair = True
            candidate = _run_voxel_repair(context, obj, cleaned, options)
        if candidate is None:
            raise QuadRepairError("四角面再構成を完了できませんでした")
        if len(candidate.polygons) > options.max_output_faces:
            raise QuadRepairError(
                f"候補面数が上限を超えます（{len(candidate.polygons):,}面）"
            )
        if attribute_sensitive and len(candidate.loops) > _ATTRIBUTE_LOOP_LIMIT:
            raise QuadRepairError(
                f"UV・材質を安全に転送できる上限の{_ATTRIBUTE_LOOP_LIMIT:,}ループを超えます"
            )
        try:
            if reuse_cleaned_quads:
                pass
            elif direct_triangle_transfer:
                transfer_local_triangle_data(cleaned, candidate, preserve_boundaries)
            else:
                transfer_surface_data(cleaned, candidate, preserve_boundaries)
        except SurfaceTransferError as exc:
            raise QuadRepairError(str(exc)) from exc
        _validate_candidate(
            context,
            obj,
            source,
            cleaned,
            candidate,
            stats,
            options,
        )
        stats.output_vertices = len(candidate.vertices)
        stats.output_faces = len(candidate.polygons)
        candidate.name = f"{source.name}_BML_QuadCandidate"
        result = QuadRepairCandidate(candidate, stats)
        candidate = None
        return result
    finally:
        _remove_mesh(candidate)
        _remove_mesh(cleaned)
