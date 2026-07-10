"""購入素材を最終レンダリング用に整える候補メッシュ生成.

元面を Blender の固定三角化で読み、コーナー法線から推定した曲線上へ
必要な辺中点だけを追加する。元頂点は動かさず、UV・素材・分割法線・
シャープ辺・UVシームを候補へ明示的に引き継ぐ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable

import bpy
from mathutils import Vector


class UnsafeMeshError(RuntimeError):
    """自動確定できない構造を検出したときに送出する."""


@dataclass(frozen=True)
class OptimizeOptions:
    passes: int = 1
    min_curve_ratio: float = 0.001
    max_displacement_ratio: float = 0.18
    max_output_faces: int = 2_000_000


@dataclass
class OptimizeStats:
    source_vertices: int = 0
    source_faces: int = 0
    output_vertices: int = 0
    output_faces: int = 0
    split_edges: int = 0
    removed_degenerate_faces: int = 0
    removed_duplicate_faces: int = 0
    open_edges: int = 0
    passes_applied: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.split_edges
            or self.removed_degenerate_faces
            or self.removed_duplicate_faces
        )


@dataclass
class CandidateResult:
    mesh: bpy.types.Mesh | None
    stats: OptimizeStats


@dataclass
class _EdgeRecord:
    endpoints: tuple[int, int]
    source_edge_index: int | None
    proposals: list[Vector] = field(default_factory=list)
    surface_signatures: set[tuple] = field(default_factory=set)
    protected_boundary: bool = False
    midpoint: Vector | None = None
    midpoint_index: int | None = None


_TRI_EDGE_LOCAL = ((0, 1), (1, 2), (2, 0))
_TOKEN_BARY = {
    0: (1.0, 0.0, 0.0),
    1: (0.0, 1.0, 0.0),
    2: (0.0, 0.0, 1.0),
    3: (0.5, 0.5, 0.0),
    4: (0.0, 0.5, 0.5),
    5: (0.5, 0.0, 0.5),
}
_FACE_TEMPLATES = {
    0: ((0, 1, 2),),
    1: ((0, 3, 2), (3, 1, 2)),
    2: ((0, 1, 4), (0, 4, 2)),
    3: ((3, 1, 4), (0, 3, 2), (3, 4, 2)),
    4: ((0, 1, 5), (5, 1, 2)),
    5: ((0, 3, 5), (3, 1, 2), (3, 2, 5)),
    6: ((4, 2, 5), (0, 1, 5), (1, 4, 5)),
    7: ((0, 3, 5), (3, 1, 4), (5, 4, 2), (3, 4, 5)),
}
_STRUCTURAL_ATTRS = {
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


def _round_vector(values: Iterable[float]) -> tuple[float, ...]:
    return tuple(round(float(value), 9) for value in values)


def _rotations(values: tuple) -> list[tuple]:
    return [values[index:] + values[:index] for index in range(len(values))]


def _canonical(values: tuple) -> tuple:
    return min(_rotations(values))


def _face_item(mesh, loop_index: int, uv_layers, corner_colors) -> tuple:
    loop = mesh.loops[loop_index]
    normal = mesh.corner_normals[loop_index].vector
    uvs = tuple(_round_vector(layer.data[loop_index].uv) for layer in uv_layers)
    colors = tuple(
        _round_vector(attribute.data[loop_index].color)
        for attribute in corner_colors
    )
    return (int(loop.vertex_index), uvs, colors, _round_vector(normal))


def _face_masks(mesh: bpy.types.Mesh) -> tuple[list[bool], int, int]:
    """除去可能面を分類し、属性差のある重複面は安全停止する."""
    uv_layers = tuple(mesh.uv_layers)
    corner_colors = tuple(
        attribute
        for attribute in mesh.color_attributes
        if attribute.domain == "CORNER"
    )
    keep = [True] * len(mesh.polygons)
    duplicate_count = 0
    degenerate_count = 0
    if not mesh.vertices:
        return keep, duplicate_count, degenerate_count

    xs = [vertex.co.x for vertex in mesh.vertices]
    ys = [vertex.co.y for vertex in mesh.vertices]
    zs = [vertex.co.z for vertex in mesh.vertices]
    diagonal_sq = (
        (max(xs) - min(xs)) ** 2
        + (max(ys) - min(ys)) ** 2
        + (max(zs) - min(zs)) ** 2
    )
    area_epsilon = max(1.0e-16, diagonal_sq * 1.0e-14)
    exact_seen: dict[tuple, int] = {}
    geometry_seen: dict[tuple, tuple] = {}

    for polygon in mesh.polygons:
        if len(polygon.vertices) < 3 or float(polygon.area) <= area_epsilon:
            keep[polygon.index] = False
            degenerate_count += 1
            continue
        loops = tuple(range(polygon.loop_start, polygon.loop_start + polygon.loop_total))
        items = tuple(
            _face_item(mesh, loop_index, uv_layers, corner_colors)
            for loop_index in loops
        )
        exact_key = (
            int(polygon.material_index),
            bool(polygon.use_smooth),
            _canonical(items),
        )
        vertices = tuple(int(vertex) for vertex in polygon.vertices)
        forward = _canonical(vertices)
        reverse = _canonical(tuple(reversed(vertices)))
        geometry_key = min(forward, reverse)
        orientation_key = forward

        if exact_key in exact_seen:
            keep[polygon.index] = False
            duplicate_count += 1
            continue
        previous = geometry_seen.get(geometry_key)
        if previous is not None:
            previous_orientation, previous_exact = previous
            if previous_orientation != orientation_key:
                raise UnsafeMeshError(
                    "表裏が重なる面があり、自動的に残す側を決められません"
                )
            if previous_exact != exact_key:
                raise UnsafeMeshError(
                    "同じ位置の面でUV・素材・法線・色が異なり、自動統合できません"
                )
        exact_seen[exact_key] = polygon.index
        geometry_seen[geometry_key] = (orientation_key, exact_key)
    return keep, duplicate_count, degenerate_count


def _edge_face_counts(mesh, face_keep: list[bool]) -> tuple[dict[tuple[int, int], int], int]:
    counts: dict[tuple[int, int], int] = {}
    for polygon in mesh.polygons:
        if not face_keep[polygon.index]:
            continue
        for edge_key in polygon.edge_keys:
            key = tuple(sorted((int(edge_key[0]), int(edge_key[1]))))
            counts[key] = counts.get(key, 0) + 1
    non_manifold = [key for key, count in counts.items() if count > 2]
    if non_manifold:
        raise UnsafeMeshError(
            f"3面以上が接続する辺が{len(non_manifold)}本あり、曲面を一意に決められません"
        )
    return counts, sum(1 for count in counts.values() if count == 1)


def _unsupported_attributes(mesh: bpy.types.Mesh) -> list[str]:
    uv_names = {layer.name for layer in mesh.uv_layers}
    color_names = {attr.name for attr in mesh.color_attributes}
    allowed = _STRUCTURAL_ATTRS | uv_names | color_names
    return sorted(
        attr.name
        for attr in mesh.attributes
        if not attr.name.startswith(".") and attr.name not in allowed
    )


def validate_source_object(obj: bpy.types.Object) -> None:
    if obj.type != "MESH" or obj.data is None:
        raise UnsafeMeshError("メッシュオブジェクトではありません")
    if obj.library is not None or obj.data.library is not None:
        raise UnsafeMeshError("リンク素材は直接変更できません")
    if getattr(obj, "override_library", None) is not None:
        raise UnsafeMeshError("ライブラリオーバーライドは直接変更できません")
    if obj.data.shape_keys is not None:
        raise UnsafeMeshError("シェイプキー付きメッシュには対応していません")
    if obj.vertex_groups:
        raise UnsafeMeshError("変形用頂点グループ付きメッシュには対応していません")
    if obj.modifiers:
        raise UnsafeMeshError("モディファイアを適用または削除してから実行してください")
    polygon_edges = {
        tuple(sorted((int(edge[0]), int(edge[1]))))
        for polygon in obj.data.polygons
        for edge in polygon.edge_keys
    }
    loose_edges = sum(
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        not in polygon_edges
        for edge in obj.data.edges
    )
    if loose_edges:
        raise UnsafeMeshError(
            f"面に属さない辺が{loose_edges}本あり、保持方法を決められません"
        )
    unsupported = _unsupported_attributes(obj.data)
    if unsupported:
        names = ", ".join(unsupported[:3])
        raise UnsafeMeshError(f"未対応のメッシュ属性があります: {names}")


def _pn_midpoint(p0: Vector, p1: Vector, n0: Vector, n1: Vector) -> Vector:
    n0 = n0.normalized() if n0.length_squared else Vector((0.0, 0.0, 1.0))
    n1 = n1.normalized() if n1.length_squared else Vector((0.0, 0.0, 1.0))
    b01 = (2.0 * p0 + p1 - n0 * (p1 - p0).dot(n0)) / 3.0
    b10 = (2.0 * p1 + p0 - n1 * (p0 - p1).dot(n1)) / 3.0
    return p0 * 0.125 + b01 * 0.375 + b10 * 0.375 + p1 * 0.125


def _resolved_midpoint(record: _EdgeRecord, positions, options) -> Vector | None:
    p0 = positions[record.endpoints[0]]
    p1 = positions[record.endpoints[1]]
    edge = p1 - p0
    length = edge.length
    if length <= 1.0e-12:
        return None
    if len(record.proposals) > 1 and (
        record.protected_boundary or len(record.surface_signatures) > 1
    ):
        return None
    linear = (p0 + p1) * 0.5
    min_displacement = length * options.min_curve_ratio
    displacements = [proposal - linear for proposal in record.proposals]
    curved = [value for value in displacements if value.length > min_displacement]
    if not curved:
        return None
    directions = [value.normalized() for value in curved]
    for index, direction in enumerate(directions):
        if any(direction.dot(other) < 0.0 for other in directions[index + 1 :]):
            return None
    displacement = sum(curved, Vector()) / len(curved)
    maximum = length * options.max_displacement_ratio
    if displacement.length > maximum:
        displacement.normalize()
        displacement *= maximum
    return linear + displacement


def _interpolate(values, bary):
    return tuple(
        float(values[0][axis]) * bary[0]
        + float(values[1][axis]) * bary[1]
        + float(values[2][axis]) * bary[2]
        for axis in range(len(values[0]))
    )


def _normal_interpolate(values, bary) -> tuple[float, float, float]:
    normal = Vector(_interpolate(values, bary))
    if not normal.length_squared:
        normal = Vector(values[0])
    normal.normalize()
    return tuple(normal)


def _edge_key(mesh_edge_by_pair, polygon_index, v0, v1):
    pair = tuple(sorted((int(v0), int(v1))))
    source_edge = mesh_edge_by_pair.get(pair)
    if source_edge is not None:
        return ("E", source_edge), source_edge, pair
    return ("P", int(polygon_index), pair[0], pair[1]), None, pair


def _loop_surface_signature(mesh, loop_index: int) -> tuple:
    return (
        tuple(
            _round_vector(layer.data[loop_index].uv)
            for layer in mesh.uv_layers
        ),
        _round_vector(mesh.corner_normals[loop_index].vector),
    )


def _collect_edge_records(mesh, triangles) -> tuple[dict[tuple, _EdgeRecord], list[tuple]]:
    edge_by_pair = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): edge.index
        for edge in mesh.edges
    }
    records: dict[tuple, _EdgeRecord] = {}
    triangle_keys: list[tuple] = []
    positions = [vertex.co.copy() for vertex in mesh.vertices]
    loop_signature_cache: dict[int, tuple] = {}
    for triangle in triangles:
        vertices = tuple(int(index) for index in triangle.vertices)
        loops = tuple(int(index) for index in triangle.loops)
        normals = [mesh.corner_normals[index].vector.copy() for index in loops]
        polygon = mesh.polygons[triangle.polygon_index]
        endpoint_signatures = {}
        for vertex_index, loop_index in zip(vertices, loops, strict=True):
            signature = loop_signature_cache.get(loop_index)
            if signature is None:
                signature = _loop_surface_signature(mesh, loop_index)
                loop_signature_cache[loop_index] = signature
            endpoint_signatures[vertex_index] = signature
        keys = []
        for local_a, local_b in _TRI_EDGE_LOCAL:
            key, source_edge, pair = _edge_key(
                edge_by_pair,
                triangle.polygon_index,
                vertices[local_a],
                vertices[local_b],
            )
            record = records.setdefault(key, _EdgeRecord(pair, source_edge))
            if source_edge is not None:
                edge = mesh.edges[source_edge]
                record.protected_boundary = bool(
                    edge.use_seam or edge.use_edge_sharp
                )
            record.surface_signatures.add(
                (
                    int(polygon.material_index),
                    bool(polygon.use_smooth),
                    tuple(endpoint_signatures[vertex_index] for vertex_index in pair),
                )
            )
            proposal = _pn_midpoint(
                positions[vertices[local_a]],
                positions[vertices[local_b]],
                normals[local_a],
                normals[local_b],
            )
            record.proposals.append(proposal)
            keys.append(key)
        triangle_keys.append(tuple(keys))
    return records, triangle_keys


def _resolve_edges(records, positions, options) -> int:
    split_count = 0
    for record in records.values():
        record.midpoint = _resolved_midpoint(record, positions, options)
        if record.midpoint is None:
            continue
        record.midpoint_index = len(positions)
        positions.append(record.midpoint.copy())
        split_count += 1
    return split_count


def _candidate_edge_sources(records) -> dict[tuple[int, int], int]:
    result: dict[tuple[int, int], int] = {}
    for record in records.values():
        if record.source_edge_index is None:
            continue
        v0, v1 = record.endpoints
        if record.midpoint_index is None:
            result[tuple(sorted((v0, v1)))] = record.source_edge_index
            continue
        midpoint = record.midpoint_index
        result[tuple(sorted((v0, midpoint)))] = record.source_edge_index
        result[tuple(sorted((midpoint, v1)))] = record.source_edge_index
    return result


def _copy_materials(source, candidate) -> None:
    for material in source.materials:
        candidate.materials.append(material)


def _copy_uv_layers(source, candidate, output_uvs) -> None:
    active_index = int(getattr(source.uv_layers, "active_index", 0) or 0)
    for layer_index, source_layer in enumerate(source.uv_layers):
        target = candidate.uv_layers.new(name=source_layer.name, do_init=False)
        values = output_uvs[source_layer.name]
        for item, value in zip(target.data, values, strict=True):
            item.uv = value
        target.active_render = bool(source_layer.active_render)
        if layer_index == active_index:
            candidate.uv_layers.active_index = layer_index


def _copy_color_attributes(source, candidate, corner_values, midpoint_sources) -> None:
    for source_attr in source.color_attributes:
        target = candidate.color_attributes.new(
            name=source_attr.name,
            type=source_attr.data_type,
            domain=source_attr.domain,
        )
        if source_attr.domain == "CORNER":
            values = corner_values[source_attr.name]
        else:
            values = [tuple(item.color) for item in source_attr.data]
            for v0, v1 in midpoint_sources:
                color0 = values[v0]
                color1 = values[v1]
                values.append(tuple((a + b) * 0.5 for a, b in zip(color0, color1)))
        for item, value in zip(target.data, values, strict=True):
            item.color = value
    for name in ("active_color_index", "render_color_index"):
        if hasattr(source.color_attributes, name) and hasattr(candidate.color_attributes, name):
            try:
                value = int(getattr(source.color_attributes, name))
                if value < 0:
                    continue
                setattr(
                    candidate.color_attributes,
                    name,
                    value,
                )
            except (AttributeError, TypeError, ValueError):
                pass


def _copy_vertex_data(source, candidate, midpoint_sources) -> None:
    source_crease = source.attributes.get("crease_vert")
    if source_crease is None or source_crease.domain != "POINT":
        return
    target_crease = candidate.attributes.new("crease_vert", "FLOAT", "POINT")
    values = [float(item.value) for item in source_crease.data]
    values.extend((values[v0] + values[v1]) * 0.5 for v0, v1 in midpoint_sources)
    for item, value in zip(target_crease.data, values, strict=True):
        item.value = value


def _copy_edge_data(source, candidate, edge_sources) -> None:
    target_by_pair = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): edge
        for edge in candidate.edges
    }
    for pair, source_index in edge_sources.items():
        target = target_by_pair.get(pair)
        if target is None:
            continue
        original = source.edges[source_index]
        target.use_seam = bool(original.use_seam)
        target.use_edge_sharp = bool(original.use_edge_sharp)
        if hasattr(target, "use_freestyle_mark"):
            target.use_freestyle_mark = bool(original.use_freestyle_mark)
    source_crease = source.attributes.get("crease_edge")
    if source_crease is None or source_crease.domain != "EDGE":
        return
    target_crease = candidate.attributes.new("crease_edge", "FLOAT", "EDGE")
    target_index_by_pair = {pair: edge.index for pair, edge in target_by_pair.items()}
    for pair, source_index in edge_sources.items():
        target_index = target_index_by_pair.get(pair)
        if target_index is not None:
            target_crease.data[target_index].value = source_crease.data[source_index].value


def _copy_mesh_properties(source, candidate) -> None:
    texspace_location = tuple(source.texspace_location)
    texspace_size = tuple(source.texspace_size)
    candidate.use_auto_texspace = False
    candidate.texspace_location = texspace_location
    candidate.texspace_size = texspace_size
    for key in source.keys():
        if key == "_RNA_UI":
            continue
        try:
            candidate[key] = source[key]
        except (TypeError, ValueError):
            continue


def _build_once(source: bpy.types.Mesh, name: str, options: OptimizeOptions) -> CandidateResult:
    source.calc_loop_triangles()
    face_keep, duplicates, degenerates = _face_masks(source)
    _, open_edges = _edge_face_counts(source, face_keep)
    triangles = [
        triangle
        for triangle in source.loop_triangles
        if face_keep[triangle.polygon_index]
    ]
    stats = OptimizeStats(
        source_vertices=len(source.vertices),
        source_faces=len(source.polygons),
        removed_duplicate_faces=duplicates,
        removed_degenerate_faces=degenerates,
        open_edges=open_edges,
    )
    if not triangles:
        raise UnsafeMeshError("有効な面がありません")

    records, triangle_keys = _collect_edge_records(source, triangles)
    positions = [vertex.co.copy() for vertex in source.vertices]
    stats.split_edges = _resolve_edges(records, positions, options)
    if not stats.changed:
        stats.output_vertices = len(source.vertices)
        stats.output_faces = len(source.polygons)
        return CandidateResult(None, stats)

    output_faces: list[tuple[int, int, int]] = []
    output_sources: list[int] = []
    output_normals: list[tuple[float, float, float]] = []
    output_uvs = {layer.name: [] for layer in source.uv_layers}
    corner_colors = {
        attr.name: []
        for attr in source.color_attributes
        if attr.domain == "CORNER"
    }
    midpoint_sources = [record.endpoints for record in records.values() if record.midpoint is not None]

    for triangle, keys in zip(triangles, triangle_keys, strict=True):
        vertices = tuple(int(index) for index in triangle.vertices)
        loops = tuple(int(index) for index in triangle.loops)
        local_indices = [vertices[0], vertices[1], vertices[2]]
        mask = 0
        for edge_index, key in enumerate(keys):
            record = records[key]
            if record.midpoint_index is not None:
                mask |= 1 << edge_index
                local_indices.append(record.midpoint_index)
            else:
                local_indices.append(-1)
        source_normals = [tuple(source.corner_normals[index].vector) for index in loops]
        source_uvs = {
            layer.name: [tuple(layer.data[index].uv) for index in loops]
            for layer in source.uv_layers
        }
        source_colors = {
            attr.name: [tuple(attr.data[index].color) for index in loops]
            for attr in source.color_attributes
            if attr.domain == "CORNER"
        }
        for token_face in _FACE_TEMPLATES[mask]:
            face = tuple(local_indices[token] for token in token_face)
            output_faces.append(face)
            output_sources.append(int(triangle.polygon_index))
            for token in token_face:
                bary = _TOKEN_BARY[token]
                output_normals.append(_normal_interpolate(source_normals, bary))
                for layer_name, values in source_uvs.items():
                    output_uvs[layer_name].append(_interpolate(values, bary))
                for attr_name, values in source_colors.items():
                    corner_colors[attr_name].append(_interpolate(values, bary))

    if len(output_faces) > options.max_output_faces:
        raise UnsafeMeshError(
            f"候補面数が上限を超えます（{len(output_faces):,}面）"
        )
    candidate = bpy.data.meshes.new(name)
    try:
        candidate.from_pydata([tuple(position) for position in positions], [], output_faces)
        if candidate.validate(clean_customdata=False):
            raise UnsafeMeshError("候補メッシュに不正な要素が生成されました")
        candidate.update(calc_edges=True)
        _copy_materials(source, candidate)
        for polygon, source_index in zip(candidate.polygons, output_sources, strict=True):
            source_polygon = source.polygons[source_index]
            polygon.material_index = int(source_polygon.material_index)
            polygon.use_smooth = bool(source_polygon.use_smooth)
            if hasattr(polygon, "use_freestyle_mark"):
                polygon.use_freestyle_mark = bool(source_polygon.use_freestyle_mark)
        _copy_uv_layers(source, candidate, output_uvs)
        _copy_color_attributes(source, candidate, corner_colors, midpoint_sources)
        _copy_vertex_data(source, candidate, midpoint_sources)
        _copy_edge_data(source, candidate, _candidate_edge_sources(records))
        if source.has_custom_normals:
            candidate.normals_split_custom_set(output_normals)
        _copy_mesh_properties(source, candidate)
        candidate.update()
        if candidate.validate_material_indices():
            raise UnsafeMeshError("候補の素材番号が元の素材範囲を超えました")
        if any(not math.isfinite(value) for vertex in candidate.vertices for value in vertex.co):
            raise UnsafeMeshError("候補に有限でない座標が生成されました")
    except Exception:
        bpy.data.meshes.remove(candidate)
        raise
    stats.output_vertices = len(candidate.vertices)
    stats.output_faces = len(candidate.polygons)
    stats.passes_applied = 1
    return CandidateResult(candidate, stats)


def _merge_stats(total: OptimizeStats, current: OptimizeStats) -> None:
    if total.source_faces == 0:
        total.source_vertices = current.source_vertices
        total.source_faces = current.source_faces
    total.output_vertices = current.output_vertices
    total.output_faces = current.output_faces
    total.split_edges += current.split_edges
    total.removed_degenerate_faces += current.removed_degenerate_faces
    total.removed_duplicate_faces += current.removed_duplicate_faces
    total.open_edges = current.open_edges
    total.passes_applied += current.passes_applied
    total.warnings.extend(current.warnings)


def build_candidate(
    source: bpy.types.Mesh,
    name: str,
    options: OptimizeOptions,
) -> CandidateResult:
    """検証済み候補を返す。変更不要なら mesh=None を返す."""
    current = source
    owned: list[bpy.types.Mesh] = []
    total = OptimizeStats()
    try:
        for pass_index in range(max(1, int(options.passes))):
            result = _build_once(current, f"{name}_Pass{pass_index + 1}", options)
            _merge_stats(total, result.stats)
            if result.mesh is None:
                break
            owned.append(result.mesh)
            current = result.mesh
        if current is source:
            return CandidateResult(None, total)
        final = current
        for mesh in owned[:-1]:
            bpy.data.meshes.remove(mesh)
        return CandidateResult(final, total)
    except Exception:
        for mesh in owned:
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)
        raise
