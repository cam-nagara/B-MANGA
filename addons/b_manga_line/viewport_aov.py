"""B-MANGA Line viewport AOV display helpers."""

from __future__ import annotations

import json

import bpy

from .core import AOV_NAME


PROP_VIEW_STATE = "bml_line_aov_view_state"


def _view3d_spaces(context) -> list[bpy.types.SpaceView3D]:
    spaces: list[bpy.types.SpaceView3D] = []
    seen: set[int] = set()

    def _add_from_screen(screen) -> None:
        for area in getattr(screen, "areas", ()) or ():
            if getattr(area, "type", None) != "VIEW_3D":
                continue
            for space in area.spaces:
                if getattr(space, "type", None) != "VIEW_3D":
                    continue
                key = space.as_pointer()
                if key not in seen:
                    seen.add(key)
                    spaces.append(space)

    area = getattr(context, "area", None)
    if area is not None and getattr(area, "type", None) == "VIEW_3D":
        for space in area.spaces:
            if getattr(space, "type", None) == "VIEW_3D":
                seen.add(space.as_pointer())
                spaces.append(space)

    _add_from_screen(getattr(context, "screen", None))

    wm = getattr(context, "window_manager", None)
    for window in getattr(wm, "windows", ()) or ():
        _add_from_screen(getattr(window, "screen", None))
    return spaces


def _load_state(scene) -> dict[str, dict[str, str]]:
    raw = scene.get(PROP_VIEW_STATE, "{}")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(scene, data: dict[str, dict[str, str]]) -> None:
    if data:
        scene[PROP_VIEW_STATE] = json.dumps(data, ensure_ascii=False)
    elif PROP_VIEW_STATE in scene:
        del scene[PROP_VIEW_STATE]


def _space_key(space: bpy.types.SpaceView3D) -> str:
    return str(space.as_pointer())


def _capture_shading(space: bpy.types.SpaceView3D) -> dict[str, str]:
    shading = space.shading
    return {
        "type": str(shading.type),
        "render_pass": str(getattr(shading, "render_pass", "COMBINED")),
        "aov_name": str(getattr(shading, "aov_name", "")),
    }


def _restore_shading(space: bpy.types.SpaceView3D, state: dict[str, str]) -> None:
    shading = space.shading
    for name in ("type", "render_pass", "aov_name"):
        if name not in state or not hasattr(shading, name):
            continue
        try:
            setattr(shading, name, state[name])
        except (AttributeError, TypeError, ValueError):
            pass


def is_line_aov_active(context) -> bool:
    for space in _view3d_spaces(context):
        if _space_is_line_aov(space):
            return True
    return False


def _space_is_line_aov(space: bpy.types.SpaceView3D) -> bool:
    shading = space.shading
    return (
        getattr(shading, "type", "") == "RENDERED"
        and getattr(shading, "render_pass", "") in {"AOV", AOV_NAME}
        and getattr(shading, "aov_name", "") == AOV_NAME
    )


def _set_line_aov_pass(shading) -> None:
    try:
        shading.render_pass = "AOV"
    except (TypeError, ValueError):
        shading.render_pass = AOV_NAME
    shading.aov_name = AOV_NAME


def enable_line_aov(context) -> bool:
    from . import outline_setup

    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    spaces = _view3d_spaces(context)
    if not spaces:
        return False

    outline_setup.ensure_aov_passes(scene)
    state = _load_state(scene)
    changed = False
    for space in spaces:
        key = _space_key(space)
        saved = _capture_shading(space)
        if key not in state and not _space_is_line_aov(space):
            state[key] = saved
        shading = space.shading
        try:
            shading.type = "RENDERED"
            _set_line_aov_pass(shading)
            changed = True
        except (AttributeError, TypeError, ValueError):
            _restore_shading(space, saved)
            if key in state and state[key] == saved:
                del state[key]
            continue
    _save_state(scene, state)
    return changed


def disable_line_aov(context) -> bool:
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    spaces = _view3d_spaces(context)
    if not spaces:
        return False

    state = _load_state(scene)
    changed = False
    for space in spaces:
        key = _space_key(space)
        saved = state.pop(key, None)
        if saved is not None:
            _restore_shading(space, saved)
            changed = True
            continue
        shading = space.shading
        if (
            getattr(shading, "render_pass", "") in {"AOV", AOV_NAME}
            and getattr(shading, "aov_name", "") == AOV_NAME
        ):
            try:
                shading.render_pass = "COMBINED"
                shading.aov_name = ""
                changed = True
            except (AttributeError, TypeError, ValueError):
                pass
    _save_state(scene, state)
    return changed
