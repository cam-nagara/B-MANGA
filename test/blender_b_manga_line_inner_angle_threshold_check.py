"""Blender実機用: 内部線の検出角度が60度の辺を含み、キャップ三角分割を拾わないことを確認."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import inner_line_chains, inner_lines, outline_setup, subdivision_lod  # noqa: E402


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
        if int(getattr(item, "value", -1)) >= 0
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


def _assert_round_rim_loops_are_not_resampled_as_triangles() -> None:
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
        rim_edge_count = max(chain_edges.values())
        count = subdivision_lod.line_resample_count(obj)
        assert count >= min(96, rim_edge_count + 1), (
            f"{label}の円周が低分割で三角形化します: count={count}, rim={rim_edge_count}"
        )
        _clear_scene()


def _assert_saved_current_tree_refreshes_stale_cap_selection() -> None:
    obj = _new_cone("inner_stale_chain_refresh_check", 16)
    settings = obj.bmanga_line_settings
    settings.inner_line_angle = math.radians(60.0)
    settings.use_marked_inner_edges = False
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
        attr.data[edge_index].value = 0
    selected, _vertical, cap_spokes = _edge_selection_sets(obj)
    assert cap_spokes & selected, "テスト用の古い三角分割選択を作れません"

    assert inner_lines.repair_scene_inner_lines(bpy.context.scene) == 0
    selected, _vertical, cap_spokes = _edge_selection_sets(obj)
    assert not (cap_spokes & selected), f"保存済みの古い三角分割選択が残っています: {cap_spokes & selected}"


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
    compare = next(
        node for node in tree.nodes
        if getattr(node, "label", "") == inner_lines._EDGE_ANGLE_COMPARE_LABEL
    )
    assert compare.operation == "GREATER_EQUAL"
    chain_compare = next(
        node for node in tree.nodes
        if getattr(node, "label", "") == inner_lines._CHAIN_SELECTION_COMPARE_LABEL
    )
    assert chain_compare.operation == "GREATER_EQUAL"
    selection_switch = next(
        node for node in tree.nodes
        if getattr(node, "label", "") == inner_lines._MARKED_SELECTION_SWITCH_LABEL
    )
    false_links = list(selection_switch.inputs["False"].links)
    assert false_links, "内部線の自動選択入力が未接続です"
    assert false_links[0].from_node == chain_compare
    assert any(
        getattr(node, "label", "") == inner_lines._CURVE_JITTER_CENTER_LABEL
        for node in tree.nodes
    )
    sid = inner_lines._find_socket_id(tree, inner_lines._MIDPOINT_JITTER_SOCKET_NAME)
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
    assert any(
        getattr(node, "label", "") == inner_lines._CHAIN_SELECTION_COMPARE_LABEL
        for node in repaired.node_group.nodes
    )
    sid = inner_lines._find_socket_id(
        repaired.node_group,
        inner_lines._MIDPOINT_JITTER_SOCKET_NAME,
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
        _assert_round_rim_loops_are_not_resampled_as_triangles()
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
