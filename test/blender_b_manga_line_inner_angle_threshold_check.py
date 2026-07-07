"""Blender実機用: 内部線の検出角度が60度の辺を含み、キャップ三角分割を拾わないことを確認."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    inner_line_cache,
    inner_line_chains,
    inner_lines,
    outline_setup,
    subdivision_lod,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _new_cylinder(name: str, vertices: int) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=1.0,
        depth=2.0,
        end_fill_type="TRIFAN",
    )
    obj = bpy.context.object
    obj.name = name
    return obj


def _new_cone(name: str, vertices: int) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cone_add(
        vertices=vertices,
        radius1=1.0,
        radius2=0.0,
        depth=2.0,
        end_fill_type="TRIFAN",
    )
    obj = bpy.context.object
    obj.name = name
    return obj


def _edge_selection_sets(obj: bpy.types.Object) -> tuple[set[int], set[int], set[int]]:
    attr = obj.data.attributes.get(inner_line_chains.CHAIN_ID_ATTR)
    assert attr is not None
    selected = {
        i for i, item in enumerate(attr.data)
        if int(getattr(item, "value", 0)) > 0
    }
    vertical = set()
    cap_spokes = set()
    for edge in obj.data.edges:
        v1, v2 = edge.vertices
        co1 = obj.data.vertices[v1].co
        co2 = obj.data.vertices[v2].co
        if abs(co1.z - co2.z) > 1.5:
            vertical.add(edge.index)
        elif abs(co1.z - co2.z) < 1.0e-6 and (
            co1.xy.length < 0.1 or co2.xy.length < 0.1
        ):
            cap_spokes.add(edge.index)
    return selected, vertical, cap_spokes


def _assert_hex_60_degree_edges_are_selected() -> None:
    obj = _new_cylinder("hex_prism_60_degree_check", 6)
    inner_line_chains.update_chain_id_attribute(obj, math.radians(60.0), False)
    selected, vertical, cap_spokes = _edge_selection_sets(obj)
    assert vertical, "六角柱の縦辺を検出できません"
    assert vertical <= selected, f"60度の縦辺が内部線対象になっていません: {vertical - selected}"
    assert not (cap_spokes & selected), f"上下面の三角分割辺が内部線対象です: {cap_spokes & selected}"

    count = subdivision_lod.mark_sharp_edges_for_subsurf(obj, math.radians(60.0))
    attr = obj.data.attributes.get(subdivision_lod.CREASE_EDGE_ATTR)
    assert attr is not None
    creased = {
        edge.index for edge in obj.data.edges
        if edge.index < len(attr.data) and float(attr.data[edge.index].value) > 0.0
    }
    assert count >= len(vertical)
    assert vertical <= creased, f"60度の縦辺にサブディビジョン保持が入りません: {vertical - creased}"


def _assert_cylinder_cap_spokes_are_not_selected() -> None:
    obj = _new_cylinder("cylinder_cap_spoke_check", 32)
    inner_line_chains.update_chain_id_attribute(obj, math.radians(60.0), False)
    selected, _vertical, cap_spokes = _edge_selection_sets(obj)
    assert cap_spokes, "円柱の上下面三角分割辺を検出できません"
    assert not (cap_spokes & selected), f"円柱上下面の三角分割辺が内部線対象です: {cap_spokes & selected}"


def _assert_cone_cap_spokes_are_not_selected() -> None:
    obj = _new_cone("cone_cap_spoke_check", 32)
    inner_line_chains.update_chain_id_attribute(obj, math.radians(60.0), False)
    selected, _vertical, cap_spokes = _edge_selection_sets(obj)
    assert cap_spokes, "円錐の底面三角分割辺を検出できません"
    assert not (cap_spokes & selected), f"円錐底面の三角分割辺が内部線対象です: {cap_spokes & selected}"


def _assert_round_rim_loops_keep_polygon_edges() -> None:
    for maker, label in ((_new_cylinder, "円柱"), (_new_cone, "円錐")):
        obj = maker(f"{label}_rim_chain_check", 32)
        inner_line_chains.update_chain_id_attribute(obj, math.radians(60.0), False)
        attr = obj.data.attributes[inner_line_chains.CHAIN_ID_ATTR]
        chain_edges: dict[int, int] = {}
        for edge in obj.data.edges:
            value = int(attr.data[edge.index].value)
            if value < 0:
                continue
            v1, v2 = edge.vertices
            co1 = obj.data.vertices[v1].co
            co2 = obj.data.vertices[v2].co
            same_z = abs(co1.z - co2.z) < 1.0e-6
            near_center = co1.xy.length < 0.1 or co2.xy.length < 0.1
            if same_z and not near_center:
                chain_edges[value] = chain_edges.get(value, 0) + 1
        assert chain_edges, f"{label}の円周内部線を検出できません"
        count = subdivision_lod.line_resample_count(obj)
        assert 1 <= count <= 8, f"{label}の内部線分割数が辺ごと補間として過大です: {count}"
        mat = outline_setup.get_line_material(obj, "inner")
        assert inner_lines.apply_inner_lines(
            obj,
            angle=math.radians(60.0),
            thickness=0.01,
            material=mat,
        )
        mod = obj.modifiers[inner_lines.GN_MODIFIER_NAME]
        tree = mod.node_group
        assert inner_line_cache.is_cached_modifier(mod), f"{label}の内部線が保存済み線方式ではありません"
        cache = bpy.data.objects.get(str(obj.get(inner_line_cache.CACHE_OBJECT_PROP, "") or ""))
        assert cache is not None and len(cache.data.edges) >= sum(chain_edges.values()), (
            f"{label}の稜谷線キャッシュが不足しています",
            len(cache.data.edges) if cache and cache.data else None,
            chain_edges,
        )
        assert any(
            getattr(node, "label", "") == inner_line_cache._SUBDIVIDE_LABEL
            and node.bl_idname == "GeometryNodeSubdivideCurve"
            for node in tree.nodes
        ), f"{label}の保存済み稜谷線が表示時に辺上分割されていません"
        assert not any(
            node.bl_idname == "GeometryNodeResampleCurve"
            for node in tree.nodes
        ), f"{label}の内部線に形状を変える再サンプルが残っています"
        _clear_scene()


def _assert_saved_current_tree_refreshes_stale_cap_selection() -> None:
    obj = _new_cone("inner_stale_chain_refresh_check", 16)
    settings = obj.bmanga_line_settings
    settings.inner_line_angle = math.radians(60.0)
    mat = outline_setup.get_line_material(obj, "inner")
    assert inner_lines.apply_inner_lines(
        obj,
        angle=settings.inner_line_angle,
        thickness=0.01,
        material=mat,
    )
    selected, _vertical, cap_spokes = _edge_selection_sets(obj)
    assert cap_spokes, "円錐の底面三角分割辺を検出できません"
    assert not (cap_spokes & selected), selected

    attr = obj.data.attributes[inner_line_chains.CHAIN_ID_ATTR]
    for edge_index in cap_spokes:
        attr.data[edge_index].value = 1
    selected, _vertical, cap_spokes = _edge_selection_sets(obj)
    assert cap_spokes & selected, "テスト用の古い三角分割選択を作れません"

    cache = bpy.data.objects.get(str(obj.get(inner_line_cache.CACHE_OBJECT_PROP, "") or ""))
    assert cache is not None
    edge_count = len(cache.data.edges)
    assert inner_lines.repair_scene_inner_lines(bpy.context.scene) == 0
    assert len(cache.data.edges) == edge_count, "現行の保存済み稜谷線が修復で作り直されています"


def _assert_node_tree_uses_inclusive_angle() -> None:
    obj = _new_cylinder("inner_node_rebuild_check", 6)
    mat = outline_setup.get_line_material(obj, "inner")
    assert inner_lines.apply_inner_lines(
        obj,
        angle=math.radians(60.0),
        thickness=0.01,
        material=mat,
        midpoint_factor=-1.0,
        midpoint_jitter_percent=37.5,
    )
    tree = obj.modifiers[inner_lines.GN_MODIFIER_NAME].node_group
    assert inner_line_cache.is_cached_modifier(obj.modifiers[inner_lines.GN_MODIFIER_NAME])
    assert any(
        getattr(node, "label", "") == inner_line_cache._SUBDIVIDE_LABEL
        and node.bl_idname == "GeometryNodeSubdivideCurve"
        for node in tree.nodes
    )
    assert not any(
        node.bl_idname == "GeometryNodeResampleCurve"
        for node in tree.nodes
    )
    assert any(
        getattr(node, "label", "") == "BML_InnerCachedJitterCenter"
        for node in tree.nodes
    )
    sid = inner_line_cache._find_socket_id(tree, inner_line_cache._MIDPOINT_JITTER_SOCKET)
    assert sid is not None
    assert abs(float(obj.modifiers[inner_lines.GN_MODIFIER_NAME][sid]) - 37.5) < 1.0e-6


def _assert_stale_inner_tree_is_repaired() -> None:
    obj = _new_cylinder("inner_stale_tree_repair_check", 12)
    settings = obj.bmanga_line_settings
    settings.inner_line_angle = math.radians(60.0)
    settings.inner_line_thickness = 0.01
    settings.inner_line_offset = 1.0
    settings.inner_edge_smooth_factor = -1.0
    settings.inner_edge_midpoint_jitter_percent = 25.0
    settings.inner_edge_width_curve_25 = 0.1
    settings.inner_edge_width_curve_50 = 0.2
    settings.inner_edge_width_curve_75 = 0.3
    stale = bpy.data.node_groups.get(inner_lines.GN_TREE_NAME)
    if stale is not None:
        bpy.data.node_groups.remove(stale)
    stale = bpy.data.node_groups.new(inner_lines.GN_TREE_NAME, "GeometryNodeTree")
    mod = obj.modifiers.new(inner_lines.GN_MODIFIER_NAME, "NODES")
    mod.node_group = stale
    mod.show_viewport = False
    mod.show_render = True

    assert inner_lines.repair_scene_inner_lines(bpy.context.scene) == 1
    repaired = obj.modifiers[inner_lines.GN_MODIFIER_NAME]
    assert repaired.node_group != stale
    assert not repaired.show_viewport
    assert repaired.show_render
    assert inner_line_cache.is_cached_modifier(repaired)
    sid = inner_line_cache._find_socket_id(
        repaired.node_group,
        inner_line_cache._MIDPOINT_JITTER_SOCKET,
    )
    assert sid is not None
    assert abs(float(repaired[sid]) - 25.0) < 1.0e-6


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        _assert_hex_60_degree_edges_are_selected()
        _clear_scene()
        _assert_cylinder_cap_spokes_are_not_selected()
        _clear_scene()
        _assert_cone_cap_spokes_are_not_selected()
        _clear_scene()
        _assert_round_rim_loops_keep_polygon_edges()
        _clear_scene()
        _assert_saved_current_tree_refreshes_stale_cap_selection()
        _clear_scene()
        _assert_node_tree_uses_inclusive_angle()
        _clear_scene()
        _assert_stale_inner_tree_is_repaired()
        print("[PASS] inner line angle threshold includes 60 degrees and ignores cap spokes")
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
