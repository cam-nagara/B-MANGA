"""B-MANGA Line — 頂点解析（AO焼き付け・エッジ角度ウェイト計算）.

複数の情報源（手動頂点カラー・AO・エッジ角度）を合成して
最終的な頂点グループウェイトを生成する。
"""

from __future__ import annotations

import hashlib
import math

import bmesh
import bpy

from .core import (
    AO_ATTR_NAME,
    COLOR_ATTR_NAME,
    VG_LINE_WIDTH,
)


# ------------------------------------------------------------------
# SubSurf 適用
# ------------------------------------------------------------------

_ANCHOR_PROP = "bml_subsurf_anchors"


def _apply_subsurf_for_midpoint(obj) -> set[int]:
    """SubSurfがあれば適用して頂点密度を確保.

    適用前にベースメッシュのアンカー頂点（≥90°エッジ）を検出し、
    カスタムプロパティに保存。SubSurf後はエッジ角度が平滑化
    されるため、適用前の情報が必要。
    戻り値: アンカー頂点インデックスのset（SubSurfなしなら空set）
    """
    subsurf_names = [
        m.name for m in obj.modifiers
        if m.type == "SUBSURF" and (m.show_viewport or m.show_render)
    ]
    if not subsurf_names:
        return set()

    threshold = math.pi / 2 - 0.001
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
    return base_anchors


def _get_saved_anchors(obj) -> set[int] | None:
    """カスタムプロパティに保存されたアンカーインデックスを取得."""
    raw = obj.get(_ANCHOR_PROP)
    if raw is not None:
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


def _write_vertex_group_weights(
    obj: bpy.types.Object,
    weights: list[float],
) -> int:
    """線幅用頂点グループへウェイトを書き込む."""
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    if vg is None:
        vg = obj.vertex_groups.new(name=VG_LINE_WIDTH)
    for i, value in enumerate(weights):
        vg.add([i], _clamp01(value), "REPLACE")
    return len(weights)


def reset_width_weights(obj: bpy.types.Object, value: float = 1.0) -> int:
    """線幅用頂点グループを均一値に戻す."""
    if obj.type != "MESH" or obj.data is None:
        return 0
    count = len(obj.data.vertices)
    if count == 0:
        return 0
    return _write_vertex_group_weights(obj, [_clamp01(value)] * count)


def multiply_width_weights(
    obj: bpy.types.Object,
    multipliers: list[float],
) -> int:
    """既存の線幅ウェイトへ追加倍率を掛けて書き戻す."""
    if obj.type != "MESH" or obj.data is None:
        return 0
    count = len(obj.data.vertices)
    if count == 0:
        return 0
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    weights = []
    for i in range(count):
        base = 1.0
        if vg is not None:
            try:
                base = vg.weight(i)
            except RuntimeError:
                base = 1.0
        mult = multipliers[i] if i < len(multipliers) else 1.0
        weights.append(base * mult)
    return _write_vertex_group_weights(obj, weights)


def _curve_points_from_settings(settings) -> tuple[float, float, float]:
    return (
        _clamp01(getattr(settings, "edge_width_curve_25", 0.25)),
        _clamp01(getattr(settings, "edge_width_curve_50", 0.50)),
        _clamp01(getattr(settings, "edge_width_curve_75", 0.75)),
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
) -> dict[int, float]:
    """鋭角アンカー頂点間の中間度を計算.

    forced_anchors が指定された場合、エッジ角度検出をスキップして
    そのインデックスをアンカーとして使用する（SubSurf適用後に使用）。
    戻り値: {vertex_index: midpoint_factor}
    0.0 = アンカー頂点（鋭角）, 1.0 = アンカー間の最も遠い中間点
    """
    threshold = math.pi / 2 - 0.001

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    n = len(bm.verts)
    sharp_neighbors = _build_sharp_graph(bm, threshold)

    graph_anchors = {
        i for i, neighbors in enumerate(sharp_neighbors)
        if neighbors and len(neighbors) != 2
    }
    if forced_anchors:
        graph_anchors |= {
            i for i in forced_anchors
            if i < n and sharp_neighbors[i]
        }

    if not graph_anchors:
        bm.free()
        return {i: (0.0 if sharp_neighbors[i] else 1.0) for i in range(n)}

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

    bm.free()
    return result


# ------------------------------------------------------------------
# 統合ウェイト計算
# ------------------------------------------------------------------

def compute_and_apply_weights(obj, settings) -> int:
    """全ソースから最終頂点ウェイトを計算して頂点グループに書き込み.

    合成順序:
    1. 手動頂点カラー（BML_LineWidth）の明度 → ベースウェイト
    2. AO（BML_AO）の暗さ → 暗い部分ほど太い線
    3. エッジ角度 → 鋭角 vs 平坦部の線幅差
    """
    if obj.type != "MESH":
        return 0

    mesh = obj.data
    n = len(mesh.vertices)
    if n == 0:
        return 0

    weights = [1.0] * n

    # 1. 手動頂点カラー
    if settings.use_vertex_color:
        attr = mesh.color_attributes.get(COLOR_ATTR_NAME)
        if attr is not None and attr.domain == "POINT":
            for i in range(min(n, len(attr.data))):
                c = attr.data[i].color
                weights[i] = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    # 2. AO
    if settings.use_ao_influence:
        attr_ao = mesh.color_attributes.get(AO_ATTR_NAME)
        if attr_ao is not None and attr_ao.domain == "POINT":
            strength = settings.ao_influence_strength
            for i in range(min(n, len(attr_ao.data))):
                c = attr_ao.data[i].color
                ao_lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
                ao_thick = 1.0 - ao_lum
                weights[i] = weights[i] * (1.0 - strength) + ao_thick * strength

    # 3. 中間頂点の線幅調整
    factor = settings.edge_smooth_factor
    if abs(factor) > 0.001:
        base_anchors = _apply_subsurf_for_midpoint(obj) or _get_saved_anchors(obj)
        if base_anchors:
            mesh = obj.data
            n = len(mesh.vertices)
            weights = [1.0] * n
            if settings.use_vertex_color:
                attr = mesh.color_attributes.get(COLOR_ATTR_NAME)
                if attr is not None and attr.domain == "POINT":
                    for i in range(min(n, len(attr.data))):
                        c = attr.data[i].color
                        weights[i] = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
            if settings.use_ao_influence:
                attr_ao = mesh.color_attributes.get(AO_ATTR_NAME)
                if attr_ao is not None and attr_ao.domain == "POINT":
                    strength = settings.ao_influence_strength
                    for i in range(min(n, len(attr_ao.data))):
                        c = attr_ao.data[i].color
                        ao_lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
                        ao_thick = 1.0 - ao_lum
                        weights[i] = weights[i] * (1.0 - strength) + ao_thick * strength
        jitter = getattr(settings, "edge_midpoint_jitter_percent", 0.0)
        curve_points = _curve_points_from_settings(settings)
        midpoint = _calc_midpoint_factor(
            obj, base_anchors or None, jitter, curve_points,
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

    # 頂点グループに書き込み
    return _write_vertex_group_weights(obj, weights)


# ------------------------------------------------------------------
# AO 焼き付け
# ------------------------------------------------------------------

def bake_ao(context, obj) -> bool:
    """Cycles で AO を頂点カラーに焼き付け. 成功時 True."""
    if obj.type != "MESH":
        return False

    mesh = obj.data

    attr = mesh.color_attributes.get(AO_ATTR_NAME)
    if attr is None:
        attr = mesh.color_attributes.new(
            name=AO_ATTR_NAME, type="FLOAT_COLOR", domain="POINT"
        )

    mesh.color_attributes.active_color = attr
    context.view_layer.objects.active = obj

    prev_engine = context.scene.render.engine
    context.scene.render.engine = "CYCLES"

    prev_samples = context.scene.cycles.samples
    context.scene.cycles.samples = 32

    try:
        result = bpy.ops.object.bake(type="AO", target="VERTEX_COLORS")
        return result == {"FINISHED"}
    except RuntimeError:
        return False
    finally:
        context.scene.render.engine = prev_engine
        context.scene.cycles.samples = prev_samples
