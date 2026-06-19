"""Blender UI visual check for work-info text placement."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".codex" / "visual" / "work_info_position_check"
WORK_DIR = OUT_DIR / "WorkInfoPosition.bmanga"
SCREENSHOT = OUT_DIR / "work_info_position_overview_ui.png"
SUMMARY = OUT_DIR / "work_info_position_summary.json"
MODULE_NAME = "bmanga_dev_work_info_position_visual"


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


def _view3d_override():
    window = next(iter(bpy.context.window_manager.windows), None)
    screen = getattr(window, "screen", None) if window is not None else None
    if window is None or screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        space = area.spaces.active
        rv3d = getattr(space, "region_3d", None)
        if region is None or rv3d is None:
            continue
        try:
            space.shading.type = "MATERIAL"
            space.show_region_ui = True
        except Exception:  # noqa: BLE001
            pass
        return {
            "window": window,
            "screen": screen,
            "area": area,
            "region": region,
            "space_data": space,
            "region_data": rv3d,
        }
    return {"window": window, "screen": screen}


def _redraw(iterations: int = 8) -> None:
    override = _view3d_override()
    if override is None:
        return
    with bpy.context.temp_override(**override):
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)


def _screenshot() -> str:
    override = _view3d_override()
    if override is not None:
        with bpy.context.temp_override(**override):
            _redraw(10)
            result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(SCREENSHOT), check_existing=False)
    else:
        result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(SCREENSHOT), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    return str(SCREENSHOT)


def _work_name_object(work_info_text_object):
    for obj in bpy.data.objects:
        if obj.get(work_info_text_object.PROP_WORK_INFO_KIND) != "work_info_text":
            continue
        if str(obj.get(work_info_text_object.PROP_WORK_INFO_OWNER_ID, "") or "").endswith(":work_name"):
            return obj
    return None


def _run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    mod = _load_addon()
    try:
        result = bpy.ops.bmanga.work_new(filepath=str(WORK_DIR))
        if "FINISHED" not in result:
            raise RuntimeError(f"work_new failed: {result}")
        work = bpy.context.scene.bmanga_work
        work.work_info.work_name = "左下確認"
        work.work_info.display_work_name.enabled = True
        work.work_info.display_work_name.position = "bottom-left"
        from bmanga_dev_work_info_position_visual.ui import overlay_shared
        from bmanga_dev_work_info_position_visual.utils import page_grid, work_info_text_object
        from bmanga_dev_work_info_position_visual.utils.geom import m_to_mm

        page_grid.apply_page_collection_transforms(bpy.context, work)
        work_info_text_object.regenerate_all_work_info_texts(bpy.context.scene, work)
        try:
            bpy.ops.bmanga.view_fit_all("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            pass

        obj = _work_name_object(work_info_text_object)
        if obj is None:
            raise RuntimeError("work-name text object not found")
        rects = overlay_shared.compute_paper_rects(work.paper)
        ox, oy = page_grid.page_total_offset_mm(work, bpy.context.scene, 0)
        x_mm = m_to_mm(float(obj.location.x)) - ox
        y_mm = m_to_mm(float(obj.location.y)) - oy
        expected_x = rects.bleed.x
        expected_y = rects.bleed.y - 2.0
        summary = {
            "screenshot": _screenshot(),
            "position": str(work.work_info.display_work_name.position),
            "x_mm": round(x_mm, 4),
            "y_mm": round(y_mm, 4),
            "expected_x_mm": round(expected_x, 4),
            "expected_y_mm": round(expected_y, 4),
            "inside_inner_frame": bool(
                rects.inner_frame.x <= x_mm <= rects.inner_frame.x2
                and rects.inner_frame.y <= y_mm <= rects.inner_frame.y2
            ),
        }
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BMANGA_WORK_INFO_POSITION_VISUAL_CHECK", json.dumps(summary, ensure_ascii=False), flush=True)
        ok = (
            summary["position"] == "bottom-left"
            and abs(x_mm - expected_x) < 0.001
            and abs(y_mm - expected_y) < 0.001
            and not summary["inside_inner_frame"]
        )
        os._exit(0 if ok else 2)
    finally:
        try:
            mod.unregister()
        except Exception:  # noqa: BLE001
            pass


def _timer():
    _run()
    return None


bpy.app.timers.register(_timer, first_interval=0.5)
