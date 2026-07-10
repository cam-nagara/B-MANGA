"""Shared Geometry Nodes outline shell with local-only subdivision.

The source geometry is passed to the output untouched.  Only the generated
outline shell is subdivided, offset, flipped, and marked as a generated line.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import bpy

from . import intersection_shell_node_helpers


MODIFIER_NAME = "BML_OutlineLocalSubdivision"
TREE_NAME = "BML_OutlineLocalSubdivision"
GENERATED_LINE_ATTR = "BML_GeneratedLine"
LINE_WIDTH_ATTR = "BML_LineWidth"

_OWNER_KEY = "bml_outline_local_subdivision_owner"
_TREE_GENERATION = "BML_OutlineLocalSubdivision_Generation_20260710_V3"
_GENERATION_LABEL = "BML Local Subdivision 2026-07-10 V3"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_MATERIAL_SOCKET = "マテリアル"
_MIDPOINT_FACTOR_SOCKET = "中間頂点の線幅調整"
_MIDPOINT_JITTER_SOCKET = "中間頂点の乱れ (%)"
_MIDPOINT_ANGLE_SOCKET = "検出角度"
_CURVE_25_SOCKET = "変化グラフ 25%"
_CURVE_50_SOCKET = "変化グラフ 50%"
_CURVE_75_SOCKET = "変化グラフ 75%"
_SUBDIVISION_SOCKET = "ライン細分化"
_SHARP_ANGLE = math.radians(45.0)


def _socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    for item in tree.interface.items_tree:
        if (
            getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "in_out", None) == "INPUT"
            and item.name == name
        ):
            return item.identifier
    return None


def _new_float_socket(
    tree: bpy.types.NodeTree,
    name: str,
    default: float,
    minimum: float,
    maximum: float,
    *,
    angle: bool = False,
) -> None:
    socket = tree.interface.new_socket(
        name=name,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    socket.default_value = default
    socket.min_value = minimum
    socket.max_value = maximum
    if angle and hasattr(socket, "subtype"):
        socket.subtype = "ANGLE"


def _setup_interface(tree: bpy.types.NodeTree) -> None:
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    _new_float_socket(tree, _THICKNESS_SOCKET, 0.01, 0.0, 100.0)
    _new_float_socket(tree, _OFFSET_SOCKET, 0.0, -1.0, 1.0)
    tree.interface.new_socket(
        name=_MATERIAL_SOCKET, in_out="INPUT", socket_type="NodeSocketMaterial"
    )
    _new_float_socket(tree, _MIDPOINT_FACTOR_SOCKET, 0.0, -1.0, 1.0)
    _new_float_socket(tree, _MIDPOINT_JITTER_SOCKET, 0.0, 0.0, 100.0)
    _new_float_socket(
        tree,
        _MIDPOINT_ANGLE_SOCKET,
        math.radians(100.0),
        0.0,
        math.pi,
        angle=True,
    )
    for name, value in (
        (_CURVE_25_SOCKET, 0.25),
        (_CURVE_50_SOCKET, 0.50),
        (_CURVE_75_SOCKET, 0.75),
    ):
        _new_float_socket(tree, name, value, 0.0, 1.0)
    subdivision = tree.interface.new_socket(
        name=_SUBDIVISION_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketBool",
    )
    subdivision.default_value = True


def _math(nodes, operation: str, location, value: float | None = None):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.location = location
    if value is not None:
        node.inputs[1].default_value = value
    return node


def _bool_or(nodes, links, left, right, location):
    node = nodes.new("FunctionNodeBooleanMath")
    node.operation = "OR"
    node.location = location
    links.new(left, node.inputs[0])
    links.new(right, node.inputs[1])
    return node.outputs[0]


def _switch_float(nodes, links, condition, false_value, true_value, location):
    node = nodes.new("GeometryNodeSwitch")
    node.input_type = "FLOAT"
    node.location = location
    links.new(condition, node.inputs["Switch"])
    links.new(false_value, node.inputs["False"])
    links.new(true_value, node.inputs["True"])
    return node.outputs["Output"]


def _edge_curve(nodes, links, geometry, group_in):
    neighbors = nodes.new("GeometryNodeInputMeshEdgeNeighbors")
    neighbors.location = (-1220, -420)
    boundary = nodes.new("FunctionNodeCompare")
    boundary.data_type = "INT"
    boundary.operation = "EQUAL"
    boundary.location = (-1040, -420)
    boundary.inputs[3].default_value = 1
    links.new(neighbors.outputs["Face Count"], boundary.inputs[2])

    edge_angle = nodes.new("GeometryNodeInputMeshEdgeAngle")
    edge_angle.location = (-1220, -560)
    sharp = nodes.new("FunctionNodeCompare")
    sharp.data_type = "FLOAT"
    sharp.operation = "GREATER_EQUAL"
    sharp.location = (-1040, -560)
    sharp.inputs[1].default_value = _SHARP_ANGLE
    links.new(edge_angle.outputs["Unsigned Angle"], sharp.inputs[0])

    selected = _bool_or(
        nodes, links, boundary.outputs[0], sharp.outputs[0], (-840, -470)
    )
    to_curve = nodes.new("GeometryNodeMeshToCurve")
    to_curve.location = (-640, -360)
    links.new(geometry, to_curve.inputs["Mesh"])
    links.new(selected, to_curve.inputs["Selection"])

    subdivide = nodes.new("GeometryNodeSubdivideCurve")
    subdivide.label = _GENERATION_LABEL + " Width Curve"
    subdivide.location = (-440, -360)
    subdivide.inputs["Cuts"].default_value = 3
    links.new(to_curve.outputs["Curve"], subdivide.inputs["Curve"])
    return subdivide.outputs["Curve"]


def _curve_width_scale(nodes, links, curve, group_in):
    scale = intersection_shell_node_helpers.add_curve_midpoint_width_scale(
        nodes,
        links,
        curve,
        group_in.outputs[_MIDPOINT_ANGLE_SOCKET],
        group_in.outputs[_MIDPOINT_FACTOR_SOCKET],
        (-240, -900),
        label=_GENERATION_LABEL + " Width",
        width_curve_outputs=(
            group_in.outputs[_CURVE_25_SOCKET],
            group_in.outputs[_CURVE_50_SOCKET],
            group_in.outputs[_CURVE_75_SOCKET],
        ),
        jitter_output=group_in.outputs[_MIDPOINT_JITTER_SOCKET],
        angle_split_min_segment_fraction=0.0,
        angle_split_confirmation_offset=1,
    )
    stored = nodes.new("GeometryNodeStoreNamedAttribute")
    stored.label = _GENERATION_LABEL + " Curve Scale"
    stored.data_type = "FLOAT"
    stored.domain = "POINT"
    stored.location = (0, -360)
    stored.inputs["Name"].default_value = _TREE_GENERATION + "_scale"
    links.new(curve, stored.inputs["Geometry"])
    links.new(scale, stored.inputs["Value"])

    points = nodes.new("GeometryNodeCurveToPoints")
    points.location = (200, -360)
    points.mode = "EVALUATED"
    links.new(stored.outputs["Geometry"], points.inputs["Curve"])
    return points.outputs["Points"]


def _sample_width_scale(nodes, links, curve_points, target_geometry):
    point_count = nodes.new("GeometryNodeAttributeDomainSize")
    point_count.location = (380, -610)
    # Curve to Points outputs a point cloud. CURVE would always report zero
    # here and silently disable midpoint width sampling.
    point_count.component = "POINTCLOUD"
    links.new(curve_points, point_count.inputs["Geometry"])
    has_points = nodes.new("FunctionNodeCompare")
    has_points.data_type = "INT"
    has_points.operation = "GREATER_THAN"
    has_points.location = (580, -610)
    has_points.inputs[3].default_value = 0
    links.new(point_count.outputs["Point Count"], has_points.inputs[2])

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (380, -760)
    nearest = nodes.new("GeometryNodeSampleNearest")
    nearest.location = (580, -760)
    links.new(curve_points, nearest.inputs["Geometry"])
    links.new(position.outputs["Position"], nearest.inputs["Sample Position"])

    scale_attr = nodes.new("GeometryNodeInputNamedAttribute")
    scale_attr.data_type = "FLOAT"
    scale_attr.location = (580, -900)
    scale_attr.inputs["Name"].default_value = _TREE_GENERATION + "_scale"
    sampled = nodes.new("GeometryNodeSampleIndex")
    sampled.data_type = "FLOAT"
    sampled.location = (800, -760)
    links.new(curve_points, sampled.inputs["Geometry"])
    links.new(scale_attr.outputs["Attribute"], sampled.inputs["Value"])
    links.new(nearest.outputs["Index"], sampled.inputs["Index"])

    one = nodes.new("ShaderNodeValue")
    one.location = (800, -620)
    one.outputs[0].default_value = 1.0
    return _switch_float(
        nodes,
        links,
        has_points.outputs[0],
        one.outputs[0],
        sampled.outputs["Value"],
        (1000, -700),
    )


def _create_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(TREE_NAME, "GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1600, 100)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (1680, 100)

    # This path is deliberately never modified; it preserves source topology.
    source_geometry = group_in.outputs["Geometry"]
    curves = _edge_curve(nodes, links, source_geometry, group_in)
    curve_points = _curve_width_scale(nodes, links, curves, group_in)

    level = nodes.new("GeometryNodeSwitch")
    level.label = _GENERATION_LABEL + " Level 1 or 2"
    level.input_type = "INT"
    level.location = (-1340, 100)
    level.inputs["False"].default_value = 1
    level.inputs["True"].default_value = 2
    links.new(group_in.outputs[_SUBDIVISION_SOCKET], level.inputs["Switch"])

    subdivide = nodes.new("GeometryNodeSubdivideMesh")
    subdivide.label = _GENERATION_LABEL + " Mesh Subdivide"
    subdivide.location = (-1140, 100)
    links.new(source_geometry, subdivide.inputs["Mesh"])
    links.new(level.outputs["Output"], subdivide.inputs["Level"])

    midpoint_scale = _sample_width_scale(
        nodes, links, curve_points, subdivide.outputs["Mesh"]
    )
    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.data_type = "FLOAT"
    width_attr.location = (1000, -460)
    width_attr.inputs["Name"].default_value = LINE_WIDTH_ATTR
    one = nodes.new("ShaderNodeValue")
    one.location = (1000, -340)
    one.outputs[0].default_value = 1.0
    width_value = _switch_float(
        nodes,
        links,
        width_attr.outputs["Exists"],
        one.outputs[0],
        width_attr.outputs["Attribute"],
        (1200, -400),
    )
    combined_width = _math(nodes, "MULTIPLY", (1200, -140))
    links.new(midpoint_scale, combined_width.inputs[0])
    links.new(width_value, combined_width.inputs[1])
    safe_width = _math(nodes, "MAXIMUM", (1400, -140), 0.0)
    links.new(combined_width.outputs[0], safe_width.inputs[0])

    offset_normalized = _math(nodes, "ADD", (-940, -120), 1.0)
    links.new(group_in.outputs[_OFFSET_SOCKET], offset_normalized.inputs[0])
    offset_half = _math(nodes, "MULTIPLY", (-740, -120), 0.5)
    links.new(offset_normalized.outputs[0], offset_half.inputs[0])
    offset_safe = _math(nodes, "MAXIMUM", (-540, -120), 0.0)
    links.new(offset_half.outputs[0], offset_safe.inputs[0])
    thickness = _math(nodes, "MULTIPLY", (1400, 20))
    links.new(group_in.outputs[_THICKNESS_SOCKET], thickness.inputs[0])
    links.new(offset_safe.outputs[0], thickness.inputs[1])
    distance = _math(nodes, "MULTIPLY", (1580, 20))
    links.new(thickness.outputs[0], distance.inputs[0])
    links.new(safe_width.outputs[0], distance.inputs[1])

    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (1580, -120)
    offset_vector = nodes.new("ShaderNodeVectorMath")
    offset_vector.operation = "SCALE"
    offset_vector.location = (1780, -20)
    links.new(normal.outputs["Normal"], offset_vector.inputs[0])
    links.new(distance.outputs[0], offset_vector.inputs["Scale"])
    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (1980, 100)
    links.new(subdivide.outputs["Mesh"], set_position.inputs["Geometry"])
    links.new(offset_vector.outputs["Vector"], set_position.inputs["Offset"])

    flip = nodes.new("GeometryNodeFlipFaces")
    flip.location = (2180, 100)
    links.new(set_position.outputs["Geometry"], flip.inputs["Mesh"])
    material = nodes.new("GeometryNodeSetMaterial")
    material.location = (2380, 100)
    links.new(flip.outputs["Mesh"], material.inputs["Geometry"])
    links.new(group_in.outputs[_MATERIAL_SOCKET], material.inputs["Material"])
    generated = nodes.new("GeometryNodeStoreNamedAttribute")
    generated.label = _GENERATION_LABEL + " Generated Line"
    generated.data_type = "BOOLEAN"
    generated.domain = "FACE"
    generated.location = (2580, 100)
    generated.inputs["Name"].default_value = GENERATED_LINE_ATTR
    generated.inputs["Value"].default_value = True
    links.new(material.outputs["Geometry"], generated.inputs["Geometry"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (2820, 100)
    # Newer links are evaluated first.  Connect in reverse to retain source
    # material slots before the generated line material.
    links.new(generated.outputs["Geometry"], join.inputs["Geometry"])
    links.new(source_geometry, join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], group_out.inputs["Geometry"])
    return tree


def _tree_is_current(tree: bpy.types.NodeTree) -> bool:
    required = (
        _THICKNESS_SOCKET,
        _OFFSET_SOCKET,
        _MATERIAL_SOCKET,
        _MIDPOINT_FACTOR_SOCKET,
        _MIDPOINT_JITTER_SOCKET,
        _MIDPOINT_ANGLE_SOCKET,
        _CURVE_25_SOCKET,
        _CURVE_50_SOCKET,
        _CURVE_75_SOCKET,
        _SUBDIVISION_SOCKET,
    )
    return (
        all(_socket_id(tree, name) is not None for name in required)
        and any(
            node.bl_idname == "GeometryNodeSubdivideMesh"
            and node.label == _GENERATION_LABEL + " Mesh Subdivide"
            for node in tree.nodes
        )
        and any(
            node.bl_idname == "GeometryNodeStoreNamedAttribute"
            and node.label == _GENERATION_LABEL + " Generated Line"
            for node in tree.nodes
        )
    )


def _shared_tree() -> bpy.types.NodeTree:
    existing = bpy.data.node_groups.get(TREE_NAME)
    if existing is None:
        return _create_tree()
    if _tree_is_current(existing):
        return existing

    replacement = _create_tree()
    users = [
        mod
        for obj in bpy.data.objects
        for mod in obj.modifiers
        if getattr(mod, "type", None) == "NODES" and mod.node_group == existing
    ]
    for mod in users:
        mod.node_group = replacement
    bpy.data.node_groups.remove(existing)
    replacement.name = TREE_NAME
    return replacement


def is_modifier(mod: bpy.types.Modifier | None) -> bool:
    return bool(
        mod is not None
        and getattr(mod, "type", None) == "NODES"
        and (
            mod.get(_OWNER_KEY) == _TREE_GENERATION
            or getattr(getattr(mod, "node_group", None), "name", "") == TREE_NAME
        )
    )


def _owned_modifier(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    for mod in obj.modifiers:
        if is_modifier(mod):
            return mod
    return None


def get_modifier(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    """Return the owned local-outline modifier regardless of its display name."""
    return _owned_modifier(obj)


def _value(settings, names: tuple[str, ...], default):
    if settings is None:
        return default
    for name in names:
        if isinstance(settings, Mapping) and name in settings:
            return settings[name]
        if hasattr(settings, name):
            return getattr(settings, name)
    return default


def _set_input(mod: bpy.types.Modifier, name: str, value) -> None:
    identifier = _socket_id(mod.node_group, name)
    if identifier is not None:
        mod[identifier] = value


def _ensure_material_slot(obj: bpy.types.Object, material: bpy.types.Material | None) -> None:
    if material is not None and all(slot.material != material for slot in obj.material_slots):
        obj.data.materials.append(material)


def _sync_settings(mod: bpy.types.Modifier, settings) -> None:
    midpoint_factor = float(
        _value(
            settings,
            ("outline_edge_smooth_factor", "edge_smooth_factor", "midpoint_width_scale"),
            0.0,
        )
    )
    midpoint_jitter = float(
        _value(
            settings,
            (
                "outline_edge_midpoint_jitter_percent",
                "edge_midpoint_jitter_percent",
                "midpoint_jitter_percent",
            ),
            0.0,
        )
    )
    requested = bool(
        _value(
            settings,
            ("line_subdivision", "outline_line_subdivision", "auto_subdivision_for_midpoint"),
            True,
        )
    )
    values = {
        _MIDPOINT_FACTOR_SOCKET: midpoint_factor,
        _MIDPOINT_JITTER_SOCKET: midpoint_jitter,
        _MIDPOINT_ANGLE_SOCKET: _value(
            settings,
            ("outline_edge_midpoint_angle", "edge_midpoint_angle", "midpoint_angle"),
            math.radians(100.0),
        ),
        _CURVE_25_SOCKET: _value(
            settings, ("outline_edge_width_curve_25", "edge_width_curve_25", "width_curve_25"), 0.25
        ),
        _CURVE_50_SOCKET: _value(
            settings, ("outline_edge_width_curve_50", "edge_width_curve_50", "width_curve_50"), 0.50
        ),
        _CURVE_75_SOCKET: _value(
            settings, ("outline_edge_width_curve_75", "edge_width_curve_75", "width_curve_75"), 0.75
        ),
        # Avoid a 16x line-shell face increase when midpoint controls have no
        # visible effect. The source path remains untouched in either case.
        _SUBDIVISION_SOCKET: requested
        and (abs(midpoint_factor) > 1.0e-7 or abs(midpoint_jitter) > 1.0e-7),
    }
    for name, value in values.items():
        _set_input(mod, name, value)


def ensure(
    obj: bpy.types.Object,
    *,
    local_thickness: float,
    offset: float,
    material: bpy.types.Material | None,
    settings=None,
    enabled: bool = True,
) -> bpy.types.Modifier | None:
    """Create or update the owned nodes modifier without touching foreign ones."""
    if obj.type != "MESH" or obj.data is None:
        return None
    tree = _shared_tree()
    mod = _owned_modifier(obj)
    if mod is None:
        name = MODIFIER_NAME
        exact = obj.modifiers.get(name)
        if exact is not None and not is_modifier(exact):
            name = MODIFIER_NAME + "_Generated"
        mod = obj.modifiers.new(name=name, type="NODES")
        mod[_OWNER_KEY] = _TREE_GENERATION
    mod.node_group = tree
    _ensure_material_slot(obj, material)
    _set_input(mod, _THICKNESS_SOCKET, max(0.0, float(local_thickness)))
    _set_input(mod, _OFFSET_SOCKET, float(offset))
    _set_input(mod, _MATERIAL_SOCKET, material)
    _sync_settings(mod, settings)
    set_visibility(obj, enabled)
    return mod


def sync(
    obj: bpy.types.Object,
    *,
    local_thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
    settings=None,
    enabled: bool | None = None,
) -> bpy.types.Modifier | None:
    mod = _owned_modifier(obj)
    if mod is None:
        return None
    mod.node_group = _shared_tree()
    if local_thickness is not None:
        _set_input(mod, _THICKNESS_SOCKET, max(0.0, float(local_thickness)))
    if offset is not None:
        _set_input(mod, _OFFSET_SOCKET, float(offset))
    if material is not None:
        _ensure_material_slot(obj, material)
        _set_input(mod, _MATERIAL_SOCKET, material)
    if settings is not None:
        _sync_settings(mod, settings)
    if enabled is not None:
        set_visibility(obj, enabled)
    return mod


def remove(obj: bpy.types.Object) -> bool:
    mod = _owned_modifier(obj)
    if mod is None:
        return False
    obj.modifiers.remove(mod)
    return True


def set_visibility(obj: bpy.types.Object, visible: bool) -> bool:
    mod = _owned_modifier(obj)
    if mod is None:
        return False
    value = bool(visible)
    mod.show_viewport = value
    mod.show_render = value
    return True


def local_thickness(obj: bpy.types.Object) -> float | None:
    mod = _owned_modifier(obj)
    if mod is None:
        return None
    identifier = _socket_id(mod.node_group, _THICKNESS_SOCKET)
    return float(mod.get(identifier, 0.0)) if identifier is not None else None


def has_active(obj: bpy.types.Object) -> bool:
    mod = _owned_modifier(obj)
    return bool(mod is not None and (mod.show_viewport or mod.show_render))
