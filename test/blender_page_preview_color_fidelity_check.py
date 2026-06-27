"""Blender background check: page preview PNG uses display-equivalent colors."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_page_preview_color"


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


def _px_for_mm(image, work, x_mm: float, y_mm: float) -> tuple[int, int]:
    width, height = image.size
    cw = max(1.0, float(work.paper.canvas_width_mm))
    ch = max(1.0, float(work.paper.canvas_height_mm))
    x = int(round(x_mm / cw * (width - 1)))
    y = int(round((1.0 - y_mm / ch) * (height - 1)))
    return max(0, min(width - 1, x)), max(0, min(height - 1, y))


def _assert_rgb_close(actual, expected, label: str, tolerance: int = 4) -> None:
    delta = tuple(abs(int(actual[i]) - int(expected[i])) for i in range(3))
    if any(value > tolerance for value in delta):
        raise AssertionError(f"{label} の色が画面表示相当ではありません: actual={actual}, expected={expected}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_preview_color_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PreviewColor.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bmanga_dev_page_preview_color.io import export_pipeline
        from bmanga_dev_page_preview_color.utils import color_space, page_preview_object
        from PIL import Image

        scene = bpy.context.scene
        work = scene.bmanga_work
        page = work.pages[0]

        paper_srgb = (0.70, 0.70, 0.70)
        coma_srgb = (0.50, 0.50, 0.50)
        paper_linear = color_space.srgb_to_linear_rgb(paper_srgb)
        coma_linear = color_space.srgb_to_linear_rgb(coma_srgb)

        if export_pipeline._rgb255((*coma_linear, 1.0))[:3] != (128, 128, 128):
            raise AssertionError("書き出し用の色変換が 50% グレーを暗く変換しています")

        work.paper.paper_color = (*paper_linear, 1.0)
        for index, coma in enumerate(page.comas):
            coma.visible = index == 0
        coma = page.comas[0]
        coma.rect_x_mm = 80.0
        coma.rect_y_mm = 120.0
        coma.rect_width_mm = 40.0
        coma.rect_height_mm = 40.0
        coma.background_color = (*coma_linear, 1.0)
        coma.border.visible = False
        coma.white_margin.enabled = False

        scene.bmanga_page_preview_resolution_percentage = 25.0
        path = page_preview_object.ensure_preview_png(work, page, 0, current=False, scene=scene, force=True)
        if path is None or not Path(path).is_file():
            raise AssertionError(f"ページプレビュー画像が生成されませんでした: {path}")

        with Image.open(path) as loaded:
            image = loaded.convert("RGBA")
            paper_px = image.getpixel(_px_for_mm(image, work, 12.0, float(work.paper.canvas_height_mm) - 12.0))
            coma_px = image.getpixel(_px_for_mm(image, work, 100.0, 140.0))

        _assert_rgb_close(paper_px, (178, 178, 178), "用紙色")
        _assert_rgb_close(coma_px, (128, 128, 128), "コマ背景色")
        print(
            "BMANGA_PAGE_PREVIEW_COLOR_FIDELITY_OK",
            f"paper={paper_px}",
            f"coma={coma_px}",
            flush=True,
        )
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
