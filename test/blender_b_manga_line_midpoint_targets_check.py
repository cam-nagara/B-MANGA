"""B-MANGA Line: midpoint width settings are independent per line type."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, inner_line_chains, inner_lines, panels, vertex_analysis  # noqa: E402


LEVELS = 9


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_folded_strip(name: str) -> bpy.types.Object:
    verts = []
    faces = []
    for i in range(LEVELS):
        x = i / (LEVELS - 1) * 4.0 - 2.0
        verts.extend(((x, -0.5, 0.0), (x, 0.0, 0.35), (x, 0.5, 0.0)))
    for i in range(LEVELS - 1):
        current = i * 3
        nxt = (i + 1) * 3
        faces.append((current, nxt, nxt + 1, current + 1))
        faces.append((current + 1, nxt + 1, nxt + 2, current + 2))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(bpy.data.materials.new(name + "_surface"))
    return obj


def _make_segmented_box(name: str) -> bpy.types.Object:
    verts = []
    faces = []
    for i in range(LEVELS):
        x = i / (LEVELS - 1) * 4.0 - 2.0
        verts.extend(
            (
                (x, -0.5, -0.5),
                (x, 0.5, -0.5),
                (x, 0.5, 0.5),
                (x, -0.5, 0.5),
            )
        )
    for i in range(LEVELS - 1):
        current = i * 4
        nxt = (i + 1) * 4
        faces.extend(
            (
                (current, nxt, nxt + 1, current + 1),
                (current + 1, nxt + 1, nxt + 2, current + 2),
                (current + 2, nxt + 2, nxt + 3, current + 3),
                (current + 3, nxt + 3, nxt, current),
            )
        )
    faces.append((0, 1, 2, 3))
    last = (LEVELS - 1) * 4
    faces.append((last, last + 3, last + 2, last + 1))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(bpy.data.materials.new(name + "_surface"))
    return obj


def _make_subdivided_rectangle(name: str) -> bpy.types.Object:
    verts = (
        (-1.0, -0.7, 0.0),
        (0.0, -0.7, 0.0),
        (1.0, -0.7, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.7, 0.0),
        (0.0, 0.7, 0.0),
        (-1.0, 0.7, 0.0),
        (-1.0, 0.0, 0.0),
    )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [tuple(range(len(verts)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(bpy.data.materials.new(name + "_surface"))
    return obj


def _make_t_branch_graph(name: str) -> bpy.types.Object:
    verts = (
        (0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 2.0, 0.0),
        (-1.0, 0.0, 0.0),
        (-2.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
    )
    edges = (
        (0, 1),
        (1, 2),
        (0, 3),
        (3, 4),
        (0, 5),
        (5, 6),
    )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _make_bent_graph(name: str) -> bpy.types.Object:
    verts = (
        (-2.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 2.0, 0.0),
    )
    edges = (
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
    )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _make_acute_graph(name: str) -> bpy.types.Object:
    d = math.sqrt(0.5)
    verts = (
        (-2.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (-d, d, 0.0),
        (-2.0 * d, 2.0 * d, 0.0),
    )
    edges = (
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
    )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _make_diamond_loop_graph(name: str) -> bpy.types.Object:
    verts = (
        (0.0, -0.85, 0.0),
        (0.32, 0.0, 0.0),
        (0.0, 0.85, 0.0),
        (-0.32, 0.0, 0.0),
    )
    edges = (
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
    )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _center_ridge_vertex() -> int:
    return (LEVELS // 2) * 3 + 1


def _center_box_corner_vertex() -> int:
    return (LEVELS // 2) * 4 + 3


def _weight(obj: bpy.types.Object, group_name: str, vertex_index: int) -> float:
    vg = obj.vertex_groups.get(group_name)
    assert vg is not None, f"{group_name} がありません"
    return vg.weight(vertex_index)


def _reset_all_groups(obj: bpy.types.Object) -> None:
    for name in (
        core.VG_LINE_WIDTH,
        core.VG_INNER_LINE_WIDTH,
        core.VG_INTERSECTION_LINE_WIDTH,
    ):
        vertex_analysis.reset_width_weights(obj, group_name=name)


def _mark_all_edges(obj: bpy.types.Object) -> None:
    attr = obj.data.attributes.get(inner_line_chains.SHARP_EDGE_ATTR)
    if attr is None:
        attr = obj.data.attributes.new(
            inner_line_chains.SHARP_EDGE_ATTR,
            "BOOLEAN",
            "EDGE",
        )
    for item in attr.data:
        item.value = True


def _edge_chain_ids(obj: bpy.types.Object) -> list[int]:
    attr = obj.data.attributes.get(inner_line_chains.CHAIN_ID_ATTR)
    assert attr is not None, "内部線チェーンIDがありません"
    return [int(item.value) for item in attr.data]


class _DummyLayout:
    enabled = True
    alignment = ""

    def column(self, *args, **kwargs):
        return self

    def row(self, *args, **kwargs):
        return self

    def prop(self, *args, **kwargs):
        return None

    def label(self, *args, **kwargs):
        return None

    def separator(self, *args, **kwargs):
        return None


def _assert_inner_midpoint_angle_is_hidden() -> None:
    obj = _make_subdivided_rectangle("BML_inner_midpoint_angle_hidden")
    settings = obj.bmanga_line_settings
    captured = []
    real_draw = panels._draw_midpoint_width_controls
    try:
        panels._draw_midpoint_width_controls = lambda *args, **_kwargs: captured.append(args)
        panels._draw_inner_line(
            _DummyLayout(),
            SimpleNamespace(scene=bpy.context.scene),
            settings,
        )
    finally:
        panels._draw_midpoint_width_controls = real_draw
    assert captured, "内部線の線幅詳細が描画されていません"
    assert captured[0][-1] is None, "内部線の線幅詳細に旧検出角度が表示されています"


def _set_detection_angle(settings, target: str, angle_prop: str, value: float) -> None:
    if target == "inner":
        settings.inner_line_angle = value
    else:
        setattr(settings, angle_prop, value)


def _assert_target_only(target: str, group_name: str, factor_prop: str) -> None:
    obj = _make_subdivided_rectangle("BML_midpoint_" + target)
    settings = obj.bmanga_line_settings
    angle_prop = {
        "outline": "edge_midpoint_angle",
        "inner": "inner_line_angle",
        "intersection": "intersection_edge_midpoint_angle",
    }[target]
    _set_detection_angle(settings, target, angle_prop, math.radians(100.0))
    _reset_all_groups(obj)
    setattr(settings, factor_prop, -1.0)

    vertex_analysis.compute_and_apply_weights(obj, settings, target)
    center = 1
    assert _weight(obj, group_name, center) < 0.001

    untouched = {
        core.VG_LINE_WIDTH,
        core.VG_INNER_LINE_WIDTH,
        core.VG_INTERSECTION_LINE_WIDTH,
    } - {group_name}
    for other_name in untouched:
        assert _weight(obj, other_name, center) > 0.999, (target, other_name)


def _assert_outline_uses_detection_angle() -> None:
    obj = _make_bent_graph("BML_midpoint_outline_angle")
    settings = obj.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    bend_vertex = 2
    real_edge_is_sharp = vertex_analysis._edge_is_sharp

    try:
        vertex_analysis._edge_is_sharp = lambda _edge, _threshold: True
        settings.inner_line_angle = math.radians(100.0)
        settings.edge_midpoint_angle = math.radians(60.0)
        vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
        assert _weight(obj, core.VG_LINE_WIDTH, bend_vertex) < 0.001

        settings.inner_line_angle = math.radians(60.0)
        settings.edge_midpoint_angle = math.radians(100.0)
        vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
        assert _weight(obj, core.VG_LINE_WIDTH, bend_vertex) > 0.999
    finally:
        vertex_analysis._edge_is_sharp = real_edge_is_sharp


def _assert_rectangle_corners_are_outline_endpoints() -> None:
    obj = _make_subdivided_rectangle("BML_midpoint_outline_rectangle_endpoints")
    settings = obj.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    settings.edge_midpoint_angle = math.radians(100.0)

    vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
    corners = (0, 2, 4, 6)
    side_centers = (1, 3, 5, 7)
    assert all(_weight(obj, core.VG_LINE_WIDTH, vi) > 0.999 for vi in corners)
    assert all(_weight(obj, core.VG_LINE_WIDTH, vi) < 0.05 for vi in side_centers)


def _assert_cylinder_rims_use_view_endpoints() -> None:
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=1.0)
    obj = bpy.context.object
    obj.name = "BML_midpoint_outline_cylinder_endpoints"
    settings = obj.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    settings.edge_midpoint_angle = math.radians(100.0)

    vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
    top_vertices = [v for v in obj.data.vertices if v.co.z > 0.49]
    assert top_vertices, "円柱上面の頂点がありません"
    x_endpoint = max(top_vertices, key=lambda v: abs(v.co.x)).index
    y_midpoint = max(top_vertices, key=lambda v: abs(v.co.y)).index
    assert _weight(obj, core.VG_LINE_WIDTH, x_endpoint) > 0.95
    assert _weight(obj, core.VG_LINE_WIDTH, y_midpoint) < 0.05


def _assert_generated_target_uses_detection_angle(
    target: str,
    group_name: str,
    factor_prop: str,
    angle_prop: str,
) -> None:
    obj = _make_bent_graph("BML_midpoint_angle_" + target)
    settings = obj.bmanga_line_settings
    setattr(settings, factor_prop, -1.0)
    bend_vertex = 2
    real_edge_is_sharp = vertex_analysis._edge_is_sharp

    try:
        vertex_analysis._edge_is_sharp = lambda _edge, _threshold: True
        if target == "inner":
            settings.inner_edge_midpoint_angle = math.radians(100.0)
        settings.inner_line_angle = math.radians(60.0)
        _set_detection_angle(settings, target, angle_prop, math.radians(60.0))
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
        assert _weight(obj, group_name, bend_vertex) < 0.001

        settings.inner_line_angle = math.radians(60.0)
        _set_detection_angle(settings, target, angle_prop, math.radians(100.0))
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
        assert _weight(obj, group_name, bend_vertex) > 0.999
    finally:
        vertex_analysis._edge_is_sharp = real_edge_is_sharp


def _assert_closed_cylinder_rim_uses_camera_view_endpoints() -> None:
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=1.0)
    obj = bpy.context.object
    obj.name = "BML_midpoint_closed_rim"
    settings = obj.bmanga_line_settings
    settings.inner_edge_smooth_factor = -1.0
    settings.inner_line_angle = math.radians(60.0)

    vertex_analysis.compute_and_apply_weights(obj, settings, "inner")
    top_vertices = [v for v in obj.data.vertices if v.co.z > 0.49]
    assert top_vertices, "円柱上面の頂点がありません"
    x_endpoint = max(top_vertices, key=lambda v: abs(v.co.x)).index
    y_midpoint = max(top_vertices, key=lambda v: abs(v.co.y)).index

    assert _weight(obj, core.VG_INNER_LINE_WIDTH, x_endpoint) > 0.95
    assert _weight(obj, core.VG_INNER_LINE_WIDTH, y_midpoint) < 0.05


def _assert_branch_vertices_are_endpoints(
    target: str,
    group_name: str,
    factor_prop: str,
) -> None:
    obj = _make_t_branch_graph("BML_midpoint_branch_" + target)
    settings = obj.bmanga_line_settings
    setattr(settings, factor_prop, -1.0)
    real_edge_is_sharp = vertex_analysis._edge_is_sharp
    try:
        # 交差線生成後の線グラフ相当を検査するため、面を持たない辺も
        # このテスト内では中間頂点判定対象として扱う。
        vertex_analysis._edge_is_sharp = lambda _edge, _threshold: True
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
    finally:
        vertex_analysis._edge_is_sharp = real_edge_is_sharp

    branch_vertex = 0
    mid_vertices = (1, 3, 5)
    endpoints = (2, 4, 6)
    assert _weight(obj, group_name, branch_vertex) > 0.999
    assert all(_weight(obj, group_name, vi) < 0.001 for vi in mid_vertices)
    assert all(_weight(obj, group_name, vi) > 0.999 for vi in endpoints)


def _assert_bent_path_uses_detection_angle(
    target: str,
    group_name: str,
    factor_prop: str,
    angle_prop: str,
) -> None:
    obj = _make_bent_graph("BML_midpoint_bent_" + target)
    settings = obj.bmanga_line_settings
    setattr(settings, factor_prop, -1.0)
    _set_detection_angle(settings, target, angle_prop, math.radians(100.0))
    real_edge_is_sharp = vertex_analysis._edge_is_sharp
    try:
        vertex_analysis._edge_is_sharp = lambda _edge, _threshold: True
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
    finally:
        vertex_analysis._edge_is_sharp = real_edge_is_sharp

    bend_vertex = 2
    segment_centers = (1, 3)
    endpoints = (0, 4)
    assert _weight(obj, group_name, bend_vertex) > 0.999
    assert all(_weight(obj, group_name, vi) > 0.999 for vi in endpoints)
    assert all(_weight(obj, group_name, vi) < 0.001 for vi in segment_centers)


def _assert_acute_path_splits_below_detection_angle(
    target: str,
    group_name: str,
    factor_prop: str,
    angle_prop: str,
) -> None:
    obj = _make_acute_graph("BML_midpoint_acute_" + target)
    settings = obj.bmanga_line_settings
    setattr(settings, factor_prop, -1.0)
    _set_detection_angle(settings, target, angle_prop, math.radians(100.0))
    real_edge_is_sharp = vertex_analysis._edge_is_sharp
    try:
        vertex_analysis._edge_is_sharp = lambda _edge, _threshold: True
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
    finally:
        vertex_analysis._edge_is_sharp = real_edge_is_sharp

    split_vertex = 2
    endpoints = (0, 4)
    segment_centers = (1, 3)
    assert _weight(obj, group_name, split_vertex) > 0.999
    assert all(_weight(obj, group_name, vi) > 0.999 for vi in endpoints)
    assert all(_weight(obj, group_name, vi) < 0.001 for vi in segment_centers)


def _assert_diamond_loop_splits_only_acute_points(
    target: str,
    group_name: str,
    factor_prop: str,
    angle_prop: str,
) -> None:
    obj = _make_diamond_loop_graph("BML_midpoint_diamond_" + target)
    settings = obj.bmanga_line_settings
    setattr(settings, factor_prop, -1.0)
    _set_detection_angle(settings, target, angle_prop, math.radians(100.0))
    real_edge_is_sharp = vertex_analysis._edge_is_sharp
    try:
        vertex_analysis._edge_is_sharp = lambda _edge, _threshold: True
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
    finally:
        vertex_analysis._edge_is_sharp = real_edge_is_sharp

    acute_points = (0, 2)
    obtuse_centers = (1, 3)
    assert all(_weight(obj, group_name, vi) > 0.999 for vi in acute_points)
    assert all(_weight(obj, group_name, vi) < 0.001 for vi in obtuse_centers)


def _assert_inner_chain_ids_split_at_branch_and_acute_points() -> None:
    branch = _make_t_branch_graph("BML_inner_chain_branch_split")
    _mark_all_edges(branch)
    inner_line_chains.update_chain_id_attribute(
        branch,
        math.radians(60.0),
        True,
        math.radians(100.0),
    )
    ids = _edge_chain_ids(branch)
    assert len(set(ids)) == 3, ids
    assert ids[0] == ids[1], ids
    assert ids[2] == ids[3], ids
    assert ids[4] == ids[5], ids

    diamond = _make_diamond_loop_graph("BML_inner_chain_diamond_split")
    _mark_all_edges(diamond)
    inner_line_chains.update_chain_id_attribute(
        diamond,
        math.radians(60.0),
        True,
        math.radians(100.0),
    )
    ids = _edge_chain_ids(diamond)
    assert len(set(ids)) == 2, ids
    assert ids[0] == ids[1], ids
    assert ids[2] == ids[3], ids
    assert ids[0] != ids[2], ids


def _assert_inner_repair_preserves_midpoint_angle_split() -> None:
    obj = _make_diamond_loop_graph("BML_inner_repair_midpoint_angle_split")
    _mark_all_edges(obj)
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.use_marked_inner_edges = True
    settings.inner_edge_smooth_factor = -1.0
    settings.inner_line_angle = math.radians(100.0)
    settings.inner_edge_midpoint_angle = math.radians(60.0)
    material = bpy.data.materials.new("BML_inner_repair_midpoint_angle_split_material")
    assert inner_lines.apply_inner_lines(
        obj,
        angle=settings.inner_line_angle,
        thickness=0.01,
        material=material,
        use_marked_edges=settings.use_marked_inner_edges,
        midpoint_factor=settings.inner_edge_smooth_factor,
    )

    attr = obj.data.attributes.get(inner_line_chains.CHAIN_ID_ATTR)
    assert attr is not None
    for item in attr.data:
        item.value = 0
    assert len(set(_edge_chain_ids(obj))) == 1

    assert inner_lines.repair_scene_inner_lines(bpy.context.scene) == 0
    ids = _edge_chain_ids(obj)
    assert len(set(ids)) == 2, ids
    assert ids[0] == ids[1], ids
    assert ids[2] == ids[3], ids
    assert ids[0] != ids[2], ids


def _assert_inner_jitter_uses_chain_id() -> None:
    tree = inner_lines._get_or_create_tree()
    random = next(
        (node for node in tree.nodes if node.bl_idname == "FunctionNodeRandomValue"),
        None,
    )
    assert random is not None, "中間点の乱れ用の乱数ノードがありません"
    links = [link for link in tree.links if link.to_socket == random.inputs["ID"]]
    assert links, "中間点の乱れの乱数IDが未接続です"
    source = links[0].from_node
    assert getattr(source, "label", "") == inner_lines._CURVE_JITTER_CHAIN_ID_LABEL, (
        "内部線ごとの乱れIDが内部線チェーンIDではありません"
    )
    assert source.inputs["Name"].default_value == inner_line_chains.CHAIN_ID_ATTR


def main() -> None:
    b_manga_line.register()
    _clear_scene()

    _assert_target_only("outline", core.VG_LINE_WIDTH, "edge_smooth_factor")
    _assert_target_only("inner", core.VG_INNER_LINE_WIDTH, "inner_edge_smooth_factor")
    _assert_target_only(
        "intersection",
        core.VG_INTERSECTION_LINE_WIDTH,
        "intersection_edge_smooth_factor",
    )
    _assert_outline_uses_detection_angle()
    _assert_inner_midpoint_angle_is_hidden()
    _assert_rectangle_corners_are_outline_endpoints()
    _assert_cylinder_rims_use_view_endpoints()
    _assert_generated_target_uses_detection_angle(
        "inner",
        core.VG_INNER_LINE_WIDTH,
        "inner_edge_smooth_factor",
        "inner_line_angle",
    )
    _assert_generated_target_uses_detection_angle(
        "intersection",
        core.VG_INTERSECTION_LINE_WIDTH,
        "intersection_edge_smooth_factor",
        "intersection_edge_midpoint_angle",
    )
    _assert_closed_cylinder_rim_uses_camera_view_endpoints()
    _assert_branch_vertices_are_endpoints(
        "inner",
        core.VG_INNER_LINE_WIDTH,
        "inner_edge_smooth_factor",
    )
    _assert_branch_vertices_are_endpoints(
        "intersection",
        core.VG_INTERSECTION_LINE_WIDTH,
        "intersection_edge_smooth_factor",
    )
    _assert_bent_path_uses_detection_angle(
        "outline",
        core.VG_LINE_WIDTH,
        "edge_smooth_factor",
        "edge_midpoint_angle",
    )
    _assert_bent_path_uses_detection_angle(
        "inner",
        core.VG_INNER_LINE_WIDTH,
        "inner_edge_smooth_factor",
        "inner_line_angle",
    )
    _assert_bent_path_uses_detection_angle(
        "intersection",
        core.VG_INTERSECTION_LINE_WIDTH,
        "intersection_edge_smooth_factor",
        "intersection_edge_midpoint_angle",
    )
    _assert_acute_path_splits_below_detection_angle(
        "outline",
        core.VG_LINE_WIDTH,
        "edge_smooth_factor",
        "edge_midpoint_angle",
    )
    _assert_acute_path_splits_below_detection_angle(
        "inner",
        core.VG_INNER_LINE_WIDTH,
        "inner_edge_smooth_factor",
        "inner_line_angle",
    )
    _assert_acute_path_splits_below_detection_angle(
        "intersection",
        core.VG_INTERSECTION_LINE_WIDTH,
        "intersection_edge_smooth_factor",
        "intersection_edge_midpoint_angle",
    )
    _assert_diamond_loop_splits_only_acute_points(
        "outline",
        core.VG_LINE_WIDTH,
        "edge_smooth_factor",
        "edge_midpoint_angle",
    )
    _assert_diamond_loop_splits_only_acute_points(
        "inner",
        core.VG_INNER_LINE_WIDTH,
        "inner_edge_smooth_factor",
        "inner_line_angle",
    )
    _assert_diamond_loop_splits_only_acute_points(
        "intersection",
        core.VG_INTERSECTION_LINE_WIDTH,
        "intersection_edge_smooth_factor",
        "intersection_edge_midpoint_angle",
    )
    _assert_inner_chain_ids_split_at_branch_and_acute_points()
    _assert_inner_repair_preserves_midpoint_angle_split()
    _assert_inner_jitter_uses_chain_id()

    print("[PASS] midpoint width settings are independent per line type")


if __name__ == "__main__":
    main()
