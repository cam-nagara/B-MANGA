"""B-MANGA Line modifier stack ordering helpers."""

from __future__ import annotations

import bpy

from .core import (
    GN_MODIFIER_NAME,
    INTERSECTION_MODIFIER_NAME,
    INTERSECTION_MODIFIER_PREFIX,
    MODIFIER_NAME,
    OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
    SELECTION_LINE_MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
)


def is_line_modifier_name(name: str) -> bool:
    """B-MANGA Line が生成するモディファイア名か返す."""
    return (
        name == SHEET_OUTLINE_MODIFIER_NAME
        or name == OUTLINE_WIDTH_ATTR_MODIFIER_NAME
        or name == MODIFIER_NAME
        or name == GN_MODIFIER_NAME
        or name == SELECTION_LINE_MODIFIER_NAME
        or name == INTERSECTION_MODIFIER_NAME
        or name.startswith(INTERSECTION_MODIFIER_PREFIX)
    )


def _line_modifier_order(mod: bpy.types.Modifier) -> tuple[int, str]:
    name = mod.name
    # シートのチューブは Solidify より前（境界辺を元メッシュから拾うため）
    if name == SHEET_OUTLINE_MODIFIER_NAME:
        return (0, name)
    if name == OUTLINE_WIDTH_ATTR_MODIFIER_NAME:
        return (1, name)
    if name == MODIFIER_NAME:
        return (2, name)
    if name == GN_MODIFIER_NAME:
        return (3, name)
    if name == SELECTION_LINE_MODIFIER_NAME:
        return (4, name)
    if name == INTERSECTION_MODIFIER_NAME or name.startswith(INTERSECTION_MODIFIER_PREFIX):
        return (5, name)
    return (99, name)


def _is_auto_smooth_modifier(mod: bpy.types.Modifier) -> bool:
    return mod.name == "Smooth by Angle" and mod.type == "NODES"


def reorder_line_modifiers(obj: bpy.types.Object) -> None:
    """既存のメッシュ調整後に、アウトライン/内部線/交差線を安定配置する."""
    if obj.type != "MESH":
        return
    modifiers = list(obj.modifiers)
    line_mods = [mod for mod in modifiers if is_line_modifier_name(mod.name)]
    if not line_mods:
        return
    base_index = sum(
        1
        for mod in modifiers
        if not is_line_modifier_name(mod.name) and not _is_auto_smooth_modifier(mod)
    )
    for mod in sorted(line_mods, key=_line_modifier_order):
        try:
            current = list(obj.modifiers).index(mod)
        except ValueError:
            continue
        target = base_index
        base_index += 1
        if current != target:
            obj.modifiers.move(current, target)
