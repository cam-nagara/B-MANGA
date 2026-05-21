"""B-Name 実体表示用 Geometry Nodes ブリッジ."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import bpy

from . import log

_logger = log.get_logger(__name__)

MODIFIER_NAME = "B-Name Geometry Nodes"
GROUP_PREFIX = "BName_GN_"
PROP_GN_KIND = "bname_geometry_nodes_kind"
PROP_GROUP_VERSION = "bname_geometry_nodes_version"
_GROUP_VERSION = 3


@dataclass(frozen=True)
class SocketSpec:
    name: str
    socket_type: str
    default: Any = 0.0


_GROUP_SOCKETS: dict[str, tuple[SocketSpec, ...]] = {
    "effect_line": (
        SocketSpec("種類", "NodeSocketInt", 0),
        SocketSpec("線幅", "NodeSocketFloat", 0.3),
        SocketSpec("不透明度", "NodeSocketFloat", 1.0),
        SocketSpec("本数", "NodeSocketInt", 0),
        SocketSpec("乱数", "NodeSocketInt", 0),
        SocketSpec("幅", "NodeSocketFloat", 0.0),
        SocketSpec("高さ", "NodeSocketFloat", 0.0),
    ),
    "balloon": (
        SocketSpec("線幅", "NodeSocketFloat", 0.3),
        SocketSpec("塗り不透明度", "NodeSocketFloat", 1.0),
        SocketSpec("幅", "NodeSocketFloat", 0.0),
        SocketSpec("高さ", "NodeSocketFloat", 0.0),
        SocketSpec("形状", "NodeSocketInt", 0),
    ),
    "uni_flash": (
        SocketSpec("線幅", "NodeSocketFloat", 0.3),
        SocketSpec("塗り不透明度", "NodeSocketFloat", 1.0),
        SocketSpec("幅", "NodeSocketFloat", 0.0),
        SocketSpec("高さ", "NodeSocketFloat", 0.0),
        SocketSpec("線の間隔", "NodeSocketFloat", 0.4),
        SocketSpec("最大本数", "NodeSocketInt", 1000),
    ),
}

_SHAPE_CODES = {
    "rect": 1,
    "ellipse": 2,
    "cloud": 3,
    "fluffy": 4,
    "thorn": 5,
    "uni_flash": 6,
}

_EFFECT_CODES = {
    "focus": 1,
    "uni_flash": 2,
    "beta_flash": 3,
    "speed": 4,
    "white_outline": 5,
}


def _group_name(kind: str) -> str:
    suffix = {
        "effect_line": "EffectLine",
        "balloon": "Balloon",
        "uni_flash": "UniFlash",
    }.get(kind, kind)
    return f"{GROUP_PREFIX}{suffix}"


def _interface_socket(group, name: str, in_out: str):
    for item in group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "name", "") == name and getattr(item, "in_out", "") == in_out:
            return item
    return None


def _ensure_socket(group, spec: SocketSpec, *, in_out: str = "INPUT"):
    item = _interface_socket(group, spec.name, in_out)
    if item is None:
        item = group.interface.new_socket(
            name=spec.name,
            in_out=in_out,
            socket_type=spec.socket_type,
        )
    try:
        item.default_value = int(spec.default) if spec.socket_type == "NodeSocketInt" else float(spec.default)
    except Exception:  # noqa: BLE001
        pass
    return item


def _clear_nodes(group) -> None:
    for node in list(group.nodes):
        group.nodes.remove(node)


def _node(group, node_type: str, *, label: str = "", location: tuple[float, float] = (0.0, 0.0)):
    node = group.nodes.new(node_type)
    if label:
        node.label = label
    node.location = location
    return node


def _set_default(socket, value) -> None:
    try:
        socket.default_value = value
    except Exception:  # noqa: BLE001
        pass


def _socket_by_identifier(sockets, identifier: str):
    for socket in sockets:
        if str(getattr(socket, "identifier", "") or "") == identifier:
            return socket
    raise KeyError(identifier)


def _link(group, output_socket, input_socket) -> None:
    try:
        group.links.new(output_socket, input_socket)
    except Exception:  # noqa: BLE001
        _logger.exception("geometry nodes bridge: node link failed")


def _group_input_output(group):
    input_node = _node(group, "NodeGroupInput", label="B-Name パネル入力", location=(-1050, 0))
    output_node = _node(group, "NodeGroupOutput", label="生成結果", location=(760, 0))
    return input_node, output_node


def _math_multiply(group, source_socket, factor: float, *, label: str, location: tuple[float, float]):
    node = _node(group, "ShaderNodeMath", label=label, location=location)
    node.operation = "MULTIPLY"
    _set_default(node.inputs[1], factor)
    _link(group, source_socket, node.inputs[0])
    return node.outputs[0]


def _math_add(group, a_socket, b_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "ShaderNodeMath", label=label, location=location)
    node.operation = "ADD"
    _link(group, a_socket, node.inputs[0])
    _link(group, b_socket, node.inputs[1])
    return node.outputs[0]


def _combine_xyz(
    group,
    x_socket=None,
    y_socket=None,
    *,
    z: float = 0.0,
    label: str,
    location: tuple[float, float],
):
    node = _node(group, "ShaderNodeCombineXYZ", label=label, location=location)
    if x_socket is not None:
        _link(group, x_socket, node.inputs["X"])
    if y_socket is not None:
        _link(group, y_socket, node.inputs["Y"])
    _set_default(node.inputs["Z"], z)
    return node.outputs["Vector"]


def _set_material_index(group, geometry_socket, index: int, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeSetMaterialIndex", label=label, location=location)
    _set_default(node.inputs["Material Index"], int(index))
    _link(group, geometry_socket, node.inputs["Geometry"])
    return node.outputs["Geometry"]


def _ellipse_fill(group, input_node, width_half_m, height_half_m, *, z: float):
    circle = _node(group, "GeometryNodeMeshCircle", label="塗りを生成", location=(-520, 170))
    circle.fill_type = "TRIANGLE_FAN"
    _set_default(circle.inputs["Vertices"], 128)
    _set_default(circle.inputs["Radius"], 1.0)
    scale = _combine_xyz(
        group,
        width_half_m,
        height_half_m,
        z=1.0,
        label="塗りサイズ",
        location=(-520, -40),
    )
    translation = _combine_xyz(
        group,
        width_half_m,
        height_half_m,
        z=z,
        label="塗り位置",
        location=(-520, -250),
    )
    transform = _node(group, "GeometryNodeTransform", label="塗りを配置", location=(-220, 140))
    _link(group, circle.outputs["Mesh"], transform.inputs["Geometry"])
    _link(group, scale, transform.inputs["Scale"])
    _link(group, translation, transform.inputs["Translation"])
    return _set_material_index(
        group,
        transform.outputs["Geometry"],
        1,
        label="塗り素材",
        location=(60, 140),
    )


def _ellipse_outline(group, input_node, width_half_m, height_half_m, line_half_m):
    circle = _node(group, "GeometryNodeCurvePrimitiveCircle", label="輪郭を生成", location=(-520, -480))
    _set_default(circle.inputs["Resolution"], 128)
    _set_default(circle.inputs["Radius"], 1.0)
    scale = _combine_xyz(
        group,
        width_half_m,
        height_half_m,
        z=1.0,
        label="輪郭サイズ",
        location=(-520, -690),
    )
    translation = _combine_xyz(
        group,
        width_half_m,
        height_half_m,
        z=0.0,
        label="輪郭位置",
        location=(-520, -900),
    )
    transform = _node(group, "GeometryNodeTransform", label="輪郭を配置", location=(-220, -520))
    _link(group, circle.outputs["Curve"], transform.inputs["Geometry"])
    _link(group, scale, transform.inputs["Scale"])
    _link(group, translation, transform.inputs["Translation"])

    profile = _node(group, "GeometryNodeCurvePrimitiveCircle", label="線幅を生成", location=(-220, -760))
    _set_default(profile.inputs["Resolution"], 8)
    _link(group, line_half_m, profile.inputs["Radius"])

    curve_to_mesh = _node(group, "GeometryNodeCurveToMesh", label="輪郭をメッシュ化", location=(60, -520))
    _set_default(curve_to_mesh.inputs["Fill Caps"], True)
    _link(group, transform.outputs["Geometry"], curve_to_mesh.inputs["Curve"])
    _link(group, profile.outputs["Curve"], curve_to_mesh.inputs["Profile Curve"])
    return _set_material_index(
        group,
        curve_to_mesh.outputs["Mesh"],
        0,
        label="線素材",
        location=(340, -520),
    )


def _balloon_ellipse_geometry(group, input_node, width_half_m, height_half_m, line_half_m):
    fill = _ellipse_fill(group, input_node, width_half_m, height_half_m, z=-0.001)
    outline = _ellipse_outline(group, input_node, width_half_m, height_half_m, line_half_m)
    join = _node(group, "GeometryNodeJoinGeometry", label="楕円フキダシ生成", location=(570, -120))
    _link(group, fill, join.inputs["Geometry"])
    _link(group, outline, join.inputs["Geometry"])
    return join.outputs["Geometry"]


def _build_balloon_nodes(group) -> None:
    _clear_nodes(group)
    input_node, output_node = _group_input_output(group)
    width_half_m = _math_multiply(
        group,
        input_node.outputs["幅"],
        0.0005,
        label="幅 mm → 半幅 m",
        location=(-800, -80),
    )
    height_half_m = _math_multiply(
        group,
        input_node.outputs["高さ"],
        0.0005,
        label="高さ mm → 半高 m",
        location=(-800, -240),
    )
    line_half_m = _math_multiply(
        group,
        input_node.outputs["線幅"],
        0.0005,
        label="線幅 mm → 半径 m",
        location=(-800, -400),
    )
    generated = _balloon_ellipse_geometry(group, input_node, width_half_m, height_half_m, line_half_m)
    compare = _node(group, "FunctionNodeCompare", label="楕円ならノード生成", location=(340, 220))
    compare.data_type = "INT"
    compare.operation = "EQUAL"
    _set_default(_socket_by_identifier(compare.inputs, "B_INT"), 2)
    _link(group, input_node.outputs["形状"], _socket_by_identifier(compare.inputs, "A_INT"))
    switch = _node(group, "GeometryNodeSwitch", label="複雑形状は互換形状", location=(570, 90))
    switch.input_type = "GEOMETRY"
    _link(group, compare.outputs["Result"], switch.inputs["Switch"])
    _link(group, input_node.outputs["Geometry"], switch.inputs["False"])
    _link(group, generated, switch.inputs["True"])
    _link(group, switch.outputs["Output"], output_node.inputs["Geometry"])
    group[PROP_GROUP_VERSION] = _GROUP_VERSION


def _radial_line_points(
    group,
    width_half_m,
    height_half_m,
    cos_v: float,
    sin_v: float,
    inner_scale: float,
    index: int,
    row: int,
):
    sx_off = _math_multiply(group, width_half_m, cos_v * inner_scale, label=f"始点X {index}", location=(-520, row))
    sy_off = _math_multiply(group, height_half_m, sin_v * inner_scale, label=f"始点Y {index}", location=(-320, row))
    ex_off = _math_multiply(group, width_half_m, cos_v, label=f"終点X {index}", location=(-120, row))
    ey_off = _math_multiply(group, height_half_m, sin_v, label=f"終点Y {index}", location=(80, row))
    sx = _math_add(group, width_half_m, sx_off, label=f"始点X配置 {index}", location=(-520, row - 1520))
    sy = _math_add(group, height_half_m, sy_off, label=f"始点Y配置 {index}", location=(-320, row - 1520))
    ex = _math_add(group, width_half_m, ex_off, label=f"終点X配置 {index}", location=(-120, row - 1520))
    ey = _math_add(group, height_half_m, ey_off, label=f"終点Y配置 {index}", location=(80, row - 1520))
    start_vec = _combine_xyz(group, sx, sy, z=0.0, label=f"始点 {index}", location=(280, row))
    end_vec = _combine_xyz(group, ex, ey, z=0.0, label=f"終点 {index}", location=(480, row))
    return start_vec, end_vec


def _add_radial_line(group, start_vec, end_vec, profile_socket, *, index: int, row: int):
    line = _node(group, "GeometryNodeCurvePrimitiveLine", label=f"線 {index}", location=(680, row))
    _link(group, start_vec, line.inputs["Start"])
    _link(group, end_vec, line.inputs["End"])
    curve_to_mesh = _node(group, "GeometryNodeCurveToMesh", label=f"線をメッシュ化 {index}", location=(880, row))
    _set_default(curve_to_mesh.inputs["Fill Caps"], True)
    _link(group, line.outputs["Curve"], curve_to_mesh.inputs["Curve"])
    _link(group, profile_socket, curve_to_mesh.inputs["Profile Curve"])
    return _set_material_index(
        group,
        curve_to_mesh.outputs["Mesh"],
        0,
        label=f"線素材 {index}",
        location=(1080, row),
    )


def _radial_line_geometry(
    group,
    width_half_m,
    height_half_m,
    line_half_m,
    *,
    inner_scale: float,
    line_count: int,
    label: str,
):
    import math

    profile = _node(group, "GeometryNodeCurvePrimitiveCircle", label="線幅を生成", location=(-220, -220))
    _set_default(profile.inputs["Resolution"], 8)
    _link(group, line_half_m, profile.inputs["Radius"])
    join = _node(group, "GeometryNodeJoinGeometry", label=label, location=(1320, -80))
    count = max(3, int(line_count))
    for i in range(count):
        angle = (math.tau * i) / count
        row = -420 - i * 70
        start_vec, end_vec = _radial_line_points(
            group,
            width_half_m,
            height_half_m,
            math.cos(angle),
            math.sin(angle),
            inner_scale,
            i + 1,
            row,
        )
        material = _add_radial_line(group, start_vec, end_vec, profile.outputs["Curve"], index=i + 1, row=row)
        _link(group, material, join.inputs["Geometry"])
    return join.outputs["Geometry"]


def _build_uni_flash_nodes(group) -> None:
    _clear_nodes(group)
    input_node, output_node = _group_input_output(group)
    width_half_m = _math_multiply(
        group,
        input_node.outputs["幅"],
        0.0005,
        label="幅 mm → 半幅 m",
        location=(-800, -80),
    )
    height_half_m = _math_multiply(
        group,
        input_node.outputs["高さ"],
        0.0005,
        label="高さ mm → 半高 m",
        location=(-800, -240),
    )
    line_half_m = _math_multiply(
        group,
        input_node.outputs["線幅"],
        0.0005,
        label="線幅 mm → 半径 m",
        location=(-800, -400),
    )
    fill = _ellipse_fill(group, input_node, width_half_m, height_half_m, z=-0.001)
    lines = _radial_line_geometry(
        group,
        width_half_m,
        height_half_m,
        line_half_m,
        inner_scale=0.38,
        line_count=24,
        label="ウニフラッシュ線生成",
    )
    join = _node(group, "GeometryNodeJoinGeometry", label="ウニフラッシュ生成", location=(1520, 80))
    _link(group, fill, join.inputs["Geometry"])
    _link(group, lines, join.inputs["Geometry"])
    _link(group, join.outputs["Geometry"], output_node.inputs["Geometry"])
    group[PROP_GROUP_VERSION] = _GROUP_VERSION


def _build_effect_line_nodes(group) -> None:
    _clear_nodes(group)
    input_node, output_node = _group_input_output(group)
    width_half_m = _math_multiply(
        group,
        input_node.outputs["幅"],
        0.0005,
        label="幅 mm → 半幅 m",
        location=(-800, -40),
    )
    height_half_m = _math_multiply(
        group,
        input_node.outputs["高さ"],
        0.0005,
        label="高さ mm → 半高 m",
        location=(-800, -190),
    )
    line_half_m = _math_multiply(
        group,
        input_node.outputs["線幅"],
        0.0005,
        label="線幅 mm → 半径 m",
        location=(-800, -340),
    )
    lines = _radial_line_geometry(
        group,
        width_half_m,
        height_half_m,
        line_half_m,
        inner_scale=0.12,
        line_count=16,
        label="効果線生成",
    )
    _link(group, lines, output_node.inputs["Geometry"])
    group[PROP_GROUP_VERSION] = _GROUP_VERSION


def _build_generator_nodes(group, kind: str) -> None:
    if kind == "effect_line":
        _build_effect_line_nodes(group)
    elif kind == "uni_flash":
        _build_uni_flash_nodes(group)
    else:
        _build_balloon_nodes(group)


def _group_needs_rebuild(group, kind: str) -> bool:
    try:
        if int(group.get(PROP_GROUP_VERSION, 0) or 0) != _GROUP_VERSION:
            return True
    except Exception:  # noqa: BLE001
        return True
    generator_types = {
        "effect_line": {"GeometryNodeCurvePrimitiveLine", "GeometryNodeCurveToMesh"},
        "balloon": {"GeometryNodeMeshCircle", "GeometryNodeCurveToMesh", "GeometryNodeSetMaterialIndex"},
        "uni_flash": {"GeometryNodeMeshCircle", "GeometryNodeCurveToMesh", "GeometryNodeSetMaterialIndex"},
    }.get(kind, set())
    existing = {node.bl_idname for node in group.nodes}
    return not generator_types.issubset(existing)


def ensure_node_group(kind: str) -> bpy.types.NodeTree:
    """B-Name 用 Geometry Nodes グループを取得または作成する."""
    group = bpy.data.node_groups.get(_group_name(kind))
    if group is None:
        group = bpy.data.node_groups.new(_group_name(kind), "GeometryNodeTree")
    group.use_fake_user = True
    if _interface_socket(group, "Geometry", "INPUT") is None:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    if _interface_socket(group, "Geometry", "OUTPUT") is None:
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for spec in _GROUP_SOCKETS.get(kind, ()):
        _ensure_socket(group, spec)
    if len(group.nodes) == 0 or _group_needs_rebuild(group, kind):
        _build_generator_nodes(group, kind)
    else:
        has_group_input = any(node.bl_idname == "NodeGroupInput" for node in group.nodes)
        has_group_output = any(node.bl_idname == "NodeGroupOutput" for node in group.nodes)
        if not has_group_input or not has_group_output:
            _build_generator_nodes(group, kind)
    return group


def _socket_specs(kind: str) -> dict[str, SocketSpec]:
    return {spec.name: spec for spec in _GROUP_SOCKETS.get(kind, ())}


def _socket_identifiers(group, kind: str) -> dict[str, tuple[str, SocketSpec]]:
    specs = _socket_specs(kind)
    out: dict[str, tuple[str, SocketSpec]] = {}
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


def _set_modifier_value(modifier, identifier: str, spec: SocketSpec, value: Any) -> None:
    if not identifier:
        return
    try:
        if spec.socket_type == "NodeSocketInt":
            modifier[identifier] = int(round(float(value or 0)))
        else:
            modifier[identifier] = float(value or 0.0)
    except Exception:  # noqa: BLE001
        _logger.exception("geometry nodes bridge: modifier input sync failed")


def ensure_modifier(obj: bpy.types.Object | None, kind: str, values: Mapping[str, Any] | None = None):
    """対象オブジェクトへ B-Name 用 Geometry Nodes モディファイアを同期する."""
    if obj is None:
        return None
    group = ensure_node_group(kind)
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None or getattr(modifier, "type", "") != "NODES":
        modifier = obj.modifiers.new(MODIFIER_NAME, "NODES")
    try:
        modifier.node_group = group
    except Exception:  # noqa: BLE001
        _logger.exception("geometry nodes bridge: assign node group failed")
        return modifier
    obj[PROP_GN_KIND] = kind
    identifiers = _socket_identifiers(group, kind)
    for name, value in dict(values or {}).items():
        socket = identifiers.get(name)
        if socket:
            ident, spec = socket
            _set_modifier_value(modifier, ident, spec, value)
    try:
        obj.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return modifier


def register() -> None:
    for kind in _GROUP_SOCKETS:
        try:
            ensure_node_group(kind)
        except Exception:  # noqa: BLE001
            _logger.exception("geometry nodes bridge: group register rebuild failed")


def unregister() -> None:
    return None


def effect_values(params, bounds: tuple[float, float, float, float] | None, seed: int) -> dict[str, Any]:
    if bounds is None:
        width = height = 0.0
    else:
        _x, _y, width, height = bounds
    effect_type = str(getattr(params, "effect_type", "") or "")
    if effect_type == "speed":
        line_count = int(getattr(params, "speed_line_count", 0) or 0)
    elif effect_type == "white_outline":
        line_count = int(getattr(params, "white_outline_count", 0) or 0)
    else:
        line_count = int(getattr(params, "max_line_count", 0) or 0)
    return {
        "種類": _EFFECT_CODES.get(effect_type, 0),
        "線幅": float(getattr(params, "brush_size_mm", 0.3) or 0.3),
        "不透明度": float(getattr(params, "opacity", 1.0) or 1.0),
        "本数": line_count,
        "乱数": int(seed),
        "幅": float(width or 0.0),
        "高さ": float(height or 0.0),
    }


def balloon_values(entry, *, uni_flash: bool = False) -> dict[str, Any]:
    shape = str(getattr(entry, "shape", "") or "")
    values = {
        "線幅": float(getattr(entry, "line_width_mm", 0.3) or 0.3),
        "塗り不透明度": float(getattr(entry, "fill_opacity", 1.0) or 1.0),
        "幅": float(getattr(entry, "width_mm", 0.0) or 0.0),
        "高さ": float(getattr(entry, "height_mm", 0.0) or 0.0),
        "形状": _SHAPE_CODES.get(shape, 0),
    }
    if uni_flash:
        params = getattr(entry, "shape_params", None)
        values.update({
            "線の間隔": float(getattr(params, "uni_flash_spacing_mm", 0.4) or 0.4),
            "最大本数": int(getattr(params, "uni_flash_max_line_count", 1000) or 1000),
        })
    return values
