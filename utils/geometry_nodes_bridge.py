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
_GROUP_VERSION = 21
_BALLOON_TAIL_SOCKET_COUNT = 8
_SETTING_OUTPUT_PREFIX = "設定接続確認: "
_COMMON_SHAPE_GROUP_NAME = f"{GROUP_PREFIX}CommonCloudThornShape"
_COMMON_SHAPE_OUTPUT_NAME = "形状係数"


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
    SocketSpec("始点コマ枠オブジェクト", "NodeSocketObject", None),
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

_COMMON_SHAPE_INPUT_SOCKETS = (
    SocketSpec("形状", "NodeSocketInt", 2),
    SocketSpec("角度", "NodeSocketFloat", 0.0),
    SocketSpec("平均半径", "NodeSocketFloat", 0.01),
    SocketSpec("周長", "NodeSocketFloat", 60.0),
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
)


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
        if spec.socket_type in {"NodeSocketMaterial", "NodeSocketObject"}:
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
        if spec.socket_type not in {"NodeSocketMaterial", "NodeSocketObject"}
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


def _compare_int_socket(group, source_socket, value: int, *, label: str, location: tuple[float, float]):
    compare = _node(group, "FunctionNodeCompare", label=label, location=location)
    compare.data_type = "INT"
    compare.operation = "EQUAL"
    _set_default(_socket_by_identifier(compare.inputs, "B_INT"), int(value))
    _link(group, source_socket, _socket_by_identifier(compare.inputs, "A_INT"))
    return compare.outputs["Result"]


def _compare_float_socket(
    group,
    source_socket,
    value: float,
    *,
    operation: str = "EQUAL",
    label: str,
    location: tuple[float, float],
):
    compare = _node(group, "FunctionNodeCompare", label=label, location=location)
    compare.data_type = "FLOAT"
    compare.operation = operation
    _set_default(_socket_by_identifier(compare.inputs, "B"), float(value))
    _link(group, source_socket, _socket_by_identifier(compare.inputs, "A"))
    return compare.outputs["Result"]


def _compare_float_sockets(
    group,
    a_socket,
    b_socket,
    *,
    operation: str = "LESS_THAN",
    label: str,
    location: tuple[float, float],
):
    compare = _node(group, "FunctionNodeCompare", label=label, location=location)
    compare.data_type = "FLOAT"
    compare.operation = operation
    _link(group, a_socket, _socket_by_identifier(compare.inputs, "A"))
    _link(group, b_socket, _socket_by_identifier(compare.inputs, "B"))
    return compare.outputs["Result"]


def _switch_float(group, switch_socket, false_socket, true_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeSwitch", label=label, location=location)
    node.input_type = "FLOAT"
    _link(group, switch_socket, node.inputs["Switch"])
    _link(group, false_socket, node.inputs["False"])
    _link(group, true_socket, node.inputs["True"])
    return node.outputs["Output"]


def _switch_int(group, switch_socket, false_socket, true_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "GeometryNodeSwitch", label=label, location=location)
    node.input_type = "INT"
    _link(group, switch_socket, node.inputs["Switch"])
    _link(group, false_socket, node.inputs["False"])
    _link(group, true_socket, node.inputs["True"])
    return node.outputs["Output"]


def _constant_float(group, value: float, *, label: str, location: tuple[float, float]):
    node = _node(group, "ShaderNodeValue", label=label, location=location)
    _set_default(node.outputs["Value"], float(value))
    return node.outputs["Value"]


def _float_to_int(group, source_socket, *, label: str, location: tuple[float, float], mode: str = "ROUND"):
    node = _node(group, "FunctionNodeFloatToInt", label=label, location=location)
    node.rounding_mode = mode
    _link(group, source_socket, node.inputs["Float"])
    return node.outputs["Integer"]


def _boolean_or(group, a_socket, b_socket, *, label: str, location: tuple[float, float]):
    node = _node(group, "FunctionNodeBooleanMath", label=label, location=location)
    node.operation = "OR"
    _link(group, a_socket, node.inputs[0])
    _link(group, b_socket, node.inputs[1])
    return node.outputs["Boolean"]


def _build_common_shape_group(group) -> None:
    import math

    _clear_nodes(group)
    input_node, output_node = _group_input_output(group)
    shape_socket = input_node.outputs["形状"]
    angle_socket = input_node.outputs["角度"]
    radius = _math_binary(group, "MAXIMUM", input_node.outputs["平均半径"], b_value=0.000001, label="平均半径下限", location=(-760, 240))
    perimeter = _math_binary(group, "MAXIMUM", input_node.outputs["周長"], b_value=0.001, label="周長下限", location=(-760, 80))
    one = _constant_float(group, 1.0, label="通常形状", location=(-760, -80))

    width_noise = _math_binary(
        group,
        "SINE",
        _math_binary(group, "MULTIPLY", angle_socket, b_value=3.0, label="山幅乱れ角", location=(-760, -260)),
        label="山幅乱れ波",
        location=(-560, -260),
    )
    width_factor = _math_add(
        group,
        one,
        _math_binary(group, "MULTIPLY", width_noise, input_node.outputs["山の幅 乱れ"], label="山幅乱れ量", location=(-360, -260)),
        label="山幅係数",
        location=(-160, -260),
    )
    width_factor = _math_binary(group, "MAXIMUM", width_factor, b_value=0.05, label="山幅係数下限", location=(40, -260))
    effective_width = _math_binary(group, "MULTIPLY", input_node.outputs["山の幅"], width_factor, label="有効山幅", location=(-560, 80))
    effective_width = _math_binary(group, "MAXIMUM", effective_width, b_value=0.001, label="山幅下限", location=(-360, 80))
    cloud_count = _math_binary(group, "DIVIDE", perimeter, effective_width, label="山幅から山数", location=(-160, 80))
    cloud_count = _math_binary(group, "MAXIMUM", cloud_count, b_value=3.0, label="山数下限", location=(40, 80))

    fluffy_count = _math_binary(group, "MAXIMUM", input_node.outputs["雲の波数"], b_value=3.0, label="もやもや波数下限", location=(-160, -80))
    is_fluffy = _compare_int_socket(group, shape_socket, 4, label="もやもやか", location=(40, -80))
    wave_count = _switch_float(group, is_fluffy, cloud_count, fluffy_count, label="山数選択", location=(240, 20))
    cloud_height = _switch_float(group, is_fluffy, input_node.outputs["山の高さ"], input_node.outputs["波の振幅"], label="高さ選択", location=(240, -140))

    phase = _math_binary(group, "MULTIPLY", input_node.outputs["ズラし量 (%)"], b_value=math.tau / 100.0, label="ズラし量", location=(-160, 300))
    phased_angle = _math_add(group, angle_socket, phase, label="位相付き角度", location=(40, 300))
    wave = _math_binary(
        group,
        "SINE",
        _math_binary(group, "MULTIPLY", phased_angle, wave_count, label="山角度", location=(240, 300)),
        label="山波",
        location=(440, 300),
    )

    height_noise = _math_binary(
        group,
        "SINE",
        _math_binary(group, "MULTIPLY", angle_socket, b_value=5.0, label="山高乱れ角", location=(240, -320)),
        label="山高乱れ波",
        location=(440, -320),
    )
    height_factor = _math_add(
        group,
        one,
        _math_binary(group, "MULTIPLY", height_noise, input_node.outputs["山の高さ 乱れ"], label="山高乱れ量", location=(640, -320)),
        label="山高係数",
        location=(840, -320),
    )
    height_factor = _math_binary(group, "MAXIMUM", height_factor, b_value=0.0, label="山高係数下限", location=(1040, -320))
    effective_height = _math_binary(group, "MULTIPLY", cloud_height, height_factor, label="有効山高", location=(440, -140))
    amp = _math_binary(
        group,
        "DIVIDE",
        _math_binary(group, "MULTIPLY", effective_height, b_value=0.001, label="山高m", location=(640, -140)),
        radius,
        label="山高率",
        location=(840, -140),
    )

    sub_width_noise = _math_binary(
        group,
        "SINE",
        _math_binary(group, "MULTIPLY", angle_socket, b_value=11.0, label="小山幅乱れ角", location=(440, -520)),
        label="小山幅乱れ波",
        location=(640, -520),
    )
    sub_width_factor = _math_add(
        group,
        one,
        _math_binary(group, "MULTIPLY", sub_width_noise, input_node.outputs["小山幅 乱れ"], label="小山幅乱れ量", location=(840, -520)),
        label="小山幅係数",
        location=(1040, -520),
    )
    sub_width_base = _math_add(
        group,
        _constant_float(group, 2.0, label="小山基準", location=(640, -700)),
        _math_binary(group, "MULTIPLY", input_node.outputs["小山幅 (%)"], b_value=0.01, label="小山幅率", location=(840, -700)),
        label="小山幅",
        location=(1040, -700),
    )
    sub_count = _math_binary(group, "MULTIPLY", wave_count, _math_binary(group, "MULTIPLY", sub_width_base, sub_width_factor, label="小山幅反映", location=(1240, -620)), label="小山数", location=(1440, -620))
    sub_wave = _math_binary(
        group,
        "SINE",
        _math_binary(group, "MULTIPLY", phased_angle, sub_count, label="小山角度", location=(1640, -620)),
        label="小山波",
        location=(1840, -620),
    )
    sub_height_noise = _math_binary(
        group,
        "SINE",
        _math_binary(group, "MULTIPLY", angle_socket, b_value=7.0, label="小山高乱れ角", location=(1440, -820)),
        label="小山高乱れ波",
        location=(1640, -820),
    )
    sub_height_ratio = _math_binary(group, "MULTIPLY", input_node.outputs["小山高 (%)"], b_value=0.01, label="小山高率", location=(1840, -820))
    sub_height_factor = _math_add(
        group,
        sub_height_ratio,
        _math_binary(group, "MULTIPLY", sub_height_noise, input_node.outputs["小山高 乱れ"], label="小山高乱れ量", location=(2040, -820)),
        label="小山高係数",
        location=(2240, -820),
    )
    sub_amp = _math_binary(group, "MULTIPLY", amp, sub_height_factor, label="小山高", location=(2040, -620))
    cloud_delta = _math_add(
        group,
        _math_binary(group, "MULTIPLY", wave, amp, label="雲変化", location=(1040, 140)),
        _math_binary(group, "MULTIPLY", sub_wave, sub_amp, label="小山変化", location=(2240, -620)),
        label="雲合成",
        location=(2440, 0),
    )
    cloud_factor = _math_add(group, one, cloud_delta, label="雲形状", location=(2640, 0))

    spike_count = _math_binary(group, "MAXIMUM", input_node.outputs["トゲ数"], b_value=3.0, label="トゲ数下限", location=(840, 500))
    spike_wave_angle = _math_binary(group, "MULTIPLY", phased_angle, spike_count, label="トゲ角度", location=(1040, 500))
    spike_wave = _math_binary(group, "ABSOLUTE", _math_binary(group, "SINE", spike_wave_angle, label="トゲ波", location=(1240, 500)), label="直線トゲ波", location=(1440, 500))
    spike_curve_wave = _math_add(
        group,
        _math_binary(group, "MULTIPLY", _math_binary(group, "COSINE", spike_wave_angle, label="曲線トゲcos", location=(1240, 660)), b_value=-0.5, label="曲線トゲ反転", location=(1440, 660)),
        _constant_float(group, 0.5, label="曲線トゲ基準", location=(1440, 800)),
        label="曲線トゲ波",
        location=(1640, 660),
    )
    is_thorn_curve = _compare_int_socket(group, shape_socket, 6, label="トゲ曲線か", location=(1840, 660))
    thorn_wave = _switch_float(group, is_thorn_curve, spike_wave, spike_curve_wave, label="トゲ波選択", location=(2040, 560))
    spike_noise = _math_binary(
        group,
        "SINE",
        _math_binary(group, "MULTIPLY", angle_socket, b_value=17.0, label="トゲ乱れ角", location=(1440, 300)),
        label="トゲ乱れ波",
        location=(1640, 300),
    )
    spike_jitter = _math_add(
        group,
        one,
        _math_binary(group, "MULTIPLY", spike_noise, input_node.outputs["トゲのばらつき"], label="トゲ乱れ量", location=(1840, 300)),
        label="トゲ乱れ係数",
        location=(2040, 300),
    )
    spike_jitter = _math_binary(group, "MAXIMUM", spike_jitter, b_value=0.0, label="トゲ乱れ下限", location=(2240, 300))
    spike_amp = _math_binary(
        group,
        "DIVIDE",
        _math_binary(group, "MULTIPLY", input_node.outputs["トゲの深さ"], b_value=0.001, label="トゲ深さm", location=(2040, 500)),
        radius,
        label="トゲ深さ率",
        location=(2240, 500),
    )
    thorn_factor = _math_add(
        group,
        one,
        _math_binary(group, "MULTIPLY", thorn_wave, _math_binary(group, "MULTIPLY", spike_amp, spike_jitter, label="トゲ深さ反映", location=(2440, 420)), label="トゲ変化", location=(2640, 420)),
        label="トゲ形状",
        location=(2840, 420),
    )

    is_cloud = _compare_int_socket(group, shape_socket, 3, label="雲か", location=(2840, -180))
    cloud_like = _boolean_or(group, is_cloud, is_fluffy, label="雲系", location=(3040, -80))
    after_cloud = _switch_float(group, cloud_like, one, cloud_factor, label="雲系切替", location=(3240, -80))
    is_thorn = _compare_int_socket(group, shape_socket, 5, label="トゲか", location=(3040, 320))
    thorn_like = _boolean_or(group, is_thorn, is_thorn_curve, label="トゲ系", location=(3240, 420))
    factor = _switch_float(group, thorn_like, after_cloud, thorn_factor, label="形状切替", location=(3440, 120))
    factor = _math_binary(group, "MAXIMUM", factor, b_value=0.001, label="形状下限", location=(3640, 120))
    _link(group, factor, output_node.inputs[_COMMON_SHAPE_OUTPUT_NAME])
    group[PROP_GROUP_VERSION] = _GROUP_VERSION


def _ensure_common_shape_group():
    group = bpy.data.node_groups.get(_COMMON_SHAPE_GROUP_NAME)
    if group is None:
        group = bpy.data.node_groups.new(_COMMON_SHAPE_GROUP_NAME, "GeometryNodeTree")
    for spec in _COMMON_SHAPE_INPUT_SOCKETS:
        _ensure_socket(group, spec)
    _ensure_socket(group, SocketSpec(_COMMON_SHAPE_OUTPUT_NAME, "NodeSocketFloat", 1.0), in_out="OUTPUT")
    if int(group.get(PROP_GROUP_VERSION, -1)) != _GROUP_VERSION or not group.nodes:
        _build_common_shape_group(group)
    return group


def _common_shape_factor(
    group,
    *,
    shape_socket,
    angle_socket,
    radius_socket,
    perimeter_socket,
    width_socket,
    width_jitter_socket,
    height_socket,
    height_jitter_socket,
    offset_socket,
    sub_width_socket,
    sub_width_jitter_socket,
    sub_height_socket,
    sub_height_jitter_socket,
    fluffy_count_socket,
    fluffy_amplitude_socket,
    spike_count_socket,
    spike_depth_socket,
    spike_jitter_socket,
    label: str,
    location: tuple[float, float],
):
    node = _node(group, "GeometryNodeGroup", label=label, location=location)
    node.node_tree = _ensure_common_shape_group()
    links = {
        "形状": shape_socket,
        "角度": angle_socket,
        "平均半径": radius_socket,
        "周長": perimeter_socket,
        "山の幅": width_socket,
        "山の幅 乱れ": width_jitter_socket,
        "山の高さ": height_socket,
        "山の高さ 乱れ": height_jitter_socket,
        "ズラし量 (%)": offset_socket,
        "小山幅 (%)": sub_width_socket,
        "小山幅 乱れ": sub_width_jitter_socket,
        "小山高 (%)": sub_height_socket,
        "小山高 乱れ": sub_height_jitter_socket,
        "雲の波数": fluffy_count_socket,
        "波の振幅": fluffy_amplitude_socket,
        "トゲ数": spike_count_socket,
        "トゲの深さ": spike_depth_socket,
        "トゲのばらつき": spike_jitter_socket,
    }
    for name, socket in links.items():
        if socket is not None and name in node.inputs:
            _link(group, socket, node.inputs[name])
    return node.outputs[_COMMON_SHAPE_OUTPUT_NAME]


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
    fill = _ellipse_fill(group, input_node, width_half_m, height_half_m, z=-0.00002)
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
    return _common_shape_factor(
        group,
        shape_socket=input_node.outputs["形状"],
        angle_socket=angle,
        radius_socket=radius_avg,
        perimeter_socket=cloud_perimeter,
        width_socket=input_node.outputs["山の幅"],
        width_jitter_socket=input_node.outputs["山の幅 乱れ"],
        height_socket=input_node.outputs["山の高さ"],
        height_jitter_socket=input_node.outputs["山の高さ 乱れ"],
        offset_socket=input_node.outputs["ズラし量 (%)"],
        sub_width_socket=input_node.outputs["小山幅 (%)"],
        sub_width_jitter_socket=input_node.outputs["小山幅 乱れ"],
        sub_height_socket=input_node.outputs["小山高 (%)"],
        sub_height_jitter_socket=input_node.outputs["小山高 乱れ"],
        fluffy_count_socket=input_node.outputs["雲の波数"],
        fluffy_amplitude_socket=input_node.outputs["波の振幅"],
        spike_count_socket=input_node.outputs["トゲ数"],
        spike_depth_socket=input_node.outputs["トゲの深さ"],
        spike_jitter_socket=input_node.outputs["トゲのばらつき"],
        label="共通 雲・もやもや・トゲ形状",
        location=(-160, 520),
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
        z=-0.00002,
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


def _radial_rect_metric_socket(group, rx, ry, *, location: tuple[float, float]):
    """旧B-Nameの距離指定に近い矩形枠の半径積算長をノード内で算出する。"""
    import math

    rx2 = _math_binary(group, "MULTIPLY", rx, rx, label="矩形密度 横二乗", location=location)
    ry2 = _math_binary(group, "MULTIPLY", ry, ry, label="矩形密度 縦二乗", location=(location[0], location[1] - 140))
    diag = _math_binary(
        group,
        "SQRT",
        _math_add(group, rx2, ry2, label="矩形密度 対角二乗", location=(location[0] + 200, location[1] - 70)),
        label="矩形密度 対角",
        location=(location[0] + 400, location[1] - 70),
    )
    ratio_x = _math_binary(
        group,
        "DIVIDE",
        _math_add(group, diag, ry, label="矩形密度 横対数分子", location=(location[0] + 600, location[1] + 80)),
        rx,
        label="矩形密度 横対数比",
        location=(location[0] + 800, location[1] + 80),
    )
    ratio_y = _math_binary(
        group,
        "DIVIDE",
        _math_add(group, diag, rx, label="矩形密度 縦対数分子", location=(location[0] + 600, location[1] - 220)),
        ry,
        label="矩形密度 縦対数比",
        location=(location[0] + 800, location[1] - 220),
    )
    log_x = _math_binary(
        group,
        "LOGARITHM",
        ratio_x,
        b_value=math.e,
        label="矩形密度 横対数",
        location=(location[0] + 1000, location[1] + 80),
    )
    log_y = _math_binary(
        group,
        "LOGARITHM",
        ratio_y,
        b_value=math.e,
        label="矩形密度 縦対数",
        location=(location[0] + 1000, location[1] - 220),
    )
    part_x = _math_binary(group, "MULTIPLY", rx, log_x, label="矩形密度 横成分", location=(location[0] + 1200, location[1] + 80))
    part_y = _math_binary(group, "MULTIPLY", ry, log_y, label="矩形密度 縦成分", location=(location[0] + 1200, location[1] - 220))
    return _math_binary(
        group,
        "MULTIPLY",
        _math_add(group, part_x, part_y, label="矩形密度 四半周", location=(location[0] + 1400, location[1] - 70)),
        b_value=4.0,
        label="矩形密度 半径積算",
        location=(location[0] + 1600, location[1] - 70),
    )


def _radial_ellipse_metric_socket(group, rx, ry, *, location: tuple[float, float]):
    """楕円密度基準の半径積算長を、ノードだけで軽量近似する。"""
    import math

    product = _math_binary(group, "MULTIPLY", rx, ry, label="楕円密度 半径積", location=location)
    product = _math_binary(group, "MAXIMUM", product, b_value=0.000000000001, label="楕円密度 半径積下限", location=(location[0] + 200, location[1]))
    geom_mean = _math_binary(group, "SQRT", product, label="楕円密度 幾何平均", location=(location[0] + 400, location[1]))
    base = _math_binary(
        group,
        "MULTIPLY",
        geom_mean,
        b_value=math.tau,
        label="楕円密度 基準積算",
        location=(location[0] + 600, location[1]),
    )
    radius_sum = _math_add(group, rx, ry, label="楕円密度 半径和", location=(location[0] + 200, location[1] - 180))
    radius_sum = _math_binary(group, "MAXIMUM", radius_sum, b_value=0.000001, label="楕円密度 半径和下限", location=(location[0] + 400, location[1] - 180))
    radius_diff = _math_binary(
        group,
        "ABSOLUTE",
        _math_binary(group, "SUBTRACT", rx, ry, label="楕円密度 半径差", location=(location[0] + 200, location[1] - 360)),
        label="楕円密度 半径差絶対",
        location=(location[0] + 400, location[1] - 360),
    )
    aspect = _math_binary(group, "DIVIDE", radius_diff, radius_sum, label="楕円密度 縦横差率", location=(location[0] + 600, location[1] - 260))
    aspect2 = _math_binary(group, "MULTIPLY", aspect, aspect, label="楕円密度 縦横差率二乗", location=(location[0] + 800, location[1] - 260))
    aspect4 = _math_binary(group, "MULTIPLY", aspect2, aspect2, label="楕円密度 縦横差率四乗", location=(location[0] + 1000, location[1] - 260))
    factor = _math_binary(
        group,
        "SUBTRACT",
        _constant_float(group, 1.0, label="楕円密度 補正基準", location=(location[0] + 800, location[1] - 520)),
        _math_binary(group, "MULTIPLY", aspect2, b_value=0.23, label="楕円密度 二乗補正", location=(location[0] + 1000, location[1] - 420)),
        label="楕円密度 補正一段",
        location=(location[0] + 1200, location[1] - 420),
    )
    factor = _math_binary(
        group,
        "SUBTRACT",
        factor,
        _math_binary(group, "MULTIPLY", aspect4, b_value=0.20, label="楕円密度 四乗補正", location=(location[0] + 1200, location[1] - 600)),
        label="楕円密度 補正",
        location=(location[0] + 1400, location[1] - 500),
    )
    factor = _math_binary(group, "MAXIMUM", factor, b_value=0.2, label="楕円密度 補正下限", location=(location[0] + 1600, location[1] - 500))
    return _math_binary(group, "MULTIPLY", base, factor, label="楕円密度 半径積算", location=(location[0] + 1800, location[1] - 120))


def _focus_line_count_socket(group, input_node, width_half_m, height_half_m):
    """集中線の本数を Geometry Nodes 内で算出する。"""
    import math

    is_angle = _compare_int_socket(group, input_node.outputs["線の間隔"], 1, label="角度指定か", location=(-760, -240))
    angle_step = _math_binary(
        group,
        "MAXIMUM",
        input_node.outputs["線の間隔 (角度)"],
        b_value=0.1,
        label="角度間隔下限",
        location=(-560, -240),
    )
    angle_count = _math_binary(
        group,
        "DIVIDE",
        _constant_float(group, 360.0, label="360度", location=(-560, -390)),
        angle_step,
        label="角度から本数",
        location=(-360, -240),
    )

    rx = _math_binary(group, "MAXIMUM", width_half_m, b_value=0.000001, label="横半径下限", location=(-760, -560))
    ry = _math_binary(group, "MAXIMUM", height_half_m, b_value=0.000001, label="縦半径下限", location=(-760, -720))
    rect_perimeter = _radial_rect_metric_socket(group, rx, ry, location=(-560, -620))
    ellipse_perimeter = _radial_ellipse_metric_socket(group, rx, ry, location=(-560, -1160))
    basis_is_ellipse = _compare_int_socket(group, input_node.outputs["密度基準"], 3, label="密度基準 楕円", location=(-160, -840))
    basis_is_round = _compare_int_socket(group, input_node.outputs["密度基準"], 2, label="密度基準 角丸", location=(-160, -700))
    round_amount = _math_binary(
        group,
        "MULTIPLY",
        input_node.outputs["角丸率 (%)"],
        b_value=0.01,
        label="角丸率",
        location=(-160, -560),
    )
    round_delta = _math_binary(
        group,
        "MULTIPLY",
        _math_binary(group, "SUBTRACT", ellipse_perimeter, rect_perimeter, label="周長差", location=(40, -720)),
        round_amount,
        label="角丸周長差",
        location=(240, -640),
    )
    rounded_perimeter = _math_add(group, rect_perimeter, round_delta, label="角丸周長", location=(440, -640))
    basis_perimeter = _switch_float(
        group,
        basis_is_round,
        rect_perimeter,
        rounded_perimeter,
        label="角丸密度基準",
        location=(640, -640),
    )
    basis_perimeter = _switch_float(
        group,
        basis_is_ellipse,
        basis_perimeter,
        ellipse_perimeter,
        label="楕円密度基準",
        location=(840, -640),
    )
    distance_step = _math_binary(
        group,
        "MAXIMUM",
        _math_binary(
            group,
            "MULTIPLY",
            input_node.outputs["線の間隔 (距離)"],
            b_value=0.001,
            label="距離間隔 mm→m",
            location=(-160, -980),
        ),
        b_value=0.00001,
        label="距離間隔下限",
        location=(40, -980),
    )
    distance_count = _math_binary(
        group,
        "DIVIDE",
        basis_perimeter,
        distance_step,
        label="距離から本数（密度補正込み）",
        location=(1240, -780),
    )
    raw_count = _switch_float(group, is_angle, distance_count, angle_count, label="間隔方式で本数", location=(1440, -620))
    clamped = _math_binary(group, "MINIMUM", raw_count, input_node.outputs["本数"], label="最大本数", location=(1640, -620))
    clamped = _math_binary(group, "MAXIMUM", clamped, b_value=3.0, label="本数下限", location=(1840, -620))
    return _float_to_int(group, clamped, label="集中線本数", location=(2040, -620))


def _blend_float(group, a_socket, b_socket, factor_socket, *, label: str, location: tuple[float, float]):
    delta = _math_binary(
        group,
        "SUBTRACT",
        b_socket,
        a_socket,
        label=f"{label} 差分",
        location=(location[0], location[1] + 120),
    )
    scaled = _math_binary(
        group,
        "MULTIPLY",
        delta,
        factor_socket,
        label=f"{label} 率",
        location=(location[0] + 180, location[1] + 120),
    )
    return _math_add(group, a_socket, scaled, label=label, location=(location[0] + 360, location[1]))


def _density_ellipse_point(group, frac, rx, ry, *, label: str, location: tuple[float, float]):
    import math

    angle = _math_binary(
        group,
        "MULTIPLY",
        frac,
        b_value=math.tau,
        label=f"{label} 周回",
        location=location,
    )
    cos_socket = _math_binary(group, "COSINE", angle, label=f"{label} cos", location=(location[0] + 180, location[1] + 80))
    sin_socket = _math_binary(group, "SINE", angle, label=f"{label} sin", location=(location[0] + 180, location[1] - 80))
    x_socket = _math_binary(group, "MULTIPLY", cos_socket, rx, label=f"{label} X", location=(location[0] + 380, location[1] + 80))
    y_socket = _math_binary(group, "MULTIPLY", sin_socket, ry, label=f"{label} Y", location=(location[0] + 380, location[1] - 80))
    return x_socket, y_socket


def _density_rect_point(group, frac, rx, ry, *, label: str, location: tuple[float, float]):
    perimeter = _math_binary(
        group,
        "MULTIPLY",
        _math_add(group, rx, ry, label=f"{label} 半径和", location=location),
        b_value=4.0,
        label=f"{label} 周長",
        location=(location[0] + 200, location[1]),
    )
    p = _math_binary(group, "MULTIPLY", frac, perimeter, label=f"{label} 距離", location=(location[0] + 400, location[1]))
    two_rx = _math_binary(group, "MULTIPLY", rx, b_value=2.0, label=f"{label} 横幅", location=(location[0] + 400, location[1] + 180))
    three_ry = _math_binary(group, "MULTIPLY", ry, b_value=3.0, label=f"{label} 縦3", location=(location[0] + 400, location[1] - 180))
    limit1 = ry
    limit2 = _math_add(group, ry, two_rx, label=f"{label} 区切り2", location=(location[0] + 600, location[1] + 120))
    limit3 = _math_add(group, three_ry, two_rx, label=f"{label} 区切り3", location=(location[0] + 600, location[1] - 40))
    limit4 = _math_add(
        group,
        three_ry,
        _math_binary(group, "MULTIPLY", rx, b_value=4.0, label=f"{label} 横4", location=(location[0] + 600, location[1] - 220)),
        label=f"{label} 区切り4",
        location=(location[0] + 800, location[1] - 160),
    )
    is1 = _compare_float_sockets(group, p, limit1, label=f"{label} 右上辺", location=(location[0] + 800, location[1] + 220))
    is2 = _compare_float_sockets(group, p, limit2, label=f"{label} 上辺", location=(location[0] + 800, location[1] + 80))
    is3 = _compare_float_sockets(group, p, limit3, label=f"{label} 左辺", location=(location[0] + 800, location[1] - 60))
    is4 = _compare_float_sockets(group, p, limit4, label=f"{label} 下辺", location=(location[0] + 1000, location[1] - 160))

    neg_rx = _math_binary(group, "MULTIPLY", rx, b_value=-1.0, label=f"{label} -X", location=(location[0] + 1000, location[1] + 220))
    neg_ry = _math_binary(group, "MULTIPLY", ry, b_value=-1.0, label=f"{label} -Y", location=(location[0] + 1000, location[1] + 60))

    x1, y1 = rx, p
    p2 = _math_binary(group, "SUBTRACT", p, ry, label=f"{label} 上距離", location=(location[0] + 1000, location[1] - 20))
    x2 = _math_binary(group, "SUBTRACT", rx, p2, label=f"{label} 上X", location=(location[0] + 1200, location[1] - 20))
    y2 = ry
    p3 = _math_binary(group, "SUBTRACT", p, limit2, label=f"{label} 左距離", location=(location[0] + 1000, location[1] - 300))
    x3 = neg_rx
    y3 = _math_binary(group, "SUBTRACT", ry, p3, label=f"{label} 左Y", location=(location[0] + 1200, location[1] - 300))
    p4 = _math_binary(group, "SUBTRACT", p, limit3, label=f"{label} 下距離", location=(location[0] + 1000, location[1] - 460))
    x4 = _math_add(group, neg_rx, p4, label=f"{label} 下X", location=(location[0] + 1200, location[1] - 460))
    y4 = neg_ry
    p5 = _math_binary(group, "SUBTRACT", p, limit4, label=f"{label} 右下距離", location=(location[0] + 1000, location[1] - 620))
    x5 = rx
    y5 = _math_add(group, neg_ry, p5, label=f"{label} 右下Y", location=(location[0] + 1200, location[1] - 620))

    x45 = _switch_float(group, is4, x5, x4, label=f"{label} X4", location=(location[0] + 1400, location[1] - 460))
    y45 = _switch_float(group, is4, y5, y4, label=f"{label} Y4", location=(location[0] + 1400, location[1] - 620))
    x345 = _switch_float(group, is3, x45, x3, label=f"{label} X3", location=(location[0] + 1600, location[1] - 300))
    y345 = _switch_float(group, is3, y45, y3, label=f"{label} Y3", location=(location[0] + 1600, location[1] - 460))
    x2345 = _switch_float(group, is2, x345, x2, label=f"{label} X2", location=(location[0] + 1800, location[1] - 140))
    y2345 = _switch_float(group, is2, y345, y2, label=f"{label} Y2", location=(location[0] + 1800, location[1] - 300))
    x = _switch_float(group, is1, x2345, x1, label=f"{label} X", location=(location[0] + 2000, location[1] + 20))
    y = _switch_float(group, is1, y2345, y1, label=f"{label} Y", location=(location[0] + 2000, location[1] - 140))
    return x, y


def _focus_density_angle(group, input_node, frac, rx, ry, uniform_angle, *, location: tuple[float, float]):
    is_distance = _compare_int_socket(group, input_node.outputs["線の間隔"], 2, label="距離指定か", location=location)
    rect_x, rect_y = _density_rect_point(group, frac, rx, ry, label="密度基準枠", location=(location[0], location[1] - 260))
    ellipse_x, ellipse_y = _density_ellipse_point(group, frac, rx, ry, label="密度基準楕円", location=(location[0], location[1] - 1320))
    round_amount = _math_binary(
        group,
        "MULTIPLY",
        input_node.outputs["角丸率 (%)"],
        b_value=0.01,
        label="密度角丸率",
        location=(location[0] + 420, location[1] - 1520),
    )
    round_amount = _math_binary(group, "MINIMUM", round_amount, b_value=1.0, label="密度角丸率上限", location=(location[0] + 620, location[1] - 1520))
    round_amount = _math_binary(group, "MAXIMUM", round_amount, b_value=0.0, label="密度角丸率下限", location=(location[0] + 820, location[1] - 1520))
    rounded_x = _blend_float(group, rect_x, ellipse_x, round_amount, label="角丸密度X", location=(location[0] + 2260, location[1] - 720))
    rounded_y = _blend_float(group, rect_y, ellipse_y, round_amount, label="角丸密度Y", location=(location[0] + 2260, location[1] - 920))
    basis_is_round = _compare_int_socket(group, input_node.outputs["密度基準"], 2, label="密度角度 角丸", location=(location[0] + 2640, location[1] - 520))
    basis_is_ellipse = _compare_int_socket(group, input_node.outputs["密度基準"], 3, label="密度角度 楕円", location=(location[0] + 2640, location[1] - 1120))
    basis_x = _switch_float(group, basis_is_round, rect_x, rounded_x, label="密度角度X 角丸", location=(location[0] + 2840, location[1] - 640))
    basis_y = _switch_float(group, basis_is_round, rect_y, rounded_y, label="密度角度Y 角丸", location=(location[0] + 2840, location[1] - 840))
    basis_x = _switch_float(group, basis_is_ellipse, basis_x, ellipse_x, label="密度角度X 楕円", location=(location[0] + 3040, location[1] - 760))
    basis_y = _switch_float(group, basis_is_ellipse, basis_y, ellipse_y, label="密度角度Y 楕円", location=(location[0] + 3040, location[1] - 960))
    density_angle = _math_binary(group, "ARCTAN2", basis_y, basis_x, label="距離指定密度角", location=(location[0] + 3240, location[1] - 860))
    return _switch_float(group, is_distance, uniform_angle, density_angle, label="密度補正角度", location=(location[0] + 3440, location[1] - 700))


def _effect_taper_lengths(group, input_node, line_length_socket, *, label: str, location: tuple[float, float]):
    in_percent_len = _math_binary(
        group,
        "MULTIPLY",
        line_length_socket,
        _math_binary(
            group,
            "MULTIPLY",
            input_node.outputs["入り始点 (%)"],
            b_value=0.01,
            label=f"{label} 入り始点率",
            location=(location[0], location[1] + 240),
        ),
        label=f"{label} 入り始点長",
        location=(location[0] + 220, location[1] + 240),
    )
    out_percent_len = _math_binary(
        group,
        "MULTIPLY",
        line_length_socket,
        _math_binary(
            group,
            "MULTIPLY",
            input_node.outputs["抜き始点 (%)"],
            b_value=0.01,
            label=f"{label} 抜き始点率",
            location=(location[0], location[1] + 80),
        ),
        label=f"{label} 抜き始点長",
        location=(location[0] + 220, location[1] + 80),
    )
    in_range_percent = _math_binary(
        group,
        "MULTIPLY",
        line_length_socket,
        _math_binary(
            group,
            "MULTIPLY",
            input_node.outputs["入りの範囲 (%)"],
            b_value=0.01,
            label=f"{label} 入り範囲率",
            location=(location[0], location[1] - 80),
        ),
        label=f"{label} 入り範囲長",
        location=(location[0] + 220, location[1] - 80),
    )
    out_range_percent = _math_binary(
        group,
        "MULTIPLY",
        line_length_socket,
        _math_binary(
            group,
            "MULTIPLY",
            input_node.outputs["抜きの範囲 (%)"],
            b_value=0.01,
            label=f"{label} 抜き範囲率",
            location=(location[0], location[1] - 240),
        ),
        label=f"{label} 抜き範囲長",
        location=(location[0] + 220, location[1] - 240),
    )
    in_range_mm = _math_binary(group, "MULTIPLY", input_node.outputs["入りの範囲 (mm)"], b_value=0.001, label=f"{label} 入り範囲mm", location=(location[0], location[1] - 400))
    out_range_mm = _math_binary(group, "MULTIPLY", input_node.outputs["抜きの範囲 (mm)"], b_value=0.001, label=f"{label} 抜き範囲mm", location=(location[0], location[1] - 560))
    is_length = _compare_int_socket(group, input_node.outputs["範囲"], 2, label=f"{label} 長さ指定", location=(location[0] + 220, location[1] - 440))
    in_range = _switch_float(group, is_length, in_range_percent, in_range_mm, label=f"{label} 入り範囲方式", location=(location[0] + 440, location[1] - 160))
    out_range = _switch_float(group, is_length, out_range_percent, out_range_mm, label=f"{label} 抜き範囲方式", location=(location[0] + 440, location[1] - 320))
    in_len = _math_binary(group, "MINIMUM", in_percent_len, in_range, label=f"{label} 入り長", location=(location[0] + 660, location[1] + 40))
    out_len = _math_binary(group, "MINIMUM", out_percent_len, out_range, label=f"{label} 抜き長", location=(location[0] + 660, location[1] - 120))
    return in_len, out_len


def _effect_shape_factor(
    group,
    input_node,
    shape_socket,
    angle_socket,
    width_half_m,
    height_half_m,
    *,
    prefix: str,
    label: str,
    location: tuple[float, float],
):
    import math

    one = _constant_float(group, 1.0, label=f"{label} 通常", location=(location[0], location[1]))
    radius_sum = _math_add(group, width_half_m, height_half_m, label=f"{label} 半径合計", location=(location[0], location[1] - 160))
    radius_avg = _math_binary(group, "MULTIPLY", radius_sum, b_value=0.5, label=f"{label} 平均半径", location=(location[0] + 200, location[1] - 160))
    safe_avg = _math_binary(group, "MAXIMUM", radius_avg, b_value=0.000001, label=f"{label} 平均半径下限", location=(location[0] + 400, location[1] - 320))
    cos_abs = _math_binary(
        group,
        "ABSOLUTE",
        _math_binary(group, "COSINE", angle_socket, label=f"{label} cos", location=(location[0] + 400, location[1] - 520)),
        label=f"{label} |cos|",
        location=(location[0] + 600, location[1] - 520),
    )
    sin_abs = _math_binary(
        group,
        "ABSOLUTE",
        _math_binary(group, "SINE", angle_socket, label=f"{label} sin", location=(location[0] + 400, location[1] - 680)),
        label=f"{label} |sin|",
        location=(location[0] + 600, location[1] - 680),
    )
    safe_cos = _math_binary(group, "MAXIMUM", cos_abs, b_value=0.000001, label=f"{label} cos下限", location=(location[0] + 800, location[1] - 520))
    safe_sin = _math_binary(group, "MAXIMUM", sin_abs, b_value=0.000001, label=f"{label} sin下限", location=(location[0] + 800, location[1] - 680))
    rect_rx = _math_binary(group, "DIVIDE", width_half_m, safe_cos, label=f"{label} 枠X半径", location=(location[0] + 1000, location[1] - 520))
    rect_ry = _math_binary(group, "DIVIDE", height_half_m, safe_sin, label=f"{label} 枠Y半径", location=(location[0] + 1000, location[1] - 680))
    rect_radius = _math_binary(group, "MINIMUM", rect_rx, rect_ry, label=f"{label} 枠半径", location=(location[0] + 1200, location[1] - 600))
    ellipse_cos = _math_binary(
        group,
        "DIVIDE",
        _math_binary(group, "COSINE", angle_socket, label=f"{label} 楕円cos", location=(location[0] + 400, location[1] - 880)),
        _math_binary(group, "MAXIMUM", width_half_m, b_value=0.000001, label=f"{label} 楕円X下限", location=(location[0] + 600, location[1] - 880)),
        label=f"{label} 楕円cos比",
        location=(location[0] + 800, location[1] - 880),
    )
    ellipse_sin = _math_binary(
        group,
        "DIVIDE",
        _math_binary(group, "SINE", angle_socket, label=f"{label} 楕円sin", location=(location[0] + 400, location[1] - 1040)),
        _math_binary(group, "MAXIMUM", height_half_m, b_value=0.000001, label=f"{label} 楕円Y下限", location=(location[0] + 600, location[1] - 1040)),
        label=f"{label} 楕円sin比",
        location=(location[0] + 800, location[1] - 1040),
    )
    ellipse_radius = _math_binary(
        group,
        "DIVIDE",
        one,
        _math_binary(
            group,
            "SQRT",
            _math_add(
                group,
                _math_binary(group, "MULTIPLY", ellipse_cos, ellipse_cos, label=f"{label} 楕円cos2", location=(location[0] + 1000, location[1] - 880)),
                _math_binary(group, "MULTIPLY", ellipse_sin, ellipse_sin, label=f"{label} 楕円sin2", location=(location[0] + 1000, location[1] - 1040)),
                label=f"{label} 楕円式",
                location=(location[0] + 1200, location[1] - 960),
            ),
            label=f"{label} 楕円式根",
            location=(location[0] + 1400, location[1] - 960),
        ),
        label=f"{label} 楕円半径",
        location=(location[0] + 1600, location[1] - 960),
    )
    rect_factor = _math_binary(group, "DIVIDE", rect_radius, safe_avg, label=f"{label} 枠倍率", location=(location[0] + 1400, location[1] - 600))
    ellipse_factor = _math_binary(group, "DIVIDE", ellipse_radius, safe_avg, label=f"{label} 楕円倍率", location=(location[0] + 1800, location[1] - 960))
    is_rect = _compare_int_socket(group, shape_socket, 1, label=f"{label} 矩形", location=(location[0] + 1600, location[1] - 600))
    is_octagon = _compare_int_socket(group, shape_socket, 7, label=f"{label} 八角形", location=(location[0] + 1600, location[1] - 760))
    rect_like = _boolean_or(group, is_rect, is_octagon, label=f"{label} 枠系", location=(location[0] + 1800, location[1] - 680))
    base_factor = _switch_float(group, rect_like, ellipse_factor, rect_factor, label=f"{label} 縦横比", location=(location[0] + 2000, location[1] - 760))
    radius_mm = _math_binary(group, "MULTIPLY", radius_avg, b_value=1000.0, label=f"{label} 半径mm", location=(location[0] + 400, location[1] - 160))
    perimeter = _math_binary(group, "MULTIPLY", radius_mm, b_value=math.tau, label=f"{label} 周長", location=(location[0] + 600, location[1] - 160))
    width_name = f"{prefix} 山の幅"
    height_name = f"{prefix} 山の高さ"
    width_jitter_name = f"{prefix} 山の幅 乱れ"
    height_jitter_name = f"{prefix} 山の高さ 乱れ"
    sub_width_name = f"{prefix} 小山幅 (%)"
    sub_width_jitter_name = f"{prefix} 小山幅 乱れ"
    sub_height_name = f"{prefix} 小山高 (%)"
    sub_height_jitter_name = f"{prefix} 小山高 乱れ"
    offset_name = f"{prefix} ズラし量 (%)"
    plain_width = _math_binary(group, "MAXIMUM", input_node.outputs[width_name], b_value=0.001, label=f"{label} 山幅下限", location=(location[0] + 800, location[1] + 40))
    derived_count = _math_binary(group, "DIVIDE", perimeter, plain_width, label=f"{label} 山数", location=(location[0] + 1000, location[1] + 40))
    derived_count = _math_binary(group, "MAXIMUM", derived_count, b_value=3.0, label=f"{label} 山数下限", location=(location[0] + 1200, location[1] + 40))
    derived_count_int = _float_to_int(group, derived_count, label=f"{label} 山数整数", location=(location[0] + 1400, location[1] + 40))
    shared_shape = _common_shape_factor(
        group,
        shape_socket=shape_socket,
        angle_socket=angle_socket,
        radius_socket=radius_avg,
        perimeter_socket=perimeter,
        width_socket=input_node.outputs[width_name],
        width_jitter_socket=input_node.outputs[width_jitter_name],
        height_socket=input_node.outputs[height_name],
        height_jitter_socket=input_node.outputs[height_jitter_name],
        offset_socket=input_node.outputs[offset_name],
        sub_width_socket=input_node.outputs[sub_width_name],
        sub_width_jitter_socket=input_node.outputs[sub_width_jitter_name],
        sub_height_socket=input_node.outputs[sub_height_name],
        sub_height_jitter_socket=input_node.outputs[sub_height_jitter_name],
        fluffy_count_socket=derived_count_int,
        fluffy_amplitude_socket=input_node.outputs[height_name],
        spike_count_socket=derived_count_int,
        spike_depth_socket=input_node.outputs[height_name],
        spike_jitter_socket=input_node.outputs[height_jitter_name],
        label=f"{label} 共通 雲・もやもや・トゲ形状",
        location=(location[0] + 1600, location[1] - 80),
    )
    shaped = _math_binary(group, "MULTIPLY", shared_shape, base_factor, label=f"{label} 縦横比反映", location=(location[0] + 2800, location[1] - 40))
    round_name = f"{prefix} 角丸"
    radius_name = f"{prefix} 角半径"
    round_factor = _math_binary(
        group,
        "SUBTRACT",
        one,
        _math_binary(
            group,
            "MULTIPLY",
            _math_binary(group, "DIVIDE", _math_binary(group, "MULTIPLY", input_node.outputs[radius_name], b_value=0.001, label=f"{label} 角半径m", location=(location[0] + 2400, location[1] - 760)), radius_avg, label=f"{label} 角半径率", location=(location[0] + 2600, location[1] - 760)),
            b_value=0.05,
            label=f"{label} 角丸補正量",
            location=(location[0] + 2800, location[1] - 760),
        ),
        label=f"{label} 角丸補正",
        location=(location[0] + 3000, location[1] - 760),
    )
    rounded = _math_binary(group, "MULTIPLY", shaped, round_factor, label=f"{label} 角丸形状", location=(location[0] + 3200, location[1] - 460))
    return _switch_float(group, input_node.outputs[round_name], shaped, rounded, label=f"{label} 角丸切替", location=(location[0] + 3400, location[1] - 160))


def _effect_taper_half_widths(group, input_node, line_half_m, *, label: str, location: tuple[float, float]):
    is_width = _compare_int_socket(group, input_node.outputs["適用先"], 1, label=f"{label} 線幅適用", location=location)
    in_scale = _math_binary(group, "MULTIPLY", input_node.outputs["入り (%)"], b_value=0.01, label=f"{label} 入り率", location=(location[0] + 200, location[1] + 120))
    out_scale = _math_binary(group, "MULTIPLY", input_node.outputs["抜き (%)"], b_value=0.01, label=f"{label} 抜き率", location=(location[0] + 200, location[1] - 40))
    outer_raw = _math_binary(group, "MULTIPLY", line_half_m, in_scale, label=f"{label} 入り線幅", location=(location[0] + 400, location[1] + 120))
    inner_raw = _math_binary(group, "MULTIPLY", line_half_m, out_scale, label=f"{label} 抜き線幅", location=(location[0] + 400, location[1] - 40))
    outer_half = _switch_float(group, is_width, line_half_m, outer_raw, label=f"{label} 入り線幅切替", location=(location[0] + 600, location[1] + 120))
    inner_half = _switch_float(group, is_width, line_half_m, inner_raw, label=f"{label} 抜き線幅切替", location=(location[0] + 600, location[1] - 40))
    return inner_half, outer_half


def _line_quad_mesh_x(
    group,
    x0_socket,
    x1_socket,
    half0_socket,
    half1_socket,
    material_socket,
    *,
    label: str,
    location: tuple[float, float],
):
    p1 = _combine_xyz(group, x0_socket, half0_socket, z=0.0, label=f"{label} 左上", location=(location[0], location[1] + 220))
    p2 = _combine_xyz(group, x1_socket, half1_socket, z=0.0, label=f"{label} 右上", location=(location[0], location[1] + 60))
    neg1 = _math_binary(group, "MULTIPLY", half1_socket, b_value=-1.0, label=f"{label} 右下幅", location=(location[0], location[1] - 100))
    neg0 = _math_binary(group, "MULTIPLY", half0_socket, b_value=-1.0, label=f"{label} 左下幅", location=(location[0], location[1] - 260))
    p3 = _combine_xyz(group, x1_socket, neg1, z=0.0, label=f"{label} 右下", location=(location[0] + 220, location[1] - 20))
    p4 = _combine_xyz(group, x0_socket, neg0, z=0.0, label=f"{label} 左下", location=(location[0] + 220, location[1] - 180))
    quad = _node(group, "GeometryNodeCurvePrimitiveQuadrilateral", label=label, location=(location[0] + 440, location[1]))
    quad.mode = "POINTS"
    for name, socket in (("Point 1", p1), ("Point 2", p2), ("Point 3", p3), ("Point 4", p4)):
        _link(group, socket, quad.inputs[name])
    fill = _node(group, "GeometryNodeFillCurve", label=f"{label} メッシュ", location=(location[0] + 660, location[1]))
    _link(group, quad.outputs["Curve"], fill.inputs["Curve"])
    return _set_material(group, fill.outputs["Mesh"], material_socket, label=f"{label} 素材", location=(location[0] + 880, location[1]))


def _tapered_line_mesh_x(
    group,
    input_node,
    x0_socket,
    x1_socket,
    line_half_m,
    material_socket,
    *,
    label: str,
    location: tuple[float, float],
):
    inner_half, outer_half = _effect_taper_half_widths(group, input_node, line_half_m, label=label, location=location)
    length = _math_binary(group, "SUBTRACT", x1_socket, x0_socket, label=f"{label} 長さ", location=(location[0] + 820, location[1] + 360))
    length = _math_binary(group, "MAXIMUM", length, b_value=0.000001, label=f"{label} 長さ下限", location=(location[0] + 1020, location[1] + 360))
    in_len, out_len = _effect_taper_lengths(group, input_node, length, label=label, location=(location[0] + 1020, location[1] + 80))
    x_out = _math_add(group, x0_socket, out_len, label=f"{label} 抜き終点", location=(location[0] + 1900, location[1] + 80))
    x_in = _math_binary(group, "SUBTRACT", x1_socket, in_len, label=f"{label} 入り始点", location=(location[0] + 1900, location[1] + 240))
    join = _node(group, "GeometryNodeJoinGeometry", label=f"{label} 入り抜き線", location=(location[0] + 3020, location[1]))
    seg_out = _line_quad_mesh_x(group, x0_socket, x_out, inner_half, line_half_m, material_socket, label=f"{label} 抜き", location=(location[0] + 2100, location[1] - 740))
    _link(group, seg_out, join.inputs["Geometry"])
    seg_mid = _line_quad_mesh_x(group, x_out, x_in, line_half_m, line_half_m, material_socket, label=f"{label} 中間", location=(location[0] + 2100, location[1] - 240))
    seg_in = _line_quad_mesh_x(group, x_in, x1_socket, line_half_m, outer_half, material_socket, label=f"{label} 入り", location=(location[0] + 2100, location[1] + 260))
    _link(group, seg_mid, join.inputs["Geometry"])
    _link(group, seg_in, join.inputs["Geometry"])
    return join.outputs["Geometry"]


def _frame_raycast_distance(
    group,
    input_node,
    center_x_socket,
    center_y_socket,
    angle_socket,
    fallback_distance_socket,
    *,
    label: str,
    location: tuple[float, float],
):
    object_info = _node(group, "GeometryNodeObjectInfo", label=f"{label} 参照", location=location)
    try:
        object_info.transform_space = "RELATIVE"
    except Exception:  # noqa: BLE001
        pass
    _link(group, input_node.outputs["始点コマ枠オブジェクト"], object_info.inputs["Object"])
    if "As Instance" in object_info.inputs:
        _set_default(object_info.inputs["As Instance"], False)

    source = _combine_xyz(
        group,
        center_x_socket,
        center_y_socket,
        z=0.0,
        label=f"{label} 中心",
        location=(location[0] + 220, location[1] + 180),
    )
    ray_x = _math_binary(group, "COSINE", angle_socket, label=f"{label} X方向", location=(location[0] + 220, location[1] - 20))
    ray_y = _math_binary(group, "SINE", angle_socket, label=f"{label} Y方向", location=(location[0] + 220, location[1] - 180))
    direction = _combine_xyz(
        group,
        ray_x,
        ray_y,
        z=0.0,
        label=f"{label} 方向",
        location=(location[0] + 440, location[1] - 100),
    )

    raycast = _node(group, "GeometryNodeRaycast", label=label, location=(location[0] + 680, location[1]))
    _link(group, object_info.outputs["Geometry"], raycast.inputs["Target Geometry"])
    _link(group, source, raycast.inputs["Source Position"])
    _link(group, direction, raycast.inputs["Ray Direction"])
    _set_default(raycast.inputs["Ray Length"], 100.0)
    return _switch_float(
        group,
        raycast.outputs["Is Hit"],
        fallback_distance_socket,
        raycast.outputs["Hit Distance"],
        label=f"{label} 結果",
        location=(location[0] + 900, location[1]),
    )


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

    count_socket = _focus_line_count_socket(group, input_node, width_half_m, height_half_m)
    points = _node(group, "GeometryNodeMeshLine", label="線の本数", location=(-520, -520))
    _link(group, count_socket, points.inputs["Count"])
    _set_default(points.inputs["Start Location"], (0.0, 0.0, 0.0))
    _set_default(points.inputs["Offset"], (0.0, 0.0, 0.0))

    index = _node(group, "GeometryNodeInputIndex", label="線番号", location=(-520, -720))
    frac = _math_binary(
        group,
        "DIVIDE",
        index.outputs["Index"],
        count_socket,
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
    angle = _focus_density_angle(
        group,
        input_node,
        frac,
        width_half_m,
        height_half_m,
        angle,
        location=(-1020, -2600),
    )
    step_angle = _math_binary(
        group,
        "DIVIDE",
        _constant_float(group, math.tau, label="一周", location=(-320, -520)),
        count_socket,
        label="一線分の角度",
        location=(-120, -520),
    )
    seed_float = _math_add(group, index.outputs["Index"], input_node.outputs["乱数"], label="乱数種", location=(-320, -360))
    spacing_wave = _math_binary(group, "SINE", seed_float, label="間隔乱れ波", location=(-120, -360))
    spacing_jitter = _math_binary(
        group,
        "MULTIPLY",
        _math_binary(
            group,
            "MULTIPLY",
            step_angle,
            input_node.outputs["間隔乱れ量"],
            label="間隔乱れ量",
            location=(80, -520),
        ),
        spacing_wave,
        label="間隔乱れ角",
        location=(280, -520),
    )
    zero_jitter = _constant_float(group, 0.0, label="乱れなし", location=(80, -360))
    spacing_jitter = _switch_float(
        group,
        input_node.outputs["間隔 乱れ"],
        zero_jitter,
        spacing_jitter,
        label="間隔乱れ切替",
        location=(480, -520),
    )
    bundle_count_wave = _math_binary(
        group,
        "ABSOLUTE",
        _math_binary(
            group,
            "SINE",
            _math_add(
                group,
                seed_float,
                _constant_float(group, 5.77, label="まとまり数乱れ位相", location=(-120, -1180)),
                label="まとまり数乱れ種",
                location=(80, -1180),
            ),
            label="まとまり数乱れ波",
            location=(280, -1180),
        ),
        label="まとまり数乱れ正",
        location=(480, -1180),
    )
    bundle_count_factor = _math_add(
        group,
        _constant_float(group, 1.0, label="まとまり数基準", location=(-120, -1340)),
        _math_binary(
            group,
            "MULTIPLY",
            bundle_count_wave,
            input_node.outputs["まとまり 数の乱れ"],
            label="まとまり数乱れ量",
            location=(80, -1340),
        ),
        label="まとまり数係数",
        location=(280, -1340),
    )
    bundle_count_effective = _math_binary(
        group,
        "MULTIPLY",
        input_node.outputs["まとまり 数"],
        bundle_count_factor,
        label="有効まとまり数",
        location=(480, -1340),
    )
    bundle_size = _math_binary(
        group,
        "MAXIMUM",
        bundle_count_effective,
        b_value=1.0,
        label="まとまり数下限",
        location=(80, -1080),
    )
    bundle_index = _math_binary(
        group,
        "FLOOR",
        _math_binary(group, "DIVIDE", index.outputs["Index"], bundle_size, label="まとまり番号", location=(280, -1080)),
        label="まとまり番号整数",
        location=(480, -1080),
    )
    bundle_gap_wave = _math_binary(
        group,
        "SINE",
        _math_add(
            group,
            seed_float,
            _constant_float(group, 9.41, label="まとまり間隔乱れ位相", location=(80, -760)),
            label="まとまり間隔乱れ種",
            location=(280, -760),
        ),
        label="まとまり間隔乱れ波",
        location=(480, -760),
    )
    bundle_gap_factor = _math_add(
        group,
        _constant_float(group, 1.0, label="まとまり間隔基準", location=(280, -600)),
        _math_binary(
            group,
            "MULTIPLY",
            bundle_gap_wave,
            input_node.outputs["まとまり間隔の乱れ"],
            label="まとまり間隔乱れ量",
            location=(480, -600),
        ),
        label="まとまり間隔係数",
        location=(680, -600),
    )
    bundle_gap_m = _math_binary(
        group,
        "MULTIPLY",
        _math_binary(
            group,
            "MULTIPLY",
            input_node.outputs["まとまり間隔"],
            bundle_gap_factor,
            label="有効まとまり間隔",
            location=(80, -900),
        ),
        b_value=0.001,
        label="まとまり間隔m",
        location=(280, -900),
    )
    bundle_gap_angle = _math_binary(
        group,
        "DIVIDE",
        bundle_gap_m,
        _math_binary(group, "MAXIMUM", width_half_m, b_value=0.000001, label="まとまり半径", location=(480, -900)),
        label="まとまり間隔角",
        location=(680, -900),
    )
    bundle_angle = _math_binary(
        group,
        "MULTIPLY",
        bundle_index,
        bundle_gap_angle,
        label="まとまり角",
        location=(880, -900),
    )
    bundle_jitter = _math_binary(
        group,
        "MULTIPLY",
        _math_binary(
            group,
            "MULTIPLY",
            step_angle,
            input_node.outputs["まとまりの乱れ"],
            label="まとまり乱れ量",
            location=(880, -740),
        ),
        spacing_wave,
        label="まとまり乱れ角",
        location=(1080, -740),
    )
    bundle_angle = _math_add(group, bundle_angle, bundle_jitter, label="まとまり乱れ済み角", location=(1280, -740))
    bundle_angle = _switch_float(
        group,
        input_node.outputs["まとまり"],
        zero_jitter,
        bundle_angle,
        label="まとまり切替",
        location=(1080, -900),
    )
    angle = _math_add(group, angle, spacing_jitter, label="間隔乱れ済み角", location=(680, -520))
    angle = _math_add(group, angle, bundle_angle, label="まとまり済み角", location=(1280, -900))
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
    center_x = _math_add(group, origin_x_m, width_half_m, label="中心 X", location=(760, -260))
    center_y = _math_add(group, origin_y_m, height_half_m, label="中心 Y", location=(760, -420))

    radius_sum = _math_add(
        group,
        width_half_m,
        height_half_m,
        label="半径合計",
        location=(-520, -1080),
    )
    base_radius = _math_binary(
        group,
        "MULTIPLY",
        radius_sum,
        b_value=0.5,
        label="平均半径",
        location=(-320, -1080),
    )
    start_width_half_m = _math_binary(
        group,
        "MULTIPLY",
        width_half_m,
        b_value=2.0,
        label="始点形状 半幅",
        location=(-520, -2260),
    )
    start_height_half_m = _math_binary(
        group,
        "MULTIPLY",
        height_half_m,
        b_value=2.0,
        label="始点形状 半高",
        location=(-520, -2420),
    )
    start_base_radius = _math_binary(
        group,
        "MULTIPLY",
        base_radius,
        b_value=2.0,
        label="始点基準半径",
        location=(-120, -1080),
    )
    start_shape_factor = _effect_shape_factor(
        group,
        input_node,
        input_node.outputs["始点形状"],
        angle_with_rotation,
        start_width_half_m,
        start_height_half_m,
        prefix="始点",
        label="始点形状",
        location=(-320, -2260),
    )
    radius = _math_binary(group, "MULTIPLY", start_base_radius, start_shape_factor, label="始点形状半径", location=(3280, -2260))
    end_shape_factor = _effect_shape_factor(
        group,
        input_node,
        input_node.outputs["終点形状"],
        angle_with_rotation,
        width_half_m,
        height_half_m,
        prefix="終点",
        label="終点形状",
        location=(-320, -3220),
    )
    inner_radius = _math_binary(group, "MULTIPLY", base_radius, end_shape_factor, label="終点形状半径", location=(3280, -3220))
    length_wave = _math_binary(
        group,
        "SINE",
        _math_add(group, seed_float, _constant_float(group, 3.17, label="線長乱れ位相", location=(-120, -1560)), label="線長乱れ種", location=(80, -1560)),
        label="線長乱れ波",
        location=(280, -1560),
    )
    length_wave = _math_binary(group, "ABSOLUTE", length_wave, label="線長乱れ正", location=(480, -1560))
    line_span = _math_binary(group, "SUBTRACT", radius, inner_radius, label="線長", location=(680, -1560))
    start_trim = _math_binary(
        group,
        "MULTIPLY",
        _math_binary(group, "MULTIPLY", line_span, input_node.outputs["始点乱れ量"], label="始点乱れ量", location=(880, -1560)),
        length_wave,
        label="始点乱れ長",
        location=(1080, -1560),
    )
    start_trim = _switch_float(group, input_node.outputs["始点乱れ"], zero_jitter, start_trim, label="始点乱れ切替", location=(1280, -1560))
    radius = _math_binary(group, "SUBTRACT", radius, start_trim, label="始点乱れ半径", location=(1480, -1560))
    end_wave = _math_binary(
        group,
        "SINE",
        _math_add(group, seed_float, _constant_float(group, 7.31, label="終点乱れ位相", location=(-120, -1740)), label="終点乱れ種", location=(80, -1740)),
        label="終点乱れ波",
        location=(280, -1740),
    )
    end_wave = _math_binary(group, "ABSOLUTE", end_wave, label="終点乱れ正", location=(480, -1740))
    end_trim = _math_binary(
        group,
        "MULTIPLY",
        _math_binary(group, "MULTIPLY", line_span, input_node.outputs["終点乱れ量"], label="終点乱れ量", location=(880, -1740)),
        end_wave,
        label="終点乱れ長",
        location=(1080, -1740),
    )
    end_trim = _switch_float(group, input_node.outputs["終点乱れ"], zero_jitter, end_trim, label="終点乱れ切替", location=(1280, -1740))
    inner_radius = _math_add(group, inner_radius, end_trim, label="終点乱れ半径", location=(1480, -1740))
    base_line_half_m = line_half_m
    width_wave = _math_binary(
        group,
        "SINE",
        _math_add(group, seed_float, _constant_float(group, 11.13, label="線幅乱れ位相", location=(80, -2040)), label="線幅乱れ種", location=(280, -2040)),
        label="線幅乱れ波",
        location=(480, -2040),
    )
    width_delta = _math_binary(
        group,
        "MULTIPLY",
        width_wave,
        input_node.outputs["線幅 乱れ量"],
        label="線幅乱れ率",
        location=(680, -2040),
    )
    width_factor = _math_add(group, _constant_float(group, 1.0, label="線幅基準", location=(680, -2200)), width_delta, label="線幅乱れ係数", location=(880, -2040))
    jittered_half = _math_binary(group, "MULTIPLY", base_line_half_m, width_factor, label="乱れ線幅", location=(1080, -2040))
    actual_line_half_m = _switch_float(group, input_node.outputs["線幅 乱れ"], base_line_half_m, jittered_half, label="線幅乱れ切替", location=(1280, -2040))
    width_scale = _math_binary(
        group,
        "DIVIDE",
        actual_line_half_m,
        _math_binary(group, "MAXIMUM", base_line_half_m, b_value=0.000001, label="線幅倍率基準", location=(1280, -2200)),
        label="線幅倍率",
        location=(1480, -2040),
    )
    frame_fallback_radius = _math_binary(group, "MULTIPLY", radius, b_value=1.25, label="コマ枠始点半径", location=(80, -1880))
    frame_hit_radius = _frame_raycast_distance(
        group,
        input_node,
        center_x,
        center_y,
        angle_with_rotation,
        frame_fallback_radius,
        label="コマ枠始点",
        location=(80, -2440),
    )
    frame_radius = _math_add(
        group,
        frame_hit_radius,
        _math_binary(group, "MULTIPLY", actual_line_half_m, b_value=2.0, label="始点外側実線幅", location=(1080, -2440)),
        label="コマ枠始点外側",
        location=(1280, -2440),
    )
    radius = _switch_float(group, input_node.outputs["始点をコマ枠に設定"], radius, frame_radius, label="コマ枠始点切替", location=(280, -1880))
    index_mod = _math_binary(group, "MODULO", index.outputs["Index"], b_value=2.0, label="偶奇", location=(-120, -1240))
    is_even = _compare_float_socket(group, index_mod, 0.0, label="偶数線", location=(80, -1400))
    uni_short = _math_binary(group, "MULTIPLY", radius, b_value=0.84, label="ウニ短線", location=(80, -1240))
    uni_long = _math_binary(group, "MULTIPLY", radius, b_value=1.10, label="ウニ長線", location=(280, -1240))
    uni_radius = _switch_float(group, is_even, uni_long, uni_short, label="ウニ線長", location=(480, -1240))
    is_uni = _compare_int_socket(group, input_node.outputs["種類"], 2, label="ウニフラか", location=(480, -1400))
    end_radius = _switch_float(group, is_uni, radius, uni_radius, label="終点半径", location=(680, -1240))

    base_start = _constant_float(group, 0.0, label="線原型始点", location=(80, -1080))
    base_end = _constant_float(group, 1.0, label="線原型終点", location=(80, -1240))
    material = _tapered_line_mesh_x(
        group,
        input_node,
        base_start,
        base_end,
        base_line_half_m,
        line_material,
        label="線素材",
        location=(280, -1080),
    )
    line_length = _math_binary(group, "SUBTRACT", end_radius, inner_radius, label="線の実長", location=(760, -1560))
    line_length = _math_binary(group, "MAXIMUM", line_length, b_value=0.000001, label="線の実長下限", location=(980, -1560))
    ray_x = _math_binary(group, "COSINE", angle_with_rotation, label="線X方向", location=(560, -1880))
    ray_y = _math_binary(group, "SINE", angle_with_rotation, label="線Y方向", location=(560, -2040))
    point_x = _math_binary(group, "MULTIPLY", ray_x, inner_radius, label="始点X", location=(760, -1880))
    point_y = _math_binary(group, "MULTIPLY", ray_y, inner_radius, label="始点Y", location=(760, -2040))
    point_pos = _combine_xyz(group, point_x, point_y, z=0.0, label="線始点位置", location=(980, -1960))
    placed_points = _node(group, "GeometryNodeSetPosition", label="線始点を配置", location=(760, -840))
    _link(group, points.outputs["Mesh"], placed_points.inputs["Geometry"])
    _link(group, point_pos, placed_points.inputs["Position"])
    scale_vec = _combine_xyz(
        group,
        line_length,
        width_scale,
        z=1.0,
        label="線ごとの長さ",
        location=(1180, -1640),
    )

    instance = _node(group, "GeometryNodeInstanceOnPoints", label="線を繰り返し配置", location=(760, -640))
    _link(group, placed_points.outputs["Geometry"], instance.inputs["Points"])
    _link(group, material, instance.inputs["Instance"])
    _link(group, axis_angle.outputs["Rotation"], instance.inputs["Rotation"])
    _link(group, scale_vec, instance.inputs["Scale"])
    realize = _node(group, "GeometryNodeRealizeInstances", label="線を実体化", location=(980, -640))
    _link(group, instance.outputs["Instances"], realize.inputs["Geometry"])
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
    translation = _combine_xyz(group, cx, cy, z=-0.00002, label="下地位置", location=(1180, -160))
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


def effect_values(
    params,
    bounds: tuple[float, float, float, float] | None,
    seed: int,
    *,
    start_frame_object: bpy.types.Object | None = None,
) -> dict[str, Any]:
    if bounds is None:
        x = y = width = height = 0.0
    else:
        x, y, width, height = bounds
    effect_type = str(getattr(params, "effect_type", "") or "")
    values = {
        "乱数": int(seed),
        "位置 X": float(x or 0.0),
        "位置 Y": float(y or 0.0),
        "幅": float(width or 0.0),
        "高さ": float(height or 0.0),
        "始点コマ枠オブジェクト": start_frame_object,
    }
    for field, spec in _EFFECT_FIELD_SPECS.items():
        raw = getattr(params, field, spec.default) if params is not None else spec.default
        values[spec.name] = _socket_value_for_spec(field, spec, raw)
    values["種類"] = _EFFECT_CODES.get(effect_type, 0)
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
