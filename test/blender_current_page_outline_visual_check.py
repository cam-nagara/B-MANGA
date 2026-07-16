"""Blender UI実機: 現在ページ枠と選択枠を別色で検証する。"""

from __future__ import annotations

import importlib
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
MOD_NAME = "bmanga_dev_current_page_outline"
OUT_DIR = ROOT / "_verify" / "2026-07-17_current_page_outline"
WORK_DIR = OUT_DIR / "CurrentPageOutline.bmanga"
PAGE_SHOT = OUT_DIR / "page_current_and_selection.png"
COMA_ON_SHOT = OUT_DIR / "coma_current_overlay_on.png"
COMA_OFF_SHOT = OUT_DIR / "coma_current_overlay_off.png"
SUMMARY = OUT_DIR / "summary.json"


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


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


def _fail(stage: str, exc: BaseException) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "ok": False,
        "stage": stage,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BMANGA_CURRENT_PAGE_OUTLINE_FAILED", json.dumps(data, ensure_ascii=False), flush=True)
    os._exit(1)


def _view3d():
    for window in bpy.context.window_manager.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if region is not None and rv3d is not None:
                return window, screen, area, region, space, rv3d
    raise RuntimeError("3Dビューが見つかりません")


def _redraw(iterations: int = 5) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:  # noqa: BLE001
        pass


def _capture(path: Path) -> None:
    window, screen, _area, _region, _space, _rv3d = _view3d()
    path.parent.mkdir(parents=True, exist_ok=True)
    with bpy.context.temp_override(window=window, screen=screen):
        _redraw(6)
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(path),
            check_existing=False,
        )
    if "FINISHED" not in result or not path.is_file():
        raise AssertionError(f"スクリーンショット作成に失敗しました: {path} {result}")


def _page_rect_mm(work, page_index: int):
    page_grid = _sub("utils.page_grid")
    scene = bpy.context.scene
    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    width = page_grid.page_content_width_mm(
        work,
        page_index,
        float(work.paper.canvas_width_mm),
    )
    return (
        float(ox),
        float(oy),
        float(ox) + float(width),
        float(oy) + float(work.paper.canvas_height_mm),
    )


def _world_to_region(region, rv3d, x_mm: float, y_mm: float):
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    geom = _sub("utils.geom")
    return location_3d_to_region_2d(
        region,
        rv3d,
        (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0),
    )


def _prepare_page_view(work, indices: tuple[int, ...]) -> None:
    window, screen, area, region, space, rv3d = _view3d()
    rects = [_page_rect_mm(work, index) for index in indices]
    x0 = min(rect[0] for rect in rects)
    y0 = min(rect[1] for rect in rects)
    x1 = max(rect[2] for rect in rects)
    y1 = max(rect[3] for rect in rects)
    geom = _sub("utils.geom")
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        space.show_region_toolbar = False
        space.show_region_ui = False
        space.show_gizmo = False
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
        space.shading.type = "SOLID"
        space.shading.light = "FLAT"
        space.shading.background_type = "VIEWPORT"
        space.shading.background_color = (0.18, 0.18, 0.18)
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        rv3d.view_location = Vector(
            (geom.mm_to_m((x0 + x1) * 0.5), geom.mm_to_m((y0 + y1) * 0.5), 0.0)
        )
        for distance in (0.35, 0.50, 0.70, 0.95, 1.30, 1.80, 2.50, 3.50):
            rv3d.view_distance = distance
            _redraw(2)
            points = (
                _world_to_region(region, rv3d, x0, y0),
                _world_to_region(region, rv3d, x1, y1),
            )
            if any(point is None for point in points):
                continue
            if all(
                45.0 <= point.x <= float(region.width) - 45.0
                and 45.0 <= point.y <= float(region.height) - 45.0
                for point in points
            ):
                break


def _world_rect_roi(path: Path, rect_mm, margin_px: int = 22):
    from PIL import Image

    _window, _screen, _area, region, _space, rv3d = _view3d()
    points = [
        _world_to_region(region, rv3d, rect_mm[0], rect_mm[1]),
        _world_to_region(region, rv3d, rect_mm[2], rect_mm[3]),
    ]
    if any(point is None for point in points):
        raise AssertionError("ページ矩形を画面座標へ変換できません")
    with Image.open(path) as opened:
        height = opened.height
    xs = [float(region.x) + point.x for point in points]
    ys = [height - (float(region.y) + point.y) for point in points]
    return (
        int(min(xs)) - margin_px,
        int(min(ys)) - margin_px,
        int(max(xs)) + margin_px,
        int(max(ys)) + margin_px,
    )


def _pixel_rect_roi(path: Path, rect_px, margin_px: int = 12):
    from PIL import Image

    _window, _screen, _area, region, _space, _rv3d = _view3d()
    with Image.open(path) as opened:
        height = opened.height
    x0, y0, x1, y1 = (float(value) for value in rect_px)
    return (
        int(float(region.x) + min(x0, x1)) - margin_px,
        int(height - (float(region.y) + max(y0, y1))) - margin_px,
        int(float(region.x) + max(x0, x1)) + margin_px,
        int(height - (float(region.y) + min(y0, y1))) + margin_px,
    )


def _color_counts(path: Path, roi) -> dict[str, int]:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        x0 = max(0, min(image.width, int(roi[0])))
        y0 = max(0, min(image.height, int(roi[1])))
        x1 = max(x0, min(image.width, int(roi[2])))
        y1 = max(y0, min(image.height, int(roi[3])))
        pixels = image.crop((x0, y0, x1, y1)).getdata()
        orange = sum(1 for r, g, b in pixels if r > 185 and 55 < g < 245 and b < 105)
        magenta = sum(1 for r, g, b in pixels if r > 180 and g < 110 and b > 125)
    return {"orange": orange, "magenta": magenta}


def _configure_work(work) -> None:
    work.safe_area_overlay.enabled = False
    work.safe_area_overlay.bleed_outer_enabled = False
    work.paper.paper_color = (0.8, 0.8, 0.8, 1.0)
    for page_index, page in enumerate(work.pages):
        page.title = f"current_outline_{page_index + 1}"
        for coma in page.comas:
            shade = 0.62 + page_index * 0.08
            coma.background_color = (shade, shade, shade, 1.0)
            coma.border.color = (0.08, 0.08, 0.08, 1.0)


def _start() -> None:
    try:
        if bpy.app.background:
            raise RuntimeError("この視覚テストは --background なしで実行してください")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _load_addon()
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        result = bpy.ops.bmanga.work_new(filepath=str(WORK_DIR))
        if result != {"FINISHED"}:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        if bpy.ops.bmanga.page_add() != {"FINISHED"}:
            raise AssertionError("2ページ目を作成できません")
        work = bpy.context.scene.bmanga_work
        _configure_work(work)
        if bpy.ops.bmanga.work_save() != {"FINISHED"}:
            raise AssertionError("作品を保存できません")
        if bpy.ops.bmanga.open_page_file(index=0) != {"FINISHED"}:
            raise AssertionError("ページファイルを開けません")
        bpy.app.timers.register(_after_page_open, first_interval=1.5)
    except Exception as exc:  # noqa: BLE001
        _fail("start", exc)


def _after_page_open() -> None:
    try:
        scene = bpy.context.scene
        work = scene.bmanga_work
        page_file_scene = _sub("utils.page_file_scene")
        current_overlay = _sub("ui.overlay_current_page")
        role, page_id, _coma_id = page_file_scene.current_role(bpy.context)
        if role != page_file_scene.ROLE_PAGE or page_id != "p0001":
            raise AssertionError(f"ページファイル役割が不正です: {(role, page_id)}")

        # active_page_indexを意図的に別ページへ動かしても、正本はファイルパス。
        work.active_page_index = 1
        resolved = current_overlay._current_page_id_for_role(
            bpy.context,
            page_file_scene.ROLE_PAGE,
        )
        if resolved != "p0001":
            raise AssertionError(f"active_page_indexを現在ページと誤認しました: {resolved}")
        work.active_page_index = 0

        scene.bmanga_overlay_enabled = True
        scene.bmanga_page_preview_enabled = True
        scene.bmanga_page_preview_range_mode = "ALL"
        scene.bmanga_page_preview_resolution_percentage = 50.0
        scene.bmanga_page_preview_opacity = 100.0
        scene.bmanga_active_layer_kind = "text"
        page_preview_object = _sub("utils.page_preview_object")
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        page_preview_object.highlight_preview_page(scene, work, 1)
        _prepare_page_view(work, (0, 1))
        _capture(PAGE_SHOT)

        current_counts = _color_counts(PAGE_SHOT, _world_rect_roi(PAGE_SHOT, _page_rect_mm(work, 0)))
        selected_counts = _color_counts(PAGE_SHOT, _world_rect_roi(PAGE_SHOT, _page_rect_mm(work, 1)))
        if current_counts["orange"] < 20:
            raise AssertionError(f"現在ページのオレンジ枠がありません: {current_counts}")
        if selected_counts["magenta"] < 20:
            raise AssertionError(f"別ページの選択マゼンタ枠がありません: {selected_counts}")
        if selected_counts["orange"] > max(8, current_counts["orange"] // 8):
            raise AssertionError(
                f"選択ページを現在ページ色で描いています: current={current_counts} selected={selected_counts}"
            )

        page_preview_object.highlight_preview_page(scene, work, None)
        work.active_page_index = 0
        work.pages[0].active_coma_index = 0
        result = bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
        if result != {"FINISHED"}:
            raise AssertionError(f"コマファイルを開けません: {result}")
        bpy.app.timers.register(
            lambda: _after_coma_open(current_counts, selected_counts),
            first_interval=2.0,
        )
    except Exception as exc:  # noqa: BLE001
        _fail("page", exc)
    return None


def _after_coma_open(current_counts, selected_counts) -> None:
    try:
        scene = bpy.context.scene
        work = scene.bmanga_work
        page_file_scene = _sub("utils.page_file_scene")
        role, page_id, coma_id = page_file_scene.current_role(bpy.context)
        if role != page_file_scene.ROLE_COMA or page_id != "p0001" or not coma_id:
            raise AssertionError(f"コマファイル役割が不正です: {(role, page_id, coma_id)}")

        scene.bmanga_overlay_enabled = True
        scene.bmanga_page_preview_enabled = True
        scene.bmanga_page_preview_range_mode = "ALL"
        settings = scene.bmanga_coma_camera_settings
        settings.own_page_visible = True
        settings.koma_visible = True
        settings.name_visible = True
        settings.own_page_opacity = 100.0
        settings.koma_bg_images_opacity = 100.0
        settings.name_bg_images_opacity = 100.0
        coma_camera = _sub("utils.coma_camera")
        coma_camera.refresh_coma_page_overview(bpy.context)
        coma_camera.view_camera_in_viewports(bpy.context)

        window, screen, area, region, space, rv3d = _view3d()
        with bpy.context.temp_override(
            window=window,
            screen=screen,
            area=area,
            region=region,
            space_data=space,
            region_data=rv3d,
        ):
            space.show_region_toolbar = False
            space.show_region_ui = False
            space.show_gizmo = False
            space.overlay.show_floor = False
            space.overlay.show_axis_x = False
            space.overlay.show_axis_y = False
            rv3d.view_perspective = "CAMERA"
            rv3d.view_camera_zoom = -38.0
            rv3d.view_camera_offset = (0.0, 0.0)
        _capture(COMA_ON_SHOT)

        current_overlay = _sub("ui.overlay_current_page")
        coma_rect = current_overlay._current_coma_background_rect(
            scene,
            region,
            rv3d,
            page_id,
        )
        if coma_rect is None:
            raise AssertionError("コマファイルの現在ページ下絵矩形を解決できません")
        coma_roi = _pixel_rect_roi(COMA_ON_SHOT, coma_rect)
        coma_on_counts = _color_counts(COMA_ON_SHOT, coma_roi)
        if coma_on_counts["orange"] < 40:
            raise AssertionError(f"コマファイルのオレンジ枠がありません: {coma_on_counts}")

        scene.bmanga_overlay_enabled = False
        _capture(COMA_OFF_SHOT)
        coma_off_counts = _color_counts(COMA_OFF_SHOT, _pixel_rect_roi(COMA_OFF_SHOT, coma_rect))
        if coma_off_counts["orange"] > max(5, coma_on_counts["orange"] // 20):
            raise AssertionError(
                f"オーバーレイOFFでも現在ページ枠が残っています: on={coma_on_counts} off={coma_off_counts}"
            )

        data = {
            "ok": True,
            "page_file": {
                "current_page_id": "p0001",
                "current_counts": current_counts,
                "selected_counts": selected_counts,
                "screenshot": str(PAGE_SHOT),
            },
            "coma_file": {
                "current_page_id": page_id,
                "coma_id": coma_id,
                "overlay_on_counts": coma_on_counts,
                "overlay_off_counts": coma_off_counts,
                "on_screenshot": str(COMA_ON_SHOT),
                "off_screenshot": str(COMA_OFF_SHOT),
            },
        }
        SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BMANGA_CURRENT_PAGE_OUTLINE_OK", json.dumps(data, ensure_ascii=False), flush=True)
        os._exit(0)
    except Exception as exc:  # noqa: BLE001
        _fail("coma", exc)
    return None


bpy.app.timers.register(_start, first_interval=0.5)
