"""B-MANGA Line visibility helpers."""

from __future__ import annotations

import bpy

from .core import (
    GN_MODIFIER_NAME,
    INTERSECTION_MODIFIER_NAME,
    INTERSECTION_MODIFIER_PREFIX,
    LINE_MODIFIER_NAMES,
    MODIFIER_NAME,
    OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME,
    PROP_LINES_HIDDEN,
    SELECTION_LINE_MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
)


def has_outline(obj: bpy.types.Object) -> bool:
    from . import outline_local_subdivision

    return (
        obj.type == "MESH"
        and (
            obj.modifiers.get(MODIFIER_NAME) is not None
            or outline_local_subdivision.get_modifier(obj) is not None
            or obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is not None
        )
    )


def is_intersection_modifier_name(name: str) -> bool:
    return (
        name == INTERSECTION_MODIFIER_NAME
        or name.startswith(INTERSECTION_MODIFIER_PREFIX)
    )


def iter_intersection_modifiers(obj: bpy.types.Object):
    if obj.type != "MESH":
        return
    for mod in obj.modifiers:
        if is_intersection_modifier_name(mod.name):
            yield mod


def iter_line_modifiers(obj: bpy.types.Object):
    if obj.type != "MESH":
        return
    seen: set[int] = set()
    from . import outline_local_subdivision

    for name in LINE_MODIFIER_NAMES:
        mod = obj.modifiers.get(name)
        if (
            mod is not None
            and (
                name != OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME
                or outline_local_subdivision.is_modifier(mod)
            )
        ):
            seen.add(mod.as_pointer())
            yield mod

    local_mod = outline_local_subdivision.get_modifier(obj)
    if local_mod is not None and local_mod.as_pointer() not in seen:
        yield local_mod
    yield from iter_intersection_modifiers(obj)


def iter_target_line_modifiers(obj: bpy.types.Object, targets):
    if obj.type != "MESH":
        return
    target_set = set(targets or ())
    if "outline" in target_set:
        from . import outline_local_subdivision

        for mod in (
            obj.modifiers.get(MODIFIER_NAME),
            outline_local_subdivision.get_modifier(obj),
            obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME),
        ):
            if mod is not None:
                yield mod
    if "inner" in target_set:
        mod = obj.modifiers.get(GN_MODIFIER_NAME)
        if mod is not None:
            yield mod
    if "selection" in target_set:
        mod = obj.modifiers.get(SELECTION_LINE_MODIFIER_NAME)
        if mod is not None:
            yield mod
    if "intersection" in target_set:
        yield from iter_intersection_modifiers(obj)


def has_line(obj: bpy.types.Object) -> bool:
    return obj.type == "MESH" and any(iter_line_modifiers(obj))


def _line_modifier_enabled_by_settings(
    obj: bpy.types.Object,
    mod: bpy.types.Modifier,
) -> bool:
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None:
        return True
    if mod.name == GN_MODIFIER_NAME:
        from . import plane_filter
        return (
            bool(getattr(settings, "inner_line_enabled", False))
            and not plane_filter.should_skip_inner_lines(obj, settings)
        )
    if is_intersection_modifier_name(mod.name):
        from . import plane_filter
        return (
            bool(getattr(settings, "intersection_enabled", False))
            and not plane_filter.should_exclude_generated_lines(obj, settings)
        )
    if mod.name == SELECTION_LINE_MODIFIER_NAME:
        return bool(getattr(settings, "selection_line_enabled", False))
    from . import outline_local_subdivision

    local_mod = outline_local_subdivision.get_modifier(obj)
    if (
        mod.name == MODIFIER_NAME
        and local_mod is not None
        and bool(getattr(settings, "auto_subdivision_for_midpoint", False))
        and outline_local_subdivision.resolve_camera(obj) is not None
    ):
        # BML_Outline is retained as settings storage while the camera-outline
        # curve is active. Enabling both would draw two outlines.
        return False
    if (
        mod.name in (MODIFIER_NAME, SHEET_OUTLINE_MODIFIER_NAME)
        or mod == local_mod
    ):
        return bool(getattr(settings, "outline_enabled", True))
    return True


def set_line_visibility(obj: bpy.types.Object, visible: bool) -> bool:
    mods = list(iter_line_modifiers(obj))
    if not mods:
        return False
    for mod in mods:
        mod_visible = visible and _line_modifier_enabled_by_settings(obj, mod)
        if mod.show_viewport != mod_visible:
            mod.show_viewport = mod_visible
        if mod.show_render != mod_visible:
            mod.show_render = mod_visible
    obj[PROP_LINES_HIDDEN] = not visible
    try:
        from . import core

        core.sync_line_visibility_setting(obj)
    except Exception:  # noqa: BLE001 - UI状態同期に失敗しても表示切替は維持する
        pass
    return True


def set_line_targets_visibility(obj: bpy.types.Object, visible: bool, targets) -> bool:
    mods = list(iter_target_line_modifiers(obj, targets))
    if not mods:
        return False
    changed = False
    for mod in mods:
        mod_visible = visible and _line_modifier_enabled_by_settings(obj, mod)
        if mod.show_viewport != mod_visible:
            mod.show_viewport = mod_visible
            changed = True
        if mod.show_render != mod_visible:
            mod.show_render = mod_visible
            changed = True
    return changed
