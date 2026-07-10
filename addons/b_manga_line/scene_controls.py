"""B-MANGA Liner scene-wide display controls."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty

_lines_visible_scene_updating = False


def _scene_mesh_objects(scene: bpy.types.Scene | None) -> list[bpy.types.Object]:
    if scene is None:
        return []
    return [
        obj for obj in scene.objects
        if getattr(obj, "type", None) == "MESH"
    ]


def _set_scene_lines_visible_setting(scene, enabled: bool) -> None:
    if scene is None or not hasattr(scene, "bmanga_line_lines_visible"):
        return
    global _lines_visible_scene_updating
    old = _lines_visible_scene_updating
    _lines_visible_scene_updating = True
    try:
        scene.bmanga_line_lines_visible = bool(enabled)
    finally:
        _lines_visible_scene_updating = old


def set_scene_lines_visible(context, visible: bool) -> int:
    """シーン内すべてのB-MANGA Linerライン表示を切り替える."""
    from . import core

    scene = getattr(context, "scene", None)
    visible = bool(visible)
    _set_scene_lines_visible_setting(scene, visible)
    changed = 0
    old = core._propagating
    core._propagating = True
    try:
        for obj in _scene_mesh_objects(scene):
            settings = getattr(obj, "bmanga_line_settings", None)
            if settings is not None:
                settings.lines_visible = visible
                core.record_override_edits(obj)
            if core.has_line(obj) and core.set_line_visibility(obj, visible):
                changed += 1
    finally:
        core._propagating = old
    if visible:
        core._refresh_print_widths(context)
    return changed


def on_object_lines_visible_changed(self, context):
    from . import core

    if core._propagating:
        return
    set_scene_lines_visible(context, bool(self.lines_visible))


def _on_scene_lines_visible_changed(self, context):
    if _lines_visible_scene_updating:
        return
    set_scene_lines_visible(context, bool(self.bmanga_line_lines_visible))


def register_scene_properties() -> None:
    unregister_scene_properties()
    bpy.types.Scene.bmanga_line_lines_visible = BoolProperty(
        name="ラインを表示",
        description="シーン内すべてのB-MANGA Linerライン表示を切り替える",
        default=True,
        update=_on_scene_lines_visible_changed,
    )


def unregister_scene_properties() -> None:
    if hasattr(bpy.types.Scene, "bmanga_line_lines_visible"):
        del bpy.types.Scene.bmanga_line_lines_visible
