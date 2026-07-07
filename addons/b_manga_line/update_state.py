"""B-MANGA Liner の明示更新待ち状態を管理する."""

from __future__ import annotations

import bpy


LINE_TARGETS = ("outline", "inner", "intersection", "selection")
PROP_PENDING_TARGETS = "bml_pending_line_update_targets"

_LABELS = {
    "outline": "アウトライン",
    "inner": "稜谷線",
    "intersection": "交差線",
    "selection": "選択線",
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
    }:
        return ("outline",)
    return LINE_TARGETS


def pending_targets(obj: bpy.types.Object) -> tuple[str, ...]:
    raw = str(obj.get(PROP_PENDING_TARGETS, "") or "")
    targets = [item for item in raw.split(",") if item in LINE_TARGETS]
    return tuple(item for item in LINE_TARGETS if item in targets)


def mark_pending(obj: bpy.types.Object, targets=None) -> None:
    if obj is None or obj.type != "MESH":
        return
    current = set(pending_targets(obj))
    current.update(normalize_targets(targets))
    if current:
        obj[PROP_PENDING_TARGETS] = ",".join(
            target for target in LINE_TARGETS if target in current
        )


def mark_pending_many(objects, targets=None) -> None:
    for obj in objects:
        mark_pending(obj, targets)


def clear_pending(obj: bpy.types.Object, targets=None) -> None:
    if obj is None or obj.type != "MESH" or PROP_PENDING_TARGETS not in obj:
        return
    remove = set(normalize_targets(targets))
    remain = [target for target in pending_targets(obj) if target not in remove]
    if remain:
        obj[PROP_PENDING_TARGETS] = ",".join(remain)
    else:
        del obj[PROP_PENDING_TARGETS]


def clear_pending_many(objects, targets=None) -> None:
    for obj in objects:
        clear_pending(obj, targets)


def pending_label(obj: bpy.types.Object) -> str:
    targets = pending_targets(obj)
    if not targets:
        return ""
    return "未更新: " + " / ".join(_LABELS[target] for target in targets)

