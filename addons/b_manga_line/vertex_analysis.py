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
# エッジ角度解析
# ------------------------------------------------------------------

def _calc_vertex_sharpness(obj) -> dict[int, float]:
    """各頂点の鋭角度を計算.

    戻り値: {vertex_index: sharpness}
    sharpness = 0.0 (平坦) 〜 1.0 (90° 以上の鋭角)
    """
    threshold = math.pi / 2  # 90° = 直方体の角

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    result = {}
    for vert in bm.verts:
        max_angle = 0.0
        for edge in vert.link_edges:
            if len(edge.link_faces) >= 2:
                try:
                    angle = edge.calc_face_angle()
                    max_angle = max(max_angle, angle)
                except ValueError:
                    pass
        result[vert.index] = min(1.0, max_angle / threshold)

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

    # 3. エッジ角度
    factor = settings.edge_smooth_factor
    if abs(factor) > 0.001:
        sharpness = _calc_vertex_sharpness(obj)
        for i in range(n):
            s = sharpness.get(i, 0.0)
            if factor >= 0:
                # 正: 鋭角頂点を細くして平坦部を相対的に太くする
                edge_mult = 1.0 - s * factor
            else:
                # 負: 平坦頂点を細くする
                edge_mult = 1.0 - (1.0 - s) * abs(factor)
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
