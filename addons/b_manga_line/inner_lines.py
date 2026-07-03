"""B-MANGA Line — 内部線（稜線・谷線）のジオメトリノードセットアップ.

Edge Angle ノードでメッシュの折れ目を検出し、
そのエッジに沿った細いチューブ状ジオメトリを生成する。
"""

from __future__ import annotations

import math

import bpy

from . import modifier_stack
from .core import (
    GENERATED_LINE_ATTR,
    GN_MODIFIER_NAME,
    GN_TREE_NAME,
    MATERIAL_NAME,
    VG_INNER_LINE_WIDTH,
)


_GENERATED_LINE_NODE_LABEL = "BML_GeneratedLineMark"
_RADIUS_HALF_NODE_LABEL = "BML_InnerLineRadiusHalf"
_OFFSET_SOCKET_NAME = "オフセット"
_MARKED_ONLY_SOCKET_NAME = "指定済みの辺だけ線にする"
_MIDPOINT_FACTOR_SOCKET_NAME = "中間頂点の線幅調整"
_RESAMPLE_COUNT_SOCKET_NAME = "線の分割数"
_WIDTH_CURVE_25_SOCKET_NAME = "線幅カーブ25%"
_WIDTH_CURVE_50_SOCKET_NAME = "線幅カーブ50%"
_WIDTH_CURVE_75_SOCKET_NAME = "線幅カーブ75%"
_MARKED_SELECTION_SWITCH_LABEL = "BML_MarkedInnerEdgeSelection"
_CURVE_WIDTH_SCALE_LABEL = "BML_InnerCurveWidthScale"
_RESAMPLE_CURVE_LABEL = "BML_InnerCurveResample"
_SHARP_EDGE_ATTR = "sharp_edge"
_CREASE_EDGE_ATTR = "crease_edge"


def _vector_scale_input(node):
    return node.inputs.get("Scale") or node.inputs[min(3, len(node.inputs) - 1)]


def _math(nodes, operation: str, loc, value0=None, value1=None):
    node = nodes.new("ShaderNodeMath")
    node.location = loc
    node.operation = operation
    if value0 is not None:
        node.inputs[0].default_value = value0
    if value1 is not None and len(node.inputs) > 1:
        node.inputs[1].default_value = value1
    return node


def _compare_le(nodes, links, value_output, threshold: float, loc):
    node = nodes.new("FunctionNodeCompare")
    node.location = loc
    node.data_type = "FLOAT"
    node.operation = "LESS_EQUAL"
    node.inputs[1].default_value = threshold
    links.new(value_output, node.inputs[0])
    return node.outputs[0]


def _switch_float(nodes, links, switch_output, false_output, true_output, loc):
    node = nodes.new("GeometryNodeSwitch")
    node.location = loc
    node.input_type = "FLOAT"
    links.new(switch_output, node.inputs["Switch"])
    links.new(false_output, node.inputs["False"])
    links.new(true_output, node.inputs["True"])
    return node.outputs["Output"]


def _value_or_socket(nodes, links, output, fallback: float, loc):
    if output is not None:
        return output
    value = nodes.new("ShaderNodeValue")
    value.location = loc
    value.outputs[0].default_value = fallback
    return value.outputs[0]


def _curve_segment(nodes, links, raw_output, y0_output, y1_output, x0, x1, x_offset, y):
    sub = _math(nodes, "SUBTRACT", (x_offset - 600, y), value1=x0)
    links.new(raw_output, sub.inputs[0])
    div = _math(nodes, "DIVIDE", (x_offset - 420, y), value1=(x1 - x0))
    links.new(sub.outputs[0], div.inputs[0])
    y0_value = _value_or_socket(nodes, links, y0_output, x0, (x_offset - 600, y - 80))
    y1_value = _value_or_socket(nodes, links, y1_output, x1, (x_offset - 600, y - 160))
    span = _math(nodes, "SUBTRACT", (x_offset - 240, y - 80))
    links.new(y1_value, span.inputs[0])
    links.new(y0_value, span.inputs[1])
    scaled = _math(nodes, "MULTIPLY", (x_offset - 60, y))
    links.new(div.outputs[0], scaled.inputs[0])
    links.new(span.outputs[0], scaled.inputs[1])
    add = _math(nodes, "ADD", (x_offset + 120, y))
    links.new(y0_value, add.inputs[0])
    links.new(scaled.outputs[0], add.inputs[1])
    return add.outputs[0]


def _add_width_curve(nodes, links, raw_output, gin, x_offset=0):
    seg0 = _curve_segment(
        nodes, links, raw_output, None, gin.outputs[_WIDTH_CURVE_25_SOCKET_NAME],
        0.00, 0.25, x_offset, -860,
    )
    seg1 = _curve_segment(
        nodes, links, raw_output, gin.outputs[_WIDTH_CURVE_25_SOCKET_NAME],
        gin.outputs[_WIDTH_CURVE_50_SOCKET_NAME], 0.25, 0.50, x_offset, -1040,
    )
    seg2 = _curve_segment(
        nodes, links, raw_output, gin.outputs[_WIDTH_CURVE_50_SOCKET_NAME],
        gin.outputs[_WIDTH_CURVE_75_SOCKET_NAME], 0.50, 0.75, x_offset, -1220,
    )
    seg3 = _curve_segment(
        nodes, links, raw_output, gin.outputs[_WIDTH_CURVE_75_SOCKET_NAME],
        None, 0.75, 1.00, x_offset, -1400,
    )
    le25 = _compare_le(nodes, links, raw_output, 0.25, (x_offset + 280, -840))
    le50 = _compare_le(nodes, links, raw_output, 0.50, (x_offset + 280, -1020))
    le75 = _compare_le(nodes, links, raw_output, 0.75, (x_offset + 280, -1200))
    switch75 = _switch_float(nodes, links, le75, seg2, seg3, (x_offset + 500, -1200))
    switch50 = _switch_float(nodes, links, le50, seg1, switch75, (x_offset + 700, -1020))
    switch25 = _switch_float(nodes, links, le25, seg0, switch50, (x_offset + 900, -840))
    return switch25


def _add_curve_width_scale(nodes, links, gin, x_offset=0):
    spline = nodes.new("GeometryNodeSplineParameter")
    spline.location = (x_offset - 760, 360)

    doubled = _math(nodes, "MULTIPLY", (x_offset - 560, 360), value1=2.0)
    links.new(spline.outputs["Factor"], doubled.inputs[0])
    minus_one = _math(nodes, "SUBTRACT", (x_offset - 380, 360), value1=1.0)
    links.new(doubled.outputs[0], minus_one.inputs[0])
    abs_mid = _math(nodes, "ABSOLUTE", (x_offset - 200, 360))
    links.new(minus_one.outputs[0], abs_mid.inputs[0])
    midpoint = _math(nodes, "SUBTRACT", (x_offset - 20, 360), value0=1.0)
    links.new(abs_mid.outputs[0], midpoint.inputs[1])

    curved = _add_width_curve(nodes, links, midpoint.outputs[0], gin, x_offset)

    one_minus_curve = _math(nodes, "SUBTRACT", (x_offset + 500, 360), value0=1.0)
    links.new(curved, one_minus_curve.inputs[1])
    pos_drop = _math(nodes, "MULTIPLY", (x_offset + 700, 380))
    links.new(one_minus_curve.outputs[0], pos_drop.inputs[0])
    links.new(gin.outputs[_MIDPOINT_FACTOR_SOCKET_NAME], pos_drop.inputs[1])
    pos_scale = _math(nodes, "SUBTRACT", (x_offset + 900, 380), value0=1.0)
    links.new(pos_drop.outputs[0], pos_scale.inputs[1])

    abs_factor = _math(nodes, "ABSOLUTE", (x_offset + 500, 180))
    links.new(gin.outputs[_MIDPOINT_FACTOR_SOCKET_NAME], abs_factor.inputs[0])
    neg_drop = _math(nodes, "MULTIPLY", (x_offset + 700, 180))
    links.new(curved, neg_drop.inputs[0])
    links.new(abs_factor.outputs[0], neg_drop.inputs[1])
    neg_scale = _math(nodes, "SUBTRACT", (x_offset + 900, 180), value0=1.0)
    links.new(neg_drop.outputs[0], neg_scale.inputs[1])

    positive = nodes.new("FunctionNodeCompare")
    positive.location = (x_offset + 700, 560)
    positive.data_type = "FLOAT"
    positive.operation = "GREATER_EQUAL"
    positive.inputs[1].default_value = 0.0
    links.new(gin.outputs[_MIDPOINT_FACTOR_SOCKET_NAME], positive.inputs[0])

    scale_switch = nodes.new("GeometryNodeSwitch")
    scale_switch.label = _CURVE_WIDTH_SCALE_LABEL
    scale_switch.location = (x_offset + 1100, 300)
    scale_switch.input_type = "FLOAT"
    links.new(positive.outputs[0], scale_switch.inputs["Switch"])
    links.new(neg_scale.outputs[0], scale_switch.inputs["False"])
    links.new(pos_scale.outputs[0], scale_switch.inputs["True"])

    clamped_min = _math(nodes, "MAXIMUM", (x_offset + 1300, 300), value1=0.0)
    links.new(scale_switch.outputs["Output"], clamped_min.inputs[0])
    clamped_max = _math(nodes, "MINIMUM", (x_offset + 1480, 300), value1=1.0)
    links.new(clamped_min.outputs[0], clamped_max.inputs[0])
    return clamped_max.outputs[0]


# ------------------------------------------------------------------
# ノードツリー構築
# ------------------------------------------------------------------

def _create_node_tree() -> bpy.types.NodeTree:
    """内部線用ジオメトリノードツリーを新規作成."""
    tree = bpy.data.node_groups.new(name=GN_TREE_NAME, type="GeometryNodeTree")

    # --- インターフェース定義 ---
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    angle_sock = tree.interface.new_socket(
        name="検出角度", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    angle_sock.default_value = math.radians(60)
    angle_sock.min_value = math.radians(1)
    angle_sock.max_value = math.radians(180)
    if hasattr(angle_sock, "subtype"):
        angle_sock.subtype = "ANGLE"

    radius_sock = tree.interface.new_socket(
        name="線の太さ", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    radius_sock.default_value = 0.0005
    radius_sock.min_value = 0.0001
    radius_sock.max_value = 1.0
    offset_sock = tree.interface.new_socket(
        name=_OFFSET_SOCKET_NAME, in_out="INPUT", socket_type="NodeSocketFloat"
    )
    offset_sock.default_value = 1.0
    offset_sock.min_value = -1.0
    offset_sock.max_value = 1.0

    tree.interface.new_socket(
        name="マテリアル", in_out="INPUT", socket_type="NodeSocketMaterial"
    )
    line_material_sock = tree.interface.new_socket(
        name="ライン素材番号", in_out="INPUT", socket_type="NodeSocketInt"
    )
    line_material_sock.default_value = 999
    line_material_sock.min_value = 0
    marked_only_sock = tree.interface.new_socket(
        name=_MARKED_ONLY_SOCKET_NAME,
        in_out="INPUT",
        socket_type="NodeSocketBool",
    )
    marked_only_sock.default_value = False
    midpoint_sock = tree.interface.new_socket(
        name=_MIDPOINT_FACTOR_SOCKET_NAME,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    midpoint_sock.default_value = 0.0
    midpoint_sock.min_value = -1.0
    midpoint_sock.max_value = 1.0
    resample_count_sock = tree.interface.new_socket(
        name=_RESAMPLE_COUNT_SOCKET_NAME,
        in_out="INPUT",
        socket_type="NodeSocketInt",
    )
    resample_count_sock.default_value = 17
    resample_count_sock.min_value = 2
    resample_count_sock.max_value = 256
    for name, default in (
        (_WIDTH_CURVE_25_SOCKET_NAME, 0.25),
        (_WIDTH_CURVE_50_SOCKET_NAME, 0.50),
        (_WIDTH_CURVE_75_SOCKET_NAME, 0.75),
    ):
        sock = tree.interface.new_socket(
            name=name,
            in_out="INPUT",
            socket_type="NodeSocketFloat",
        )
        sock.default_value = default
        sock.min_value = 0.0
        sock.max_value = 1.0

    nodes = tree.nodes
    links = tree.links

    # --- ノード配置 ---
    gin = nodes.new("NodeGroupInput")
    gin.location = (-800, 0)

    gout = nodes.new("NodeGroupOutput")
    gout.location = (800, 0)

    # Solidify 後に実行しても、検出元は元メッシュ面だけに限定する。
    mat_idx = nodes.new("GeometryNodeInputMaterialIndex")
    mat_idx.location = (-760, -420)

    is_line_material = nodes.new("FunctionNodeCompare")
    is_line_material.location = (-600, -420)
    is_line_material.data_type = "INT"
    is_line_material.operation = "GREATER_EQUAL"
    links.new(mat_idx.outputs[0], is_line_material.inputs[2])
    links.new(gin.outputs["ライン素材番号"], is_line_material.inputs[3])

    generated_attr = nodes.new("GeometryNodeInputNamedAttribute")
    generated_attr.location = (-440, -600)
    generated_attr.data_type = "BOOLEAN"
    generated_attr.inputs["Name"].default_value = GENERATED_LINE_ATTR

    generated_marked = nodes.new("FunctionNodeBooleanMath")
    generated_marked.location = (-260, -620)
    generated_marked.operation = "AND"
    links.new(generated_attr.outputs["Exists"], generated_marked.inputs[0])
    links.new(generated_attr.outputs["Attribute"], generated_marked.inputs[1])

    delete_selection = nodes.new("FunctionNodeBooleanMath")
    delete_selection.location = (-260, -500)
    delete_selection.operation = "OR"
    links.new(is_line_material.outputs[0], delete_selection.inputs[0])
    links.new(generated_marked.outputs[0], delete_selection.inputs[1])

    del_shell = nodes.new("GeometryNodeDeleteGeometry")
    del_shell.location = (-280, -420)
    del_shell.domain = "FACE"
    links.new(gin.outputs[0], del_shell.inputs["Geometry"])
    links.new(delete_selection.outputs[0], del_shell.inputs["Selection"])

    # Edge Angle: エッジの二面角を取得
    edge_angle = nodes.new("GeometryNodeInputMeshEdgeAngle")
    edge_angle.location = (-600, -200)

    # Compare: 角度 > 閾値 → 折れ目エッジを選択
    compare = nodes.new("FunctionNodeCompare")
    compare.location = (-400, -200)
    compare.data_type = "FLOAT"
    compare.operation = "GREATER_THAN"
    links.new(edge_angle.outputs[0], compare.inputs["A"])  # Unsigned Angle
    links.new(gin.outputs[1], compare.inputs["B"])  # 検出角度

    sharp_attr = nodes.new("GeometryNodeInputNamedAttribute")
    sharp_attr.location = (-600, 40)
    sharp_attr.data_type = "BOOLEAN"
    sharp_attr.inputs["Name"].default_value = _SHARP_EDGE_ATTR

    sharp_marked = nodes.new("FunctionNodeBooleanMath")
    sharp_marked.location = (-400, 40)
    sharp_marked.operation = "AND"
    links.new(sharp_attr.outputs["Exists"], sharp_marked.inputs[0])
    links.new(sharp_attr.outputs["Attribute"], sharp_marked.inputs[1])

    crease_attr = nodes.new("GeometryNodeInputNamedAttribute")
    crease_attr.location = (-600, -40)
    crease_attr.data_type = "FLOAT"
    crease_attr.inputs["Name"].default_value = _CREASE_EDGE_ATTR

    crease_positive = nodes.new("FunctionNodeCompare")
    crease_positive.location = (-400, -60)
    crease_positive.data_type = "FLOAT"
    crease_positive.operation = "GREATER_THAN"
    crease_positive.inputs["B"].default_value = 0.0
    links.new(crease_attr.outputs["Attribute"], crease_positive.inputs["A"])

    crease_marked = nodes.new("FunctionNodeBooleanMath")
    crease_marked.location = (-220, -40)
    crease_marked.operation = "AND"
    links.new(crease_attr.outputs["Exists"], crease_marked.inputs[0])
    links.new(crease_positive.outputs[0], crease_marked.inputs[1])

    marked_selection = nodes.new("FunctionNodeBooleanMath")
    marked_selection.location = (-40, 0)
    marked_selection.operation = "OR"
    links.new(sharp_marked.outputs[0], marked_selection.inputs[0])
    links.new(crease_marked.outputs[0], marked_selection.inputs[1])

    selection_switch = nodes.new("GeometryNodeSwitch")
    selection_switch.label = _MARKED_SELECTION_SWITCH_LABEL
    selection_switch.location = (-20, -200)
    selection_switch.input_type = "BOOLEAN"
    links.new(gin.outputs[_MARKED_ONLY_SOCKET_NAME], selection_switch.inputs["Switch"])
    links.new(compare.outputs[0], selection_switch.inputs["False"])
    links.new(marked_selection.outputs[0], selection_switch.inputs["True"])

    offset_amount = nodes.new("ShaderNodeMath")
    offset_amount.location = (-600, -320)
    offset_amount.operation = "MULTIPLY"
    links.new(gin.outputs["線の太さ"], offset_amount.inputs[0])
    links.new(gin.outputs[_OFFSET_SOCKET_NAME], offset_amount.inputs[1])

    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-600, -440)

    offset_vector = nodes.new("ShaderNodeVectorMath")
    offset_vector.location = (-400, -360)
    offset_vector.operation = "SCALE"
    links.new(normal.outputs[0], offset_vector.inputs[0])
    links.new(offset_amount.outputs[0], _vector_scale_input(offset_vector))

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (-380, -260)
    links.new(del_shell.outputs["Geometry"], set_position.inputs["Geometry"])
    links.new(offset_vector.outputs[0], set_position.inputs["Offset"])

    # Mesh to Curve: 選択エッジをカーブに変換
    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (-200, -200)
    links.new(set_position.outputs["Geometry"], m2c.inputs[0])  # 元メッシュのみ
    links.new(selection_switch.outputs["Output"], m2c.inputs[1])  # Selection

    resample = nodes.new("GeometryNodeResampleCurve")
    resample.label = _RESAMPLE_CURVE_LABEL
    resample.location = (-20, -200)
    if hasattr(resample, "mode"):
        resample.mode = "COUNT"
    count_input = resample.inputs.get("Count")
    if count_input is not None:
        count_input.default_value = 17
        links.new(gin.outputs[_RESAMPLE_COUNT_SOCKET_NAME], count_input)
    links.new(m2c.outputs[0], resample.inputs["Curve"])

    # 内部線専用の線幅値を反映する。
    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.location = (-220, 120)
    width_attr.data_type = "FLOAT"
    width_attr.inputs["Name"].default_value = VG_INNER_LINE_WIDTH

    width_switch = nodes.new("GeometryNodeSwitch")
    width_switch.location = (-20, 120)
    width_switch.input_type = "FLOAT"
    width_switch.inputs["False"].default_value = 1.0
    links.new(width_attr.outputs["Exists"], width_switch.inputs["Switch"])
    links.new(width_attr.outputs["Attribute"], width_switch.inputs["True"])

    width_min = nodes.new("ShaderNodeMath")
    width_min.location = (160, 120)
    width_min.operation = "MAXIMUM"
    width_min.inputs[1].default_value = 0.0
    links.new(width_switch.outputs["Output"], width_min.inputs[0])

    width_max = nodes.new("ShaderNodeMath")
    width_max.location = (340, 120)
    width_max.operation = "MINIMUM"
    width_max.inputs[1].default_value = 1.0
    links.new(width_min.outputs[0], width_max.inputs[0])

    curve_scale = _add_curve_width_scale(nodes, links, gin, x_offset=520)
    combined_scale = nodes.new("ShaderNodeMath")
    combined_scale.location = (620, 120)
    combined_scale.operation = "MULTIPLY"
    links.new(width_max.outputs[0], combined_scale.inputs[0])
    links.new(curve_scale, combined_scale.inputs[1])

    # Curve Circle: チューブ断面
    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.location = (-200, -400)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = 4
    radius_half = nodes.new("ShaderNodeMath")
    radius_half.label = _RADIUS_HALF_NODE_LABEL
    radius_half.location = (-400, -360)
    radius_half.operation = "MULTIPLY"
    radius_half.inputs[1].default_value = 0.5
    links.new(gin.outputs["線の太さ"], radius_half.inputs[0])
    links.new(radius_half.outputs[0], circle.inputs["Radius"])  # 線の太さ → 半径

    # Curve to Mesh: カーブをチューブメッシュに変換
    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (140, -200)
    links.new(resample.outputs["Curve"], c2m.inputs[0])  # Curve
    links.new(circle.outputs[0], c2m.inputs[1])  # Profile Curve
    if "Scale" in c2m.inputs:
        links.new(combined_scale.outputs[0], c2m.inputs["Scale"])  # 頂点/線上位置ごとの太さ倍率
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True

    mark_generated = nodes.new("GeometryNodeStoreNamedAttribute")
    mark_generated.label = _GENERATED_LINE_NODE_LABEL
    mark_generated.location = (120, -360)
    mark_generated.data_type = "BOOLEAN"
    mark_generated.domain = "FACE"
    mark_generated.inputs["Name"].default_value = GENERATED_LINE_ATTR
    mark_generated.inputs["Value"].default_value = True
    links.new(c2m.outputs[0], mark_generated.inputs["Geometry"])

    # Set Material: マテリアル入力ソケットから割り当て
    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (300, -200)
    links.new(mark_generated.outputs["Geometry"], setmat.inputs[0])
    links.new(gin.outputs["マテリアル"], setmat.inputs["Material"])

    # Join Geometry: 元メッシュ + 内部線ジオメトリ
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (500, 0)
    links.new(gin.outputs[0], join.inputs[0])  # 元ジオメトリ
    links.new(setmat.outputs[0], join.inputs[0])  # 内部線ジオメトリ

    links.new(join.outputs[0], gout.inputs[0])

    return tree


def _get_or_create_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(GN_TREE_NAME)
    if tree is not None:
        if _find_socket_id(tree, "マテリアル") is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, "ライン素材番号") is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, _MARKED_ONLY_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, _OFFSET_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, _MIDPOINT_FACTOR_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, _RESAMPLE_COUNT_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, _WIDTH_CURVE_25_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, _WIDTH_CURVE_50_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if _find_socket_id(tree, _WIDTH_CURVE_75_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not any(n.bl_idname == "GeometryNodeDeleteGeometry" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not any(n.bl_idname == "GeometryNodeInputNamedAttribute" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not _uses_named_attribute(tree, VG_INNER_LINE_WIDTH):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not _uses_named_attribute(tree, _SHARP_EDGE_ATTR):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not _uses_named_attribute(tree, _CREASE_EDGE_ATTR):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not any(getattr(n, "label", "") == _GENERATED_LINE_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not any(getattr(n, "label", "") == _RADIUS_HALF_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not any(getattr(n, "label", "") == _MARKED_SELECTION_SWITCH_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not any(getattr(n, "label", "") == _CURVE_WIDTH_SCALE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if not any(getattr(n, "label", "") == _RESAMPLE_CURVE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        if any(n.bl_idname == "GeometryNodeSetCurveRadius" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        radius_socket = _find_interface_socket(tree, "線の太さ")
        if radius_socket is not None and getattr(radius_socket, "max_value", 0.0) < 1.0:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        return tree
    return _create_node_tree()


def _find_interface_socket(tree: bpy.types.NodeTree, name: str):
    for item in tree.interface.items_tree:
        if getattr(item, "name", None) == name and getattr(item, "in_out", None) == "INPUT":
            return item
    return None


def _uses_named_attribute(tree: bpy.types.NodeTree, attr_name: str) -> bool:
    for node in tree.nodes:
        if node.bl_idname != "GeometryNodeInputNamedAttribute":
            continue
        name_input = node.inputs.get("Name")
        if name_input is not None and name_input.default_value == attr_name:
            return True
    return False


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    """ツリーインターフェースからソケット識別子を検索."""
    for item in tree.interface.items_tree:
        if getattr(item, "name", None) == name and getattr(item, "in_out", None) == "INPUT":
            return item.identifier
    return None


def _ensure_material_slot(
    obj: bpy.types.Object,
    material: bpy.types.Material | None,
) -> int:
    """生成した線素材を後続処理でも素材番号として扱えるようにする."""
    if material is not None and not any(slot_mat == material for slot_mat in obj.data.materials):
        obj.data.materials.append(material)
    for index, slot_mat in enumerate(obj.data.materials):
        if slot_mat and slot_mat.name.startswith(MATERIAL_NAME):
            return index
    return 999


# ------------------------------------------------------------------
# 適用 / 削除 / 更新
# ------------------------------------------------------------------

def apply_inner_lines(
    obj: bpy.types.Object,
    angle: float = 0.5236,
    thickness: float = 0.0005,
    offset: float = 1.0,
    material: bpy.types.Material | None = None,
    use_marked_edges: bool = False,
    midpoint_factor: float = 0.0,
    resample_count: int | None = None,
    width_curve_25: float = 0.25,
    width_curve_50: float = 0.50,
    width_curve_75: float = 0.75,
    enable: bool = True,
) -> bool:
    """内部線 GN モディファイアを適用. 成功時 True."""
    if obj.type != "MESH":
        return False

    tree = _get_or_create_tree()

    # 既存モディファイアを更新 or 新規作成
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=GN_MODIFIER_NAME, type="NODES")
    if not enable:
        mod.show_viewport = False
        mod.show_render = False
    mod.node_group = tree
    mod.show_viewport = enable
    mod.show_render = enable

    # パラメータ設定
    sid_angle = _find_socket_id(tree, "検出角度")
    sid_thickness = _find_socket_id(tree, "線の太さ")
    sid_offset = _find_socket_id(tree, _OFFSET_SOCKET_NAME)
    sid_marked_only = _find_socket_id(tree, _MARKED_ONLY_SOCKET_NAME)
    sid_midpoint_factor = _find_socket_id(tree, _MIDPOINT_FACTOR_SOCKET_NAME)
    sid_resample_count = _find_socket_id(tree, _RESAMPLE_COUNT_SOCKET_NAME)
    sid_curve_25 = _find_socket_id(tree, _WIDTH_CURVE_25_SOCKET_NAME)
    sid_curve_50 = _find_socket_id(tree, _WIDTH_CURVE_50_SOCKET_NAME)
    sid_curve_75 = _find_socket_id(tree, _WIDTH_CURVE_75_SOCKET_NAME)
    if sid_angle is not None:
        mod[sid_angle] = angle
    if sid_thickness is not None:
        mod[sid_thickness] = thickness
    if sid_offset is not None:
        mod[sid_offset] = offset
    if sid_marked_only is not None:
        mod[sid_marked_only] = bool(use_marked_edges)
    if sid_midpoint_factor is not None:
        mod[sid_midpoint_factor] = float(midpoint_factor)
    if sid_resample_count is not None:
        if resample_count is None:
            from . import subdivision_lod

            resample_count = subdivision_lod.line_resample_count(obj)
        mod[sid_resample_count] = max(2, int(resample_count))
    if sid_curve_25 is not None:
        mod[sid_curve_25] = float(width_curve_25)
    if sid_curve_50 is not None:
        mod[sid_curve_50] = float(width_curve_50)
    if sid_curve_75 is not None:
        mod[sid_curve_75] = float(width_curve_75)

    # マテリアル
    line_material_index = 999
    if material is not None:
        line_material_index = _ensure_material_slot(obj, material)
        sid_mat = _find_socket_id(tree, "マテリアル")
        if sid_mat is not None:
            mod[sid_mat] = material
    else:
        line_material_index = _ensure_material_slot(obj, None)
    sid_line_material = _find_socket_id(tree, "ライン素材番号")
    if sid_line_material is not None:
        mod[sid_line_material] = line_material_index

    # 頂点グループ: 元メッシュ頂点 = weight 1.0
    vg = obj.vertex_groups.get(VG_INNER_LINE_WIDTH)
    if vg is None:
        vg = obj.vertex_groups.new(name=VG_INNER_LINE_WIDTH)
        vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")

    # 既存のメッシュ調整を先に通し、ライン生成は最後にまとめる。
    modifier_stack.reorder_line_modifiers(obj)

    return True


def remove_inner_lines(obj: bpy.types.Object) -> bool:
    """内部線 GN モディファイアを削除."""
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        return False
    obj.modifiers.remove(mod)
    return True


def disable_inner_lines(obj: bpy.types.Object) -> bool:
    """内部線を削除せず無効化する."""
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        return False
    changed = bool(mod.show_viewport or mod.show_render)
    if not changed:
        return False
    mod.show_viewport = False
    mod.show_render = False
    return changed


def enable_inner_lines(obj: bpy.types.Object) -> bool:
    """内部線を表示・レンダー有効に戻す."""
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        return False
    changed = not bool(mod.show_viewport and mod.show_render)
    if not changed:
        return False
    mod.show_viewport = True
    mod.show_render = True
    return changed


def update_parameters(
    obj: bpy.types.Object,
    angle: float | None = None,
    thickness: float | None = None,
    offset: float | None = None,
    use_marked_edges: bool | None = None,
    midpoint_factor: float | None = None,
    resample_count: int | None = None,
    width_curve_25: float | None = None,
    width_curve_50: float | None = None,
    width_curve_75: float | None = None,
    material: bpy.types.Material | None = None,
) -> bool:
    """既存モディファイアのパラメータを更新."""
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None or mod.node_group is None:
        return False
    tree = mod.node_group
    if angle is not None:
        sid = _find_socket_id(tree, "検出角度")
        if sid is not None:
            mod[sid] = angle
    if thickness is not None:
        sid = _find_socket_id(tree, "線の太さ")
        if sid is not None:
            mod[sid] = thickness
    if offset is not None:
        sid = _find_socket_id(tree, _OFFSET_SOCKET_NAME)
        if sid is not None:
            mod[sid] = offset
    if use_marked_edges is not None:
        sid = _find_socket_id(tree, _MARKED_ONLY_SOCKET_NAME)
        if sid is not None:
            mod[sid] = bool(use_marked_edges)
    if midpoint_factor is not None:
        sid = _find_socket_id(tree, _MIDPOINT_FACTOR_SOCKET_NAME)
        if sid is not None:
            mod[sid] = float(midpoint_factor)
    if resample_count is not None:
        sid = _find_socket_id(tree, _RESAMPLE_COUNT_SOCKET_NAME)
        if sid is not None:
            mod[sid] = max(2, int(resample_count))
    if width_curve_25 is not None:
        sid = _find_socket_id(tree, _WIDTH_CURVE_25_SOCKET_NAME)
        if sid is not None:
            mod[sid] = float(width_curve_25)
    if width_curve_50 is not None:
        sid = _find_socket_id(tree, _WIDTH_CURVE_50_SOCKET_NAME)
        if sid is not None:
            mod[sid] = float(width_curve_50)
    if width_curve_75 is not None:
        sid = _find_socket_id(tree, _WIDTH_CURVE_75_SOCKET_NAME)
        if sid is not None:
            mod[sid] = float(width_curve_75)
    if material is not None:
        line_material_index = _ensure_material_slot(obj, material)
        sid_mat = _find_socket_id(tree, "マテリアル")
        if sid_mat is not None:
            mod[sid_mat] = material
        sid_line_material = _find_socket_id(tree, "ライン素材番号")
        if sid_line_material is not None:
            mod[sid_line_material] = line_material_index
    return True
