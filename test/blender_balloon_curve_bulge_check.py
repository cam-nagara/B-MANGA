"""Blender実機用: 雲・トゲ(曲線) の「カーブのふくらみ」を数値で検証する。

検証項目:
  1. 既定値（トゲ38% / 雲0%）は従来の形状と完全一致する（後方互換）
  2. 値を変えると形が変わる／トゲ0%では側面が直線になる
  3. 雲のふくらみを変えてもこぶの高さ（外接半径）は変わらない
  4. 雲の主線が本体のふくらみへ追従する（外周が本体＋半幅を保つ）
  5. 上限まで塗りが壊れる規模の自己交差を起こさない
  6. 保存→読み込みで値が保持される／キー無しの旧ファイルは従来の見た目で読める
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_curve_bulge"
W_MM = H_MM = 62.0
COMMON = dict(cloud_bump_width_mm=13.0, cloud_bump_height_mm=6.0, jitter_seed=3,
              base_kind="ellipse")
THORN_DEFAULT = 38.0


def _fail(message: str) -> None:
    print(f"BMANGA_CURVE_BULGE_CHECK_FAIL: {message}")
    sys.exit(1)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        ADDON_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(name: str):
    return importlib.import_module(f"{ADDON_NAME}.{name}")


def _shoelace(points) -> float:
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) * 0.5


def _area_error(points) -> float:
    try:
        from shapely.geometry import Polygon
    except Exception:  # noqa: BLE001 - shapely 無しの環境では検査をスキップ
        return 0.0
    raw = _shoelace(points)
    if raw <= 1.0e-9:
        return 1.0
    return abs(Polygon(points).buffer(0).area - raw) / raw


def _max_radius(points) -> float:
    cx, cy = W_MM * 0.5, H_MM * 0.5
    return max(math.hypot(x - cx, y - cy) for x, y in points)


def _straightness(points) -> float:
    """輪郭の全長 / 頂点を直線で結んだ長さ。1.0 に近いほど直線的."""
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def main() -> None:
    _load_addon()
    shapes = _sub("utils.balloon_shapes")
    Rect = _sub("utils.geom").Rect
    rect = Rect(0.0, 0.0, W_MM, H_MM)

    def outline(shape, **over):
        kw = dict(COMMON)
        kw.update(over)
        return shapes.outline_for_shape(shape, rect, **kw)

    # 1. 既定値が従来と完全一致
    if outline("thorn-curve") != outline("thorn-curve", thorn_curve_bulge_percent=THORN_DEFAULT):
        _fail("トゲ(曲線): 既定値 38% が従来形状と一致しない")
    if outline("cloud") != outline("cloud", cloud_bump_bulge_percent=0.0):
        _fail("雲: 既定値 0% が従来形状と一致しない")

    # 2. 値を変えると形が変わる
    base_thorn = outline("thorn-curve")
    for v in (0.0, 15.0, 60.0, 100.0):
        if outline("thorn-curve", thorn_curve_bulge_percent=v) == base_thorn and v != THORN_DEFAULT:
            _fail(f"トゲ(曲線): ふくらみ {v}% で形が変わらない")
    base_cloud = outline("cloud")
    for v in (-20.0, 15.0, 30.0):
        if outline("cloud", cloud_bump_bulge_percent=v) == base_cloud:
            _fail(f"雲: ふくらみ {v}% で形が変わらない")

    # 2b. トゲ 0% は側面が直線（＝トゲ直線と同じ周長になるはず）
    flat = outline("thorn-curve", thorn_curve_bulge_percent=0.0)
    straight = outline("thorn")
    if abs(_straightness(flat) - _straightness(straight)) > 0.5:
        _fail(
            "トゲ(曲線): ふくらみ 0% でも側面が直線になっていない "
            f"(周長 {_straightness(flat):.3f} vs トゲ直線 {_straightness(straight):.3f})"
        )
    if _straightness(outline("thorn-curve", thorn_curve_bulge_percent=80.0)) <= _straightness(flat):
        _fail("トゲ(曲線): ふくらみを上げても周長が伸びていない")

    # 3. 雲のふくらみでこぶの高さが変わらない
    r0 = _max_radius(outline("cloud", cloud_bump_bulge_percent=0.0))
    for v in (-20.0, 10.0, 30.0, 50.0):
        r = _max_radius(outline("cloud", cloud_bump_bulge_percent=v))
        if abs(r - r0) > 0.01:
            _fail(f"雲: ふくらみ {v}% でこぶの高さが変わった ({r0:.4f} -> {r:.4f} mm)")

    # 4. 雲の主線が本体のふくらみへ追従する
    import random

    half = 0.35
    for v in (0.0, 15.0, 30.0):
        opts = shapes._DynamicOpts(
            bump_w=13.0, bump_w_jitter=0.0, bump_h=6.0, bump_h_jitter=0.0, offset=0.5,
            sub_w=0.0, sub_w_jitter=0.0, sub_h=0.0, sub_h_jitter=0.0,
            rng=random.Random(3), base_kind="ellipse", cloud_bulge=v,
        )
        loops = shapes._bezier_cloud_line_loops(rect, opts, half, None)
        if loops is None:
            _fail(f"雲: ふくらみ {v}% で主線ループが生成できない")
        outer, _inner = loops
        body_r = _max_radius(outline("cloud", cloud_bump_bulge_percent=v))
        outer_r = _max_radius([a.co for a in outer])
        if abs((outer_r - body_r) - half) > 0.02:
            _fail(
                f"雲: ふくらみ {v}% で主線が本体に追従していない "
                f"(外周-本体 = {outer_r - body_r:.4f} mm, 期待 {half})"
            )

    # 5. 上限まで塗りが壊れる規模の自己交差を起こさない
    for v in (0.0, 50.0, 100.0):
        err = _area_error(outline("thorn-curve", thorn_curve_bulge_percent=v))
        if err > 0.03:
            _fail(f"トゲ(曲線): ふくらみ {v}% で自己交差が大きい (面積誤差 {err * 100:.1f}%)")
    for v in (-30.0, 0.0, 30.0):
        err = _area_error(outline("cloud", cloud_bump_bulge_percent=v))
        if err > 0.03:
            _fail(f"雲: ふくらみ {v}% で自己交差が大きい (面積誤差 {err * 100:.1f}%)")

    # 6. 保存往復
    schema = _sub("io.schema")
    work = getattr(bpy.context.scene, "bmanga_work", None)
    if work is None:
        _fail("scene.bmanga_work が無い（アドオンの登録に失敗している）")
    page = work.pages.add()
    entry = page.balloons.add()
    entry.shape = "thorn-curve"
    entry.shape_params.thorn_curve_bulge_percent = 72.0
    entry.shape_params.cloud_bump_bulge_percent = -12.0
    data = schema.balloon_entry_to_dict(entry)
    if abs(float(data["shapeParams"]["thornCurveBulgePercent"]) - 72.0) > 1e-6:
        _fail("保存: 側面のふくらみが書き出されていない")
    if abs(float(data["shapeParams"]["cloudBumpBulgePercent"]) + 12.0) > 1e-6:
        _fail("保存: こぶのふくらみが書き出されていない")
    restored = page.balloons.add()
    schema.balloon_entry_from_dict(restored, data)
    if abs(float(restored.shape_params.thorn_curve_bulge_percent) - 72.0) > 1e-6:
        _fail("読込: 側面のふくらみが復元されない")
    if abs(float(restored.shape_params.cloud_bump_bulge_percent) + 12.0) > 1e-6:
        _fail("読込: こぶのふくらみが復元されない")

    # 6b. キー無しの旧ファイルは従来の見た目（トゲ38% / 雲0%）で読める
    legacy_sp = dict(data["shapeParams"])
    legacy_sp.pop("thornCurveBulgePercent", None)
    legacy_sp.pop("cloudBumpBulgePercent", None)
    legacy = dict(data)
    legacy["shapeParams"] = legacy_sp
    old = page.balloons.add()
    schema.balloon_entry_from_dict(old, legacy)
    if abs(float(old.shape_params.thorn_curve_bulge_percent) - THORN_DEFAULT) > 1e-6:
        _fail("互換: キー無しファイルで側面のふくらみが 38% にならない")
    if float(old.shape_params.cloud_bump_bulge_percent) != 0.0:
        _fail("互換: キー無しファイルでこぶのふくらみが 0% にならない")

    print("BMANGA_CURVE_BULGE_CHECK_OK")


if __name__ == "__main__":
    main()
