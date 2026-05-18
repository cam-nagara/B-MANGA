"""コマ頂点編集の選択・スナップ補助."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable


def selection_vertex_indices(selection: dict, poly_len: int) -> list[int]:
    values = selection.get("vertices")
    if values is None:
        values = (selection.get("vertex", -1),)
    out: list[int] = []
    for value in values:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < poly_len and idx not in out:
            out.append(idx)
    if not out:
        try:
            idx = int(selection.get("vertex", -1))
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < poly_len:
            out.append(idx)
    return out


def _edge_unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    return dx / length, dy / length


def _project_to_line(
    point: tuple[float, float],
    anchor: tuple[float, float],
    direction: tuple[float, float],
) -> tuple[float, float]:
    ux, uy = direction
    vx = point[0] - anchor[0]
    vy = point[1] - anchor[1]
    t = vx * ux + vy * uy
    return anchor[0] + ux * t, anchor[1] + uy * t


def _adjacent_edge_directions(adjacent_edge_states: list[dict]) -> list[tuple[float, float]]:
    directions: list[tuple[float, float]] = []
    for state in adjacent_edge_states or []:
        poly2 = state.get("poly") or []
        edge = int(state.get("edge", -1))
        if not (0 <= edge < len(poly2)):
            continue
        unit = _edge_unit(poly2[edge], poly2[(edge + 1) % len(poly2)])
        if unit is None:
            continue
        if not any(abs(unit[0] * old[0] + unit[1] * old[1]) > 0.995 for old in directions):
            directions.append(unit)
    return directions


def snap_vertex_delta_to_guides(
    poly: list[tuple[float, float]],
    moving_vertices: set[int],
    primary_vertex: int,
    dx: float,
    dy: float,
    adjacent_edge_states: list[dict],
    *,
    snap_tolerance_mm: float,
    direction_snap_tolerance_mm: float,
) -> tuple[float, float]:
    """頂点ドラッグを他頂点の水平/垂直と隣接辺の角度へ吸着する."""
    if not (0 <= primary_vertex < len(poly)):
        return dx, dy
    origin = poly[primary_vertex]
    target = (origin[0] + dx, origin[1] + dy)
    best_dx, best_dy = dx, dy

    for i, point in enumerate(poly):
        if i in moving_vertices:
            continue
        if abs(target[0] - point[0]) <= snap_tolerance_mm:
            best_dx = point[0] - origin[0]
            target = (origin[0] + best_dx, target[1])
        if abs(target[1] - point[1]) <= snap_tolerance_mm:
            best_dy = point[1] - origin[1]
            target = (target[0], origin[1] + best_dy)

    directions = _adjacent_edge_directions(adjacent_edge_states)
    best_angle_dist = direction_snap_tolerance_mm
    for neighbor_idx in ((primary_vertex - 1) % len(poly), (primary_vertex + 1) % len(poly)):
        if neighbor_idx in moving_vertices:
            continue
        anchor = poly[neighbor_idx]
        for unit in directions:
            projected = _project_to_line(target, anchor, unit)
            dist = math.hypot(projected[0] - target[0], projected[1] - target[1])
            if dist < best_angle_dist:
                best_angle_dist = dist
                best_dx = projected[0] - origin[0]
                best_dy = projected[1] - origin[1]
    return best_dx, best_dy


def find_extended_vertex_adjacent_edges(
    work,
    page_idx: int,
    coma_idx: int,
    selected_edge: int,
    vertex_idx: int,
    poly: list[tuple[float, float]],
    *,
    page_offset_fn: Callable,
    all_edges_world_fn: Callable,
    gap_tolerance_mm: float,
) -> list[tuple[int, int, int]]:
    """移動頂点から見て同一直線の延長上にある他コマ辺を探す."""
    if len(poly) < 3:
        return []
    pox, poy = page_offset_fn(work, page_idx)
    a = (poly[selected_edge][0] + pox, poly[selected_edge][1] + poy)
    b = (
        poly[(selected_edge + 1) % len(poly)][0] + pox,
        poly[(selected_edge + 1) % len(poly)][1] + poy,
    )
    vertex_world = (poly[vertex_idx][0] + pox, poly[vertex_idx][1] + poy)
    unit = _edge_unit(a, b)
    if unit is None:
        return []
    ux, uy = unit
    nx, ny = -uy, ux
    out: list[tuple[int, int, int]] = []
    for pi2, panel_i2, ei2, a2, b2 in all_edges_world_fn(work):
        if (pi2, panel_i2) == (page_idx, coma_idx):
            continue
        unit2 = _edge_unit(a2, b2)
        if unit2 is None:
            continue
        if abs(abs(unit2[0] * ux + unit2[1] * uy) - 1.0) > 0.05:
            continue
        d1 = (a2[0] - vertex_world[0]) * nx + (a2[1] - vertex_world[1]) * ny
        d2 = (b2[0] - vertex_world[0]) * nx + (b2[1] - vertex_world[1]) * ny
        if min(abs(d1), abs(d2), abs((d1 + d2) * 0.5)) > gap_tolerance_mm * 2.5:
            continue
        t1 = (a2[0] - vertex_world[0]) * ux + (a2[1] - vertex_world[1]) * uy
        t2 = (b2[0] - vertex_world[0]) * ux + (b2[1] - vertex_world[1]) * uy
        if min(abs(t1), abs(t2)) > 30.0:
            continue
        out.append((pi2, panel_i2, ei2))
    return out


def _edge_world_line(
    poly: list[tuple[float, float]],
    edge_idx: int,
    offset: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    ox, oy = offset
    return (
        (poly[edge_idx][0] + ox, poly[edge_idx][1] + oy),
        (
            poly[(edge_idx + 1) % len(poly)][0] + ox,
            poly[(edge_idx + 1) % len(poly)][1] + oy,
        ),
    )


def capture_extended_vertex_edge_states(
    work,
    page_idx: int,
    coma_idx: int,
    vertex_idx: int,
    poly: list[tuple[float, float]],
    seen: set[tuple[int, int, int, int]],
    *,
    page_offset_fn: Callable,
    coma_polygon_fn: Callable,
    all_edges_world_fn: Callable,
    edge_projection_params_fn: Callable,
    gap_tolerance_mm: float,
) -> list[dict]:
    out: list[dict] = []
    if len(poly) < 3:
        return out
    offset = page_offset_fn(work, page_idx)
    for selected_edge in ((vertex_idx - 1) % len(poly), vertex_idx):
        sel_a, sel_b = _edge_world_line(poly, selected_edge, offset)
        candidates = find_extended_vertex_adjacent_edges(
            work,
            page_idx,
            coma_idx,
            selected_edge,
            vertex_idx,
            poly,
            page_offset_fn=page_offset_fn,
            all_edges_world_fn=all_edges_world_fn,
            gap_tolerance_mm=gap_tolerance_mm,
        )
        for pi2, panel_i2, ei2 in candidates:
            key = (selected_edge, pi2, panel_i2, ei2)
            if key in seen:
                continue
            seen.add(key)
            poly2 = coma_polygon_fn(work.pages[pi2].comas[panel_i2])
            state = _extended_edge_state(
                page_offset_fn,
                edge_projection_params_fn,
                work,
                pi2,
                panel_i2,
                ei2,
                poly2,
                sel_a,
                sel_b,
                selected_edge,
            )
            if state is not None:
                out.append(state)
    return out


def _extended_edge_state(
    page_offset_fn: Callable,
    edge_projection_params_fn: Callable,
    work,
    page_idx: int,
    coma_idx: int,
    edge_idx: int,
    poly: Iterable[tuple[float, float]],
    sel_a: tuple[float, float],
    sel_b: tuple[float, float],
    selected_edge: int,
) -> dict | None:
    poly2 = list(poly)
    if len(poly2) < 3:
        return None
    ox2, oy2 = page_offset_fn(work, page_idx)
    adj_a = (poly2[edge_idx][0] + ox2, poly2[edge_idx][1] + oy2)
    adj_b = (
        poly2[(edge_idx + 1) % len(poly2)][0] + ox2,
        poly2[(edge_idx + 1) % len(poly2)][1] + oy2,
    )
    params = edge_projection_params_fn(sel_a, sel_b, adj_a, adj_b)
    if params is None:
        return None
    return {
        "selected_edge": selected_edge,
        "page": page_idx,
        "coma": coma_idx,
        "edge": edge_idx,
        "poly": poly2,
        "params": params,
    }
