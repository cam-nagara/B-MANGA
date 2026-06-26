"""Blender UI check: clicking a page preview keeps its page image visible."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_page_preview_click_visual"
OUT_DIR = ROOT / ".codex" / "visual" / "page_preview_click_visual"
WORK_DIR = OUT_DIR / "PagePreviewClick.bmanga"
SCREENSHOT = OUT_DIR / "page_preview_click_screen.png"
SUMMARY = OUT_DIR / "page_preview_click_summary.json"


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


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _view3d_context():
    window = next(iter(bpy.context.window_manager.windows), None)
    screen = getattr(window, "screen", None)
    if window is None or screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        space = area.spaces.active
        rv3d = getattr(space, "region_3d", None)
        if region is not None and rv3d is not None:
            return window, screen, area, region, space, rv3d
    return None


def _redraw(iterations: int = 4) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:  # noqa: BLE001
        pass


def _page_rect(work, page_index: int) -> tuple[float, float, float, float]:
    page_grid = _sub("utils.page_grid")
    scene = bpy.context.scene
    cw = float(work.paper.canvas_width_mm)
    ch = float(work.paper.canvas_height_mm)
    gap_x, gap_y = page_grid.resolve_gap_mm(scene)
    ox, oy = page_grid.page_grid_offset_mm(
        page_index,
        max(1, int(getattr(scene, "bmanga_overview_cols", 4) or 4)),
        gap_x,
        cw,
        ch,
        getattr(work.paper, "start_side", "right"),
        getattr(work.paper, "read_direction", "left"),
        work=work,
        gap_y_mm=gap_y,
    )
    page = work.pages[page_index]
    add_x, add_y = page_grid.page_manual_offset_mm(page)
    width = page_grid.page_content_width_mm(work, page_index, cw)
    return ox + add_x, oy + add_y, ox + add_x + width, oy + add_y + ch


def _world_point_for_mm(region, rv3d, x_mm: float, y_mm: float):
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    geom = _sub("utils.geom")
    return location_3d_to_region_2d(
        region,
        rv3d,
        (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0),
    )


def _fit_view_to_pages(work, indices: list[int]) -> None:
    view = _view3d_context()
    if view is None:
        raise RuntimeError("VIEW_3D が見つかりません")
    window, screen, area, region, space, rv3d = view
    geom = _sub("utils.geom")
    rects = [_page_rect(work, index) for index in indices]
    x0 = min(rect[0] for rect in rects)
    y0 = min(rect[1] for rect in rects)
    x1 = max(rect[2] for rect in rects)
    y1 = max(rect[3] for rect in rects)
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        rv3d.view_location = Vector((geom.mm_to_m(cx), geom.mm_to_m(cy), 0.0))
        try:
            space.overlay.show_floor = False
            space.overlay.show_axis_x = False
            space.overlay.show_axis_y = False
            space.shading.type = "SOLID"
            space.shading.light = "FLAT"
            space.shading.background_type = "VIEWPORT"
            space.shading.background_color = (0.25, 0.25, 0.25)
        except Exception:  # noqa: BLE001
            pass
        for distance in (0.6, 0.9, 1.3, 1.8, 2.6, 3.8, 5.5):
            rv3d.view_distance = distance
            _redraw(2)
            points = [
                _world_point_for_mm(region, rv3d, x_mm, y_mm)
                for x_mm, y_mm in ((x0, y0), (x1, y1))
            ]
            if any(point is None for point in points):
                continue
            margin = 60
            if all(
                margin <= point.x <= region.width - margin
                and margin <= point.y <= region.height - margin
                for point in points
            ):
                return


def _press_event_for_page(work, page_index: int):
    view = _view3d_context()
    if view is None:
        raise RuntimeError("VIEW_3D が見つかりません")
    _window, _screen, _area, region, _space, rv3d = view
    x0, y0, x1, y1 = _page_rect(work, page_index)
    point = _world_point_for_mm(region, rv3d, (x0 + x1) * 0.5, (y0 + y1) * 0.5)
    if point is None:
        raise AssertionError("クリック対象ページの中心が画面座標へ変換できません")
    if not (0 <= point.x <= region.width and 0 <= point.y <= region.height):
        raise AssertionError(f"クリック対象ページが画面外です: ({point.x:.1f}, {point.y:.1f})")
    return SimpleNamespace(
        type="LEFTMOUSE",
        value="PRESS",
        mouse_x=int(region.x + point.x),
        mouse_y=int(region.y + point.y),
        mouse_region_x=int(point.x),
        mouse_region_y=int(point.y),
        ctrl=False,
        shift=False,
        alt=False,
        oskey=False,
    )


def _screenshot() -> tuple[int, int]:
    view = _view3d_context()
    if view is None:
        raise RuntimeError("VIEW_3D が見つかりません")
    window, screen, _area, _region, _space, _rv3d = view
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with bpy.context.temp_override(window=window, screen=screen):
        _redraw(8)
        result = bpy.ops.screen.screenshot(filepath=str(SCREENSHOT), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    from PIL import Image

    with Image.open(SCREENSHOT) as opened:
        return opened.width, opened.height


def _sample_target_center(work, page_index: int, image_height: int) -> tuple[float, float, float]:
    from PIL import Image

    view = _view3d_context()
    if view is None:
        raise RuntimeError("VIEW_3D が見つかりません")
    _window, _screen, _area, region, _space, rv3d = view
    x0, y0, x1, y1 = _page_rect(work, page_index)
    point = _world_point_for_mm(region, rv3d, (x0 + x1) * 0.5, (y0 + y1) * 0.5)
    if point is None:
        raise AssertionError("サンプル対象ページの中心が画面座標へ変換できません")
    px = int(round(region.x + point.x))
    py = int(round(image_height - (region.y + point.y)))
    with Image.open(SCREENSHOT) as opened:
        image = opened.convert("RGB")
        pixels = []
        for sy in range(max(0, py - 8), min(image.height, py + 9)):
            for sx in range(max(0, px - 8), min(image.width, px + 9)):
                pixels.append(image.getpixel((sx, sy)))
    if not pixels:
        raise AssertionError(f"サンプル位置がスクリーンショット範囲外です: ({px}, {py})")
    return tuple(sum(pixel[i] for pixel in pixels) / len(pixels) for i in range(3))


def _configure_pages(work) -> None:
    for i, page in enumerate(work.pages):
        page.title = f"click_preview_{i + 1:02d}"
        if len(page.comas) == 0:
            continue
        coma = page.comas[0]
        coma.rect_x_mm = 26.0
        coma.rect_y_mm = 42.0
        coma.rect_width_mm = 128.0
        coma.rect_height_mm = 176.0


def _run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    _load_addon()
    bpy.ops.bmanga.work_new(filepath=str(WORK_DIR))
    for _ in range(7):
        bpy.ops.bmanga.page_add()
    work = bpy.context.scene.bmanga_work
    _configure_pages(work)
    bpy.ops.bmanga.work_save()
    bpy.ops.bmanga.open_page_file(index=4)
    bpy.app.timers.register(_after_page_open, first_interval=1.0)


def _after_page_open() -> None:
    scene = bpy.context.scene
    work = scene.bmanga_work
    current_index = 4
    target_index = 5
    scene.bmanga_overview_mode = True
    scene.bmanga_page_preview_enabled = True
    scene.bmanga_page_preview_range_mode = "ALL"
    scene.bmanga_page_preview_resolution_percentage = 100.0
    scene.bmanga_page_preview_opacity = 100.0

    page_preview_object = _sub("utils.page_preview_object")
    page_op = _sub("operators.page_op")

    updated = page_preview_object.sync_page_previews(bpy.context, work, force=True)
    _fit_view_to_pages(work, [current_index, target_index])
    page_op._clear_page_open_click_state()
    event = _press_event_for_page(work, target_index)
    view = _view3d_context()
    window, screen, area, region, space, rv3d = view
    fake_op = SimpleNamespace(report=lambda *_a, **_k: None)
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        result = page_op.BMANGA_OT_page_pick_viewport.invoke(fake_op, bpy.context, event)
    if result != {"FINISHED"}:
        raise AssertionError(f"ページプレビュークリックが完了しません: {result}")
    expected_page_id = str(getattr(work.pages[target_index], "id", "") or "")
    highlighted = page_preview_object.highlighted_page_id()
    if highlighted != expected_page_id:
        raise AssertionError(f"クリックしたページがハイライトされていません: {highlighted} != {expected_page_id}")
    width, height = _screenshot()
    center_rgb = _sample_target_center(work, target_index, height)
    if not all(channel >= 180.0 for channel in center_rgb):
        raise AssertionError(
            f"クリック選択後にページ画像が消えています: center={center_rgb} screenshot={SCREENSHOT}"
        )

    data = {
        "screenshot": str(SCREENSHOT),
        "screenshot_size": [width, height],
        "current_index": current_index,
        "target_index": target_index,
        "target_page_id": expected_page_id,
        "highlighted_page_id": highlighted,
        "preview_count": updated,
        "target_center_rgb": [round(v, 2) for v in center_rgb],
    }
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BMANGA_PAGE_PREVIEW_CLICK_VISUAL_OK", json.dumps(data, ensure_ascii=False), flush=True)
    os._exit(0)


def _timer():
    _run()
    return None


bpy.app.timers.register(_timer, first_interval=0.5)
