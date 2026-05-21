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


def _build_passthrough_nodes(group) -> None:
    _clear_nodes(group)
    input_node = group.nodes.new("NodeGroupInput")
    input_node.location = (-220, 0)
    output_node = group.nodes.new("NodeGroupOutput")
    output_node.location = (220, 0)
    try:
        group.links.new(input_node.outputs["Geometry"], output_node.inputs["Geometry"])
    except Exception:  # noqa: BLE001
        _logger.exception("geometry nodes bridge: passthrough link failed")


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
    if len(group.nodes) == 0:
        _build_passthrough_nodes(group)
    else:
        has_group_input = any(node.bl_idname == "NodeGroupInput" for node in group.nodes)
        has_group_output = any(node.bl_idname == "NodeGroupOutput" for node in group.nodes)
        if not has_group_input or not has_group_output:
            _build_passthrough_nodes(group)
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
