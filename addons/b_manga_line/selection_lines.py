"""B-MANGA Line — Freestyleマーク辺から選択線を生成する."""

from __future__ import annotations

import bpy

from . import inner_lines
from .core import (
    FREESTYLE_EDGE_ATTR,
    SELECTION_LINE_CHAIN_ID_ATTR,
    SELECTION_LINE_MODIFIER_NAME,
    SELECTION_LINE_TREE_NAME,
    VG_SELECTION_LINE_WIDTH,
)

_BLENDER_FREESTYLE_EDGE_ATTR = "freestyle_edge"


def _source_freestyle_value(mesh: bpy.types.Mesh, edge_index: int) -> bool:
    source = mesh.attributes.get(_BLENDER_FREESTYLE_EDGE_ATTR)
    if (
        source is not None
        and getattr(source, "domain", None) == "EDGE"
        and getattr(source, "data_type", None) == "BOOLEAN"
        and edge_index < len(source.data)
    ):
        return bool(source.data[edge_index].value)
    edge = mesh.edges[edge_index]
    return bool(getattr(edge, "use_freestyle_mark", False))


def sync_freestyle_edge_attribute(obj: bpy.types.Object) -> bool:
    """メッシュ辺のFreestyleマークを選択線用の辺属性へ同期する."""
    if obj.type != "MESH" or obj.data is None:
        return False
    mesh = obj.data
    attr = mesh.attributes.get(FREESTYLE_EDGE_ATTR)
    if (
        attr is not None
        and getattr(attr, "domain", None) == "EDGE"
        and getattr(attr, "data_type", None) == "BOOLEAN"
    ):
        pass
    else:
        if attr is not None:
            mesh.attributes.remove(attr)
        attr = mesh.attributes.new(FREESTYLE_EDGE_ATTR, "BOOLEAN", "EDGE")
    for edge in mesh.edges:
        if edge.index < len(attr.data):
            attr.data[edge.index].value = _source_freestyle_value(mesh, edge.index)
    mesh.update()
    return True


def apply_selection_lines(
    obj: bpy.types.Object,
    angle: float = 0.5236,
    thickness: float = 0.0005,
    offset: float = 0.0,
    material: bpy.types.Material | None = None,
    midpoint_factor: float = 0.0,
    midpoint_angle: float | None = None,
    midpoint_jitter_percent: float = 0.0,
    resample_count: int | None = None,
    width_curve_25: float = 0.25,
    width_curve_50: float = 0.50,
    width_curve_75: float = 0.75,
    enable: bool = True,
) -> bool:
    """Freestyleマーク済みの辺に選択線を適用する."""
    if not sync_freestyle_edge_attribute(obj):
        return False
    return inner_lines.apply_inner_lines(
        obj,
        angle=angle,
        thickness=thickness,
        offset=offset,
        material=material,
        use_marked_edges=True,
        midpoint_factor=midpoint_factor,
        midpoint_angle=midpoint_angle,
        midpoint_jitter_percent=midpoint_jitter_percent,
        resample_count=resample_count,
        width_curve_25=width_curve_25,
        width_curve_50=width_curve_50,
        width_curve_75=width_curve_75,
        enable=enable,
        modifier_name=SELECTION_LINE_MODIFIER_NAME,
        tree_name=SELECTION_LINE_TREE_NAME,
        width_group_name=VG_SELECTION_LINE_WIDTH,
        chain_id_attr_name=SELECTION_LINE_CHAIN_ID_ATTR,
        marked_attr_name=FREESTYLE_EDGE_ATTR,
    )


def remove_selection_lines(obj: bpy.types.Object) -> bool:
    return inner_lines.remove_inner_lines(obj, SELECTION_LINE_MODIFIER_NAME)


def disable_selection_lines(obj: bpy.types.Object) -> bool:
    return inner_lines.disable_inner_lines(obj, SELECTION_LINE_MODIFIER_NAME)


def enable_selection_lines(obj: bpy.types.Object) -> bool:
    sync_freestyle_edge_attribute(obj)
    return inner_lines.enable_inner_lines(obj, SELECTION_LINE_MODIFIER_NAME)


def update_parameters(
    obj: bpy.types.Object,
    angle: float | None = None,
    thickness: float | None = None,
    offset: float | None = None,
    midpoint_factor: float | None = None,
    midpoint_angle: float | None = None,
    midpoint_jitter_percent: float | None = None,
    resample_count: int | None = None,
    width_curve_25: float | None = None,
    width_curve_50: float | None = None,
    width_curve_75: float | None = None,
    material: bpy.types.Material | None = None,
) -> bool:
    sync_freestyle_edge_attribute(obj)
    return inner_lines.update_parameters(
        obj,
        angle=angle,
        thickness=thickness,
        offset=offset,
        use_marked_edges=True,
        midpoint_factor=midpoint_factor,
        midpoint_angle=midpoint_angle,
        midpoint_jitter_percent=midpoint_jitter_percent,
        resample_count=resample_count,
        width_curve_25=width_curve_25,
        width_curve_50=width_curve_50,
        width_curve_75=width_curve_75,
        material=material,
        modifier_name=SELECTION_LINE_MODIFIER_NAME,
        chain_id_attr_name=SELECTION_LINE_CHAIN_ID_ATTR,
        marked_attr_name=FREESTYLE_EDGE_ATTR,
    )


def register() -> None:
    pass


def unregister() -> None:
    pass
