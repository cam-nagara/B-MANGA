"""Blender実機用: パターンカーブの編集ハンドル表示を確認する."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "pattern_curve_handle_visual_current"
MODULE_NAME = "bmanga_dev_pattern_curve_handle_visual"
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQCW"
    "A0r4AAAAAElFTkSuQmCC"
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_png(path: Path) -> None:
    path.write_bytes(base64.b64decode(PNG_1PX))


def _view3d_context():
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is None:
                continue
            return window, screen, area, region
    return None, None, None, None


def _configure_view(target_obj) -> tuple[object, object, object, object]:
    window, screen, area, region = _view3d_context()
    assert area is not None, "3Dビューが見つかりません"
    for view_area in screen.areas:
        if view_area.type != "VIEW_3D":
            continue
        for space in view_area.spaces:
            if space.type != "VIEW_3D":
                continue
            region3d = space.region_3d
            region3d.view_perspective = "ORTHO"
            region3d.view_rotation = (1.0, 0.0, 0.0, 0.0)
            region3d.view_location = (
                float(target_obj.location.x),
                float(target_obj.location.y),
                0.0,
            )
            region3d.view_distance = 0.12
            space.overlay.show_floor = False
            space.overlay.show_axis_x = False
            space.overlay.show_axis_y = False
            space.show_gizmo = True
            space.show_gizmo_object_translate = True
            space.shading.type = "SOLID"
            space.shading.light = "FLAT"
    return window, screen, area, region


def _run_check() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_pattern_curve_handle_"))
    mod = None
    try:
        try:
            bpy.context.preferences.view.show_splash = False
        except Exception:
            pass
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PatternCurveHandle.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file()
        assert "FINISHED" in result, result

        from bmanga_dev_pattern_curve_handle_visual.utils import image_path_object
        from bmanga_dev_pattern_curve_handle_visual.utils.layer_hierarchy import page_stack_key

        image_path = temp_root / "source.png"
        _write_png(image_path)

        scene = bpy.context.scene
        work = scene.bmanga_work
        page = work.pages[0]
        entry = scene.bmanga_image_path_layers.add()
        entry.id = "handle_visual"
        entry.title = "ハンドル表示確認"
        entry.filepath = str(image_path)
        entry.parent_kind = "page"
        entry.parent_key = page_stack_key(page)
        entry.path_points_json = json.dumps(
            [[12.0, 18.0], [36.0, 46.0], [66.0, 22.0], [92.0, 50.0]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        entry.draw_mode = "ribbon"
        entry.ribbon_repeat_mode = "repeat"
        entry.brush_size_mm = 8.0
        entry.aspect_ratio = 1.0
        entry.spacing_percent = 100.0
        entry.visible = True

        obj = image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
        curve_obj = bpy.data.objects.get("image_path_curve_handle_visual")
        assert obj is not None, "パターンカーブ実体がありません"
        assert curve_obj is not None and curve_obj.type == "CURVE", "編集用カーブがありません"
        assert not curve_obj.hide_viewport, "編集用カーブが非表示です"
        assert not curve_obj.hide_select, "編集用カーブが選択不可です"
        assert curve_obj.show_in_front, "編集用カーブが前面表示になっていません"
        assert curve_obj.hide_render, "編集用カーブがレンダー対象です"

        spline = curve_obj.data.splines[0]
        points = list(spline.bezier_points)
        assert len(points) == 4, f"制御点数が想定外です: {len(points)}"
        visible_handles = 0
        for point in points:
            assert point.handle_left_type != "AUTO", "左ハンドルが自動表示のままです"
            assert point.handle_right_type != "AUTO", "右ハンドルが自動表示のままです"
            if (point.handle_left - point.co).length > 1.0e-7:
                visible_handles += 1
            if (point.handle_right - point.co).length > 1.0e-7:
                visible_handles += 1
            point.select_control_point = True
            point.select_left_handle = True
            point.select_right_handle = True
        assert visible_handles >= 4, f"表示できるハンドルが少なすぎます: {visible_handles}"

        if curve_obj.name not in bpy.context.view_layer.objects:
            try:
                bpy.context.scene.collection.objects.link(curve_obj)
            except RuntimeError:
                pass
        bpy.context.view_layer.update()
        window, screen, area, region = _configure_view(curve_obj)
        bpy.ops.object.select_all(action="DESELECT")
        curve_obj.select_set(True)
        bpy.context.view_layer.objects.active = curve_obj
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
            active_object=curve_obj,
            object=curve_obj,
            selected_objects=[curve_obj],
            selected_editable_objects=[curve_obj],
        ):
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.curve.select_all(action="SELECT")

        screenshot = OUT_DIR / "pattern_curve_handle_visual.png"
        with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
            bpy.ops.screen.screenshot(filepath=str(screenshot))
        print(
            "BMANGA_PATTERN_CURVE_HANDLE_VISUAL_OK "
            f"points={len(points)} handles={visible_handles} image={screenshot}",
            flush=True,
        )
    finally:
        if mod is not None:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _tick():
    ok = False
    try:
        _run_check()
        ok = True
    except Exception:
        traceback.print_exc()
    finally:
        os._exit(0 if ok else 1)
    return None


if __name__ == "__main__":
    bpy.app.timers.register(_tick, first_interval=0.5)
