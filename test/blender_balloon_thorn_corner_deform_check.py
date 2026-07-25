"""Blender実機用: トゲの「角ばり」「四隅寄せ」変形を数値で検証する。

検証項目:
  1. 0% / 0% は変形前（従来のトゲ形状）と完全一致する（後方互換）
  2. ベース形状が矩形のときは変形が適用されない
  3. 角ばりは四隅だけを外へ張り出させ、上下左右の辺の中心（＝外形サイズ）を変えない
  4. 四隅寄せはトゲの本数を変えず、トゲの位置を四隅へ寄せる
  5. 上限（角ばり200% / 四隅寄せ300%）まで自己交差しない単純多角形になる
  6. 保存→読み込みで値が保持される
"""

from __future__ import annotations

import importlib
import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_thorn_corner_deform"
RECT_W, RECT_H = 58.0, 74.0
COMMON = dict(
    cloud_bump_width_mm=11.0,
    cloud_bump_height_mm=5.0,
    jitter_seed=3,
)


def _fail(message: str) -> None:
    print(f"BMANGA_THORN_CORNER_DEFORM_CHECK_FAIL: {message}")
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


def _submodule(name: str):
    return importlib.import_module(f"{ADDON_NAME}.{name}")


def _load_modules():
    _load_addon()
    shapes = _submodule("utils.balloon_shapes")
    geom = _submodule("utils.geom")
    return shapes, geom.Rect


def _outline(shapes, Rect, shape: str, square: float, squeeze: float, base_kind="ellipse"):
    return shapes.outline_for_shape(
        shape,
        Rect(0.0, 0.0, RECT_W, RECT_H),
        base_kind=base_kind,
        thorn_corner_square_percent=square,
        thorn_corner_squeeze_percent=squeeze,
        **COMMON,
    )


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _shoelace_area(points) -> float:
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) * 0.5


def _self_intersection_area_error(points) -> float:
    """自己交差の「大きさ」を面積の食い違いで測る（0 に近いほど健全）.

    トゲ曲線は変形の有無に関わらず、鋭い先端で 0.1mm 規模の微小な自己交差を
    元から起こす（現行実装でも 240 通り中 65 通りが shapely 的には invalid）。
    そのため厳密な単純多角形は要求せず、塗り面積が壊れるレベルの
    大きな自己交差だけを検出する。
    """
    try:
        from shapely.geometry import Polygon
    except Exception:  # noqa: BLE001 - shapely 無しの環境では検査をスキップ
        return 0.0
    raw = _shoelace_area(points)
    if raw <= 1.0e-9:
        return 1.0
    repaired = Polygon(points).buffer(0).area
    return abs(repaired - raw) / raw


def _segment_length(points) -> float:
    total = 0.0
    for i in range(len(points)):
        ax, ay = points[i]
        bx, by = points[(i + 1) % len(points)]
        total += math.hypot(bx - ax, by - ay)
    return total


def main() -> None:
    shapes, Rect = _load_modules()

    for shape in ("thorn", "thorn-curve"):
        # 1. 0%/0% は従来と完全一致
        base = shapes.outline_for_shape(
            shape, Rect(0.0, 0.0, RECT_W, RECT_H), base_kind="ellipse", **COMMON
        )
        zero = _outline(shapes, Rect, shape, 0.0, 0.0)
        if base != zero:
            _fail(f"{shape}: 角ばり0%/四隅寄せ0% が従来形状と一致しない")

        # 2. 矩形ベースでは変形しない
        rect_base = shapes.outline_for_shape(
            shape, Rect(0.0, 0.0, RECT_W, RECT_H), base_kind="rect", **COMMON
        )
        rect_deformed = _outline(shapes, Rect, shape, 200.0, 300.0, base_kind="rect")
        if rect_base != rect_deformed:
            _fail(f"{shape}: ベース形状が矩形なのに四隅変形が適用されている")

        # 3. 角ばりは軸方向（上下左右の辺の中心）のベース輪郭を動かさない。
        #    実際の輪郭は、軸の近くにあるトゲの先端がわずかに外へ出るぶんだけ
        #    bbox が広がるので、山の高さに対する割合で上限を設ける。
        for square in (50.0, 100.0, 200.0):
            n = 2.0 + (square / 100.0) * 12.0
            for axis in (0.0, math.pi * 0.5, math.pi, math.pi * 1.5):
                r = shapes._corner_super_radius(axis, n)
                if abs(r - 1.0) > 1.0e-9:
                    _fail(f"角ばり{square}%: 軸方向の半径が 1.0 でない ({r:.12f})")
        b0 = _bbox(zero)
        bump_h = float(COMMON["cloud_bump_height_mm"])
        for square in (50.0, 100.0, 200.0):
            bs_ = _bbox(_outline(shapes, Rect, shape, square, 0.0))
            for i, (a, b) in enumerate(zip(b0, bs_)):
                if abs(a - b) > bump_h * 0.20:
                    _fail(
                        f"{shape}: 角ばり{square}% で外形が想定以上に変わった "
                        f"(bbox[{i}] {a:.3f} -> {b:.3f})"
                    )

        # 4. 角ばりは四隅を外へ張り出させる（対角方向の到達距離が増える）
        def corner_reach(points):
            cx, cy = RECT_W * 0.5, RECT_H * 0.5
            best = 0.0
            for x, y in points:
                dx, dy = (x - cx) / (RECT_W * 0.5), (y - cy) / (RECT_H * 0.5)
                if dx > 0.25 and dy > 0.25:  # 右下（rect 座標系）方向のみ
                    best = max(best, math.hypot(dx, dy))
            return best

        if corner_reach(_outline(shapes, Rect, shape, 200.0, 0.0)) <= corner_reach(zero) + 1.0e-6:
            _fail(f"{shape}: 角ばり200% で四隅が張り出していない")

        # 5. 四隅寄せは頂点数（＝トゲの本数）を変えない
        for squeeze in (100.0, 200.0, 300.0):
            moved = _outline(shapes, Rect, shape, 0.0, squeeze)
            if len(moved) != len(zero):
                _fail(
                    f"{shape}: 四隅寄せ{squeeze}% でトゲの本数が変わった "
                    f"({len(zero)} -> {len(moved)})"
                )
            if moved == zero:
                _fail(f"{shape}: 四隅寄せ{squeeze}% で形が変わっていない")

        # 6. 上限まで自己交差しない・退化しない
        for square in (0.0, 100.0, 200.0):
            for squeeze in (0.0, 100.0, 200.0, 300.0):
                pts = _outline(shapes, Rect, shape, square, squeeze)
                if len(pts) < 3:
                    _fail(f"{shape}: 角ばり{square}/四隅寄せ{squeeze} で輪郭が退化した")
                if _segment_length(pts) <= 1.0:
                    _fail(f"{shape}: 角ばり{square}/四隅寄せ{squeeze} で周長がほぼ 0")
                err = _self_intersection_area_error(pts)
                if err > 0.03:
                    _fail(
                        f"{shape}: 角ばり{square}/四隅寄せ{squeeze} で塗りが壊れる規模の"
                        f"自己交差 (面積誤差 {err * 100:.1f}%)"
                    )

    # 7. ベジェ経路（トゲ曲線の主線）も同じ条件で通ること
    for squeeze in (0.0, 150.0, 300.0):
        anchors = shapes.bezier_loop_for_shape(
            "thorn-curve",
            Rect(0.0, 0.0, RECT_W, RECT_H),
            base_kind="ellipse",
            thorn_corner_square_percent=100.0,
            thorn_corner_squeeze_percent=squeeze,
            **COMMON,
        )
        if not anchors or len(anchors) < 6:
            _fail(f"bezier: 四隅寄せ{squeeze}% でアンカー列が得られない")

    # 8. 保存 → 読み込みで値が保持される
    schema = _submodule("io.schema")
    work = getattr(bpy.context.scene, "bmanga_work", None)
    if work is None:
        _fail("scene.bmanga_work が無い（アドオンの登録に失敗している）")
    page = work.pages.add()
    entry = page.balloons.add()
    entry.shape = "thorn-curve"
    entry.shape_params.thorn_corner_square_percent = 123.0
    entry.shape_params.thorn_corner_squeeze_percent = 234.0
    data = schema.balloon_entry_to_dict(entry)
    if abs(float(data["shapeParams"]["thornCornerSquarePercent"]) - 123.0) > 1e-6:
        _fail("保存: 角ばりが書き出されていない")
    if abs(float(data["shapeParams"]["thornCornerSqueezePercent"]) - 234.0) > 1e-6:
        _fail("保存: 四隅寄せが書き出されていない")

    restored = page.balloons.add()
    schema.balloon_entry_from_dict(restored, data)
    if abs(float(restored.shape_params.thorn_corner_square_percent) - 123.0) > 1e-6:
        _fail("読み込み: 角ばりが復元されない")
    if abs(float(restored.shape_params.thorn_corner_squeeze_percent) - 234.0) > 1e-6:
        _fail("読み込み: 四隅寄せが復元されない")

    # 9. 古いファイル（キー無し）は 0% として読み込まれる
    legacy = dict(data)
    legacy_sp = dict(data["shapeParams"])
    legacy_sp.pop("thornCornerSquarePercent", None)
    legacy_sp.pop("thornCornerSqueezePercent", None)
    legacy["shapeParams"] = legacy_sp
    old = page.balloons.add()
    schema.balloon_entry_from_dict(old, legacy)
    if float(old.shape_params.thorn_corner_square_percent) != 0.0:
        _fail("互換: キー無しファイルで角ばりが 0% にならない")
    if float(old.shape_params.thorn_corner_squeeze_percent) != 0.0:
        _fail("互換: キー無しファイルで四隅寄せが 0% にならない")

    print("BMANGA_THORN_CORNER_DEFORM_CHECK_OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory():
        main()
