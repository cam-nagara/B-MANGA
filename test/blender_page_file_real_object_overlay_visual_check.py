"""Blender UI実機用: ページファイル現在ページの実体表示と補助オーバーレイ分離を確認."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".codex" / "visual" / "page_file_real_object_overlay_visual_check"
WORK_DIR = OUT_DIR / "RealObjectOverlayVisual.bmanga"
SHOT_ON = OUT_DIR / "overlay_on.png"
SHOT_OFF = OUT_DIR / "overlay_off.png"
SUMMARY = OUT_DIR / "summary.json"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_page_real_overlay_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_page_real_overlay_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _first_view3d():
    for window in bpy.context.window_manager.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if region is not None and rv3d is not None:
                return window, screen, area, region, space, rv3d
    raise RuntimeError("VIEW_3D が見つかりません")


def _redraw(iterations: int = 4) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:
        pass


def _fit_page() -> None:
    window, screen, area, region, space, rv3d = _first_view3d()
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        try:
            space.shading.type = "RENDERED"
        except Exception:
            pass
        try:
            rv3d.view_perspective = "ORTHO"
            rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
            rv3d.view_location = Vector((0.13, 0.18, 0.0))
        except Exception:
            pass
        bpy.ops.bmanga.view_fit_page("EXEC_DEFAULT")
    _redraw(5)


def _screenshot(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _redraw(4)
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")


def _configure_page_scene() -> dict:
    from bmanga_dev_page_real_overlay_visual.core.work import get_work
    from bmanga_dev_page_real_overlay_visual.utils import page_file_scene, paper_bg_object, paper_guide_object

    context = bpy.context
    scene = context.scene
    work = get_work(context)
    if work is None or not work.loaded:
        raise AssertionError("作品データが読み込まれていません")
    page_id = page_file_scene.current_page_id(scene)
    if not page_id:
        raise AssertionError("ページファイルとして開かれていません")
    page = next((p for p in work.pages if str(getattr(p, "id", "") or "") == page_id), None)
    if page is None:
        raise AssertionError(f"現在ページが見つかりません: {page_id}")
    if len(page.comas) > 0:
        coma = page.comas[0]
        coma.border.visible = True
        coma.border.width_mm = 2.0
        coma.border.style = "solid"
        coma.white_margin.enabled = True
        coma.white_margin.width_mm = 1.2
        coma.background_color = (1.0, 1.0, 1.0, 1.0)
    work.safe_area_overlay.enabled = True
    work.safe_area_overlay.opacity = 30.0
    work.safe_area_overlay.bleed_outer_enabled = True
    work.safe_area_overlay.bleed_outer_opacity = 100.0
    scene.bmanga_overlay_enabled = True
    scene.bmanga_page_guides_visible = True
    scene.bmanga_page_work_info_visible = True
    page_file_scene.resync_page_runtime_objects(scene, work, page_id)
    paper_guide_object.regenerate_all_paper_guides(scene, page_file_scene.work_for_pages(work, {page_id}))
    paper_guide_object.apply_view_constant_thickness()
    bg_obj = bpy.data.objects.get(f"{paper_bg_object.PAPER_BG_NAME_PREFIX}{page_id}")
    guide_objs = [
        obj for obj in bpy.data.objects
        if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "") == page_id
    ]
    if bg_obj is None or bool(getattr(bg_obj, "hide_viewport", True)):
        raise AssertionError("用紙背景の実体が表示されていません")
    if not guide_objs or any(bool(getattr(obj, "hide_viewport", False)) for obj in guide_objs):
        raise AssertionError("用紙ガイド/塗りの実体が表示されていません")
    return {
        "page_id": page_id,
        "paper_bg": getattr(bg_obj, "name", ""),
        "guide_objects": [getattr(obj, "name", "") for obj in guide_objs],
    }


def _after_page_open() -> None:
    try:
        data = _configure_page_scene()
        _fit_page()
        bpy.context.scene.bmanga_overlay_enabled = True
        _screenshot(SHOT_ON)
        bpy.context.scene.bmanga_overlay_enabled = False
        _screenshot(SHOT_OFF)
        data.update(
            {
                "overlay_on": str(SHOT_ON),
                "overlay_off": str(SHOT_OFF),
            }
        )
        SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BMANGA_PAGE_FILE_REAL_OBJECT_OVERLAY_VISUAL_OK", json.dumps(data, ensure_ascii=False), flush=True)
        os._exit(0)
    except Exception as exc:  # noqa: BLE001
        SUMMARY.write_text(
            json.dumps({"error": repr(exc)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"BMANGA_PAGE_FILE_REAL_OBJECT_OVERLAY_VISUAL_FAILED {exc!r}", flush=True)
        os._exit(1)


def _run() -> None:
    if bpy.app.background:
        raise RuntimeError("このチェックは --background なしで実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    _load_addon()
    bpy.ops.bmanga.work_new(filepath=str(WORK_DIR))
    bpy.ops.bmanga.work_save()
    bpy.ops.bmanga.open_page_file(index=0)
    bpy.app.timers.register(_after_page_open, first_interval=1.0)


def _timer():
    _run()
    return None


bpy.app.timers.register(_timer, first_interval=0.5)
