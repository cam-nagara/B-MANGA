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
GROUP_VERSION = 34
FILL_BLUR_ALPHA_ATTRIBUTE = "bname_fill_blur_alpha"
_MASK_UNSET = object()
_MAX_MULTI_LINE_RINGS = 12
_CURVE_PROFILE_RADIUS_FROM_WIDTH_MM = 0.0007071067811865476
_MULTI_LINE_ROLE_RADIUS_OFFSET = 100.0
_OUTER_EDGE_ROLE_RADIUS = 200.0
_INNER_EDGE_ROLE_RADIUS = 300.0
_CLIPPED_FILL_ROLE_RADIUS = 400.0


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
    _SocketSpec("多重線", "NodeSocketBool", False, True),
    _SocketSpec("多重線本数", "NodeSocketFloat", 3.0, True),
    _SocketSpec("多重線幅 (mm)", "NodeSocketFloat", 0.3, True),
    _SocketSpec("多重線間隔 (mm)", "NodeSocketFloat", 0.4, True),
    _SocketSpec("多重線幅変化 (%)", "NodeSocketFloat", 100.0, True),
    _SocketSpec("多重線方向", "NodeSocketFloat", 0.0, True),
    _SocketSpec("谷の線幅 (mm)", "NodeSocketFloat", 0.3, True),
    _SocketSpec("山の線幅 (mm)", "NodeSocketFloat", 0.3, True),
    _SocketSpec("多重線長さ変化 (%)", "NodeSocketFloat", 100.0, True),
    _SocketSpec("多重線を延ばして交差", "NodeSocketBool", False, True),
    _SocketSpec("外側フチ", "NodeSocketBool", False, True),
    _SocketSpec("外側フチ幅 (mm)", "NodeSocketFloat", 1.0, True),
    _SocketSpec("外側フチ素材", "NodeSocketMaterial", None, True),
    _SocketSpec("内側フチ", "NodeSocketBool", False, True),
    _SocketSpec("内側フチ幅 (mm)", "NodeSocketFloat", 1.0, True),
    _SocketSpec("内側フチ素材", "NodeSocketMaterial", None, True),
    _SocketSpec("塗り輪郭ぼかし", "NodeSocketFloat", 0.0, True),
    _SocketSpec("塗りぼかしをディザ化", "NodeSocketBool", False, True),
    _SocketSpec("マスク使用", "NodeSocketBool", False, True),
    _SocketSpec("マスク対象", "NodeSocketObject", None, True),
    _SocketSpec("塗り切り抜き必要", "NodeSocketBool", False, True),
    _SocketSpec("切り抜き必要", "NodeSocketBool", False, True),
) + tuple(
    item
    for ring_index in range(1, _MAX_MULTI_LINE_RINGS)
    for item in (
        _SocketSpec(f"多重線{ring_index}表示", "NodeSocketBool", False, True),
        _SocketSpec(f"多重線{ring_index}外半径 (mm)", "NodeSocketFloat", 0.0, True),
        _SocketSpec(f"多重線{ring_index}内半径 (mm)", "NodeSocketFloat", 0.0, True),
    )
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


def _boolean_or(group, left_socket, right_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "FunctionNodeBooleanMath", label=label, location=location)
    try:
        node.operation = "OR"
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


def _compare_float_equal(group, value_socket, expected: float, *, label: str, location: tuple[float, float]):
    node = _node(group, "FunctionNodeCompare", label=label, location=location)
    try:
        node.data_type = "FLOAT"
        node.operation = "EQUAL"
    except Exception:  # noqa: BLE001
        pass
    a_socket = _socket_by_name(node.inputs, "A")
    b_socket = _socket_by_name(node.inputs, "B")
    epsilon_socket = _socket_by_name(node.inputs, "Epsilon")
    if a_socket is not None:
        _link(group, value_socket, a_socket)
    if b_socket is not None:
        _set_default(b_socket, float(expected))
    if epsilon_socket is not None:
        _set_default(epsilon_socket, 0.001)
    return _socket_by_name(node.outputs, "Result") or node.outputs[0]


def _compare_float_greater(group, value_socket, expected: float, *, label: str, location: tuple[float, float]):
    node = _node(group, "FunctionNodeCompare", label=label, location=location)
    try:
        node.data_type = "FLOAT"
        node.operation = "GREATER_THAN"
    except Exception:  # noqa: BLE001
        pass
    a_socket = _socket_by_name(node.inputs, "A")
    b_socket = _socket_by_name(node.inputs, "B")
    if a_socket is not None:
        _link(group, value_socket, a_socket)
    if b_socket is not None:
        _set_default(b_socket, float(expected))
    return _socket_by_name(node.outputs, "Result") or node.outputs[0]


def _switch_geometry(group, switch_socket, false_socket, true_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeSwitch", label=label, location=location)
    node.input_type = "GEOMETRY"
    _link(group, switch_socket, node.inputs["Switch"])
    _link(group, false_socket, node.inputs["False"])
    _link(group, true_socket, node.inputs["True"])
    return node.outputs["Output"]


def _separate_geometry(group, geometry_socket, selection_socket, *, label: str, location: tuple[float, float]):
    separate = _node(group, "GeometryNodeSeparateGeometry", label=label, location=location)
    _link(group, geometry_socket, separate.inputs["Geometry"])
    _link(group, selection_socket, separate.inputs["Selection"])
    selected = _socket_by_name(separate.outputs, "Selection")
    inverted = _socket_by_name(separate.outputs, "Inverted")
    return selected or separate.outputs[0], inverted or separate.outputs[1]


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
    node = _node(group, "GeometryNodeSetPosition", label=label, location=location)
    _link(group, geometry_socket, node.inputs["Geometry"])
    offset = _socket_by_name(node.inputs, "Offset")
    if offset is not None:
        _set_default(offset, (0.0, 0.0, float(z_value)))
    return _socket_by_name(node.outputs, "Geometry") or node.outputs[0]


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


def _point_radius_scale(group, *, location: tuple[float, float], subtract_value: float = 0.0):
    node = _node(group, "GeometryNodeInputNamedAttribute", label="制御点ごとの線幅倍率", location=location)
    try:
        node.data_type = "FLOAT"
    except Exception:  # noqa: BLE001
        pass
    _set_default(node.inputs["Name"], "radius")
    attr = node.outputs["Attribute"]
    if abs(float(subtract_value)) <= 1.0e-9:
        return attr
    subtract = _node(group, "ShaderNodeMath", label="制御点線幅の目印を除外", location=(location[0] + 220, location[1]))
    try:
        subtract.operation = "SUBTRACT"
    except Exception:  # noqa: BLE001
        pass
    _link(group, attr, subtract.inputs[0])
    _set_default(subtract.inputs[1], float(subtract_value))
    clamp = _node(group, "ShaderNodeMath", label="制御点線幅下限", location=(location[0] + 440, location[1]))
    try:
        clamp.operation = "MAXIMUM"
    except Exception:  # noqa: BLE001
        pass
    _link(group, subtract.outputs["Value"], clamp.inputs[0])
    _set_default(clamp.inputs[1], 0.0)
    return clamp.outputs["Value"]


def _masked_geometry(
    group,
    geometry_socket,
    mask_geometry_socket,
    use_mask_socket,
    *,
    label: str,
    location: tuple[float, float],
):
    clipped = _clip_geometry_by_mask_hit(
        group,
        geometry_socket,
        mask_geometry_socket,
        keep_inside=True,
        label=label,
        location=(location[0] + 220, location[1] + 40),
    )

    switch = _node(
        group,
        "GeometryNodeSwitch",
        label=f"{label}のマスク使用",
        location=(location[0] + 480, location[1] + 40),
    )
    switch.input_type = "GEOMETRY"
    _link(group, use_mask_socket, switch.inputs["Switch"])
    _link(group, geometry_socket, switch.inputs["False"])
    _link(group, clipped, switch.inputs["True"])
    return switch.outputs["Output"]


def _clip_geometry_by_mask_hit(
    group,
    geometry_socket,
    mask_geometry_socket,
    *,
    keep_inside: bool,
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
    raycast = _node(group, "GeometryNodeRaycast", label=f"{label}の内外判定", location=location)
    _link(group, mask_geometry_socket, raycast.inputs["Target Geometry"])
    _link(group, source_position, raycast.inputs["Source Position"])
    _set_default(raycast.inputs["Ray Direction"], (0.0, 0.0, -1.0))
    _set_default(raycast.inputs["Ray Length"], 2.0)
    if keep_inside:
        selection = _boolean_not(
            group,
            raycast.outputs["Is Hit"],
            label=f"{label}の外側を消す",
            location=(location[0] + 220, location[1] - 120),
        )
    else:
        selection = raycast.outputs["Is Hit"]
    delete = _node(
        group,
        "GeometryNodeDeleteGeometry",
        label=f"{label}を必要な側だけ残す",
        location=(location[0] + 220, location[1] + 40),
    )
    try:
        delete.domain = "FACE"
        delete.mode = "ALL"
    except Exception:  # noqa: BLE001
        pass
    _link(group, geometry_socket, delete.inputs["Geometry"])
    _link(group, selection, delete.inputs["Selection"])
    return delete.outputs["Geometry"]


def _outline_mesh_with_radius(
    group,
    curve_socket,
    radius_socket,
    material_socket,
    mask_geometry,
    clip_enabled,
    *,
    label: str,
    z_value: float,
    location: tuple[float, float],
    use_point_radius: bool = False,
    point_radius_offset: float = 0.0,
):
    profile = _node(group, "GeometryNodeCurvePrimitiveCircle", label=f"{label}断面", location=location)
    _set_default(profile.inputs["Resolution"], 32)
    _link(group, radius_socket, profile.inputs["Radius"])
    mesh = _node(group, "GeometryNodeCurveToMesh", label=label, location=(location[0] + 250, location[1]))
    _link(group, curve_socket, mesh.inputs["Curve"])
    _link(group, profile.outputs["Curve"], mesh.inputs["Profile Curve"])
    scale_input = _socket_by_name(mesh.inputs, "Scale")
    if use_point_radius and scale_input is not None:
        point_radius = _point_radius_scale(group, location=(location[0], location[1] - 180), subtract_value=point_radius_offset)
        _link(group, point_radius, scale_input)
    fill_caps = _socket_by_name(mesh.inputs, "Fill Caps")
    if fill_caps is not None:
        _set_default(fill_caps, True)
    masked = _masked_geometry(
        group,
        mesh.outputs["Mesh"],
        mask_geometry,
        clip_enabled,
        label=label,
        location=(location[0] + 250, location[1] - 220),
    )
    material = _set_material(
        group,
        masked,
        material_socket,
        label=f"{label}素材",
        location=(location[0] + 690, location[1]),
    )
    return _offset_geometry_z(
        group,
        material,
        z_value,
        label=f"{label}を前面へ",
        location=(location[0] + 900, location[1]),
    )


def _edge_radius_socket(group, base_radius, width_mm_socket, *, label: str, location: tuple[float, float]):
    edge_radius = _node(group, "ShaderNodeMath", label=f"{label}幅を半径へ", location=location)
    try:
        edge_radius.operation = "MULTIPLY"
    except Exception:  # noqa: BLE001
        pass
    _link(group, width_mm_socket, edge_radius.inputs[0])
    _set_default(edge_radius.inputs[1], _CURVE_PROFILE_RADIUS_FROM_WIDTH_MM)
    total = _node(group, "ShaderNodeMath", label=f"{label}半径", location=(location[0] + 220, location[1]))
    try:
        total.operation = "ADD"
    except Exception:  # noqa: BLE001
        pass
    _link(group, base_radius, total.inputs[0])
    _link(group, edge_radius.outputs["Value"], total.inputs[1])
    return total.outputs["Value"]


def _radius_from_mm_socket(group, mm_socket, *, label: str, location: tuple[float, float]):
    radius = _node(group, "ShaderNodeMath", label=label, location=location)
    try:
        radius.operation = "MULTIPLY"
    except Exception:  # noqa: BLE001
        pass
    _link(group, mm_socket, radius.inputs[0])
    _set_default(radius.inputs[1], _CURVE_PROFILE_RADIUS_FROM_WIDTH_MM)
    return radius.outputs["Value"]


def _math_node(
    group,
    operation: str,
    left_socket,
    right_value_or_socket,
    *,
    label: str,
    location: tuple[float, float],
):
    node = _node(group, "ShaderNodeMath", label=label, location=location)
    try:
        node.operation = operation
    except Exception:  # noqa: BLE001
        pass
    _link(group, left_socket, node.inputs[0])
    if hasattr(right_value_or_socket, "default_value"):
        _link(group, right_value_or_socket, node.inputs[1])
    else:
        _set_default(node.inputs[1], float(right_value_or_socket))
    return node.outputs["Value"]


def _fill_blur_width_socket(group, line_width_socket, blur_socket, *, location: tuple[float, float]):
    blur_part = _math_node(
        group,
        "MULTIPLY",
        blur_socket,
        3.35,
        label="塗り輪郭ぼかし係数",
        location=location,
    )
    blur_base = _math_node(
        group,
        "ADD",
        blur_part,
        0.65,
        label="塗り輪郭ぼかし基準",
        location=(location[0] + 220, location[1]),
    )
    blur_mm = _math_node(
        group,
        "MULTIPLY",
        line_width_socket,
        blur_base,
        label="塗り輪郭ぼかし幅mm",
        location=(location[0] + 440, location[1]),
    )
    blur_mm_min = _math_node(
        group,
        "MAXIMUM",
        blur_mm,
        0.15,
        label="塗り輪郭ぼかし最小幅",
        location=(location[0] + 660, location[1]),
    )
    return _math_node(
        group,
        "MULTIPLY",
        blur_mm_min,
        0.001,
        label="塗り輪郭ぼかし幅",
        location=(location[0] + 880, location[1]),
    )


def _store_fill_blur_alpha(
    group,
    fill_mesh_socket,
    source_curve_socket,
    line_width_socket,
    blur_socket,
    *,
    location: tuple[float, float],
):
    proximity = _node(group, "GeometryNodeProximity", label="塗り輪郭ぼかし距離", location=location)
    target_socket = _socket_by_name(proximity.inputs, "Target", "Target Geometry", "Geometry")
    if target_socket is not None:
        _link(group, source_curve_socket, target_socket)
    blur_width = _fill_blur_width_socket(
        group,
        line_width_socket,
        blur_socket,
        location=(location[0] - 1040, location[1] - 170),
    )
    alpha = _math_node(
        group,
        "DIVIDE",
        proximity.outputs["Distance"],
        blur_width,
        label="塗り輪郭ぼかし濃度",
        location=(location[0] + 230, location[1]),
    )
    alpha = _math_node(
        group,
        "MINIMUM",
        alpha,
        1.0,
        label="塗り輪郭ぼかし濃度上限",
        location=(location[0] + 450, location[1]),
    )
    alpha = _math_node(
        group,
        "MAXIMUM",
        alpha,
        0.0,
        label="塗り輪郭ぼかし濃度下限",
        location=(location[0] + 670, location[1]),
    )
    blur_enabled = _compare_float_greater(
        group,
        blur_socket,
        0.0001,
        label="塗り輪郭ぼかし有効",
        location=(location[0] + 670, location[1] - 140),
    )
    alpha_switch = _node(
        group,
        "GeometryNodeSwitch",
        label="塗り輪郭ぼかし切り替え",
        location=(location[0] + 900, location[1] - 80),
    )
    try:
        alpha_switch.input_type = "FLOAT"
    except Exception:  # noqa: BLE001
        pass
    _link(group, blur_enabled, alpha_switch.inputs["Switch"])
    _set_default(alpha_switch.inputs["False"], 1.0)
    _link(group, alpha, alpha_switch.inputs["True"])
    store = _node(group, "GeometryNodeStoreNamedAttribute", label="塗り輪郭ぼかしを保持", location=(location[0] + 900, location[1]))
    try:
        store.data_type = "FLOAT"
        store.domain = "POINT"
    except Exception:  # noqa: BLE001
        pass
    _link(group, fill_mesh_socket, store.inputs["Geometry"])
    _set_default(store.inputs["Name"], FILL_BLUR_ALPHA_ATTRIBUTE)
    _link(group, alpha_switch.outputs["Output"], store.inputs["Value"])
    selection = _socket_by_name(store.inputs, "Selection")
    if selection is not None:
        _set_default(selection, True)
    return store.outputs["Geometry"]


def _switch_edge(group, enabled_socket, geometry_socket, *, label: str, location: tuple[float, float]):
    switch = _node(group, "GeometryNodeSwitch", label=f"{label}表示", location=location)
    switch.input_type = "GEOMETRY"
    _link(group, enabled_socket, switch.inputs["Switch"])
    _link(group, geometry_socket, switch.inputs["True"])
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
    role_radius = _point_radius_scale(group, location=(-1320, -40))
    clipped_fill_selection = _compare_float_equal(
        group,
        role_radius,
        _CLIPPED_FILL_ROLE_RADIUS,
        label="見切れ塗りを分離",
        location=(-1340, 220),
    )
    clipped_fill_curve, without_clipped_fill = _separate_geometry(
        group,
        input_node.outputs["Geometry"],
        clipped_fill_selection,
        label="見切れ塗り",
        location=(-1120, 220),
    )
    outer_selection = _compare_float_equal(
        group,
        role_radius,
        _OUTER_EDGE_ROLE_RADIUS,
        label="外側フチを分離",
        location=(-1120, -240),
    )
    outer_curve, without_outer = _separate_geometry(
        group,
        without_clipped_fill,
        outer_selection,
        label="外側フチ",
        location=(-900, -240),
    )
    inner_selection = _compare_float_equal(
        group,
        role_radius,
        _INNER_EDGE_ROLE_RADIUS,
        label="内側フチを分離",
        location=(-1120, -460),
    )
    inner_curve, without_edges = _separate_geometry(
        group,
        without_outer,
        inner_selection,
        label="内側フチ",
        location=(-900, -460),
    )
    multi_by_role = _compare_float_greater(
        group,
        role_radius,
        50.0,
        label="多重線を分離",
        location=(-1120, -20),
    )
    spline_cyclic = _node(group, "GeometryNodeInputSplineCyclic", label="閉じた輪郭", location=(-1140, -140))
    legacy_multi_selection = _boolean_not(
        group,
        _socket_by_name(spline_cyclic.outputs, "Cyclic") or spline_cyclic.outputs[0],
        label="旧多重線を分離",
        location=(-920, -120),
    )
    multi_selection = _boolean_or(
        group,
        multi_by_role,
        legacy_multi_selection,
        label="多重線判定",
        location=(-700, -80),
    )
    multi_curve, body_curve = _separate_geometry(
        group,
        without_edges,
        multi_selection,
        label="通常輪郭と多重線",
        location=(-700, -80),
    )

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

    fill_body_source = _set_curve_radius(
        group,
        body_curve,
        0.0,
        label="塗り用の線幅を消す",
        location=(-700, 180),
    )
    fill_clipped_source = _set_curve_radius(
        group,
        clipped_fill_curve,
        0.0,
        label="見切れ塗り用の線幅を消す",
        location=(-700, 360),
    )
    fill_source = _node(
        group,
        "GeometryNodeSwitch",
        label="見切れ塗りへ切り替え",
        location=(-500, 300),
    )
    fill_source.input_type = "GEOMETRY"
    _link(group, fill_clip_enabled, fill_source.inputs["Switch"])
    _link(group, fill_body_source, fill_source.inputs["False"])
    _link(group, fill_clipped_source, fill_source.inputs["True"])
    fill_curve = _node(group, "GeometryNodeFillCurve", label="塗り面", location=(-500, 180))
    _link(group, fill_source.outputs["Output"], fill_curve.inputs["Curve"])
    fill_mesh = _store_fill_blur_alpha(
        group,
        fill_curve.outputs["Mesh"],
        fill_source.outputs["Output"],
        input_node.outputs["線幅 (mm)"],
        input_node.outputs["塗り輪郭ぼかし"],
        location=(-260, 420),
    )
    fill_geometry = _set_material(
        group,
        fill_mesh,
        input_node.outputs["塗り素材"],
        label="塗り素材",
        location=(190, 220),
    )
    fill_geometry = _offset_geometry_z(
        group,
        fill_geometry,
        0.0,
        label="塗りを背面へ",
        location=(470, 220),
    )

    radius = _node(group, "ShaderNodeMath", label="線幅を半径へ", location=(-500, -260))
    try:
        radius.operation = "MULTIPLY"
    except Exception:  # noqa: BLE001
        pass
    _link(group, input_node.outputs["線幅 (mm)"], radius.inputs[0])
    _set_default(radius.inputs[1], _CURVE_PROFILE_RADIUS_FROM_WIDTH_MM)

    outer_radius = _radius_from_mm_socket(
        group,
        input_node.outputs["外側フチ幅 (mm)"],
        label="外側フチ幅を半径へ",
        location=(-520, -760),
    )
    outer_geometry = _outline_mesh_with_radius(
        group,
        outer_curve,
        outer_radius,
        input_node.outputs["外側フチ素材"],
        mask_geometry,
        line_clip_enabled,
        label="外側フチ",
        z_value=0.010,
        location=(-250, -760),
    )
    outer_geometry = _switch_edge(
        group,
        input_node.outputs["外側フチ"],
        outer_geometry,
        label="外側フチ",
        location=(880, -720),
    )

    inner_radius = _radius_from_mm_socket(
        group,
        input_node.outputs["内側フチ幅 (mm)"],
        label="内側フチ幅を半径へ",
        location=(-520, -1180),
    )
    inner_geometry = _outline_mesh_with_radius(
        group,
        inner_curve,
        inner_radius,
        input_node.outputs["内側フチ素材"],
        mask_geometry,
        line_clip_enabled,
        label="内側フチ",
        z_value=0.012,
        location=(-250, -1180),
    )
    inner_geometry = _switch_edge(
        group,
        input_node.outputs["内側フチ"],
        inner_geometry,
        label="内側フチ",
        location=(880, -1140),
    )

    line_geometry = _outline_mesh_with_radius(
        group,
        body_curve,
        radius.outputs["Value"],
        input_node.outputs["線素材"],
        mask_geometry,
        line_clip_enabled,
        label="輪郭線",
        z_value=0.030,
        location=(-250, -340),
    )
    thorn_multi_geometry = _outline_mesh_with_radius(
        group,
        multi_curve,
        radius.outputs["Value"],
        input_node.outputs["線素材"],
        mask_geometry,
        line_clip_enabled,
        label="多重線",
        z_value=0.020,
        location=(150, -1540),
        use_point_radius=True,
        point_radius_offset=_MULTI_LINE_ROLE_RADIUS_OFFSET,
    )

    joined = _node(group, "GeometryNodeJoinGeometry", label="塗りと線", location=(880, 120))
    _link(group, fill_geometry, joined.inputs["Geometry"])
    _link(group, outer_geometry, joined.inputs["Geometry"])
    _link(group, inner_geometry, joined.inputs["Geometry"])
    _link(group, thorn_multi_geometry, joined.inputs["Geometry"])
    _link(group, line_geometry, joined.inputs["Geometry"])
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
    multi_line_enabled: bool = False,
    multi_line_count: int = 3,
    multi_line_width_mm: float = 0.3,
    multi_line_spacing_mm: float = 0.4,
    multi_line_width_scale_percent: float = 100.0,
    multi_line_direction: str = "outside",
    native_multi_line_rings_enabled: bool = True,
    thorn_multi_line_valley_width_mm: float = 0.3,
    thorn_multi_line_peak_width_mm: float = 0.3,
    thorn_multi_line_length_scale_percent: float = 100.0,
    thorn_multi_line_cross_enabled: bool = False,
    outer_edge_enabled: bool = False,
    outer_edge_width_mm: float = 1.0,
    inner_edge_enabled: bool = False,
    inner_edge_width_mm: float = 1.0,
    fill_blur_amount: float = 0.0,
    fill_blur_dither: bool = False,
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
    direction_code = {"outside": 0.0, "inside": 1.0, "both": 2.0}.get(
        str(multi_line_direction or "outside"),
        0.0,
    )
    values = {
        "線幅 (mm)": float(line_width_mm or 0.0),
        "線素材": _material_at(obj, 0),
        "塗り素材": _material_at(obj, 1),
        "多重線": bool(multi_line_enabled),
        "多重線本数": float(multi_line_count or 1),
        "多重線幅 (mm)": float(multi_line_width_mm or 0.0),
        "多重線間隔 (mm)": float(multi_line_spacing_mm or 0.0),
        "多重線幅変化 (%)": float(multi_line_width_scale_percent or 0.0),
        "多重線方向": direction_code,
        "谷の線幅 (mm)": float(thorn_multi_line_valley_width_mm or 0.0),
        "山の線幅 (mm)": float(thorn_multi_line_peak_width_mm or 0.0),
        "多重線長さ変化 (%)": float(thorn_multi_line_length_scale_percent or 0.0),
        "多重線を延ばして交差": bool(thorn_multi_line_cross_enabled),
        "外側フチ": bool(outer_edge_enabled),
        "外側フチ幅 (mm)": float(outer_edge_width_mm or 0.0),
        "外側フチ素材": _material_at(obj, 2),
        "内側フチ": bool(inner_edge_enabled),
        "内側フチ幅 (mm)": float(inner_edge_width_mm or 0.0),
        "内側フチ素材": _material_at(obj, 3),
        "塗り輪郭ぼかし": max(0.0, min(1.0, float(fill_blur_amount or 0.0))),
        "塗りぼかしをディザ化": bool(fill_blur_dither),
    }
    spacing_mm = max(0.0, float(multi_line_spacing_mm or 0.0))
    width_base_mm = max(0.0, float(multi_line_width_mm or 0.0))
    scale = max(0.0, float(multi_line_width_scale_percent or 0.0)) / 100.0
    extra_count = (
        min(
            _MAX_MULTI_LINE_RINGS - 1,
            max(0, int(multi_line_count or 1) - 1),
        )
        if multi_line_enabled and native_multi_line_rings_enabled
        else 0
    )
    current_inner_mm = max(0.0, float(line_width_mm or 0.0)) * 0.5 + spacing_mm
    for ring_index in range(1, _MAX_MULTI_LINE_RINGS):
        width_mm = width_base_mm * (scale ** max(0, ring_index - 1))
        outer_mm = current_inner_mm + width_mm
        values[f"多重線{ring_index}表示"] = bool(ring_index <= extra_count and width_mm > 0.0)
        values[f"多重線{ring_index}外半径 (mm)"] = outer_mm
        values[f"多重線{ring_index}内半径 (mm)"] = current_inner_mm
        current_inner_mm = outer_mm + spacing_mm
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
    multi_line_enabled: bool = False,
    multi_line_count: int = 3,
    multi_line_width_mm: float = 0.3,
    multi_line_spacing_mm: float = 0.4,
    multi_line_width_scale_percent: float = 100.0,
    multi_line_direction: str = "outside",
    native_multi_line_rings_enabled: bool = True,
    thorn_multi_line_valley_width_mm: float = 0.3,
    thorn_multi_line_peak_width_mm: float = 0.3,
    thorn_multi_line_length_scale_percent: float = 100.0,
    thorn_multi_line_cross_enabled: bool = False,
    outer_edge_enabled: bool = False,
    outer_edge_width_mm: float = 1.0,
    inner_edge_enabled: bool = False,
    inner_edge_width_mm: float = 1.0,
    fill_blur_amount: float = 0.0,
    fill_blur_dither: bool = False,
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
        multi_line_enabled=multi_line_enabled,
        multi_line_count=multi_line_count,
        multi_line_width_mm=multi_line_width_mm,
        multi_line_spacing_mm=multi_line_spacing_mm,
        multi_line_width_scale_percent=multi_line_width_scale_percent,
        multi_line_direction=multi_line_direction,
        native_multi_line_rings_enabled=native_multi_line_rings_enabled,
        thorn_multi_line_valley_width_mm=thorn_multi_line_valley_width_mm,
        thorn_multi_line_peak_width_mm=thorn_multi_line_peak_width_mm,
        thorn_multi_line_length_scale_percent=thorn_multi_line_length_scale_percent,
        thorn_multi_line_cross_enabled=thorn_multi_line_cross_enabled,
        outer_edge_enabled=outer_edge_enabled,
        outer_edge_width_mm=outer_edge_width_mm,
        inner_edge_enabled=inner_edge_enabled,
        inner_edge_width_mm=inner_edge_width_mm,
        fill_blur_amount=fill_blur_amount,
        fill_blur_dither=fill_blur_dither,
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
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None or modifier.node_group is None:
        modifier = ensure_modifier(obj)
    if modifier is None or modifier.node_group is None:
        return
    current_width = 0.3
    current_multi_enabled = False
    current_multi_count = 3
    current_multi_width = 0.3
    current_multi_spacing = 0.4
    current_multi_scale = 100.0
    current_multi_direction = "outside"
    current_thorn_valley_width = 0.3
    current_thorn_peak_width = 0.3
    current_thorn_length_scale = 100.0
    current_thorn_cross_enabled = False
    current_outer_enabled = False
    current_outer_width = 1.0
    current_inner_enabled = False
    current_inner_width = 1.0
    current_fill_blur = 0.0
    current_fill_blur_dither = False
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET" or getattr(item, "in_out", "") != "INPUT":
            continue
        name = getattr(item, "name", "")
        try:
            if name == "線幅 (mm)":
                current_width = float(modifier.get(item.identifier, current_width))
            elif name == "多重線":
                current_multi_enabled = bool(modifier.get(item.identifier, current_multi_enabled))
            elif name == "多重線本数":
                current_multi_count = int(float(modifier.get(item.identifier, current_multi_count)))
            elif name == "多重線幅 (mm)":
                current_multi_width = float(modifier.get(item.identifier, current_multi_width))
            elif name == "多重線間隔 (mm)":
                current_multi_spacing = float(modifier.get(item.identifier, current_multi_spacing))
            elif name == "多重線幅変化 (%)":
                current_multi_scale = float(modifier.get(item.identifier, current_multi_scale))
            elif name == "多重線方向":
                direction_value = int(float(modifier.get(item.identifier, 0.0) or 0.0))
                current_multi_direction = {0: "outside", 1: "inside", 2: "both"}.get(direction_value, "outside")
            elif name == "谷の線幅 (mm)":
                current_thorn_valley_width = float(modifier.get(item.identifier, current_thorn_valley_width))
            elif name == "山の線幅 (mm)":
                current_thorn_peak_width = float(modifier.get(item.identifier, current_thorn_peak_width))
            elif name == "多重線長さ変化 (%)":
                current_thorn_length_scale = float(modifier.get(item.identifier, current_thorn_length_scale))
            elif name == "多重線を延ばして交差":
                current_thorn_cross_enabled = bool(modifier.get(item.identifier, current_thorn_cross_enabled))
            elif name == "外側フチ":
                current_outer_enabled = bool(modifier.get(item.identifier, current_outer_enabled))
            elif name == "外側フチ幅 (mm)":
                current_outer_width = float(modifier.get(item.identifier, current_outer_width))
            elif name == "内側フチ":
                current_inner_enabled = bool(modifier.get(item.identifier, current_inner_enabled))
            elif name == "内側フチ幅 (mm)":
                current_inner_width = float(modifier.get(item.identifier, current_inner_width))
            elif name == "塗り輪郭ぼかし":
                current_fill_blur = float(modifier.get(item.identifier, current_fill_blur))
            elif name == "塗りぼかしをディザ化":
                current_fill_blur_dither = bool(modifier.get(item.identifier, current_fill_blur_dither))
        except Exception:  # noqa: BLE001
            pass
    _set_modifier_values(
        obj,
        modifier,
        line_width_mm=current_width,
        multi_line_enabled=current_multi_enabled,
        multi_line_count=current_multi_count,
        multi_line_width_mm=current_multi_width,
        multi_line_spacing_mm=current_multi_spacing,
        multi_line_width_scale_percent=current_multi_scale,
        multi_line_direction=current_multi_direction,
        thorn_multi_line_valley_width_mm=current_thorn_valley_width,
        thorn_multi_line_peak_width_mm=current_thorn_peak_width,
        thorn_multi_line_length_scale_percent=current_thorn_length_scale,
        thorn_multi_line_cross_enabled=current_thorn_cross_enabled,
        outer_edge_enabled=current_outer_enabled,
        outer_edge_width_mm=current_outer_width,
        inner_edge_enabled=current_inner_enabled,
        inner_edge_width_mm=current_inner_width,
        fill_blur_amount=current_fill_blur,
        fill_blur_dither=current_fill_blur_dither,
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
