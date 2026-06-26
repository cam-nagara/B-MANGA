"""B-MANGA Line — 頂点解析（AO焼き付け・エッジ角度ウェイト計算）.

複数の情報源（手動頂点カラー・AO・エッジ角度）を合成して
最終的な頂点グループウェイトを生成する。
"""

from __future__ import annotations

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

def _calc_midpoint_factor(obj, forced_anchors: set[int] | None = None) -> dict[int, float]:
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
    sharp_neighbors: list[set[int]] = [set() for _ in range(n)]
    for edge in bm.edges:
        is_sharp = False
        if len(edge.link_faces) >= 2:
            try:
                is_sharp = edge.calc_face_angle() >= threshold
            except ValueError:
                is_sharp = False
        elif len(edge.link_faces) == 1:
            is_sharp = True
        if is_sharp:
            v1 = edge.verts[0].index
            v2 = edge.verts[1].index
            sharp_neighbors[v1].add(v2)
            sharp_neighbors[v2].add(v1)

    graph_anchors = {
        i for i, neighbors in enumerate(sharp_neighbors)
        if neighbors and len(neighbors) != 2
    }
    if not graph_anchors and forced_anchors:
        graph_anchors = {i for i in forced_anchors if i < n}

    if not graph_anchors:
        bm.free()
        return {i: 0.0 for i in range(n)}

    from collections import deque
    result = {i: 0.0 for i in range(n)}
    seen: set[int] = set()
    for start in range(n):
        if start in seen or not sharp_neighbors[start]:
            continue
        component: set[int] = set()
        stack = [start]
        seen.add(start)
        while stack:
            vi = stack.pop()
            component.add(vi)
            for other in sharp_neighbors[vi]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)

        anchors = component & graph_anchors
        if not anchors:
            continue

        dist = {vi: n for vi in component}
        queue = deque()
        for vi in anchors:
            dist[vi] = 0
            queue.append(vi)

        while queue:
            vi = queue.popleft()
            for other in sharp_neighbors[vi]:
                if other not in component:
                    continue
                if dist[vi] + 1 < dist[other]:
                    dist[other] = dist[vi] + 1
                    queue.append(other)

        max_dist = max((d for d in dist.values() if d < n), default=0)
        if max_dist <= 0:
            continue
        for vi, d in dist.items():
            if d < n:
                result[vi] = d / max_dist

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
        midpoint = _calc_midpoint_factor(obj, base_anchors or None)
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
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    if vg is None:
        vg = obj.vertex_groups.new(name=VG_LINE_WIDTH)
    for i in range(n):
        vg.add([i], max(0.0, min(1.0, weights[i])), "REPLACE")

    return n


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
