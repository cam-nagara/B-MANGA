"""B-MANGA Line: default shell intersection lines avoid precise pair generation."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_lines,
    intersection_shell,
    outline_setup,
    presets,
)


THICKNESS_SOCKET = "線の太さ"
OFFSET_SOCKET = "オフセット"
MATERIAL_SOCKET = "マテリアル"


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    assert settings.intersection_method == "SHELL"
    settings.intersection_enabled = True
    settings.use_intersection_creation_limit = False
    return obj


def _make_surface_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return mat


def _make_source_slab(surface_mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_shell_contact_slab"
    obj.dimensions = (3.0, 3.0, 0.1)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(surface_mat)
    settings = obj.bmanga_line_settings
    settings.intersection_enabled = True
    settings.use_intersection_creation_limit = False
    settings.intersection_thickness = 0.03
    return obj


def _make_contact_cylinder(surface_mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48,
        radius=0.5,
        depth=1.0,
        location=(0.0, 0.0, 0.55),
    )
    obj = bpy.context.object
    obj.name = "BML_shell_contact_cylinder"
    obj.data.materials.append(surface_mat)
    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.03
    settings.intersection_enabled = True
    settings.use_intersection_creation_limit = False
    settings.intersection_thickness = 0.03
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _socket_id(tree: bpy.types.NodeTree, name: str) -> str:
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item.identifier
    raise AssertionError(f"socket not found: {name}")


def _socket_value(mod: bpy.types.Modifier, name: str):
    tree = getattr(mod, "node_group", None)
    if tree is None:
        raise AssertionError(f"node group missing: {mod.name}")
    return mod[_socket_id(tree, name)]


def _new_input_socket(
    tree: bpy.types.NodeTree,
    name: str,
    socket_type: str = "NodeSocketGeometry",
) -> bpy.types.NodeTreeInterfaceSocket:
    return tree.interface.new_socket(name=name, in_out="INPUT", socket_type=socket_type)


def _new_output_socket(
    tree: bpy.types.NodeTree,
    name: str,
    socket_type: str = "NodeSocketGeometry",
) -> bpy.types.NodeTreeInterfaceSocket:
    return tree.interface.new_socket(name=name, in_out="OUTPUT", socket_type=socket_type)


def _profile_resolutions(tree: bpy.types.NodeTree) -> list[int]:
    values: list[int] = []
    for node in tree.nodes:
        if node.bl_idname != "GeometryNodeCurvePrimitiveCircle":
            continue
        if getattr(node, "label", "") != "BML_IntersectionShellProfile":
            continue
        for inp in node.inputs:
            if inp.name == "Resolution":
                values.append(int(inp.default_value))
    return values


def _incoming_link(
    tree: bpy.types.NodeTree,
    node: bpy.types.Node,
    socket: bpy.types.NodeSocket,
) -> bpy.types.NodeLink | None:
    for link in tree.links:
        if link.to_node == node and link.to_socket == socket:
            return link
    return None


def _assert_shell_tree_has_midpoint_width_nodes() -> None:
    tree = intersection_shell._get_or_create_tree()
    assert _socket_id(tree, "中間頂点の乱れ (%)")
    assert _socket_id(tree, "検出角度")
    midpoint = next(
        (
            node
            for node in tree.nodes
            if getattr(node, "label", "") == intersection_shell._SHELL_SUBDIVIDE_NODE_LABEL
        ),
        None,
    )
    assert midpoint is not None, "区間ごとの中心点を追加するノードがありません"
    assert midpoint.bl_idname == "GeometryNodeSubdivideCurve"
    cuts_input = midpoint.inputs.get("Cuts")
    assert cuts_input is not None
    assert int(cuts_input.default_value) >= intersection_shell._SHELL_SUBDIVIDE_CUTS
    safe_scale = next(
        (
            node for node in tree.nodes
            if getattr(node, "label", "") == intersection_shell._SHELL_SAFE_SCALE_NODE_LABEL
        ),
        None,
    )
    assert safe_scale is not None, "交差線の極細部を安定化するノードがありません"
    assert safe_scale.bl_idname == "ShaderNodeMath"
    assert safe_scale.operation == "MAXIMUM"
    assert 0.0 < float(safe_scale.inputs[1].default_value) <= 0.05
    angle_compare = next(
        (
            node for node in tree.nodes
            if (
                node.bl_idname == "FunctionNodeCompare"
                and node.data_type == "FLOAT"
                and node.operation == "GREATER_THAN"
                and getattr(node, "label", "") == (
                    intersection_shell._SHELL_BRANCH_SPLIT_NODE_LABEL + "Angle"
                )
            )
        ),
        None,
    )
    assert angle_compare is not None
    angle_input_link = _incoming_link(tree, angle_compare, angle_compare.inputs[1])
    assert angle_input_link is not None, "交差線の角端点判定に検出角度が接続されていません"
    angle_cos = angle_input_link.from_node
    assert angle_cos.bl_idname == "ShaderNodeMath"
    assert angle_cos.operation == "COSINE"
    ui_angle_link = _incoming_link(tree, angle_cos, angle_cos.inputs[0])
    assert ui_angle_link is not None, "検出角度UI値が角端点判定へ届いていません"
    assert ui_angle_link.from_node.bl_idname == "NodeGroupInput"
    assert ui_angle_link.from_socket.name == "検出角度"
    split_store = next(
        (
            node for node in tree.nodes
            if (
                node.bl_idname == "GeometryNodeStoreNamedAttribute"
                and getattr(node, "label", "") == (
                    intersection_shell._SHELL_BRANCH_SPLIT_NODE_LABEL + "AngleStore"
                )
            )
        ),
        None,
    )
    assert split_store is not None, "交差線の端点候補を細分化前に保存していません"
    assert split_store.data_type == "FLOAT"
    assert split_store.inputs["Name"].default_value == intersection_shell._SHELL_SPLIT_ATTR
    graph_split = next(
        (
            node for node in tree.nodes
            if (
                node.bl_idname == "GeometryNodeStoreNamedAttribute"
                and getattr(node, "label", "") == (
                    intersection_shell._SHELL_GRAPH_SPLIT_NODE_LABEL + "Store"
                )
            )
        ),
        None,
    )
    assert graph_split is not None, "交差線の実端・分岐点を端点候補に保存していません"
    assert graph_split.data_type == "BOOLEAN"
    assert graph_split.domain == "POINT"
    assert graph_split.inputs["Name"].default_value == intersection_shell._SHELL_GRAPH_SPLIT_ATTR
    graph_degree = next(
        (
            node for node in tree.nodes
            if (
                node.bl_idname == "FunctionNodeCompare"
                and node.data_type == "INT"
                and node.operation == "NOT_EQUAL"
                and getattr(node, "label", "") == intersection_shell._SHELL_GRAPH_SPLIT_NODE_LABEL
            )
        ),
        None,
    )
    assert graph_degree is not None, "交差線グラフの分岐点判定がありません"
    assert any(
        link.from_node == split_store
        and link.to_node == midpoint
        and link.to_socket == midpoint.inputs["Curve"]
        for link in tree.links
    ), "角端点候補を保存した交差線カーブが細分化へ渡っていません"
    split_read = next(
        (
            node for node in tree.nodes
            if (
                node.bl_idname == "GeometryNodeInputNamedAttribute"
                and node.data_type == "FLOAT"
                and node.inputs["Name"].default_value == intersection_shell._SHELL_SPLIT_ATTR
            )
        ),
        None,
    )
    assert split_read is not None, "保存済み端点候補を線幅補間へ渡していません"
    assert not any(
        str(getattr(node, "label", "")).startswith("BML_IntersectionShellPathWidthV15")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellPathWidthV16")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellPathWidthV17")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellPathWidthV19")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellCurveEndpointV21")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellCurveEndpointV22")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellCurveEndpointV23")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellCurveEndpointV24")
        or str(getattr(node, "label", "")).startswith("BML_IntersectionShellCurveEndpointV25")
        for node in tree.nodes
    ), "旧世代の交差線中間頂点ノードが残っています"
    assert any(
        node.bl_idname == "GeometryNodeSplineParameter"
        for node in tree.nodes
    ), "線幅が端点から中間点まで連続補間されていません"
    assert any(
        node.bl_idname == "GeometryNodeAccumulateField"
        for node in tree.nodes
    ), "交差線の線幅が角/端点ごとの区間距離で補間されていません"
    assert any(
        node.bl_idname == "FunctionNodeRandomValue"
        for node in tree.nodes
    ), "交差線の中間頂点の乱れがノードに接続されていません"
    assert not any(
        node.bl_idname == "GeometryNodeSplitEdges"
        for node in tree.nodes
    ), "線そのものを分割するノードが残っています"
    assert not any(
        node.bl_idname == "GeometryNodeResampleCurve"
        for node in tree.nodes
    ), "再サンプリングで線形状を変えるノードが残っています"
    assert any(
        node.bl_idname == "GeometryNodeOffsetPointInCurve"
        for node in tree.nodes
    )
    assert any(
        node.bl_idname == "GeometryNodeSampleIndex"
        for node in tree.nodes
    )
    assert any(
        node.bl_idname == "GeometryNodeMergeByDistance"
        for node in tree.nodes
    )
    contact_offset = next(
        (
            node for node in tree.nodes
            if getattr(node, "label", "") == intersection_shell._SHELL_CONTACT_OFFSET_NODE_LABEL
        ),
        None,
    )
    assert contact_offset is not None, "固定微小値で接触判定するノードがありません"
    assert contact_offset.bl_idname == "GeometryNodeSetPosition"
    visual_radius_marker = next(
        (
            node for node in tree.nodes
            if getattr(node, "label", "") == intersection_shell._SHELL_VISUAL_RADIUS_NODE_LABEL
        ),
        None,
    )
    assert visual_radius_marker is not None, "交差線の表示半径が現行仕様ではありません"
    radius = next(
        (
            node for node in tree.nodes
            if getattr(node, "label", "") == intersection_shell._SHELL_RADIUS_NODE_LABEL
        ),
        None,
    )
    assert radius is not None, "交差線の表示半径ノードがありません"
    radius_link = _incoming_link(tree, radius, radius.inputs[0])
    assert radius_link is not None, "交差線の線幅入力が表示半径へ届いていません"
    assert radius_link.from_node.bl_idname == "NodeGroupInput"
    assert radius_link.from_socket.name == "線の太さ"
    assert not any(
        node.bl_idname == "ShaderNodeMath"
        and node.operation == "MAXIMUM"
        and len(node.inputs) > 1
        and abs(float(node.inputs[1].default_value) - 0.0005) < 1.0e-9
        for node in tree.nodes
    ), "線幅に連動して交差対象を押し出す旧世代ノードが残っています"
    assert not any(
        node.bl_idname == "ShaderNodeMath"
        and node.operation == "MAXIMUM"
        and len(node.inputs) > 1
        and abs(float(node.inputs[1].default_value) - 0.02) < 1.0e-9
        for node in tree.nodes
    ), "中間頂点の線幅調整に0幅を妨げる下限が残っています"


def _make_short_corner_probe_object() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BMLShortCornerProbeMesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.7)],
        [(0, 1), (1, 2)],
        [],
    )
    mesh.update()
    obj = bpy.data.objects.new("BML_short_corner_probe", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _build_short_corner_probe_tree() -> bpy.types.NodeTree:
    from b_manga_line import intersection_shell_node_helpers as helpers

    tree = bpy.data.node_groups.new("BML_short_corner_probe_tree", "GeometryNodeTree")
    _new_input_socket(tree, "Geometry")
    _new_output_socket(tree, "Geometry")
    nodes = tree.nodes
    links = tree.links
    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1000, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (2500, 0)

    mesh_to_curve = nodes.new("GeometryNodeMeshToCurve")
    mesh_to_curve.location = (-780, 0)
    links.new(group_in.outputs["Geometry"], mesh_to_curve.inputs["Mesh"])

    set_radius = nodes.new("GeometryNodeSetCurveRadius")
    set_radius.location = (-560, 0)
    set_radius.inputs["Radius"].default_value = 1.0
    links.new(mesh_to_curve.outputs["Curve"], set_radius.inputs["Curve"])

    angle = nodes.new("ShaderNodeValue")
    angle.location = (-780, -260)
    angle.outputs[0].default_value = math.radians(100.0)

    split_curve = helpers.store_curve_midpoint_split_attribute(
        nodes,
        links,
        set_radius.outputs["Curve"],
        angle.outputs[0],
        (-340, -300),
        attribute_name="BMLShortCornerSplit",
        label="BML_ShortCornerProbeAngle",
        angle_split_min_segment_fraction=0.0,
        angle_split_confirmation_offset=1,
        include_curve_endpoints=True,
    )

    subdivide = nodes.new("GeometryNodeSubdivideCurve")
    subdivide.location = (780, 0)
    subdivide.inputs["Cuts"].default_value = 15
    links.new(split_curve, subdivide.inputs["Curve"])

    factor = nodes.new("ShaderNodeValue")
    factor.location = (760, -360)
    factor.outputs[0].default_value = -1.0
    jitter = nodes.new("ShaderNodeValue")
    jitter.location = (760, -480)
    jitter.outputs[0].default_value = 0.0
    scale = helpers.add_curve_midpoint_width_scale_from_split_attribute(
        nodes,
        links,
        subdivide.outputs["Curve"],
        factor.outputs[0],
        (1020, -620),
        attribute_name="BMLShortCornerSplit",
        label="BML_ShortCornerProbeScale",
        jitter_output=jitter.outputs[0],
    )

    profile = nodes.new("GeometryNodeCurvePrimitiveCircle")
    profile.location = (1700, -240)
    profile.mode = "RADIUS"
    profile.inputs["Radius"].default_value = 0.1
    profile.inputs["Resolution"].default_value = 12

    curve_to_mesh = nodes.new("GeometryNodeCurveToMesh")
    curve_to_mesh.location = (1980, 0)
    links.new(subdivide.outputs["Curve"], curve_to_mesh.inputs["Curve"])
    links.new(profile.outputs["Curve"], curve_to_mesh.inputs["Profile Curve"])
    scale_socket = curve_to_mesh.inputs.get("Scale")
    assert scale_socket is not None, "Curve to Mesh の線幅Scale入力がありません"
    links.new(scale, scale_socket)
    links.new(curve_to_mesh.outputs["Mesh"], group_out.inputs["Geometry"])
    return tree


def _short_corner_probe_path() -> tuple[list[tuple[float, float, float]], list[float]]:
    points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.7)]
    lengths = [0.0]
    for start, end in zip(points, points[1:]):
        segment = math.sqrt(sum((end[i] - start[i]) ** 2 for i in range(3)))
        lengths.append(lengths[-1] + segment)
    return points, lengths


def _point_segment_station_and_radius(
    co,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    base_station: float,
) -> tuple[float, float]:
    px, py, pz = co.x, co.y, co.z
    sx, sy, sz = start
    ex, ey, ez = end
    vx, vy, vz = ex - sx, ey - sy, ez - sz
    wx, wy, wz = px - sx, py - sy, pz - sz
    length_sq = vx * vx + vy * vy + vz * vz
    if length_sq <= 0.0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, (wx * vx + wy * vy + wz * vz) / length_sq))
    cx, cy, cz = sx + vx * t, sy + vy * t, sz + vz * t
    radius = math.sqrt((px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2)
    station = base_station + math.sqrt(length_sq) * t
    return station, radius


def _short_corner_station_and_radius(co) -> tuple[float, float]:
    points, lengths = _short_corner_probe_path()
    candidates = [
        _point_segment_station_and_radius(co, points[index], points[index + 1], lengths[index])
        for index in range(len(points) - 1)
    ]
    return min(candidates, key=lambda item: item[1])


def _short_corner_bucket_radii(obj: bpy.types.Object) -> dict[str, float]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        buckets: dict[str, list[float]] = {
            "first_mid": [],
            "corner": [],
            "second_mid": [],
        }
        for vertex in mesh.vertices:
            station, radius = _short_corner_station_and_radius(vertex.co)
            if 0.45 <= station <= 0.55:
                buckets["first_mid"].append(radius)
            elif 0.95 <= station <= 1.05:
                buckets["corner"].append(radius)
            elif 1.55 <= station <= 1.70:
                buckets["second_mid"].append(radius)
        return {
            "first_mid_min": min(buckets["first_mid"]) if buckets["first_mid"] else -1.0,
            "corner_max": max(buckets["corner"]) if buckets["corner"] else -1.0,
            "second_mid_min": min(buckets["second_mid"]) if buckets["second_mid"] else -1.0,
        }
    finally:
        bpy.data.meshes.remove(mesh)


def _assert_3d_curve_angle_keeps_tapered_segments() -> None:
    _clear_scene()
    obj = _make_short_corner_probe_object()
    tree = _build_short_corner_probe_tree()
    mod = obj.modifiers.new("BML_short_corner_width_probe", "NODES")
    mod.node_group = tree
    bpy.context.view_layer.update()

    radii = _short_corner_bucket_radii(obj)
    first_mid = radii["first_mid_min"]
    corner = radii["corner_max"]
    second_mid = radii["second_mid_min"]
    assert 0.0 <= first_mid < 0.025, radii
    assert 0.0 <= second_mid < 0.025, radii
    assert corner > 0.07, radii
    assert corner > first_mid * 4.0 and corner > second_mid * 4.0, radii


def _intersection_material_polygons(obj: bpy.types.Object) -> int:
    mat = outline_setup.get_line_material(obj, "intersection")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_index = None
        for index, item in enumerate(mesh.materials):
            if item is not None and item.name.startswith(mat.name):
                line_index = index
                break
        assert line_index is not None, "交差線素材が評価済みメッシュにありません"
        return sum(1 for poly in mesh.polygons if poly.material_index == line_index)
    finally:
        bpy.data.meshes.remove(mesh)


def _intersection_material_vertices(obj: bpy.types.Object) -> list:
    mat = outline_setup.get_line_material(obj, "intersection")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_index = None
        for index, item in enumerate(mesh.materials):
            if item is not None and item.name.startswith(mat.name):
                line_index = index
                break
        assert line_index is not None, "交差線素材が評価済みメッシュにありません"
        line_vertices = set()
        for poly in mesh.polygons:
            if poly.material_index == line_index:
                line_vertices.update(poly.vertices)
        matrix = obj.matrix_world.copy()
        return [matrix @ mesh.vertices[index].co for index in line_vertices]
    finally:
        bpy.data.meshes.remove(mesh)


def _assert_shell_contact_line_appears() -> None:
    _clear_scene()
    surface = _make_surface_material("BML_shell_contact_surface")
    source = _make_source_slab(surface)
    target = _make_contact_cylinder(surface)
    assert presets.apply_line_settings(target, bpy.context)
    assert presets.apply_line_settings(source, bpy.context)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)

    coords = _intersection_material_vertices(source) + _intersection_material_vertices(target)
    assert len(coords) > 80, f"接触部の交差線素材面が少なすぎます: {len(coords)}"
    min_x = min(co.x for co in coords)
    max_x = max(co.x for co in coords)
    min_y = min(co.y for co in coords)
    max_y = max(co.y for co in coords)
    min_z = min(co.z for co in coords)
    max_z = max(co.z for co in coords)
    assert min_x < -0.45 and max_x > 0.45, (min_x, max_x)
    assert min_y < -0.45 and max_y > 0.45, (min_y, max_y)
    assert -0.08 < min_z < 0.10, (min_z, max_z)
    assert -0.08 < max_z < 0.12, (min_z, max_z)


def _assert_non_intersecting_shell_stays_clean() -> None:
    _clear_scene()
    surface = _make_surface_material("BML_shell_far_surface")
    source = _make_source_slab(surface)
    target = _make_contact_cylinder(surface)
    target.location.z += 1.0
    assert presets.apply_line_settings(target, bpy.context)
    assert presets.apply_line_settings(source, bpy.context)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)

    coords = _intersection_material_vertices(source) + _intersection_material_vertices(target)
    assert not coords, f"離れたメッシュに交差線素材が出ています: {len(coords)}"


def _z_span(coords: list) -> float:
    if not coords:
        return 0.0
    values = [co.z for co in coords]
    return max(values) - min(values)


def _abs_z_percentile(coords: list, ratio: float) -> float:
    if not coords:
        return 0.0
    values = sorted(abs(co.z) for co in coords)
    index = min(len(values) - 1, max(0, int(len(values) * ratio)))
    return values[index]


def _assert_shell_width_controls_affect_generated_mesh() -> None:
    # ペアの持ち主は面数の少ない側（スラブ）に決定的に決まる
    # （2026-07-03: アクティブオブジェクト優先を廃止）
    _clear_scene()
    surface = _make_surface_material("BML_shell_width_surface")
    slab = _make_source_slab(surface)
    cylinder = _make_contact_cylinder(surface)
    slab.bmanga_line_settings.intersection_line_offset = 0.0
    cylinder.bmanga_line_settings.intersection_line_offset = 0.0
    slab.bmanga_line_settings.intersection_edge_smooth_factor = 0.0
    _select([slab, cylinder])
    bpy.context.view_layer.objects.active = cylinder

    assert presets.apply_line_settings(slab, bpy.context, refresh_scene=False)
    assert presets.apply_line_settings(cylinder, bpy.context, refresh_scene=False)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)

    base_coords = _intersection_material_vertices(slab)
    assert _z_span(base_coords) > 0.04, _z_span(base_coords)

    intersection_lines.update_parameters(slab, thickness=0.08)
    slab.update_tag()
    bpy.context.view_layer.update()
    thick_coords = _intersection_material_vertices(slab)
    assert _z_span(thick_coords) > _z_span(base_coords) * 2.0, (
        _z_span(base_coords),
        _z_span(thick_coords),
    )
    thick_p75 = _abs_z_percentile(thick_coords, 0.75)

    slab.bmanga_line_settings.intersection_edge_smooth_factor = -1.0
    intersection_lines.update_parameters(slab)
    slab.update_tag()
    bpy.context.view_layer.update()
    tapered_coords = _intersection_material_vertices(slab)
    tapered_p75 = _abs_z_percentile(tapered_coords, 0.75)
    assert tapered_p75 < thick_p75 * 0.75, (
        thick_p75,
        tapered_p75,
    )


def _assert_stale_shell_tree_is_rebuilt() -> None:
    old = bpy.data.node_groups.get(intersection_shell.SHELL_TREE_NAME)
    if old is not None:
        bpy.data.node_groups.remove(old)
    stale = bpy.data.node_groups.new(
        name=intersection_shell.SHELL_TREE_NAME,
        type="GeometryNodeTree",
    )
    for name, socket_type in (
        ("Geometry", "NodeSocketGeometry"),
        ("線の太さ", "NodeSocketFloat"),
        ("オフセット", "NodeSocketFloat"),
        ("マテリアル", "NodeSocketMaterial"),
        ("交差対象グループ", "NodeSocketCollection"),
        ("交差対象の線幅", "NodeSocketFloat"),
        ("ライン素材番号", "NodeSocketInt"),
        ("交差対象あり", "NodeSocketBool"),
        ("中間頂点の線幅調整", "NodeSocketFloat"),
        ("変化グラフ 25%", "NodeSocketFloat"),
        ("変化グラフ 50%", "NodeSocketFloat"),
        ("変化グラフ 75%", "NodeSocketFloat"),
    ):
        stale.interface.new_socket(name=name, in_out="INPUT", socket_type=socket_type)
    stale.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    boolean = stale.nodes.new("GeometryNodeMeshBoolean")
    boolean.label = "BML_IntersectionShellBoolean"
    radius = stale.nodes.new("ShaderNodeMath")
    radius.label = "BML_IntersectionShellOwnRadius"
    normalizer = stale.nodes.new("GeometryNodeSetCurveRadius")
    normalizer.label = "BML_IntersectionShellCurveRadius"

    rebuilt = intersection_shell._get_or_create_tree()
    assert rebuilt != stale
    assert intersection_shell._tree_uses_generated_mark(rebuilt)
    assert _profile_resolutions(rebuilt) == [
        intersection_shell.SHELL_TUBE_PROFILE_RESOLUTION
    ]
    _assert_shell_tree_has_midpoint_width_nodes()


def _assert_low_resolution_shell_tree_is_rebuilt() -> None:
    old = bpy.data.node_groups.get(intersection_shell.SHELL_TREE_NAME)
    if old is not None:
        bpy.data.node_groups.remove(old)
    stale = bpy.data.node_groups.new(
        name=intersection_shell.SHELL_TREE_NAME,
        type="GeometryNodeTree",
    )
    for name, socket_type in (
        ("Geometry", "NodeSocketGeometry"),
        ("線の太さ", "NodeSocketFloat"),
        ("オフセット", "NodeSocketFloat"),
        ("マテリアル", "NodeSocketMaterial"),
        ("交差対象グループ", "NodeSocketCollection"),
        ("交差対象の線幅", "NodeSocketFloat"),
        ("自分のアウトライン幅", "NodeSocketFloat"),
        ("アウトライン素材", "NodeSocketMaterial"),
        ("ライン素材番号", "NodeSocketInt"),
        ("交差対象あり", "NodeSocketBool"),
        ("中間頂点の線幅調整", "NodeSocketFloat"),
        ("変化グラフ 25%", "NodeSocketFloat"),
        ("変化グラフ 50%", "NodeSocketFloat"),
        ("変化グラフ 75%", "NodeSocketFloat"),
    ):
        stale.interface.new_socket(name=name, in_out="INPUT", socket_type=socket_type)
    stale.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    stale.nodes.new("GeometryNodeMaterialSelection")
    boolean = stale.nodes.new("GeometryNodeMeshBoolean")
    boolean.label = "BML_IntersectionShellBoolean"
    radius = stale.nodes.new("ShaderNodeMath")
    radius.label = "BML_IntersectionShellOwnRadius"
    normalizer = stale.nodes.new("GeometryNodeSetCurveRadius")
    normalizer.label = "BML_IntersectionShellCurveRadius"
    surface = stale.nodes.new("FunctionNodeBooleanMath")
    surface.label = "BML_IntersectionShellSurface"
    union = stale.nodes.new("GeometryNodeMeshBoolean")
    union.label = "BML_IntersectionShellTargetUnion"
    stale.nodes.new("GeometryNodeMergeByDistance")
    combined = stale.nodes.new("ShaderNodeMath")
    combined.label = "BML_IntersectionShellCombinedThickness"
    mark = stale.nodes.new("GeometryNodeStoreNamedAttribute")
    mark.label = intersection_lines._GENERATED_LINE_NODE_LABEL
    profile = stale.nodes.new("GeometryNodeCurvePrimitiveCircle")
    profile.label = "BML_IntersectionShellProfile"
    profile.inputs["Resolution"].default_value = 4

    rebuilt = intersection_shell._get_or_create_tree()
    assert rebuilt != stale
    assert _profile_resolutions(rebuilt) == [
        intersection_shell.SHELL_TUBE_PROFILE_RESOLUTION
    ]
    _assert_shell_tree_has_midpoint_width_nodes()


def _assert_missing_gap_coverage_shell_tree_is_rebuilt() -> None:
    tree = intersection_shell._get_or_create_tree()
    coverage = next(
        (
            node
            for node in tree.nodes
            if getattr(node, "label", "") == "BML_IntersectionShellGapCoverage"
        ),
        None,
    )
    assert coverage is not None, "交差線の隙間カバー係数ノードがありません"
    tree.nodes.remove(coverage)

    rebuilt = intersection_shell._get_or_create_tree()
    assert rebuilt != tree
    assert any(
        getattr(node, "label", "") == "BML_IntersectionShellGapCoverage"
        for node in rebuilt.nodes
    )
    _assert_shell_tree_has_midpoint_width_nodes()


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _assert_stale_shell_tree_is_rebuilt()
        _assert_low_resolution_shell_tree_is_rebuilt()
        _assert_missing_gap_coverage_shell_tree_is_rebuilt()
        _assert_shell_tree_has_midpoint_width_nodes()
        _assert_3d_curve_angle_keeps_tapered_segments()
        _clear_scene()
        # Zを互いにずらし、完全同一平面の重なり（EXACTブーリアンの縮退
        # ケース）を避ける現実的な配置にする
        objects = [
            _make_cube("BML_shell_intersection_A", (-0.25, 0.0, 0.0)),
            _make_cube("BML_shell_intersection_B", (0.25, 0.0, -0.07)),
            _make_cube("BML_shell_intersection_C", (0.0, 0.25, 0.11)),
        ]
        _select(objects)

        real_auto_targets = intersection_lines._auto_targets

        def forbidden_auto_targets(*_args, **_kwargs):
            raise AssertionError("交差相手の候補列挙が呼ばれています")

        intersection_lines._auto_targets = forbidden_auto_targets
        try:
            for obj in objects:
                assert presets.apply_line_settings(
                    obj,
                    bpy.context,
                    refresh_scene=False,
                ), obj.name

            old_threshold = intersection_lines._DEFERRED_VIEWPORT_THRESHOLD
            intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = 0
            try:
                refreshed = intersection_lines.refresh_scene_intersections(bpy.context.scene)
            finally:
                intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = old_threshold
        finally:
            intersection_lines._auto_targets = real_auto_targets

        assert refreshed, "交差線モディファイアが作成されていません"
        owned_pairs = set()
        total_vertices = 0
        for obj in objects:
            mods = list(core.iter_intersection_modifiers(obj))
            assert len(mods) <= 1, (obj.name, [mod.name for mod in mods])
            if not mods:
                continue
            mod = mods[0]
            assert mod.name == intersection_shell.SHELL_MODIFIER_NAME
            assert mod.node_group is not None
            assert not any(
                node.bl_idname == "GeometryNodeProximity"
                for node in mod.node_group.nodes
            )
            assert any(
                node.bl_idname == "GeometryNodeMeshBoolean"
                and getattr(node, "label", "") == "BML_IntersectionShellBoolean"
                for node in mod.node_group.nodes
            )
            assert _profile_resolutions(mod.node_group) == [
                intersection_shell.SHELL_TUBE_PROFILE_RESOLUTION
            ]
            _assert_shell_tree_has_midpoint_width_nodes()
            assert intersection_lines._modifier_target(mod) is None
            targets = intersection_shell.modifier_targets(mod)
            assert obj not in targets
            assert targets, obj.name
            for target in targets:
                pair = tuple(sorted((obj.name, target.name)))
                assert pair not in owned_pairs, f"交差ペアが二重生成されています: {pair}"
                owned_pairs.add(pair)
            assert mod.show_viewport
            assert mod.show_render
            assert not intersection_lines.is_deferred_viewport_modifier(mod)
            assert _intersection_material_polygons(obj) > 0
            total_vertices += len(_intersection_material_vertices(obj))
        assert len(owned_pairs) == 3, owned_pairs
        assert total_vertices > 0

        source = next(obj for obj in objects if list(core.iter_intersection_modifiers(obj)))
        mod = next(core.iter_intersection_modifiers(source))
        mat = outline_setup.get_line_material(source, "intersection")
        intersection_lines.update_parameters(
            source,
            thickness=0.0123,
            offset=-0.25,
            material=mat,
        )
        assert abs(float(_socket_value(mod, THICKNESS_SOCKET)) - 0.0123) < 1.0e-7
        assert abs(float(_socket_value(mod, OFFSET_SOCKET)) + 0.25) < 1.0e-7
        assert _socket_value(mod, MATERIAL_SOCKET) == mat

        _assert_shell_contact_line_appears()
        _assert_non_intersecting_shell_stays_clean()
        _assert_shell_width_controls_affect_generated_mesh()

        print("[PASS] default shell intersection lines work without precise pair generation")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
