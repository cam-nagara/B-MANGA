"""Blender実機チェック: 見開き化の「トンボを合わせる」設定."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_spread_tombo"


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


def _assert_close(actual: float, expected: float, label: str, eps: float = 0.01) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: actual={actual:.4f} expected={expected:.4f}")


def _mm_to_px(mm: float, dpi: int) -> int:
    return int(round(float(mm) / 25.4 * float(dpi)))


def _page_bg_width_mm(page_id: str) -> float:
    obj = bpy.data.objects.get(f"page_paper_bg_{page_id}")
    if obj is None or obj.data is None:
        raise AssertionError(f"用紙背景がありません: {page_id}")
    xs = [float(vertex.co.x) * 1000.0 for vertex in obj.data.vertices]
    return max(xs) - min(xs)


def _guide_x_values_mm(page_id: str) -> list[float]:
    obj = bpy.data.objects.get(f"page_paper_guide_{page_id}")
    if obj is None or obj.data is None:
        raise AssertionError(f"用紙ガイドがありません: {page_id}")
    xs: list[float] = []
    for spline in obj.data.splines:
        for point in spline.points:
            xs.append(round(float(point.co.x) * 1000.0, 3))
    return xs


def _ensure_two_pages(work) -> None:
    while len(work.pages) < 2:
        result = bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise AssertionError(f"ページ追加に失敗しました: {result}")


def _first_coma_x(page) -> float:
    if len(page.comas) == 0:
        raise AssertionError("確認用のコマがありません")
    return float(page.comas[0].rect_x_mm)


def _ensure_page_coma(work, page_index: int) -> None:
    if len(work.pages[page_index].comas) > 0:
        return
    work.active_page_index = page_index
    result = bpy.ops.bmanga.coma_add("EXEC_DEFAULT")
    if "FINISHED" not in result:
        raise AssertionError(f"確認用コマの追加に失敗しました: {result}")


def _assert_default_values(work) -> None:
    op_rna = bpy.ops.bmanga.pages_merge_spread.get_rna_type()
    if bool(op_rna.properties["tombo_aligned"].default) is not True:
        raise AssertionError("「トンボを合わせる」の実行初期値がオンではありません")
    _assert_close(
        float(op_rna.properties["tombo_gap_mm"].default),
        -9.6,
        "「間隔」の実行初期値",
    )
    if bool(getattr(work.pages[0], "tombo_aligned", False)) is not True:
        raise AssertionError("ページデータの「トンボを合わせる」初期値がオンではありません")
    _assert_close(float(getattr(work.pages[0], "tombo_gap_mm", 0.0)), -9.6, "ページデータの「間隔」初期値")


def _assert_default_merge_case(work) -> None:
    page_grid = _sub("utils.page_grid")

    _ensure_two_pages(work)
    _ensure_page_coma(work, 0)
    _ensure_page_coma(work, 1)
    work.active_page_index = 0
    work.pages[0].comas[0].rect_x_mm = 35.0
    work.pages[0].comas[0].rect_width_mm = 20.0
    work.pages[1].comas[0].rect_x_mm = 10.0
    work.pages[1].comas[0].rect_width_mm = 20.0
    right_before_x = _first_coma_x(work.pages[0])
    base_width = float(work.paper.canvas_width_mm)
    expected_offset = base_width - 9.6

    result = bpy.ops.bmanga.pages_merge_spread("EXEC_DEFAULT", left_index=0)
    if "FINISHED" not in result:
        raise AssertionError(f"初期値での見開き化に失敗しました: {result}")
    spread = work.pages[0]
    if bool(getattr(spread, "tombo_aligned", False)) is not True:
        raise AssertionError("見開き化後の「トンボを合わせる」初期値がオンではありません")
    _assert_close(float(getattr(spread, "tombo_gap_mm", 0.0)), -9.6, "見開き化後の「間隔」初期値")
    _assert_close(page_grid.spread_right_page_offset_mm(spread, base_width), expected_offset, "初期値の右ページ開始位置")
    _assert_close(_first_coma_x(spread), right_before_x + expected_offset, "初期値の右ページ側コマ位置")

    result = bpy.ops.bmanga.pages_split_spread("EXEC_DEFAULT", spread_index=0)
    if "FINISHED" not in result:
        raise AssertionError(f"初期値見開きの解除に失敗しました: {result}")


def _assert_missing_value_fallbacks() -> None:
    page_grid = _sub("utils.page_grid")

    class MinimalPage:
        spread = True

    page = MinimalPage()
    _assert_close(page_grid.spread_right_page_offset_mm(page, 100.0), 90.4, "未設定時の右ページ開始位置")
    page.tombo_aligned = None
    page.tombo_gap_mm = None
    _assert_close(page_grid.spread_right_page_offset_mm(page, 100.0), 90.4, "None時の右ページ開始位置")
    page.tombo_aligned = True
    page.tombo_gap_mm = 0.0
    _assert_close(page_grid.spread_right_page_offset_mm(page, 100.0), 100.0, "0mm指定時の右ページ開始位置")
    page.tombo_aligned = False
    page.tombo_gap_mm = None
    _assert_close(page_grid.spread_right_page_offset_mm(page, 100.0), 100.0, "トンボ合わせオフ時の右ページ開始位置")


def _assert_tombo_merge_case(work, *, aligned: bool, gap_mm: float) -> None:
    page_grid = _sub("utils.page_grid")
    overlay_shared = _sub("ui.overlay_shared")
    export_page_regions = _sub("io.export_page_regions")
    export_pipeline = _sub("io.export_pipeline")
    paper_bg_object = _sub("utils.paper_bg_object")
    paper_guide_object = _sub("utils.paper_guide_object")

    _ensure_two_pages(work)
    _ensure_page_coma(work, 0)
    _ensure_page_coma(work, 1)
    work.active_page_index = 0
    work.pages[0].comas[0].rect_x_mm = 35.0
    work.pages[0].comas[0].rect_width_mm = 20.0
    work.pages[1].comas[0].rect_x_mm = 10.0
    work.pages[1].comas[0].rect_width_mm = 20.0
    right_before_x = _first_coma_x(work.pages[0])
    left_before_x = _first_coma_x(work.pages[1])
    base_width = float(work.paper.canvas_width_mm)
    expected_offset = page_grid.spread_right_page_offset_mm_for_values(
        base_width,
        aligned,
        gap_mm,
    )
    expected_width = max(base_width, expected_offset + base_width)

    result = bpy.ops.bmanga.pages_merge_spread(
        "EXEC_DEFAULT",
        left_index=0,
        tombo_aligned=aligned,
        tombo_gap_mm=gap_mm,
    )
    if "FINISHED" not in result:
        raise AssertionError(f"見開き化に失敗しました: {result}")
    spread = work.pages[0]
    spread_id = str(spread.id)

    _assert_close(page_grid.spread_right_page_offset_mm(spread, base_width), expected_offset, "右ページ開始位置")
    _assert_close(page_grid.page_content_width_mm(work, 0, base_width), expected_width, "見開き幅")
    _assert_close(_first_coma_x(spread), right_before_x + expected_offset, "右ページ側コマ位置")

    dpi = 254
    options = export_pipeline.ExportOptions(area="canvas", dpi_override=dpi)
    expected_px = (_mm_to_px(expected_width, dpi), _mm_to_px(float(work.paper.canvas_height_mm), dpi))
    if export_pipeline._page_canvas_size_px(work, spread, options) != expected_px:
        raise AssertionError(f"見開き出力サイズが違います: {export_pipeline._page_canvas_size_px(work, spread, options)} != {expected_px}")
    left_box = export_page_regions.page_crop_box(work, spread, options, spread_side="left")
    right_box = export_page_regions.page_crop_box(work, spread, options, spread_side="right")
    expected_left = (0, 0, _mm_to_px(base_width, dpi), expected_px[1])
    expected_right = (_mm_to_px(expected_offset, dpi), 0, _mm_to_px(expected_offset + base_width, dpi), expected_px[1])
    if left_box != expected_left or right_box != expected_right:
        raise AssertionError(f"見開き左右切り出し位置が違います: left={left_box}, right={right_box}")

    paper_bg_object.ensure_paper_bg_for_page(bpy.context.scene, work, 0)
    _assert_close(_page_bg_width_mm(spread_id), expected_width, "見開き用紙背景幅")

    paper_guide_object.ensure_paper_guides_for_page(bpy.context.scene, work, 0)
    xs = _guide_x_values_mm(spread_id)
    if not any(abs(x - expected_width) < 0.1 for x in xs):
        raise AssertionError(f"見開きガイドの右端が見開き全体幅にありません: width={expected_width}, xs={xs[:12]}")
    left_rects = overlay_shared.compute_paper_rects(work.paper, is_left_half=True)
    right_rects = overlay_shared.compute_paper_rects(work.paper, is_left_half=False)
    page_pair_edges = {
        "左ページ裁ち落とし枠の右端": left_rects.bleed.x2,
        "右ページ裁ち落とし枠の左端": expected_offset + right_rects.bleed.x,
        "左ページ仕上がり枠の右端": left_rects.finish.x2,
        "右ページ仕上がり枠の左端": expected_offset + right_rects.finish.x,
    }
    missing = [
        label
        for label, expected in page_pair_edges.items()
        if not any(abs(x - expected) < 0.1 for x in xs)
    ]
    if missing:
        raise AssertionError(f"見開きガイドが左右ページ別に残っていません: {missing} xs={xs[:24]}")

    result = bpy.ops.bmanga.pages_split_spread("EXEC_DEFAULT", spread_index=0)
    if "FINISHED" not in result:
        raise AssertionError(f"見開き解除に失敗しました: {result}")
    ids = [str(page.id) for page in work.pages[:2]]
    if ids != ["p0001", "p0002"]:
        raise AssertionError(f"見開き解除後のページが戻っていません: {ids}")
    _assert_close(_first_coma_x(work.pages[0]), right_before_x, "見開き解除後の右ページ側コマ位置")
    _assert_close(_first_coma_x(work.pages[1]), left_before_x, "見開き解除後の左ページ側コマ位置")


def main() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_spread_tombo_"))
    success = False
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "SpreadTombo.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        work = bpy.context.scene.bmanga_work
        _ensure_two_pages(work)
        work.paper.canvas_width_mm = 100.0
        work.paper.canvas_height_mm = 150.0

        _assert_default_values(work)
        _assert_missing_value_fallbacks()
        _assert_default_merge_case(work)
        _assert_tombo_merge_case(work, aligned=True, gap_mm=-10.0)
        _assert_tombo_merge_case(work, aligned=False, gap_mm=-10.0)
        print("BMANGA_SPREAD_TOMBO_ALIGN_OK", flush=True)
        success = True
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)
        os._exit(0 if success else 1)


if __name__ == "__main__":
    main()
