"""Selection helpers for B-MANGA Line."""

from __future__ import annotations

import bpy


def _add_unique_mesh_object(items: list[bpy.types.Object], obj) -> None:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    if obj not in items:
        items.append(obj)


def selected_mesh_objects(context, owner: bpy.types.Object) -> list[bpy.types.Object]:
    """Collect selected mesh objects even from restricted panel contexts."""
    items: list[bpy.types.Object] = []
    for obj in getattr(context, "selected_objects", ()) or ():
        _add_unique_mesh_object(items, obj)

    global_context = getattr(bpy, "context", None)
    for obj in getattr(global_context, "selected_objects", ()) or ():
        _add_unique_mesh_object(items, obj)

    scenes = []
    for scene in (
        getattr(context, "scene", None),
        getattr(global_context, "scene", None),
    ):
        if scene is not None and scene not in scenes:
            scenes.append(scene)
    for scene in getattr(owner, "users_scene", ()) or ():
        if scene is not None and scene not in scenes:
            scenes.append(scene)

    for scene in scenes:
        for obj in getattr(scene, "objects", ()) or ():
            try:
                selected = obj.select_get()
            except (ReferenceError, RuntimeError):
                selected = False
            if selected:
                _add_unique_mesh_object(items, obj)
    return items
