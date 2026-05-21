"""Blender UI実機用: 用紙ガイド線が実際のビューで見えることを確認."""

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
    os.environ.get("BNAME_PAPER_GUIDE_VISUAL_OUT", "")
    or tempfile.mkdtemp(prefix="bname_paper_guide_visual_")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_paper_guide_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_paper_guide_visual"] = mod
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


def _redraw(iterations: int = 5) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:
        pass


def _screenshot(name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    _redraw(8)
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    return path


def _guide_pixel_stats(path: Path, *, expected: str) -> tuple[int, tuple[int, int, int]]:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        hits = 0
        strongest = (0, 0, 0)
        strongest_score = -999
        for py in range(80, max(80, height - 120)):
            for px in range(80, max(80, width - 420)):
                r, g, b = image.getpixel((px, py))
                if expected == "cyan":
                    score = (g + b) - (2 * r)
                    if score > strongest_score:
                        strongest_score = score
                        strongest = (r, g, b)
                    if g >= 150 and b >= 150 and r <= 90:
                        hits += 1
                else:
                    score = (2 * g) - (r + b)
                    if score > strongest_score:
                        strongest_score = score
                        strongest = (r, g, b)
                    if g >= 150 and r <= 120 and b <= 140:
                        hits += 1
    return hits, strongest


def _run_visual_check() -> None:
    if bpy.app.background:
        raise RuntimeError("このチェックは --background なしで実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="bname_paper_guide_visual_work_"))
    mod = None
    try:
        try:
            bpy.context.preferences.view.show_splash = False
        except Exception:
            pass
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PaperGuideVisual.bname"))
        assert "FINISHED" in result, result

        from bname_dev_paper_guide_visual.core.work import get_work
        from bname_dev_paper_guide_visual.ui import overlay
        from bname_dev_paper_guide_visual.ui import overlay_shared
        from bname_dev_paper_guide_visual.utils import page_grid, paper_guide_object

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        paper = work.paper
        paper.show_guides = True
        paper.show_canvas_frame = True
        paper.show_finish_frame = True
        paper.show_bleed_frame = True
        paper.show_inner_frame = True
        paper.show_safe_line = True
        paper.show_trim_marks = True
        overlay.apply_bname_shading_mode(context)

        with _view3d_override():
            bpy.ops.view3d.view_axis(type="TOP", align_active=False)
            space = bpy.context.space_data
            rv3d = space.region_3d
            rv3d.view_perspective = "ORTHO"
            rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
            rv3d.view_location = Vector((0.1285, 0.182, 0.0))
            space.overlay.show_floor = False
            space.overlay.show_axis_x = False
            space.overlay.show_axis_y = False
            if getattr(space.shading, "type", "") != "SOLID":
                space.shading.type = "SOLID"
            space.shading.light = "FLAT"
            space.shading.color_type = "TEXTURE"
            fit = bpy.ops.bname.view_fit_page("EXEC_DEFAULT")
            assert "FINISHED" in fit, fit

        paper_guide_object.apply_view_constant_thickness()
        _ = overlay_shared.compute_paper_rects(paper)
        _ = page_grid.page_total_offset_mm(work, context.scene, 0)
        path = _screenshot("paper_guide_visual.png")
        cyan_hits, cyan_strongest = _guide_pixel_stats(path, expected="cyan")
        green_hits, green_strongest = _guide_pixel_stats(path, expected="green")
        if cyan_hits < 300:
            raise AssertionError(f"用紙ガイド線が画面に十分出ていません: hits={cyan_hits} rgb={cyan_strongest} image={path}")
        if green_hits < 100:
            raise AssertionError(f"セーフラインが画面に十分出ていません: hits={green_hits} rgb={green_strongest} image={path}")

        print(
            "BNAME_PAPER_GUIDE_VIEWPORT_VISUAL_OK "
            f"cyan_hits={cyan_hits}:rgb={cyan_strongest} "
            f"green_hits={green_hits}:rgb={green_strongest} "
            + f" out={OUT_DIR}",
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
