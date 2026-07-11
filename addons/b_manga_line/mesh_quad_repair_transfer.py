"""四角面候補を元表面へ投影し、表示用メッシュ属性を再構成する."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree


class SurfaceTransferError(RuntimeError):
    """元表面から安全に形状または属性を転送できない場合に送出する."""


@dataclass(frozen=True)
class _Sample:
    triangle_index: int
    weights: tuple[float, float, float]


class _SurfaceSampler:
    def __init__(self, mesh):
        mesh.calc_loop_triangles()
        self.mesh = mesh
        self.triangles = list(mesh.loop_triangles)
        vertices = [vertex.co.copy() for vertex in mesh.vertices]
        faces = [tuple(int(index) for index in tri.vertices) for tri in self.triangles]
        if not faces:
            raise SurfaceTransferError("属性を転送できる面がありません")
        self.bvh = BVHTree.FromPolygons(vertices, faces, all_triangles=True)
        self.material_surfaces = self._build_material_surfaces(vertices)

    def _build_material_surfaces(self, vertices):
        grouped: dict[int, list[int]] = {}
        for index, triangle in enumerate(self.triangles):
            material_index = int(self.mesh.polygons[triangle.polygon_index].material_index)
            grouped.setdefault(material_index, []).append(index)
        result = {}
        for material_index, triangle_indices in grouped.items():
            faces = [
                tuple(int(vertex) for vertex in self.triangles[index].vertices)
                for index in triangle_indices
            ]
            result[material_index] = (
                BVHTree.FromPolygons(vertices, faces, all_triangles=True),
                triangle_indices,
            )
        return result

    def nearest_location(self, coordinate, material_index=None):
        bvh = self.bvh
        triangle_indices = None
        if material_index in self.material_surfaces:
            bvh, triangle_indices = self.material_surfaces[material_index]
        nearest = bvh.find_nearest(coordinate)
        if nearest is None or nearest[0] is None or nearest[2] is None:
            raise SurfaceTransferError("元表面上の対応位置を取得できません")
        triangle_index = int(nearest[2])
        if triangle_indices is not None:
            triangle_index = triangle_indices[triangle_index]
        return nearest[0], triangle_index

    def sample(self, coordinate, material_index=None, *, value_coordinate=None) -> _Sample:
        location, triangle_index = self.nearest_location(coordinate, material_index)
        triangle = self.triangles[triangle_index]
        points = [self.mesh.vertices[index].co for index in triangle.vertices]
        value_location = location if value_coordinate is None else value_coordinate
        return _Sample(triangle_index, _barycentric(value_location, *points))


class _SegmentIndex:
    def __init__(self, segments):
        self.segments = segments
        self.tree = KDTree(len(segments))
        for index, (start, end) in enumerate(segments):
            self.tree.insert((start + end) * 0.5, index)
        self.tree.balance()

    def nearest(self, coordinate):
        return self.nearest_with_index(coordinate)[0]

    def nearest_with_index(self, coordinate):
        candidates = self.tree.find_n(coordinate, min(12, len(self.segments)))
        if not candidates:
            raise SurfaceTransferError("元の境界線へ投影できません")
        nearest = None
        nearest_index = -1
        nearest_distance = math.inf
        for _point, index, _distance in candidates:
            start, end = self.segments[index]
            point = _nearest_on_segment(coordinate, start, end)
            distance = (coordinate - point).length_squared
            if distance < nearest_distance:
                nearest = point
                nearest_index = index
                nearest_distance = distance
        return nearest, nearest_index, nearest_distance


def _barycentric(point, p0, p1, p2):
    edge0 = p1 - p0
    edge1 = p2 - p0
    offset = point - p0
    d00 = edge0.dot(edge0)
    d01 = edge0.dot(edge1)
    d11 = edge1.dot(edge1)
    d20 = offset.dot(edge0)
    d21 = offset.dot(edge1)
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 1.0e-20:
        return (1.0, 0.0, 0.0)
    weight1 = (d11 * d20 - d01 * d21) / denominator
    weight2 = (d00 * d21 - d01 * d20) / denominator
    weight0 = 1.0 - weight1 - weight2
    weights = [max(0.0, min(1.0, value)) for value in (weight0, weight1, weight2)]
    total = sum(weights)
    if total <= 1.0e-12:
        return (1.0, 0.0, 0.0)
    return tuple(value / total for value in weights)


def _nearest_on_segment(point, start, end):
    direction = end - start
    denominator = direction.length_squared
    if denominator <= 1.0e-20:
        return start.copy()
    factor = max(0.0, min(1.0, (point - start).dot(direction) / denominator))
    return start + direction * factor


def _edge_counts(mesh):
    counts = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): 0
        for edge in mesh.edges
    }
    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            key = tuple(sorted((int(edge_key[0]), int(edge_key[1]))))
            counts[key] = counts.get(key, 0) + 1
    return counts


def _boundary_components(mesh):
    boundary = [edge for edge, count in _edge_counts(mesh).items() if count == 1]
    adjacency: dict[int, set[int]] = {}
    for first, second in boundary:
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    components = []
    remaining = set(adjacency)
    while remaining:
        start = remaining.pop()
        vertices = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in adjacency.get(current, ()):
                if neighbor in vertices:
                    continue
                vertices.add(neighbor)
                remaining.discard(neighbor)
                stack.append(neighbor)
        edges = [edge for edge in boundary if edge[0] in vertices and edge[1] in vertices]
        components.append((vertices, edges))
    return components


def _component_perimeter(mesh, edges):
    return sum(
        (mesh.vertices[first].co - mesh.vertices[second].co).length
        for first, second in edges
    )


def _sample_vertex_positions(mesh, vertices, limit=128):
    ordered = sorted(vertices)
    step = max(1, math.ceil(len(ordered) / max(1, limit)))
    return [mesh.vertices[index].co for index in ordered[::step]]


def _component_match_cost(source, source_component, candidate, candidate_component, diagonal):
    source_segments = [
        (source.vertices[first].co.copy(), source.vertices[second].co.copy())
        for first, second in source_component[1]
    ]
    candidate_segments = [
        (candidate.vertices[first].co.copy(), candidate.vertices[second].co.copy())
        for first, second in candidate_component[1]
    ]
    source_index = _SegmentIndex(source_segments)
    candidate_index = _SegmentIndex(candidate_segments)
    forward = max(
        (
            (point - source_index.nearest(point)).length
            for point in _sample_vertex_positions(candidate, candidate_component[0])
        ),
        default=0.0,
    )
    reverse = max(
        (
            (point - candidate_index.nearest(point)).length
            for point in _sample_vertex_positions(source, source_component[0])
        ),
        default=0.0,
    )
    source_perimeter = _component_perimeter(source, source_component[1])
    candidate_perimeter = _component_perimeter(candidate, candidate_component[1])
    perimeter_ratio = abs(source_perimeter - candidate_perimeter) / max(
        source_perimeter, candidate_perimeter, 1.0e-12
    )
    return max(forward, reverse) + diagonal * perimeter_ratio * 0.1


def _minimum_cost_assignment(costs):
    count = len(costs)
    if count <= 8:
        return min(
            itertools.permutations(range(count)),
            key=lambda assignment: sum(
                costs[candidate][source]
                for candidate, source in enumerate(assignment)
            ),
        )
    remaining_sources = set(range(count))
    assignment = [-1] * count
    pairs = sorted(
        (costs[candidate][source], candidate, source)
        for candidate in range(count)
        for source in range(count)
    )
    for _cost, candidate, source in pairs:
        if assignment[candidate] >= 0 or source not in remaining_sources:
            continue
        assignment[candidate] = source
        remaining_sources.remove(source)
    return tuple(assignment)


def _match_boundary_components(source, candidate):
    source_components = _boundary_components(source)
    candidate_components = _boundary_components(candidate)
    if len(source_components) != len(candidate_components):
        raise SurfaceTransferError(
            "開口部の数を保持できませんでした"
            f"（元{len(source_components)} / 候補{len(candidate_components)}）"
        )
    extents = [
        max(vertex.co[axis] for vertex in source.vertices)
        - min(vertex.co[axis] for vertex in source.vertices)
        for axis in range(3)
    ]
    diagonal = max(math.sqrt(sum(value * value for value in extents)), 1.0e-9)
    costs = [
        [
            _component_match_cost(
                source,
                source_component,
                candidate,
                candidate_component,
                diagonal,
            )
            for source_component in source_components
        ]
        for candidate_component in candidate_components
    ]
    assignment = _minimum_cost_assignment(costs)
    matches = [
        (source_components[source_index], candidate_components[candidate_index])
        for candidate_index, source_index in enumerate(assignment)
    ]
    if any(
        costs[candidate_index][source_index] > diagonal * 0.02
        for candidate_index, source_index in enumerate(assignment)
    ):
        raise SurfaceTransferError("開口部の輪郭を一意に対応付けできません")
    return matches


def _project_geometry(source, candidate, sampler, preserve_boundaries):
    boundary_vertices = set()
    if preserve_boundaries:
        for source_component, candidate_component in _match_boundary_components(source, candidate):
            segments = [
                (source.vertices[first].co.copy(), source.vertices[second].co.copy())
                for first, second in source_component[1]
            ]
            index = _SegmentIndex(segments)
            for vertex_index in candidate_component[0]:
                vertex = candidate.vertices[vertex_index]
                vertex.co = index.nearest(vertex.co)
                boundary_vertices.add(vertex_index)
    for vertex in candidate.vertices:
        if vertex.index in boundary_vertices:
            continue
        vertex.co = sampler.nearest_location(vertex.co)[0]
    candidate.update()


def _interpolate(values, weights):
    return sum((value * weight for value, weight in zip(values, weights)), values[0] * 0.0)


def _triangle_item_indices(source, triangle, domain):
    if domain == "CORNER":
        return tuple(int(index) for index in triangle.loops)
    return tuple(int(index) for index in triangle.vertices)


def _sample_vector(source, data, domain, sample, field):
    triangle = source.loop_triangles[sample.triangle_index]
    indices = _triangle_item_indices(source, triangle, domain)
    values = [Vector(getattr(data[index], field)) for index in indices]
    return _interpolate(values, sample.weights)


def _replace_uv_layers(source, candidate, loop_samples):
    for layer in list(candidate.uv_layers):
        candidate.uv_layers.remove(layer)
    for source_layer in source.uv_layers:
        target_layer = candidate.uv_layers.new(name=source_layer.name, do_init=False)
        for loop, sample in zip(target_layer.data, loop_samples, strict=True):
            loop.uv = _sample_vector(source, source_layer.data, "CORNER", sample, "uv")
        target_layer.active_render = bool(source_layer.active_render)
    if source.uv_layers:
        candidate.uv_layers.active_index = min(
            int(source.uv_layers.active_index), len(candidate.uv_layers) - 1
        )


def _replace_color_attributes(source, candidate, loop_samples, point_samples):
    for attribute in list(candidate.color_attributes):
        candidate.color_attributes.remove(attribute)
    for source_attr in source.color_attributes:
        target_attr = candidate.color_attributes.new(
            name=source_attr.name,
            type=source_attr.data_type,
            domain=source_attr.domain,
        )
        samples = loop_samples if source_attr.domain == "CORNER" else point_samples
        for item, sample in zip(target_attr.data, samples, strict=True):
            item.color = _sample_vector(
                source, source_attr.data, source_attr.domain, sample, "color"
            )


def _replace_custom_normals(source, candidate, loop_samples):
    if not source.has_custom_normals:
        return
    normals = []
    for sample in loop_samples:
        triangle = source.loop_triangles[sample.triangle_index]
        values = [source.corner_normals[index].vector for index in triangle.loops]
        normal = _interpolate(values, sample.weights)
        if normal.length_squared <= 1.0e-20:
            normal = Vector((0.0, 0.0, 1.0))
        else:
            normal.normalize()
        normals.append(tuple(normal))
    candidate.normals_split_custom_set(normals)


def _replace_materials(source, candidate, sampler):
    candidate.materials.clear()
    for material in source.materials:
        candidate.materials.append(material)
    source_materials = {int(polygon.material_index) for polygon in source.polygons}
    source_smoothing = {bool(polygon.use_smooth) for polygon in source.polygons}
    if len(source_materials) == 1 and len(source_smoothing) == 1:
        material_index = next(iter(source_materials))
        use_smooth = next(iter(source_smoothing))
        for polygon in candidate.polygons:
            polygon.material_index = material_index
            polygon.use_smooth = use_smooth
        return [material_index] * len(candidate.polygons)
    material_indices = []
    smooth_values = []
    for polygon in candidate.polygons:
        _location, triangle_index = sampler.nearest_location(polygon.center)
        source_polygon = source.polygons[
            sampler.triangles[triangle_index].polygon_index
        ]
        material_indices.append(int(source_polygon.material_index))
        smooth_values.append(bool(source_polygon.use_smooth))
    for polygon, material_index, use_smooth in zip(
        candidate.polygons, material_indices, smooth_values, strict=True
    ):
        polygon.material_index = material_index
        polygon.use_smooth = use_smooth
    return material_indices


def _build_loop_samples(candidate, sampler, material_indices):
    samples = []
    for polygon, material_index in zip(candidate.polygons, material_indices, strict=True):
        for loop_index in polygon.loop_indices:
            vertex_index = int(candidate.loops[loop_index].vertex_index)
            coordinate = candidate.vertices[vertex_index].co
            inward = coordinate.lerp(polygon.center, 1.0e-4)
            samples.append(
                sampler.sample(
                    inward,
                    material_index,
                    value_coordinate=coordinate,
                )
            )
    return samples


def _protect_boundaries(candidate):
    counts = _edge_counts(candidate)
    crease = candidate.attributes.get("crease_edge")
    if crease is None:
        crease = candidate.attributes.new("crease_edge", "FLOAT", "EDGE")
    for edge in candidate.edges:
        key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        if counts.get(key) != 1:
            continue
        edge.use_edge_sharp = True
        crease.data[edge.index].value = 1.0


def _edge_adjacency(mesh):
    adjacency: dict[tuple[int, int], list[int]] = {}
    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            key = tuple(sorted((int(edge_key[0]), int(edge_key[1]))))
            adjacency.setdefault(key, []).append(int(polygon.index))
    return adjacency


def mark_catmull_features(mesh, angle_degrees=55.0):
    """境界と急角度をCatmull-Clarkで維持する辺として印付けする."""

    adjacency = _edge_adjacency(mesh)
    crease = mesh.attributes.get("crease_edge")
    if crease is None:
        crease = mesh.attributes.new("crease_edge", "FLOAT", "EDGE")
    cosine_limit = math.cos(math.radians(angle_degrees))
    for edge in mesh.edges:
        key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        polygons = adjacency.get(key, ())
        is_boundary = len(polygons) == 1
        is_sharp_angle = (
            len(polygons) == 2
            and mesh.polygons[polygons[0]].normal.dot(mesh.polygons[polygons[1]].normal)
            < cosine_limit
        )
        if not (is_boundary or is_sharp_angle):
            continue
        edge.use_edge_sharp = True
        crease.data[edge.index].value = 1.0
    mesh.update()


def _source_edge_features(source):
    adjacency = _edge_adjacency(source)
    crease = source.attributes.get("crease_edge")
    cosine_limit = math.cos(math.radians(55.0))
    segments = []
    features = []
    for edge in source.edges:
        key = tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        polygons = adjacency.get(key, ())
        sharp_angle = (
            len(polygons) == 2
            and source.polygons[polygons[0]].normal.dot(source.polygons[polygons[1]].normal)
            < cosine_limit
        )
        crease_value = (
            float(crease.data[edge.index].value)
            if crease is not None and crease.domain == "EDGE"
            else 0.0
        )
        if not (edge.use_seam or edge.use_edge_sharp or sharp_angle or crease_value > 0.0):
            continue
        first, second = (int(index) for index in edge.vertices)
        segments.append(
            (source.vertices[first].co.copy(), source.vertices[second].co.copy())
        )
        features.append(
            (
                bool(edge.use_seam),
                bool(edge.use_edge_sharp or sharp_angle),
                max(crease_value, 1.0 if sharp_angle else 0.0),
            )
        )
    return segments, features


def _transfer_edge_features(source, candidate):
    segments, features = _source_edge_features(source)
    if not segments:
        return
    index = _SegmentIndex(segments)
    crease = candidate.attributes.get("crease_edge")
    if crease is None:
        crease = candidate.attributes.new("crease_edge", "FLOAT", "EDGE")
    extents = [
        max(vertex.co[axis] for vertex in source.vertices)
        - min(vertex.co[axis] for vertex in source.vertices)
        for axis in range(3)
    ]
    diagonal = math.sqrt(sum(value * value for value in extents))
    for edge in candidate.edges:
        first, second = (candidate.vertices[item].co for item in edge.vertices)
        direction = second - first
        if direction.length_squared <= 1.0e-20:
            continue
        midpoint = (first + second) * 0.5
        _point, source_index, distance_squared = index.nearest_with_index(midpoint)
        source_start, source_end = segments[source_index]
        source_direction = source_end - source_start
        if source_direction.length_squared <= 1.0e-20:
            continue
        alignment = abs(direction.normalized().dot(source_direction.normalized()))
        tolerance = max(diagonal * 1.0e-6, min(direction.length, source_direction.length) * 0.35)
        if alignment < 0.65 or distance_squared > tolerance * tolerance:
            continue
        use_seam, use_sharp, crease_value = features[source_index]
        edge.use_seam = edge.use_seam or use_seam
        edge.use_edge_sharp = edge.use_edge_sharp or use_sharp
        crease.data[edge.index].value = max(
            float(crease.data[edge.index].value), crease_value
        )


def _transfer_vertex_creases(source, candidate):
    source_crease = source.attributes.get("crease_vert")
    if source_crease is None or source_crease.domain != "POINT":
        return
    marked = [
        vertex.index
        for vertex in source.vertices
        if source_crease.data[vertex.index].value > 0.0
    ]
    if not marked:
        return
    tree = KDTree(len(marked))
    for index, vertex_index in enumerate(marked):
        tree.insert(source.vertices[vertex_index].co, index)
    tree.balance()
    extents = [
        max(vertex.co[axis] for vertex in source.vertices)
        - min(vertex.co[axis] for vertex in source.vertices)
        for axis in range(3)
    ]
    tolerance = max(math.sqrt(sum(value * value for value in extents)) * 1.0e-6, 1.0e-9)
    target_crease = candidate.attributes.get("crease_vert")
    if target_crease is None:
        target_crease = candidate.attributes.new("crease_vert", "FLOAT", "POINT")
    for vertex in candidate.vertices:
        _coordinate, marked_index, distance = tree.find(vertex.co)
        if distance > tolerance:
            continue
        source_index = marked[marked_index]
        target_crease.data[vertex.index].value = max(
            float(target_crease.data[vertex.index].value),
            float(source_crease.data[source_index].value),
        )


def _copy_custom_properties(source, candidate):
    for key in source.keys():
        if key == "_RNA_UI":
            continue
        try:
            candidate[key] = source[key]
        except (TypeError, ValueError):
            continue


def _direct_triangle_samples(source, candidate):
    faces_per_source = 3
    expected_faces = len(source.polygons) * faces_per_source
    if any(len(polygon.vertices) != 3 for polygon in source.polygons):
        raise SurfaceTransferError("局所転送元が三角面だけではありません")
    if len(candidate.polygons) != expected_faces:
        raise SurfaceTransferError("局所四角面と元三角面の対応数が一致しません")
    loop_samples = [None] * len(candidate.loops)
    point_samples = [None] * len(candidate.vertices)
    for polygon in candidate.polygons:
        source_index = int(polygon.index) // faces_per_source
        source_polygon = source.polygons[source_index]
        points = [source.vertices[index].co for index in source_polygon.vertices]
        for loop_index in polygon.loop_indices:
            vertex_index = int(candidate.loops[loop_index].vertex_index)
            weights = _barycentric(candidate.vertices[vertex_index].co, *points)
            sample = (source_index, weights)
            loop_samples[loop_index] = sample
            if point_samples[vertex_index] is None:
                point_samples[vertex_index] = sample
    if any(sample is None for sample in loop_samples + point_samples):
        raise SurfaceTransferError("局所四角面の属性対応を構築できません")
    return loop_samples, point_samples


def _direct_sample_vector(source, data, domain, sample, field):
    polygon_index, weights = sample
    polygon = source.polygons[polygon_index]
    indices = polygon.loop_indices if domain == "CORNER" else polygon.vertices
    values = [Vector(getattr(data[index], field)) for index in indices]
    return _interpolate(values, weights)


def _replace_direct_attributes(source, candidate, loop_samples, point_samples):
    candidate.materials.clear()
    for material in source.materials:
        candidate.materials.append(material)
    for polygon in candidate.polygons:
        source_polygon = source.polygons[int(polygon.index) // 3]
        polygon.material_index = int(source_polygon.material_index)
        polygon.use_smooth = bool(source_polygon.use_smooth)
    for layer in list(candidate.uv_layers):
        candidate.uv_layers.remove(layer)
    for source_layer in source.uv_layers:
        target_layer = candidate.uv_layers.new(name=source_layer.name, do_init=False)
        for item, sample in zip(target_layer.data, loop_samples, strict=True):
            item.uv = _direct_sample_vector(
                source, source_layer.data, "CORNER", sample, "uv"
            )
        target_layer.active_render = bool(source_layer.active_render)
    if source.uv_layers:
        candidate.uv_layers.active_index = min(
            int(source.uv_layers.active_index), len(candidate.uv_layers) - 1
        )
    for attribute in list(candidate.color_attributes):
        candidate.color_attributes.remove(attribute)
    for source_attr in source.color_attributes:
        target_attr = candidate.color_attributes.new(
            name=source_attr.name,
            type=source_attr.data_type,
            domain=source_attr.domain,
        )
        samples = loop_samples if source_attr.domain == "CORNER" else point_samples
        for item, sample in zip(target_attr.data, samples, strict=True):
            item.color = _direct_sample_vector(
                source, source_attr.data, source_attr.domain, sample, "color"
            )


def _copy_split_edge_features(source, candidate):
    source_crease = source.attributes.get("crease_edge")
    target_crease = None
    if source_crease is not None and source_crease.domain == "EDGE":
        target_crease = candidate.attributes.get("crease_edge")
        if target_crease is None:
            target_crease = candidate.attributes.new("crease_edge", "FLOAT", "EDGE")
    target_edges = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1])))): edge
        for edge in candidate.edges
    }
    midpoint_start = len(source.vertices)
    for source_edge in source.edges:
        midpoint = midpoint_start + int(source_edge.index)
        crease_value = (
            float(source_crease.data[source_edge.index].value)
            if target_crease is not None
            else 0.0
        )
        for endpoint in source_edge.vertices:
            target_edge = target_edges.get(tuple(sorted((int(endpoint), midpoint))))
            if target_edge is None:
                raise SurfaceTransferError("分割した元辺の対応を取得できません")
            target_edge.use_seam = bool(source_edge.use_seam)
            target_edge.use_edge_sharp = bool(source_edge.use_edge_sharp)
            if target_crease is not None:
                target_crease.data[target_edge.index].value = crease_value


def _copy_original_vertex_creases(source, candidate):
    source_crease = source.attributes.get("crease_vert")
    if source_crease is None or source_crease.domain != "POINT":
        return
    target_crease = candidate.attributes.get("crease_vert")
    if target_crease is None:
        target_crease = candidate.attributes.new("crease_vert", "FLOAT", "POINT")
    for vertex in source.vertices:
        target_crease.data[vertex.index].value = float(
            source_crease.data[vertex.index].value
        )


def transfer_local_triangle_data(source, candidate, preserve_boundaries):
    """局所分割の既知対応を使い、最近傍探索なしで表示属性を転送する."""

    loop_samples, point_samples = _direct_triangle_samples(source, candidate)
    _replace_direct_attributes(source, candidate, loop_samples, point_samples)
    _copy_split_edge_features(source, candidate)
    _copy_original_vertex_creases(source, candidate)
    if preserve_boundaries:
        _protect_boundaries(candidate)
    candidate.update()
    if source.has_custom_normals:
        normals = []
        for sample in loop_samples:
            normal = _direct_sample_vector(
                source, source.corner_normals, "CORNER", sample, "vector"
            )
            if normal.length_squared <= 1.0e-20:
                normal = Vector((0.0, 0.0, 1.0))
            else:
                normal.normalize()
            normals.append(tuple(normal))
        candidate.normals_split_custom_set(normals)
    _copy_custom_properties(source, candidate)
    candidate.update()


def transfer_surface_data(source, candidate, preserve_boundaries):
    """形状を再投影し、材質・UV・カラー・分割法線を元表面から転送する."""

    sampler = _SurfaceSampler(source)
    _project_geometry(source, candidate, sampler, preserve_boundaries)
    material_indices = _replace_materials(source, candidate, sampler)
    needs_loop_samples = bool(source.uv_layers) or bool(source.has_custom_normals) or any(
        attribute.domain == "CORNER" for attribute in source.color_attributes
    )
    loop_samples = (
        _build_loop_samples(candidate, sampler, material_indices)
        if needs_loop_samples
        else []
    )
    needs_point_samples = any(
        attribute.domain == "POINT" for attribute in source.color_attributes
    )
    point_samples = (
        [sampler.sample(vertex.co) for vertex in candidate.vertices]
        if needs_point_samples
        else []
    )
    if source.uv_layers:
        _replace_uv_layers(source, candidate, loop_samples)
    if source.color_attributes:
        _replace_color_attributes(source, candidate, loop_samples, point_samples)
    _transfer_edge_features(source, candidate)
    _transfer_vertex_creases(source, candidate)
    if preserve_boundaries:
        _protect_boundaries(candidate)
    candidate.update()
    if source.has_custom_normals:
        _replace_custom_normals(source, candidate, loop_samples)
    _copy_custom_properties(source, candidate)
    candidate.update()
