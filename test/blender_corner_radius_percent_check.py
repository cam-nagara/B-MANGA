"""Blender実機用: 角丸の角半径を%指定できることを確認。"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_corner_radius_percent"


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


def _assert_close(actual: float, expected: float, label: str) -> None:
    if abs(float(actual) - float(expected)) > 1.0e-6:
        raise AssertionError(f"{label}: {actual} != {expected}")


def _assert_points_close(a, b, label: str) -> None:
    if len(a) != len(b):
        raise AssertionError(f"{label}: 点数が違います {len(a)} != {len(b)}")
    for index, (pa, pb) in enumerate(zip(a, b)):
        _assert_close(pa[0], pb[0], f"{label}[{index}].x")
        _assert_close(pa[1], pb[1], f"{label}[{index}].y")


def main() -> None:
    _load_addon()
    balloon_shapes = _import("utils.balloon_shapes")
    corner_radius = _import("utils.corner_radius")
    geom = _import("utils.geom")
    gn = _import("utils.geometry_nodes_bridge")
    effect_line_gen = _import("operators.effect_line_gen")
    effect_line_core = _import("core.effect_line")
    schema = _import("io.schema")

    scene = bpy.context.scene
    work = scene.bmanga_work
    page = work.pages.add()
    page.id = "p0001"
    entry = page.balloons.add()
    entry.id = "balloon_test"
    entry.shape = "rect"
    entry.width_mm = 80.0
    entry.height_mm = 40.0
    entry.rounded_corner_enabled = True
    entry.rounded_corner_radius_unit = "percent"
    entry.rounded_corner_radius_percent = 50.0

    rect = geom.Rect(0.0, 0.0, 80.0, 40.0)
    _assert_close(corner_radius.radius_for_balloon_entry(entry, rect), 10.0, "フキダシ%角半径")
    percent_outline = balloon_shapes.outline_for_entry(entry, rect)
    mm_outline = balloon_shapes.outline_for_shape(
        "rect",
        rect,
        rounded_corner_enabled=True,
        rounded_corner_radius_mm=10.0,
    )
    _assert_points_close(percent_outline, mm_outline, "フキダシ%角丸輪郭")
    values = gn.balloon_values(entry)
    _assert_close(values["角半径"], 10.0, "フキダシ表示値")

    data = schema.balloon_entry_to_dict(entry)
    if data.get("roundedCornerRadiusUnit") != "percent":
        raise AssertionError("フキダシ保存: 単位が保存されていません")
    _assert_close(data.get("roundedCornerRadiusPercent"), 50.0, "フキダシ保存: %")
    restored = page.balloons.add()
    schema.balloon_entry_from_dict(restored, data, opacity_percent=True)
    if restored.rounded_corner_radius_unit != "percent":
        raise AssertionError("フキダシ読込: 単位が復元されていません")
    _assert_close(restored.rounded_corner_radius_percent, 50.0, "フキダシ読込: %")

    params = scene.bmanga_effect_line_params
    params.start_shape = "rect"
    params.start_rounded_corner_enabled = True
    params.start_rounded_corner_radius_unit = "percent"
    params.start_rounded_corner_radius_percent = 50.0
    params.end_shape = "rect"
    params.end_rounded_corner_enabled = True
    params.end_rounded_corner_radius_unit = "percent"
    params.end_rounded_corner_radius_percent = 100.0

    effect_start = effect_line_gen._shape_outline(params, "start", rect, (40.0, 20.0), seed=0)
    _assert_points_close(effect_start, mm_outline, "効果線始点%角丸輪郭")
    if not effect_line_gen._shape_guide_uses_smooth_bezier(params, "start"):
        raise AssertionError("効果線始点: %指定の角丸が曲線扱いになっていません")
    effect_values = gn.effect_values(params, (0.0, 0.0, 80.0, 40.0), 1)
    _assert_close(effect_values["始点 角半径"], 10.0, "効果線始点表示値")
    _assert_close(effect_values["終点 角半径"], 20.0, "効果線終点表示値")

    effect_data = effect_line_core.effect_params_to_dict(params)
    if effect_data.get("start_rounded_corner_radius_unit") != "percent":
        raise AssertionError("効果線保存: 単位が保存されていません")
    _assert_close(effect_data.get("end_rounded_corner_radius_percent"), 100.0, "効果線保存: 終点%")
    params.start_rounded_corner_radius_unit = "mm"
    params.end_rounded_corner_radius_unit = "mm"
    effect_line_core.effect_params_from_dict(params, effect_data)
    if params.start_rounded_corner_radius_unit != "percent" or params.end_rounded_corner_radius_unit != "percent":
        raise AssertionError("効果線読込: 単位が復元されていません")

    params.start_rounded_corner_radius_percent = 0.0
    if effect_line_gen._shape_guide_uses_smooth_bezier(params, "start"):
        raise AssertionError("効果線始点: 0%の角丸が曲線扱いになっています")

    print("BMANGA_CORNER_RADIUS_PERCENT_OK")


if __name__ == "__main__":
    main()
