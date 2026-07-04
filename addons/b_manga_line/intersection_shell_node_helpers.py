"""Node helper builders for shell-style intersection lines."""

from __future__ import annotations


_PATH_SPLIT_ATTR = "BML_PathSplitVertex"


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


def _switch_vector(nodes, links, switch_output, true_output, false_output, loc):
    node = nodes.new("GeometryNodeSwitch")
    node.location = loc
    node.input_type = "VECTOR"
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


def _compare_int_equal(nodes, links, left_output, right_output, loc):
    node = nodes.new("FunctionNodeCompare")
    node.location = loc
    node.data_type = "INT"
    node.operation = "EQUAL"
    links.new(left_output, node.inputs[2])
    links.new(right_output, node.inputs[3])
    return node.outputs[0]


def _compare_int_value(nodes, links, value_output, operation: str, value: int, loc):
    node = nodes.new("FunctionNodeCompare")
    node.location = loc
    node.data_type = "INT"
    node.operation = operation
    links.new(value_output, node.inputs[2])
    node.inputs[3].default_value = value
    return node.outputs[0]


def _sample_index(nodes, links, mesh_output, value_output, index_output, loc, data_type):
    node = nodes.new("GeometryNodeSampleIndex")
    node.location = loc
    node.data_type = data_type
    node.domain = "EDGE"
    links.new(mesh_output, node.inputs["Geometry"])
    links.new(value_output, node.inputs["Value"])
    links.new(index_output, node.inputs["Index"])
    return node.outputs["Value"]


def _store_split_vertex_attribute(nodes, links, mesh_output, selection_output, loc):
    node = nodes.new("GeometryNodeStoreNamedAttribute")
    node.location = loc
    node.domain = "POINT"
    node.data_type = "BOOLEAN"
    links.new(mesh_output, node.inputs["Geometry"])
    node.inputs["Name"].default_value = _PATH_SPLIT_ATTR
    links.new(selection_output, node.inputs["Value"])
    return node.outputs["Geometry"]


def _edge_selection_from_vertex_selection(nodes, links, mesh_output, selection_output, loc):
    x, y = loc
    edge_vertices = nodes.new("GeometryNodeInputMeshEdgeVertices")
    edge_vertices.location = (x, y)

    sample1 = nodes.new("GeometryNodeSampleIndex")
    sample1.location = (x + 240, y + 80)
    sample1.data_type = "BOOLEAN"
    sample1.domain = "POINT"
    links.new(mesh_output, sample1.inputs["Geometry"])
    links.new(selection_output, sample1.inputs["Value"])
    links.new(edge_vertices.outputs["Vertex Index 1"], sample1.inputs["Index"])

    sample2 = nodes.new("GeometryNodeSampleIndex")
    sample2.location = (x + 240, y - 80)
    sample2.data_type = "BOOLEAN"
    sample2.domain = "POINT"
    links.new(mesh_output, sample2.inputs["Geometry"])
    links.new(selection_output, sample2.inputs["Value"])
    links.new(edge_vertices.outputs["Vertex Index 2"], sample2.inputs["Index"])

    edge_selection = nodes.new("FunctionNodeBooleanMath")
    edge_selection.location = (x + 480, y)
    edge_selection.operation = "OR"
    links.new(sample1.outputs["Value"], edge_selection.inputs[0])
    links.new(sample2.outputs["Value"], edge_selection.inputs[1])
    return edge_selection.outputs[0]


def _non_split_vertex_selection(nodes, links, loc):
    attr = nodes.new("GeometryNodeInputNamedAttribute")
    attr.location = loc
    attr.data_type = "BOOLEAN"
    attr.inputs["Name"].default_value = _PATH_SPLIT_ATTR
    invert = nodes.new("FunctionNodeBooleanMath")
    invert.location = (loc[0] + 240, loc[1])
    invert.operation = "NOT"
    links.new(attr.outputs["Attribute"], invert.inputs[0])
    return invert.outputs[0]


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


def _edge_other_position(
    nodes,
    links,
    mesh_output,
    edge_vertices,
    edge_index_output,
    current_index_output,
    *,
    x_offset: int,
    y: int,
):
    vi1 = _sample_index(
        nodes,
        links,
        mesh_output,
        edge_vertices.outputs["Vertex Index 1"],
        edge_index_output,
        (x_offset, y + 220),
        "INT",
    )
    pos1 = _sample_index(
        nodes,
        links,
        mesh_output,
        edge_vertices.outputs["Position 1"],
        edge_index_output,
        (x_offset, y + 80),
        "FLOAT_VECTOR",
    )
    pos2 = _sample_index(
        nodes,
        links,
        mesh_output,
        edge_vertices.outputs["Position 2"],
        edge_index_output,
        (x_offset, y - 60),
        "FLOAT_VECTOR",
    )
    vi1_is_current = _compare_int_equal(
        nodes,
        links,
        vi1,
        current_index_output,
        (x_offset + 220, y + 220),
    )
    return _switch_vector(
        nodes,
        links,
        vi1_is_current,
        pos2,
        pos1,
        (x_offset + 420, y + 60),
    )


def _add_turn_angle_selection(nodes, links, mesh_output, angle_output, loc):
    """Select degree-2 vertices where the path angle is below the threshold."""
    x, y = loc
    index = nodes.new("GeometryNodeInputIndex")
    index.location = (x, y + 420)
    position = nodes.new("GeometryNodeInputPosition")
    position.location = (x, y + 260)

    degree_neighbors = nodes.new("GeometryNodeInputMeshVertexNeighbors")
    degree_neighbors.location = (x, y + 80)
    degree_two = _compare_int_value(
        nodes,
        links,
        degree_neighbors.outputs["Vertex Count"],
        "EQUAL",
        2,
        (x + 220, y + 80),
    )

    edge0 = nodes.new("GeometryNodeEdgesOfVertex")
    edge0.location = (x + 220, y + 420)
    links.new(index.outputs["Index"], edge0.inputs["Vertex Index"])
    edge0.inputs["Sort Index"].default_value = 0

    edge1 = nodes.new("GeometryNodeEdgesOfVertex")
    edge1.location = (x + 220, y + 260)
    links.new(index.outputs["Index"], edge1.inputs["Vertex Index"])
    edge1.inputs["Sort Index"].default_value = 1

    edge_vertices = nodes.new("GeometryNodeInputMeshEdgeVertices")
    edge_vertices.location = (x + 460, y + 520)

    other0 = _edge_other_position(
        nodes,
        links,
        mesh_output,
        edge_vertices,
        edge0.outputs["Edge Index"],
        index.outputs["Index"],
        x_offset=x + 460,
        y=y + 260,
    )
    other1 = _edge_other_position(
        nodes,
        links,
        mesh_output,
        edge_vertices,
        edge1.outputs["Edge Index"],
        index.outputs["Index"],
        x_offset=x + 460,
        y=y - 160,
    )

    vec0 = nodes.new("ShaderNodeVectorMath")
    vec0.location = (x + 1100, y + 220)
    vec0.operation = "SUBTRACT"
    links.new(other0, vec0.inputs[0])
    links.new(position.outputs["Position"], vec0.inputs[1])

    vec1 = nodes.new("ShaderNodeVectorMath")
    vec1.location = (x + 1100, y - 20)
    vec1.operation = "SUBTRACT"
    links.new(other1, vec1.inputs[0])
    links.new(position.outputs["Position"], vec1.inputs[1])

    norm0 = nodes.new("ShaderNodeVectorMath")
    norm0.location = (x + 1320, y + 220)
    norm0.operation = "NORMALIZE"
    links.new(vec0.outputs["Vector"], norm0.inputs[0])

    norm1 = nodes.new("ShaderNodeVectorMath")
    norm1.location = (x + 1320, y - 20)
    norm1.operation = "NORMALIZE"
    links.new(vec1.outputs["Vector"], norm1.inputs[0])

    dot = nodes.new("ShaderNodeVectorMath")
    dot.location = (x + 1540, y + 100)
    dot.operation = "DOT_PRODUCT"
    links.new(norm0.outputs["Vector"], dot.inputs[0])
    links.new(norm1.outputs["Vector"], dot.inputs[1])

    angle_cos = _math(nodes, "COSINE", (x + 1540, y - 120))
    links.new(angle_output, angle_cos.inputs[0])

    acute_path_angle = nodes.new("FunctionNodeCompare")
    acute_path_angle.location = (x + 1720, y + 100)
    acute_path_angle.data_type = "FLOAT"
    acute_path_angle.operation = "GREATER_THAN"
    links.new(dot.outputs["Value"], acute_path_angle.inputs[0])
    links.new(angle_cos.outputs[0], acute_path_angle.inputs[1])

    selected = nodes.new("FunctionNodeBooleanMath")
    selected.location = (x + 1940, y + 80)
    selected.operation = "AND"
    links.new(degree_two, selected.inputs[0])
    links.new(acute_path_angle.outputs[0], selected.inputs[1])
    return selected.outputs[0]


def add_branch_split(nodes, links, mesh_output, label: str, *, angle_output=None):
    neighbors = nodes.new("GeometryNodeInputMeshVertexNeighbors")
    neighbors.location = (660, 80)
    branch_selection = _compare_int_value(
        nodes,
        links,
        neighbors.outputs["Vertex Count"],
        "GREATER_EQUAL",
        3,
        (820, 80),
    )
    selection = branch_selection
    if angle_output is not None:
        angle_selection = _add_turn_angle_selection(
            nodes,
            links,
            mesh_output,
            angle_output,
            (420, -520),
        )
        combine = nodes.new("FunctionNodeBooleanMath")
        combine.location = (1020, 0)
        combine.operation = "OR"
        links.new(branch_selection, combine.inputs[0])
        links.new(angle_selection, combine.inputs[1])
        selection = combine.outputs[0]

    marked_mesh = _store_split_vertex_attribute(
        nodes,
        links,
        mesh_output,
        selection,
        (1120, 120),
    )
    edge_selection = _edge_selection_from_vertex_selection(
        nodes,
        links,
        marked_mesh,
        selection,
        (1120, -260),
    )

    split = nodes.new("GeometryNodeSplitEdges")
    split.label = label
    split.location = (1720, -120) if angle_output is not None else (1480, -120)
    links.new(marked_mesh, split.inputs["Mesh"])
    links.new(edge_selection, split.inputs["Selection"])

    merge = nodes.new("GeometryNodeMergeByDistance")
    merge.location = (1940, -120) if angle_output is not None else (1700, -120)
    merge.inputs["Distance"].default_value = 0.000001
    links.new(split.outputs["Mesh"], merge.inputs["Geometry"])
    links.new(
        _non_split_vertex_selection(nodes, links, (1720, -360)),
        merge.inputs["Selection"],
    )
    return merge.outputs["Geometry"]


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
