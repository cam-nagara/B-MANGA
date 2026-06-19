"""Blender実機用: フキダシの角処理と線種設定を確認。"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_balloon_corner_line_pattern"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        ADDON_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _import(name: str):
    return importlib.import_module(f"{ADDON_NAME}.{name}")


def _dot_bbox_ratio(polygons, sample_count: int = 8) -> float:
    worst = 0.0
    for outer, _holes in polygons[:sample_count]:
        xs = [float(p[0]) for p in outer]
        ys = [float(p[1]) for p in outer]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        if width <= 0.0 or height <= 0.0:
            raise AssertionError("点線の点に面積がありません")
        ratio = max(width, height) / min(width, height)
        worst = max(worst, ratio)
    return worst


def main() -> None:
    _load_addon()
    balloon_shapes = _import("utils.balloon_shapes")
    balloon_line_mesh = _import("utils.balloon_line_mesh")
    line_pattern = _import("utils.line_pattern")
    geom = _import("utils.geom")
    schema = _import("io.schema")

    scene = bpy.context.scene
    work = scene.bmanga_work
    page = work.pages.add()
    page.id = "p0001"
    entry = page.balloons.add()
    entry.id = "balloon_corner_pattern"
    entry.shape = "rect"
    entry.width_mm = 80.0
    entry.height_mm = 40.0
    entry.rounded_corner_radius_mm = 8.0
    entry.line_width_mm = 1.0
    rect = geom.Rect(0.0, 0.0, 80.0, 40.0)

    entry.corner_type = "square"
    entry.corner_type_initialized = True
    square_outline = balloon_shapes.outline_for_entry(entry, rect)
    if len(square_outline) != 4:
        raise AssertionError(f"直角フキダシの点数が不正です: {len(square_outline)}")
    if balloon_shapes.bezier_loop_for_entry(entry, rect) is not None:
        raise AssertionError("直角フキダシが曲線輪郭になっています")

    entry.corner_type = "rounded"
    rounded_outline = balloon_shapes.outline_for_entry(entry, rect)
    if len(rounded_outline) <= 8:
        raise AssertionError("丸角フキダシの輪郭点が不足しています")
    if balloon_shapes.bezier_loop_for_entry(entry, rect) is None:
        raise AssertionError("丸角フキダシが曲線輪郭になっていません")

    entry.corner_type = "bevel"
    bevel_outline = balloon_shapes.outline_for_entry(entry, rect)
    if len(bevel_outline) != 8:
        raise AssertionError(f"面取りフキダシの点数が不正です: {len(bevel_outline)}")
    if balloon_shapes.bezier_loop_for_entry(entry, rect) is not None:
        raise AssertionError("面取りフキダシが曲線輪郭になっています")
    data = schema.balloon_entry_to_dict(entry)
    if data.get("cornerType") != "bevel":
        raise AssertionError("角の種類が保存されていません")

    entry.line_style = "dashed"
    entry.dashed_segment_length_mm = 5.5
    entry.dashed_gap_mm = 2.25
    entry.dotted_gap_mm = 0.8
    data = schema.balloon_entry_to_dict(entry)
    restored = page.balloons.add()
    schema.balloon_entry_from_dict(restored, data, opacity_percent=True)
    if restored.corner_type != "bevel":
        raise AssertionError("角の種類が復元されていません")
    if abs(restored.dashed_segment_length_mm - 5.5) > 1.0e-6:
        raise AssertionError("破線の線分が復元されていません")
    if abs(restored.dashed_gap_mm - 2.25) > 1.0e-6:
        raise AssertionError("破線の間隔が復元されていません")
    if abs(restored.dotted_gap_mm - 0.8) > 1.0e-6:
        raise AssertionError("点線の間隔が復元されていません")
    restored.dashed_gap_mm = 0.0
    restored.dotted_gap_mm = 0.0
    if line_pattern.dashed_gap_mm(restored, restored.line_width_mm) != 0.0:
        raise AssertionError("破線の間隔0が反映されていません")
    if line_pattern.dotted_gap_mm(restored, restored.line_width_mm) != 0.0:
        raise AssertionError("点線の間隔0が反映されていません")

    samples = [(0.0, 0.0), (0.08, 0.0), (0.08, 0.04), (0.0, 0.04)]
    dotted = balloon_line_mesh._build_dashed_band_polygons(
        samples,
        line_width_m=0.001,
        line_style="dotted",
        valley_sharp=False,
        dotted_gap_mm=0.8,
    )
    if len(dotted) < 6:
        raise AssertionError("点線の点が生成されていません")
    ratio = _dot_bbox_ratio(dotted)
    if ratio > 1.02:
        raise AssertionError(f"点線の点が真円ではありません: ratio={ratio:.4f}")

    dense = balloon_line_mesh._build_dashed_band_polygons(
        samples,
        line_width_m=0.001,
        line_style="dashed",
        valley_sharp=False,
        dash_segment_mm=1.0,
        dash_gap_mm=1.0,
    )
    sparse = balloon_line_mesh._build_dashed_band_polygons(
        samples,
        line_width_m=0.001,
        line_style="dashed",
        valley_sharp=False,
        dash_segment_mm=8.0,
        dash_gap_mm=4.0,
    )
    if len(dense) <= len(sparse):
        raise AssertionError("破線の線分・間隔が生成数へ反映されていません")

    print("BMANGA_BALLOON_CORNER_LINE_PATTERN_OK")


if __name__ == "__main__":
    main()
