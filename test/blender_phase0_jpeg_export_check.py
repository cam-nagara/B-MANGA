"""Phase 0: 製品のページ書き出しOperatorでJPEG基準成果物を作る。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get(
        "BMANGA_PHASE0_JPEG_OUT",
        str(
            ROOT
            / "_verify"
            / "2026-07-28_full_refactor_phase0"
            / "visual_probe"
            / "product_jpeg"
        ),
    )
)
MODULE_NAME = "bmanga_phase0_jpeg_export"
RUNS = 10
REQUESTED_DPI = 72


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("B-MANGAを読み込めません")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _add_page_content() -> None:
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    balloon = page.balloons.add()
    balloon.id = "phase0_jpeg_balloon"
    balloon.shape = "cloud"
    balloon.x_mm = 52.0
    balloon.y_mm = 72.0
    balloon.width_mm = 64.0
    balloon.height_mm = 44.0
    balloon.fill_color = (0.2, 0.45, 0.8, 1.0)
    balloon.line_width_mm = 1.2
    text = page.texts.add()
    text.id = "phase0_jpeg_text"
    text.body = "JPEG製品経路\n基準画像"
    text.x_mm = 64.0
    text.y_mm = 82.0
    text.width_mm = 36.0
    text.height_mm = 24.0


def _latest_export(work_root: Path, suffix: str) -> Path:
    candidates = list((work_root / "exports").rglob(f"*{suffix}"))
    if not candidates:
        raise AssertionError(f"製品書き出し成果物がありません: {suffix}")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _export_page(work_root: Path, image_format: str) -> Path:
    result = bpy.ops.bmanga.export_page(
        "EXEC_DEFAULT",
        format=image_format,
        color_mode="rgb",
        area="canvas",
        dpi_override=REQUESTED_DPI,
        include_border=True,
        include_white_margin=True,
        include_nombre=True,
        include_work_info=True,
        include_tombo=False,
        include_paper_color=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"{image_format}製品書き出し失敗: {result}")
    return _latest_export(work_root, ".jpg" if image_format == "jpeg" else ".png")


def _reader_record(path: Path) -> dict[str, object]:
    from PIL import Image, JpegImagePlugin

    with Image.open(path) as image:
        image.load()
        dpi = image.info.get("dpi")
        icc = image.info.get("icc_profile")
        sampling = JpegImagePlugin.get_sampling(image) if image.format == "JPEG" else None
        return {
            "file": path.name,
            "format": image.format,
            "mode": image.mode,
            "size": list(image.size),
            "dpi": list(dpi) if dpi else None,
            "icc_present": bool(icc),
            "icc_sha256": hashlib.sha256(icc).hexdigest() if icc else "",
            "jpeg_sampling": sampling,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }


def _create_outputs(work_root: Path) -> tuple[Path, list[Path]]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = _export_page(work_root, "png")
    source_copy = OUT_DIR / "product_source.png"
    shutil.copy2(source, source_copy)
    outputs: list[Path] = []
    for run in range(RUNS):
        exported = _export_page(work_root, "jpeg")
        target = OUT_DIR / f"product_page_q95_{run:02d}.jpg"
        shutil.copy2(exported, target)
        outputs.append(target)
    return source_copy, outputs


def _payload(source: Path, outputs: list[Path]) -> dict[str, object]:
    records = [_reader_record(path) for path in outputs]
    sizes = {tuple(record["size"]) for record in records}
    modes = {str(record["mode"]) for record in records}
    if len(sizes) != 1 or modes != {"RGB"}:
        raise AssertionError(f"JPEG reader contract不一致: sizes={sizes}, modes={modes}")
    return {
        "schema_version": 1,
        "producer": "bpy.ops.bmanga.export_page",
        "requested_dpi": REQUESTED_DPI,
        "quality": 95,
        "source_png": _reader_record(source),
        "jpeg_outputs": records,
        "dpi_contract_status": (
            "present"
            if all(record["dpi"] is not None for record in records)
            else "missing_in_current_product_output"
        ),
        "icc_contract_status": (
            "present"
            if all(record["icc_present"] for record in records)
            else "missing_in_current_product_output"
        ),
    }


def main() -> None:
    module = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase0_jpeg_"))
    try:
        module = _load_addon()
        work_root = temp_root / "Phase0JPEG.bmanga"
        if bpy.ops.bmanga.work_new(filepath=str(work_root)) != {"FINISHED"}:
            raise RuntimeError("JPEG基準作品を作成できません")
        _add_page_content()
        source, outputs = _create_outputs(work_root)
        metadata = OUT_DIR / "product_jpeg_metadata.json"
        metadata.write_text(
            json.dumps(_payload(source, outputs), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"BMANGA_PHASE0_JPEG_EXPORT_OK {metadata}", flush=True)
    finally:
        if module is not None:
            module.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
