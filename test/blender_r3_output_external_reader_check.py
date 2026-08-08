"""Blender実機: 本体UIの全出力形式を独立readerで再読込する。"""

from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "test"
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))
OUT_DIR = Path(
    os.environ.get("BMANGA_R3_OUTPUT_OUT", "")
    or ROOT / "_verify" / "2026-08-08_r3_output_external_reader"
)
MODULE_NAME = "bmanga_dev_r3_output"
DPI = 144


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _new_page(work, page_id: str, *, spread: bool):
    page = work.pages.add()
    page.id = page_id
    page.title = page_id
    page.spread = spread
    page.in_page_range = True
    page.detail_loaded = True
    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.title = "R3コマ"
    coma.shape_type = "rect"
    coma.rect_x_mm = 5.0
    coma.rect_y_mm = 3.0
    coma.rect_width_mm = 12.0
    coma.rect_height_mm = 12.0
    coma.background_color = (1.0, 1.0, 1.0, 1.0)
    return page


def _setup_work(temp_root: Path):
    from bmanga_dev_r3_output.io import page_io, work_io

    scene = bpy.context.scene
    scene.bmanga_mode = "PAGE"
    work = scene.bmanga_work
    work.loaded = False
    work.work_dir = str(temp_root / "R3Output.bmanga")
    work.work_info.work_name = "R3Output"
    work.work_info.episode_number = 1
    work.work_info.page_number_start = 1
    work.work_info.page_number_end = 3
    work.paper.canvas_width_mm = 40.0
    work.paper.canvas_height_mm = 20.0
    work.paper.finish_width_mm = 40.0
    work.paper.finish_height_mm = 20.0
    work.paper.dpi = DPI
    work.pages.clear()
    spread = _new_page(work, "p0001-0002", spread=True)
    normal = _new_page(work, "p0003", spread=False)
    work.active_page_index = 0
    work.safe_area_overlay.enabled = True
    work.safe_area_overlay.opacity = 50.0
    work.safe_area_overlay.bleed_outer_enabled = True
    work.safe_area_overlay.bleed_outer_opacity = 100.0
    work_dir = Path(work.work_dir)
    work_io.create_bmanga_skeleton(work_dir)
    work_io.save_work_json(work_dir, work)
    for page in work.pages:
        page_io.save_page_json(work_dir, page)
    work.loaded = True
    return work, spread, normal


def _add_masked_image(temp_root: Path, work, page) -> None:
    from bmanga_dev_r3_output.io import export_pipeline
    from bmanga_dev_r3_output.utils import image_real_object
    from bmanga_dev_r3_output.utils.layer_hierarchy import coma_stack_key

    source = temp_root / "masked_source.png"
    export_pipeline.Image.new("RGBA", (240, 120), (230, 32, 48, 255)).save(source)
    entry = bpy.context.scene.bmanga_image_layers.add()
    entry.id = "r3_masked_image"
    entry.title = "R3マスク画像"
    entry.filepath = str(source)
    entry.x_mm = -4.0
    entry.y_mm = 0.0
    entry.width_mm = 32.0
    entry.height_mm = 18.0
    entry.opacity = 100.0
    entry.parent_kind = "coma"
    entry.parent_key = coma_stack_key(page, page.comas[0])
    image_real_object.ensure_image_real_object(
        scene=bpy.context.scene,
        entry=entry,
        page=page,
    )
    assert work.loaded


def _new_file(before: set[Path], root: Path, suffix: str) -> Path:
    after = {path for path in root.rglob(f"*{suffix}") if path.is_file()}
    created = after - before
    assert len(created) == 1, (suffix, sorted(str(path) for path in created))
    return next(iter(created))


def _single_outputs(work) -> dict[str, Path]:
    exports = Path(work.work_dir) / "exports"
    results = {}
    for fmt, suffix in (("png", ".png"), ("jpeg", ".jpg"), ("tiff", ".tiff"), ("psd", ".psd")):
        before = {path for path in exports.rglob(f"*{suffix}")} if exports.exists() else set()
        result = bpy.ops.bmanga.export_page(
            "EXEC_DEFAULT",
            format=fmt,
            color_mode="rgb",
            area="canvas",
            dpi_override=DPI,
            include_border=True,
            include_white_margin=True,
            include_nombre=False,
            include_work_info=False,
            include_tombo=False,
            include_paper_color=True,
        )
        assert result == {"FINISHED"}, (fmt, result)
        source = _new_file(before, exports, suffix)
        target = OUT_DIR / f"single.{suffix.lstrip('.')}"
        shutil.copy2(source, target)
        results[fmt] = target
    return results


def _multi_output(work, *, fmt: str, layered: bool, split: bool) -> list[Path]:
    label = f"multi_{fmt}_{'split' if split else 'whole'}"
    out = OUT_DIR / label
    out.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.bmanga.export_all_pages(
        "EXEC_DEFAULT",
        filepath=str(out),
        output_start=1,
        output_end=3,
        split_spreads=split,
        output_mode="layered" if layered else "flat",
        flat_format=fmt if not layered else "png",
        flat_scale_percent=100.0,
        color_mode="rgb",
        area="canvas",
        filename_template="r3_{page}",
        include_border=True,
        include_white_margin=True,
        include_nombre=False,
        include_work_info=False,
        include_tombo=False,
        include_paper_color=True,
    )
    assert result == {"FINISHED"}, (label, result)
    suffix = ".psd" if layered else (".jpg" if fmt == "jpeg" else f".{fmt}")
    paths = sorted(out.glob(f"*{suffix}"))
    assert len(paths) == (3 if split else 2), (label, [path.name for path in paths])
    return paths


def _pdf_output(work, *, split: bool) -> Path:
    exports = Path(work.work_dir) / "exports"
    time.sleep(1.05)
    before = {path for path in exports.rglob("*.pdf")} if exports.exists() else set()
    result = bpy.ops.bmanga.export_pdf(
        "EXEC_DEFAULT",
        output_start=1,
        output_end=3,
        split_spreads=split,
        scale_percent=100.0,
        color_mode="rgb",
        area="canvas",
        include_border=True,
        include_white_margin=True,
        include_nombre=False,
        include_work_info=False,
        include_tombo=False,
        include_paper_color=True,
    )
    assert result == {"FINISHED"}, result
    source = _new_file(before, exports, ".pdf")
    target = OUT_DIR / f"pdf_{'split' if split else 'whole'}.pdf"
    shutil.copy2(source, target)
    return target


def _assert_dpi(record: dict[str, object], label: str) -> None:
    dpi = record.get("dpi")
    assert dpi is not None, f"{label}: DPI metadataがありません"
    assert all(abs(float(value) - DPI) <= 0.2 for value in dpi), (label, dpi)


def _read_and_assert(single, multi, pdfs) -> dict[str, object]:
    from bmanga_dev_r3_output.io import export_pipeline
    from output_external_reader import read_flat_image, read_pdf, read_psd

    flat = {}
    for fmt in ("png", "jpeg", "tiff"):
        record = read_flat_image(single[fmt], export_pipeline.Image)
        expected = {"png": "PNG", "jpeg": "JPEG", "tiff": "TIFF"}[fmt]
        assert record["format"] == expected, record
        _assert_dpi(record, f"single {fmt}")
        assert any(lo != hi for lo, hi in record["extrema"]), record
        flat[fmt] = record
    assert len({tuple(record["size"]) for record in flat.values()}) == 1, flat

    psd = read_psd(single["psd"])
    assert psd["size"] == flat["png"]["size"], (psd, flat["png"])
    assert psd["dpi"] is not None and abs(float(psd["dpi"]) - DPI) <= 0.01, psd
    names = [row["name"] for row in psd["layers"]]
    masked_name = next((name for name in names if name.endswith("/R3マスク画像")), None)
    paper_name = next((name for name in names if name.endswith("/paper")), None)
    assert masked_name is not None and paper_name is not None, names
    assert names.index(masked_name) < names.index(paper_name), names
    masked = next(row for row in psd["layers"] if row["name"] == masked_name)
    assert masked["alpha_zero"] > 0 and masked["alpha_nonzero"] > 0, masked

    multi_records = {}
    for label, paths in multi.items():
        if label.startswith("psd"):
            rows = [read_psd(path) for path in paths]
            for row in rows:
                assert row["dpi"] is not None and abs(float(row["dpi"]) - DPI) <= 0.01, row
        else:
            rows = [read_flat_image(path, export_pipeline.Image) for path in paths]
            for row in rows:
                _assert_dpi(row, label)
        multi_records[label] = rows
    for label in ("png_split", "psd_split"):
        widths = [int(row["size"][0]) for row in multi_records[label]]
        assert len(widths) == 3 and max(widths) - min(widths) <= 1, (label, widths)

    pdf_records = {label: read_pdf(path) for label, path in pdfs.items()}
    assert pdf_records["whole"]["page_count"] == 2, pdf_records["whole"]
    assert pdf_records["split"]["page_count"] == 3, pdf_records["split"]
    for label, record in pdf_records.items():
        assert len(record["media_boxes"]) >= record["page_count"], (label, record)
        assert "DeviceRGB" in record["color_spaces"], (label, record)
    whole_widths = [box[0] for box in pdf_records["whole"]["media_boxes"][:2]]
    split_widths = [box[0] for box in pdf_records["split"]["media_boxes"][:3]]
    assert max(whole_widths) > min(whole_widths) * 1.5, whole_widths
    assert max(split_widths) - min(split_widths) <= 1.0, split_widths
    expected_height_pt = 20.0 / 25.4 * 72.0
    for record in pdf_records.values():
        for _width, height in record["media_boxes"][:record["page_count"]]:
            assert math.isclose(height, expected_height_pt, abs_tol=0.6), (height, expected_height_pt)
    return {"single": {**flat, "psd": psd}, "multi": multi_records, "pdf": pdf_records}


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_r3_output_"))
    module = None
    try:
        if OUT_DIR.exists():
            shutil.rmtree(OUT_DIR)
        OUT_DIR.mkdir(parents=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        work, spread, _normal = _setup_work(temp_root)
        _add_masked_image(temp_root, work, spread)
        single = _single_outputs(work)
        multi = {
            "png_whole": _multi_output(work, fmt="png", layered=False, split=False),
            "jpeg_whole": _multi_output(work, fmt="jpeg", layered=False, split=False),
            "tiff_whole": _multi_output(work, fmt="tiff", layered=False, split=False),
            "psd_whole": _multi_output(work, fmt="psd", layered=True, split=False),
            "png_split": _multi_output(work, fmt="png", layered=False, split=True),
            "psd_split": _multi_output(work, fmt="psd", layered=True, split=True),
        }
        pdfs = {"whole": _pdf_output(work, split=False), "split": _pdf_output(work, split=True)}
        report = _read_and_assert(single, multi, pdfs)
        (OUT_DIR / "reader_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("BMANGA_R3_OUTPUT_EXTERNAL_READER_OK", flush=True)
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
