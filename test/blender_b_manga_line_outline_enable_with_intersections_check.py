"""B-MANGA Line: outline enable keeps existing shell intersections stable."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(index: int) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.4, location=((index - 1) * 0.45, 0.0, -4.0))
    obj = bpy.context.object
    obj.name = f"BML_outline_enable_intersection_{index}"
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.intersection_enabled = True
    settings.intersection_method = "SHELL"
    settings.use_intersection_creation_limit = False
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, -4.0, 1.0), rotation=(1.25, 0.0, 0.0))
        bpy.context.scene.camera = bpy.context.object
        objects = [_make_cube(index) for index in range(5)]
        _select(objects)
        assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {"FINISHED"}

        owners = []
        for obj in objects:
            if any(core.iter_intersection_modifiers(obj)):
                owners.append(obj)
        assert owners

        _select(owners)
        for obj in owners:
            obj.bmanga_line_settings.outline_enabled = False
        assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
        for obj in owners:
            outline = obj.modifiers.get(core.MODIFIER_NAME)
            assert outline is None or not outline.show_viewport, obj.name
            intersections = list(core.iter_intersection_modifiers(obj))
            assert intersections, obj.name

        for obj in owners:
            obj.bmanga_line_settings.outline_enabled = True
        assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
        for obj in owners:
            outline = obj.modifiers.get(core.MODIFIER_NAME)
            assert outline is not None and outline.show_viewport, obj.name
        for obj in owners:
            intersections = list(core.iter_intersection_modifiers(obj))
            assert intersections, obj.name
            assert all(mod.name == core.INTERSECTION_MODIFIER_NAME for mod in intersections)

        print("[PASS] outline enable keeps existing intersections stable")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
