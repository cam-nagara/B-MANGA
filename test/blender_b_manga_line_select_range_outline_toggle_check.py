"""B-MANGA Line: render-range selection and outline visibility toggle."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera(*, fisheye: bool = False) -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.clip_start = 0.01
    camera.data.clip_end = 100.0
    if fisheye:
        camera.data.type = "PANO"
        if hasattr(camera.data, "panorama_type"):
            camera.data.panorama_type = "FISHEYE_EQUIDISTANT"
        if hasattr(camera.data, "fisheye_fov"):
            camera.data.fisheye_fov = math.radians(180.0)
    else:
        camera.data.type = "PERSP"
        camera.data.lens = 50.0
    scene.camera = camera
    return camera


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _selected_names() -> set[str]:
    return {obj.name for obj in bpy.context.selected_objects}


def _assert_perspective_selection() -> None:
    _clear_scene()
    _make_camera()

    visible = _make_cube("BML_range_visible", (0.0, 0.0, -5.0))
    _make_cube("BML_range_offscreen", (6.0, 0.0, -5.0))
    _make_cube("BML_range_behind", (0.0, 0.0, 3.0))
    locked = _make_cube("BML_range_locked", (0.0, 0.0, -4.0))
    locked.hide_select = True

    assert bpy.ops.bmanga_line.select_render_range_meshes() == {"FINISHED"}
    selected = _selected_names()
    assert visible.name in selected
    assert "BML_range_offscreen" not in selected
    assert "BML_range_behind" not in selected
    assert locked.name not in selected
    assert bpy.context.view_layer.objects.active == visible


def _assert_fisheye_selection() -> None:
    _clear_scene()
    _make_camera(fisheye=True)

    wide_angle = _make_cube("BML_fisheye_range_visible", (5.0, 0.0, -1.0))
    _make_cube("BML_fisheye_range_outside", (6.0, 0.0, 3.0))

    assert bpy.ops.bmanga_line.select_render_range_meshes() == {"FINISHED"}
    selected = _selected_names()
    assert wide_angle.name in selected
    assert "BML_fisheye_range_outside" not in selected


def _assert_outline_toggle() -> None:
    _clear_scene()
    _make_camera()

    obj = _make_cube("BML_outline_toggle", (0.0, 0.0, -4.0))
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    assert presets.apply_line_settings(obj, bpy.context)

    outline_mod = obj.modifiers.get(core.MODIFIER_NAME)
    inner_mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert outline_mod is not None and outline_mod.show_viewport and outline_mod.show_render
    assert inner_mod is not None and inner_mod.show_viewport and inner_mod.show_render

    settings.outline_enabled = False
    assert not outline_mod.show_viewport
    assert not outline_mod.show_render
    assert inner_mod.show_viewport
    assert inner_mod.show_render

    settings.outline_enabled = True
    assert outline_mod.show_viewport
    assert outline_mod.show_render

    off_at_apply = _make_cube("BML_outline_off_at_apply", (2.0, 0.0, -4.0))
    off_settings = off_at_apply.bmanga_line_settings
    off_settings.outline_enabled = False
    off_settings.inner_line_enabled = True
    assert presets.apply_line_settings(off_at_apply, bpy.context)
    off_outline = off_at_apply.modifiers.get(core.MODIFIER_NAME)
    off_inner = off_at_apply.modifiers.get(core.GN_MODIFIER_NAME)
    if off_outline is not None:
        assert not off_outline.show_viewport
        assert not off_outline.show_render
    assert off_inner is not None and off_inner.show_viewport and off_inner.show_render

    core.set_line_visibility(off_at_apply, True)
    off_outline = off_at_apply.modifiers.get(core.MODIFIER_NAME)
    assert off_outline is None or not off_outline.show_viewport
    assert off_inner.show_viewport


def main() -> None:
    b_manga_line.register()
    try:
        _assert_perspective_selection()
        _assert_fisheye_selection()
        _assert_outline_toggle()
        print("[PASS] render-range selection and outline toggle work")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
