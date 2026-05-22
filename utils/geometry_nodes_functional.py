"""Functional Geometry Nodes builders for B-Name visible objects."""

from __future__ import annotations

import math


def _switch_geometry(gn, group, switch_socket, false_socket, true_socket, *, label: str, location: tuple[float, float]):
    node = gn._node(group, "GeometryNodeSwitch", label=label, location=location)
    node.input_type = "GEOMETRY"
    gn._link(group, switch_socket, node.inputs["Switch"])
    gn._link(group, false_socket, node.inputs["False"])
    gn._link(group, true_socket, node.inputs["True"])
    return node.outputs["Output"]


def _compare_int(gn, group, source_socket, value: int, *, operation: str = "EQUAL", label: str, location: tuple[float, float]):
    node = gn._node(group, "FunctionNodeCompare", label=label, location=location)
    node.data_type = "INT"
    node.operation = operation
    gn._link(group, source_socket, gn._socket_by_identifier(node.inputs, "A_INT"))
    gn._set_default(gn._socket_by_identifier(node.inputs, "B_INT"), int(value))
    return node.outputs["Result"]


def _empty_geometry(gn, group, *, label: str, location: tuple[float, float]):
    node = gn._node(group, "GeometryNodeMeshLine", label=label, location=location)
    gn._set_default(node.inputs["Count"], 0)
    return node.outputs["Mesh"]


def _float_to_int(gn, group, source_socket, *, label: str, location: tuple[float, float], mode: str = "ROUND"):
    node = gn._node(group, "FunctionNodeFloatToInt", label=label, location=location)
    node.rounding_mode = mode
    gn._link(group, source_socket, node.inputs["Float"])
    return node.outputs["Integer"]


def _quadrilateral_curve(gn, group, input_node, width_half_m, height_half_m, *, label: str, location: tuple[float, float]):
    width_m = gn._math_binary(group, "MULTIPLY", width_half_m, b_value=2.0, label=f"{label} 幅", location=(location[0], location[1] + 220))
    height_m = gn._math_binary(group, "MULTIPLY", height_half_m, b_value=2.0, label=f"{label} 高さ", location=(location[0], location[1] + 60))
    quad = gn._node(group, "GeometryNodeCurvePrimitiveQuadrilateral", label=label, location=(location[0] + 220, location[1]))
    quad.mode = "RECTANGLE"
    gn._link(group, width_m, quad.inputs["Width"])
    gn._link(group, height_m, quad.inputs["Height"])
    rounded = gn._node(group, "GeometryNodeFilletCurve", label=f"{label} 角丸", location=(location[0] + 460, location[1]))
    gn._link(group, quad.outputs["Curve"], rounded.inputs["Curve"])
    radius_m = gn._math_binary(group, "MULTIPLY", input_node.outputs["角半径"], b_value=0.001, label=f"{label} 角半径", location=(location[0] + 220, location[1] - 200))
    gn._link(group, radius_m, rounded.inputs["Radius"])
    gn._set_default(rounded.inputs["Count"], 8)
    curve = _switch_geometry(
        gn,
        group,
        input_node.outputs["角丸"],
        quad.outputs["Curve"],
        rounded.outputs["Curve"],
        label=f"{label} 角丸切替",
        location=(location[0] + 700, location[1]),
    )
    translation = gn._combine_xyz(group, width_half_m, height_half_m, z=0.0, label=f"{label} 中心配置", location=(location[0] + 700, location[1] - 200))
    transform = gn._node(group, "GeometryNodeTransform", label=f"{label} 配置", location=(location[0] + 920, location[1]))
    gn._link(group, curve, transform.inputs["Geometry"])
    gn._link(group, translation, transform.inputs["Translation"])
    return transform.outputs["Geometry"]


def _curve_fill_and_outline(gn, group, curve_socket, line_half_m, line_material, fill_material, *, label: str, location: tuple[float, float]):
    fill = gn._node(group, "GeometryNodeFillCurve", label=f"{label} 塗り", location=(location[0], location[1] + 180))
    gn._link(group, curve_socket, fill.inputs["Curve"])
    fill_mat = gn._set_material(group, fill.outputs["Mesh"], fill_material, label=f"{label} 塗り素材", location=(location[0] + 220, location[1] + 180))

    profile = gn._node(group, "GeometryNodeCurvePrimitiveCircle", label=f"{label} 線幅", location=(location[0], location[1] - 120))
    gn._set_default(profile.inputs["Resolution"], 8)
    gn._link(group, line_half_m, profile.inputs["Radius"])
    outline = gn._node(group, "GeometryNodeCurveToMesh", label=f"{label} 輪郭", location=(location[0] + 220, location[1] - 60))
    gn._set_default(outline.inputs["Fill Caps"], True)
    gn._link(group, curve_socket, outline.inputs["Curve"])
    gn._link(group, profile.outputs["Curve"], outline.inputs["Profile Curve"])
    outline_mat = gn._set_material(group, outline.outputs["Mesh"], line_material, label=f"{label} 線素材", location=(location[0] + 440, location[1] - 60))
    join = gn._node(group, "GeometryNodeJoinGeometry", label=f"{label} 結合", location=(location[0] + 660, location[1] + 60))
    gn._link(group, fill_mat, join.inputs["Geometry"])
    gn._link(group, outline_mat, join.inputs["Geometry"])
    return join.outputs["Geometry"]


def _tail_geometry(gn, group, input_node, width_half_m, height_half_m, line_half_m, line_material, fill_material, index: int):
    prefix = "しっぽ" if index == 1 else f"しっぽ{index}"
    row = -1700 - index * 520
    type_socket = input_node.outputs[f"{prefix} 種類"]
    is_curve = _compare_int(gn, group, type_socket, 2, label=f"{prefix} 曲線種別", location=(-1180, row + 220))
    is_sticky = _compare_int(gn, group, type_socket, 3, label=f"{prefix} 付箋種別", location=(-1180, row + 80))
    angle = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 方向"], b_value=math.pi / 180.0, label=f"{prefix} 角度", location=(-980, row))
    dx = gn._math_binary(group, "COSINE", angle, label=f"{prefix} X方向", location=(-780, row + 120))
    dy = gn._math_binary(group, "SINE", angle, label=f"{prefix} Y方向", location=(-780, row - 40))
    px = gn._math_binary(group, "MULTIPLY", dy, b_value=-1.0, label=f"{prefix} 垂直X", location=(-580, row + 160))
    py = dx
    root_x_default = gn._math_add(group, width_half_m, gn._math_binary(group, "MULTIPLY", width_half_m, dx, label=f"{prefix} 根元X方向", location=(-580, row - 80)), label=f"{prefix} 根元X", location=(-380, row - 80))
    root_y_default = gn._math_add(group, height_half_m, gn._math_binary(group, "MULTIPLY", height_half_m, dy, label=f"{prefix} 根元Y方向", location=(-580, row - 240)), label=f"{prefix} 根元Y", location=(-380, row - 240))
    end_len = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 長さ"], b_value=0.001, label=f"{prefix} 長さ", location=(-580, row - 400))
    bend_raw = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 曲げ"], end_len, label=f"{prefix} 曲げ量", location=(-380, row - 480))
    bend_zero = gn._constant_float(group, 0.0, label=f"{prefix} 直線曲げなし", location=(-380, row - 640))
    bend = gn._switch_float(group, is_curve, bend_zero, bend_raw, label=f"{prefix} 曲げ種別切替", location=(-180, row - 520))
    end_x_default = gn._math_add(group, root_x_default, gn._math_add(group, gn._math_binary(group, "MULTIPLY", dx, end_len, label=f"{prefix} 先端X方向", location=(-180, row - 80)), gn._math_binary(group, "MULTIPLY", px, bend, label=f"{prefix} 曲げX", location=(-180, row - 240)), label=f"{prefix} 先端X差分", location=(20, row - 160)), label=f"{prefix} 先端X", location=(220, row - 160))
    end_y_default = gn._math_add(group, root_y_default, gn._math_add(group, gn._math_binary(group, "MULTIPLY", dy, end_len, label=f"{prefix} 先端Y方向", location=(-180, row - 400)), gn._math_binary(group, "MULTIPLY", py, bend, label=f"{prefix} 曲げY", location=(-180, row - 560)), label=f"{prefix} 先端Y差分", location=(20, row - 480)), label=f"{prefix} 先端Y", location=(220, row - 480))
    start_x_m = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 始点 X"], b_value=0.001, label=f"{prefix} 固定始点X", location=(20, row + 220))
    start_y_m = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 始点 Y"], b_value=0.001, label=f"{prefix} 固定始点Y", location=(20, row + 60))
    end_x_m = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 終点 X"], b_value=0.001, label=f"{prefix} 固定終点X", location=(220, row + 220))
    end_y_m = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 終点 Y"], b_value=0.001, label=f"{prefix} 固定終点Y", location=(220, row + 60))
    root_x = gn._switch_float(group, input_node.outputs[f"{prefix} 始点・終点を固定"], root_x_default, start_x_m, label=f"{prefix} 始点X切替", location=(420, row + 120))
    root_y = gn._switch_float(group, input_node.outputs[f"{prefix} 始点・終点を固定"], root_y_default, start_y_m, label=f"{prefix} 始点Y切替", location=(420, row - 40))
    tip_x = gn._switch_float(group, input_node.outputs[f"{prefix} 始点・終点を固定"], end_x_default, end_x_m, label=f"{prefix} 終点X切替", location=(620, row + 120))
    tip_y = gn._switch_float(group, input_node.outputs[f"{prefix} 始点・終点を固定"], end_y_default, end_y_m, label=f"{prefix} 終点Y切替", location=(620, row - 40))
    root_half = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 根元幅"], b_value=0.0005, label=f"{prefix} 根元半幅", location=(620, row - 240))
    tip_half_raw = gn._math_binary(group, "MULTIPLY", input_node.outputs[f"{prefix} 先端幅"], b_value=0.0005, label=f"{prefix} 先端半幅", location=(620, row - 400))
    tip_half = gn._switch_float(group, is_sticky, tip_half_raw, root_half, label=f"{prefix} 付箋幅切替", location=(820, row - 400))
    def point(label: str, cx, cy, half, sign: float, loc_y: int):
        ox = gn._math_binary(group, "MULTIPLY", px, half, label=f"{label} X幅", location=(820, loc_y))
        oy = gn._math_binary(group, "MULTIPLY", py, half, label=f"{label} Y幅", location=(820, loc_y - 120))
        if sign < 0:
            ox = gn._math_binary(group, "MULTIPLY", ox, b_value=-1.0, label=f"{label} X反転", location=(1020, loc_y))
            oy = gn._math_binary(group, "MULTIPLY", oy, b_value=-1.0, label=f"{label} Y反転", location=(1020, loc_y - 120))
        return gn._combine_xyz(group, gn._math_add(group, cx, ox, label=f"{label} X", location=(1220, loc_y)), gn._math_add(group, cy, oy, label=f"{label} Y", location=(1220, loc_y - 120)), z=0.0, label=label, location=(1420, loc_y - 60))

    p1 = point(f"{prefix} 根元左", root_x, root_y, root_half, 1.0, row + 260)
    p2 = point(f"{prefix} 先端左", tip_x, tip_y, tip_half, 1.0, row + 20)
    p3 = point(f"{prefix} 先端右", tip_x, tip_y, tip_half, -1.0, row - 220)
    p4 = point(f"{prefix} 根元右", root_x, root_y, root_half, -1.0, row - 460)
    quad = gn._node(group, "GeometryNodeCurvePrimitiveQuadrilateral", label=f"{prefix} 形状", location=(1640, row - 120))
    quad.mode = "POINTS"
    for name, socket in (("Point 1", p1), ("Point 2", p2), ("Point 3", p3), ("Point 4", p4)):
        gn._link(group, socket, quad.inputs[name])
    geometry = _curve_fill_and_outline(gn, group, quad.outputs["Curve"], line_half_m, line_material, fill_material, label=prefix, location=(1860, row - 120))
    enabled = _compare_int(gn, group, input_node.outputs["しっぽ数"], index, operation="GREATER_EQUAL", label=f"{prefix} 有効", location=(1860, row + 260))
    empty = _empty_geometry(gn, group, label=f"{prefix} なし", location=(1860, row + 420))
    return _switch_geometry(gn, group, enabled, empty, geometry, label=f"{prefix} 表示", location=(2540, row - 40))


def _balloon_body_geometry(gn, group, input_node, width_half_m, height_half_m, line_half_m, line_material, fill_material):
    dynamic = gn._balloon_generated_geometry(group, input_node, width_half_m, height_half_m, line_half_m, line_material, fill_material)
    rect_curve = _quadrilateral_curve(gn, group, input_node, width_half_m, height_half_m, label="矩形フキダシ", location=(-500, 1120))
    rect = _curve_fill_and_outline(gn, group, rect_curve, line_half_m, line_material, fill_material, label="矩形フキダシ", location=(520, 1120))
    octagon_mesh = gn._node(group, "GeometryNodeMeshCircle", label="八角形元", location=(-500, 760))
    octagon_mesh.fill_type = "TRIANGLE_FAN"
    gn._set_default(octagon_mesh.inputs["Vertices"], 8)
    gn._set_default(octagon_mesh.inputs["Radius"], 1.0)
    octagon = gn._deform_balloon_mesh(group, input_node, octagon_mesh.outputs["Mesh"], width_half_m, height_half_m, z=-0.00002, label="八角形フキダシ", location=(-240, 760))
    octagon = gn._set_material(group, octagon, fill_material, label="八角形塗り素材", location=(620, 760))
    is_rect = gn._shape_compare(group, input_node, 1, label="矩形か", location=(1500, 760))
    is_octagon = gn._shape_compare(group, input_node, 7, label="八角形か", location=(1500, 620))
    body = _switch_geometry(gn, group, is_rect, dynamic, rect, label="矩形切替", location=(1720, 760))
    return _switch_geometry(gn, group, is_octagon, body, octagon, label="八角形切替", location=(1940, 760))


def _balloon_tails_geometry(gn, group, input_node, width_half_m, height_half_m, line_half_m, line_material, fill_material):
    tails_join = gn._node(group, "GeometryNodeJoinGeometry", label="しっぽ結合", location=(2860, -2460))
    for index in range(1, 9):
        tail = _tail_geometry(gn, group, input_node, width_half_m, height_half_m, line_half_m, line_material, fill_material, index)
        gn._link(group, tail, tails_join.inputs["Geometry"])
    return tails_join.outputs["Geometry"]


def build_balloon_nodes(group, gn) -> None:
    gn._clear_nodes(group)
    input_node, output_node = gn._group_input_output(group)
    gn._link_settings_to_audit_outputs(group, input_node, output_node, "balloon")
    width_half_m = gn._math_multiply(group, input_node.outputs["幅"], 0.0005, label="幅 mm → 半幅 m", location=(-800, -80))
    height_half_m = gn._math_multiply(group, input_node.outputs["高さ"], 0.0005, label="高さ mm → 半高 m", location=(-800, -240))
    line_half_m = gn._math_multiply(group, input_node.outputs["線幅"], 0.0005, label="線幅 mm → 半径 m", location=(-800, -400))

    line_material = input_node.outputs["線素材"]
    fill_material = input_node.outputs["塗り素材"]
    body = _balloon_body_geometry(gn, group, input_node, width_half_m, height_half_m, line_half_m, line_material, fill_material)
    tails = _balloon_tails_geometry(gn, group, input_node, width_half_m, height_half_m, line_half_m, line_material, fill_material)
    join = gn._node(group, "GeometryNodeJoinGeometry", label="フキダシ本体としっぽ", location=(3080, 260))
    gn._link(group, tails, join.inputs["Geometry"])
    gn._link(group, body, join.inputs["Geometry"])

    sx = gn._node(group, "GeometryNodeSwitch", label="水平反転", location=(3080, 20))
    sx.input_type = "FLOAT"
    gn._link(group, input_node.outputs["水平反転"], sx.inputs["Switch"])
    gn._set_default(sx.inputs["False"], 1.0)
    gn._set_default(sx.inputs["True"], -1.0)
    sy = gn._node(group, "GeometryNodeSwitch", label="垂直反転", location=(3080, -160))
    sy.input_type = "FLOAT"
    gn._link(group, input_node.outputs["垂直反転"], sy.inputs["Switch"])
    gn._set_default(sy.inputs["False"], 1.0)
    gn._set_default(sy.inputs["True"], -1.0)
    scale = gn._combine_xyz(group, sx.outputs["Output"], sy.outputs["Output"], z=1.0, label="反転", location=(3300, -80))
    angle = gn._math_binary(group, "MULTIPLY", input_node.outputs["回転"], b_value=math.pi / 180.0, label="回転角", location=(3300, -300))
    rotation = gn._node(group, "FunctionNodeAxisAngleToRotation", label="回転", location=(3520, -300))
    gn._set_default(rotation.inputs["Axis"], (0.0, 0.0, 1.0))
    gn._link(group, angle, rotation.inputs["Angle"])
    tx = gn._math_binary(group, "MULTIPLY", input_node.outputs["中心点 X"], b_value=0.001, label="中心点X", location=(3300, -520))
    ty = gn._math_binary(group, "MULTIPLY", input_node.outputs["中心点 Y"], b_value=0.001, label="中心点Y", location=(3300, -680))
    translation = gn._combine_xyz(group, tx, ty, z=0.0, label="中心点移動", location=(3520, -600))
    transform = gn._node(group, "GeometryNodeTransform", label="回転・反転を適用", location=(3740, 160))
    gn._link(group, join.outputs["Geometry"], transform.inputs["Geometry"])
    gn._link(group, scale, transform.inputs["Scale"])
    gn._link(group, rotation.outputs["Rotation"], transform.inputs["Rotation"])
    gn._link(group, translation, transform.inputs["Translation"])
    is_none = gn._shape_compare(group, input_node, 9, label="本体なしか", location=(3740, -120))
    empty = _empty_geometry(gn, group, label="本体なし", location=(3740, -280))
    final = _switch_geometry(gn, group, is_none, transform.outputs["Geometry"], empty, label="本体なし切替", location=(3960, 120))
    gn._link(group, final, output_node.inputs["Geometry"])
    group[gn.PROP_GROUP_VERSION] = gn._GROUP_VERSION


def _parallel_line_geometry(
    gn,
    group,
    input_node,
    origin_x_m,
    origin_y_m,
    width_half_m,
    height_half_m,
    radius_socket,
    material_socket,
    *,
    count_socket_name: str,
    angle_socket_name: str,
    label: str,
    location: tuple[float, float],
    use_inout: bool = False,
    span_socket=None,
    step_socket=None,
    length_scale_socket=None,
):
    count = input_node.outputs[count_socket_name]
    width_m = gn._math_binary(group, "MULTIPLY", width_half_m, b_value=2.0, label=f"{label} 幅", location=(location[0], location[1] + 260))
    height_m = gn._math_binary(group, "MULTIPLY", height_half_m, b_value=2.0, label=f"{label} 高さ", location=(location[0], location[1] + 100))
    count_minus = gn._math_binary(group, "SUBTRACT", count, b_value=1.0, label=f"{label} 本数-1", location=(location[0], location[1] - 60))
    count_div = gn._math_binary(group, "MAXIMUM", count_minus, b_value=1.0, label=f"{label} 分母", location=(location[0] + 200, location[1] - 60))
    span = span_socket or height_m
    spacing = step_socket or gn._math_binary(group, "DIVIDE", height_m, count_div, label=f"{label} 間隔", location=(location[0] + 400, location[1] + 40))
    start_y = gn._math_binary(group, "MULTIPLY", span, b_value=-0.5, label=f"{label} 開始Y", location=(location[0] + 400, location[1] - 160))
    start = gn._combine_xyz(group, None, start_y, z=0.0, label=f"{label} 開始位置", location=(location[0] + 620, location[1] - 90))
    offset = gn._combine_xyz(group, None, spacing, z=0.0, label=f"{label} 点間隔", location=(location[0] + 620, location[1] + 80))
    points = gn._node(group, "GeometryNodeMeshLine", label=f"{label} 本数", location=(location[0] + 840, location[1]))
    gn._link(group, count, points.inputs["Count"])
    gn._link(group, start, points.inputs["Start Location"])
    gn._link(group, offset, points.inputs["Offset"])
    right_x = width_half_m
    if length_scale_socket is not None:
        right_x = gn._math_binary(group, "MULTIPLY", width_half_m, length_scale_socket, label=f"{label} 右端長さ", location=(location[0] + 840, location[1] - 220))
    left_x = gn._math_binary(group, "MULTIPLY", right_x, b_value=-1.0, label=f"{label} 左端", location=(location[0] + 840, location[1] - 220))
    p0 = gn._combine_xyz(group, left_x, None, z=0.0, label=f"{label} 線始点", location=(location[0] + 1060, location[1] - 160))
    p1 = gn._combine_xyz(group, right_x, None, z=0.0, label=f"{label} 線終点", location=(location[0] + 1060, location[1] - 360))
    if use_inout:
        material = gn._tapered_line_mesh_x(
            group,
            input_node,
            left_x,
            right_x,
            radius_socket,
            material_socket,
            label=f"{label} 原型",
            location=(location[0] + 1280, location[1] - 240),
        )
    else:
        line = gn._node(group, "GeometryNodeCurvePrimitiveLine", label=f"{label} 原型", location=(location[0] + 1280, location[1] - 240))
        gn._link(group, p0, line.inputs["Start"])
        gn._link(group, p1, line.inputs["End"])
        profile = gn._node(group, "GeometryNodeCurvePrimitiveCircle", label=f"{label} 線幅", location=(location[0] + 1280, location[1] - 480))
        gn._set_default(profile.inputs["Resolution"], 8)
        gn._link(group, radius_socket, profile.inputs["Radius"])
        mesh = gn._node(group, "GeometryNodeCurveToMesh", label=f"{label} メッシュ化", location=(location[0] + 1500, location[1] - 240))
        gn._set_default(mesh.inputs["Fill Caps"], True)
        gn._link(group, line.outputs["Curve"], mesh.inputs["Curve"])
        gn._link(group, profile.outputs["Curve"], mesh.inputs["Profile Curve"])
        material = gn._set_material(group, mesh.outputs["Mesh"], material_socket, label=f"{label} 素材", location=(location[0] + 1720, location[1] - 240))
    angle = gn._math_binary(group, "MULTIPLY", input_node.outputs[angle_socket_name], b_value=math.pi / 180.0, label=f"{label} 角度", location=(location[0] + 1500, location[1] + 180))
    rotation = gn._node(group, "FunctionNodeAxisAngleToRotation", label=f"{label} 回転", location=(location[0] + 1720, location[1] + 160))
    gn._set_default(rotation.inputs["Axis"], (0.0, 0.0, 1.0))
    gn._link(group, angle, rotation.inputs["Angle"])
    instance = gn._node(group, "GeometryNodeInstanceOnPoints", label=f"{label} 配置", location=(location[0] + 1940, location[1] - 40))
    gn._link(group, points.outputs["Mesh"], instance.inputs["Points"])
    gn._link(group, material, instance.inputs["Instance"])
    gn._link(group, rotation.outputs["Rotation"], instance.inputs["Rotation"])
    realize = gn._node(group, "GeometryNodeRealizeInstances", label=f"{label} 実体化", location=(location[0] + 2160, location[1] - 40))
    gn._link(group, instance.outputs["Instances"], realize.inputs["Geometry"])
    cx = gn._math_add(group, origin_x_m, width_half_m, label=f"{label} 中心X", location=(location[0] + 2160, location[1] - 260))
    cy = gn._math_add(group, origin_y_m, height_half_m, label=f"{label} 中心Y", location=(location[0] + 2160, location[1] - 420))
    translation = gn._combine_xyz(group, cx, cy, z=0.0, label=f"{label} 表示位置", location=(location[0] + 2380, location[1] - 340))
    transform = gn._node(group, "GeometryNodeTransform", label=f"{label} 移動", location=(location[0] + 2600, location[1] - 40))
    gn._link(group, realize.outputs["Geometry"], transform.inputs["Geometry"])
    gn._link(group, translation, transform.inputs["Translation"])
    return transform.outputs["Geometry"]


def _effect_rect_fill_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material, *, label: str, location: tuple[float, float]):
    width_m = gn._math_binary(group, "MULTIPLY", width_half_m, b_value=2.0, label=f"{label} 幅", location=(location[0], location[1] + 240))
    height_m = gn._math_binary(group, "MULTIPLY", height_half_m, b_value=2.0, label=f"{label} 高さ", location=(location[0], location[1] + 80))
    quad = gn._node(group, "GeometryNodeCurvePrimitiveQuadrilateral", label=f"{label} 矩形", location=(location[0] + 220, location[1]))
    quad.mode = "RECTANGLE"
    gn._link(group, width_m, quad.inputs["Width"])
    gn._link(group, height_m, quad.inputs["Height"])
    rounded = gn._node(group, "GeometryNodeFilletCurve", label=f"{label} 角丸", location=(location[0] + 440, location[1]))
    gn._link(group, quad.outputs["Curve"], rounded.inputs["Curve"])
    radius_m = gn._math_binary(group, "MULTIPLY", input_node.outputs["終点 角半径"], b_value=0.001, label=f"{label} 角半径", location=(location[0] + 220, location[1] - 200))
    gn._link(group, radius_m, rounded.inputs["Radius"])
    gn._set_default(rounded.inputs["Count"], 10)
    curve = _switch_geometry(gn, group, input_node.outputs["終点 角丸"], quad.outputs["Curve"], rounded.outputs["Curve"], label=f"{label} 角丸切替", location=(location[0] + 660, location[1]))
    center_x = gn._math_add(group, origin_x_m, width_half_m, label=f"{label} 中心X", location=(location[0] + 660, location[1] - 200))
    center_y = gn._math_add(group, origin_y_m, height_half_m, label=f"{label} 中心Y", location=(location[0] + 660, location[1] - 360))
    translation = gn._combine_xyz(group, center_x, center_y, z=-0.00002, label=f"{label} 位置", location=(location[0] + 880, location[1] - 280))
    transform = gn._node(group, "GeometryNodeTransform", label=f"{label} 配置", location=(location[0] + 880, location[1]))
    gn._link(group, curve, transform.inputs["Geometry"])
    gn._link(group, translation, transform.inputs["Translation"])
    fill = gn._node(group, "GeometryNodeFillCurve", label=f"{label} 塗り", location=(location[0] + 1100, location[1]))
    gn._link(group, transform.outputs["Geometry"], fill.inputs["Curve"])
    return gn._set_material(group, fill.outputs["Mesh"], fill_material, label=f"{label} 素材", location=(location[0] + 1320, location[1]))


def _effect_octagon_fill_geometry(gn, group, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material, *, label: str, location: tuple[float, float]):
    fill = gn._node(group, "GeometryNodeMeshCircle", label=f"{label} 八角形", location=location)
    fill.fill_type = "TRIANGLE_FAN"
    gn._set_default(fill.inputs["Vertices"], 8)
    gn._set_default(fill.inputs["Radius"], 1.0)
    scale = gn._combine_xyz(group, width_half_m, height_half_m, z=1.0, label=f"{label} サイズ", location=(location[0] + 220, location[1] - 160))
    center_x = gn._math_add(group, origin_x_m, width_half_m, label=f"{label} 中心X", location=(location[0] + 220, location[1] + 120))
    center_y = gn._math_add(group, origin_y_m, height_half_m, label=f"{label} 中心Y", location=(location[0] + 220, location[1] - 20))
    translation = gn._combine_xyz(group, center_x, center_y, z=-0.00002, label=f"{label} 位置", location=(location[0] + 440, location[1] + 40))
    transform = gn._node(group, "GeometryNodeTransform", label=f"{label} 配置", location=(location[0] + 660, location[1]))
    gn._link(group, fill.outputs["Mesh"], transform.inputs["Geometry"])
    gn._link(group, scale, transform.inputs["Scale"])
    gn._link(group, translation, transform.inputs["Translation"])
    return gn._set_material(group, transform.outputs["Geometry"], fill_material, label=f"{label} 素材", location=(location[0] + 880, location[1]))


def _effect_end_fill_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material):
    ellipse = gn._effect_fill_geometry(group, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material)
    rect = _effect_rect_fill_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material, label="終点矩形下地", location=(760, 620))
    octagon = _effect_octagon_fill_geometry(gn, group, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material, label="終点八角形下地", location=(760, 1320))
    is_rect = _compare_int(gn, group, input_node.outputs["終点形状"], 1, label="終点矩形か", location=(1660, 620))
    is_octagon = _compare_int(gn, group, input_node.outputs["終点形状"], 7, label="終点八角形か", location=(1660, 480))
    selected = _switch_geometry(gn, group, is_rect, ellipse, rect, label="終点矩形下地切替", location=(1880, 620))
    return _switch_geometry(gn, group, is_octagon, selected, octagon, label="終点八角形下地切替", location=(2100, 620))


def build_effect_line_nodes(group, gn) -> None:
    gn._clear_nodes(group)
    input_node, output_node = gn._group_input_output(group)
    gn._link_settings_to_audit_outputs(group, input_node, output_node, "effect_line")
    origin_x_m = gn._math_multiply(group, input_node.outputs["位置 X"], 0.001, label="位置 X mm → m", location=(-800, 260))
    origin_y_m = gn._math_multiply(group, input_node.outputs["位置 Y"], 0.001, label="位置 Y mm → m", location=(-800, 110))
    width_half_m = gn._math_multiply(group, input_node.outputs["幅"], 0.0005, label="幅 mm → 半幅 m", location=(-800, -40))
    height_half_m = gn._math_multiply(group, input_node.outputs["高さ"], 0.0005, label="高さ mm → 半高 m", location=(-800, -190))
    line_half_m = gn._math_multiply(group, input_node.outputs["線幅"], 0.0005, label="線幅 mm → 半径 m", location=(-800, -340))
    line_material = input_node.outputs["線素材"]
    fill_material = input_node.outputs["塗り素材"]
    radial = gn._instanced_radial_line_geometry(group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, line_half_m, line_material)
    fill = _effect_end_fill_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material)
    focus_join = gn._node(group, "GeometryNodeJoinGeometry", label="集中線と下地", location=(1460, -420))
    gn._link(group, fill, focus_join.inputs["Geometry"])
    gn._link(group, radial, focus_join.inputs["Geometry"])
    fill_switch = _switch_geometry(gn, group, input_node.outputs["終点形状を下地として塗る"], radial, focus_join.outputs["Geometry"], label="下地塗り表示", location=(1660, -420))
    speed = _parallel_line_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, line_half_m, line_material, count_socket_name="流線の本数上限", angle_socket_name="流線の角度", label="流線", location=(-520, 920), use_inout=True)
    white_width_min = gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 最小太さ (%)"], b_value=0.01, label="白抜き線 最小太さ率", location=(-820, 2300))
    white_width_factor = gn._math_binary(
        group,
        "MULTIPLY",
        gn._math_add(group, gn._constant_float(group, 1.0, label="白抜き線 太さ基準", location=(-820, 2460)), white_width_min, label="白抜き線 太さ平均", location=(-620, 2380)),
        b_value=0.5,
        label="白抜き線 太さ乱れ係数",
        location=(-420, 2380),
    )
    white_width_factor = gn._switch_float(
        group,
        input_node.outputs["白抜き線 太さ乱れ"],
        gn._constant_float(group, 1.0, label="白抜き線 太さ乱れなし", location=(-420, 2220)),
        white_width_factor,
        label="白抜き線 太さ乱れ切替",
        location=(-220, 2380),
    )
    white_band_width = gn._math_binary(
        group,
        "MULTIPLY",
        gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 太さ"], b_value=0.001, label="白抜き線 太さm", location=(-20, 2380)),
        white_width_factor,
        label="白抜き線 有効太さ",
        location=(180, 2380),
    )
    white_spacing = gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 間隔"], b_value=0.001, label="白抜き線 間隔m", location=(-20, 2220))
    white_step = gn._math_add(group, white_band_width, white_spacing, label="白抜き線 帯間隔", location=(380, 2300))
    white_count_minus = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "SUBTRACT", input_node.outputs["白抜き線 本数"], b_value=1.0, label="白抜き線 本数-1", location=(180, 2140)), b_value=0.0, label="白抜き線 間隔数", location=(380, 2140))
    white_span = gn._math_add(
        group,
        gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 本数"], white_band_width, label="白抜き線 帯合計", location=(580, 2380)),
        gn._math_binary(group, "MULTIPLY", white_count_minus, white_spacing, label="白抜き線 間隔合計", location=(580, 2140)),
        label="白抜き線 全体幅",
        location=(780, 2260),
    )
    white_length_min = gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 最小長さ (%)"], b_value=0.01, label="白抜き線 最小長さ率", location=(-820, 2020))
    white_length_factor = gn._math_binary(
        group,
        "MULTIPLY",
        gn._math_add(group, gn._constant_float(group, 1.0, label="白抜き線 長さ基準", location=(-820, 1860)), white_length_min, label="白抜き線 長さ平均", location=(-620, 1940)),
        b_value=0.5,
        label="白抜き線 長さ乱れ係数",
        location=(-420, 1940),
    )
    white_length_factor = gn._switch_float(
        group,
        input_node.outputs["白抜き線 長さ乱れ"],
        gn._constant_float(group, 1.0, label="白抜き線 長さ乱れなし", location=(-420, 1780)),
        white_length_factor,
        label="白抜き線 長さ乱れ切替",
        location=(-220, 1940),
    )
    white_ratio = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "MULTIPLY", input_node.outputs["白線割合 (%)"], b_value=0.01, label="白線割合", location=(-820, 2740)), b_value=0.01, label="白線割合下限", location=(-620, 2740))
    black_ratio = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "SUBTRACT", gn._constant_float(group, 1.0, label="黒線割合基準", location=(-820, 2900)), white_ratio, label="黒線割合", location=(-420, 2820)), b_value=0.01, label="黒線割合下限", location=(-220, 2820))
    white_attenuation = gn._math_binary(group, "MAXIMUM", gn._math_add(group, gn._constant_float(group, 1.0, label="白線減衰基準", location=(-820, 3060)), gn._math_binary(group, "MULTIPLY", input_node.outputs["白線減衰"], b_value=0.05, label="白線減衰量", location=(-620, 3060)), label="白線減衰係数", location=(-420, 3060)), b_value=0.05, label="白線減衰下限", location=(-220, 3060))
    black_attenuation = gn._math_binary(group, "MAXIMUM", gn._math_add(group, gn._constant_float(group, 1.0, label="黒線減衰基準", location=(-820, 3220)), gn._math_binary(group, "MULTIPLY", input_node.outputs["黒線減衰"], b_value=0.05, label="黒線減衰量", location=(-620, 3220)), label="黒線減衰係数", location=(-420, 3220)), b_value=0.05, label="黒線減衰下限", location=(-220, 3220))
    white_black_radius = gn._math_binary(group, "MULTIPLY", gn._math_binary(group, "MULTIPLY", input_node.outputs["黒線太さ"], b_value=0.0005, label="黒線半径", location=(-520, 2600)), gn._math_binary(group, "MULTIPLY", black_ratio, black_attenuation, label="黒線有効係数", location=(-20, 2820)), label="黒線有効半径", location=(180, 2820))
    white_radius = gn._math_binary(group, "MULTIPLY", gn._math_binary(group, "MULTIPLY", input_node.outputs["白線太さ"], b_value=0.0005, label="白線半径", location=(-520, 2440)), gn._math_binary(group, "MULTIPLY", white_ratio, white_attenuation, label="白線有効係数", location=(-20, 3060)), label="白線有効半径", location=(180, 3060))
    white_black = _parallel_line_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, white_black_radius, line_material, count_socket_name="白抜き線 本数", angle_socket_name="白抜き線 角度", label="白抜き黒線", location=(-320, 2500), span_socket=white_span, step_socket=white_step, length_scale_socket=white_length_factor)
    white_line = _parallel_line_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, white_radius, fill_material, count_socket_name="白抜き線 本数", angle_socket_name="白抜き線 角度", label="白抜き白線", location=(-320, 3700), span_socket=white_span, step_socket=white_step, length_scale_socket=white_length_factor)
    white_join = gn._node(group, "GeometryNodeJoinGeometry", label="白抜き線", location=(2360, 3320))
    gn._link(group, white_black, white_join.inputs["Geometry"])
    gn._link(group, white_line, white_join.inputs["Geometry"])
    is_beta = _compare_int(gn, group, input_node.outputs["種類"], 3, label="ベタフラか", location=(1840, 160))
    is_speed = _compare_int(gn, group, input_node.outputs["種類"], 4, label="流線か", location=(1840, 20))
    is_white = _compare_int(gn, group, input_node.outputs["種類"], 5, label="白抜き線か", location=(1840, -120))
    selected = _switch_geometry(gn, group, is_beta, fill_switch, fill, label="ベタフラ切替", location=(2060, 160))
    selected = _switch_geometry(gn, group, is_speed, selected, speed, label="流線切替", location=(2280, 160))
    selected = _switch_geometry(gn, group, is_white, selected, white_join.outputs["Geometry"], label="白抜き線切替", location=(2500, 160))
    gn._link(group, selected, output_node.inputs["Geometry"])
    group[gn.PROP_GROUP_VERSION] = gn._GROUP_VERSION
