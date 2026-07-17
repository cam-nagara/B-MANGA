"""Camera silhouette curves that leave the source mesh component untouched."""

from __future__ import annotations

import math
from collections.abc import Mapping

import bpy

from . import curve_smoothing_nodes, intersection_shell_node_helpers
from .gn_socket_compat import compare_operand_socket, get_gn_modifier_input, set_gn_modifier_input


MODIFIER_NAME = "BML_OutlineLocalSubdivision"
TREE_NAME = "BML_OutlineLocalSubdivision"
GENERATED_LINE_ATTR = "BML_GeneratedLine"
LINE_WIDTH_ATTR = "BML_LineWidth"

_OWNER_KEY = "bml_outline_local_subdivision_owner"
_TREE_GENERATION = "BML_OutlineLocalSubdivision_Generation_20260710_V5"
_GENERATION_LABEL = "BML Local Subdivision 2026-07-10 V5"
_SURFACE_NORMAL_ATTR = _TREE_GENERATION + "_surface_normal"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_MATERIAL_SOCKET = "マテリアル"
_CAMERA_SOCKET = "カメラ"
_MIDPOINT_FACTOR_SOCKET = "中間頂点の線幅調整"
_MIDPOINT_JITTER_SOCKET = "中間頂点の乱れ (%)"
_MIDPOINT_ANGLE_SOCKET = "検出角度"
_CURVE_25_SOCKET = "変化グラフ 25%"
_CURVE_50_SOCKET = "変化グラフ 50%"
_CURVE_75_SOCKET = "変化グラフ 75%"
_SUBDIVISION_SOCKET = "ライン細分化"
_HIDE_THROUGH_SOCKET = "透明面の奥で非表示"


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
    tree.interface.new_socket(
        name=_CAMERA_SOCKET, in_out="INPUT", socket_type="NodeSocketObject"
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
    hide_through = tree.interface.new_socket(
        name=_HIDE_THROUGH_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketBool",
    )
    hide_through.default_value = False


def _math(nodes, operation: str, location, value: float | None = None):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.location = location
    if value is not None:
        node.inputs[1].default_value = value
    return node


def _switch_float(nodes, links, condition, false_value, true_value, location):
    node = nodes.new("GeometryNodeSwitch")
    node.input_type = "FLOAT"
    node.location = location
    links.new(condition, node.inputs["Switch"])
    links.new(false_value, node.inputs["False"])
    links.new(true_value, node.inputs["True"])
    return node.outputs["Output"]


def _camera_view_vector(nodes, links, position, camera):
    camera_object = nodes.new("GeometryNodeObjectInfo")
    camera_object.transform_space = "RELATIVE"
    camera_object.location = (-1380, -560)
    links.new(camera, camera_object.inputs["Object"])

    perspective = nodes.new("ShaderNodeVectorMath")
    perspective.operation = "SUBTRACT"
    perspective.location = (-1160, -520)
    links.new(camera_object.outputs["Location"], perspective.inputs[0])
    links.new(position, perspective.inputs[1])

    ortho = nodes.new("ShaderNodeVectorRotate")
    ortho.rotation_type = "EULER_XYZ"
    ortho.location = (-1160, -700)
    ortho.inputs["Vector"].default_value = (0.0, 0.0, 1.0)
    links.new(camera_object.outputs["Rotation"], ortho.inputs["Rotation"])
    camera_info = nodes.new("GeometryNodeCameraInfo")
    camera_info.location = (-1380, -760)
    links.new(camera, camera_info.inputs["Camera"])

    projection = nodes.new("GeometryNodeSwitch")
    projection.input_type = "VECTOR"
    projection.location = (-940, -600)
    links.new(camera_info.outputs["Is Orthographic"], projection.inputs["Switch"])
    links.new(perspective.outputs["Vector"], projection.inputs["False"])
    links.new(ortho.outputs["Vector"], projection.inputs["True"])
    return projection.outputs["Output"]


def _camera_facing(nodes, links, normal, position, camera):
    view_vector = _camera_view_vector(nodes, links, position, camera)
    facing = nodes.new("ShaderNodeVectorMath")
    facing.operation = "DOT_PRODUCT"
    facing.location = (-720, -360)
    links.new(normal, facing.inputs[0])
    links.new(view_vector, facing.inputs[1])
    front = nodes.new("FunctionNodeCompare")
    front.data_type = "FLOAT"
    front.operation = "GREATER_THAN"
    front.location = (-520, -360)
    front.inputs[1].default_value = 0.0
    links.new(facing.outputs["Value"], front.inputs[0])
    return front.outputs["Result"]


def _store_surface_normals(nodes, links, geometry, normal):
    stored = nodes.new("GeometryNodeStoreNamedAttribute")
    stored.label = _GENERATION_LABEL + " Surface Normal"
    stored.data_type = "FLOAT_VECTOR"
    stored.domain = "POINT"
    stored.location = (-1380, 100)
    stored.inputs["Name"].default_value = _SURFACE_NORMAL_ATTR
    links.new(geometry, stored.inputs["Geometry"])
    links.new(normal, stored.inputs["Value"])
    return stored.outputs["Geometry"]


def _silhouette_curve(nodes, links, geometry, camera):
    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-1580, -220)
    geometry = _store_surface_normals(
        nodes, links, geometry, normal.outputs["Normal"]
    )

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (-1580, -420)
    front = _camera_facing(
        nodes,
        links,
        normal.outputs["True Normal"],
        position.outputs["Position"],
        camera,
    )
    back = nodes.new("FunctionNodeBooleanMath")
    back.operation = "NOT"
    back.location = (-320, -360)
    links.new(front, back.inputs[0])

    visible_faces = nodes.new("GeometryNodeDeleteGeometry")
    visible_faces.label = _GENERATION_LABEL + " Camera Facing"
    visible_faces.domain = "FACE"
    visible_faces.location = (-120, 100)
    links.new(geometry, visible_faces.inputs["Geometry"])
    links.new(back.outputs["Boolean"], visible_faces.inputs["Selection"])
    neighbors = nodes.new("GeometryNodeInputMeshEdgeNeighbors")
    neighbors.location = (80, -240)
    boundary = nodes.new("FunctionNodeCompare")
    boundary.data_type = "INT"
    boundary.operation = "EQUAL"
    boundary.location = (280, -240)
    compare_operand_socket(boundary, "B").default_value = 1
    links.new(neighbors.outputs["Face Count"], compare_operand_socket(boundary, "A"))
    to_curve = nodes.new("GeometryNodeMeshToCurve")
    to_curve.label = _GENERATION_LABEL + " Silhouette"
    to_curve.location = (280, 100)
    links.new(visible_faces.outputs["Geometry"], to_curve.inputs["Mesh"])
    links.new(boundary.outputs["Result"], to_curve.inputs["Selection"])
    return to_curve.outputs["Curve"]


def _curve_width_scale(nodes, links, curve, group_in):
    midpoint = intersection_shell_node_helpers.add_curve_midpoint_width_scale(
        nodes,
        links,
        curve,
        group_in.outputs[_MIDPOINT_ANGLE_SOCKET],
        group_in.outputs[_MIDPOINT_FACTOR_SOCKET],
        (2480, -760),
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
    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.data_type = "FLOAT"
    width_attr.location = (2480, -420)
    width_attr.inputs["Name"].default_value = LINE_WIDTH_ATTR
    one = nodes.new("ShaderNodeValue")
    one.location = (2480, -300)
    one.outputs[0].default_value = 1.0
    width = _switch_float(
        nodes,
        links,
        width_attr.outputs["Exists"],
        one.outputs[0],
        width_attr.outputs["Attribute"],
        (2680, -380),
    )
    combined = _math(nodes, "MULTIPLY", (2880, -460))
    links.new(midpoint, combined.inputs[0])
    links.new(width, combined.inputs[1])
    safe = _math(nodes, "MAXIMUM", (3080, -460), 0.0)
    links.new(combined.outputs[0], safe.inputs[0])
    return safe.outputs[0]


def _generated_curve(nodes, links, source_geometry, group_in):
    silhouette = _silhouette_curve(
        nodes, links, source_geometry, group_in.outputs[_CAMERA_SOCKET]
    )
    smooth_curve = curve_smoothing_nodes.add_corner_preserving_bezier(
        nodes,
        links,
        silhouette,
        group_in.outputs[_SUBDIVISION_SOCKET],
        (500, 100),
        label=_GENERATION_LABEL + " Shape",
    )
    width_points = nodes.new("GeometryNodeSubdivideCurve")
    width_points.label = _GENERATION_LABEL + " Width Points"
    width_points.location = (2700, 100)
    width_points.inputs["Cuts"].default_value = 3
    links.new(smooth_curve, width_points.inputs["Curve"])
    width_curve = nodes.new("GeometryNodeSwitch")
    width_curve.input_type = "GEOMETRY"
    width_curve.location = (2880, 100)
    links.new(group_in.outputs[_SUBDIVISION_SOCKET], width_curve.inputs["Switch"])
    links.new(smooth_curve, width_curve.inputs["False"])
    links.new(width_points.outputs["Curve"], width_curve.inputs["True"])
    generated_curve = width_curve.outputs["Output"]
    width_scale = _curve_width_scale(nodes, links, generated_curve, group_in)

    half_thickness = _math(nodes, "MULTIPLY", (2900, -120), 0.5)
    links.new(group_in.outputs[_THICKNESS_SOCKET], half_thickness.inputs[0])
    return generated_curve, width_scale, half_thickness.outputs[0]


def _camera_offset_vector(nodes, links, width_scale, half_thickness, group_in):
    offset_distance = _math(nodes, "MULTIPLY", (3100, -120))
    links.new(half_thickness, offset_distance.inputs[0])
    links.new(group_in.outputs[_OFFSET_SOCKET], offset_distance.inputs[1])
    scaled_offset = _math(nodes, "MULTIPLY", (3300, -120))
    links.new(offset_distance.outputs[0], scaled_offset.inputs[0])
    links.new(width_scale, scaled_offset.inputs[1])

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (3100, -360)
    view_vector = _camera_view_vector(
        nodes,
        links,
        position.outputs["Position"],
        group_in.outputs[_CAMERA_SOCKET],
    )
    view_normalized = nodes.new("ShaderNodeVectorMath")
    view_normalized.operation = "NORMALIZE"
    view_normalized.location = (3300, -300)
    links.new(view_vector, view_normalized.inputs[0])
    offset_vector = nodes.new("ShaderNodeVectorMath")
    offset_vector.operation = "SCALE"
    offset_vector.location = (3500, -120)
    links.new(view_normalized.outputs["Vector"], offset_vector.inputs[0])
    links.new(scaled_offset.outputs[0], offset_vector.inputs["Scale"])
    return offset_vector.outputs["Vector"]


def _transparent_hide_vector(nodes, links, width_scale, half_thickness, group_in):
    hide_inset = nodes.new("GeometryNodeSwitch")
    hide_inset.input_type = "FLOAT"
    hide_inset.location = (2900, -520)
    hide_inset.inputs["False"].default_value = 0.0
    hide_inset.inputs["True"].default_value = -2.0
    links.new(group_in.outputs[_HIDE_THROUGH_SOCKET], hide_inset.inputs["Switch"])
    hide_distance = _math(nodes, "MULTIPLY", (3100, -500))
    links.new(half_thickness, hide_distance.inputs[0])
    links.new(hide_inset.outputs["Output"], hide_distance.inputs[1])
    scaled_hide = _math(nodes, "MULTIPLY", (3300, -500))
    links.new(hide_distance.outputs[0], scaled_hide.inputs[0])
    links.new(width_scale, scaled_hide.inputs[1])
    surface_normal = nodes.new("GeometryNodeInputNamedAttribute")
    surface_normal.data_type = "FLOAT_VECTOR"
    surface_normal.location = (3100, -680)
    surface_normal.inputs["Name"].default_value = _SURFACE_NORMAL_ATTR
    normalized_surface = nodes.new("ShaderNodeVectorMath")
    normalized_surface.operation = "NORMALIZE"
    normalized_surface.location = (3300, -680)
    links.new(surface_normal.outputs["Attribute"], normalized_surface.inputs[0])
    hide_vector = nodes.new("ShaderNodeVectorMath")
    hide_vector.operation = "SCALE"
    hide_vector.location = (3500, -500)
    links.new(normalized_surface.outputs["Vector"], hide_vector.inputs[0])
    links.new(scaled_hide.outputs[0], hide_vector.inputs["Scale"])
    return hide_vector.outputs["Vector"]


def _offset_curve(nodes, links, curve, width_scale, half_thickness, group_in):
    camera_offset = _camera_offset_vector(
        nodes, links, width_scale, half_thickness, group_in
    )
    hide_offset = _transparent_hide_vector(
        nodes, links, width_scale, half_thickness, group_in
    )
    combined_offset = nodes.new("ShaderNodeVectorMath")
    combined_offset.operation = "ADD"
    combined_offset.location = (3700, -220)
    links.new(camera_offset, combined_offset.inputs[0])
    links.new(hide_offset, combined_offset.inputs[1])
    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (3900, 100)
    links.new(curve, set_position.inputs["Geometry"])
    links.new(combined_offset.outputs["Vector"], set_position.inputs["Offset"])
    return set_position.outputs["Geometry"]


def _generated_line_instance(
    nodes, links, curve, width_scale, half_thickness, material_value
):
    profile = nodes.new("GeometryNodeCurvePrimitiveCircle")
    profile.mode = "RADIUS"
    profile.location = (3300, -520)
    profile.inputs["Resolution"].default_value = 8
    links.new(half_thickness, profile.inputs["Radius"])
    tube = nodes.new("GeometryNodeCurveToMesh")
    tube.label = _GENERATION_LABEL + " Line Tube"
    tube.location = (3700, 100)
    tube.inputs["Fill Caps"].default_value = True
    links.new(curve, tube.inputs["Curve"])
    links.new(profile.outputs["Curve"], tube.inputs["Profile Curve"])
    links.new(width_scale, tube.inputs["Scale"])
    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (3880, 100)
    links.new(tube.outputs["Mesh"], smooth.inputs["Geometry"])
    set_material = nodes.new("GeometryNodeSetMaterial")
    set_material.location = (4060, 100)
    links.new(smooth.outputs["Geometry"], set_material.inputs["Geometry"])
    links.new(material_value, set_material.inputs["Material"])
    generated = nodes.new("GeometryNodeStoreNamedAttribute")
    generated.label = _GENERATION_LABEL + " Generated Line"
    generated.data_type = "BOOLEAN"
    generated.domain = "FACE"
    generated.location = (4240, 100)
    generated.inputs["Name"].default_value = GENERATED_LINE_ATTR
    generated.inputs["Value"].default_value = True
    links.new(set_material.outputs["Geometry"], generated.inputs["Geometry"])

    line_instance = nodes.new("GeometryNodeGeometryToInstance")
    line_instance.label = _GENERATION_LABEL + " Line Only"
    line_instance.location = (4420, -80)
    links.new(generated.outputs["Geometry"], line_instance.inputs["Geometry"])
    return line_instance.outputs["Instances"]


def _create_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(TREE_NAME, "GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1800, 100)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (4860, 100)
    source_geometry = group_in.outputs["Geometry"]
    curve, width_scale, half_thickness = _generated_curve(
        nodes, links, source_geometry, group_in
    )
    offset_curve = _offset_curve(
        nodes, links, curve, width_scale, half_thickness, group_in
    )
    line_instance = _generated_line_instance(
        nodes,
        links,
        offset_curve,
        width_scale,
        half_thickness,
        group_in.outputs[_MATERIAL_SOCKET],
    )

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (4640, 100)
    links.new(line_instance, join.inputs["Geometry"])
    links.new(source_geometry, join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], group_out.inputs["Geometry"])
    return tree


def _tree_is_current(tree: bpy.types.NodeTree) -> bool:
    required = (
        _THICKNESS_SOCKET,
        _OFFSET_SOCKET,
        _MATERIAL_SOCKET,
        _CAMERA_SOCKET,
        _MIDPOINT_FACTOR_SOCKET,
        _MIDPOINT_JITTER_SOCKET,
        _MIDPOINT_ANGLE_SOCKET,
        _CURVE_25_SOCKET,
        _CURVE_50_SOCKET,
        _CURVE_75_SOCKET,
        _SUBDIVISION_SOCKET,
        _HIDE_THROUGH_SOCKET,
    )
    return (
        all(_socket_id(tree, name) is not None for name in required)
        and any(
            node.bl_idname == "GeometryNodeMeshToCurve"
            and node.label == _GENERATION_LABEL + " Silhouette"
            for node in tree.nodes
        )
        and any(
            node.bl_idname == "GeometryNodeGeometryToInstance"
            and node.label == _GENERATION_LABEL + " Line Only"
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
            get_gn_modifier_input(mod, _OWNER_KEY, None) == _TREE_GENERATION
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
        set_gn_modifier_input(mod, identifier, value)


def _ensure_material_slot(obj: bpy.types.Object, material: bpy.types.Material | None) -> None:
    if material is not None and all(slot.material != material for slot in obj.material_slots):
        obj.data.materials.append(material)


def _scene_contains_object(scene, obj: bpy.types.Object) -> bool:
    try:
        return scene is not None and scene.objects.get(obj.name) == obj
    except (AttributeError, ReferenceError):
        return False


def resolve_camera(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None = None,
) -> bpy.types.Object | None:
    scenes: list[bpy.types.Scene] = []
    preferred = scene or getattr(bpy.context, "scene", None)
    if _scene_contains_object(preferred, obj):
        scenes.append(preferred)
    for candidate in getattr(obj, "users_scene", ()):
        if candidate not in scenes:
            scenes.append(candidate)
    for scene in scenes:
        camera = getattr(scene, "bmanga_line_camera", None)
        if camera is not None and getattr(camera, "type", None) == "CAMERA":
            return camera
        camera = getattr(scene, "camera", None)
        if camera is not None and getattr(camera, "type", None) == "CAMERA":
            return camera
    return None


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
        _SUBDIVISION_SOCKET: requested,
        _HIDE_THROUGH_SOCKET: bool(
            _value(settings, ("hide_through_transparent",), False)
        ),
    }
    for name, value in values.items():
        _set_input(mod, name, value)


def ensure(
    obj: bpy.types.Object,
    *,
    local_thickness: float,
    offset: float,
    material: bpy.types.Material | None,
    camera: bpy.types.Object | None = None,
    scene: bpy.types.Scene | None = None,
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
        set_gn_modifier_input(mod, _OWNER_KEY, _TREE_GENERATION)
    mod.node_group = tree
    _ensure_material_slot(obj, material)
    _set_input(mod, _THICKNESS_SOCKET, max(0.0, float(local_thickness)))
    _set_input(mod, _OFFSET_SOCKET, float(offset))
    _set_input(mod, _MATERIAL_SOCKET, material)
    _set_input(mod, _CAMERA_SOCKET, camera or resolve_camera(obj, scene))
    _sync_settings(mod, settings)
    set_visibility(obj, enabled)
    return mod


def sync(
    obj: bpy.types.Object,
    *,
    local_thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
    camera: bpy.types.Object | None = None,
    scene: bpy.types.Scene | None = None,
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
    _set_input(mod, _CAMERA_SOCKET, camera or resolve_camera(obj, scene))
    if settings is not None:
        _sync_settings(mod, settings)
    if enabled is not None:
        set_visibility(obj, enabled)
    return mod


def sync_scene_cameras(scene: bpy.types.Scene | None) -> int:
    """Refresh only camera inputs before rendering; geometry is not rebuilt."""
    if scene is None:
        return 0
    changed = 0
    for obj in scene.objects:
        mod = _owned_modifier(obj)
        if mod is None:
            continue
        camera = resolve_camera(obj, scene)
        identifier = _socket_id(mod.node_group, _CAMERA_SOCKET)
        if identifier is None or get_gn_modifier_input(mod, identifier, None) == camera:
            continue
        set_gn_modifier_input(mod, identifier, camera)
        changed += 1
    return changed


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
    return float(get_gn_modifier_input(mod, identifier, 0.0)) if identifier is not None else None


def has_active(obj: bpy.types.Object) -> bool:
    mod = _owned_modifier(obj)
    return bool(mod is not None and (mod.show_viewport or mod.show_render))
