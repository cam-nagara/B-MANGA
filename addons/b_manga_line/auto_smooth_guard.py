"""自動スムーズの保存時復旧ガード."""

from __future__ import annotations

import math

import bpy
from bpy.app.handlers import persistent


AUTO_SMOOTH_NAME = "Smooth by Angle"
ANGLE_SOCKET_ID = "Input_1"
IGNORE_SHARPNESS_SOCKET_ID = "Socket_1"
STORED_ANGLE_PROP = "bml_auto_smooth_angle"
STORED_IGNORE_SHARPNESS_PROP = "bml_auto_smooth_ignore_sharpness"
OBJ_STORED_ANGLE_PROP = "bml_auto_smooth_saved_angle"
OBJ_STORED_IGNORE_SHARPNESS_PROP = "bml_auto_smooth_saved_ignore_sharpness"


def _angle_prop_name(mod_name: str) -> str:
    return f"{OBJ_STORED_ANGLE_PROP}:{mod_name}"


def _ignore_sharpness_prop_name(mod_name: str) -> str:
    return f"{OBJ_STORED_IGNORE_SHARPNESS_PROP}:{mod_name}"


def _is_smooth_modifier(mod: bpy.types.Modifier) -> bool:
    if getattr(mod, "type", None) != "NODES":
        return False
    group = getattr(mod, "node_group", None)
    return mod.name == AUTO_SMOOTH_NAME or getattr(group, "name", "") == AUTO_SMOOTH_NAME


def _has_interface_socket(group: bpy.types.NodeTree, name: str) -> bool:
    for item in getattr(group.interface, "items_tree", []) or []:
        if getattr(item, "name", None) == name and getattr(item, "in_out", None) == "INPUT":
            return True
    return False


def _is_valid_smooth_modifier(mod: bpy.types.Modifier) -> bool:
    group = getattr(mod, "node_group", None)
    if group is None:
        return False
    return _has_interface_socket(group, "Angle") and _has_interface_socket(group, "Ignore Sharpness")


def _is_line_object(obj: bpy.types.Object) -> bool:
    try:
        from .line_visibility import has_line

        return has_line(obj)
    except Exception:  # noqa: BLE001
        return False


def _modifier_index(obj: bpy.types.Object, mod: bpy.types.Modifier) -> int:
    try:
        return list(obj.modifiers).index(mod)
    except ValueError:
        return len(obj.modifiers)


def _remember_modifier_settings(obj: bpy.types.Object, mod: bpy.types.Modifier) -> None:
    try:
        angle = float(mod.get(ANGLE_SOCKET_ID, math.radians(30.0)))
        ignore_sharpness = bool(mod.get(IGNORE_SHARPNESS_SOCKET_ID, False))
        mod[STORED_ANGLE_PROP] = angle
        mod[STORED_IGNORE_SHARPNESS_PROP] = ignore_sharpness
        obj[OBJ_STORED_ANGLE_PROP] = angle
        obj[OBJ_STORED_IGNORE_SHARPNESS_PROP] = ignore_sharpness
        obj[_angle_prop_name(mod.name)] = angle
        obj[_ignore_sharpness_prop_name(mod.name)] = ignore_sharpness
    except Exception:  # noqa: BLE001
        pass


def _restore_selection(context, active, selected, mode: str) -> None:
    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:  # noqa: BLE001
        pass
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if active is not None and active.name in bpy.data.objects:
            context.view_layer.objects.active = active
        if active is not None and mode != "OBJECT" and active.name in bpy.data.objects:
            bpy.ops.object.mode_set(mode=mode)
    except Exception:  # noqa: BLE001
        pass


def _repair_modifier(obj: bpy.types.Object, mod: bpy.types.Modifier, context) -> bool:
    index = _modifier_index(obj, mod)
    name = mod.name
    angle = float(
        obj.get(
            _angle_prop_name(name),
            obj.get(
                OBJ_STORED_ANGLE_PROP,
                mod.get(
                    STORED_ANGLE_PROP,
                    mod.get(ANGLE_SOCKET_ID, math.radians(30.0)),
                ),
            ),
        )
        or math.radians(30.0)
    )
    ignore_sharpness = bool(
        obj.get(
            _ignore_sharpness_prop_name(name),
            obj.get(
                OBJ_STORED_IGNORE_SHARPNESS_PROP,
                mod.get(
                    STORED_IGNORE_SHARPNESS_PROP,
                    mod.get(IGNORE_SHARPNESS_SOCKET_ID, False),
                ),
            ),
        )
    )
    show_viewport = bool(getattr(mod, "show_viewport", True))
    show_render = bool(getattr(mod, "show_render", True))

    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    mode = getattr(active, "mode", "OBJECT") if active is not None else "OBJECT"

    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        obj.modifiers.remove(mod)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.shade_auto_smooth(angle=angle)
        new_mod = obj.modifiers.get(AUTO_SMOOTH_NAME)
        if new_mod is None or not _is_valid_smooth_modifier(new_mod):
            return False
        new_mod.name = name
        new_mod[ANGLE_SOCKET_ID] = angle
        new_mod[IGNORE_SHARPNESS_SOCKET_ID] = ignore_sharpness
        _remember_modifier_settings(obj, new_mod)
        new_mod.show_viewport = show_viewport
        new_mod.show_render = show_render
        current = _modifier_index(obj, new_mod)
        target = min(index, len(obj.modifiers) - 1)
        if current != target:
            obj.modifiers.move(current, target)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        _restore_selection(context, active, selected, mode)


def ensure_auto_smooth_nodes(objects=None, context=None) -> int:
    """壊れた自動スムーズを見つけて復旧し、復旧数を返す."""
    context = context or bpy.context
    if objects is None:
        objects = list(getattr(context.scene, "objects", []) or [])
    repaired = 0
    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        if not _is_line_object(obj):
            continue
        for mod in list(getattr(obj, "modifiers", []) or []):
            if not _is_smooth_modifier(mod):
                continue
            if _is_valid_smooth_modifier(mod):
                _remember_modifier_settings(obj, mod)
                continue
            if _repair_modifier(obj, mod, context):
                repaired += 1
    return repaired


@persistent
def _on_save_pre(_filepath) -> None:
    ensure_auto_smooth_nodes()


@persistent
def _on_save_post(_filepath) -> None:
    ensure_auto_smooth_nodes()


@persistent
def _on_load_post(_filepath) -> None:
    ensure_auto_smooth_nodes()


def _append_once(handler_list, handler) -> None:
    if handler not in handler_list:
        handler_list.append(handler)


def _remove(handler_list, handler) -> None:
    if handler in handler_list:
        handler_list.remove(handler)


def register() -> None:
    _append_once(bpy.app.handlers.save_pre, _on_save_pre)
    _append_once(bpy.app.handlers.save_post, _on_save_post)
    _append_once(bpy.app.handlers.load_post, _on_load_post)


def unregister() -> None:
    _remove(bpy.app.handlers.save_pre, _on_save_pre)
    _remove(bpy.app.handlers.save_post, _on_save_post)
    _remove(bpy.app.handlers.load_post, _on_load_post)
