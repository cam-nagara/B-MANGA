"""Blender実機: ページプレビューのテキストが実体より太らないことを検証する。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_page_preview_text_weight"
OUT_DIR = ROOT / "_verify" / "2026-07-17_page_preview_text_weight"
IMAGE_PATH = OUT_DIR / "actual_and_page_preview.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(path: str):
    __import__(f"{MODULE_NAME}.{path}")
    return sys.modules[f"{MODULE_NAME}.{path}"]


def _alpha_mass(image, dpi: int) -> float:
    alpha = image.convert("RGBA").getchannel("A")
    weighted_pixels = sum(value * count for value, count in enumerate(alpha.histogram()))
    return weighted_pixels / 255.0 / float(dpi * dpi)


def _assert_resolution_independent_weight(entry, canvas_height_mm: float) -> dict[str, dict[int, float]]:
    from PIL import Image

    export_pipeline = _sub("io.export_pipeline")
    text_real_object = _sub("utils.text_real_object")
    results: dict[str, dict[int, float]] = {}
    for writing_mode in ("horizontal", "vertical"):
        for bold in (False, True):
            entry.writing_mode = writing_mode
            entry.font_bold = bold
            entry.width_mm = 95.0 if writing_mode == "horizontal" else 44.0
            entry.height_mm = 44.0 if writing_mode == "horizontal" else 95.0
            masses: dict[int, float] = {}
            layers = {}
            for dpi in (300, 150, 75):
                canvas_height_px = int(round(canvas_height_mm / 25.4 * dpi))
                layer = export_pipeline._render_text_layer(entry, canvas_height_px, dpi)
                assert layer is not None
                layers[dpi] = layer
                masses[dpi] = _alpha_mass(layer.image, dpi)
            actual = text_real_object._render_entry_to_pillow(entry)
            assert actual is not None
            actual_mass = _alpha_mass(actual[0], 300)
            assert abs(actual_mass / masses[300] - 1.0) < 0.02, (
                writing_mode,
                bold,
                actual_mass,
                masses[300],
            )
            for dpi in (150, 75):
                ratio = masses[dpi] / masses[300]
                assert 0.80 <= ratio <= 1.20, (
                    writing_mode,
                    bold,
                    dpi,
                    ratio,
                    masses,
                )
            results[f"{writing_mode}_{'bold' if bold else 'normal'}"] = masses

            # 同じ物理寸法で比較した時にも、低解像度側だけ字面が膨らまない。
            high = layers[300].image
            resized = high.resize(
                (max(1, high.width // 2), max(1, high.height // 2)),
                getattr(getattr(Image, "Resampling", Image), "LANCZOS"),
            )
            high_ratio = _alpha_mass(resized, 150) / masses[300]
            assert 0.80 <= high_ratio <= 1.20, (writing_mode, bold, high_ratio)
    return results


def _make_linked_balloon_text(page) -> object:
    balloon = page.balloons.add()
    balloon.id = "preview_weight_balloon"
    balloon.title = "プレビュー太さ確認"
    balloon.shape = "ellipse"
    balloon.x_mm = 42.0
    balloon.y_mm = 72.0
    balloon.width_mm = 86.0
    balloon.height_mm = 122.0

    entry = page.texts.add()
    entry.id = "preview_weight_text"
    entry.title = "フキダシ内テキスト"
    entry.body = "太さ比較本文文字"
    entry.x_mm = 63.0
    entry.y_mm = 86.0
    entry.width_mm = 44.0
    entry.height_mm = 95.0
    entry.writing_mode = "vertical"
    entry.font = r"C:\Windows\Fonts\meiryo.ttc"
    entry.font_size_q = 20.0
    entry.font_bold = False
    entry.stroke_enabled = False
    entry.parent_balloon_id = balloon.id
    balloon.text_id = entry.id
    return entry


def _write_visual(work, page, scene) -> None:
    from PIL import Image, ImageDraw

    export_pipeline = _sub("io.export_pipeline")
    page_preview_object = _sub("utils.page_preview_object")
    actual = export_pipeline.render_page(
        work,
        page,
        export_pipeline.ExportOptions(
            area="canvas",
            dpi_override=300,
            include_border=True,
            include_white_margin=True,
            include_nombre=False,
            include_work_info=False,
            include_tombo=False,
            include_paper_color=True,
            include_coma_previews=True,
        ),
    ).convert("RGBA")
    guides_visible = bool(getattr(scene, "bmanga_page_guides_visible", True))
    scene.bmanga_page_guides_visible = False
    try:
        preview = page_preview_object._render_preview_image(
            work,
            page,
            0,
            current=False,
            scene=scene,
        ).convert("RGBA")
    finally:
        scene.bmanga_page_guides_visible = guides_visible
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    actual = actual.resize(preview.size, resampling)
    margin = 28
    header = 42
    sheet = Image.new(
        "RGBA",
        (preview.width * 2 + margin * 3, preview.height + header + margin * 2),
        (38, 38, 42, 255),
    )
    sheet.paste(actual, (margin, header + margin))
    sheet.paste(preview, (preview.width + margin * 2, header + margin))
    draw = ImageDraw.Draw(sheet)
    draw.text((margin, 14), "Actual 300dpi resized", fill=(255, 255, 255, 255))
    draw.text((preview.width + margin * 2, 14), "Page preview", fill=(255, 255, 255, 255))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet.save(IMAGE_PATH)


def _assert_preview_cache_version(work, page, scene) -> None:
    from PIL import Image

    page_preview_object = _sub("utils.page_preview_object")
    path = page_preview_object.ensure_preview_png(
        work,
        page,
        0,
        current=False,
        scene=scene,
        force=True,
    )
    assert path is not None and path.is_file()
    with Image.open(path) as image:
        version = str(image.info.get(page_preview_object.PREVIEW_RENDER_VERSION_KEY, ""))
    assert version == "11", version


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_preview_text_weight_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    addon = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon = _load_addon()
        assert bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "PreviewTextWeight.bmanga")
        ) == {"FINISHED"}
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}
        context = bpy.context
        work = _sub("core.work").get_work(context)
        page = work.pages[0]
        work.paper.dpi = 600
        context.scene.bmanga_page_preview_resolution_percentage = 25.0
        entry = _make_linked_balloon_text(page)
        _sub("utils.layer_stack").sync_layer_stack_after_data_change(context)
        masses = _assert_resolution_independent_weight(
            entry,
            float(work.paper.canvas_height_mm),
        )
        entry.writing_mode = "vertical"
        entry.font_bold = False
        entry.width_mm = 44.0
        entry.height_mm = 95.0
        _write_visual(work, page, context.scene)
        _assert_preview_cache_version(work, page, context.scene)
        print("BMANGA_PAGE_PREVIEW_TEXT_WEIGHT_OK", masses, IMAGE_PATH)
    finally:
        if addon is not None:
            addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
