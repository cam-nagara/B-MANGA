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
_GROUP_VERSION = 11
_BALLOON_TAIL_SOCKET_COUNT = 8
_SETTING_OUTPUT_PREFIX = "設定接続確認: "


@dataclass(frozen=True)
class SocketSpec:
    name: str
    socket_type: str
    default: Any = 0.0


_SHAPE_CODES = {
    "rect": 1,
    "ellipse": 2,
    "cloud": 3,
    "fluffy": 4,
    "thorn": 5,
    "thorn-curve": 6,
    "octagon": 7,
    "custom": 8,
    "none": 9,
}

_EFFECT_CODES = {
    "focus": 1,
    "uni_flash": 2,
    "beta_flash": 3,
    "speed": 4,
    "white_outline": 5,
}

_ENUM_CODES = {
    "effect_type": _EFFECT_CODES,
    "start_shape": _SHAPE_CODES,
    "end_shape": _SHAPE_CODES,
    "spacing_mode": {"angle": 1, "distance": 2},
    "start_frame_density_basis": {"frame": 1, "rounded_frame": 2, "ellipse": 3},
    "inout_apply": {"brush_size": 1, "opacity": 2},
    "inout_range_mode": {"percent": 1, "length": 2},
    "line_style": {"solid": 1, "dashed": 2, "dotted": 3, "double": 4},
    "blend_mode": {"normal": 1, "lighten": 2},
}

_TAIL_TYPE_CODES = {"straight": 1, "curve": 2, "sticky": 3}

_EFFECT_FIELD_SPECS: dict[str, SocketSpec] = {
    "effect_type": SocketSpec("種類", "NodeSocketInt", 0),
    "rotation_deg": SocketSpec("全体回転", "NodeSocketFloat", 0.0),
    "start_shape": SocketSpec("始点形状", "NodeSocketInt", 1),
    "start_to_coma_frame": SocketSpec("始点をコマ枠に設定", "NodeSocketBool", False),
    "start_frame_density_basis": SocketSpec("密度基準", "NodeSocketInt", 2),
    "start_frame_density_rounding_percent": SocketSpec("角丸率 (%)", "NodeSocketFloat", 100.0),
    "start_rounded_corner_enabled": SocketSpec("始点 角丸", "NodeSocketBool", False),
    "start_rounded_corner_radius_mm": SocketSpec("始点 角半径", "NodeSocketFloat", 3.0),
    "start_cloud_bump_width_mm": SocketSpec("始点 山の幅", "NodeSocketFloat", 10.0),
    "start_cloud_bump_width_jitter": SocketSpec("始点 山の幅 乱れ", "NodeSocketFloat", 0.0),
    "start_cloud_bump_height_mm": SocketSpec("始点 山の高さ", "NodeSocketFloat", 4.0),
    "start_cloud_bump_height_jitter": SocketSpec("始点 山の高さ 乱れ", "NodeSocketFloat", 0.0),
    "start_cloud_offset_percent": SocketSpec("始点 ズラし量 (%)", "NodeSocketFloat", 50.0),
    "start_cloud_sub_width_ratio": SocketSpec("始点 小山幅 (%)", "NodeSocketFloat", 0.0),
    "start_cloud_sub_width_jitter": SocketSpec("始点 小山幅 乱れ", "NodeSocketFloat", 0.0),
    "start_cloud_sub_height_ratio": SocketSpec("始点 小山高 (%)", "NodeSocketFloat", 0.0),
    "start_cloud_sub_height_jitter": SocketSpec("始点 小山高 乱れ", "NodeSocketFloat", 0.0),
    "end_shape": SocketSpec("終点形状", "NodeSocketInt", 2),
    "end_rounded_corner_enabled": SocketSpec("終点 角丸", "NodeSocketBool", False),
    "end_rounded_corner_radius_mm": SocketSpec("終点 角半径", "NodeSocketFloat", 3.0),
    "end_cloud_bump_width_mm": SocketSpec("終点 山の幅", "NodeSocketFloat", 10.0),
    "end_cloud_bump_width_jitter": SocketSpec("終点 山の幅 乱れ", "NodeSocketFloat", 0.0),
    "end_cloud_bump_height_mm": SocketSpec("終点 山の高さ", "NodeSocketFloat", 4.0),
    "end_cloud_bump_height_jitter": SocketSpec("終点 山の高さ 乱れ", "NodeSocketFloat", 0.0),
    "end_cloud_offset_percent": SocketSpec("終点 ズラし量 (%)", "NodeSocketFloat", 50.0),
    "end_cloud_sub_width_ratio": SocketSpec("終点 小山幅 (%)", "NodeSocketFloat", 0.0),
    "end_cloud_sub_width_jitter": SocketSpec("終点 小山幅 乱れ", "NodeSocketFloat", 0.0),
    "end_cloud_sub_height_ratio": SocketSpec("終点 小山高 (%)", "NodeSocketFloat", 0.0),
    "end_cloud_sub_height_jitter": SocketSpec("終点 小山高 乱れ", "NodeSocketFloat", 0.0),
    "brush_size_mm": SocketSpec("線幅", "NodeSocketFloat", 0.3),
    "brush_jitter_enabled": SocketSpec("線幅 乱れ", "NodeSocketBool", False),
    "brush_jitter_amount": SocketSpec("線幅 乱れ量", "NodeSocketFloat", 0.2),
    "length_jitter_enabled": SocketSpec("始点乱れ", "NodeSocketBool", False),
    "length_jitter_amount": SocketSpec("始点乱れ量", "NodeSocketFloat", 0.2),
    "end_length_jitter_enabled": SocketSpec("終点乱れ", "NodeSocketBool", False),
    "end_length_jitter_amount": SocketSpec("終点乱れ量", "NodeSocketFloat", 0.2),
    "spacing_mode": SocketSpec("線の間隔", "NodeSocketInt", 2),
    "spacing_angle_deg": SocketSpec("線の間隔 (角度)", "NodeSocketFloat", 5.0),
    "spacing_distance_mm": SocketSpec("線の間隔 (距離)", "NodeSocketFloat", 0.4),
    "spacing_density_compensation": SocketSpec("密度補正", "NodeSocketBool", True),
    "spacing_jitter_enabled": SocketSpec("間隔 乱れ", "NodeSocketBool", False),
    "spacing_jitter_amount": SocketSpec("間隔乱れ量", "NodeSocketFloat", 0.2),
    "max_line_count": SocketSpec("本数", "NodeSocketInt", 1000),
    "bundle_enabled": SocketSpec("まとまり", "NodeSocketBool", False),
    "bundle_line_count": SocketSpec("まとまり 数", "NodeSocketInt", 4),
    "bundle_line_count_jitter": SocketSpec("まとまり 数の乱れ", "NodeSocketFloat", 0.0),
    "bundle_jitter_amount": SocketSpec("まとまりの乱れ", "NodeSocketFloat", 0.2),
    "bundle_gap_mm": SocketSpec("まとまり間隔", "NodeSocketFloat", 0.2),
    "bundle_gap_jitter_amount": SocketSpec("まとまり間隔の乱れ", "NodeSocketFloat", 0.0),
    "inout_apply": SocketSpec("適用先", "NodeSocketInt", 1),
    "in_percent": SocketSpec("入り (%)", "NodeSocketFloat", 100.0),
    "out_percent": SocketSpec("抜き (%)", "NodeSocketFloat", 0.0),
    "in_start_percent": SocketSpec("入り始点 (%)", "NodeSocketFloat", 50.0),
    "out_start_percent": SocketSpec("抜き始点 (%)", "NodeSocketFloat", 50.0),
    "in_easing_curve": SocketSpec("入りカーブ", "NodeSocketString", "0.0000,0.0000;1.0000,1.0000"),
    "out_easing_curve": SocketSpec("抜きカーブ", "NodeSocketString", "0.0000,0.0000;1.0000,1.0000"),
    "inout_range_mode": SocketSpec("範囲", "NodeSocketInt", 1),
    "in_range_percent": SocketSpec("入りの範囲 (%)", "NodeSocketFloat", 100.0),
    "out_range_percent": SocketSpec("抜きの範囲 (%)", "NodeSocketFloat", 100.0),
    "in_range_mm": SocketSpec("入りの範囲 (mm)", "NodeSocketFloat", 10.0),
    "out_range_mm": SocketSpec("抜きの範囲 (mm)", "NodeSocketFloat", 10.0),
    "opacity": SocketSpec("不透明度", "NodeSocketFloat", 1.0),
    "line_color": SocketSpec("線色", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0)),
    "fill_color": SocketSpec("塗り色", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0)),
    "fill_opacity": SocketSpec("塗り不透明度", "NodeSocketFloat", 1.0),
    "fill_base_shape": SocketSpec("終点形状を下地として塗る", "NodeSocketBool", False),
    "speed_angle_deg": SocketSpec("流線の角度", "NodeSocketFloat", 0.0),
    "speed_line_count": SocketSpec("流線の本数上限", "NodeSocketInt", 300),
    "white_outline_count": SocketSpec("白抜き線 本数", "NodeSocketInt", 5),
    "white_outline_spacing_mm": SocketSpec("白抜き線 間隔", "NodeSocketFloat", 0.2),
    "white_outline_width_mm": SocketSpec("白抜き線 太さ", "NodeSocketFloat", 10.0),
    "white_outline_width_jitter_enabled": SocketSpec("白抜き線 太さ乱れ", "NodeSocketBool", False),
    "white_outline_width_min_percent": SocketSpec("白抜き線 最小太さ (%)", "NodeSocketFloat", 50.0),
    "white_outline_length_jitter_enabled": SocketSpec("白抜き線 長さ乱れ", "NodeSocketBool", False),
    "white_outline_length_min_percent": SocketSpec("白抜き線 最小長さ (%)", "NodeSocketFloat", 50.0),
    "white_outline_white_ratio_percent": SocketSpec("白線割合 (%)", "NodeSocketFloat", 30.0),
    "white_outline_white_brush_mm": SocketSpec("白線太さ", "NodeSocketFloat", 0.3),
    "white_outline_white_attenuation": SocketSpec("白線減衰", "NodeSocketFloat", 0.0),
    "white_outline_black_brush_mm": SocketSpec("黒線太さ", "NodeSocketFloat", 0.3),
    "white_outline_black_attenuation": SocketSpec("黒線減衰", "NodeSocketFloat", 0.0),
    "white_outline_angle_deg": SocketSpec("白抜き線 角度", "NodeSocketFloat", 0.0),
}

_EFFECT_POSITION_SOCKETS = (
    SocketSpec("位置 X", "NodeSocketFloat", 0.0),
    SocketSpec("位置 Y", "NodeSocketFloat", 0.0),
    SocketSpec("幅", "NodeSocketFloat", 0.0),
    SocketSpec("高さ", "NodeSocketFloat", 0.0),
    SocketSpec("乱数", "NodeSocketInt", 0),
)

_MATERIAL_SOCKETS = (
    SocketSpec("線素材", "NodeSocketMaterial", None),
    SocketSpec("塗り素材", "NodeSocketMaterial", None),
)


def _balloon_tail_socket_specs(index: int) -> tuple[SocketSpec, ...]:
    prefix = f"しっぽ{index}"
    return (
        SocketSpec(f"{prefix} 種類", "NodeSocketInt", 0),
        SocketSpec(f"{prefix} 方向", "NodeSocketFloat", 270.0),
        SocketSpec(f"{prefix} 長さ", "NodeSocketFloat", 6.0),
        SocketSpec(f"{prefix} 根元幅", "NodeSocketFloat", 3.0),
        SocketSpec(f"{prefix} 先端幅", "NodeSocketFloat", 0.0),
        SocketSpec(f"{prefix} 曲げ", "NodeSocketFloat", 0.0),
        SocketSpec(f"{prefix} 始点・終点を固定", "NodeSocketBool", False),
        SocketSpec(f"{prefix} 始点 X", "NodeSocketFloat", 0.0),
        SocketSpec(f"{prefix} 始点 Y", "NodeSocketFloat", 0.0),
        SocketSpec(f"{prefix} 終点 X", "NodeSocketFloat", 0.0),
        SocketSpec(f"{prefix} 終点 Y", "NodeSocketFloat", 0.0),
    )


_BALLOON_TAIL_SOCKETS = tuple(
    spec
    for index in range(1, _BALLOON_TAIL_SOCKET_COUNT + 1)
    for spec in _balloon_tail_socket_specs(index)
)

_BALLOON_EXTRA_SOCKETS = (
    SocketSpec("形状", "NodeSocketInt", 0),
    SocketSpec("カスタム形状名", "NodeSocketString", ""),
    SocketSpec("X", "NodeSocketFloat", 0.0),
    SocketSpec("Y", "NodeSocketFloat", 0.0),
    SocketSpec("幅", "NodeSocketFloat", 0.0),
    SocketSpec("高さ", "NodeSocketFloat", 0.0),
    SocketSpec("回転", "NodeSocketFloat", 0.0),
    SocketSpec("中心点 X", "NodeSocketFloat", 0.0),
    SocketSpec("中心点 Y", "NodeSocketFloat", 0.0),
    SocketSpec("角丸", "NodeSocketBool", False),
    SocketSpec("角半径", "NodeSocketFloat", 3.0),
    SocketSpec("線種", "NodeSocketInt", 1),
    SocketSpec("線幅", "NodeSocketFloat", 0.3),
    SocketSpec("線色", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0)),
    SocketSpec("塗り色", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    SocketSpec("塗り不透明度", "NodeSocketFloat", 1.0),
    SocketSpec("塗りマテリアル", "NodeSocketString", ""),
    SocketSpec("塗り輪郭ぼかし", "NodeSocketFloat", 0.0),
    SocketSpec("塗りぼかしをディザ化", "NodeSocketBool", False),
    SocketSpec("塗りグラデーション", "NodeSocketBool", False),
    SocketSpec("グラデーション開始色", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    SocketSpec("グラデーション終了色", "NodeSocketColor", (0.82, 0.82, 0.82, 1.0)),
    SocketSpec("グラデーション角度", "NodeSocketFloat", 90.0),
    SocketSpec("外側白フチ", "NodeSocketBool", False),
    SocketSpec("外側白フチ幅", "NodeSocketFloat", 1.0),
    SocketSpec("外側白フチ色", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    SocketSpec("内側白フチ", "NodeSocketBool", False),
    SocketSpec("内側白フチ幅", "NodeSocketFloat", 1.0),
    SocketSpec("内側白フチ色", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    SocketSpec("合成モード", "NodeSocketInt", 1),
    SocketSpec("水平反転", "NodeSocketBool", False),
    SocketSpec("垂直反転", "NodeSocketBool", False),
    SocketSpec("不透明度", "NodeSocketFloat", 1.0),
    SocketSpec("山の幅", "NodeSocketFloat", 10.0),
    SocketSpec("山の幅 乱れ", "NodeSocketFloat", 0.0),
    SocketSpec("山の高さ", "NodeSocketFloat", 4.0),
    SocketSpec("山の高さ 乱れ", "NodeSocketFloat", 0.0),
    SocketSpec("ズラし量 (%)", "NodeSocketFloat", 50.0),
    SocketSpec("小山幅 (%)", "NodeSocketFloat", 0.0),
    SocketSpec("小山幅 乱れ", "NodeSocketFloat", 0.0),
    SocketSpec("小山高 (%)", "NodeSocketFloat", 0.0),
    SocketSpec("小山高 乱れ", "NodeSocketFloat", 0.0),
    SocketSpec("雲の波数", "NodeSocketInt", 12),
    SocketSpec("波の振幅", "NodeSocketFloat", 3.0),
    SocketSpec("トゲ数", "NodeSocketInt", 24),
    SocketSpec("トゲの深さ", "NodeSocketFloat", 6.0),
    SocketSpec("トゲのばらつき", "NodeSocketFloat", 0.2),
    SocketSpec("しっぽ数", "NodeSocketInt", 0),
    SocketSpec("しっぽ 種類", "NodeSocketInt", 0),
    SocketSpec("しっぽ 方向", "NodeSocketFloat", 270.0),
    SocketSpec("しっぽ 長さ", "NodeSocketFloat", 6.0),
    SocketSpec("しっぽ 根元幅", "NodeSocketFloat", 3.0),
    SocketSpec("しっぽ 先端幅", "NodeSocketFloat", 0.0),
    SocketSpec("しっぽ 曲げ", "NodeSocketFloat", 0.0),
    SocketSpec("しっぽ 始点・終点を固定", "NodeSocketBool", False),
    SocketSpec("しっぽ 始点 X", "NodeSocketFloat", 0.0),
    SocketSpec("しっぽ 始点 Y", "NodeSocketFloat", 0.0),
    SocketSpec("しっぽ 終点 X", "NodeSocketFloat", 0.0),
    SocketSpec("しっぽ 終点 Y", "NodeSocketFloat", 0.0),
) + _BALLOON_TAIL_SOCKETS

_GROUP_SOCKETS: dict[str, tuple[SocketSpec, ...]] = {
    "effect_line": tuple(_EFFECT_FIELD_SPECS.values()) + _EFFECT_POSITION_SOCKETS + _MATERIAL_SOCKETS,
    "balloon": _BALLOON_EXTRA_SOCKETS + _MATERIAL_SOCKETS,
}


def _group_name(kind: str) -> str:
    suffix = {
        "effect_line": "EffectLine",
        "balloon": "Balloon",
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
        try:
            item = group.interface.new_socket(
                name=spec.name,
                in_out=in_out,
                socket_type=spec.socket_type,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("geometry nodes bridge: socket create failed: %s", spec.name)
            return None
    try:
        if spec.socket_type == "NodeSocketBool":
            item.default_value = bool(spec.default)
        elif spec.socket_type == "NodeSocketInt":
            item.default_value = int(spec.default or 0)
        elif spec.socket_type == "NodeSocketColor":
            item.default_value = tuple(spec.default or (0.0, 0.0, 0.0, 1.0))
        elif spec.socket_type == "NodeSocketString":
            item.default_value = str(spec.default or "")
        elif spec.socket_type not in {"NodeSocketObject", "NodeSocketMaterial"}:
            item.default_value = float(spec.default or 0.0)
    except Exception:  # noqa: BLE001
        pass
    return item


def _prune_input_sockets(group, kind: str) -> None:
    allowed = {"Geometry"} | {spec.name for spec in _GROUP_SOCKETS.get(kind, ())}
    for item in list(group.interface.items_tree):
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "in_out", "") != "INPUT":
            continue
        if str(getattr(item, "name", "") or "") in allowed:
            continue
        try:
            group.interface.remove(item)
        except Exception:  # noqa: BLE001
            pass


def _setting_output_name(socket_name: str) -> str:
    return f"{_SETTING_OUTPUT_PREFIX}{socket_name}"


def _ensure_setting_output_sockets(group, kind: str) -> None:
    for spec in _GROUP_SOCKETS.get(kind, ()):
        if spec.socket_type == "NodeSocketMaterial":
            continue
        _ensure_socket(
            group,
            SocketSpec(_setting_output_name(spec.name), spec.socket_type, spec.default),
            in_out="OUTPUT",
        )


def _prune_setting_output_sockets(group, kind: str) -> None:
    allowed = {"Geometry"} | {
        _setting_output_name(spec.name)
        for spec in _GROUP_SOCKETS.get(kind, ())
        if spec.socket_type != "NodeSocketMaterial"
    }
    for item in list(group.interface.items_tree):
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "in_out", "") != "OUTPUT":
            continue
        name = str(getattr(item, "name", "") or "")
        if name in allowed:
            continue
        if not name.startswith(_SETTING_OUTPUT_PREFIX):
            continue
        try:
            group.interface.remove(item)
        except Exception:  # noqa: BLE001
            pass


def _link_settings_to_audit_outputs(group, input_node, output_node, kind: str) -> None:
    for spec in _GROUP_SOCKETS.get(kind, ()):
        if spec.socket_type == "NodeSocketMaterial":
            continue
        source = input_node.outputs.get(spec.name)
        target = output_node.inputs.get(_setting_output_name(spec.name))
        if source is None or target is None:
            continue
        _link(group, source, target)


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


def _math_binary(
    group,
    operation: str,
    a_socket,
    b_socket=None,
    *,
    b_value: float | None = None,
    label: str,
    location: tuple[float, float],
):
    node = _node(group, "ShaderNodeMath", label=label, location=location)
    node.operation = operation
    _link(group, a_socket, node.inputs[0])
    if b_socket is not None:
        _link(group, b_socket, node.inputs[1])
    elif b_value is not None:
        _set_default(node.inputs[1], float(b_value))
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
    if "Selection" in node.inputs:
        _set_default(node.inputs["Selection"], True)
    _set_default(node.inputs["Material Index"], int(index))
    _link(group, geometry_socket, node.inputs["Geometry"])
    return node.outputs["Geometry"]


def _set_material(group, geometry_socket, material_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeSetMaterial", label=label, location=location)
    if "Selection" in node.inputs:
        _set_default(node.inputs["Selection"], True)
    _link(group, geometry_socket, node.inputs["Geometry"])
    if material_socket is not None:
        _link(group, material_socket, node.inputs["Material"])
    return node.outputs["Geometry"]


def _separate_xyz(group, vector_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "ShaderNodeSeparateXYZ", label=label, location=location)
    _link(group, vector_socket, node.inputs["Vector"])
    return node


def _shape_compare(group, input_node, shape_code: int, *, label: str, location: tuple[float, float]):
    compare = _node(group, "FunctionNodeCompare", label=label, location=location)
    compare.data_type = "INT"
    compare.operation = "EQUAL"
    _set_default(_socket_by_identifier(compare.inputs, "B_INT"), int(shape_code))
    _link(group, input_node.outputs["形状"], _socket_by_identifier(compare.inputs, "A_INT"))
    return compare.outputs["Result"]


def _switch_float(group, switch_socket, false_socket, true_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeSwitch", label=label, location=location)
    node.input_type = "FLOAT"
    _link(group, switch_socket, node.inputs["Switch"])
    _link(group, false_socket, node.inputs["False"])
    _link(group, true_socket, node.inputs["True"])
    return node.outputs["Output"]


def _constant_float(group, value: float, *, label: str, location: tuple[float, float]):
    node = _node(group, "ShaderNodeValue", label=label, location=location)
    _set_default(node.outputs["Value"], float(value))
    return node.outputs["Value"]


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


def _balloon_shape_factor(group, input_node, pos_x, pos_y, width_half_m, height_half_m):
    import math

    angle = _math_binary(
        group,
        "ARCTAN2",
        pos_y,
        pos_x,
        label="輪郭角度",
        location=(-760, 500),
    )
    radius_sum = _math_add(group, width_half_m, height_half_m, label="半径合計", location=(-760, 340))
    radius_avg = _math_binary(
        group,
        "MULTIPLY",
        radius_sum,
        b_value=0.5,
        label="平均半径",
        location=(-560, 340),
    )
    one = _constant_float(group, 1.0, label="通常形状", location=(-560, 180))

    radius_mm = _math_binary(
        group,
        "MULTIPLY",
        radius_avg,
        b_value=1000.0,
        label="平均半径 m → mm",
        location=(-760, 620),
    )
    cloud_perimeter = _math_binary(
        group,
        "MULTIPLY",
        radius_mm,
        b_value=math.tau,
        label="雲の周長",
        location=(-560, 620),
    )
    cloud_wave_count = _math_binary(
        group,
        "DIVIDE",
        cloud_perimeter,
        input_node.outputs["山の幅"],
        label="山の幅から山数",
        location=(-360, 620),
    )
    cloud_wave_count = _math_binary(
        group,
        "MAXIMUM",
        cloud_wave_count,
        b_value=3.0,
        label="山数下限",
        location=(-160, 620),
    )
    cloud_phase = _math_binary(
        group,
        "MULTIPLY",
        input_node.outputs["ズラし量 (%)"],
        b_value=math.tau / 100.0,
        label="山のズラし",
        location=(-160, 460),
    )
    cloud_angle = _math_add(
        group,
        angle,
        cloud_phase,
        label="ズラし済み角度",
        location=(40, 460),
    )
    cloud_wave = _math_binary(
        group,
        "MULTIPLY",
        cloud_angle,
        cloud_wave_count,
        label="雲の波",
        location=(-560, 560),
    )
    cloud_sin = _math_binary(
        group,
        "SINE",
        cloud_wave,
        label="雲の丸み",
        location=(-360, 560),
    )
    cloud_amp_m = _math_binary(
        group,
        "MULTIPLY",
        input_node.outputs["山の高さ"],
        b_value=0.001,
        label="雲の振幅 mm → m",
        location=(-560, 430),
    )
    cloud_amp = _math_binary(
        group,
        "DIVIDE",
        cloud_amp_m,
        radius_avg,
        label="雲の振幅率",
        location=(-360, 430),
    )
    cloud_delta = _math_binary(
        group,
        "MULTIPLY",
        cloud_sin,
        cloud_amp,
        label="雲の半径変化",
        location=(-160, 520),
    )
    cloud_factor = _math_add(group, one, cloud_delta, label="雲形状", location=(40, 520))

    thorn_wave_count = _math_binary(
        group,
        "DIVIDE",
        cloud_perimeter,
        input_node.outputs["山の幅"],
        label="トゲ幅から山数",
        location=(-560, 880),
    )
    thorn_wave_count = _math_binary(
        group,
        "MAXIMUM",
        thorn_wave_count,
        b_value=3.0,
        label="トゲ山数下限",
        location=(-360, 880),
    )
    thorn_angle = _math_add(
        group,
        angle,
        cloud_phase,
        label="トゲズラし角度",
        location=(-160, 880),
    )
    thorn_wave = _math_binary(
        group,
        "MULTIPLY",
        thorn_angle,
        thorn_wave_count,
        label="トゲの波",
        location=(-560, 760),
    )
    thorn_sin = _math_binary(
        group,
        "SINE",
        thorn_wave,
        label="トゲの山",
        location=(-360, 760),
    )
    thorn_abs = _math_binary(
        group,
        "ABSOLUTE",
        thorn_sin,
        label="トゲを外向きにする",
        location=(-160, 760),
    )
    thorn_depth_m = _math_binary(
        group,
        "MULTIPLY",
        input_node.outputs["山の高さ"],
        b_value=0.001,
        label="トゲ深さ mm → m",
        location=(-360, 640),
    )
    thorn_depth = _math_binary(
        group,
        "DIVIDE",
        thorn_depth_m,
        radius_avg,
        label="トゲ深さ率",
        location=(-160, 640),
    )
    thorn_delta = _math_binary(
        group,
        "MULTIPLY",
        thorn_abs,
        thorn_depth,
        label="トゲ半径変化",
        location=(40, 700),
    )
    thorn_factor = _math_add(group, one, thorn_delta, label="トゲ形状", location=(240, 700))

    is_cloud = _shape_compare(group, input_node, 3, label="雲か", location=(40, 360))
    is_fluffy = _shape_compare(group, input_node, 4, label="もやもやか", location=(40, 240))
    cloud_or_fluffy = _node(group, "FunctionNodeBooleanMath", label="雲/もやもや", location=(240, 300))
    cloud_or_fluffy.operation = "OR"
    _link(group, is_cloud, cloud_or_fluffy.inputs[0])
    _link(group, is_fluffy, cloud_or_fluffy.inputs[1])
    factor_after_cloud = _switch_float(
        group,
        cloud_or_fluffy.outputs["Boolean"],
        one,
        cloud_factor,
        label="雲系を適用",
        location=(440, 420),
    )

    is_thorn = _shape_compare(group, input_node, 5, label="トゲか", location=(240, 120))
    is_thorn_curve = _shape_compare(group, input_node, 6, label="トゲ曲線か", location=(240, 0))
    thorn_or_curve = _node(group, "FunctionNodeBooleanMath", label="トゲ系", location=(440, 60))
    thorn_or_curve.operation = "OR"
    _link(group, is_thorn, thorn_or_curve.inputs[0])
    _link(group, is_thorn_curve, thorn_or_curve.inputs[1])
    return _switch_float(
        group,
        thorn_or_curve.outputs["Boolean"],
        factor_after_cloud,
        thorn_factor,
        label="トゲ系を適用",
        location=(640, 360),
    )


def _deform_balloon_mesh(
    group,
    input_node,
    mesh_socket,
    width_half_m,
    height_half_m,
    *,
    z: float,
    label: str,
    location: tuple[float, float],
):
    position = _node(group, "GeometryNodeInputPosition", label=f"{label} 元位置", location=(location[0] - 560, location[1] + 180))
    sep = _separate_xyz(group, position.outputs["Position"], label=f"{label} 位置分解", location=(location[0] - 360, location[1] + 180))
    factor = _balloon_shape_factor(group, input_node, sep.outputs["X"], sep.outputs["Y"], width_half_m, height_half_m)
    sx = _math_binary(group, "MULTIPLY", sep.outputs["X"], width_half_m, label=f"{label} X拡大", location=(location[0] - 160, location[1]))
    sx = _math_binary(group, "MULTIPLY", sx, factor, label=f"{label} X形状", location=(location[0] + 40, location[1]))
    sy = _math_binary(group, "MULTIPLY", sep.outputs["Y"], height_half_m, label=f"{label} Y拡大", location=(location[0] - 160, location[1] - 160))
    sy = _math_binary(group, "MULTIPLY", sy, factor, label=f"{label} Y形状", location=(location[0] + 40, location[1] - 160))
    px = _math_add(group, width_half_m, sx, label=f"{label} X配置", location=(location[0] + 240, location[1]))
    py = _math_add(group, height_half_m, sy, label=f"{label} Y配置", location=(location[0] + 240, location[1] - 160))
    vector = _combine_xyz(group, px, py, z=z, label=f"{label} 座標", location=(location[0] + 440, location[1] - 80))
    set_pos = _node(group, "GeometryNodeSetPosition", label=label, location=(location[0] + 640, location[1] - 80))
    _link(group, mesh_socket, set_pos.inputs["Geometry"])
    _link(group, vector, set_pos.inputs["Position"])
    return set_pos.outputs["Geometry"]


def _balloon_generated_geometry(group, input_node, width_half_m, height_half_m, line_half_m, line_material=None, fill_material=None):
    fill_circle = _node(group, "GeometryNodeMeshCircle", label="塗り元円", location=(-520, 120))
    fill_circle.fill_type = "TRIANGLE_FAN"
    _set_default(fill_circle.inputs["Vertices"], 192)
    _set_default(fill_circle.inputs["Radius"], 1.0)
    fill = _deform_balloon_mesh(
        group,
        input_node,
        fill_circle.outputs["Mesh"],
        width_half_m,
        height_half_m,
        z=-0.001,
        label="塗り形状を生成",
        location=(-240, 120),
    )
    fill = _set_material(group, fill, fill_material, label="塗り素材", location=(620, 120))

    outline_circle = _node(group, "GeometryNodeMeshCircle", label="輪郭元円", location=(-520, -760))
    outline_circle.fill_type = "NONE"
    _set_default(outline_circle.inputs["Vertices"], 192)
    _set_default(outline_circle.inputs["Radius"], 1.0)
    outline_mesh = _deform_balloon_mesh(
        group,
        input_node,
        outline_circle.outputs["Mesh"],
        width_half_m,
        height_half_m,
        z=0.0,
        label="輪郭形状を生成",
        location=(-240, -760),
    )
    mesh_to_curve = _node(group, "GeometryNodeMeshToCurve", label="輪郭を曲線化", location=(620, -760))
    _link(group, outline_mesh, mesh_to_curve.inputs["Mesh"])
    profile = _node(group, "GeometryNodeCurvePrimitiveCircle", label="線幅を生成", location=(620, -1020))
    _set_default(profile.inputs["Resolution"], 8)
    _link(group, line_half_m, profile.inputs["Radius"])
    outline = _node(group, "GeometryNodeCurveToMesh", label="輪郭をメッシュ化", location=(840, -760))
    _set_default(outline.inputs["Fill Caps"], True)
    _link(group, mesh_to_curve.outputs["Curve"], outline.inputs["Curve"])
    _link(group, profile.outputs["Curve"], outline.inputs["Profile Curve"])
    outline = _set_material(group, outline.outputs["Mesh"], line_material, label="線素材", location=(1060, -760))

    join = _node(group, "GeometryNodeJoinGeometry", label="フキダシ生成", location=(1280, -260))
    _link(group, fill, join.inputs["Geometry"])
    _link(group, outline, join.inputs["Geometry"])
    return join.outputs["Geometry"]


def _build_balloon_nodes(group) -> None:
    from . import geometry_nodes_functional

    geometry_nodes_functional.build_balloon_nodes(group, __import__(__name__, fromlist=[""]))


def _radial_line_points(
    group,
    origin_x_m,
    origin_y_m,
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
    base_x = _math_add(group, origin_x_m, width_half_m, label=f"中心X配置 {index}", location=(-720, row - 1520))
    base_y = _math_add(group, origin_y_m, height_half_m, label=f"中心Y配置 {index}", location=(-520, row - 1520))
    sx = _math_add(group, base_x, sx_off, label=f"始点X配置 {index}", location=(-320, row - 1520))
    sy = _math_add(group, base_y, sy_off, label=f"始点Y配置 {index}", location=(-120, row - 1520))
    ex = _math_add(group, base_x, ex_off, label=f"終点X配置 {index}", location=(80, row - 1520))
    ey = _math_add(group, base_y, ey_off, label=f"終点Y配置 {index}", location=(280, row - 1520))
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
    origin_x_m,
    origin_y_m,
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
            origin_x_m,
            origin_y_m,
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


def _instanced_radial_line_geometry(
    group,
    input_node,
    origin_x_m,
    origin_y_m,
    width_half_m,
    height_half_m,
    line_half_m,
    line_material=None,
):
    """本数入力から Geometry Nodes 側で放射線を繰り返し生成する."""
    import math

    points = _node(group, "GeometryNodeMeshLine", label="線の本数", location=(-520, -520))
    _link(group, input_node.outputs["本数"], points.inputs["Count"])
    _set_default(points.inputs["Start Location"], (0.0, 0.0, 0.0))
    _set_default(points.inputs["Offset"], (0.0, 0.0, 0.0))

    index = _node(group, "GeometryNodeInputIndex", label="線番号", location=(-520, -720))
    frac = _math_binary(
        group,
        "DIVIDE",
        index.outputs["Index"],
        input_node.outputs["本数"],
        label="線番号 / 本数",
        location=(-320, -720),
    )
    angle = _math_binary(
        group,
        "MULTIPLY",
        frac,
        b_value=math.tau,
        label="角度",
        location=(-120, -720),
    )
    rotation_rad = _math_binary(
        group,
        "MULTIPLY",
        input_node.outputs["全体回転"],
        b_value=math.pi / 180.0,
        label="全体回転をラジアンへ",
        location=(-120, -900),
    )
    angle_with_rotation = _math_add(
        group,
        angle,
        rotation_rad,
        label="回転済み角度",
        location=(80, -720),
    )
    axis_angle = _node(group, "FunctionNodeAxisAngleToRotation", label="線を回転", location=(280, -720))
    _set_default(axis_angle.inputs["Axis"], (0.0, 0.0, 1.0))
    _link(group, angle_with_rotation, axis_angle.inputs["Angle"])

    radius_sum = _math_add(
        group,
        width_half_m,
        height_half_m,
        label="半径合計",
        location=(-520, -1080),
    )
    radius = _math_binary(
        group,
        "MULTIPLY",
        radius_sum,
        b_value=0.5,
        label="平均半径",
        location=(-320, -1080),
    )
    inner_radius = _math_binary(
        group,
        "MULTIPLY",
        radius,
        b_value=0.12,
        label="始点半径",
        location=(-120, -1080),
    )
    start_vec = _combine_xyz(group, inner_radius, None, z=0.0, label="線始点", location=(80, -1080))
    end_vec = _combine_xyz(group, radius, None, z=0.0, label="線終点", location=(80, -1240))

    line = _node(group, "GeometryNodeCurvePrimitiveLine", label="線の原型", location=(280, -1080))
    _link(group, start_vec, line.inputs["Start"])
    _link(group, end_vec, line.inputs["End"])
    profile = _node(group, "GeometryNodeCurvePrimitiveCircle", label="線幅", location=(280, -1320))
    _set_default(profile.inputs["Resolution"], 8)
    _link(group, line_half_m, profile.inputs["Radius"])
    mesh = _node(group, "GeometryNodeCurveToMesh", label="線をメッシュ化", location=(520, -1080))
    _set_default(mesh.inputs["Fill Caps"], True)
    _link(group, line.outputs["Curve"], mesh.inputs["Curve"])
    _link(group, profile.outputs["Curve"], mesh.inputs["Profile Curve"])
    material = _set_material(
        group,
        mesh.outputs["Mesh"],
        line_material,
        label="線素材",
        location=(760, -1080),
    )

    instance = _node(group, "GeometryNodeInstanceOnPoints", label="線を繰り返し配置", location=(760, -640))
    _link(group, points.outputs["Mesh"], instance.inputs["Points"])
    _link(group, material, instance.inputs["Instance"])
    _link(group, axis_angle.outputs["Rotation"], instance.inputs["Rotation"])
    realize = _node(group, "GeometryNodeRealizeInstances", label="線を実体化", location=(980, -640))
    _link(group, instance.outputs["Instances"], realize.inputs["Geometry"])
    center_x = _math_add(group, origin_x_m, width_half_m, label="中心 X", location=(760, -260))
    center_y = _math_add(group, origin_y_m, height_half_m, label="中心 Y", location=(760, -420))
    translation = _combine_xyz(group, center_x, center_y, z=0.0, label="表示位置", location=(980, -360))
    transform = _node(group, "GeometryNodeTransform", label="効果線を配置", location=(1180, -640))
    _link(group, realize.outputs["Geometry"], transform.inputs["Geometry"])
    _link(group, translation, transform.inputs["Translation"])
    return transform.outputs["Geometry"]


def _effect_fill_geometry(group, origin_x_m, origin_y_m, width_half_m, height_half_m, fill_material=None):
    fill = _node(group, "GeometryNodeMeshCircle", label="終点形状下地", location=(780, 120))
    fill.fill_type = "TRIANGLE_FAN"
    _set_default(fill.inputs["Vertices"], 192)
    _set_default(fill.inputs["Radius"], 1.0)
    scale = _combine_xyz(group, width_half_m, height_half_m, z=1.0, label="下地サイズ", location=(980, 120))
    cx = _math_add(group, origin_x_m, width_half_m, label="下地中心 X", location=(980, -80))
    cy = _math_add(group, origin_y_m, height_half_m, label="下地中心 Y", location=(980, -240))
    translation = _combine_xyz(group, cx, cy, z=-0.001, label="下地位置", location=(1180, -160))
    transform = _node(group, "GeometryNodeTransform", label="下地を配置", location=(1180, 120))
    _link(group, fill.outputs["Mesh"], transform.inputs["Geometry"])
    _link(group, scale, transform.inputs["Scale"])
    _link(group, translation, transform.inputs["Translation"])
    return _set_material(group, transform.outputs["Geometry"], fill_material, label="下地素材", location=(1380, 120))


def _build_effect_line_nodes(group) -> None:
    from . import geometry_nodes_functional

    geometry_nodes_functional.build_effect_line_nodes(group, __import__(__name__, fromlist=[""]))


def _build_generator_nodes(group, kind: str) -> None:
    if kind == "effect_line":
        _build_effect_line_nodes(group)
    else:
        _build_balloon_nodes(group)


def _group_needs_rebuild(group, kind: str) -> bool:
    try:
        if int(group.get(PROP_GROUP_VERSION, 0) or 0) != _GROUP_VERSION:
            return True
    except Exception:  # noqa: BLE001
        return True
    generator_types = {
        "effect_line": {"GeometryNodeCurvePrimitiveLine", "GeometryNodeCurveToMesh", "GeometryNodeSetMaterial"},
        "balloon": {"GeometryNodeMeshCircle", "GeometryNodeCurveToMesh", "GeometryNodeSetMaterial"},
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
    _prune_input_sockets(group, kind)
    _ensure_setting_output_sockets(group, kind)
    _prune_setting_output_sockets(group, kind)
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
        if spec.socket_type in {"NodeSocketObject", "NodeSocketMaterial"}:
            modifier[identifier] = value
        elif spec.socket_type == "NodeSocketBool":
            modifier[identifier] = bool(value)
        elif spec.socket_type == "NodeSocketInt":
            modifier[identifier] = int(round(float(value or 0)))
        elif spec.socket_type == "NodeSocketColor":
            modifier[identifier] = tuple(value or (0.0, 0.0, 0.0, 1.0))
        elif spec.socket_type == "NodeSocketString":
            modifier[identifier] = str(value or "")
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
    material_slots = getattr(getattr(obj, "data", None), "materials", None)
    for name, slot_index in (("線素材", 0), ("塗り素材", 1)):
        socket = identifiers.get(name)
        if not socket:
            continue
        material = None
        try:
            if material_slots is not None and len(material_slots) > slot_index:
                material = material_slots[slot_index]
        except Exception:  # noqa: BLE001
            material = None
        ident, spec = socket
        _set_modifier_value(modifier, ident, spec, material)
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


def effect_field_socket_names() -> dict[str, str]:
    return {field: spec.name for field, spec in _EFFECT_FIELD_SPECS.items()}


def _enum_code(field: str, value: Any) -> int:
    mapping = _ENUM_CODES.get(field)
    if mapping is None:
        return 0
    return int(mapping.get(str(value or ""), 0))


def _socket_value_for_spec(field: str, spec: SocketSpec, value: Any):
    if field in _ENUM_CODES:
        return _enum_code(field, value)
    if spec.socket_type == "NodeSocketBool":
        return bool(value)
    if spec.socket_type == "NodeSocketInt":
        try:
            return int(value)
        except Exception:  # noqa: BLE001
            return int(spec.default or 0)
    if spec.socket_type == "NodeSocketColor":
        try:
            return tuple(float(value[i]) for i in range(4))
        except Exception:  # noqa: BLE001
            return tuple(spec.default or (0.0, 0.0, 0.0, 1.0))
    if spec.socket_type == "NodeSocketString":
        return str(value or "")
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return float(spec.default or 0.0)


def _effect_focus_line_count(params, bounds: tuple[float, float, float, float] | None) -> int:
    import math

    max_count = max(1, int(getattr(params, "max_line_count", 1000) or 1))
    if bounds is None:
        width = height = 40.0
    else:
        width = max(0.001, float(bounds[2] or 0.001))
        height = max(0.001, float(bounds[3] or 0.001))
    if str(getattr(params, "spacing_mode", "distance") or "distance") == "angle":
        step = max(0.1, float(getattr(params, "spacing_angle_deg", 5.0) or 5.0))
        raw = max(3, int(round(360.0 / step)))
    else:
        step = max(0.01, float(getattr(params, "spacing_distance_mm", 0.4) or 0.4))
        rx = width * 0.5
        ry = height * 0.5
        h = ((rx - ry) ** 2) / max((rx + ry) ** 2, 1.0e-9)
        perimeter = math.pi * (rx + ry) * (1.0 + (3.0 * h) / max(10.0 + math.sqrt(max(0.0, 4.0 - 3.0 * h)), 1.0e-9))
        raw = max(3, int(round(perimeter / step)))
    return min(raw, max_count)


def effect_values(params, bounds: tuple[float, float, float, float] | None, seed: int) -> dict[str, Any]:
    if bounds is None:
        x = y = width = height = 0.0
    else:
        x, y, width, height = bounds
    effect_type = str(getattr(params, "effect_type", "") or "")
    if effect_type == "speed":
        line_count = int(getattr(params, "speed_line_count", 0) or 0)
    elif effect_type == "white_outline":
        line_count = int(getattr(params, "white_outline_count", 0) or 0)
    else:
        line_count = _effect_focus_line_count(params, bounds)
    values = {
        "乱数": int(seed),
        "位置 X": float(x or 0.0),
        "位置 Y": float(y or 0.0),
        "幅": float(width or 0.0),
        "高さ": float(height or 0.0),
    }
    for field, spec in _EFFECT_FIELD_SPECS.items():
        raw = getattr(params, field, spec.default) if params is not None else spec.default
        values[spec.name] = _socket_value_for_spec(field, spec, raw)
    values["種類"] = _EFFECT_CODES.get(effect_type, 0)
    values["本数"] = line_count
    return values


def _balloon_tail_values(prefix: str, tail) -> dict[str, Any]:
    return {
        f"{prefix} 種類": _TAIL_TYPE_CODES.get(str(getattr(tail, "type", "") or ""), 0),
        f"{prefix} 方向": float(getattr(tail, "direction_deg", 270.0) if tail is not None else 270.0),
        f"{prefix} 長さ": float(getattr(tail, "length_mm", 6.0) if tail is not None else 6.0),
        f"{prefix} 根元幅": float(getattr(tail, "root_width_mm", 3.0) if tail is not None else 3.0),
        f"{prefix} 先端幅": float(getattr(tail, "tip_width_mm", 0.0) if tail is not None else 0.0),
        f"{prefix} 曲げ": float(getattr(tail, "curve_bend", 0.0) if tail is not None else 0.0),
        f"{prefix} 始点・終点を固定": bool(
            getattr(tail, "custom_points_enabled", False) if tail is not None else False
        ),
        f"{prefix} 始点 X": float(getattr(tail, "start_x_mm", 0.0) if tail is not None else 0.0),
        f"{prefix} 始点 Y": float(getattr(tail, "start_y_mm", 0.0) if tail is not None else 0.0),
        f"{prefix} 終点 X": float(getattr(tail, "end_x_mm", 0.0) if tail is not None else 0.0),
        f"{prefix} 終点 Y": float(getattr(tail, "end_y_mm", 0.0) if tail is not None else 0.0),
    }


def balloon_values(entry) -> dict[str, Any]:
    from . import balloon_shapes

    shape = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
    params = getattr(entry, "shape_params", None)
    tail = None
    tails = getattr(entry, "tails", None)
    if tails is not None and len(tails) > 0:
        tail = tails[0]
    values = {
        "線幅": float(getattr(entry, "line_width_mm", 0.3) or 0.3),
        "塗り不透明度": float(getattr(entry, "fill_opacity", 1.0) or 1.0),
        "幅": float(getattr(entry, "width_mm", 0.0) or 0.0),
        "高さ": float(getattr(entry, "height_mm", 0.0) or 0.0),
        "形状": _SHAPE_CODES.get(shape, 0),
        "カスタム形状名": str(getattr(entry, "custom_preset_name", "") or ""),
        "X": float(getattr(entry, "x_mm", 0.0) or 0.0),
        "Y": float(getattr(entry, "y_mm", 0.0) or 0.0),
        "回転": float(getattr(entry, "rotation_deg", 0.0) or 0.0),
        "中心点 X": float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
        "中心点 Y": float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
        "角丸": bool(getattr(entry, "rounded_corner_enabled", False)),
        "角半径": float(getattr(entry, "rounded_corner_radius_mm", 3.0) or 0.0),
        "線種": _enum_code("line_style", getattr(entry, "line_style", "")),
        "線色": tuple(getattr(entry, "line_color", (0.0, 0.0, 0.0, 1.0))),
        "塗り色": tuple(getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0))),
        "塗りマテリアル": str(getattr(entry, "fill_material_name", "") or ""),
        "塗り輪郭ぼかし": float(getattr(entry, "fill_blur_amount", 0.0) or 0.0),
        "塗りぼかしをディザ化": bool(getattr(entry, "fill_blur_dither", False)),
        "塗りグラデーション": bool(getattr(entry, "fill_gradient_enabled", False)),
        "グラデーション開始色": tuple(getattr(entry, "fill_gradient_start_color", (1.0, 1.0, 1.0, 1.0))),
        "グラデーション終了色": tuple(getattr(entry, "fill_gradient_end_color", (0.82, 0.82, 0.82, 1.0))),
        "グラデーション角度": float(getattr(entry, "fill_gradient_angle_deg", 90.0) or 0.0),
        "外側白フチ": bool(getattr(entry, "outer_white_margin_enabled", False)),
        "外側白フチ幅": float(getattr(entry, "outer_white_margin_width_mm", 1.0) or 0.0),
        "外側白フチ色": tuple(getattr(entry, "outer_white_margin_color", (1.0, 1.0, 1.0, 1.0))),
        "内側白フチ": bool(getattr(entry, "inner_white_margin_enabled", False)),
        "内側白フチ幅": float(getattr(entry, "inner_white_margin_width_mm", 1.0) or 0.0),
        "内側白フチ色": tuple(getattr(entry, "inner_white_margin_color", (1.0, 1.0, 1.0, 1.0))),
        "合成モード": _enum_code("blend_mode", getattr(entry, "blend_mode", "")),
        "水平反転": bool(getattr(entry, "flip_h", False)),
        "垂直反転": bool(getattr(entry, "flip_v", False)),
        "不透明度": float(getattr(entry, "opacity", 1.0) or 1.0),
        "山の幅": float(getattr(params, "cloud_bump_width_mm", 10.0) or 0.0),
        "山の幅 乱れ": float(getattr(params, "cloud_bump_width_jitter", 0.0) or 0.0),
        "山の高さ": float(getattr(params, "cloud_bump_height_mm", 4.0) or 0.0),
        "山の高さ 乱れ": float(getattr(params, "cloud_bump_height_jitter", 0.0) or 0.0),
        "ズラし量 (%)": float(getattr(params, "cloud_offset_percent", 50.0) or 0.0),
        "小山幅 (%)": float(getattr(params, "cloud_sub_width_ratio", 0.0) or 0.0),
        "小山幅 乱れ": float(getattr(params, "cloud_sub_width_jitter", 0.0) or 0.0),
        "小山高 (%)": float(getattr(params, "cloud_sub_height_ratio", 0.0) or 0.0),
        "小山高 乱れ": float(getattr(params, "cloud_sub_height_jitter", 0.0) or 0.0),
        "雲の波数": int(getattr(params, "cloud_wave_count", 12) or 0),
        "波の振幅": float(getattr(params, "cloud_wave_amplitude_mm", 3.0) or 0.0),
        "トゲ数": int(getattr(params, "spike_count", 24) or 0),
        "トゲの深さ": float(getattr(params, "spike_depth_mm", 6.0) or 0.0),
        "トゲのばらつき": float(getattr(params, "spike_jitter", 0.2) or 0.0),
        "しっぽ数": int(len(tails) if tails is not None else 0),
        "しっぽ 種類": _TAIL_TYPE_CODES.get(str(getattr(tail, "type", "") or ""), 0),
        "しっぽ 方向": float(getattr(tail, "direction_deg", 270.0) if tail is not None else 270.0),
        "しっぽ 長さ": float(getattr(tail, "length_mm", 6.0) if tail is not None else 6.0),
        "しっぽ 根元幅": float(getattr(tail, "root_width_mm", 3.0) if tail is not None else 3.0),
        "しっぽ 先端幅": float(getattr(tail, "tip_width_mm", 0.0) if tail is not None else 0.0),
        "しっぽ 曲げ": float(getattr(tail, "curve_bend", 0.0) if tail is not None else 0.0),
        "しっぽ 始点・終点を固定": bool(getattr(tail, "custom_points_enabled", False) if tail is not None else False),
        "しっぽ 始点 X": float(getattr(tail, "start_x_mm", 0.0) if tail is not None else 0.0),
        "しっぽ 始点 Y": float(getattr(tail, "start_y_mm", 0.0) if tail is not None else 0.0),
        "しっぽ 終点 X": float(getattr(tail, "end_x_mm", 0.0) if tail is not None else 0.0),
        "しっぽ 終点 Y": float(getattr(tail, "end_y_mm", 0.0) if tail is not None else 0.0),
    }
    for index in range(_BALLOON_TAIL_SOCKET_COUNT):
        item = tails[index] if tails is not None and index < len(tails) else None
        values.update(_balloon_tail_values(f"しっぽ{index + 1}", item))
    return values
