"""B-MANGA Line inner-line chain grouping for midpoint width endpoints."""

from __future__ import annotations

import bmesh
import bpy


CHAIN_ID_ATTR = "BML_InnerLineChainID"
SHARP_EDGE_ATTR = "sharp_edge"
CREASE_EDGE_ATTR = "crease_edge"


def _edge_attr_value(mesh: bpy.types.Mesh, attr_name: str, edge_index: int):
    attr = mesh.attributes.get(attr_name)
    if attr is None or getattr(attr, "domain", None) != "EDGE":
        return None
    if edge_index >= len(attr.data):
        return None
    return getattr(attr.data[edge_index], "value", None)


def _edge_is_marked(mesh: bpy.types.Mesh, edge_index: int) -> bool:
    if bool(_edge_attr_value(mesh, SHARP_EDGE_ATTR, edge_index)):
        return True
    crease = _edge_attr_value(mesh, CREASE_EDGE_ATTR, edge_index)
    if crease is None:
        return False
    try:
        return float(crease) > 0.0
    except (TypeError, ValueError):
        return False


def _edge_is_angle_selected(edge, threshold: float) -> bool:
    if len(edge.link_faces) < 2:
        return False
    try:
        return edge.calc_face_angle() + 1.0e-7 >= threshold
    except ValueError:
        return False


def _ensure_chain_id_attribute(mesh: bpy.types.Mesh):
    attr = mesh.attributes.get(CHAIN_ID_ATTR)
    if (
        attr is not None
        and getattr(attr, "domain", None) == "EDGE"
        and getattr(attr, "data_type", None) == "INT"
    ):
        return attr
    if attr is not None:
        mesh.attributes.remove(attr)
    return mesh.attributes.new(CHAIN_ID_ATTR, "INT", "EDGE")


def _collect_selected_graph(mesh, bm, angle: float, use_marked_edges: bool):
    selected_edges: set[int] = set()
    edge_vertices: dict[int, tuple[int, int]] = {}
    edge_lookup: dict[tuple[int, int], int] = {}
    neighbors: list[set[int]] = [set() for _ in range(len(bm.verts))]
    vertex_edges: list[set[int]] = [set() for _ in range(len(bm.verts))]
    for edge in bm.edges:
        edge_index = edge.index
        selected = (
            _edge_is_marked(mesh, edge_index)
            if use_marked_edges
            else _edge_is_angle_selected(edge, angle)
        )
        if not selected:
            continue
        v1 = edge.verts[0].index
        v2 = edge.verts[1].index
        selected_edges.add(edge_index)
        edge_vertices[edge_index] = (v1, v2)
        edge_lookup[tuple(sorted((v1, v2)))] = edge_index
        neighbors[v1].add(v2)
        neighbors[v2].add(v1)
        vertex_edges[v1].add(edge_index)
        vertex_edges[v2].add(edge_index)
    return selected_edges, edge_vertices, edge_lookup, neighbors, vertex_edges


def _trace_chain_edges(
    edge_lookup: dict[tuple[int, int], int],
    neighbors: list[set[int]],
    anchors: set[int],
    start: int,
    next_vert: int,
) -> list[int]:
    chain: list[int] = []
    prev = start
    current = next_vert
    while True:
        edge_index = edge_lookup.get(tuple(sorted((prev, current))))
        if edge_index is None:
            break
        chain.append(edge_index)
        if current in anchors:
            break
        candidates = [vi for vi in neighbors[current] if vi != prev]
        if len(candidates) != 1:
            break
        prev, current = current, candidates[0]
    return chain


def _assign_remaining_component(
    selected_edges: set[int],
    edge_vertices: dict[int, tuple[int, int]],
    vertex_edges: list[set[int]],
    visited: set[int],
    start_edge: int,
) -> set[int]:
    component: set[int] = set()
    stack = [start_edge]
    while stack:
        edge_index = stack.pop()
        if edge_index in visited or edge_index not in selected_edges:
            continue
        visited.add(edge_index)
        component.add(edge_index)
        for vertex_index in edge_vertices[edge_index]:
            for connected_edge in vertex_edges[vertex_index]:
                if connected_edge not in visited:
                    stack.append(connected_edge)
    return component


def _write_chain(attr, chain: list[int], chain_id: int, visited: set[int]) -> None:
    for item in chain:
        visited.add(item)
        if item < len(attr.data):
            attr.data[item].value = chain_id


def _write_anchor_chains(
    attr,
    edge_lookup: dict[tuple[int, int], int],
    neighbors: list[set[int]],
    anchors: set[int],
    visited: set[int],
    chain_id: int,
) -> int:
    for start in sorted(anchors):
        for next_vert in sorted(neighbors[start]):
            edge_index = edge_lookup.get(tuple(sorted((start, next_vert))))
            if edge_index is None or edge_index in visited:
                continue
            chain = _trace_chain_edges(edge_lookup, neighbors, anchors, start, next_vert)
            if not chain:
                continue
            _write_chain(attr, chain, chain_id, visited)
            chain_id += 1
    return chain_id


def _write_remaining_components(
    attr,
    selected_edges: set[int],
    edge_vertices: dict[int, tuple[int, int]],
    vertex_edges: list[set[int]],
    visited: set[int],
    chain_id: int,
) -> int:
    for edge_index in sorted(selected_edges):
        if edge_index in visited:
            continue
        component = _assign_remaining_component(
            selected_edges, edge_vertices, vertex_edges, visited, edge_index,
        )
        if not component:
            continue
        _write_chain(attr, sorted(component), chain_id, visited)
        chain_id += 1
    return chain_id


def update_chain_id_attribute(
    obj: bpy.types.Object,
    angle: float,
    use_marked_edges: bool,
) -> None:
    if obj.type != "MESH" or obj.data is None:
        return
    mesh = obj.data
    attr = _ensure_chain_id_attribute(mesh)
    for item in attr.data:
        item.value = -1

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        graph = _collect_selected_graph(mesh, bm, angle, use_marked_edges)
        selected_edges, edge_vertices, edge_lookup, neighbors, vertex_edges = graph
        anchors = {
            i for i, connected in enumerate(neighbors)
            if connected and len(connected) != 2
        }
        visited: set[int] = set()
        chain_id = _write_anchor_chains(
            attr, edge_lookup, neighbors, anchors, visited, 0,
        )
        _write_remaining_components(
            attr, selected_edges, edge_vertices, vertex_edges, visited, chain_id,
        )
    finally:
        bm.free()
    mesh.update()
