"""B-MANGA Line — 頂点解析（エッジ角度ウェイト計算）.

複数の情報源（手動頂点カラー・エッジ角度）を合成して
最終的な頂点グループウェイトを生成する。
"""

from __future__ import annotations

import hashlib
import math

import bmesh
import bpy
import numpy as np

from .core import (
    COLOR_ATTR_NAME,
    VG_INNER_LINE_WIDTH,
    VG_INTERSECTION_LINE_WIDTH,
    VG_LINE_WIDTH,
    VG_SELECTION_LINE_WIDTH,
)


# ------------------------------------------------------------------
# SubSurf 適用
# ------------------------------------------------------------------

_ANCHOR_PROP = "bml_subsurf_anchors"
_ANCHOR_THRESHOLD_PROP = "bml_subsurf_anchor_threshold"


def _apply_subsurf_for_midpoint(obj, threshold: float) -> set[int]:
    """SubSurfがあれば適用して頂点密度を確保.

    適用前にベースメッシュのアンカー頂点（検出角度未満のパス角）を検出し、
    カスタムプロパティに保存。SubSurf後はエッジ角度が平滑化
    されるため、適用前の情報が必要。
    戻り値: アンカー頂点インデックスのset（SubSurfなしなら空set）
    """
    from . import subdivision_lod

    subsurf_names = [
        m.name for m in obj.modifiers
        if (
            m.type == "SUBSURF"
            and (m.show_viewport or m.show_render)
            and not subdivision_lod.is_auto_subsurf_modifier(m)
        )
    ]
    if not subsurf_names:
        return set()

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    base_anchors = set()
    for vert in bm.verts:
        for edge in vert.link_edges:
            if len(edge.link_faces) >= 2:
                try:
                    if edge.calc_face_angle() >= threshold:
                        base_anchors.add(vert.index)
                        break
                except ValueError:
                    pass
    bm.free()

    disabled = {}
    for m in obj.modifiers:
        if m.type != "SUBSURF" and m.show_viewport:
            disabled[m.name] = True
            m.show_viewport = False

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        new_mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    finally:
        for name in disabled:
            m = obj.modifiers.get(name)
            if m:
                m.show_viewport = True

    old_mesh = obj.data
    old_name = old_mesh.name
    obj.data = new_mesh
    new_mesh.name = old_name
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    for name in subsurf_names:
        m = obj.modifiers.get(name)
        if m:
            obj.modifiers.remove(m)

    obj[_ANCHOR_PROP] = list(base_anchors)
    obj[_ANCHOR_THRESHOLD_PROP] = float(threshold)
    return base_anchors


def _get_saved_anchors(obj, threshold: float | None = None) -> set[int] | None:
    """カスタムプロパティに保存されたアンカーインデックスを取得."""
    raw = obj.get(_ANCHOR_PROP)
    if raw is not None:
        saved_threshold = obj.get(_ANCHOR_THRESHOLD_PROP)
        if threshold is not None and saved_threshold is not None:
            if abs(float(saved_threshold) - float(threshold)) > 1.0e-6:
                return None
        return set(int(i) for i in raw)
    return None


# ------------------------------------------------------------------
# エッジ角度解析
# ------------------------------------------------------------------

def _edge_is_sharp(edge, threshold: float) -> bool:
    if len(edge.link_faces) >= 2:
        try:
            return edge.calc_face_angle() >= threshold
        except ValueError:
            return False
    return len(edge.link_faces) == 1


def _edge_angle(edge) -> float | None:
    if len(edge.link_faces) < 2:
        return None
    try:
        return float(edge.calc_face_angle())
    except ValueError:
        return None


def _build_sharp_graph(bm, threshold: float) -> list[set[int]]:
    sharp_neighbors: list[set[int]] = [set() for _ in range(len(bm.verts))]
    for edge in bm.edges:
        if not _edge_is_sharp(edge, threshold):
            continue
        v1 = edge.verts[0].index
        v2 = edge.verts[1].index
        sharp_neighbors[v1].add(v2)
        sharp_neighbors[v2].add(v1)
    return sharp_neighbors


def _hard_endpoint_anchors(
    bm,
    threshold: float,
    hard_endpoint_angle: float | None,
) -> set[int]:
    if hard_endpoint_angle is None or hard_endpoint_angle <= 0.0:
        return set()
    anchors: set[int] = set()
    for vert in bm.verts:
        directions = []
        for edge in vert.link_edges:
            if not _edge_is_sharp(edge, threshold):
                continue
            other = edge.other_vert(vert)
            direction = other.co - vert.co
            if direction.length <= 1.0e-8:
                continue
            directions.append(direction.normalized())
        if len(directions) != 2:
            continue
        path_angle = directions[0].angle(directions[1], math.pi)
        if path_angle + 1.0e-7 < hard_endpoint_angle:
            anchors.add(vert.index)
    return anchors


def _stable_random_01(obj, chain: list[int]) -> float:
    start = obj.data.vertices[chain[0]].co
    end = obj.data.vertices[chain[-1]].co
    chain_key = ",".join(str(i) for i in chain)
    payload = (
        f"{obj.name}|{chain_key}|"
        f"{start.x:.6f},{start.y:.6f},{start.z:.6f}|"
        f"{end.x:.6f},{end.y:.6f},{end.z:.6f}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _trace_anchor_chain(
    sharp_neighbors: list[set[int]],
    anchors: set[int],
    start: int,
    next_vert: int,
) -> list[int]:
    chain = [start, next_vert]
    prev = start
    current = next_vert
    while current not in anchors and len(sharp_neighbors[current]) == 2:
        candidates = [vi for vi in sharp_neighbors[current] if vi != prev]
        if not candidates:
            break
        prev, current = current, candidates[0]
        chain.append(current)
    return chain


def _iter_anchor_chains(
    sharp_neighbors: list[set[int]],
    anchors: set[int],
) -> list[list[int]]:
    chains: list[list[int]] = []
    visited_edges: set[tuple[int, int]] = set()
    for start in sorted(anchors):
        for next_vert in sorted(sharp_neighbors[start]):
            edge_key = tuple(sorted((start, next_vert)))
            if edge_key in visited_edges:
                continue
            chain = _trace_anchor_chain(sharp_neighbors, anchors, start, next_vert)
            for i in range(len(chain) - 1):
                visited_edges.add(tuple(sorted((chain[i], chain[i + 1]))))
            if len(chain) >= 3 and chain[-1] in anchors:
                chains.append(chain)
    return chains


def _trace_closed_loop(
    sharp_neighbors: list[set[int]],
    start: int,
    visited_edges: set[tuple[int, int]],
) -> list[int]:
    if len(sharp_neighbors[start]) != 2:
        return []
    chain = [start]
    prev = -1
    current = start
    while True:
        candidates = sorted(sharp_neighbors[current])
        if len(candidates) != 2:
            return []
        next_vert = candidates[0] if candidates[0] != prev else candidates[1]
        edge_key = tuple(sorted((current, next_vert)))
        if next_vert == start:
            visited_edges.add(edge_key)
            return chain if len(chain) >= 4 else []
        if edge_key in visited_edges or next_vert in chain:
            return []
        visited_edges.add(edge_key)
        prev, current = current, next_vert
        chain.append(current)


def _iter_closed_loops(
    sharp_neighbors: list[set[int]],
    anchors: set[int],
) -> list[list[int]]:
    loops: list[list[int]] = []
    visited_edges: set[tuple[int, int]] = set()
    for start in range(len(sharp_neighbors)):
        if start in anchors or len(sharp_neighbors[start]) != 2:
            continue
        if all(tuple(sorted((start, nxt))) in visited_edges for nxt in sharp_neighbors[start]):
            continue
        loop = _trace_closed_loop(sharp_neighbors, start, visited_edges)
        if not loop:
            continue
        if any(vertex in anchors for vertex in loop):
            continue
        loops.append(loop)
    return loops


def _chain_positions(obj, chain: list[int]) -> dict[int, float]:
    distances = [0.0]
    total = 0.0
    vertices = obj.data.vertices
    for i in range(1, len(chain)):
        total += (vertices[chain[i]].co - vertices[chain[i - 1]].co).length
        distances.append(total)
    if total <= 1e-8:
        return {vi: 0.0 for vi in chain}
    return {vi: distances[i] / total for i, vi in enumerate(chain)}


def _camera_projected_x(obj, vertex_index: int) -> float | None:
    scene = getattr(bpy.context, "scene", None)
    camera = getattr(scene, "camera", None) if scene is not None else None
    if camera is None:
        return None
    try:
        from bpy_extras.object_utils import world_to_camera_view

        world = obj.matrix_world @ obj.data.vertices[vertex_index].co
        projected = world_to_camera_view(scene, camera, world)
        return float(projected.x)
    except Exception:  # noqa: BLE001
        return None


def _loop_endpoint_pair(obj, loop: list[int]) -> tuple[int, int]:
    projected = [(vi, _camera_projected_x(obj, vi)) for vi in loop]
    projected = [(vi, x) for vi, x in projected if x is not None]
    if len(projected) >= 2:
        left = min(projected, key=lambda item: item[1])[0]
        right = max(projected, key=lambda item: item[1])[0]
        if left != right:
            return left, right

    vertices = obj.data.vertices
    left = min(loop, key=lambda vi: vertices[vi].co.x)
    right = max(loop, key=lambda vi: vertices[vi].co.x)
    if left != right:
        return left, right
    return loop[0], loop[len(loop) // 2]


def _cycle_path(loop: list[int], start_index: int, end_index: int, step: int) -> list[int]:
    path = [loop[start_index]]
    index = start_index
    guard = 0
    while index != end_index and guard <= len(loop):
        index = (index + step) % len(loop)
        path.append(loop[index])
        guard += 1
    return path if path[-1] == loop[end_index] else []


def _select_midpoint_vertex(
    obj,
    chain: list[int],
    positions: dict[int, float],
    jitter_percent: float,
    used_position_bins: set[int] | None = None,
) -> tuple[int, float]:
    internal = chain[1:-1]
    if not internal:
        center = chain[len(chain) // 2]
        return center, positions.get(center, 0.5)

    jitter = max(0.0, min(50.0, float(jitter_percent))) / 100.0
    target = 0.5
    if jitter > 0.0:
        target += (_stable_random_01(obj, chain) * 2.0 - 1.0) * jitter
    low = 0.5 - jitter
    high = 0.5 + jitter

    candidates = [
        vi for vi in internal
        if low - 1e-8 <= positions.get(vi, 0.5) <= high + 1e-8
    ]
    if not candidates:
        candidates = internal
    if jitter > 0.0 and used_position_bins is not None:
        unused = [
            vi for vi in candidates
            if _position_bin(positions.get(vi, 0.5)) not in used_position_bins
        ]
        if unused:
            candidates = unused
    selected = min(candidates, key=lambda vi: abs(positions.get(vi, 0.5) - target))
    selected_position = positions.get(selected, 0.5)
    if jitter > 0.0 and used_position_bins is not None:
        used_position_bins.add(_position_bin(selected_position))
    return selected, selected_position


def _position_bin(position: float) -> int:
    return int(round(max(0.0, min(1.0, position)) * 1000000.0))


def _chain_factor(position: float, midpoint: float) -> float:
    if midpoint <= 1e-8:
        return 1.0 - position
    if midpoint >= 1.0 - 1e-8:
        return position
    if position <= midpoint:
        return position / midpoint
    return (1.0 - position) / (1.0 - midpoint)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_target(target: str) -> str:
    if target in {"inner", "intersection", "selection"}:
        return target
    return "outline"


def width_group_name(target: str = "outline") -> str:
    """線種ごとの線幅用頂点グループ名を返す."""
    target = _normalize_target(target)
    if target == "inner":
        return VG_INNER_LINE_WIDTH
    if target == "intersection":
        return VG_INTERSECTION_LINE_WIDTH
    if target == "selection":
        return VG_SELECTION_LINE_WIDTH
    return VG_LINE_WIDTH


def _is_generated_width_name(group_name: str) -> bool:
    return group_name in {
        VG_INNER_LINE_WIDTH,
        VG_INTERSECTION_LINE_WIDTH,
        VG_SELECTION_LINE_WIDTH,
    }


def _remove_vertex_group(obj: bpy.types.Object, group_name: str) -> bool:
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return False
    obj.vertex_groups.remove(vg)
    return True


def _ensure_float_point_attribute(
    obj: bpy.types.Object,
    name: str,
) -> bpy.types.Attribute | None:
    if obj.type != "MESH" or obj.data is None:
        return None
    mesh = obj.data
    attr = mesh.attributes.get(name)
    if (
        attr is not None
        and getattr(attr, "domain", None) == "POINT"
        and getattr(attr, "data_type", None) == "FLOAT"
    ):
        return attr
    if attr is not None:
        mesh.attributes.remove(attr)
    return mesh.attributes.new(name, "FLOAT", "POINT")


def _write_float_point_attribute(
    obj: bpy.types.Object,
    weights,
    name: str,
) -> int:
    attr = _ensure_float_point_attribute(obj, name)
    if attr is None:
        return 0
    count = min(len(obj.data.vertices), len(weights))
    if count <= 0:
        _remove_vertex_group(obj, name)
        return 0
    values = np.asarray(weights, dtype=np.float32)
    if values.size < len(obj.data.vertices):
        filled = np.ones(len(obj.data.vertices), dtype=np.float32)
        if values.size:
            filled[:values.size] = values
        values = filled
    values = np.clip(values[:len(obj.data.vertices)], 0.0, 1.0)
    attr.data.foreach_set("value", values)
    obj.data.update()
    _remove_vertex_group(obj, name)
    return len(obj.data.vertices)


def _vertex_group_weights(
    obj: bpy.types.Object,
    group_name: str,
    default: float = 1.0,
) -> list[float]:
    count = len(obj.data.vertices)
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return [_clamp01(default)] * count
    values = []
    fallback = _clamp01(default)
    for i in range(count):
        try:
            values.append(_clamp01(vg.weight(i)))
        except RuntimeError:
            values.append(fallback)
    return values


def migrate_width_storage(obj: bpy.types.Object, group_name: str) -> bool:
    """生成線幅の旧頂点グループを同名FLOAT属性へ移す."""
    if obj.type != "MESH" or obj.data is None or not _is_generated_width_name(group_name):
        return False
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return False
    attr = obj.data.attributes.get(group_name)
    if (
        attr is None
        or getattr(attr, "domain", None) != "POINT"
        or getattr(attr, "data_type", None) != "FLOAT"
    ):
        _write_float_point_attribute(obj, _vertex_group_weights(obj, group_name), group_name)
        return True
    _remove_vertex_group(obj, group_name)
    return True


def ensure_generated_width_storage(obj: bpy.types.Object, group_name: str) -> None:
    """生成線の線幅保存先を属性へ寄せ、旧頂点グループを残さない."""
    if obj.type != "MESH" or obj.data is None or not _is_generated_width_name(group_name):
        return
    migrate_width_storage(obj, group_name)


def stored_width_weight(
    obj: bpy.types.Object,
    group_name: str,
    vertex_index: int,
    default: float = 1.0,
) -> float:
    if obj.type != "MESH" or obj.data is None:
        return _clamp01(default)
    if _is_generated_width_name(group_name):
        attr = obj.data.attributes.get(group_name)
        if (
            attr is not None
            and getattr(attr, "domain", None) == "POINT"
            and getattr(attr, "data_type", None) == "FLOAT"
            and 0 <= vertex_index < len(attr.data)
        ):
            return _clamp01(attr.data[vertex_index].value)
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return _clamp01(default)
    try:
        return _clamp01(vg.weight(vertex_index))
    except RuntimeError:
        return _clamp01(default)


def stored_width_weights(
    obj: bpy.types.Object,
    target: str = "outline",
    default: float = 1.0,
) -> list[float]:
    group_name = width_group_name(target)
    if obj.type != "MESH" or obj.data is None:
        return []
    return [
        stored_width_weight(obj, group_name, i, default)
        for i in range(len(obj.data.vertices))
    ]


def has_width_controls(settings, target: str = "outline") -> bool:
    """線種別の中間頂点・色による線幅制御が有効か返す."""
    target = _normalize_target(target)
    if target == "inner":
        return abs(getattr(settings, "inner_edge_smooth_factor", 0.0)) > 0.001
    if target == "intersection":
        return abs(getattr(settings, "intersection_edge_smooth_factor", 0.0)) > 0.001
    if target == "selection":
        return abs(getattr(settings, "selection_edge_smooth_factor", 0.0)) > 0.001
    return (
        bool(getattr(settings, "use_vertex_color", False))
        or abs(getattr(settings, "edge_smooth_factor", 0.0)) > 0.001
    )


def _target_midpoint_props(settings, target: str) -> tuple[float, float, tuple[float, float, float]]:
    target = _normalize_target(target)
    if target == "inner":
        return (
            float(getattr(settings, "inner_edge_smooth_factor", 0.0)),
            float(getattr(settings, "inner_edge_midpoint_jitter_percent", 0.0)),
            _curve_points_from_settings(settings, target),
        )
    if target == "intersection":
        return (
            float(getattr(settings, "intersection_edge_smooth_factor", 0.0)),
            float(getattr(settings, "intersection_edge_midpoint_jitter_percent", 0.0)),
            _curve_points_from_settings(settings, target),
        )
    if target == "selection":
        return (
            float(getattr(settings, "selection_edge_smooth_factor", 0.0)),
            float(getattr(settings, "selection_edge_midpoint_jitter_percent", 0.0)),
            _curve_points_from_settings(settings, target),
        )
    return (
        float(getattr(settings, "edge_smooth_factor", 0.0)),
        float(getattr(settings, "edge_midpoint_jitter_percent", 0.0)),
        _curve_points_from_settings(settings, target),
    )


def _angle_threshold(settings, target: str = "outline") -> float:
    target = _normalize_target(target)
    if target == "inner":
        return max(
            0.0,
            float(getattr(settings, "inner_line_angle", math.radians(60.0))),
        )
    elif target == "intersection":
        prop_name = "intersection_edge_midpoint_angle"
    elif target == "selection":
        prop_name = "selection_edge_midpoint_angle"
    else:
        prop_name = "edge_midpoint_angle"
    fallback = getattr(settings, "inner_line_angle", math.pi / 2)
    return max(0.0, float(getattr(settings, prop_name, fallback)))


def _line_graph_threshold(settings, target: str = "outline") -> float:
    normalized = _normalize_target(target)
    if normalized == "inner":
        return max(0.0, float(getattr(settings, "inner_line_angle", math.radians(60.0))))
    if normalized == "selection":
        return max(0.0, float(getattr(settings, "selection_line_angle", math.radians(60.0))))
    return math.radians(60.0)


def _write_vertex_group_weights(
    obj: bpy.types.Object,
    weights: list[float],
    group_name: str = VG_LINE_WIDTH,
) -> int:
    """線幅用頂点グループへウェイトを書き込む."""
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        vg = obj.vertex_groups.new(name=group_name)
    for i, value in enumerate(weights):
        vg.add([i], _clamp01(value), "REPLACE")
    return len(weights)


def write_width_weights(
    obj: bpy.types.Object,
    weights,
    target: str = "outline",
) -> int:
    """線幅ウェイトを線種に応じた保存先へ書き込む."""
    group_name = width_group_name(target)
    if _is_generated_width_name(group_name):
        return _write_float_point_attribute(obj, weights, group_name)
    return _write_vertex_group_weights(obj, list(weights), group_name)


def reset_width_weights(
    obj: bpy.types.Object,
    value: float = 1.0,
    group_name: str = VG_LINE_WIDTH,
) -> int:
    """線幅用頂点グループを均一値に戻す."""
    if obj.type != "MESH" or obj.data is None:
        return 0
    count = len(obj.data.vertices)
    if count == 0:
        return 0
    if _is_generated_width_name(group_name):
        return _write_float_point_attribute(obj, [_clamp01(value)] * count, group_name)
    return _write_vertex_group_weights(obj, [_clamp01(value)] * count, group_name)


def clear_width_weights(
    obj: bpy.types.Object,
    group_name: str = VG_LINE_WIDTH,
) -> bool:
    """線幅用頂点グループを使わない状態に戻す."""
    if obj.type != "MESH":
        return False
    if _is_generated_width_name(group_name):
        removed = _remove_vertex_group(obj, group_name)
        attr = obj.data.attributes.get(group_name) if obj.data is not None else None
        if attr is not None:
            obj.data.attributes.remove(attr)
            removed = True
        return removed
    vg = obj.vertex_groups.get(group_name)
    if vg is None:
        return False
    obj.vertex_groups.remove(vg)
    return True


def _curve_points_from_settings(settings, target: str = "outline") -> tuple[float, float, float]:
    target = _normalize_target(target)
    if target == "inner":
        names = (
            "inner_edge_width_curve_25",
            "inner_edge_width_curve_50",
            "inner_edge_width_curve_75",
        )
    elif target == "intersection":
        names = (
            "intersection_edge_width_curve_25",
            "intersection_edge_width_curve_50",
            "intersection_edge_width_curve_75",
        )
    elif target == "selection":
        names = (
            "selection_edge_width_curve_25",
            "selection_edge_width_curve_50",
            "selection_edge_width_curve_75",
        )
    else:
        names = (
            "edge_width_curve_25",
            "edge_width_curve_50",
            "edge_width_curve_75",
        )
    return (
        _clamp01(getattr(settings, names[0], 0.25)),
        _clamp01(getattr(settings, names[1], 0.50)),
        _clamp01(getattr(settings, names[2], 0.75)),
    )


def _apply_width_curve(
    progress: float,
    curve_points: tuple[float, float, float] | None,
) -> float:
    if curve_points is None:
        return _clamp01(progress)

    points = (
        (0.00, 0.00),
        (0.25, curve_points[0]),
        (0.50, curve_points[1]),
        (0.75, curve_points[2]),
        (1.00, 1.00),
    )
    t = _clamp01(progress)
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if t <= x1 + 1e-8:
            span = x1 - x0
            local = 0.0 if span <= 1e-8 else (t - x0) / span
            return _clamp01(y0 + (y1 - y0) * local)
    return 1.0


def _calc_midpoint_factor(
    obj,
    forced_anchors: set[int] | None = None,
    jitter_percent: float = 0.0,
    curve_points: tuple[float, float, float] | None = None,
    threshold: float = math.pi / 2,
    hard_endpoint_angle: float | None = None,
    use_branch_anchors: bool = True,
) -> dict[int, float]:
    """鋭角アンカー頂点間の中間度を計算.

    forced_anchors が指定された場合、エッジ角度検出をスキップして
    そのインデックスをアンカーとして使用する（SubSurf適用後に使用）。
    戻り値: {vertex_index: midpoint_factor}
    0.0 = アンカー頂点（鋭角）, 1.0 = アンカー間の最も遠い中間点
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    n = len(bm.verts)
    sharp_neighbors = _build_sharp_graph(bm, threshold)

    if use_branch_anchors:
        graph_anchors = {
            i for i, neighbors in enumerate(sharp_neighbors)
            if neighbors and len(neighbors) != 2
        }
    else:
        graph_anchors = {
            i for i, neighbors in enumerate(sharp_neighbors)
            if len(neighbors) == 1
        }
    graph_anchors |= _hard_endpoint_anchors(bm, threshold, hard_endpoint_angle)
    if forced_anchors:
        graph_anchors |= {
            i for i in forced_anchors
            if i < n and sharp_neighbors[i]
        }

    result = {i: 0.0 for i in range(n)}
    used_midpoint_bins: set[int] = set()
    for chain in _iter_anchor_chains(sharp_neighbors, graph_anchors):
        positions = _chain_positions(obj, chain)
        _, midpoint = _select_midpoint_vertex(
            obj, chain, positions, jitter_percent, used_midpoint_bins,
        )
        for vi in chain:
            raw = _chain_factor(positions[vi], midpoint)
            value = _apply_width_curve(raw, curve_points)
            result[vi] = max(result[vi], value)

    for loop in _iter_closed_loops(sharp_neighbors, graph_anchors):
        start, end = _loop_endpoint_pair(obj, loop)
        try:
            start_index = loop.index(start)
            end_index = loop.index(end)
        except ValueError:
            continue
        for chain in (
            _cycle_path(loop, start_index, end_index, 1),
            _cycle_path(loop, start_index, end_index, -1),
        ):
            if len(chain) < 3:
                continue
            positions = _chain_positions(obj, chain)
            _, midpoint = _select_midpoint_vertex(
                obj, chain, positions, jitter_percent, used_midpoint_bins,
            )
            for vi in chain:
                raw = _chain_factor(positions[vi], midpoint)
                value = _apply_width_curve(raw, curve_points)
                result[vi] = max(result[vi], value)

    bm.free()
    return result


# ------------------------------------------------------------------
# 統合ウェイト計算
# ------------------------------------------------------------------

def _base_weights(obj, settings, n: int, *, use_color: bool) -> list[float]:
    mesh = obj.data
    weights = [1.0] * n

    if use_color:
        attr = mesh.color_attributes.get(COLOR_ATTR_NAME)
        if attr is not None and attr.domain == "POINT":
            for i in range(min(n, len(attr.data))):
                c = attr.data[i].color
                weights[i] = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    return weights


def compute_weights(obj, settings, target: str = "outline") -> list[float] | None:
    """全ソースから最終頂点ウェイトを計算する.

    合成順序:
    1. 手動頂点カラー（BML_LineWidth）の明度 → ベースウェイト
    2. 検出角度未満で分割した線ごとの両端と中心 → 中間頂点の線幅差
    """
    if obj.type != "MESH":
        return None

    mesh = obj.data
    n = len(mesh.vertices)
    if n == 0:
        return None

    target = _normalize_target(target)
    use_color = target == "outline" and settings.use_vertex_color
    weights = _base_weights(obj, settings, n, use_color=use_color)

    # 3. 中間頂点の線幅調整
    factor, jitter, curve_points = _target_midpoint_props(settings, target)
    if abs(factor) > 0.001:
        split_threshold = _angle_threshold(settings, target)
        graph_threshold = _line_graph_threshold(settings, target)
        before_mesh = obj.data
        before_count = n
        base_anchors = _apply_subsurf_for_midpoint(obj, graph_threshold)
        saved_anchors = _get_saved_anchors(obj, graph_threshold)
        base_anchors = base_anchors or saved_anchors
        if obj.data is not before_mesh or len(obj.data.vertices) != before_count:
            mesh = obj.data
            n = len(mesh.vertices)
            weights = _base_weights(obj, settings, n, use_color=use_color)
        midpoint = _calc_midpoint_factor(
            obj,
            base_anchors or None,
            jitter,
            curve_points,
            graph_threshold,
            split_threshold,
            use_branch_anchors=True,
        )
        for i in range(n):
            m = midpoint.get(i, 0.0)
            if factor >= 0:
                # 正: アンカー(鋭角)を細くし中間部を相対的に太く見せる
                edge_mult = 1.0 - (1.0 - m) * factor
            else:
                # 負: 中間部を細くしアンカー(鋭角)を太いまま残す
                edge_mult = 1.0 - m * abs(factor)
            weights[i] = max(0.0, min(1.0, weights[i] * edge_mult))

    return weights


def compute_and_apply_weights(obj, settings, target: str = "outline") -> int:
    """全ソースから最終頂点ウェイトを計算して頂点グループに書き込み."""
    weights = compute_weights(obj, settings, target)
    if not weights:
        return 0
    return write_width_weights(obj, weights, target)
