"""Evaluated outline width attribute for midpoint width control."""

from __future__ import annotations

import math

import bpy

from . import intersection_shell_node_helpers, modifier_stack
from .core import (
    COLOR_ATTR_NAME,
    OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
)


TREE_NAME = "BML_OutlineWidthAttributeV2"
_GEOMETRY_SOCKET = "Geometry"
_MIDPOINT_FACTOR_SOCKET = "中間頂点の線幅調整"
_MIDPOINT_JITTER_SOCKET = "中間頂点の乱れ (%)"
_MIDPOINT_ANGLE_SOCKET = "検出角度"
_WIDTH_CURVE_25_SOCKET = "線幅カーブ25%"
_WIDTH_CURVE_50_SOCKET = "線幅カーブ50%"
_WIDTH_CURVE_75_SOCKET = "線幅カーブ75%"
_SHARP_EDGE_ANGLE_SOCKET = "形状保持の検出角度"
_CREASE_EDGE_ATTR = "crease_edge"
_CURVE_WIDTH_LABEL = "BML_OutlineEvaluatedWidth"
_RESAMPLE_COUNT = 48


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    interface = getattr(tree, "interface", None)
    items = getattr(interface, "items_tree", ()) if interface is not None else ()
    for item in items:
        if getattr(item, "item_type", None) == "SOCKET" and item.name == name:
            return item.identifier
    return None


def _math(nodes, operation: str, loc, value0=None, value1=None):
    node = nodes.new("ShaderNodeMath")
    node.location = loc
    node.operation = operation
    if value0 is not None:
        node.inputs[0].default_value = value0
    if value1 is not None and len(node.inputs) > 1:
        node.inputs[1].default_value = value1
    return node


def _bool_or(nodes, links, left_output, right_output, loc):
    node = nodes.new("FunctionNodeBooleanMath")
    node.location = loc
    node.operation = "OR"
    links.new(left_output, node.inputs[0])
    links.new(right_output, node.inputs[1])
    return node.outputs[0]


def _bool_not(nodes, links, value_output, loc):
    node = nodes.new("FunctionNodeBooleanMath")
    node.location = loc
    node.operation = "NOT"
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


def _value(nodes, value: float, loc):
    node = nodes.new("ShaderNodeValue")
    node.location = loc
    node.outputs[0].default_value = value
    return node.outputs[0]


def _setup_interface(tree: bpy.types.NodeTree) -> None:
    tree.interface.new_socket(
        name=_GEOMETRY_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name=_GEOMETRY_SOCKET,
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    factor = tree.interface.new_socket(
        name=_MIDPOINT_FACTOR_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    factor.default_value = 0.0
    factor.min_value = -1.0
    factor.max_value = 1.0
    jitter = tree.interface.new_socket(
        name=_MIDPOINT_JITTER_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    jitter.default_value = 0.0
    jitter.min_value = 0.0
    jitter.max_value = 50.0
    midpoint_angle = tree.interface.new_socket(
        name=_MIDPOINT_ANGLE_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    midpoint_angle.default_value = 1.7453292520
    midpoint_angle.min_value = 0.0
    midpoint_angle.max_value = math.pi
    if hasattr(midpoint_angle, "subtype"):
        midpoint_angle.subtype = "ANGLE"
    sharp_angle = tree.interface.new_socket(
        name=_SHARP_EDGE_ANGLE_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    sharp_angle.default_value = math.radians(60.0)
    sharp_angle.min_value = 0.0
    sharp_angle.max_value = math.pi
    if hasattr(sharp_angle, "subtype"):
        sharp_angle.subtype = "ANGLE"
    for name, default in (
        (_WIDTH_CURVE_25_SOCKET, 0.25),
        (_WIDTH_CURVE_50_SOCKET, 0.50),
        (_WIDTH_CURVE_75_SOCKET, 0.75),
    ):
        sock = tree.interface.new_socket(
            name=name,
            in_out="INPUT",
            socket_type="NodeSocketFloat",
        )
        sock.default_value = default
        sock.min_value = 0.0
        sock.max_value = 1.0


def _selected_edge_curves(nodes, links, geometry_output, gin):
    edge_angle = nodes.new("GeometryNodeInputMeshEdgeAngle")
    edge_angle.location = (-980, -180)

    sharp_compare = nodes.new("FunctionNodeCompare")
    sharp_compare.location = (-760, -180)
    sharp_compare.data_type = "FLOAT"
    sharp_compare.operation = "GREATER_EQUAL"
    links.new(edge_angle.outputs["Unsigned Angle"], sharp_compare.inputs[0])
    links.new(gin.outputs[_SHARP_EDGE_ANGLE_SOCKET], sharp_compare.inputs[1])

    crease_attr = nodes.new("GeometryNodeInputNamedAttribute")
    crease_attr.location = (-980, -360)
    crease_attr.data_type = "FLOAT"
    crease_attr.inputs["Name"].default_value = _CREASE_EDGE_ATTR

    crease_positive = nodes.new("FunctionNodeCompare")
    crease_positive.location = (-760, -360)
    crease_positive.data_type = "FLOAT"
    crease_positive.operation = "GREATER_THAN"
    crease_positive.inputs[1].default_value = 0.0
    links.new(crease_attr.outputs["Attribute"], crease_positive.inputs[0])

    crease_marked = nodes.new("FunctionNodeBooleanMath")
    crease_marked.location = (-560, -340)
    crease_marked.operation = "AND"
    links.new(crease_attr.outputs["Exists"], crease_marked.inputs[0])
    links.new(crease_positive.outputs[0], crease_marked.inputs[1])

    selected = _bool_or(
        nodes,
        links,
        sharp_compare.outputs[0],
        crease_marked.outputs[0],
        (-360, -240),
    )
    not_selected = _bool_not(nodes, links, selected, (-160, -240))

    selected_edges = nodes.new("GeometryNodeDeleteGeometry")
    selected_edges.location = (40, -240)
    selected_edges.domain = "EDGE"
    links.new(geometry_output, selected_edges.inputs["Geometry"])
    links.new(not_selected, selected_edges.inputs["Selection"])

    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (260, -240)
    links.new(selected_edges.outputs["Geometry"], m2c.inputs["Mesh"])
    m2c.inputs["Selection"].default_value = True

    resample = nodes.new("GeometryNodeResampleCurve")
    resample.location = (480, -240)
    if hasattr(resample, "mode"):
        resample.mode = "COUNT"
    count_input = resample.inputs.get("Count")
    if count_input is not None:
        count_input.default_value = _RESAMPLE_COUNT
    links.new(m2c.outputs["Curve"], resample.inputs["Curve"])
    return resample.outputs["Curve"]


def _create_node_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(TREE_NAME, type="GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1300, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (1300, 0)

    curves = _selected_edge_curves(nodes, links, gin.outputs["Geometry"], gin)
    scale = intersection_shell_node_helpers.add_curve_midpoint_width_scale(
        nodes,
        links,
        curves,
        gin.outputs[_MIDPOINT_ANGLE_SOCKET],
        gin.outputs[_MIDPOINT_FACTOR_SOCKET],
        (720, -720),
        label=_CURVE_WIDTH_LABEL + "Split",
        width_curve_outputs=(
            gin.outputs[_WIDTH_CURVE_25_SOCKET],
            gin.outputs[_WIDTH_CURVE_50_SOCKET],
            gin.outputs[_WIDTH_CURVE_75_SOCKET],
        ),
        jitter_output=gin.outputs[_MIDPOINT_JITTER_SOCKET],
        angle_split_min_segment_fraction=0.04,
        angle_split_confirmation_offset=1,
    )

    store_curve_width = nodes.new("GeometryNodeStoreNamedAttribute")
    store_curve_width.location = (720, -240)
    store_curve_width.data_type = "FLOAT"
    store_curve_width.domain = "POINT"
    store_curve_width.inputs["Name"].default_value = _CURVE_WIDTH_LABEL
    links.new(curves, store_curve_width.inputs["Geometry"])
    links.new(scale, store_curve_width.inputs["Value"])

    curve_points = nodes.new("GeometryNodeCurveToPoints")
    curve_points.location = (960, -420)
    if hasattr(curve_points, "mode"):
        curve_points.mode = "COUNT"
    count_input = curve_points.inputs.get("Count")
    if count_input is not None:
        count_input.default_value = _RESAMPLE_COUNT
    links.new(store_curve_width.outputs["Geometry"], curve_points.inputs["Curve"])

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (720, 120)

    sample_nearest = nodes.new("GeometryNodeSampleNearest")
    sample_nearest.location = (1160, -160)
    links.new(curve_points.outputs["Points"], sample_nearest.inputs["Geometry"])
    links.new(position.outputs["Position"], sample_nearest.inputs["Sample Position"])

    curve_width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    curve_width_attr.location = (960, -520)
    curve_width_attr.data_type = "FLOAT"
    curve_width_attr.inputs["Name"].default_value = _CURVE_WIDTH_LABEL

    sample_width = nodes.new("GeometryNodeSampleIndex")
    sample_width.location = (1380, -360)
    sample_width.data_type = "FLOAT"
    links.new(curve_points.outputs["Points"], sample_width.inputs["Geometry"])
    links.new(curve_width_attr.outputs["Attribute"], sample_width.inputs["Value"])
    links.new(sample_nearest.outputs["Index"], sample_width.inputs["Index"])

    base_attr = nodes.new("GeometryNodeInputNamedAttribute")
    base_attr.location = (720, 360)
    base_attr.data_type = "FLOAT"
    base_attr.inputs["Name"].default_value = COLOR_ATTR_NAME
    one = _value(nodes, 1.0, (960, 260))
    base = _switch_float(
        nodes,
        links,
        base_attr.outputs["Exists"],
        one,
        base_attr.outputs["Attribute"],
        (1160, 260),
    )

    final_width = _math(nodes, "MULTIPLY", (1600, 80))
    links.new(base, final_width.inputs[0])
    links.new(sample_width.outputs["Value"], final_width.inputs[1])

    final_min = _math(nodes, "MAXIMUM", (1800, 80), value1=0.0)
    links.new(final_width.outputs[0], final_min.inputs[0])
    final_max = _math(nodes, "MINIMUM", (2000, 80), value1=1.0)
    links.new(final_min.outputs[0], final_max.inputs[0])

    store_result = nodes.new("GeometryNodeStoreNamedAttribute")
    store_result.location = (2220, 0)
    store_result.data_type = "FLOAT"
    store_result.domain = "POINT"
    store_result.inputs["Name"].default_value = COLOR_ATTR_NAME
    links.new(gin.outputs["Geometry"], store_result.inputs["Geometry"])
    links.new(final_max.outputs[0], store_result.inputs["Value"])
    links.new(store_result.outputs["Geometry"], gout.inputs["Geometry"])
    return tree


def get_or_create_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(TREE_NAME)
    if tree is None:
        tree = _create_node_tree()
    return tree


def outline_width_attribute_needed(settings) -> bool:
    return (
        bool(getattr(settings, "auto_subdivision_for_midpoint", False))
        and abs(float(getattr(settings, "edge_smooth_factor", 0.0))) > 0.001
    )


def ensure_outline_width_attribute(obj: bpy.types.Object, settings=None) -> bool:
    if obj.type != "MESH":
        return False
    settings = settings or getattr(obj, "bmanga_line_settings", None)
    if settings is None or not outline_width_attribute_needed(settings):
        return remove_outline_width_attribute(obj)
    creased_edges = obj.get("bml_auto_midpoint_subsurf_crease_edges")
    if creased_edges is not None and len(creased_edges) == 0:
        return remove_outline_width_attribute(obj)

    mod = obj.modifiers.get(OUTLINE_WIDTH_ATTR_MODIFIER_NAME)
    changed = False
    if mod is None:
        mod = obj.modifiers.new(OUTLINE_WIDTH_ATTR_MODIFIER_NAME, "NODES")
        changed = True
    tree = get_or_create_tree()
    if mod.node_group is not tree:
        mod.node_group = tree
        changed = True

    values = {
        _MIDPOINT_FACTOR_SOCKET: float(getattr(settings, "edge_smooth_factor", 0.0)),
        _MIDPOINT_JITTER_SOCKET: float(
            getattr(settings, "edge_midpoint_jitter_percent", 0.0)
        ),
        _MIDPOINT_ANGLE_SOCKET: float(getattr(settings, "edge_midpoint_angle", 1.7453292520)),
        _SHARP_EDGE_ANGLE_SOCKET: math.radians(60.0),
        _WIDTH_CURVE_25_SOCKET: float(getattr(settings, "edge_width_curve_25", 0.25)),
        _WIDTH_CURVE_50_SOCKET: float(getattr(settings, "edge_width_curve_50", 0.50)),
        _WIDTH_CURVE_75_SOCKET: float(getattr(settings, "edge_width_curve_75", 0.75)),
    }
    for socket_name, value in values.items():
        sid = _find_socket_id(tree, socket_name)
        if sid is None:
            continue
        try:
            current = float(mod[sid])
        except (KeyError, TypeError, ValueError):
            current = None
        if current is None or abs(current - value) > 1.0e-9:
            mod[sid] = value
            changed = True

    modifier_stack.reorder_line_modifiers(obj)
    return changed


def remove_outline_width_attribute(obj: bpy.types.Object) -> bool:
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(OUTLINE_WIDTH_ATTR_MODIFIER_NAME)
    if mod is None:
        return False
    obj.modifiers.remove(mod)
    return True
