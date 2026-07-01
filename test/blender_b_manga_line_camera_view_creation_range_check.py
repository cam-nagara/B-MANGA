"""B-MANGA Line: creation range also follows the camera view."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_lines, presets  # noqa: E402


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


def _make_cube(
    name: str,
    location: tuple[float, float, float],
    *,
    inner: bool = False,
    intersection: bool = False,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = inner
    settings.intersection_enabled = intersection
    settings.use_inner_line_creation_limit = True
    settings.inner_line_creation_max_distance = 10.0
    settings.use_intersection_creation_limit = True
    settings.intersection_creation_max_distance = 10.0
    return obj


def _apply(obj: bpy.types.Object) -> None:
    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        transforms_fresh=True,
    ), obj.name


def _has_inner(obj: bpy.types.Object) -> bool:
    return obj.modifiers.get(core.GN_MODIFIER_NAME) is not None


def _has_intersection(obj: bpy.types.Object) -> bool:
    return any(core.iter_intersection_modifiers(obj))


def _assert_perspective_creation_view() -> None:
    _clear_scene()
    _make_camera()

    visible_inner = _make_cube("BML_camera_view_inner_visible", (0.0, 0.0, -5.0), inner=True)
    offscreen_inner = _make_cube("BML_camera_view_inner_offscreen", (5.0, 0.0, -5.0), inner=True)
    for obj in (visible_inner, offscreen_inner):
        _apply(obj)

    assert _has_inner(visible_inner), "カメラ内の内部線が作成されていません"
    assert not _has_inner(offscreen_inner), "カメラ外の内部線が作成されています"

    source = _make_cube("BML_camera_view_intersection_A", (0.0, 0.0, -6.0), intersection=True)
    target = _make_cube("BML_camera_view_intersection_B", (0.35, 0.0, -6.0), intersection=True)
    off_a = _make_cube("BML_camera_view_intersection_C", (5.0, 0.0, -5.0), intersection=True)
    off_b = _make_cube("BML_camera_view_intersection_D", (5.35, 0.0, -5.0), intersection=True)
    for obj in (source, target, off_a, off_b):
        _apply(obj)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)

    assert _has_intersection(source), "カメラ内の交差線が作成されていません"
    assert not _has_intersection(off_a), "カメラ外の交差線が作成されています"
    assert not _has_intersection(off_b), "カメラ外の交差線が作成されています"


def _assert_fisheye_creation_view() -> None:
    _clear_scene()
    _make_camera(fisheye=True)

    wide_angle = _make_cube("BML_fisheye_inner_visible", (5.0, 0.0, -1.0), inner=True)
    outside_fisheye = _make_cube("BML_fisheye_inner_outside", (6.0, 0.0, 3.0), inner=True)
    for obj in (wide_angle, outside_fisheye):
        _apply(obj)

    assert _has_inner(wide_angle), "魚眼の範囲内の内部線が作成されていません"
    assert not _has_inner(outside_fisheye), "魚眼の範囲外の内部線が作成されています"


def main() -> None:
    b_manga_line.register()
    try:
        _assert_perspective_creation_view()
        _assert_fisheye_creation_view()
        print("[PASS] camera view limits generated inner and intersection lines")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
