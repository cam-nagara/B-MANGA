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
_GROUP_VERSION = 6
_BALLOON_TAIL_SOCKET_COUNT = 8


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
    SocketSpec("参照形状", "NodeSocketObject", None),
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
    "effect_line": tuple(_EFFECT_FIELD_SPECS.values()) + _EFFECT_POSITION_SOCKETS,
    "balloon": _BALLOON_EXTRA_SOCKETS,
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
        elif spec.socket_type != "NodeSocketObject":
            item.default_value = float(spec.default or 0.0)
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
    ref_info = _node(group, "GeometryNodeObjectInfo", label="参照形状を取得", location=(120, 420))
    _link(group, input_node.outputs["参照形状"], ref_info.inputs["Object"])
    try:
        _set_default(ref_info.inputs["As Instance"], False)
    except Exception:  # noqa: BLE001
        pass
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
    _link(group, ref_info.outputs["Geometry"], switch.inputs["False"])
    _link(group, generated, switch.inputs["True"])
    _link(group, switch.outputs["Output"], output_node.inputs["Geometry"])
    group[PROP_GROUP_VERSION] = _GROUP_VERSION


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


def _build_effect_line_nodes(group) -> None:
    _clear_nodes(group)
    input_node, output_node = _group_input_output(group)
    origin_x_m = _math_multiply(
        group,
        input_node.outputs["位置 X"],
        0.001,
        label="位置 X mm → m",
        location=(-800, 260),
    )
    origin_y_m = _math_multiply(
        group,
        input_node.outputs["位置 Y"],
        0.001,
        label="位置 Y mm → m",
        location=(-800, 110),
    )
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
        origin_x_m,
        origin_y_m,
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
        if spec.socket_type == "NodeSocketObject":
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
        line_count = int(getattr(params, "max_line_count", 0) or 0)
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
