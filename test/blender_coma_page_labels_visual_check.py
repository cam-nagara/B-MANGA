"""Blender UI visual check: coma file page labels/work info on page previews."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_coma_page_labels_visual"
OUT_DIR = ROOT / ".codex" / "visual" / "coma_page_labels"
SCREENSHOT = OUT_DIR / "coma_page_labels_visual.png"
STAGE_FILE = OUT_DIR / "stage.txt"


def _mark(stage: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGE_FILE.write_text(stage, encoding="utf-8")


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_work_info(work) -> None:
    info = work.work_info
    info.work_name = "WORK"
    info.author = "AUTH"
    info.display_visible = True
    info.display_work_name.enabled = True
    info.display_work_name.position = "bottom-left"
    info.display_work_name.font_size_q = 80.0
    info.display_work_name.color = (1.0, 0.0, 1.0, 1.0)
    info.display_author.enabled = True
    info.display_author.position = "bottom-right"
    info.display_author.font_size_q = 64.0
    info.display_author.color = (0.0, 0.35, 1.0, 1.0)
    info.display_page_number.enabled = True
    info.display_page_number.position = "bottom-center"
    info.display_page_number.font_size_q = 72.0
    info.display_page_number.color = (1.0, 0.0, 0.0, 1.0)


def _prepare_work() -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_page_labels_visual_"))
    work_dir = temp_root / "ComaPageLabelsVisual.bmanga"
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    if result != {"FINISHED"}:
        raise AssertionError(f"work_new failed: {result}")
    for _ in range(5):
        result = bpy.ops.bmanga.page_add()
        if result != {"FINISHED"}:
            raise AssertionError(f"page_add failed: {result}")
    result = bpy.ops.bmanga.pages_merge_spread("EXEC_DEFAULT", left_index=1)
    if result != {"FINISHED"}:
        raise AssertionError(f"pages_merge_spread failed: {result}")
    scene = bpy.context.scene
    work = scene.bmanga_work
    _set_work_info(work)
    work.active_page_index = 0
    work.pages[0].active_coma_index = 0
    scene.bmanga_overview_cols = 3
    scene.bmanga_overview_gap_x_mm = 28.0
    scene.bmanga_overview_gap_y_mm = 42.0
    scene.bmanga_page_preview_range_mode = "ALL"
    scene.bmanga_page_work_info_visible = True
    scene.bmanga_page_guides_visible = True
    scene.bmanga_page_preview_enabled = True
    result = bpy.ops.bmanga.work_save()
    if result != {"FINISHED"}:
        raise AssertionError(f"work_save failed: {result}")
    result = bpy.ops.bmanga.open_page_file(index=0)
    if result != {"FINISHED"}:
        raise AssertionError(f"open_page_file failed: {result}")
    scene = bpy.context.scene
    scene.bmanga_page_preview_range_mode = "ALL"
    scene.bmanga_page_work_info_visible = True
    scene.bmanga_page_guides_visible = True
    scene.bmanga_page_preview_enabled = True
    result = bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
    if result != {"FINISHED"}:
        raise AssertionError(f"enter_coma_mode failed: {result}")
    scene = bpy.context.scene
    scene.bmanga_page_preview_range_mode = "ALL"
    scene.bmanga_page_work_info_visible = True
    scene.bmanga_page_guides_visible = True
    scene.bmanga_page_preview_enabled = True
    settings = scene.bmanga_coma_camera_settings
    settings.name_bg_images_opacity = 100.0
    settings.own_page_opacity = 100.0
    settings.koma_bg_images_opacity = 100.0
    settings.bg_images_scale = 1.0
    settings.name_visible = True
    try:
        from bmanga_dev_coma_page_labels_visual.utils import coma_camera
        coma_camera.refresh_coma_page_overview(bpy.context)
        coma_camera.view_camera_in_viewports(bpy.context)
    except Exception:  # noqa: BLE001
        pass
    return temp_root


def _tag_redraw() -> None:
    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        try:
            area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass


def _view3d_area():
    window = next(iter(getattr(bpy.context.window_manager, "windows", []) or []), None)
    screen = getattr(window, "screen", None)
    if window is None or screen is None:
        return None, None, None, None, None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        space = area.spaces.active
        rv3d = getattr(space, "region_3d", None)
        if region is not None and rv3d is not None:
            return window, screen, area, region, space
    return None, None, None, None, None


def _prepare_capture_view() -> None:
    window, screen, area, region, space = _view3d_area()
    if window is None or screen is None or area is None or region is None or space is None:
        return
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return
    try:
        space.show_region_toolbar = False
        space.show_region_ui = False
        space.show_gizmo = False
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
    except Exception:  # noqa: BLE001
        pass
    try:
        rv3d.view_perspective = "CAMERA"
        rv3d.view_camera_zoom = -52.0
        rv3d.view_camera_offset = (0.0, 0.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
            space_data=space,
            region_data=rv3d,
        ):
            if bpy.ops.screen.screen_full_area.poll():
                bpy.ops.screen.screen_full_area(use_hide_panels=True)
    except Exception:  # noqa: BLE001
        pass
    window, screen, area, region, space = _view3d_area()
    rv3d = getattr(space, "region_3d", None) if space is not None else None
    if rv3d is None:
        return
    try:
        rv3d.view_perspective = "CAMERA"
        rv3d.view_camera_zoom = -52.0
        rv3d.view_camera_offset = (0.0, 0.0)
    except Exception:  # noqa: BLE001
        pass


def _assert_label_colors_visible(path: Path) -> None:
    from PIL import Image

    with Image.open(str(path)) as opened:
        image = opened.convert("RGBA")
        pixels = list(image.getdata())
    magenta = sum(1 for r, g, b, a in pixels if a > 200 and r > 180 and g < 80 and b > 180)
    red = sum(1 for r, g, b, a in pixels if a > 200 and r > 180 and g < 90 and b < 90)
    blue = sum(1 for r, g, b, a in pixels if a > 200 and r < 90 and 40 < g < 170 and b > 170)
    if magenta < 10:
        raise AssertionError("作品情報がスクリーンショットに表示されていません")
    if red < 10:
        raise AssertionError("ページ番号がスクリーンショットに表示されていません")
    if blue < 10:
        raise AssertionError("作者表示がスクリーンショットに表示されていません")


def main() -> None:
    _mark("start")
    if SCREENSHOT.exists():
        SCREENSHOT.unlink()
    mod = None
    temp_root = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        temp_root = _prepare_work()
        _mark("scene_ready")
    except Exception as exc:  # noqa: BLE001
        _mark(f"setup_failed: {exc}")
        raise

    state = {"done": False}

    def _capture():
        if state["done"]:
            return None
        state["done"] = True
        try:
            _prepare_capture_view()
            _tag_redraw()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
            bpy.ops.screen.screenshot(filepath=str(SCREENSHOT))
            if not SCREENSHOT.is_file():
                raise AssertionError("screenshot was not created")
            _assert_label_colors_visible(SCREENSHOT)
            _mark(f"done:{SCREENSHOT}")
        except Exception as exc:  # noqa: BLE001
            _mark(f"capture_failed: {exc}")
            os._exit(1)
        finally:
            if mod is not None:
                try:
                    mod.unregister()
                except Exception:  # noqa: BLE001
                    pass
            if temp_root is not None:
                shutil.rmtree(temp_root, ignore_errors=True)
        os._exit(0)
        return None

    bpy.app.timers.register(_capture, first_interval=2.5)


if __name__ == "__main__":
    main()
