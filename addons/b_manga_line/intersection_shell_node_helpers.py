"""Node helper builders for shell-style intersection lines."""

from __future__ import annotations


def _math(nodes, operation: str, loc, value0=None, value1=None):
    node = nodes.new("ShaderNodeMath")
    node.location = loc
    node.operation = operation
    if value0 is not None:
        node.inputs[0].default_value = value0
    if value1 is not None and len(node.inputs) > 1:
        node.inputs[1].default_value = value1
    return node


def _switch_float(nodes, links, switch_output, true_output, false_output, loc):
    node = nodes.new("GeometryNodeSwitch")
    node.location = loc
    node.input_type = "FLOAT"
    links.new(switch_output, node.inputs["Switch"])
    links.new(false_output, node.inputs["False"])
    links.new(true_output, node.inputs["True"])
    return node.outputs["Output"]


def _value_or_socket(nodes, links, socket_output, default, loc):
    if socket_output is not None:
        return socket_output
    value = nodes.new("ShaderNodeValue")
    value.location = loc
    value.outputs[0].default_value = default
    return value.outputs[0]


def _curve_segment(nodes, links, raw_output, y0_output, y1_output, x0, x1, x_offset, y):
    sub = _math(nodes, "SUBTRACT", (x_offset - 600, y), value1=x0)
    links.new(raw_output, sub.inputs[0])
    div = _math(nodes, "DIVIDE", (x_offset - 420, y), value1=(x1 - x0))
    links.new(sub.outputs[0], div.inputs[0])
    y0 = _value_or_socket(nodes, links, y0_output, x0, (x_offset - 600, y - 80))
    y1 = _value_or_socket(nodes, links, y1_output, x1, (x_offset - 600, y - 160))
    span = _math(nodes, "SUBTRACT", (x_offset - 240, y - 80))
    links.new(y1, span.inputs[0])
    links.new(y0, span.inputs[1])
    scaled = _math(nodes, "MULTIPLY", (x_offset - 60, y))
    links.new(div.outputs[0], scaled.inputs[0])
    links.new(span.outputs[0], scaled.inputs[1])
    add = _math(nodes, "ADD", (x_offset + 120, y))
    links.new(y0, add.inputs[0])
    links.new(scaled.outputs[0], add.inputs[1])
    return add.outputs[0]


def _compare_le(nodes, links, value_output, threshold, loc):
    node = nodes.new("FunctionNodeCompare")
    node.location = loc
    node.data_type = "FLOAT"
    node.operation = "LESS_EQUAL"
    node.inputs[1].default_value = threshold
    links.new(value_output, node.inputs[0])
    return node.outputs[0]


def _add_width_curve(nodes, links, raw_output, gin, sockets, x_offset=0):
    c25, c50, c75 = (gin.outputs[name] for name in sockets)
    seg0 = _curve_segment(nodes, links, raw_output, None, c25, 0.00, 0.25, x_offset, -240)
    seg1 = _curve_segment(nodes, links, raw_output, c25, c50, 0.25, 0.50, x_offset, -420)
    seg2 = _curve_segment(nodes, links, raw_output, c50, c75, 0.50, 0.75, x_offset, -600)
    seg3 = _curve_segment(nodes, links, raw_output, c75, None, 0.75, 1.00, x_offset, -780)
    le25 = _compare_le(nodes, links, raw_output, 0.25, (x_offset + 280, -220))
    le50 = _compare_le(nodes, links, raw_output, 0.50, (x_offset + 280, -400))
    le75 = _compare_le(nodes, links, raw_output, 0.75, (x_offset + 280, -580))
    switch75 = _switch_float(nodes, links, le75, seg2, seg3, (x_offset + 500, -580))
    switch50 = _switch_float(nodes, links, le50, seg1, switch75, (x_offset + 700, -400))
    return _switch_float(nodes, links, le25, seg0, switch50, (x_offset + 900, -220))


def add_branch_split(nodes, links, mesh_output, label: str):
    neighbors = nodes.new("GeometryNodeInputMeshVertexNeighbors")
    neighbors.location = (660, 80)
    compare = nodes.new("FunctionNodeCompare")
    compare.location = (820, 80)
    compare.data_type = "INT"
    compare.operation = "GREATER_EQUAL"
    links.new(neighbors.outputs["Vertex Count"], compare.inputs[2])
    compare.inputs[3].default_value = 3

    split = nodes.new("GeometryNodeSplitEdges")
    split.label = label
    split.location = (780, -120)
    links.new(mesh_output, split.inputs["Mesh"])
    links.new(compare.outputs[0], split.inputs["Selection"])
    return split.outputs["Mesh"]


def add_jittered_midpoint_factor(
    nodes,
    links,
    gin,
    spline,
    x_offset: int,
    *,
    midpoint_jitter_socket: str,
    jitter_center_label: str,
):
    jitter_div = _math(nodes, "DIVIDE", (x_offset - 760, 280), value1=100.0)
    links.new(gin.outputs[midpoint_jitter_socket], jitter_div.inputs[0])
    jitter_min = _math(nodes, "MAXIMUM", (x_offset - 580, 280), value1=0.0)
    links.new(jitter_div.outputs[0], jitter_min.inputs[0])
    jitter_max = _math(nodes, "MINIMUM", (x_offset - 400, 280), value1=0.5)
    links.new(jitter_min.outputs[0], jitter_max.inputs[0])

    random = nodes.new("FunctionNodeRandomValue")
    random.location = (x_offset - 760, 440)
    random.data_type = "FLOAT"
    random.inputs[2].default_value = -1.0
    random.inputs[3].default_value = 1.0
    links.new(spline.outputs["Index"], random.inputs["ID"])

    offset = _math(nodes, "MULTIPLY", (x_offset - 220, 340))
    links.new(random.outputs[1], offset.inputs[0])
    links.new(jitter_max.outputs[0], offset.inputs[1])
    center = _math(nodes, "ADD", (x_offset - 20, 340), value0=0.5)
    center.label = jitter_center_label
    links.new(offset.outputs[0], center.inputs[1])
    center_min = _math(nodes, "MAXIMUM", (x_offset + 160, 340), value1=0.001)
    links.new(center.outputs[0], center_min.inputs[0])
    center_max = _math(nodes, "MINIMUM", (x_offset + 340, 340), value1=0.999)
    links.new(center_min.outputs[0], center_max.inputs[0])

    left_ratio = _math(nodes, "DIVIDE", (x_offset - 220, 120))
    links.new(spline.outputs["Factor"], left_ratio.inputs[0])
    links.new(center_max.outputs[0], left_ratio.inputs[1])
    one_minus_factor = _math(nodes, "SUBTRACT", (x_offset - 220, 0), value0=1.0)
    links.new(spline.outputs["Factor"], one_minus_factor.inputs[1])
    one_minus_center = _math(nodes, "SUBTRACT", (x_offset - 20, 0), value0=1.0)
    links.new(center_max.outputs[0], one_minus_center.inputs[1])
    right_ratio = _math(nodes, "DIVIDE", (x_offset + 160, 0))
    links.new(one_minus_factor.outputs[0], right_ratio.inputs[0])
    links.new(one_minus_center.outputs[0], right_ratio.inputs[1])

    left_side = nodes.new("FunctionNodeCompare")
    left_side.location = (x_offset + 160, 180)
    left_side.data_type = "FLOAT"
    left_side.operation = "LESS_EQUAL"
    links.new(spline.outputs["Factor"], left_side.inputs[0])
    links.new(center_max.outputs[0], left_side.inputs[1])

    raw = _switch_float(
        nodes,
        links,
        left_side.outputs[0],
        left_ratio.outputs[0],
        right_ratio.outputs[0],
        (x_offset + 520, 120),
    )
    raw_min = _math(nodes, "MAXIMUM", (x_offset + 700, 120), value1=0.0)
    links.new(raw, raw_min.inputs[0])
    raw_max = _math(nodes, "MINIMUM", (x_offset + 880, 120), value1=1.0)
    links.new(raw_min.outputs[0], raw_max.inputs[0])
    return raw_max.outputs[0]


def add_curve_width_scale(
    nodes,
    links,
    gin,
    x_offset: int,
    *,
    midpoint_factor_socket: str,
    midpoint_jitter_socket: str,
    width_curve_sockets: tuple[str, str, str],
    jitter_center_label: str,
):
    spline = nodes.new("GeometryNodeSplineParameter")
    spline.location = (x_offset - 760, 120)
    raw = add_jittered_midpoint_factor(
        nodes,
        links,
        gin,
        spline,
        x_offset,
        midpoint_jitter_socket=midpoint_jitter_socket,
        jitter_center_label=jitter_center_label,
    )
    curved = _add_width_curve(nodes, links, raw, gin, width_curve_sockets, x_offset)
    one_minus = _math(nodes, "SUBTRACT", (x_offset + 500, 160), value0=1.0)
    links.new(curved, one_minus.inputs[1])
    pos_drop = _math(nodes, "MULTIPLY", (x_offset + 700, 180))
    links.new(one_minus.outputs[0], pos_drop.inputs[0])
    links.new(gin.outputs[midpoint_factor_socket], pos_drop.inputs[1])
    pos_scale = _math(nodes, "SUBTRACT", (x_offset + 900, 180), value0=1.0)
    links.new(pos_drop.outputs[0], pos_scale.inputs[1])

    abs_factor = _math(nodes, "ABSOLUTE", (x_offset + 500, -20))
    links.new(gin.outputs[midpoint_factor_socket], abs_factor.inputs[0])
    neg_drop = _math(nodes, "MULTIPLY", (x_offset + 700, -20))
    links.new(curved, neg_drop.inputs[0])
    links.new(abs_factor.outputs[0], neg_drop.inputs[1])
    neg_scale = _math(nodes, "SUBTRACT", (x_offset + 900, -20), value0=1.0)
    links.new(neg_drop.outputs[0], neg_scale.inputs[1])

    positive = nodes.new("FunctionNodeCompare")
    positive.location = (x_offset + 700, 360)
    positive.data_type = "FLOAT"
    positive.operation = "GREATER_EQUAL"
    positive.inputs[1].default_value = 0.0
    links.new(gin.outputs[midpoint_factor_socket], positive.inputs[0])
    scale_switch = _switch_float(
        nodes,
        links,
        positive.outputs[0],
        pos_scale.outputs[0],
        neg_scale.outputs[0],
        (x_offset + 1100, 80),
    )
    clamped_min = _math(nodes, "MAXIMUM", (x_offset + 1300, 80), value1=0.0)
    links.new(scale_switch, clamped_min.inputs[0])
    clamped_max = _math(nodes, "MINIMUM", (x_offset + 1480, 80), value1=1.0)
    links.new(clamped_min.outputs[0], clamped_max.inputs[0])
    return clamped_max.outputs[0]
