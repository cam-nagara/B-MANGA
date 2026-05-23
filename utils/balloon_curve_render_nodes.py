"""フキダシカーブ用の軽量 Geometry Nodes 補助."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bpy

from . import log

_logger = log.get_logger(__name__)

MODIFIER_NAME = "B-Name Geometry Nodes"
GROUP_NAME = "BName_GN_BalloonCurveRender"
PROP_GN_KIND = "bname_geometry_nodes_kind"
PROP_GROUP_VERSION = "bname_geometry_nodes_version"
KIND = "balloon_curve"
GROUP_VERSION = 17
_MASK_UNSET = object()


@dataclass(frozen=True)
class _SocketSpec:
    name: str
    socket_type: str
    default: Any = 0.0
    hide_in_modifier: bool = False


_SOCKETS = (
    _SocketSpec("線幅 (mm)", "NodeSocketFloat", 0.3),
    _SocketSpec("線素材", "NodeSocketMaterial", None, True),
    _SocketSpec("塗り素材", "NodeSocketMaterial", None, True),
    _SocketSpec("マスク使用", "NodeSocketBool", False, True),
    _SocketSpec("マスク対象", "NodeSocketObject", None, True),
    _SocketSpec("塗り切り抜き必要", "NodeSocketBool", False, True),
    _SocketSpec("切り抜き必要", "NodeSocketBool", False, True),
)


def _interface_socket(group, name: str, in_out: str):
    for item in group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "name", "") == name and getattr(item, "in_out", "") == in_out:
            return item
    return None


def _ensure_socket(group, spec: _SocketSpec, *, in_out: str = "INPUT"):
    item = _interface_socket(group, spec.name, in_out)
    if item is None:
        item = group.interface.new_socket(name=spec.name, in_out=in_out, socket_type=spec.socket_type)
    try:
        if hasattr(item, "hide_in_modifier"):
            item.hide_in_modifier = bool(spec.hide_in_modifier)
        if spec.socket_type == "NodeSocketFloat":
            item.default_value = float(spec.default or 0.0)
        elif spec.socket_type == "NodeSocketBool":
            item.default_value = bool(spec.default)
    except Exception:  # noqa: BLE001
        pass
    return item


def _prune_sockets(group) -> None:
    allowed_inputs = {"Geometry"} | {spec.name for spec in _SOCKETS}
    allowed_outputs = {"Geometry"}
    for item in list(group.interface.items_tree):
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        in_out = str(getattr(item, "in_out", "") or "")
        name = str(getattr(item, "name", "") or "")
        if in_out == "INPUT" and name in allowed_inputs:
            continue
        if in_out == "OUTPUT" and name in allowed_outputs:
            continue
        try:
            group.interface.remove(item)
        except Exception:  # noqa: BLE001
            pass


def _clear_nodes(group) -> None:
    for node in list(group.nodes):
        group.nodes.remove(node)


def _node(group, node_type: str, *, label: str = "", location: tuple[float, float] = (0.0, 0.0)):
    node = group.nodes.new(node_type)
    if label:
        node.label = label
    node.location = location
    return node


def _link(group, output_socket, input_socket) -> None:
    try:
        group.links.new(output_socket, input_socket)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve render nodes: node link failed")


def _set_default(socket, value) -> None:
    try:
        socket.default_value = value
    except Exception:  # noqa: BLE001
        pass


def _set_material(group, geometry_socket, material_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeSetMaterial", label=label, location=location)
    _link(group, geometry_socket, node.inputs["Geometry"])
    _link(group, material_socket, node.inputs["Material"])
    return node.outputs["Geometry"]


def _boolean_and(group, left_socket, right_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "FunctionNodeBooleanMath", label=label, location=location)
    try:
        node.operation = "AND"
    except Exception:  # noqa: BLE001
        pass
    _link(group, left_socket, node.inputs[0])
    _link(group, right_socket, node.inputs[1])
    return node.outputs[0]


def _boolean_not(group, value_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "FunctionNodeBooleanMath", label=label, location=location)
    try:
        node.operation = "NOT"
    except Exception:  # noqa: BLE001
        pass
    _link(group, value_socket, node.inputs[0])
    return node.outputs[0]


def _vector_add_constant(
    group,
    vector_socket,
    value: tuple[float, float, float],
    *,
    label: str,
    location: tuple[float, float],
):
    node = _node(group, "ShaderNodeVectorMath", label=label, location=location)
    try:
        node.operation = "ADD"
    except Exception:  # noqa: BLE001
        pass
    _link(group, vector_socket, node.inputs[0])
    _set_default(node.inputs[1], value)
    return node.outputs["Vector"]


def _offset_geometry_z(group, geometry_socket, z_value: float, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeTransform", label=label, location=location)
    _link(group, geometry_socket, node.inputs["Geometry"])
    translation = _socket_by_name(node.inputs, "Translation")
    if translation is not None:
        _set_default(translation, (0.0, 0.0, float(z_value)))
    return node.outputs["Geometry"]


def _set_curve_radius(group, curve_socket, radius: float, *, label: str, location: tuple[float, float]):
    try:
        node = _node(group, "GeometryNodeSetCurveRadius", label=label, location=location)
    except Exception:  # noqa: BLE001
        return curve_socket
    curve_input = _socket_by_name(node.inputs, "Curve", "Geometry")
    if curve_input is not None:
        _link(group, curve_socket, curve_input)
    selection = _socket_by_name(node.inputs, "Selection")
    if selection is not None:
        _set_default(selection, True)
    radius_input = _socket_by_name(node.inputs, "Radius")
    if radius_input is not None:
        _set_default(radius_input, float(radius))
    return _socket_by_name(node.outputs, "Curve", "Geometry") or curve_socket


def _point_radius_scale(group, *, location: tuple[float, float]):
    node = _node(group, "GeometryNodeInputNamedAttribute", label="制御点ごとの線幅倍率", location=location)
    try:
        node.data_type = "FLOAT"
    except Exception:  # noqa: BLE001
        pass
    _set_default(node.inputs["Name"], "radius")
    return node.outputs["Attribute"]


def _masked_geometry(
    group,
    geometry_socket,
    mask_geometry_socket,
    use_mask_socket,
    *,
    label: str,
    location: tuple[float, float],
):
    position = _node(
        group,
        "GeometryNodeInputPosition",
        label=f"{label}位置",
        location=(location[0] - 460, location[1] + 80),
    )
    source_position = _vector_add_constant(
        group,
        position.outputs["Position"],
        (0.0, 0.0, 1.0),
        label=f"{label}判定開始位置",
        location=(location[0] - 250, location[1] + 80),
    )
    raycast = _node(group, "GeometryNodeRaycast", label=f"{label}をコマ内だけ残す", location=location)
    _link(group, mask_geometry_socket, raycast.inputs["Target Geometry"])
    _link(group, source_position, raycast.inputs["Source Position"])
    _set_default(raycast.inputs["Ray Direction"], (0.0, 0.0, -1.0))
    _set_default(raycast.inputs["Ray Length"], 2.0)
    outside = _boolean_not(
        group,
        raycast.outputs["Is Hit"],
        label=f"{label}のコマ外判定",
        location=(location[0] + 220, location[1] - 120),
    )
    delete = _node(
        group,
        "GeometryNodeDeleteGeometry",
        label=f"{label}のコマ外を消す",
        location=(location[0] + 220, location[1] + 40),
    )
    try:
        delete.domain = "FACE"
        delete.mode = "ALL"
    except Exception:  # noqa: BLE001
        pass
    _link(group, geometry_socket, delete.inputs["Geometry"])
    _link(group, outside, delete.inputs["Selection"])

    switch = _node(
        group,
        "GeometryNodeSwitch",
        label=f"{label}のマスク使用",
        location=(location[0] + 480, location[1] + 40),
    )
    switch.input_type = "GEOMETRY"
    _link(group, use_mask_socket, switch.inputs["Switch"])
    _link(group, geometry_socket, switch.inputs["False"])
    _link(group, delete.outputs["Geometry"], switch.inputs["True"])
    return switch.outputs["Output"]


def _socket_by_name(sockets, *names: str):
    for name in names:
        try:
            return sockets[name]
        except Exception:  # noqa: BLE001
            continue
    for socket in sockets:
        if getattr(socket, "name", "") in names:
            return socket
    return None


def _build_nodes(group) -> None:
    _clear_nodes(group)
    input_node = _node(group, "NodeGroupInput", label="フキダシカーブ", location=(-760, 0))
    output_node = _node(group, "NodeGroupOutput", label="表示結果", location=(980, 0))

    fill_source = _set_curve_radius(
        group,
        input_node.outputs["Geometry"],
        0.0,
        label="塗り用の線幅を消す",
        location=(-700, 180),
    )
    fill_curve = _node(group, "GeometryNodeFillCurve", label="塗り面", location=(-500, 180))
    _link(group, fill_source, fill_curve.inputs["Curve"])
    mask_info = _node(group, "GeometryNodeObjectInfo", label="マスク対象", location=(-500, -360))
    try:
        mask_info.transform_space = "RELATIVE"
    except Exception:  # noqa: BLE001
        pass
    _set_default(mask_info.inputs["As Instance"], False)
    _link(group, input_node.outputs["マスク対象"], mask_info.inputs["Object"])
    fill_clip_enabled = _boolean_and(
        group,
        input_node.outputs["マスク使用"],
        input_node.outputs["塗り切り抜き必要"],
        label="塗り切り抜きの有効判定",
        location=(-730, -420),
    )
    line_clip_enabled = _boolean_and(
        group,
        input_node.outputs["マスク使用"],
        input_node.outputs["切り抜き必要"],
        label="線切り抜きの有効判定",
        location=(-730, -520),
    )
    mask_geometry = mask_info.outputs["Geometry"]

    fill_masked = _masked_geometry(
        group,
        fill_curve.outputs["Mesh"],
        mask_geometry,
        fill_clip_enabled,
        label="塗り",
        location=(-250, 180),
    )
    fill_geometry = _set_material(
        group,
        fill_masked,
        input_node.outputs["塗り素材"],
        label="塗り素材",
        location=(190, 220),
    )
    fill_geometry = _offset_geometry_z(
        group,
        fill_geometry,
        0.00001,
        label="塗りを背面へ",
        location=(470, 220),
    )

    radius = _node(group, "ShaderNodeMath", label="線幅を半径へ", location=(-500, -260))
    try:
        radius.operation = "MULTIPLY"
    except Exception:  # noqa: BLE001
        pass
    _link(group, input_node.outputs["線幅 (mm)"], radius.inputs[0])
    _set_default(radius.inputs[1], 0.0005)

    profile = _node(group, "GeometryNodeCurvePrimitiveCircle", label="線幅断面", location=(-250, -260))
    _set_default(profile.inputs["Resolution"], 8)
    _link(group, radius.outputs["Value"], profile.inputs["Radius"])
    point_radius = _point_radius_scale(
        group,
        location=(-250, -440),
    )

    outline_mesh = _node(group, "GeometryNodeCurveToMesh", label="輪郭線", location=(0, -260))
    _link(group, input_node.outputs["Geometry"], outline_mesh.inputs["Curve"])
    _link(group, profile.outputs["Curve"], outline_mesh.inputs["Profile Curve"])
    scale_input = _socket_by_name(outline_mesh.inputs, "Scale")
    if scale_input is not None:
        _link(group, point_radius, scale_input)
    fill_caps = _socket_by_name(outline_mesh.inputs, "Fill Caps")
    if fill_caps is not None:
        _set_default(fill_caps, True)
    outline_geometry = _set_material(
        group,
        outline_mesh.outputs["Mesh"],
        input_node.outputs["線素材"],
        label="線素材",
        location=(250, -80),
    )
    outline_geometry = _offset_geometry_z(
        group,
        outline_geometry,
        0.00004,
        label="線を前面へ",
        location=(470, -80),
    )
    outline_masked = _masked_geometry(
        group,
        outline_mesh.outputs["Mesh"],
        mask_geometry,
        line_clip_enabled,
        label="線",
        location=(250, -260),
    )
    clipped_line = _set_material(
        group,
        outline_masked,
        input_node.outputs["線素材"],
        label="切り抜き線素材",
        location=(690, -260),
    )
    clipped_line = _offset_geometry_z(
        group,
        clipped_line,
        0.00004,
        label="切り抜き線を前面へ",
        location=(690, -440),
    )
    line_switch = _node(group, "GeometryNodeSwitch", label="線の切り抜き切替", location=(850, -160))
    line_switch.input_type = "GEOMETRY"
    _link(group, line_clip_enabled, line_switch.inputs["Switch"])
    _link(group, outline_geometry, line_switch.inputs["False"])
    _link(group, clipped_line, line_switch.inputs["True"])

    joined = _node(group, "GeometryNodeJoinGeometry", label="塗りと線", location=(880, 120))
    _link(group, fill_geometry, joined.inputs["Geometry"])
    _link(group, line_switch.outputs["Output"], joined.inputs["Geometry"])
    _link(group, joined.outputs["Geometry"], output_node.inputs["Geometry"])
    group[PROP_GROUP_VERSION] = GROUP_VERSION


def ensure_node_group() -> bpy.types.NodeTree:
    group = bpy.data.node_groups.get(GROUP_NAME)
    if group is None:
        group = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    group.use_fake_user = True
    if _interface_socket(group, "Geometry", "INPUT") is None:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    if _interface_socket(group, "Geometry", "OUTPUT") is None:
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for spec in _SOCKETS:
        _ensure_socket(group, spec)
    _prune_sockets(group)
    if int(group.get(PROP_GROUP_VERSION, 0) or 0) != GROUP_VERSION:
        _build_nodes(group)
    elif not any(node.bl_idname == "NodeGroupInput" for node in group.nodes):
        _build_nodes(group)
    elif not any(node.bl_idname == "NodeGroupOutput" for node in group.nodes):
        _build_nodes(group)
    return group


def _socket_identifiers(group) -> dict[str, tuple[str, _SocketSpec]]:
    specs = {spec.name: spec for spec in _SOCKETS}
    out: dict[str, tuple[str, _SocketSpec]] = {}
    for item in group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "in_out", "") != "INPUT":
            continue
        name = str(getattr(item, "name", "") or "")
        spec = specs.get(name)
        if spec is not None:
            out[name] = (str(getattr(item, "identifier", "") or ""), spec)
    return out


def _set_modifier_value(modifier, identifier: str, spec: _SocketSpec, value: Any) -> None:
    if not identifier:
        return
    try:
        if spec.socket_type in {"NodeSocketMaterial", "NodeSocketObject"}:
            modifier[identifier] = value
        elif spec.socket_type == "NodeSocketFloat":
            modifier[identifier] = float(value or 0.0)
        elif spec.socket_type == "NodeSocketBool":
            modifier[identifier] = bool(value)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve render nodes: modifier input sync failed")


def _set_modifier_values(
    obj: bpy.types.Object,
    modifier,
    *,
    line_width_mm: float,
    mask_object=_MASK_UNSET,
    clip_needed: bool = False,
    fill_clip_needed: bool = False,
) -> None:
    curve = getattr(obj, "data", None)
    if curve is not None and getattr(obj, "type", "") == "CURVE":
        for attr, value in (
            ("bevel_depth", 0.0),
            ("bevel_resolution", 0),
            ("fill_mode", "FULL"),
            ("use_fill_caps", False),
        ):
            try:
                setattr(curve, attr, value)
            except Exception:  # noqa: BLE001
                pass
    identifiers = _socket_identifiers(modifier.node_group)
    values = {
        "線幅 (mm)": float(line_width_mm or 0.0),
        "線素材": _material_at(obj, 0),
        "塗り素材": _material_at(obj, 1),
    }
    if mask_object is not _MASK_UNSET:
        values["マスク使用"] = mask_object is not None
        values["マスク対象"] = mask_object
        values["塗り切り抜き必要"] = bool(mask_object is not None and fill_clip_needed)
        values["切り抜き必要"] = bool(mask_object is not None and clip_needed)
    else:
        mask_use_socket = identifiers.get("マスク使用")
        if mask_use_socket:
            mask_use_identifier, _spec = mask_use_socket
            if mask_use_identifier and mask_use_identifier not in modifier:
                values["マスク使用"] = False
                values["マスク対象"] = None
    for name, value in values.items():
        socket = identifiers.get(name)
        if not socket:
            continue
        ident, spec = socket
        _set_modifier_value(modifier, ident, spec, value)


def ensure_modifier(
    obj: bpy.types.Object | None,
    *,
    line_width_mm: float = 0.3,
    mask_object=_MASK_UNSET,
    clip_needed: bool = False,
    fill_clip_needed: bool = False,
):
    if obj is None:
        return None
    group = ensure_node_group()
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None or getattr(modifier, "type", "") != "NODES":
        modifier = obj.modifiers.new(MODIFIER_NAME, "NODES")
    try:
        modifier.node_group = group
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve render nodes: assign node group failed")
        return modifier
    obj[PROP_GN_KIND] = KIND
    _set_modifier_values(
        obj,
        modifier,
        line_width_mm=line_width_mm,
        mask_object=mask_object,
        clip_needed=clip_needed,
        fill_clip_needed=fill_clip_needed,
    )
    try:
        obj.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return modifier


def set_mask_object(obj: bpy.types.Object | None, mask_object) -> None:
    if obj is None:
        return
    modifier = ensure_modifier(obj)
    if modifier is None or modifier.node_group is None:
        return
    current_width = 0.3
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET" or getattr(item, "in_out", "") != "INPUT":
            continue
        if getattr(item, "name", "") != "線幅 (mm)":
            continue
        try:
            current_width = float(modifier.get(item.identifier, current_width))
        except Exception:  # noqa: BLE001
            pass
        break
    _set_modifier_values(
        obj,
        modifier,
        line_width_mm=current_width,
        mask_object=mask_object,
        clip_needed=mask_object is not None,
        fill_clip_needed=mask_object is not None,
    )


def _material_at(obj: bpy.types.Object, index: int):
    materials = getattr(getattr(obj, "data", None), "materials", None)
    if materials is None:
        return None
    try:
        if len(materials) > index:
            return materials[index]
    except Exception:  # noqa: BLE001
        return None
    return None
