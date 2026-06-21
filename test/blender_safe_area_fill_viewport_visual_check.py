"""Blender UI実機用: セーフライン外の色/不透明度が実際のビューに出るか確認."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_SAFE_AREA_VISUAL_OUT", "")
    or tempfile.mkdtemp(prefix="bmanga_safe_area_visual_")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_safe_area_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_safe_area_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d_context():
    screen = bpy.context.screen
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type == "WINDOW":
                return area, region, area.spaces.active.region_3d
    raise RuntimeError("VIEW_3D が見つかりません")


def _view3d_override():
    area, region, _rv3d = _view3d_context()
    return bpy.context.temp_override(area=area, region=region)


def _screen_point_for_mm(region, rv3d, x_mm: float, y_mm: float):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from bmanga_dev_safe_area_visual.utils.geom import mm_to_m

    return location_3d_to_region_2d(region, rv3d, (mm_to_m(x_mm), mm_to_m(y_mm), 0.0))


def _redraw(iterations: int = 5) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:
        pass


def _screenshot(name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    _redraw(6)
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    return path


def _sample_rgb(path: Path, x: int, y: int, radius: int = 5) -> tuple[float, float, float]:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        pixels = []
        for py in range(max(0, y - radius), min(height, y + radius + 1)):
            for px in range(max(0, x - radius), min(width, x + radius + 1)):
                pixels.append(image.getpixel((px, py)))
    if not pixels:
        raise AssertionError(f"sample point outside image: {path} ({x}, {y})")
    return tuple(sum(pixel[i] for pixel in pixels) / len(pixels) for i in range(3))


def _max_rgb_delta(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return max(abs(float(a[i]) - float(b[i])) for i in range(3))


def _apply_overlay_fills(work, *, safe_opacity: float, bleed_opacity: float) -> None:
    from bmanga_dev_safe_area_visual.utils import paper_guide_object

    work.safe_area_overlay.enabled = True
    work.safe_area_overlay.color = (1.0, 0.0, 0.85)
    work.safe_area_overlay.opacity = safe_opacity
    work.safe_area_overlay.bleed_outer_enabled = True
    work.safe_area_overlay.bleed_outer_color = (0.0, 0.9, 1.0)
    work.safe_area_overlay.bleed_outer_opacity = bleed_opacity
    paper_guide_object.regenerate_all_paper_guides(bpy.context.scene, work)
    _redraw(4)


def _run_visual_check() -> None:
    if bpy.app.background:
        raise RuntimeError("このチェックは --background なしで実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_safe_area_visual_work_"))
    mod = None
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    try:
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "SafeAreaVisual.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_safe_area_visual.core.work import get_work
        from bmanga_dev_safe_area_visual.ui import overlay
        from bmanga_dev_safe_area_visual.ui import overlay_shared
        from bmanga_dev_safe_area_visual.utils import page_grid, paper_guide_object

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        overlay.apply_bmanga_shading_mode(context)
        with _view3d_override():
            bpy.ops.view3d.view_axis(type="TOP", align_active=False)
            space = bpy.context.space_data
            rv3d = space.region_3d
            rv3d.view_perspective = "ORTHO"
            rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
            rv3d.view_location = Vector((0.105, 0.1485, 0.0))
            space.overlay.show_floor = False
            space.overlay.show_axis_x = False
            space.overlay.show_axis_y = False
            if getattr(space.shading, "type", "") != "SOLID":
                space.shading.type = "SOLID"
            space.shading.light = "FLAT"
            space.shading.color_type = "TEXTURE"
            try:
                space.shading.background_type = "VIEWPORT"
                space.shading.background_color = (1.0, 1.0, 1.0)
            except Exception:
                pass
            fit = bpy.ops.bmanga.view_fit_page("EXEC_DEFAULT")
            assert "FINISHED" in fit, fit

        rects = overlay_shared.compute_paper_rects(work.paper)
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, 0)
        safe_sample_x = ox + (rects.canvas.x + rects.canvas.x2) * 0.5
        safe_sample_y = oy + (rects.safe.y2 + rects.bleed.y2) * 0.5
        bleed_sample_x = ox + (rects.canvas.x + rects.bleed.x) * 0.5
        bleed_sample_y = oy + rects.bleed.y + rects.bleed.height * 0.37

        _apply_overlay_fills(work, safe_opacity=100.0, bleed_opacity=100.0)
        safe_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
        assert safe_obj is not None, "セーフライン外の塗り実体がありません"
        bleed_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page.id}")
        assert bleed_obj is not None, "裁ち落とし枠外の塗り実体がありません"
        assert getattr(safe_obj, "display_type", "") == "SOLID", "表示方法がソリッドではありません"
        assert not bool(getattr(safe_obj, "show_in_front", False)), "最前面ワイヤ表示に依存しています"
        mat = safe_obj.data.materials[0] if safe_obj.data.materials else None
        assert mat is not None and getattr(mat, "blend_method", "") == "BLEND", (
            "セーフライン外の塗り素材が透明表示になっていません"
        )
        assert any(node.bl_idname == "ShaderNodeBsdfTransparent" for node in mat.node_tree.nodes), (
            "セーフライン外の塗り素材に透明シェーダーがありません"
        )
        area, region, rv3d = _view3d_context()
        _ = area
        safe_point = _screen_point_for_mm(region, rv3d, safe_sample_x, safe_sample_y)
        bleed_point = _screen_point_for_mm(region, rv3d, bleed_sample_x, bleed_sample_y)
        if safe_point is None or bleed_point is None:
            raise AssertionError("サンプル地点が画面外です")
        full_path = _screenshot("safe_area_opacity_100.png")
        from PIL import Image

        with Image.open(full_path) as opened:
            image_h = opened.height
        safe_px = int(round(region.x + float(safe_point.x)))
        safe_py = int(round(image_h - (region.y + float(safe_point.y))))
        bleed_px = int(round(region.x + float(bleed_point.x)))
        bleed_py = int(round(image_h - (region.y + float(bleed_point.y))))
        full_rgb = _sample_rgb(full_path, safe_px, safe_py)
        bleed_full_rgb = _sample_rgb(full_path, bleed_px, bleed_py)
        if not (full_rgb[0] > 180.0 and full_rgb[1] < 100.0 and full_rgb[2] > 130.0):
            raise AssertionError(f"セーフライン外の色が画面に出ていません: RGB={full_rgb}")
        if not (bleed_full_rgb[0] < 100.0 and bleed_full_rgb[1] > 140.0 and bleed_full_rgb[2] > 140.0):
            raise AssertionError(f"裁ち落とし枠外の色が画面に出ていません: RGB={bleed_full_rgb}")

        _apply_overlay_fills(work, safe_opacity=25.0, bleed_opacity=100.0)
        quarter_path = _screenshot("safe_area_opacity_025.png")
        quarter_rgb = _sample_rgb(quarter_path, safe_px, safe_py)
        if _max_rgb_delta(quarter_rgb, full_rgb) <= 30.0:
            raise AssertionError(f"セーフライン外の不透明度が画面で薄くなっていません: 100={full_rgb} 25={quarter_rgb}")

        _apply_overlay_fills(work, safe_opacity=0.0, bleed_opacity=100.0)
        zero_path = _screenshot("safe_area_opacity_000.png")
        zero_rgb = _sample_rgb(zero_path, safe_px, safe_py)
        zero_neutral = max(zero_rgb) - min(zero_rgb)
        if not (zero_neutral <= 35.0 and sum(zero_rgb) / 3.0 > 120.0):
            raise AssertionError(f"不透明度0でセーフライン外の塗りが残っています: RGB={zero_rgb}")

        _apply_overlay_fills(work, safe_opacity=0.0, bleed_opacity=25.0)
        bleed_quarter_path = _screenshot("bleed_outer_opacity_025.png")
        bleed_quarter_rgb = _sample_rgb(bleed_quarter_path, bleed_px, bleed_py)
        if _max_rgb_delta(bleed_quarter_rgb, bleed_full_rgb) <= 8.0:
            raise AssertionError(
                f"裁ち落とし枠外の不透明度が画面で薄くなっていません: "
                f"100={bleed_full_rgb} 25={bleed_quarter_rgb}"
            )

        _apply_overlay_fills(work, safe_opacity=0.0, bleed_opacity=0.0)
        bleed_zero_path = _screenshot("bleed_outer_opacity_000.png")
        bleed_zero_rgb = _sample_rgb(bleed_zero_path, bleed_px, bleed_py)
        bleed_zero_neutral = max(bleed_zero_rgb) - min(bleed_zero_rgb)
        if not (bleed_zero_neutral <= 20.0 and sum(bleed_zero_rgb) / 3.0 > 40.0):
            raise AssertionError(f"不透明度0で裁ち落とし枠外の塗りが残っています: RGB={bleed_zero_rgb}")

        print(
            "BMANGA_SAFE_AREA_FILL_VIEWPORT_VISUAL_OK "
            f"full={tuple(round(v, 1) for v in full_rgb)} "
            f"quarter={tuple(round(v, 1) for v in quarter_rgb)} "
            f"zero={tuple(round(v, 1) for v in zero_rgb)} "
            f"bleed_full={tuple(round(v, 1) for v in bleed_full_rgb)} "
            f"bleed_quarter={tuple(round(v, 1) for v in bleed_quarter_rgb)} "
            f"bleed_zero={tuple(round(v, 1) for v in bleed_zero_rgb)} "
            f"out={OUT_DIR}",
            flush=True,
        )
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _visual_check_tick():
    try:
        _run_visual_check()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        bpy.ops.wm.quit_blender()
    return None


if __name__ == "__main__":
    bpy.app.timers.register(_visual_check_tick, first_interval=0.25)
