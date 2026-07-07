"""B-MANGA Line — 内部線（稜線・谷線）のジオメトリノードセットアップ.

Edge Angle ノードでメッシュの折れ目を検出し、
そのエッジに沿った細いチューブ状ジオメトリを生成する。
"""

from __future__ import annotations

import math

import bpy

from . import inner_line_chains, modifier_stack
from .core import (
    FREESTYLE_EDGE_ATTR,
    GENERATED_LINE_ATTR,
    GN_MODIFIER_NAME,
    GN_TREE_NAME,
    MATERIAL_NAME,
    PROP_LINES_HIDDEN,
    VG_INNER_LINE_WIDTH,
    inner_width_split_angle,
)


_GENERATED_LINE_NODE_LABEL = "BML_GeneratedLineMark"
_RADIUS_HALF_NODE_LABEL = "BML_InnerLineRadiusHalf"
_PROFILE_NODE_LABEL = "BML_InnerLineProfileV2"
_SMOOTH_NODE_LABEL = "BML_InnerLineSmoothV2"
_OFFSET_SOCKET_NAME = "オフセット"
_MARKED_ONLY_SOCKET_NAME = "Freestyleマーク辺だけ線にする"
_MIDPOINT_FACTOR_SOCKET_NAME = "中間頂点の線幅調整"
_MIDPOINT_JITTER_SOCKET_NAME = "中間頂点の乱れ (%)"
_RESAMPLE_COUNT_SOCKET_NAME = "線の分割数"
_WIDTH_CURVE_25_SOCKET_NAME = "線幅カーブ25%"
_WIDTH_CURVE_50_SOCKET_NAME = "線幅カーブ50%"
_WIDTH_CURVE_75_SOCKET_NAME = "線幅カーブ75%"
_MARKED_SELECTION_SWITCH_LABEL = "BML_MarkedInnerEdgeSelection"
_CURVE_WIDTH_SCALE_LABEL = "BML_InnerCurveWidthScale"
_SUBDIVIDE_CURVE_LABEL = "BML_InnerCurveSubdivide"
_SELECTED_EDGE_MESH_LABEL = "BML_InnerSelectedEdgeMesh"
_CHAIN_INSTANCE_SPLIT_LABEL = "BML_InnerChainInstanceSplit"
_EDGE_ANGLE_COMPARE_LABEL = "BML_InnerEdgeAngleCompareGE"
_CHAIN_SELECTION_COMPARE_LABEL = "BML_InnerChainSelectionGT"
_CHAIN_ANGLE_FILTER_LABEL = "BML_InnerChainAngleFilter"
_AUTO_EDGE_ALLOWED_LABEL = "BML_InnerAutoEdgeAllowed"
_AUTO_ANGLE_FILTER_LABEL = "BML_InnerAutoAngleFilter"
_CURVE_JITTER_CENTER_LABEL = "BML_InnerCurveJitterCenter"
_CURVE_JITTER_CHAIN_ID_LABEL = "BML_InnerCurveJitterChainID"
_SAFE_CURVE_SCALE_LABEL = "BML_InnerCurveSafeScale"
_MIN_CURVE_TO_MESH_SCALE = 0.04
INNER_TUBE_PROFILE_RESOLUTION = 12
_CHAIN_ID_ATTR = inner_line_chains.CHAIN_ID_ATTR
_FREESTYLE_EDGE_ATTR = FREESTYLE_EDGE_ATTR
_EDGE_ANGLE_EPSILON = 1.0e-7
_CHAIN_ANGLE_PROP = "bml_inner_chain_angle"
_CHAIN_MARKED_PROP = "bml_inner_chain_marked_edges"
_CHAIN_MIDPOINT_ANGLE_PROP = "bml_inner_chain_midpoint_angle"
_CHAIN_INPUT_EPSILON = 1.0e-6


def _node_angle_threshold(angle: float) -> float:
    return max(0.0, float(angle) - _EDGE_ANGLE_EPSILON)


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


def _add_curve_width_scale(nodes, links, gin, x_offset=0, random_id_output=None):
    spline = nodes.new("GeometryNodeSplineParameter")
    spline.location = (x_offset - 760, 360)

    midpoint_raw = _add_jittered_midpoint_factor(
        nodes,
        links,
        gin,
        spline,
        x_offset,
        random_id_output=random_id_output,
    )
    curved = _add_width_curve(nodes, links, midpoint_raw, gin, x_offset)

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


def _add_jittered_midpoint_factor(
    nodes,
    links,
    gin,
    spline,
    x_offset=0,
    *,
    random_id_output=None,
):
    jitter_div = _math(nodes, "DIVIDE", (x_offset - 760, 520), value1=100.0)
    links.new(gin.outputs[_MIDPOINT_JITTER_SOCKET_NAME], jitter_div.inputs[0])
    jitter_clamp_min = _math(nodes, "MAXIMUM", (x_offset - 580, 520), value1=0.0)
    links.new(jitter_div.outputs[0], jitter_clamp_min.inputs[0])
    jitter_clamp_max = _math(nodes, "MINIMUM", (x_offset - 400, 520), value1=0.5)
    links.new(jitter_clamp_min.outputs[0], jitter_clamp_max.inputs[0])

    random = nodes.new("FunctionNodeRandomValue")
    random.location = (x_offset - 760, 680)
    random.data_type = "FLOAT"
    random.inputs[2].default_value = -1.0
    random.inputs[3].default_value = 1.0
    if random_id_output is None:
        random_id_output = spline.outputs["Index"]
    links.new(random_id_output, random.inputs["ID"])

    offset = _math(nodes, "MULTIPLY", (x_offset - 220, 580))
    links.new(random.outputs[1], offset.inputs[0])
    links.new(jitter_clamp_max.outputs[0], offset.inputs[1])

    center = _math(nodes, "ADD", (x_offset - 20, 580), value0=0.5)
    center.label = _CURVE_JITTER_CENTER_LABEL
    links.new(offset.outputs[0], center.inputs[1])
    center_min = _math(nodes, "MAXIMUM", (x_offset + 160, 580), value1=0.001)
    links.new(center.outputs[0], center_min.inputs[0])
    center_max = _math(nodes, "MINIMUM", (x_offset + 340, 580), value1=0.999)
    links.new(center_min.outputs[0], center_max.inputs[0])

    left_ratio = _math(nodes, "DIVIDE", (x_offset - 220, 360))
    links.new(spline.outputs["Factor"], left_ratio.inputs[0])
    links.new(center_max.outputs[0], left_ratio.inputs[1])

    one_minus_factor = _math(nodes, "SUBTRACT", (x_offset - 220, 240), value0=1.0)
    links.new(spline.outputs["Factor"], one_minus_factor.inputs[1])
    one_minus_center = _math(nodes, "SUBTRACT", (x_offset - 20, 240), value0=1.0)
    links.new(center_max.outputs[0], one_minus_center.inputs[1])
    right_ratio = _math(nodes, "DIVIDE", (x_offset + 160, 240))
    links.new(one_minus_factor.outputs[0], right_ratio.inputs[0])
    links.new(one_minus_center.outputs[0], right_ratio.inputs[1])

    left_side = nodes.new("FunctionNodeCompare")
    left_side.location = (x_offset + 160, 420)
    left_side.data_type = "FLOAT"
    left_side.operation = "LESS_EQUAL"
    links.new(spline.outputs["Factor"], left_side.inputs[0])
    links.new(center_max.outputs[0], left_side.inputs[1])

    raw_switch = _switch_float(
        nodes, links, left_side.outputs[0], right_ratio.outputs[0], left_ratio.outputs[0],
        (x_offset + 520, 360),
    )
    raw_min = _math(nodes, "MAXIMUM", (x_offset + 700, 360), value1=0.0)
    links.new(raw_switch, raw_min.inputs[0])
    raw_max = _math(nodes, "MINIMUM", (x_offset + 880, 360), value1=1.0)
    links.new(raw_min.outputs[0], raw_max.inputs[0])
    return raw_max.outputs[0]


# ------------------------------------------------------------------
# ノードツリー構築
# ------------------------------------------------------------------

def _create_node_tree(
    tree_name: str = GN_TREE_NAME,
    width_attr_name: str = VG_INNER_LINE_WIDTH,
    chain_id_attr_name: str = _CHAIN_ID_ATTR,
    marked_attr_name: str = _FREESTYLE_EDGE_ATTR,
) -> bpy.types.NodeTree:
    """内部線用ジオメトリノードツリーを新規作成."""
    tree = bpy.data.node_groups.new(name=tree_name, type="GeometryNodeTree")

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
    offset_sock.default_value = 0.0
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
    jitter_sock = tree.interface.new_socket(
        name=_MIDPOINT_JITTER_SOCKET_NAME,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    jitter_sock.default_value = 0.0
    jitter_sock.min_value = 0.0
    jitter_sock.max_value = 50.0
    resample_count_sock = tree.interface.new_socket(
        name=_RESAMPLE_COUNT_SOCKET_NAME,
        in_out="INPUT",
        socket_type="NodeSocketInt",
    )
    resample_count_sock.default_value = 4
    resample_count_sock.min_value = 1
    resample_count_sock.max_value = 32
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

    # UIの検出角度は「その角度以上」を対象にする。
    # 六角柱の縦角は60度ちょうどなので、60度指定で含まれる必要がある。
    compare = nodes.new("FunctionNodeCompare")
    compare.label = _EDGE_ANGLE_COMPARE_LABEL
    compare.location = (-400, -200)
    compare.data_type = "FLOAT"
    compare.operation = "GREATER_EQUAL"
    links.new(edge_angle.outputs[0], compare.inputs["A"])  # Unsigned Angle
    links.new(gin.outputs[1], compare.inputs["B"])  # 検出角度

    marked_attr = nodes.new("GeometryNodeInputNamedAttribute")
    marked_attr.location = (-600, 40)
    marked_attr.data_type = "BOOLEAN"
    marked_attr.inputs["Name"].default_value = marked_attr_name

    marked_selection = nodes.new("FunctionNodeBooleanMath")
    marked_selection.location = (-400, 40)
    marked_selection.operation = "AND"
    links.new(marked_attr.outputs["Exists"], marked_selection.inputs[0])
    links.new(marked_attr.outputs["Attribute"], marked_selection.inputs[1])

    chain_selection_attr = nodes.new("GeometryNodeInputNamedAttribute")
    chain_selection_attr.location = (-600, -600)
    chain_selection_attr.data_type = "INT"
    chain_selection_attr.inputs["Name"].default_value = chain_id_attr_name

    chain_selected = nodes.new("FunctionNodeCompare")
    chain_selected.label = _CHAIN_SELECTION_COMPARE_LABEL
    chain_selected.location = (-400, -600)
    chain_selected.data_type = "INT"
    chain_selected.operation = "GREATER_THAN"
    links.new(chain_selection_attr.outputs["Attribute"], chain_selected.inputs[2])
    chain_selected.inputs[3].default_value = 0

    chain_angle_filtered = nodes.new("FunctionNodeBooleanMath")
    chain_angle_filtered.label = _CHAIN_ANGLE_FILTER_LABEL
    chain_angle_filtered.location = (-200, -680)
    chain_angle_filtered.operation = "AND"
    links.new(chain_selected.outputs[0], chain_angle_filtered.inputs[0])
    links.new(compare.outputs[0], chain_angle_filtered.inputs[1])

    auto_edge_allowed = nodes.new("FunctionNodeBooleanMath")
    auto_edge_allowed.label = _AUTO_EDGE_ALLOWED_LABEL
    auto_edge_allowed.location = (-40, -560)
    auto_edge_allowed.operation = "OR"
    links.new(chain_selected.outputs[0], auto_edge_allowed.inputs[0])
    links.new(marked_selection.outputs[0], auto_edge_allowed.inputs[1])

    auto_angle_filtered = nodes.new("FunctionNodeBooleanMath")
    auto_angle_filtered.label = _AUTO_ANGLE_FILTER_LABEL
    auto_angle_filtered.location = (-20, -420)
    auto_angle_filtered.operation = "AND"
    links.new(auto_edge_allowed.outputs[0], auto_angle_filtered.inputs[0])
    links.new(compare.outputs[0], auto_angle_filtered.inputs[1])

    selection_switch = nodes.new("GeometryNodeSwitch")
    selection_switch.label = _MARKED_SELECTION_SWITCH_LABEL
    selection_switch.location = (-20, -200)
    selection_switch.input_type = "BOOLEAN"
    links.new(gin.outputs[_MARKED_ONLY_SOCKET_NAME], selection_switch.inputs["Switch"])
    # 評価後メッシュではチェーンID属性が細分化辺へ伝播しない場合がある。
    # 元チェーンまたは形状保持用の印を持つ辺だけを角度検出し、細分面グリッドを拾わない。
    links.new(auto_angle_filtered.outputs[0], selection_switch.inputs["False"])
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

    # 選択された内部線だけの辺グラフを作り、3方向以上へ分岐する点を
    # カーブの端点として扱う。これにより中間頂点の線幅調整が
    # T字状の交差点をまたいで続かないようにする。
    not_selected = nodes.new("FunctionNodeBooleanMath")
    not_selected.location = (-200, -520)
    not_selected.operation = "NOT"
    links.new(selection_switch.outputs["Output"], not_selected.inputs[0])

    selected_edges = nodes.new("GeometryNodeDeleteGeometry")
    selected_edges.label = _SELECTED_EDGE_MESH_LABEL
    selected_edges.location = (-180, -360)
    selected_edges.domain = "EDGE"
    links.new(set_position.outputs["Geometry"], selected_edges.inputs["Geometry"])
    links.new(not_selected.outputs[0], selected_edges.inputs["Selection"])

    chain_id = nodes.new("GeometryNodeInputNamedAttribute")
    chain_id.location = (-20, -560)
    chain_id.data_type = "INT"
    chain_id.inputs["Name"].default_value = chain_id_attr_name

    split_chains = nodes.new("GeometryNodeSplitToInstances")
    split_chains.label = _CHAIN_INSTANCE_SPLIT_LABEL
    split_chains.location = (40, -260)
    split_chains.domain = "EDGE"
    split_chains.inputs["Selection"].default_value = True
    links.new(selected_edges.outputs["Geometry"], split_chains.inputs["Geometry"])
    links.new(chain_id.outputs["Attribute"], split_chains.inputs["Group ID"])

    realize_chains = nodes.new("GeometryNodeRealizeInstances")
    realize_chains.location = (220, -260)
    links.new(split_chains.outputs["Instances"], realize_chains.inputs["Geometry"])

    # Mesh to Curve: 選択エッジだけに整理済みのメッシュをカーブに変換
    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (400, -200)
    links.new(realize_chains.outputs["Geometry"], m2c.inputs[0])
    m2c.inputs["Selection"].default_value = True

    subdivide = nodes.new("GeometryNodeSubdivideCurve")
    subdivide.label = _SUBDIVIDE_CURVE_LABEL
    subdivide.location = (580, -200)
    subdivide.inputs["Cuts"].default_value = 4
    links.new(gin.outputs[_RESAMPLE_COUNT_SOCKET_NAME], subdivide.inputs["Cuts"])
    links.new(m2c.outputs[0], subdivide.inputs["Curve"])

    curve_chain_id = nodes.new("GeometryNodeInputNamedAttribute")
    curve_chain_id.label = _CURVE_JITTER_CHAIN_ID_LABEL
    curve_chain_id.location = (580, -600)
    curve_chain_id.data_type = "INT"
    curve_chain_id.inputs["Name"].default_value = chain_id_attr_name

    # 線種ごとの線幅値を反映する。
    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.location = (-20, 120)
    width_attr.data_type = "FLOAT"
    width_attr.inputs["Name"].default_value = width_attr_name

    width_switch = nodes.new("GeometryNodeSwitch")
    width_switch.location = (180, 120)
    width_switch.input_type = "FLOAT"
    width_switch.inputs["False"].default_value = 1.0
    links.new(width_attr.outputs["Exists"], width_switch.inputs["Switch"])
    links.new(width_attr.outputs["Attribute"], width_switch.inputs["True"])

    width_min = nodes.new("ShaderNodeMath")
    width_min.location = (360, 120)
    width_min.operation = "MAXIMUM"
    width_min.inputs[1].default_value = 0.0
    links.new(width_switch.outputs["Output"], width_min.inputs[0])

    width_max = nodes.new("ShaderNodeMath")
    width_max.location = (540, 120)
    width_max.operation = "MINIMUM"
    width_max.inputs[1].default_value = 1.0
    links.new(width_min.outputs[0], width_max.inputs[0])

    curve_scale = _add_curve_width_scale(
        nodes,
        links,
        gin,
        x_offset=520,
        random_id_output=curve_chain_id.outputs["Attribute"],
    )
    combined_scale = nodes.new("ShaderNodeMath")
    combined_scale.location = (760, 120)
    combined_scale.operation = "MULTIPLY"
    links.new(width_max.outputs[0], combined_scale.inputs[0])
    links.new(curve_scale, combined_scale.inputs[1])

    safe_curve_scale = nodes.new("ShaderNodeMath")
    safe_curve_scale.label = _SAFE_CURVE_SCALE_LABEL
    safe_curve_scale.location = (940, 120)
    safe_curve_scale.operation = "MAXIMUM"
    safe_curve_scale.inputs[1].default_value = _MIN_CURVE_TO_MESH_SCALE
    links.new(combined_scale.outputs[0], safe_curve_scale.inputs[0])

    # Curve Circle: チューブ断面
    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.label = _PROFILE_NODE_LABEL
    circle.location = (220, -400)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = INNER_TUBE_PROFILE_RESOLUTION
    radius_half = nodes.new("ShaderNodeMath")
    radius_half.label = _RADIUS_HALF_NODE_LABEL
    radius_half.location = (20, -360)
    radius_half.operation = "MULTIPLY"
    radius_half.inputs[1].default_value = 0.5
    links.new(gin.outputs["線の太さ"], radius_half.inputs[0])
    links.new(radius_half.outputs[0], circle.inputs["Radius"])  # 線の太さ → 半径

    # Curve to Mesh: カーブをチューブメッシュに変換
    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (740, -200)
    links.new(subdivide.outputs["Curve"], c2m.inputs[0])  # Curve
    links.new(circle.outputs[0], c2m.inputs[1])  # Profile Curve
    if "Scale" in c2m.inputs:
        links.new(safe_curve_scale.outputs[0], c2m.inputs["Scale"])  # 頂点/線上位置ごとの太さ倍率
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True

    mark_generated = nodes.new("GeometryNodeStoreNamedAttribute")
    mark_generated.label = _GENERATED_LINE_NODE_LABEL
    mark_generated.location = (720, -360)
    mark_generated.data_type = "BOOLEAN"
    mark_generated.domain = "FACE"
    mark_generated.inputs["Name"].default_value = GENERATED_LINE_ATTR
    mark_generated.inputs["Value"].default_value = True
    links.new(c2m.outputs[0], mark_generated.inputs["Geometry"])

    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.label = _SMOOTH_NODE_LABEL
    smooth.location = (820, -360)
    links.new(mark_generated.outputs["Geometry"], smooth.inputs["Geometry"])

    # Set Material: マテリアル入力ソケットから割り当て
    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (900, -200)
    links.new(smooth.outputs["Geometry"], setmat.inputs[0])
    links.new(gin.outputs["マテリアル"], setmat.inputs["Material"])

    # Join Geometry: 元メッシュ + 内部線ジオメトリ
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (1100, 0)
    links.new(gin.outputs[0], join.inputs[0])  # 元ジオメトリ
    links.new(setmat.outputs[0], join.inputs[0])  # 内部線ジオメトリ

    links.new(join.outputs[0], gout.inputs[0])

    return tree


def _get_or_create_tree(
    tree_name: str = GN_TREE_NAME,
    width_attr_name: str = VG_INNER_LINE_WIDTH,
    chain_id_attr_name: str = _CHAIN_ID_ATTR,
    marked_attr_name: str = _FREESTYLE_EDGE_ATTR,
) -> bpy.types.NodeTree:
    def _rebuild():
        return _create_node_tree(
            tree_name,
            width_attr_name,
            chain_id_attr_name,
            marked_attr_name,
        )

    tree = bpy.data.node_groups.get(tree_name)
    if tree is not None:
        if _find_socket_id(tree, "マテリアル") is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, "ライン素材番号") is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _MARKED_ONLY_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _OFFSET_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _MIDPOINT_FACTOR_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _MIDPOINT_JITTER_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _RESAMPLE_COUNT_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _WIDTH_CURVE_25_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _WIDTH_CURVE_50_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if _find_socket_id(tree, _WIDTH_CURVE_75_SOCKET_NAME) is None:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(n.bl_idname == "GeometryNodeDeleteGeometry" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(n.bl_idname == "GeometryNodeInputNamedAttribute" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not _uses_named_attribute(tree, width_attr_name):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not _uses_named_attribute(tree, marked_attr_name):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _GENERATED_LINE_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _RADIUS_HALF_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _PROFILE_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _SMOOTH_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _MARKED_SELECTION_SWITCH_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _CURVE_WIDTH_SCALE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _SUBDIVIDE_CURVE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if any(n.bl_idname == "GeometryNodeResampleCurve" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _SELECTED_EDGE_MESH_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _CHAIN_INSTANCE_SPLIT_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _CHAIN_ANGLE_FILTER_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _AUTO_EDGE_ALLOWED_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _AUTO_ANGLE_FILTER_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        compare_node = next(
            (n for n in tree.nodes if getattr(n, "label", "") == _EDGE_ANGLE_COMPARE_LABEL),
            None,
        )
        if compare_node is None or getattr(compare_node, "operation", "") != "GREATER_EQUAL":
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        chain_compare = next(
            (n for n in tree.nodes if getattr(n, "label", "") == _CHAIN_SELECTION_COMPARE_LABEL),
            None,
        )
        if chain_compare is None or getattr(chain_compare, "operation", "") != "GREATER_THAN":
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _CURVE_JITTER_CENTER_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _CURVE_JITTER_CHAIN_ID_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not any(getattr(n, "label", "") == _SAFE_CURVE_SCALE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if not _uses_named_attribute(tree, chain_id_attr_name):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        if any(n.bl_idname == "GeometryNodeSetCurveRadius" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        radius_socket = _find_interface_socket(tree, "線の太さ")
        if radius_socket is not None and getattr(radius_socket, "max_value", 0.0) < 1.0:
            bpy.data.node_groups.remove(tree)
            return _rebuild()
        return tree
    return _rebuild()


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


def _modifier_float_prop(mod: bpy.types.Modifier, prop_name: str) -> float | None:
    try:
        value = mod.get(prop_name, None)
    except TypeError:
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _modifier_bool_prop(mod: bpy.types.Modifier, prop_name: str) -> bool | None:
    try:
        value = mod.get(prop_name, None)
    except TypeError:
        return None
    if value is None:
        return None
    return bool(value)


def _modifier_socket_float(mod: bpy.types.Modifier, socket_name: str) -> float | None:
    tree = getattr(mod, "node_group", None)
    sid = _find_socket_id(tree, socket_name) if tree is not None else None
    if sid is None:
        return None
    try:
        return float(mod[sid])
    except (KeyError, TypeError, ValueError):
        return None


def _modifier_socket_bool(mod: bpy.types.Modifier, socket_name: str) -> bool | None:
    tree = getattr(mod, "node_group", None)
    sid = _find_socket_id(tree, socket_name) if tree is not None else None
    if sid is None:
        return None
    try:
        return bool(mod[sid])
    except (KeyError, TypeError, ValueError):
        return None


def _float_changed(current: float | None, requested: float | None) -> bool:
    if requested is None:
        return False
    if current is None:
        return True
    return abs(float(current) - float(requested)) > _CHAIN_INPUT_EPSILON


def _bool_changed(current: bool | None, requested: bool | None) -> bool:
    if requested is None:
        return False
    if current is None:
        return True
    return bool(current) != bool(requested)


def _current_chain_angle(mod: bpy.types.Modifier | None) -> float | None:
    if mod is None:
        return None
    value = _modifier_float_prop(mod, _CHAIN_ANGLE_PROP)
    if value is not None:
        return value
    return _modifier_socket_float(mod, "検出角度")


def _current_chain_marked(mod: bpy.types.Modifier | None) -> bool | None:
    if mod is None:
        return None
    value = _modifier_bool_prop(mod, _CHAIN_MARKED_PROP)
    if value is not None:
        return value
    return _modifier_socket_bool(mod, _MARKED_ONLY_SOCKET_NAME)


def _current_chain_midpoint_angle(mod: bpy.types.Modifier | None) -> float | None:
    if mod is None:
        return None
    return _modifier_float_prop(mod, _CHAIN_MIDPOINT_ANGLE_PROP)


def _store_chain_inputs(
    mod: bpy.types.Modifier,
    angle: float,
    use_marked_edges: bool,
    midpoint_angle: float | None,
) -> None:
    mod[_CHAIN_ANGLE_PROP] = float(angle)
    mod[_CHAIN_MARKED_PROP] = bool(use_marked_edges)
    if midpoint_angle is not None:
        mod[_CHAIN_MIDPOINT_ANGLE_PROP] = float(midpoint_angle)


def _chain_inputs_changed(
    mod: bpy.types.Modifier | None,
    *,
    angle: float | None = None,
    use_marked_edges: bool | None = None,
    midpoint_angle: float | None = None,
) -> bool:
    if mod is None:
        return True
    return (
        _float_changed(_current_chain_angle(mod), angle)
        or _bool_changed(_current_chain_marked(mod), use_marked_edges)
        or _float_changed(_current_chain_midpoint_angle(mod), midpoint_angle)
    )


# ------------------------------------------------------------------
# 適用 / 削除 / 更新
# ------------------------------------------------------------------

def apply_inner_lines(
    obj: bpy.types.Object,
    angle: float = 0.5236,
    thickness: float = 0.0005,
    offset: float = 0.0,
    material: bpy.types.Material | None = None,
    use_marked_edges: bool = False,
    midpoint_factor: float = 0.0,
    midpoint_angle: float | None = None,
    midpoint_jitter_percent: float = 0.0,
    resample_count: int | None = None,
    width_curve_25: float = 0.25,
    width_curve_50: float = 0.50,
    width_curve_75: float = 0.75,
    enable: bool = True,
    modifier_name: str = GN_MODIFIER_NAME,
    tree_name: str = GN_TREE_NAME,
    width_group_name: str = VG_INNER_LINE_WIDTH,
    chain_id_attr_name: str = _CHAIN_ID_ATTR,
    marked_attr_name: str = _FREESTYLE_EDGE_ATTR,
) -> bool:
    """内部線 GN モディファイアを適用. 成功時 True."""
    if obj.type != "MESH":
        return False

    if midpoint_angle is None:
        settings = getattr(obj, "bmanga_line_settings", None)
        midpoint_angle = (
            inner_width_split_angle(settings, angle)
            if settings is not None
            else angle
        )
    if (
        modifier_name == GN_MODIFIER_NAME
        and tree_name == GN_TREE_NAME
        and chain_id_attr_name == _CHAIN_ID_ATTR
        and not use_marked_edges
    ):
        from . import inner_line_cache, subdivision_lod

        if resample_count is None and (
            abs(float(midpoint_factor or 0.0)) > 1.0e-7
            or abs(float(midpoint_jitter_percent or 0.0)) > 1.0e-7
        ):
            resample_count = subdivision_lod.line_resample_count(obj)
        return inner_line_cache.apply_cached_inner_lines(
            obj,
            angle=angle,
            thickness=thickness,
            offset=offset,
            material=material,
            midpoint_angle=midpoint_angle,
            midpoint_factor=midpoint_factor,
            midpoint_jitter_percent=midpoint_jitter_percent,
            resample_count=resample_count,
            width_curve_25=width_curve_25,
            width_curve_50=width_curve_50,
            width_curve_75=width_curve_75,
            chain_id_attr=chain_id_attr_name,
            marked_attr_name=marked_attr_name,
            scene=getattr(bpy.context, "scene", None),
            enable=enable,
        )
    inner_line_chains.update_chain_id_attribute(
        obj,
        angle,
        use_marked_edges,
        midpoint_angle,
        chain_id_attr=chain_id_attr_name,
        marked_attr_name=marked_attr_name,
    )
    tree = _get_or_create_tree(
        tree_name,
        width_group_name,
        chain_id_attr_name,
        marked_attr_name,
    )

    # 既存モディファイアを更新 or 新規作成
    mod = obj.modifiers.get(modifier_name)
    if mod is None:
        mod = obj.modifiers.new(name=modifier_name, type="NODES")
    _store_chain_inputs(mod, angle, use_marked_edges, midpoint_angle)
    visible = bool(enable) and not bool(obj.get(PROP_LINES_HIDDEN, False))
    if not visible:
        mod.show_viewport = False
        mod.show_render = False
    mod.node_group = tree
    mod.show_viewport = visible
    mod.show_render = visible

    # パラメータ設定
    sid_angle = _find_socket_id(tree, "検出角度")
    sid_thickness = _find_socket_id(tree, "線の太さ")
    sid_offset = _find_socket_id(tree, _OFFSET_SOCKET_NAME)
    sid_marked_only = _find_socket_id(tree, _MARKED_ONLY_SOCKET_NAME)
    sid_midpoint_factor = _find_socket_id(tree, _MIDPOINT_FACTOR_SOCKET_NAME)
    sid_midpoint_jitter = _find_socket_id(tree, _MIDPOINT_JITTER_SOCKET_NAME)
    sid_resample_count = _find_socket_id(tree, _RESAMPLE_COUNT_SOCKET_NAME)
    sid_curve_25 = _find_socket_id(tree, _WIDTH_CURVE_25_SOCKET_NAME)
    sid_curve_50 = _find_socket_id(tree, _WIDTH_CURVE_50_SOCKET_NAME)
    sid_curve_75 = _find_socket_id(tree, _WIDTH_CURVE_75_SOCKET_NAME)
    if sid_angle is not None:
        mod[sid_angle] = _node_angle_threshold(angle)
    if sid_thickness is not None:
        mod[sid_thickness] = thickness
    if sid_offset is not None:
        mod[sid_offset] = offset
    if sid_marked_only is not None:
        mod[sid_marked_only] = bool(use_marked_edges)
    if sid_midpoint_factor is not None:
        mod[sid_midpoint_factor] = float(midpoint_factor)
    if sid_midpoint_jitter is not None:
        mod[sid_midpoint_jitter] = float(midpoint_jitter_percent)
    if sid_resample_count is not None:
        if resample_count is None:
            from . import subdivision_lod

            resample_count = subdivision_lod.line_resample_count(obj)
        mod[sid_resample_count] = max(1, int(resample_count))
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
    vg = obj.vertex_groups.get(width_group_name)
    if vg is None:
        vg = obj.vertex_groups.new(name=width_group_name)
        vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")

    # 既存のメッシュ調整を先に通し、ライン生成は最後にまとめる。
    modifier_stack.reorder_line_modifiers(obj)

    return True


def remove_inner_lines(
    obj: bpy.types.Object,
    modifier_name: str = GN_MODIFIER_NAME,
) -> bool:
    """内部線 GN モディファイアを削除."""
    if obj.type != "MESH":
        return False
    if modifier_name == GN_MODIFIER_NAME:
        from . import inner_line_cache

        return inner_line_cache.remove_cached_inner_lines(obj)
    mod = obj.modifiers.get(modifier_name)
    if mod is None:
        return False
    obj.modifiers.remove(mod)
    return True


def disable_inner_lines(
    obj: bpy.types.Object,
    modifier_name: str = GN_MODIFIER_NAME,
) -> bool:
    """内部線を削除せず無効化する."""
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(modifier_name)
    if mod is None:
        return False
    changed = bool(mod.show_viewport or mod.show_render)
    if not changed:
        return False
    mod.show_viewport = False
    mod.show_render = False
    return changed


def enable_inner_lines(
    obj: bpy.types.Object,
    modifier_name: str = GN_MODIFIER_NAME,
) -> bool:
    """内部線を表示・レンダー有効に戻す."""
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(modifier_name)
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
    midpoint_angle: float | None = None,
    midpoint_jitter_percent: float | None = None,
    resample_count: int | None = None,
    width_curve_25: float | None = None,
    width_curve_50: float | None = None,
    width_curve_75: float | None = None,
    material: bpy.types.Material | None = None,
    modifier_name: str = GN_MODIFIER_NAME,
    chain_id_attr_name: str = _CHAIN_ID_ATTR,
    marked_attr_name: str = _FREESTYLE_EDGE_ATTR,
) -> bool:
    """既存モディファイアのパラメータを更新."""
    current_mod = obj.modifiers.get(modifier_name)
    if modifier_name == GN_MODIFIER_NAME:
        from . import inner_line_cache

        if inner_line_cache.is_cached_modifier(current_mod):
            return inner_line_cache.update_cached_parameters(
                obj,
                thickness=thickness,
                offset=offset,
                material=material,
                midpoint_factor=midpoint_factor,
                midpoint_jitter_percent=midpoint_jitter_percent,
                resample_count=resample_count,
                width_curve_25=width_curve_25,
                width_curve_50=width_curve_50,
                width_curve_75=width_curve_75,
            )
    requested_midpoint_angle = midpoint_angle
    if requested_midpoint_angle is None and angle is not None:
        current_angle = angle if angle is not None else _current_chain_angle(current_mod)
        settings = getattr(obj, "bmanga_line_settings", None)
        requested_midpoint_angle = (
            inner_width_split_angle(
                settings,
                current_angle if current_angle is not None else None,
            )
            if settings is not None
            else current_angle
        )
    chain_input_requested = (
        angle is not None
        or use_marked_edges is not None
        or requested_midpoint_angle is not None
    )
    if chain_input_requested and _chain_inputs_changed(
        current_mod,
        angle=angle,
        use_marked_edges=use_marked_edges,
        midpoint_angle=requested_midpoint_angle,
    ):
        current_angle = angle if angle is not None else _current_chain_angle(current_mod)
        current_marked = (
            use_marked_edges
            if use_marked_edges is not None
            else _current_chain_marked(current_mod)
        )
        current_midpoint_angle = (
            requested_midpoint_angle
            if requested_midpoint_angle is not None
            else _current_chain_midpoint_angle(current_mod)
        )
        if current_midpoint_angle is None:
            settings = getattr(obj, "bmanga_line_settings", None)
            current_midpoint_angle = (
                inner_width_split_angle(
                    settings,
                    current_angle if current_angle is not None else None,
                )
                if settings is not None
                else current_angle
            )
        inner_line_chains.update_chain_id_attribute(
            obj,
            float(current_angle if current_angle is not None else 0.5236),
            bool(current_marked if current_marked is not None else False),
            current_midpoint_angle,
            chain_id_attr=chain_id_attr_name,
            marked_attr_name=marked_attr_name,
        )
        if current_mod is not None:
            _store_chain_inputs(
                current_mod,
                float(current_angle if current_angle is not None else 0.5236),
                bool(current_marked if current_marked is not None else False),
                current_midpoint_angle,
            )

    mod = obj.modifiers.get(modifier_name)
    if mod is None or mod.node_group is None:
        return False
    tree = mod.node_group
    if angle is not None:
        sid = _find_socket_id(tree, "検出角度")
        if sid is not None:
            mod[sid] = _node_angle_threshold(angle)
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
    if midpoint_jitter_percent is not None:
        sid = _find_socket_id(tree, _MIDPOINT_JITTER_SOCKET_NAME)
        if sid is not None:
            mod[sid] = float(midpoint_jitter_percent)
    if resample_count is not None:
        sid = _find_socket_id(tree, _RESAMPLE_COUNT_SOCKET_NAME)
        if sid is not None:
            mod[sid] = max(1, int(resample_count))
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


# ------------------------------------------------------------------
# 保存済みファイルの旧内部線ノード修復
# ------------------------------------------------------------------

def repair_scene_inner_lines(scene: bpy.types.Scene | None = None) -> int:
    from . import inner_line_repair
    return inner_line_repair.repair_scene_inner_lines(scene)


def register() -> None:
    from . import inner_line_repair
    inner_line_repair.register()


def unregister() -> None:
    from . import inner_line_repair
    inner_line_repair.unregister()
