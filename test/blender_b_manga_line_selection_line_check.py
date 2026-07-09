"""B-MANGA Line: Freestyle-marked edges generate selection lines."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    aov_compositor,
    core,
    outline_setup,
    selection_lines,
    vertex_analysis,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _make_marked_bent_edges(name: str) -> bpy.types.Object:
    verts = (
        (-1.5, 0.0, 0.0),
        (-0.5, 0.0, 0.0),
        (-0.5, 1.0, 0.0),
        (0.5, 1.0, 0.0),
    )
    edges = ((0, 1), (1, 2), (2, 3))
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    freestyle_attr = mesh.attributes.new("freestyle_edge", "BOOLEAN", "EDGE")
    for item in freestyle_attr.data:
        item.value = True
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def _modifier_input(mod, socket_name: str):
    tree = mod.node_group
    assert tree is not None
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == socket_name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return mod[item.identifier]
    raise AssertionError(f"{socket_name} socket not found")


def _chain_ids(obj: bpy.types.Object) -> list[int]:
    attr = obj.data.attributes.get(core.SELECTION_LINE_CHAIN_ID_ATTR)
    assert attr is not None, "選択線チェーンID属性がありません"
    return [int(attr.data[index].value) for index in range(len(obj.data.edges))]


def _assert_selection_material_aov(mat: bpy.types.Material) -> None:
    assert mat.use_nodes and mat.node_tree is not None
    aov_names = {
        getattr(node, "aov_name", "")
        for node in mat.node_tree.nodes
        if getattr(node, "aov_name", "")
    }
    assert core.AOV_SELECTION_LINES_NAME in aov_names, aov_names


def _assert_compositor_accepts_selection_aov(scene: bpy.types.Scene) -> None:
    outline_setup.ensure_aov_passes(scene)
    tree = aov_compositor.setup_line_aov_compositor(scene)
    group = next(
        node for node in tree.nodes
        if node.name == f"{aov_compositor.NODE_PREFIX}_Group"
    )
    assert group.inputs.get(core.AOV_SELECTION_LINES_NAME) is not None
    assert group.node_tree is not None
    gin = group.node_tree.nodes.get(f"{aov_compositor.NODE_PREFIX}_GroupInput")
    assert gin is not None
    assert gin.outputs.get(core.AOV_SELECTION_LINES_NAME) is not None


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        obj = _make_marked_bent_edges("BML_selection_line")
        settings = obj.bmanga_line_settings
        settings.selection_line_enabled = True
        settings.selection_edge_smooth_factor = -0.9
        settings.selection_edge_midpoint_jitter_percent = 0.0
        settings.selection_edge_midpoint_angle = math.radians(80.0)
        mat = outline_setup.get_line_material(obj, "selection")
        assert mat is not None

        assert selection_lines.apply_selection_lines(
            obj,
            angle=settings.selection_line_angle,
            thickness=settings.selection_line_thickness,
            offset=settings.selection_line_offset,
            material=mat,
            midpoint_factor=settings.selection_edge_smooth_factor,
            midpoint_angle=settings.selection_edge_midpoint_angle,
            midpoint_jitter_percent=settings.selection_edge_midpoint_jitter_percent,
            width_curve_25=settings.selection_edge_width_curve_25,
            width_curve_50=settings.selection_edge_width_curve_50,
            width_curve_75=settings.selection_edge_width_curve_75,
        )
        mod = obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME)
        assert mod is not None, "選択線モディファイアがありません"
        assert _modifier_input(mod, "Freestyleマーク辺だけ線にする") is True

        freestyle_attr = obj.data.attributes.get(core.FREESTYLE_EDGE_ATTR)
        assert freestyle_attr is not None, "Freestyleマーク辺属性がありません"
        assert [bool(item.value) for item in freestyle_attr.data] == [True, True, True]

        assert len(set(_chain_ids(obj))) == 1, _chain_ids(obj)
        settings.selection_edge_midpoint_angle = math.radians(100.0)
        assert selection_lines.update_parameters(
            obj,
            midpoint_angle=settings.selection_edge_midpoint_angle,
        )
        assert len(set(_chain_ids(obj))) == 3, _chain_ids(obj)

        vertex_analysis.compute_and_apply_weights(obj, settings, target="selection")
        assert obj.data.attributes.get(core.VG_SELECTION_LINE_WIDTH) is not None
        assert obj.vertex_groups.get(core.VG_SELECTION_LINE_WIDTH) is None
        _assert_selection_material_aov(mat)
        _assert_compositor_accepts_selection_aov(bpy.context.scene)

        print("[PASS] Freestyle marked edges generate selection lines")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
