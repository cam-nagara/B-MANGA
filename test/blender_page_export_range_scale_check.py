"""Blender実機用: ページ出力の範囲指定・倍率・最新コマ表示を確認する."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_page_export",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_page_export"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _setup_work(tmp_dir: Path):
    scene = bpy.context.scene
    scene.bname_mode = "PAGE"
    work = scene.bname_work
    work.loaded = True
    work.work_dir = str(tmp_dir / "ExportRange.bname")
    work.work_info.work_name = "ExportRange"
    work.work_info.episode_number = 1
    work.work_info.page_number_start = 1
    work.work_info.page_number_end = 4
    work.paper.canvas_width_mm = 20.0
    work.paper.canvas_height_mm = 10.0
    work.paper.dpi = 100
    work.pages.clear()
    for idx in range(4):
        page = work.pages.add()
        page.id = f"p{idx + 1:04d}"
        page.title = f"{idx + 1}ページ"
        page.in_page_range = True
        coma = page.comas.add()
        coma.id = "c01"
        coma.coma_id = "c01"
        coma.title = "基本枠"
        coma.rect_x_mm = 2.0
        coma.rect_y_mm = 2.0
        coma.rect_width_mm = 8.0
        coma.rect_height_mm = 5.0
        coma.background_color = (1.0, 1.0, 1.0, 1.0)
    work.active_page_index = 0
    return work


def _assert_range_operator(work, out_dir: Path, io_op) -> None:
    assert bpy.types.BNAME_OT_export_all_pages.bl_label == "指定範囲を書き出し"
    assert io_op._scaled_dpi(work, 12.5) == 12, "12.5% のDPI換算が不正です"
    op_rna = bpy.ops.bname.export_all_pages.get_rna_type()
    assert op_rna.properties["flat_scale_percent"].default == 100.0
    assert "TIFF" in {item.name for item in op_rna.properties["flat_format"].enum_items}
    assert io_op._resolve_filename("{pageStart}-{pageEnd}", work, work.pages[0], 3, page_end=4) == "0003-0004"
    result = bpy.ops.bname.export_all_pages(
        filepath=str(out_dir),
        output_start=2,
        output_end=3,
        split_spreads=False,
        output_mode="flat",
        flat_format="png",
        flat_scale_percent=50.0,
        color_mode="rgb",
        area="canvas",
        filename_template="page_{page}",
    )
    assert result == {"FINISHED"}, f"指定範囲の書き出しに失敗しました: {result}"
    names = sorted(path.name for path in out_dir.glob("*.png"))
    assert names == ["page_0002.png", "page_0003.png"], f"指定範囲以外が出力されています: {names}"

    tiff_dir = out_dir.parent / "range_tiff"
    result = bpy.ops.bname.export_all_pages(
        filepath=str(tiff_dir),
        output_start=2,
        output_end=2,
        split_spreads=False,
        output_mode="flat",
        flat_format="tiff",
        flat_scale_percent=25.0,
        color_mode="rgb",
        area="canvas",
        filename_template="page_{page}",
        include_border=False,
        include_white_margin=False,
        include_nombre=False,
        include_work_info=False,
        include_tombo=False,
        include_paper_color=True,
    )
    assert result == {"FINISHED"}, f"TIFF の指定範囲書き出しに失敗しました: {result}"
    assert [path.name for path in tiff_dir.glob("*.tiff")] == ["page_0002.tiff"]


def _assert_pdf_operator(work) -> None:
    exports_dir = Path(work.work_dir) / "exports"
    before = {path for path in exports_dir.glob("**/*.pdf")} if exports_dir.exists() else set()
    result = bpy.ops.bname.export_pdf(
        output_start=2,
        output_end=3,
        split_spreads=False,
        scale_percent=50.0,
        color_mode="rgb",
        area="canvas",
        include_border=False,
        include_white_margin=False,
        include_nombre=False,
        include_work_info=False,
        include_tombo=False,
        include_paper_color=True,
    )
    assert result == {"FINISHED"}, f"PDF の範囲書き出しに失敗しました: {result}"
    after = {path for path in exports_dir.glob("**/*.pdf")}
    new_files = after - before
    assert len(new_files) == 1 and next(iter(new_files)).is_file(), f"PDF が生成されていません: {new_files}"


def _assert_scale_render(work, export_pipeline, io_op) -> None:
    page = work.pages[0]
    full = export_pipeline.render_page(
        work,
        page,
        export_pipeline.ExportOptions(area="canvas", dpi_override=io_op._scaled_dpi(work, 100.0)),
    )
    half = export_pipeline.render_page(
        work,
        page,
        export_pipeline.ExportOptions(area="canvas", dpi_override=io_op._scaled_dpi(work, 50.0)),
    )
    assert full is not None and half is not None
    assert half.size[0] < full.size[0] and half.size[1] < full.size[1], (
        f"出力サイズ%で画像サイズが変わっていません: full={full.size}, half={half.size}"
    )


def _assert_latest_coma_export(work, export_pipeline) -> None:
    page = work.pages[0]
    coma = page.comas[0]
    coma.background_color = (0.0, 0.0, 0.0, 1.0)
    coma.border.style = "brush"
    coma.border.width_mm = 2.0
    coma.border.blur_amount = 1.0
    coma.border.blur_dither = False
    assert export_pipeline._draw_coma_border_layer(coma, 1000, 100) is None, (
        "ボカシブラシが別の枠線として書き出されています"
    )
    soft = export_pipeline._draw_coma_background_layer(coma, 1000, 100, include_brush_edge=True)
    hard = export_pipeline._draw_coma_background_layer(coma, 1000, 100, include_brush_edge=False)
    assert soft is not None and hard is not None
    sx = soft.image.width // 2
    sy = soft.image.height // 2
    edge_alpha = soft.image.getpixel((0, sy))[3]
    center_alpha = soft.image.getpixel((sx, sy))[3]
    hard_center_alpha = hard.image.getpixel((hard.image.width // 2, hard.image.height // 2))[3]
    max_alpha = max(pixel[3] for pixel in soft.image.getdata())
    assert max_alpha > 80 and edge_alpha < max_alpha, (
        f"ボカシブラシの内側フェードが出ていません: edge={edge_alpha}, center={center_alpha}"
    )
    assert hard_center_alpha > 240, "枠線を出力しない時のコマ面が不必要にボケています"

    coma.border.style = "solid"
    coma.border.corner_type = "rounded"
    coma.border.corner_radius_mm = 1.5
    rounded = export_pipeline._coma_polygon_mm(coma)
    assert len(rounded) > 4, "ページ出力のコマ形状が丸角に追従していません"


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="bname_page_export_"))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _load_addon()
        from bname_dev_page_export.io import export_pipeline
        from bname_dev_page_export.operators import io_op

        work = _setup_work(tmp_dir)
        out_dir = tmp_dir / "range_out"
        _assert_range_operator(work, out_dir, io_op)
        _assert_pdf_operator(work)
        _assert_scale_render(work, export_pipeline, io_op)
        _assert_latest_coma_export(work, export_pipeline)
        assert bpy.types.BNAME_OT_export_pdf.bl_label == "PDF 結合書き出し"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("BNAME_PAGE_EXPORT_RANGE_SCALE_OK")


main()
