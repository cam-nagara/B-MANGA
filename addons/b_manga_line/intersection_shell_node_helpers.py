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


def _add_width_curve_outputs(nodes, links, raw_output, c25, c50, c75, x_offset=0):
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


def _add_width_curve(nodes, links, raw_output, gin, sockets, x_offset=0):
    c25, c50, c75 = (gin.outputs[name] for name in sockets)
    return _add_width_curve_outputs(nodes, links, raw_output, c25, c50, c75, x_offset)


def _scale_from_center_weight(
    nodes,
    links,
    center_weight_output,
    factor_output,
    loc,
    *,
    width_curve_outputs=None,
):
    x, y = loc
    if width_curve_outputs is not None:
        c25, c50, c75 = width_curve_outputs
        curved = _add_width_curve_outputs(
            nodes,
            links,
            center_weight_output,
            c25,
            c50,
            c75,
            x_offset=x,
        )
    else:
        curved = center_weight_output

    one_minus = _math(nodes, "SUBTRACT", (x + 500, y + 160), value0=1.0)
    links.new(curved, one_minus.inputs[1])
    pos_drop = _math(nodes, "MULTIPLY", (x + 700, y + 180))
    links.new(one_minus.outputs[0], pos_drop.inputs[0])
    links.new(factor_output, pos_drop.inputs[1])
    pos_scale = _math(nodes, "SUBTRACT", (x + 900, y + 180), value0=1.0)
    links.new(pos_drop.outputs[0], pos_scale.inputs[1])

    abs_factor = _math(nodes, "ABSOLUTE", (x + 500, y - 20))
    links.new(factor_output, abs_factor.inputs[0])
    neg_drop = _math(nodes, "MULTIPLY", (x + 700, y - 20))
    links.new(curved, neg_drop.inputs[0])
    links.new(abs_factor.outputs[0], neg_drop.inputs[1])
    neg_scale = _math(nodes, "SUBTRACT", (x + 900, y - 20), value0=1.0)
    links.new(neg_drop.outputs[0], neg_scale.inputs[1])

    positive = nodes.new("FunctionNodeCompare")
    positive.location = (x + 700, y + 360)
    positive.data_type = "FLOAT"
    positive.operation = "GREATER_EQUAL"
    positive.inputs[1].default_value = 0.0
    links.new(factor_output, positive.inputs[0])
    scale_switch = _switch_float(
        nodes,
        links,
        positive.outputs[0],
        pos_scale.outputs[0],
        neg_scale.outputs[0],
        (x + 1100, y + 80),
    )
    clamped_min = _math(nodes, "MAXIMUM", (x + 1300, y + 80), value1=0.0)
    links.new(scale_switch, clamped_min.inputs[0])
    clamped_max = _math(nodes, "MINIMUM", (x + 1480, y + 80), value1=1.0)
    links.new(clamped_min.outputs[0], clamped_max.inputs[0])
    return clamped_max.outputs[0]


def _scale_from_endpoint_selection(nodes, links, selection_output, factor_output, loc):
    x, y = loc

    positive = nodes.new("FunctionNodeCompare")
    positive.location = (x, y + 280)
    positive.data_type = "FLOAT"
    positive.operation = "GREATER_EQUAL"
    positive.inputs[1].default_value = 0.0
    links.new(factor_output, positive.inputs[0])

    one = nodes.new("ShaderNodeValue")
    one.location = (x, y + 80)
    one.outputs[0].default_value = 1.0

    pos_endpoint = _math(nodes, "SUBTRACT", (x + 220, y + 220), value0=1.0)
    links.new(factor_output, pos_endpoint.inputs[1])

    abs_factor = _math(nodes, "ABSOLUTE", (x + 220, y - 40))
    links.new(factor_output, abs_factor.inputs[0])
    neg_center = _math(nodes, "SUBTRACT", (x + 420, y - 40), value0=1.0)
    links.new(abs_factor.outputs[0], neg_center.inputs[1])

    endpoint_scale = _switch_float(
        nodes,
        links,
        positive.outputs[0],
        pos_endpoint.outputs[0],
        one.outputs[0],
        (x + 520, y + 220),
    )
    center_scale = _switch_float(
        nodes,
        links,
        positive.outputs[0],
        one.outputs[0],
        neg_center.outputs[0],
        (x + 640, y - 20),
    )
    scale = _switch_float(
        nodes,
        links,
        selection_output,
        endpoint_scale,
        center_scale,
        (x + 860, y + 100),
    )
    clamped_min = _math(nodes, "MAXIMUM", (x + 1060, y + 100), value1=0.0)
    links.new(scale, clamped_min.inputs[0])
    return clamped_min.outputs[0]


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
    return _scale_from_center_weight(
        nodes,
        links,
        curved,
        gin.outputs[midpoint_factor_socket],
        (x_offset, 0),
    )


def _sample_curve_position(nodes, links, curve_output, position_output, index_output, loc):
    sample = nodes.new("GeometryNodeSampleIndex")
    sample.location = loc
    sample.data_type = "FLOAT_VECTOR"
    sample.domain = "POINT"
    links.new(curve_output, sample.inputs["Geometry"])
    links.new(position_output, sample.inputs["Value"])
    links.new(index_output, sample.inputs["Index"])
    return sample.outputs["Value"]


def _offset_point(nodes, links, index_output, offset: int, loc):
    node = nodes.new("GeometryNodeOffsetPointInCurve")
    node.location = loc
    links.new(index_output, node.inputs["Point Index"])
    node.inputs["Offset"].default_value = offset
    return node


def _bool_not(nodes, links, value_output, loc):
    node = nodes.new("FunctionNodeBooleanMath")
    node.location = loc
    node.operation = "NOT"
    links.new(value_output, node.inputs[0])
    return node.outputs[0]


def _bool_or(nodes, links, left_output, right_output, loc):
    node = nodes.new("FunctionNodeBooleanMath")
    node.location = loc
    node.operation = "OR"
    links.new(left_output, node.inputs[0])
    links.new(right_output, node.inputs[1])
    return node.outputs[0]


def _bool_and(nodes, links, left_output, right_output, loc):
    node = nodes.new("FunctionNodeBooleanMath")
    node.location = loc
    node.operation = "AND"
    links.new(left_output, node.inputs[0])
    links.new(right_output, node.inputs[1])
    return node.outputs[0]


def _curve_point_split_from_offsets(
    nodes,
    links,
    curve_output,
    position_output,
    index_output,
    before_offset: int,
    center_offset: int,
    after_offset: int,
    angle_cos_output,
    loc,
    *,
    label: str | None = None,
):
    x, y = loc
    before = _offset_point(nodes, links, index_output, before_offset, (x, y + 180))
    center = _offset_point(nodes, links, index_output, center_offset, (x, y + 20))
    after = _offset_point(nodes, links, index_output, after_offset, (x, y - 140))
    before_pos = _sample_curve_position(
        nodes,
        links,
        curve_output,
        position_output,
        before.outputs["Point Index"],
        (x + 220, y + 180),
    )
    center_pos = _sample_curve_position(
        nodes,
        links,
        curve_output,
        position_output,
        center.outputs["Point Index"],
        (x + 220, y + 20),
    )
    after_pos = _sample_curve_position(
        nodes,
        links,
        curve_output,
        position_output,
        after.outputs["Point Index"],
        (x + 220, y - 140),
    )
    angle_split = _curve_angle_split_from_positions(
        nodes,
        links,
        before_pos,
        center_pos,
        after_pos,
        angle_cos_output,
        (x + 440, y + 20),
        label=label,
    )
    invalid_before = _bool_not(
        nodes,
        links,
        before.outputs["Is Valid Offset"],
        (x + 1120, y + 140),
    )
    invalid_after = _bool_not(
        nodes,
        links,
        after.outputs["Is Valid Offset"],
        (x + 1120, y - 60),
    )
    endpoint = _bool_or(nodes, links, invalid_before, invalid_after, (x + 1320, y + 40))
    return _bool_or(nodes, links, angle_split, endpoint, (x + 1520, y + 40))


def _curve_angle_split_from_positions(
    nodes,
    links,
    before_position,
    center_position,
    after_position,
    angle_cos_output,
    loc,
    *,
    label: str | None = None,
):
    x, y = loc
    vec_before = nodes.new("ShaderNodeVectorMath")
    vec_before.location = (x, y + 120)
    vec_before.operation = "SUBTRACT"
    links.new(before_position, vec_before.inputs[0])
    links.new(center_position, vec_before.inputs[1])

    vec_after = nodes.new("ShaderNodeVectorMath")
    vec_after.location = (x, y - 80)
    vec_after.operation = "SUBTRACT"
    links.new(after_position, vec_after.inputs[0])
    links.new(center_position, vec_after.inputs[1])

    norm_before = nodes.new("ShaderNodeVectorMath")
    norm_before.location = (x + 220, y + 120)
    norm_before.operation = "NORMALIZE"
    links.new(vec_before.outputs["Vector"], norm_before.inputs[0])

    norm_after = nodes.new("ShaderNodeVectorMath")
    norm_after.location = (x + 220, y - 80)
    norm_after.operation = "NORMALIZE"
    links.new(vec_after.outputs["Vector"], norm_after.inputs[0])

    dot = nodes.new("ShaderNodeVectorMath")
    dot.location = (x + 440, y + 20)
    dot.operation = "DOT_PRODUCT"
    links.new(norm_before.outputs["Vector"], dot.inputs[0])
    links.new(norm_after.outputs["Vector"], dot.inputs[1])

    angle_split = nodes.new("FunctionNodeCompare")
    angle_split.location = (x + 660, y + 20)
    if label:
        angle_split.label = label
    angle_split.data_type = "FLOAT"
    angle_split.operation = "GREATER_THAN"
    links.new(dot.outputs["Value"], angle_split.inputs[0])
    links.new(angle_cos_output, angle_split.inputs[1])
    return angle_split.outputs[0]


def add_curve_midpoint_width_scale(
    nodes,
    links,
    curve_output,
    angle_output,
    factor_output,
    loc,
    *,
    label: str,
    width_curve_outputs=None,
):
    """Return a Curve to Mesh scale field without changing the curve shape."""
    x, y = loc
    index = nodes.new("GeometryNodeInputIndex")
    index.location = (x, y + 420)
    position = nodes.new("GeometryNodeInputPosition")
    position.location = (x, y + 260)

    angle_cos = _math(nodes, "COSINE", (x + 180, y + 120))
    links.new(angle_output, angle_cos.inputs[0])

    current_split = _curve_point_split_from_offsets(
        nodes,
        links,
        curve_output,
        position.outputs["Position"],
        index.outputs["Index"],
        -1,
        0,
        1,
        angle_cos.outputs[0],
        (x + 360, y + 300),
        label=label,
    )
    prev_split = _curve_point_split_from_offsets(
        nodes,
        links,
        curve_output,
        position.outputs["Position"],
        index.outputs["Index"],
        -2,
        -1,
        0,
        angle_cos.outputs[0],
        (x + 360, y - 160),
    )
    next_split = _curve_point_split_from_offsets(
        nodes,
        links,
        curve_output,
        position.outputs["Position"],
        index.outputs["Index"],
        0,
        1,
        2,
        angle_cos.outputs[0],
        (x + 360, y - 620),
    )
    prev2_split = _curve_point_split_from_offsets(
        nodes,
        links,
        curve_output,
        position.outputs["Position"],
        index.outputs["Index"],
        -3,
        -2,
        -1,
        angle_cos.outputs[0],
        (x + 2040, y - 160),
    )
    next2_split = _curve_point_split_from_offsets(
        nodes,
        links,
        curve_output,
        position.outputs["Position"],
        index.outputs["Index"],
        1,
        2,
        3,
        angle_cos.outputs[0],
        (x + 2040, y - 620),
    )

    adjacent_center = _bool_and(
        nodes,
        links,
        prev_split,
        next_split,
        (x + 3720, y + 160),
    )
    two_step_center = _bool_and(
        nodes,
        links,
        prev2_split,
        next2_split,
        (x + 3720, y - 20),
    )
    full_center = _bool_or(
        nodes,
        links,
        adjacent_center,
        two_step_center,
        (x + 3920, y + 80),
    )
    near_prev = _bool_or(nodes, links, prev_split, prev2_split, (x + 3720, y - 240))
    near_next = _bool_or(nodes, links, next_split, next2_split, (x + 3720, y - 400))
    near_endpoint = _bool_or(nodes, links, near_prev, near_next, (x + 3920, y - 320))

    one = nodes.new("ShaderNodeValue")
    one.location = (x + 3920, y - 80)
    one.outputs[0].default_value = 1.0
    half = nodes.new("ShaderNodeValue")
    half.location = (x + 3920, y - 220)
    half.outputs[0].default_value = 0.5
    zero = nodes.new("ShaderNodeValue")
    zero.location = (x + 3920, y - 560)
    zero.outputs[0].default_value = 0.0

    center_or_half = _switch_float(
        nodes,
        links,
        full_center,
        one.outputs[0],
        half.outputs[0],
        (x + 4140, y - 60),
    )
    non_endpoint_weight = _switch_float(
        nodes,
        links,
        near_endpoint,
        center_or_half,
        one.outputs[0],
        (x + 4360, y - 60),
    )
    center_weight = _switch_float(
        nodes,
        links,
        current_split,
        zero.outputs[0],
        non_endpoint_weight,
        (x + 4580, y + 40),
    )

    return _scale_from_center_weight(
        nodes,
        links,
        center_weight,
        factor_output,
        (x + 4800, y + 40),
        width_curve_outputs=width_curve_outputs,
    )
