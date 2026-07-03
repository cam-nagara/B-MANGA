"""Blender実機用: 内部線の検出角度が60度の辺を含み、キャップ三角分割を拾わないことを確認."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import inner_line_chains, inner_lines, outline_setup  # noqa: E402


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


def _assert_node_tree_uses_inclusive_angle() -> None:
    obj = _new_cylinder("inner_node_rebuild_check", 6)
    mat = outline_setup.get_line_material(obj, "inner")
    assert inner_lines.apply_inner_lines(
        obj,
        angle=math.radians(60.0),
        thickness=0.01,
        material=mat,
    )
    tree = obj.modifiers[inner_lines.GN_MODIFIER_NAME].node_group
    compare = next(
        node for node in tree.nodes
        if getattr(node, "label", "") == inner_lines._EDGE_ANGLE_COMPARE_LABEL
    )
    assert compare.operation == "GREATER_EQUAL"


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        _assert_hex_60_degree_edges_are_selected()
        _clear_scene()
        _assert_cylinder_cap_spokes_are_not_selected()
        _clear_scene()
        _assert_node_tree_uses_inclusive_angle()
        print("[PASS] inner line angle threshold includes 60 degrees and ignores cap spokes")
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
