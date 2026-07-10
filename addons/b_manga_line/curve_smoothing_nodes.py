"""Geometry Nodes helpers for corner-preserving generated line smoothing."""

from __future__ import annotations

import math


DEFAULT_CORNER_ANGLE = math.radians(40.0)
DEFAULT_RESOLUTION = 6


def _bezier_with_vector_handles(nodes, links, curve, x, y, label):
    spline_type = nodes.new("GeometryNodeCurveSplineType")
    spline_type.label = label + " Bezier"
    spline_type.spline_type = "BEZIER"
    spline_type.location = (x, y)
    links.new(curve, spline_type.inputs["Curve"])

    vector_handles = nodes.new("GeometryNodeCurveSetHandles")
    vector_handles.label = label + " Vector Corners"
    vector_handles.handle_type = "VECTOR"
    vector_handles.mode = {"LEFT", "RIGHT"}
    vector_handles.location = (x + 180, y)
    vector_handles.inputs["Selection"].default_value = True
    links.new(spline_type.outputs["Curve"], vector_handles.inputs["Curve"])
    return vector_handles.outputs["Curve"]


def _neighbor_fields(nodes, links, x, y):
    index = nodes.new("GeometryNodeInputIndex")
    index.location = (x, y - 260)
    previous = nodes.new("GeometryNodeOffsetPointInCurve")
    previous.location = (x + 180, y - 220)
    previous.inputs["Offset"].default_value = -1
    links.new(index.outputs["Index"], previous.inputs["Point Index"])
    following = nodes.new("GeometryNodeOffsetPointInCurve")
    following.location = (x + 180, y - 380)
    following.inputs["Offset"].default_value = 1
    links.new(index.outputs["Index"], following.inputs["Point Index"])

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (x + 180, y - 560)
    previous_position = nodes.new("GeometryNodeFieldAtIndex")
    previous_position.data_type = "FLOAT_VECTOR"
    previous_position.domain = "POINT"
    previous_position.location = (x + 380, y - 240)
    links.new(position.outputs["Position"], previous_position.inputs["Value"])
    links.new(previous.outputs["Point Index"], previous_position.inputs["Index"])
    following_position = nodes.new("GeometryNodeFieldAtIndex")
    following_position.data_type = "FLOAT_VECTOR"
    following_position.domain = "POINT"
    following_position.location = (x + 380, y - 440)
    links.new(position.outputs["Position"], following_position.inputs["Value"])
    links.new(following.outputs["Point Index"], following_position.inputs["Index"])
    return position, previous, following, previous_position, following_position


def _smooth_point_selection(nodes, links, fields, x, y, corner_angle):
    position, previous, following, previous_position, following_position = fields
    incoming = nodes.new("ShaderNodeVectorMath")
    incoming.operation = "SUBTRACT"
    incoming.location = (x + 580, y - 240)
    links.new(position.outputs["Position"], incoming.inputs[0])
    links.new(previous_position.outputs["Value"], incoming.inputs[1])
    outgoing = nodes.new("ShaderNodeVectorMath")
    outgoing.operation = "SUBTRACT"
    outgoing.location = (x + 580, y - 440)
    links.new(following_position.outputs["Value"], outgoing.inputs[0])
    links.new(position.outputs["Position"], outgoing.inputs[1])
    incoming_normalized = nodes.new("ShaderNodeVectorMath")
    incoming_normalized.operation = "NORMALIZE"
    incoming_normalized.location = (x + 780, y - 240)
    links.new(incoming.outputs["Vector"], incoming_normalized.inputs[0])
    outgoing_normalized = nodes.new("ShaderNodeVectorMath")
    outgoing_normalized.operation = "NORMALIZE"
    outgoing_normalized.location = (x + 780, y - 440)
    links.new(outgoing.outputs["Vector"], outgoing_normalized.inputs[0])

    dot = nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.location = (x + 980, y - 320)
    links.new(incoming_normalized.outputs["Vector"], dot.inputs[0])
    links.new(outgoing_normalized.outputs["Vector"], dot.inputs[1])
    shallow_turn = nodes.new("FunctionNodeCompare")
    shallow_turn.data_type = "FLOAT"
    shallow_turn.operation = "GREATER_THAN"
    shallow_turn.location = (x + 1180, y - 320)
    shallow_turn.inputs[1].default_value = math.cos(float(corner_angle))
    links.new(dot.outputs["Value"], shallow_turn.inputs[0])

    valid_neighbors = nodes.new("FunctionNodeBooleanMath")
    valid_neighbors.operation = "AND"
    valid_neighbors.location = (x + 980, y - 520)
    links.new(previous.outputs["Is Valid Offset"], valid_neighbors.inputs[0])
    links.new(following.outputs["Is Valid Offset"], valid_neighbors.inputs[1])
    smooth_point = nodes.new("FunctionNodeBooleanMath")
    smooth_point.operation = "AND"
    smooth_point.location = (x + 1380, y - 360)
    links.new(shallow_turn.outputs["Result"], smooth_point.inputs[0])
    links.new(valid_neighbors.outputs["Boolean"], smooth_point.inputs[1])
    return smooth_point.outputs["Boolean"]


def _resolved_auto_curve(nodes, links, curve, selection, x, y, label, resolution):
    auto_handles = nodes.new("GeometryNodeCurveSetHandles")
    auto_handles.label = label + " Auto Smooth"
    auto_handles.handle_type = "AUTO"
    auto_handles.mode = {"LEFT", "RIGHT"}
    auto_handles.location = (x + 1580, y)
    links.new(curve, auto_handles.inputs["Curve"])
    links.new(selection, auto_handles.inputs["Selection"])

    spline_resolution = nodes.new("GeometryNodeSetSplineResolution")
    spline_resolution.label = label + " Resolution"
    spline_resolution.location = (x + 1780, y)
    spline_resolution.inputs["Resolution"].default_value = max(1, int(resolution))
    links.new(auto_handles.outputs["Curve"], spline_resolution.inputs["Curve"])
    return spline_resolution.outputs["Curve"]


def add_corner_preserving_bezier(
    nodes,
    links,
    curve,
    enabled,
    location,
    *,
    label: str,
    corner_angle: float = DEFAULT_CORNER_ANGLE,
    resolution: int = DEFAULT_RESOLUTION,
):
    """Smooth shallow curve turns while retaining endpoints and sharp corners."""
    x, y = location
    vector_curve = _bezier_with_vector_handles(nodes, links, curve, x, y, label)
    fields = _neighbor_fields(nodes, links, x, y)
    smooth_points = _smooth_point_selection(
        nodes, links, fields, x, y, corner_angle
    )
    smooth_curve = _resolved_auto_curve(
        nodes, links, vector_curve, smooth_points, x, y, label, resolution
    )

    switch = nodes.new("GeometryNodeSwitch")
    switch.label = label + " Enabled"
    switch.input_type = "GEOMETRY"
    switch.location = (x + 1980, y)
    links.new(enabled, switch.inputs["Switch"])
    links.new(curve, switch.inputs["False"])
    links.new(smooth_curve, switch.inputs["True"])
    return switch.outputs["Output"]
