"""B-MANGA Liner の明示更新待ち状態を管理する."""

from __future__ import annotations

import bpy


LINE_TARGETS = ("outline", "inner", "intersection", "selection")
PROP_PENDING_TARGETS = "bml_pending_line_update_targets"
PROP_PENDING_CREATE_TARGETS = "bml_pending_line_create_targets"
PROP_PENDING_VISUAL_TARGETS = "bml_pending_line_visual_targets"

_LABELS = {
    "outline": "アウトライン",
    "inner": "稜谷線",
    "intersection": "交差線",
    "selection": "選択線",
}

_VISUAL_PROPS = {
    "outline_color",
    "inner_line_color",
    "intersection_color",
    "selection_line_color",
    "outline_thickness",
    "inner_line_thickness",
    "intersection_thickness",
    "selection_line_thickness",
    "outline_offset",
    "inner_line_offset",
    "intersection_line_offset",
    "selection_line_offset",
    "even_thickness",
    "use_rim",
    "hide_through_transparent",
    "use_camera_compensation",
    "camera_compensation_influence",
    "line_width_reference_distance",
    "use_uniform_line_width",
    "use_vertex_color",
    "auto_subdivision_for_midpoint",
    "match_subsurf_viewport_to_render",
    "lines_visible",
    "use_camera_culling",
    "culling_margin",
    "use_outline_distance_limit",
    "outline_max_distance",
    "use_inner_line_distance_limit",
    "inner_line_max_distance",
    "use_intersection_distance_limit",
    "intersection_max_distance",
    "use_selection_line_distance_limit",
    "selection_line_max_distance",
    "edge_smooth_factor",
    "edge_midpoint_jitter_percent",
    "edge_midpoint_angle",
    "edge_width_curve_25",
    "edge_width_curve_50",
    "edge_width_curve_75",
    "inner_edge_smooth_factor",
    "inner_edge_midpoint_jitter_percent",
    "inner_edge_width_curve_25",
    "inner_edge_width_curve_50",
    "inner_edge_width_curve_75",
    "intersection_edge_smooth_factor",
    "intersection_edge_midpoint_jitter_percent",
    "intersection_edge_midpoint_angle",
    "intersection_edge_width_curve_25",
    "intersection_edge_width_curve_50",
    "intersection_edge_width_curve_75",
    "selection_edge_smooth_factor",
    "selection_edge_midpoint_jitter_percent",
    "selection_edge_midpoint_angle",
    "selection_edge_width_curve_25",
    "selection_edge_width_curve_50",
    "selection_edge_width_curve_75",
}


def normalize_targets(targets=None) -> tuple[str, ...]:
    if targets is None:
        return LINE_TARGETS
    if isinstance(targets, str):
        targets = (targets,)
    normalized = []
    for target in targets:
        if target in LINE_TARGETS and target not in normalized:
            normalized.append(target)
    return tuple(normalized)


def targets_for_property(prop_name: str) -> tuple[str, ...]:
    if prop_name.startswith("inner_") or prop_name.startswith("use_inner_"):
        return ("inner",)
    if prop_name.startswith("intersection_") or prop_name.startswith("use_intersection_"):
        return ("intersection",)
    if prop_name.startswith("selection_") or prop_name.startswith("use_selection_"):
        return ("selection",)
    if prop_name.startswith("outline_") or prop_name.startswith("use_outline_"):
        return ("outline",)
    if prop_name in {
        "edge_smooth_factor",
        "edge_midpoint_jitter_percent",
        "edge_midpoint_angle",
        "even_thickness",
        "use_rim",
        "use_vertex_color",
        "hide_through_transparent",
        "weld_mesh_for_outline",
    }:
        return ("outline",)
    return LINE_TARGETS


def kind_for_property(prop_name: str) -> str:
    return "visual" if prop_name in _VISUAL_PROPS else "create"


def _pending_targets_for_prop(obj: bpy.types.Object, prop_name: str) -> tuple[str, ...]:
    raw = str(obj.get(prop_name, "") or "")
    targets = [item for item in raw.split(",") if item in LINE_TARGETS]
    return tuple(item for item in LINE_TARGETS if item in targets)


def pending_create_targets(obj: bpy.types.Object) -> tuple[str, ...]:
    legacy = _pending_targets_for_prop(obj, PROP_PENDING_TARGETS)
    current = _pending_targets_for_prop(obj, PROP_PENDING_CREATE_TARGETS)
    targets = set(legacy) | set(current)
    return tuple(item for item in LINE_TARGETS if item in targets)


def pending_visual_targets(obj: bpy.types.Object) -> tuple[str, ...]:
    return _pending_targets_for_prop(obj, PROP_PENDING_VISUAL_TARGETS)


def pending_targets(obj: bpy.types.Object) -> tuple[str, ...]:
    targets = set(pending_create_targets(obj)) | set(pending_visual_targets(obj))
    return tuple(item for item in LINE_TARGETS if item in targets)


def _pending_prop_for_kind(kind: str) -> str:
    return PROP_PENDING_VISUAL_TARGETS if kind == "visual" else PROP_PENDING_CREATE_TARGETS


def mark_pending(obj: bpy.types.Object, targets=None, *, kind: str = "create") -> None:
    if obj is None or obj.type != "MESH":
        return
    from . import core

    if core.is_settings_locked(obj):
        # ロック中は新たな作成待ち/更新待ち印を付けない（既存の印は保持し、解除後に再評価される）。
        return
    add = set(normalize_targets(targets))
    if not add:
        return
    pending_prop = _pending_prop_for_kind(kind)
    current = set(_pending_targets_for_prop(obj, pending_prop))
    if add.issubset(current):
        return
    current.update(add)
    if current:
        obj[pending_prop] = ",".join(
            target for target in LINE_TARGETS if target in current
        )


def mark_pending_many(objects, targets=None, *, kind: str = "create") -> None:
    for obj in objects:
        mark_pending(obj, targets, kind=kind)


def mark_property_pending(obj: bpy.types.Object, prop_name: str, targets=None) -> None:
    mark_pending(obj, targets, kind=kind_for_property(prop_name))


def mark_property_pending_many(objects, prop_name: str, targets=None) -> None:
    for obj in objects:
        mark_property_pending(obj, prop_name, targets)


def _clear_pending_prop(obj: bpy.types.Object, prop_name: str, targets=None) -> None:
    if prop_name not in obj:
        return
    remove = set(normalize_targets(targets))
    remain = [
        target for target in _pending_targets_for_prop(obj, prop_name)
        if target not in remove
    ]
    if remain:
        obj[prop_name] = ",".join(remain)
    else:
        del obj[prop_name]


def clear_pending(obj: bpy.types.Object, targets=None, *, kind: str | None = None) -> None:
    if obj is None or obj.type != "MESH":
        return
    props = (
        (PROP_PENDING_TARGETS, PROP_PENDING_CREATE_TARGETS, PROP_PENDING_VISUAL_TARGETS)
        if kind is None
        else (_pending_prop_for_kind(kind),)
    )
    for prop_name in props:
        _clear_pending_prop(obj, prop_name, targets)


def clear_pending_many(objects, targets=None, *, kind: str | None = None) -> None:
    for obj in objects:
        clear_pending(obj, targets, kind=kind)


def pending_label(obj: bpy.types.Object) -> str:
    create_targets = pending_create_targets(obj)
    visual_targets = pending_visual_targets(obj)
    if not create_targets and not visual_targets:
        return ""
    parts = []
    if create_targets:
        parts.append("作成待ち: " + " / ".join(_LABELS[target] for target in create_targets))
    if visual_targets:
        parts.append("更新待ち: " + " / ".join(_LABELS[target] for target in visual_targets))
    return "  ".join(parts)
