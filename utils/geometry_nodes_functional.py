"""Functional Geometry Nodes builders for B-MANGA visible objects."""

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


def _bool_to_int_socket(gn, group, bool_socket, *, false_value: int, true_value: int, label: str, location: tuple[float, float]):
    node = gn._node(group, "GeometryNodeSwitch", label=label, location=location)
    node.input_type = "INT"
    gn._link(group, bool_socket, node.inputs["Switch"])
    gn._set_default(node.inputs["False"], int(false_value))
    gn._set_default(node.inputs["True"], int(true_value))
    return node.outputs["Output"]


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
    active_count = _bool_to_int_socket(gn, group, enabled, false_value=0, true_value=1, label=f"{prefix} 表示数", location=(1860, row + 420))
    point = gn._node(group, "GeometryNodeMeshLine", label=f"{prefix} 配置点", location=(2080, row + 260))
    gn._link(group, active_count, point.inputs["Count"])
    instance = gn._node(group, "GeometryNodeInstanceOnPoints", label=f"{prefix} 配置", location=(2300, row - 40))
    gn._link(group, point.outputs["Mesh"], instance.inputs["Points"])
    gn._link(group, geometry, instance.inputs["Instance"])
    realize = gn._node(group, "GeometryNodeRealizeInstances", label=f"{prefix} 実体化", location=(2540, row - 40))
    gn._link(group, instance.outputs["Instances"], realize.inputs["Geometry"])
    return realize.outputs["Geometry"]


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
    line_half_m = gn._math_multiply(group, input_node.outputs[gn.LINE_WIDTH_MM_SOCKET], 0.0005, label="線幅 mm → 半径 m", location=(-800, -400))

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
        alpha_mesh = gn._store_alpha_constant(group, mesh.outputs["Mesh"], 1.0, label=f"{label} 不透明度属性", location=(location[0] + 1720, location[1] - 480))
        material = gn._set_material(group, alpha_mesh, material_socket, label=f"{label} 素材", location=(location[0] + 1720, location[1] - 240))
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


def _white_outline_region_geometry(
    gn,
    group,
    input_node,
    origin_x_m,
    origin_y_m,
    width_half_m,
    height_half_m,
    radius_socket,
    *,
    material_socket,
    material_index: int,
    band_width_socket,
    band_spacing_socket,
    region_center_socket,
    region_width_socket,
    attenuation_socket,
    length_scale_socket,
    label: str,
    location: tuple[float, float],
):
    count = input_node.outputs["白抜き線 本数"]
    brush_diameter = gn._math_binary(
        group,
        "MAXIMUM",
        gn._math_binary(group, "MULTIPLY", radius_socket, b_value=2.0, label=f"{label} 線幅", location=(location[0], location[1] + 620)),
        b_value=0.000001,
        label=f"{label} 線幅下限",
        location=(location[0] + 200, location[1] + 620),
    )
    line_float = gn._math_binary(
        group,
        "CEIL",
        gn._math_binary(group, "DIVIDE", region_width_socket, brush_diameter, label=f"{label} 領域内本数", location=(location[0], location[1] + 460)),
        label=f"{label} 領域内本数切上げ",
        location=(location[0] + 200, location[1] + 460),
    )
    line_float = gn._math_binary(group, "MAXIMUM", line_float, b_value=1.0, label=f"{label} 領域内本数下限", location=(location[0] + 400, location[1] + 460))
    total_count = _float_to_int(
        gn,
        group,
        gn._math_binary(group, "MULTIPLY", count, line_float, label=f"{label} 総本数", location=(location[0] + 600, location[1] + 620)),
        label=f"{label} 総本数整数",
        location=(location[0] + 800, location[1] + 620),
        mode="CEILING",
    )

    points = gn._node(group, "GeometryNodeMeshLine", label=f"{label} 点列", location=(location[0] + 1020, location[1] + 520))
    gn._link(group, total_count, points.inputs["Count"])
    zero = gn._constant_float(group, 0.0, label=f"{label} 0", location=(location[0] + 800, location[1] + 320))
    zero_vector = gn._combine_xyz(group, zero, zero, z=0.0, label=f"{label} ゼロ位置", location=(location[0] + 1020, location[1] + 320))
    gn._link(group, zero_vector, points.inputs["Start Location"])
    gn._link(group, zero_vector, points.inputs["Offset"])

    index = gn._node(group, "GeometryNodeInputIndex", label=f"{label} 線番号", location=(location[0] + 1020, location[1] + 120))
    band_index = gn._math_binary(
        group,
        "FLOOR",
        gn._math_binary(group, "DIVIDE", index.outputs["Index"], line_float, label=f"{label} 帯番号", location=(location[0] + 1220, location[1] + 200)),
        label=f"{label} 帯番号整数",
        location=(location[0] + 1420, location[1] + 200),
    )
    local_index = gn._math_binary(group, "MODULO", index.outputs["Index"], line_float, label=f"{label} 帯内番号", location=(location[0] + 1220, location[1] + 20))
    band_step = gn._math_add(group, band_width_socket, band_spacing_socket, label=f"{label} 帯間隔", location=(location[0] + 1220, location[1] + 380))
    count_minus = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "SUBTRACT", count, b_value=1.0, label=f"{label} 本数-1", location=(location[0] + 1220, location[1] + 560)), b_value=0.0, label=f"{label} 間隔数", location=(location[0] + 1420, location[1] + 560))
    total_span = gn._math_add(
        group,
        gn._math_binary(group, "MULTIPLY", count, band_width_socket, label=f"{label} 帯幅合計", location=(location[0] + 1620, location[1] + 560)),
        gn._math_binary(group, "MULTIPLY", count_minus, band_spacing_socket, label=f"{label} 帯間隔合計", location=(location[0] + 1620, location[1] + 400)),
        label=f"{label} 全体幅",
        location=(location[0] + 1820, location[1] + 480),
    )
    band_start = gn._math_binary(group, "MULTIPLY", total_span, b_value=-0.5, label=f"{label} 帯開始", location=(location[0] + 2020, location[1] + 480))
    band_center = gn._math_add(
        group,
        gn._math_add(
            group,
            band_start,
            gn._math_binary(group, "MULTIPLY", band_index, band_step, label=f"{label} 帯オフセット", location=(location[0] + 2020, location[1] + 300)),
            label=f"{label} 帯左端",
            location=(location[0] + 2220, location[1] + 380),
        ),
        gn._math_binary(group, "MULTIPLY", band_width_socket, b_value=0.5, label=f"{label} 帯半幅", location=(location[0] + 2220, location[1] + 220)),
        label=f"{label} 帯中心",
        location=(location[0] + 2420, location[1] + 300),
    )

    unit_width = gn._math_binary(group, "DIVIDE", region_width_socket, line_float, label=f"{label} 領域単位幅", location=(location[0] + 1420, location[1] - 180))
    local_offset = gn._math_add(
        group,
        gn._math_binary(group, "MULTIPLY", region_width_socket, b_value=-0.5, label=f"{label} 領域開始", location=(location[0] + 1620, location[1] - 180)),
        gn._math_binary(
            group,
            "MULTIPLY",
            gn._math_add(group, local_index, gn._constant_float(group, 0.5, label=f"{label} 中央補正", location=(location[0] + 1420, location[1] - 340)), label=f"{label} 帯内中央", location=(location[0] + 1620, location[1] - 340)),
            unit_width,
            label=f"{label} 帯内位置",
            location=(location[0] + 1820, location[1] - 260),
        ),
        label=f"{label} 領域内位置",
        location=(location[0] + 2020, location[1] - 220),
    )
    offset_from_band_center = gn._math_add(group, region_center_socket, local_offset, label=f"{label} 帯中心からの距離", location=(location[0] + 2220, location[1] - 220))
    y = gn._math_add(group, band_center, offset_from_band_center, label=f"{label} Y位置", location=(location[0] + 2620, location[1] + 40))
    point_position = gn._combine_xyz(group, None, y, z=0.0, label=f"{label} 点位置", location=(location[0] + 2820, location[1] + 40))
    set_pos = gn._node(group, "GeometryNodeSetPosition", label=f"{label} 点配置", location=(location[0] + 3040, location[1] + 320))
    gn._link(group, points.outputs["Mesh"], set_pos.inputs["Geometry"])
    gn._link(group, point_position, set_pos.inputs["Position"])

    line = gn._node(group, "GeometryNodeCurvePrimitiveLine", label=f"{label} 原型", location=(location[0] + 3040, location[1] - 260))
    line_start = gn._combine_xyz(group, gn._constant_float(group, -1.0, label=f"{label} 原型左", location=(location[0] + 2820, location[1] - 360)), zero, z=0.0, label=f"{label} 原型始点", location=(location[0] + 3040, location[1] - 520))
    line_end = gn._combine_xyz(group, gn._constant_float(group, 1.0, label=f"{label} 原型右", location=(location[0] + 2820, location[1] - 700)), zero, z=0.0, label=f"{label} 原型終点", location=(location[0] + 3040, location[1] - 700))
    gn._link(group, line_start, line.inputs["Start"])
    gn._link(group, line_end, line.inputs["End"])
    profile = gn._node(group, "GeometryNodeCurvePrimitiveCircle", label=f"{label} 線幅", location=(location[0] + 3260, location[1] - 520))
    gn._set_default(profile.inputs["Resolution"], 8)
    gn._link(group, radius_socket, profile.inputs["Radius"])
    mesh = gn._node(group, "GeometryNodeCurveToMesh", label=f"{label} メッシュ化", location=(location[0] + 3480, location[1] - 320))
    gn._set_default(mesh.inputs["Fill Caps"], True)
    gn._link(group, line.outputs["Curve"], mesh.inputs["Curve"])
    gn._link(group, profile.outputs["Curve"], mesh.inputs["Profile Curve"])
    alpha_mesh = gn._store_alpha_constant(group, mesh.outputs["Mesh"], 1.0, label=f"{label} 不透明度属性", location=(location[0] + 3700, location[1] - 520))

    band_half = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "MULTIPLY", band_width_socket, b_value=0.5, label=f"{label} 減衰半幅", location=(location[0] + 2420, location[1] - 360)), b_value=0.000001, label=f"{label} 減衰半幅下限", location=(location[0] + 2620, location[1] - 360))
    norm = gn._math_binary(group, "DIVIDE", gn._math_binary(group, "ABSOLUTE", offset_from_band_center, label=f"{label} 減衰距離", location=(location[0] + 2420, location[1] - 560)), band_half, label=f"{label} 減衰率", location=(location[0] + 2820, location[1] - 460))
    attenuation = gn._math_binary(group, "MULTIPLY", attenuation_socket, b_value=0.01, label=f"{label} 減衰入力", location=(location[0] + 2820, location[1] - 640))
    attenuation_factor = gn._math_binary(
        group,
        "MAXIMUM",
        gn._math_binary(group, "SUBTRACT", gn._constant_float(group, 1.0, label=f"{label} 長さ基準", location=(location[0] + 3020, location[1] - 820)), gn._math_binary(group, "MULTIPLY", attenuation, norm, label=f"{label} 減衰量", location=(location[0] + 3020, location[1] - 560)), label=f"{label} 減衰後", location=(location[0] + 3220, location[1] - 640)),
        b_value=0.0,
        label=f"{label} 減衰下限",
        location=(location[0] + 3420, location[1] - 640),
    )
    half_length = gn._math_binary(
        group,
        "SQRT",
        gn._math_add(
            group,
            gn._math_binary(group, "MULTIPLY", width_half_m, width_half_m, label=f"{label} 幅二乗", location=(location[0] + 6280, location[1] - 900)),
            gn._math_binary(group, "MULTIPLY", height_half_m, height_half_m, label=f"{label} 高さ二乗", location=(location[0] + 6280, location[1] - 1060)),
            label=f"{label} 対角二乗",
            location=(location[0] + 6480, location[1] - 980),
        ),
        label=f"{label} 半長",
        location=(location[0] + 6680, location[1] - 980),
    )
    length_scale = gn._math_binary(group, "MULTIPLY", half_length, gn._math_binary(group, "MULTIPLY", attenuation_factor, length_scale_socket, label=f"{label} 長さ係数", location=(location[0] + 6680, location[1] - 740)), label=f"{label} 長さ", location=(location[0] + 6880, location[1] - 820))
    scale = gn._combine_xyz(group, length_scale, gn._constant_float(group, 1.0, label=f"{label} Y等倍", location=(location[0] + 6880, location[1] - 620)), z=1.0, label=f"{label} スケール", location=(location[0] + 7080, location[1] - 720))

    angle = gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 角度"], b_value=math.pi / 180.0, label=f"{label} 角度", location=(location[0] + 6680, location[1] - 420))
    rotation = gn._node(group, "FunctionNodeAxisAngleToRotation", label=f"{label} 回転", location=(location[0] + 6900, location[1] - 420))
    gn._set_default(rotation.inputs["Axis"], (0.0, 0.0, 1.0))
    gn._link(group, angle, rotation.inputs["Angle"])
    instance = gn._node(group, "GeometryNodeInstanceOnPoints", label=f"{label} 配置", location=(location[0] + 7300, location[1] + 120))
    gn._link(group, set_pos.outputs["Geometry"], instance.inputs["Points"])
    gn._link(group, alpha_mesh, instance.inputs["Instance"])
    gn._link(group, rotation.outputs["Rotation"], instance.inputs["Rotation"])
    gn._link(group, scale, instance.inputs["Scale"])
    realize = gn._node(group, "GeometryNodeRealizeInstances", label=f"{label} 実体化", location=(location[0] + 7520, location[1] + 120))
    gn._link(group, instance.outputs["Instances"], realize.inputs["Geometry"])
    material = gn._set_material(group, realize.outputs["Geometry"], material_socket, label=f"{label} 素材", location=(location[0] + 7520, location[1] + 300))
    material = gn._set_material_index(group, material, int(material_index), label=f"{label} 素材番号", location=(location[0] + 7740, location[1] + 300))
    cx = gn._math_add(group, origin_x_m, width_half_m, label=f"{label} 中心X", location=(location[0] + 7520, location[1] - 100))
    cy = gn._math_add(group, origin_y_m, height_half_m, label=f"{label} 中心Y", location=(location[0] + 7520, location[1] - 260))
    translation = gn._combine_xyz(group, cx, cy, z=0.0, label=f"{label} 表示位置", location=(location[0] + 7740, location[1] - 180))
    transform = gn._node(group, "GeometryNodeTransform", label=f"{label} 移動", location=(location[0] + 7960, location[1] + 120))
    gn._link(group, material, transform.inputs["Geometry"])
    gn._link(group, translation, transform.inputs["Translation"])
    return transform.outputs["Geometry"]


def _boolean_math(gn, group, operation: str, a_socket, b_socket=None, *, label: str, location: tuple[float, float]):
    node = gn._node(group, "FunctionNodeBooleanMath", label=label, location=location)
    node.operation = operation
    gn._link(group, a_socket, node.inputs[0])
    if b_socket is not None and len(node.inputs) > 1:
        gn._link(group, b_socket, node.inputs[1])
    return node.outputs["Boolean"]


def _white_outline_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, line_material, fill_material):
    location = (-320, 2600)
    count = input_node.outputs["白抜き線 本数"]
    width_min = gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 最小太さ (%)"], b_value=0.01, label="白抜き線 最小太さ率", location=(location[0], location[1] + 620))
    band_width = gn._math_binary(group, "MULTIPLY", input_node.outputs[gn.WHITE_OUTLINE_WIDTH_MM_SOCKET], b_value=0.001, label="白抜き線 太さm", location=(location[0] + 880, location[1] + 700))
    spacing = gn._math_binary(group, "MULTIPLY", input_node.outputs[gn.WHITE_OUTLINE_SPACING_MM_SOCKET], b_value=0.001, label="白抜き線 間隔m", location=(location[0] + 880, location[1] + 520))

    ratio = gn._math_binary(group, "MINIMUM", gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "MULTIPLY", input_node.outputs["白線割合 (%)"], b_value=0.01, label="白線割合", location=(location[0], location[1] + 1020)), b_value=0.0, label="白線割合下限", location=(location[0] + 220, location[1] + 1020)), b_value=1.0, label="白線割合上限", location=(location[0] + 440, location[1] + 1020))
    white_width = gn._math_binary(group, "MULTIPLY", band_width, ratio, label="白抜き線 白領域幅", location=(location[0] + 660, location[1] + 1020))
    black_width = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "MULTIPLY", gn._math_binary(group, "SUBTRACT", band_width, white_width, label="白抜き線 黒領域合計", location=(location[0] + 880, location[1] + 1020)), b_value=0.5, label="白抜き線 黒領域幅", location=(location[0] + 1100, location[1] + 1020)), b_value=0.0, label="白抜き線 黒領域幅下限", location=(location[0] + 1320, location[1] + 1020))
    white_half = gn._math_binary(group, "MULTIPLY", white_width, b_value=0.5, label="白抜き線 白半幅", location=(location[0] + 1320, location[1] + 860))
    black_half = gn._math_binary(group, "MULTIPLY", black_width, b_value=0.5, label="白抜き線 黒半幅", location=(location[0] + 1540, location[1] + 860))
    black_left_center = gn._math_binary(group, "MULTIPLY", gn._math_add(group, white_half, black_half, label="白抜き線 黒左距離", location=(location[0] + 1760, location[1] + 860)), b_value=-1.0, label="白抜き線 黒左中心", location=(location[0] + 1980, location[1] + 860))
    black_right_center = gn._math_add(group, white_half, black_half, label="白抜き線 黒右中心", location=(location[0] + 1980, location[1] + 1020))

    black_diameter = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "MULTIPLY", input_node.outputs[gn.WHITE_OUTLINE_BLACK_BRUSH_MM_SOCKET], b_value=0.001, label="黒線太さm", location=(location[0] + 1320, location[1] + 620)), b_value=0.000001, label="黒線太さ下限", location=(location[0] + 1540, location[1] + 620))
    white_diameter = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "MULTIPLY", input_node.outputs[gn.WHITE_OUTLINE_WHITE_BRUSH_MM_SOCKET], b_value=0.001, label="白線太さm", location=(location[0] + 1320, location[1] + 460)), b_value=0.000001, label="白線太さ下限", location=(location[0] + 1540, location[1] + 460))
    black_line_count = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "CEIL", gn._math_binary(group, "DIVIDE", black_width, black_diameter, label="黒領域本数", location=(location[0] + 1760, location[1] + 620)), label="黒領域本数切上げ", location=(location[0] + 1980, location[1] + 620)), b_value=1.0, label="黒領域本数下限", location=(location[0] + 2200, location[1] + 620))
    white_line_count = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "CEIL", gn._math_binary(group, "DIVIDE", white_width, white_diameter, label="白領域本数", location=(location[0] + 1760, location[1] + 460)), label="白領域本数切上げ", location=(location[0] + 1980, location[1] + 460)), b_value=1.0, label="白領域本数下限", location=(location[0] + 2200, location[1] + 460))
    per_band_count = gn._math_add(group, gn._math_binary(group, "MULTIPLY", black_line_count, b_value=2.0, label="黒領域左右本数", location=(location[0] + 2420, location[1] + 620)), white_line_count, label="帯内本数", location=(location[0] + 2640, location[1] + 540))
    raw_total_count = gn._math_binary(group, "MULTIPLY", count, per_band_count, label="白抜き線 総本数", location=(location[0] + 2860, location[1] + 540))
    is_effect_white = _compare_int(gn, group, input_node.outputs["種類"], 5, label="白抜き線表示中", location=(location[0] + 2860, location[1] + 720))
    active_total_count = gn._math_binary(
        group,
        "MULTIPLY",
        raw_total_count,
        gn._switch_float(
            group,
            is_effect_white,
            gn._constant_float(group, 0.0, label="白抜き線 非表示数", location=(location[0] + 2860, location[1] + 900)),
            gn._constant_float(group, 1.0, label="白抜き線 表示数", location=(location[0] + 2860, location[1] + 820)),
            label="白抜き線 表示切替",
            location=(location[0] + 3080, location[1] + 820),
        ),
        label="白抜き線 有効総本数",
        location=(location[0] + 3300, location[1] + 720),
    )
    total_count = _float_to_int(gn, group, active_total_count, label="白抜き線 総本数整数", location=(location[0] + 3080, location[1] + 540), mode="CEILING")

    points = gn._node(group, "GeometryNodeMeshLine", label="白抜き線 点列", location=(location[0] + 3300, location[1] + 540))
    gn._link(group, total_count, points.inputs["Count"])
    zero = gn._constant_float(group, 0.0, label="白抜き線 0", location=(location[0] + 3080, location[1] + 340))
    zero_vector = gn._combine_xyz(group, zero, zero, z=0.0, label="白抜き線 ゼロ位置", location=(location[0] + 3300, location[1] + 340))
    gn._link(group, zero_vector, points.inputs["Start Location"])
    gn._link(group, zero_vector, points.inputs["Offset"])
    index = gn._node(group, "GeometryNodeInputIndex", label="白抜き線 番号", location=(location[0] + 3300, location[1] + 140))
    index_socket = index.outputs["Index"]
    band_index = gn._math_binary(group, "FLOOR", gn._math_binary(group, "DIVIDE", index_socket, per_band_count, label="白抜き線 帯番号", location=(location[0] + 3520, location[1] + 260)), label="白抜き線 帯番号整数", location=(location[0] + 3740, location[1] + 260))
    band_seed = gn._math_add(group, band_index, input_node.outputs["乱数"], label="白抜き線 帯乱数種", location=(location[0] + 3740, location[1] + 440))
    width_wave = gn._hash01_socket(group, band_seed, salt=19.73, label="白抜き線 太さ乱れ乱数", location=(location[0] + 3960, location[1] + 500))
    width_factor = gn._math_add(
        group,
        width_min,
        gn._math_binary(
            group,
            "MULTIPLY",
            gn._math_binary(group, "SUBTRACT", gn._constant_float(group, 1.0, label="白抜き線 太さ最大", location=(location[0] + 3960, location[1] + 680)), width_min, label="白抜き線 太さ乱れ幅", location=(location[0] + 4180, location[1] + 620)),
            width_wave,
            label="白抜き線 太さ乱れ量",
            location=(location[0] + 4400, location[1] + 560),
        ),
        label="白抜き線 太さ乱れ係数",
        location=(location[0] + 4620, location[1] + 560),
    )
    width_factor = gn._switch_float(group, input_node.outputs["白抜き線 太さ乱れ"], gn._constant_float(group, 1.0, label="白抜き線 太さ乱れなし", location=(location[0] + 4620, location[1] + 720)), width_factor, label="白抜き線 太さ乱れ切替", location=(location[0] + 4840, location[1] + 560))
    local_index = gn._math_binary(group, "MODULO", index_socket, per_band_count, label="白抜き線 帯内番号", location=(location[0] + 3520, location[1] + 80))
    left_done = gn._compare_float_sockets(group, local_index, black_line_count, operation="GREATER_EQUAL", label="白抜き線 左黒後", location=(location[0] + 3740, location[1] + 80))
    white_done_at = gn._math_add(group, black_line_count, white_line_count, label="白抜き線 白領域終端", location=(location[0] + 3740, location[1] - 80))
    white_done = gn._compare_float_sockets(group, local_index, white_done_at, operation="GREATER_EQUAL", label="白抜き線 白後", location=(location[0] + 3960, location[1] - 80))
    is_white = _boolean_math(gn, group, "AND", left_done, _boolean_math(gn, group, "NOT", white_done, label="白抜き線 白後でない", location=(location[0] + 4180, location[1] - 80)), label="白抜き線 白線か", location=(location[0] + 4400, location[1]))

    white_local = gn._math_binary(group, "SUBTRACT", local_index, black_line_count, label="白抜き線 白内番号", location=(location[0] + 3960, location[1] + 200))
    right_local = gn._math_binary(group, "SUBTRACT", local_index, white_done_at, label="白抜き線 右黒内番号", location=(location[0] + 3960, location[1] + 360))
    non_left_local = gn._switch_float(group, white_done, white_local, right_local, label="白抜き線 非左番号", location=(location[0] + 4180, location[1] + 280))
    region_local = gn._switch_float(group, left_done, local_index, non_left_local, label="白抜き線 領域内番号", location=(location[0] + 4400, location[1] + 280))
    non_left_width = gn._switch_float(group, white_done, white_width, black_width, label="白抜き線 非左幅", location=(location[0] + 4180, location[1] + 500))
    region_width = gn._switch_float(group, left_done, black_width, non_left_width, label="白抜き線 領域幅", location=(location[0] + 4400, location[1] + 500))
    non_left_count = gn._switch_float(group, white_done, white_line_count, black_line_count, label="白抜き線 非左本数", location=(location[0] + 4180, location[1] + 660))
    region_count = gn._switch_float(group, left_done, black_line_count, non_left_count, label="白抜き線 領域本数", location=(location[0] + 4400, location[1] + 660))
    non_left_center = gn._switch_float(group, white_done, zero, black_right_center, label="白抜き線 非左中心", location=(location[0] + 4180, location[1] + 820))
    region_center = gn._switch_float(group, left_done, black_left_center, non_left_center, label="白抜き線 領域中心", location=(location[0] + 4400, location[1] + 820))

    effective_region_width = gn._math_binary(group, "MULTIPLY", region_width, width_factor, label="白抜き線 有効領域幅", location=(location[0] + 4620, location[1] + 500))
    effective_region_center = gn._math_binary(group, "MULTIPLY", region_center, width_factor, label="白抜き線 有効領域中心", location=(location[0] + 4620, location[1] + 820))
    unit_width = gn._math_binary(group, "DIVIDE", effective_region_width, gn._math_binary(group, "MAXIMUM", region_count, b_value=1.0, label="白抜き線 領域本数下限", location=(location[0] + 4620, location[1] + 660)), label="白抜き線 領域単位幅", location=(location[0] + 4840, location[1] + 500))
    local_offset = gn._math_add(group, gn._math_binary(group, "MULTIPLY", effective_region_width, b_value=-0.5, label="白抜き線 領域開始", location=(location[0] + 5060, location[1] + 500)), gn._math_binary(group, "MULTIPLY", gn._math_add(group, region_local, gn._constant_float(group, 0.5, label="白抜き線 中央補正", location=(location[0] + 4620, location[1] + 300)), label="白抜き線 中央番号", location=(location[0] + 4840, location[1] + 300)), unit_width, label="白抜き線 領域内位置", location=(location[0] + 5060, location[1] + 400)), label="白抜き線 ローカル位置", location=(location[0] + 5280, location[1] + 460))
    offset_from_band_center = gn._math_add(group, effective_region_center, local_offset, label="白抜き線 帯中心距離", location=(location[0] + 5500, location[1] + 520))
    band_step = gn._math_add(group, band_width, spacing, label="白抜き線 帯間隔", location=(location[0] + 3300, location[1] + 860))
    count_minus = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "SUBTRACT", count, b_value=1.0, label="白抜き線 本数-1", location=(location[0] + 3300, location[1] + 1040)), b_value=0.0, label="白抜き線 間隔数", location=(location[0] + 3520, location[1] + 1040))
    total_span = gn._math_add(group, gn._math_binary(group, "MULTIPLY", count, band_width, label="白抜き線 帯幅合計", location=(location[0] + 3740, location[1] + 1040)), gn._math_binary(group, "MULTIPLY", count_minus, spacing, label="白抜き線 帯間隔合計", location=(location[0] + 3740, location[1] + 880)), label="白抜き線 全体幅", location=(location[0] + 3960, location[1] + 960))
    band_start = gn._math_binary(group, "MULTIPLY", total_span, b_value=-0.5, label="白抜き線 帯開始", location=(location[0] + 4180, location[1] + 960))
    band_center = gn._math_add(group, gn._math_add(group, band_start, gn._math_binary(group, "MULTIPLY", band_index, band_step, label="白抜き線 帯オフセット", location=(location[0] + 4400, location[1] + 1040)), label="白抜き線 帯左端", location=(location[0] + 4620, location[1] + 960)), gn._math_binary(group, "MULTIPLY", band_width, b_value=0.5, label="白抜き線 帯半幅", location=(location[0] + 4620, location[1] + 820)), label="白抜き線 帯中心", location=(location[0] + 4840, location[1] + 900))
    y = gn._math_add(group, band_center, offset_from_band_center, label="白抜き線 Y位置", location=(location[0] + 5720, location[1] + 700))
    point_position = gn._combine_xyz(group, None, y, z=0.0, label="白抜き線 点位置", location=(location[0] + 5940, location[1] + 700))
    set_pos = gn._node(group, "GeometryNodeSetPosition", label="白抜き線 点配置", location=(location[0] + 6160, location[1] + 540))
    gn._link(group, points.outputs["Mesh"], set_pos.inputs["Geometry"])
    gn._link(group, point_position, set_pos.inputs["Position"])

    quad = gn._node(group, "GeometryNodeCurvePrimitiveQuadrilateral", label="白抜き線 原型", location=(location[0] + 6160, location[1] + 220))
    quad.mode = "RECTANGLE"
    gn._set_default(quad.inputs["Width"], 2.0)
    gn._set_default(quad.inputs["Height"], 1.0)
    fill = gn._node(group, "GeometryNodeFillCurve", label="白抜き線 原型塗り", location=(location[0] + 6380, location[1] + 220))
    gn._link(group, quad.outputs["Curve"], fill.inputs["Curve"])
    alpha_mesh = gn._store_alpha_constant(group, fill.outputs["Mesh"], 1.0, label="白抜き線 不透明度属性", location=(location[0] + 6600, location[1] + 220))

    length_min = gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 最小長さ (%)"], b_value=0.01, label="白抜き線 最小長さ率", location=(location[0] + 5500, location[1] - 40))
    length_wave = gn._hash01_socket(group, band_seed, salt=61.91, label="白抜き線 長さ乱れ乱数", location=(location[0] + 5720, location[1] - 200))
    length_factor = gn._math_add(
        group,
        length_min,
        gn._math_binary(
            group,
            "MULTIPLY",
            gn._math_binary(group, "SUBTRACT", gn._constant_float(group, 1.0, label="白抜き線 長さ最大", location=(location[0] + 5720, location[1] - 380)), length_min, label="白抜き線 長さ乱れ幅", location=(location[0] + 5940, location[1] - 300)),
            length_wave,
            label="白抜き線 長さ乱れ量",
            location=(location[0] + 6160, location[1] - 240),
        ),
        label="白抜き線 長さ乱れ係数",
        location=(location[0] + 6380, location[1] - 240),
    )
    length_factor = gn._switch_float(group, input_node.outputs["白抜き線 長さ乱れ"], gn._constant_float(group, 1.0, label="白抜き線 長さ乱れなし", location=(location[0] + 6380, location[1] - 400)), length_factor, label="白抜き線 長さ乱れ切替", location=(location[0] + 6600, location[1] - 240))
    band_half = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "MULTIPLY", band_width, b_value=0.5, label="白抜き線 減衰半幅", location=(location[0] + 5500, location[1] - 440)), b_value=0.000001, label="白抜き線 減衰半幅下限", location=(location[0] + 5720, location[1] - 440))
    norm = gn._math_binary(group, "DIVIDE", gn._math_binary(group, "ABSOLUTE", offset_from_band_center, label="白抜き線 減衰距離", location=(location[0] + 5940, location[1] - 440)), band_half, label="白抜き線 減衰率", location=(location[0] + 6160, location[1] - 440))
    attenuation = gn._math_binary(group, "MULTIPLY", gn._switch_float(group, is_white, input_node.outputs["黒線減衰"], input_node.outputs["白線減衰"], label="白抜き線 減衰選択", location=(location[0] + 6160, location[1] - 620)), b_value=0.01, label="白抜き線 減衰入力", location=(location[0] + 6380, location[1] - 620))
    attenuation_factor = gn._math_binary(group, "MAXIMUM", gn._math_binary(group, "SUBTRACT", gn._constant_float(group, 1.0, label="白抜き線 長さ通常", location=(location[0] + 6380, location[1] - 800)), gn._math_binary(group, "MULTIPLY", attenuation, norm, label="白抜き線 減衰量", location=(location[0] + 6600, location[1] - 620)), label="白抜き線 減衰後", location=(location[0] + 6820, location[1] - 700)), b_value=0.0, label="白抜き線 減衰下限", location=(location[0] + 7040, location[1] - 700))
    half_length = gn._math_binary(group, "SQRT", gn._math_add(group, gn._math_binary(group, "MULTIPLY", width_half_m, width_half_m, label="白抜き線 幅二乗", location=(location[0] + 6820, location[1] - 980)), gn._math_binary(group, "MULTIPLY", height_half_m, height_half_m, label="白抜き線 高さ二乗", location=(location[0] + 6820, location[1] - 1140)), label="白抜き線 対角二乗", location=(location[0] + 7040, location[1] - 1060)), label="白抜き線 半長", location=(location[0] + 7260, location[1] - 1060))
    length_scale = gn._math_binary(group, "MULTIPLY", half_length, gn._math_binary(group, "MULTIPLY", attenuation_factor, length_factor, label="白抜き線 長さ係数", location=(location[0] + 7260, location[1] - 860)), label="白抜き線 長さ", location=(location[0] + 7480, location[1] - 960))
    brush_diameter = gn._switch_float(group, is_white, black_diameter, white_diameter, label="白抜き線 線幅選択", location=(location[0] + 7480, location[1] - 760))
    scale = gn._combine_xyz(group, length_scale, brush_diameter, z=1.0, label="白抜き線 スケール", location=(location[0] + 7700, location[1] - 860))
    angle = gn._math_binary(group, "MULTIPLY", input_node.outputs["白抜き線 角度"], b_value=math.pi / 180.0, label="白抜き線 角度", location=(location[0] + 7480, location[1] - 520))
    rotation = gn._node(group, "FunctionNodeAxisAngleToRotation", label="白抜き線 回転", location=(location[0] + 7700, location[1] - 520))
    gn._set_default(rotation.inputs["Axis"], (0.0, 0.0, 1.0))
    gn._link(group, angle, rotation.inputs["Angle"])
    instance = gn._node(group, "GeometryNodeInstanceOnPoints", label="白抜き線 配置", location=(location[0] + 7920, location[1] + 220))
    gn._link(group, set_pos.outputs["Geometry"], instance.inputs["Points"])
    gn._link(group, alpha_mesh, instance.inputs["Instance"])
    gn._link(group, rotation.outputs["Rotation"], instance.inputs["Rotation"])
    gn._link(group, scale, instance.inputs["Scale"])
    realize = gn._node(group, "GeometryNodeRealizeInstances", label="白抜き線 実体化", location=(location[0] + 8140, location[1] + 220))
    gn._link(group, instance.outputs["Instances"], realize.inputs["Geometry"])

    set_black = gn._node(group, "GeometryNodeSetMaterial", label="白抜き線 黒素材", location=(location[0] + 8360, location[1] + 220))
    gn._link(group, realize.outputs["Geometry"], set_black.inputs["Geometry"])
    gn._link(group, _boolean_math(gn, group, "NOT", is_white, label="白抜き線 黒線か", location=(location[0] + 8140, location[1] - 40)), set_black.inputs["Selection"])
    gn._link(group, line_material, set_black.inputs["Material"])
    set_white = gn._node(group, "GeometryNodeSetMaterial", label="白抜き線 白素材", location=(location[0] + 8580, location[1] + 220))
    gn._link(group, set_black.outputs["Geometry"], set_white.inputs["Geometry"])
    gn._link(group, is_white, set_white.inputs["Selection"])
    gn._link(group, fill_material, set_white.inputs["Material"])
    cx = gn._math_add(group, origin_x_m, width_half_m, label="白抜き線 中心X", location=(location[0] + 8580, location[1] - 20))
    cy = gn._math_add(group, origin_y_m, height_half_m, label="白抜き線 中心Y", location=(location[0] + 8580, location[1] - 180))
    translation = gn._combine_xyz(group, cx, cy, z=0.0, label="白抜き線 表示位置", location=(location[0] + 8800, location[1] - 100))
    transform = gn._node(group, "GeometryNodeTransform", label="白抜き線 移動", location=(location[0] + 9020, location[1] + 220))
    gn._link(group, set_white.outputs["Geometry"], transform.inputs["Geometry"])
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
    line_half_m = gn._math_multiply(group, input_node.outputs[gn.LINE_WIDTH_MM_SOCKET], 0.0005, label="線幅 mm → 半径 m", location=(-800, -340))
    line_material = input_node.outputs["線素材"]
    fill_material = input_node.outputs["塗り素材"]
    radial = gn._instanced_radial_line_geometry(group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, line_half_m, line_material, fill_material)
    fill = _effect_end_fill_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material)
    focus_join = gn._node(group, "GeometryNodeJoinGeometry", label="集中線と下地", location=(1460, -420))
    gn._link(group, fill, focus_join.inputs["Geometry"])
    gn._link(group, radial, focus_join.inputs["Geometry"])
    fill_switch = _switch_geometry(gn, group, input_node.outputs["終点形状を下地として塗る"], radial, focus_join.outputs["Geometry"], label="下地塗り表示", location=(1660, -420))
    speed = _parallel_line_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, line_half_m, line_material, count_socket_name="流線の本数上限", angle_socket_name="流線の角度", label="流線", location=(-520, 920), use_inout=True)
    white_geometry = _white_outline_geometry(gn, group, input_node, origin_x_m, origin_y_m, width_half_m, height_half_m, line_material, fill_material)
    is_beta = _compare_int(gn, group, input_node.outputs["種類"], 3, label="ベタフラか", location=(1840, 160))
    is_speed = _compare_int(gn, group, input_node.outputs["種類"], 4, label="流線か", location=(1840, 20))
    is_white = _compare_int(gn, group, input_node.outputs["種類"], 5, label="白抜き線か", location=(1840, -120))
    selected = _switch_geometry(gn, group, is_beta, fill_switch, fill, label="ベタフラ切替", location=(2060, 160))
    selected = _switch_geometry(gn, group, is_speed, selected, speed, label="流線切替", location=(2280, 160))
    selected = _switch_geometry(gn, group, is_white, selected, white_geometry, label="白抜き線切替", location=(2500, 160))
    gn._link(group, selected, output_node.inputs["Geometry"])
    group[gn.PROP_GROUP_VERSION] = gn._GROUP_VERSION
