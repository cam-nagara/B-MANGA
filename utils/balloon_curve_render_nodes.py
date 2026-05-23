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
GROUP_VERSION = 6
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


def _masked_geometry(
    group,
    geometry_socket,
    mask_geometry_socket,
    use_mask_socket,
    *,
    label: str,
    location: tuple[float, float],
):
    boolean = _node(group, "GeometryNodeMeshBoolean", label=f"{label}を切り抜き", location=location)
    mesh_a = boolean.inputs[0] if len(boolean.inputs) > 0 else None
    mesh_b = boolean.inputs[1] if len(boolean.inputs) > 1 else None
    # 5.1 の Mesh Boolean は平面メッシュと厚み付きマスクの INTERSECT で
    # Mesh 2 側の面を優先して残すため、表示したいフキダシ形状を Mesh 2 に
    # 固定する。逆順だとコマ全体が表示結果へ混ざる。
    _link(group, mask_geometry_socket, mesh_a)
    _link(group, geometry_socket, mesh_b)
    try:
        boolean.operation = "INTERSECT"
    except Exception:  # noqa: BLE001
        pass

    switch = _node(
        group,
        "GeometryNodeSwitch",
        label=f"{label}のマスク使用",
        location=(location[0] + 220, location[1] + 40),
    )
    switch.input_type = "GEOMETRY"
    _link(group, use_mask_socket, switch.inputs["Switch"])
    _link(group, geometry_socket, switch.inputs["False"])
    _link(group, boolean.outputs["Mesh"], switch.inputs["True"])
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

    fill_curve = _node(group, "GeometryNodeFillCurve", label="塗り面", location=(-500, 180))
    _link(group, input_node.outputs["Geometry"], fill_curve.inputs["Curve"])
    mask_info = _node(group, "GeometryNodeObjectInfo", label="マスク対象", location=(-500, -360))
    try:
        mask_info.transform_space = "RELATIVE"
    except Exception:  # noqa: BLE001
        pass
    _set_default(mask_info.inputs["As Instance"], False)
    _link(group, input_node.outputs["マスク対象"], mask_info.inputs["Object"])

    fill_masked = _masked_geometry(
        group,
        fill_curve.outputs["Mesh"],
        mask_info.outputs["Geometry"],
        input_node.outputs["マスク使用"],
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

    radius = _node(group, "ShaderNodeMath", label="線幅を半径へ", location=(-500, -120))
    try:
        radius.operation = "MULTIPLY"
    except Exception:  # noqa: BLE001
        pass
    _link(group, input_node.outputs["線幅 (mm)"], radius.inputs[0])
    _set_default(radius.inputs[1], 0.0005)

    profile = _node(group, "GeometryNodeCurvePrimitiveCircle", label="線幅断面", location=(-250, -120))
    _set_default(profile.inputs["Resolution"], 8)
    _link(group, radius.outputs["Value"], profile.inputs["Radius"])

    outline_mesh = _node(group, "GeometryNodeCurveToMesh", label="輪郭線", location=(0, -80))
    _link(group, input_node.outputs["Geometry"], outline_mesh.inputs["Curve"])
    _link(group, profile.outputs["Curve"], outline_mesh.inputs["Profile Curve"])
    fill_caps = _socket_by_name(outline_mesh.inputs, "Fill Caps")
    if fill_caps is not None:
        _set_default(fill_caps, True)
    outline_masked = _masked_geometry(
        group,
        outline_mesh.outputs["Mesh"],
        mask_info.outputs["Geometry"],
        input_node.outputs["マスク使用"],
        label="線",
        location=(250, -80),
    )
    outline_geometry = _set_material(
        group,
        outline_masked,
        input_node.outputs["線素材"],
        label="線素材",
        location=(690, -40),
    )

    joined = _node(group, "GeometryNodeJoinGeometry", label="塗りと線", location=(850, 120))
    _link(group, fill_geometry, joined.inputs["Geometry"])
    _link(group, outline_geometry, joined.inputs["Geometry"])
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


def _set_modifier_values(obj: bpy.types.Object, modifier, *, line_width_mm: float, mask_object=_MASK_UNSET) -> None:
    curve = getattr(obj, "data", None)
    if curve is not None and getattr(obj, "type", "") == "CURVE":
        try:
            curve.bevel_depth = 0.0
            curve.bevel_resolution = 0
            curve.fill_mode = "NONE"
            curve.use_fill_caps = False
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


def ensure_modifier(obj: bpy.types.Object | None, *, line_width_mm: float = 0.3, mask_object=_MASK_UNSET):
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
    _set_modifier_values(obj, modifier, line_width_mm=line_width_mm, mask_object=mask_object)
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
    _set_modifier_values(obj, modifier, line_width_mm=current_width, mask_object=mask_object)


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
