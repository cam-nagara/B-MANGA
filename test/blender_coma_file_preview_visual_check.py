"""UI visual check for page previews in a coma blend file.

This script creates a B-MANGA work, enters a coma blend file, enables the
nearby-page preview, captures a Blender UI screenshot, and writes a JSON
summary. It must run without ``--background``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".codex" / "visual" / "coma_file_preview_visual_check"
WORK_DIR = OUT_DIR / "ComaPreviewVisual.bmanga"
SCREENSHOT = OUT_DIR / "coma_file_preview_screen.png"
SUMMARY = OUT_DIR / "coma_file_preview_summary.json"
STAGE = OUT_DIR / "coma_file_preview_stage.txt"


def _mark(stage: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGE.write_text(stage, encoding="utf-8")


def _fail(stage: str, exc: BaseException) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "ok": False,
        "stage": stage,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
        "screenshot": str(SCREENSHOT),
        "screenshot_exists": SCREENSHOT.is_file(),
    }
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BMANGA_COMA_FILE_PREVIEW_VISUAL_ERROR", json.dumps(data, ensure_ascii=False), flush=True)
    os._exit(1)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_file_preview_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_file_preview_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_coma_variation(work) -> None:
    colors = [
        (1.0, 1.0, 1.0, 1.0),
        (0.62, 0.90, 1.0, 1.0),
        (1.0, 0.78, 0.50, 1.0),
        (0.74, 1.0, 0.64, 1.0),
        (1.0, 0.58, 0.88, 1.0),
        (0.62, 0.58, 1.0, 1.0),
        (1.0, 0.93, 0.42, 1.0),
        (0.54, 1.0, 0.88, 1.0),
    ]
    for i, page in enumerate(work.pages):
        page.title = f"preview_page_{i + 1:02d}"
        if len(page.comas) == 0:
            continue
        coma = page.comas[0]
        coma.background_color = colors[i % len(colors)]
        coma.rect_x_mm = 16.0 + (i % 3) * 5.0
        coma.rect_y_mm = 22.0 + (i % 2) * 8.0
        coma.rect_width_mm = 126.0 - (i % 4) * 9.0
        coma.rect_height_mm = 172.0 - (i % 3) * 13.0
        border = getattr(coma, "border", None)
        if border is not None:
            border.width_mm = 0.8 + (i % 3) * 0.25


def _visible_preview_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get("bmanga_kind", "") or "") == "page_preview"
        and not bool(getattr(obj, "hide_viewport", False))
    ]


def _first_window_screen():
    window = next(iter(bpy.context.window_manager.windows), None)
    screen = getattr(window, "screen", None)
    return window, screen


def _view3d_area():
    window, screen = _first_window_screen()
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


def _fit_visible_previews() -> None:
    visible = _visible_preview_objects()
    window, screen, area, region, space = _view3d_area()
    if not visible or window is None or screen is None or area is None or region is None:
        return
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return
    points: list[Vector] = []
    for obj in visible:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return
    min_x = min(p.x for p in points)
    max_x = max(p.x for p in points)
    min_y = min(p.y for p in points)
    max_y = max(p.y for p in points)
    center = Vector(((min_x + max_x) * 0.5, (min_y + max_y) * 0.5, 0.0))
    span = max(max_x - min_x, max_y - min_y, 0.1)
    try:
        space.shading.type = "MATERIAL"
        space.overlay.show_overlays = False
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
    except Exception:  # noqa: BLE001
        pass
    rv3d.view_perspective = "ORTHO"
    rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
    rv3d.view_location = center
    rv3d.view_distance = span * 2.0


def _capture_screenshot() -> None:
    scene = bpy.context.scene
    window, screen, area, region, space = _view3d_area()
    if window is None or screen is None or area is None or region is None:
        return
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return
    scene.render.filepath = str(SCREENSHOT)
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        bpy.ops.render.opengl(view_context=True, write_still=True)


def _run() -> None:
    _mark("start")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    _mark("load_addon")
    _load_addon()
    _mark("work_new")
    bpy.ops.bmanga.work_new(filepath=str(WORK_DIR))
    for _ in range(7):
        bpy.ops.bmanga.page_add()
    work = bpy.context.scene.bmanga_work
    _set_coma_variation(work)
    bpy.ops.bmanga.work_save()
    work.active_page_index = 3
    work.pages[3].active_coma_index = 0
    _mark("enter_coma_mode")
    result = bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
    if result != {"FINISHED"}:
        raise AssertionError(f"コマ用blendファイルを開けません: {result}")
    _mark("after_enter_coma_mode")
    bpy.app.timers.register(_after_timer, first_interval=1.0)


def _after_coma_open() -> None:
    _mark("after_coma_open")
    scene = bpy.context.scene
    scene.bmanga_page_preview_enabled = True
    scene.bmanga_page_preview_page_radius = 3
    scene.bmanga_page_preview_resolution_percentage = 40.0
    scene.bmanga_overview_cols = 4
    if hasattr(scene, "bmanga_overview_gap_x_mm"):
        scene.bmanga_overview_gap_x_mm = 12.0
    if hasattr(scene, "bmanga_overview_gap_y_mm"):
        scene.bmanga_overview_gap_y_mm = 14.0
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    if settings is not None:
        settings.name_bg_images_opacity = 92.0
        settings.bg_images_scale = 1.0
    from bmanga_dev_coma_file_preview_visual.utils import page_file_scene
    from bmanga_dev_coma_file_preview_visual.utils import page_preview_object

    _mark("sync_page_previews")
    page_preview_object.sync_page_previews(bpy.context, scene.bmanga_work, force=True)
    _mark("fit_visible_previews")
    _fit_visible_previews()
    _mark("capture_screenshot")
    _capture_screenshot()
    _mark("collect_summary")
    visible = _visible_preview_objects()
    role, page_id, coma_id = page_file_scene.current_role(bpy.context)
    data = {
        "work_blend": str(WORK_DIR / "work.blend"),
        "coma_blend": str(WORK_DIR / page_id / coma_id / f"{coma_id}.blend") if page_id and coma_id else "",
        "screenshot": str(SCREENSHOT),
        "role": role,
        "current_page_id": str(getattr(scene, "bmanga_current_coma_page_id", "")),
        "current_coma_id": str(getattr(scene, "bmanga_current_coma_id", "")),
        "preview_radius": int(getattr(scene, "bmanga_page_preview_page_radius", -1)),
        "preview_resolution_percentage": float(
            getattr(scene, "bmanga_page_preview_resolution_percentage", 0.0)
        ),
        "overview_cols": int(getattr(scene, "bmanga_overview_cols", -1)),
        "visible_preview_count": len(visible),
        "visible_preview_ids": [str(obj.get("bmanga_id", "") or obj.name) for obj in visible],
        "preview_locations": [
            [
                str(obj.get("bmanga_id", "") or obj.name),
                round(float(obj.location.x), 4),
                round(float(obj.location.y), 4),
                round(float(obj.location.z), 4),
            ]
            for obj in visible
        ],
        "screenshot_exists": SCREENSHOT.is_file(),
    }
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if role != "coma":
        raise AssertionError(f"コマ用blendファイルではありません: {role}")
    if len(visible) == 0:
        raise AssertionError("コマ用blendファイルでページ一覧プレビューが表示されていません")
    if not SCREENSHOT.is_file():
        raise AssertionError(f"スクリーンショットを保存できません: {SCREENSHOT}")
    _mark("done")
    print("BMANGA_COMA_FILE_PREVIEW_VISUAL_OK", json.dumps(data, ensure_ascii=False), flush=True)
    os._exit(0)


def _after_timer():
    try:
        _after_coma_open()
    except BaseException as exc:  # noqa: BLE001
        stage = STAGE.read_text(encoding="utf-8") if STAGE.is_file() else "after_timer"
        _fail(stage, exc)
    return None


def _timer_wrapped():
    try:
        _run()
    except BaseException as exc:  # noqa: BLE001
        _fail("timer", exc)
    return None


bpy.app.timers.register(_timer_wrapped, first_interval=0.5)
