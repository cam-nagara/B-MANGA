"""Blender 実機(UI)用: カメラビュー中のB-MANGA座標変換とビュー操作確認."""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_camera_view_nav",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_camera_view_nav"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d_context():
    for area in bpy.context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        rv3d = getattr(area.spaces.active, "region_3d", None)
        if region is not None and rv3d is not None:
            return area, region, rv3d
    raise AssertionError("3Dビューポートが見つかりません")


def _setup_camera(rv3d):
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("B-MANGA camera view test")
    cam = bpy.data.objects.new("B-MANGA camera view test", cam_data)
    scene.collection.objects.link(cam)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 1.0
    cam.location = (0.0, 0.0, 10.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam["bmanga_overview_camera"] = True
    scene.camera = cam
    rv3d.view_perspective = "CAMERA"
    rv3d.view_camera_offset = (0.0, 0.0)
    rv3d.view_camera_zoom = -30.0
    try:
        rv3d.update()
    except Exception:
        pass


def _assert_camera_round_trip(region, rv3d) -> None:
    from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_location_3d

    src = Vector((0.05, -0.03, 0.0))
    screen = location_3d_to_region_2d(region, rv3d, src)
    assert screen is not None, "カメラビューで紙面座標を画面座標へ変換できません"
    dst = region_2d_to_location_3d(region, rv3d, (screen.x, screen.y), (0.0, 0.0, 0.0))
    assert abs(dst.x - src.x) < 1.0e-5 and abs(dst.y - src.y) < 1.0e-5, (
        f"カメラビューの画面座標から紙面座標への逆変換がずれています: {src} -> {dst}"
    )


def _run() -> None:
    mod = _load_addon()
    from bmanga_dev_camera_view_nav.utils import camera_view_navigation
    from bmanga_dev_camera_view_nav.keymap import viewport_ops

    try:
        _area, region, rv3d = _view3d_context()
        _setup_camera(rv3d)
        assert camera_view_navigation.is_camera_view(rv3d), "カメラビューとして判定されません"

        _assert_camera_round_trip(region, rv3d)

        offset_before = tuple(rv3d.view_camera_offset)
        assert camera_view_navigation.pan(rv3d, region, 120.0, -60.0), "カメラビューのパンが実行されません"
        offset_after = tuple(rv3d.view_camera_offset)
        assert offset_after[0] < offset_before[0], "右方向ドラッグでカメラビューが追従しません"
        assert offset_after[1] > offset_before[1], "上方向ドラッグでカメラビューが追従しません"

        zoom_before = float(rv3d.view_camera_zoom)
        assert camera_view_navigation.zoom_absolute(rv3d, zoom_before, 120.0), "カメラビューのズームが実行されません"
        assert float(rv3d.view_camera_zoom) > zoom_before, "右方向ドラッグでズームインしません"

        zoom_mid = float(rv3d.view_camera_zoom)
        assert camera_view_navigation.step_zoom(rv3d, "OUT"), "カメラビューのステップズームが実行されません"
        assert float(rv3d.view_camera_zoom) < zoom_mid, "ズームアウトしません"

        from bpy_extras.view3d_utils import location_3d_to_region_2d

        loc_before = tuple(bpy.context.scene.camera.location)
        point = Vector((0.1, 0.0, 0.0))
        screen_before = location_3d_to_region_2d(region, rv3d, point)
        assert camera_view_navigation.rotate_overview_camera(rv3d, 0.5), "ページ一覧用カメラの回転が実行されません"
        screen_after = location_3d_to_region_2d(region, rv3d, point)
        assert screen_before is not None and screen_after is not None, "カメラ回転前後の画面座標を取得できません"
        moved = abs(screen_after.x - screen_before.x) + abs(screen_after.y - screen_before.y)
        assert moved > 1.0, "ページ一覧用カメラを回しても画面上の見え方が変わりません"
        assert tuple(bpy.context.scene.camera.location) == loc_before, "カメラ回転でカメラ位置が変わっています"
        _assert_camera_round_trip(region, rv3d)
        assert camera_view_navigation.reset_overview_camera_rotation(rv3d), "ページ一覧用カメラの回転リセットが実行されません"
        assert abs(float(bpy.context.scene.camera.rotation_euler.z)) < 1.0e-6, "ページ一覧用カメラの回転がリセットされません"

        op = SimpleNamespace(_rv3d=rv3d, _region=region)
        viewport_ops.BMANGA_OT_view_navigate._apply_rotation(op, 0.25)
        assert abs(float(bpy.context.scene.camera.rotation_euler.z) + 0.25) < 1.0e-5, (
            "Shift+Space回転の実行経路でページ一覧用カメラが回りません"
        )
        viewport_ops.BMANGA_OT_view_navigate._reset_rotation(op, bpy.context)
        assert abs(float(bpy.context.scene.camera.rotation_euler.z)) < 1.0e-6, (
            "Shift+Space回転リセットの実行経路でページ一覧用カメラが戻りません"
        )

        assert camera_view_navigation.reset_view(rv3d), "カメラビューのリセットが実行されません"
        assert tuple(rv3d.view_camera_offset) == (0.0, 0.0), "カメラビューの表示位置がリセットされません"
        print("BMANGA_CAMERA_VIEW_NAVIGATION_CHECK_OK", flush=True)
    finally:
        mod.unregister()


def _tick():
    try:
        _run()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        os._exit(1)


def main() -> None:
    bpy.app.timers.register(_tick, first_interval=0.25)


if __name__ == "__main__":
    main()
