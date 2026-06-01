"""UI visual check for page edit previews.

This script creates a real B-Name work, opens a page edit file, enables
nearby-page previews, saves a Blender UI screenshot, and writes a small JSON
summary. It must run without ``--background``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".codex" / "visual" / "page_file_preview_visual_check"
WORK_DIR = OUT_DIR / "PagePreviewVisual.bname"
SCREENSHOT = OUT_DIR / "page_preview_screen.png"
SUMMARY = OUT_DIR / "page_preview_summary.json"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_page_preview_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_page_preview_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_coma_variation(work) -> None:
    colors = [
        (1.0, 1.0, 1.0, 1.0),
        (0.75, 0.95, 1.0, 1.0),
        (1.0, 0.88, 0.72, 1.0),
        (0.88, 1.0, 0.78, 1.0),
        (1.0, 0.78, 0.95, 1.0),
        (0.84, 0.82, 1.0, 1.0),
        (1.0, 0.96, 0.68, 1.0),
        (0.80, 1.0, 0.94, 1.0),
    ]
    for i, page in enumerate(work.pages):
        page.title = f"preview_page_{i + 1:02d}"
        if len(page.comas) == 0:
            continue
        coma = page.comas[0]
        coma.background_color = colors[i % len(colors)]
        coma.rect_x_mm = 20.0 + (i % 3) * 4.0
        coma.rect_y_mm = 28.0 + (i % 2) * 8.0
        coma.rect_width_mm = 120.0 - (i % 4) * 8.0
        coma.rect_height_mm = 170.0 - (i % 3) * 12.0
        border = getattr(coma, "border", None)
        if border is not None:
            border.width_mm = 0.8 + (i % 3) * 0.25


def _visible_preview_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get("bname_kind", "") or "") == "page_preview"
        and not bool(getattr(obj, "hide_viewport", False))
    ]


def _first_window_screen():
    window = next(iter(bpy.context.window_manager.windows), None)
    screen = getattr(window, "screen", None)
    return window, screen


def _fit_current_page_view() -> None:
    window, screen = _first_window_screen()
    if window is None or screen is None:
        return
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        space = area.spaces.active
        rv3d = getattr(space, "region_3d", None)
        if region is None or rv3d is None:
            continue
        try:
            space.shading.type = "RENDERED"
        except Exception:  # noqa: BLE001
            pass
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
            space_data=space,
            region_data=rv3d,
        ):
            bpy.ops.bname.view_fit_page()
        break


def _run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    _load_addon()
    bpy.ops.bname.work_new(filepath=str(WORK_DIR))
    for _ in range(7):
        bpy.ops.bname.page_add()
    work = bpy.context.scene.bname_work
    _set_coma_variation(work)
    bpy.ops.bname.work_save()
    bpy.ops.bname.open_page_file(index=3)
    bpy.app.timers.register(_after_page_open, first_interval=1.0)


def _after_page_open() -> None:
    scene = bpy.context.scene
    scene.bname_page_preview_enabled = True
    scene.bname_page_preview_page_radius = 2
    scene.bname_page_preview_resolution_percentage = 50.0
    from bname_dev_page_preview_visual.utils import page_preview_object

    page_preview_object.sync_page_previews(bpy.context, scene.bname_work)
    _fit_current_page_view()
    window, screen = _first_window_screen()
    if window is not None and screen is not None:
        with bpy.context.temp_override(window=window, screen=screen):
            if bpy.ops.wm.redraw_timer.poll():
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
            bpy.ops.screen.screenshot(filepath=str(SCREENSHOT))
    else:
        bpy.ops.screen.screenshot(filepath=str(SCREENSHOT))
    visible = _visible_preview_objects()
    data = {
        "work_blend": str(WORK_DIR / "work.blend"),
        "page_blend": str(WORK_DIR / "p0004" / "page.blend"),
        "screenshot": str(SCREENSHOT),
        "current_page_id": str(getattr(scene, "bname_current_page_id", "")),
        "preview_radius": int(getattr(scene, "bname_page_preview_page_radius", -1)),
        "preview_resolution_percentage": float(
            getattr(scene, "bname_page_preview_resolution_percentage", 0.0)
        ),
        "visible_preview_count": len(visible),
        "visible_preview_ids": [str(obj.get("bname_id", "") or obj.name) for obj in visible],
        "view_shading": [
            str(area.spaces.active.shading.type)
            for _window in bpy.context.window_manager.windows
            for area in _window.screen.areas
            if area.type == "VIEW_3D"
        ],
        "sidebar_open": [
            bool(getattr(area.spaces.active, "show_region_ui", False))
            for _window in bpy.context.window_manager.windows
            for area in _window.screen.areas
            if area.type == "VIEW_3D"
        ],
        "page_collections": [
            coll.name
            for coll in bpy.data.collections
            if str(coll.get("bname_kind", "") or "") == "page"
        ],
    }
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BNAME_PAGE_FILE_PREVIEW_VISUAL_OK", json.dumps(data, ensure_ascii=False), flush=True)
    os._exit(0)


def _timer():
    _run()
    return None


bpy.app.timers.register(_timer, first_interval=0.5)
