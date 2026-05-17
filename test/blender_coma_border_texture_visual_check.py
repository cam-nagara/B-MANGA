"""Blender 実機(背景)用: ボカシブラシ透明画像の目視確認PNGを生成."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".codex" / "visual_checks"
OUT_PATH = OUT_DIR / "coma_border_texture_alpha.png"
RENDER_PATH = OUT_DIR / "coma_border_texture_preview_render.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_border_texture_visual", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_border_texture_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_preview_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = 360
    height = 240
    image = bpy.data.images.new("BName_TestPreview_Texture", width=width, height=height, alpha=True)
    pixels = [0.0] * (width * height * 4)
    for y in range(height):
        for x in range(width):
            u = x / max(1, width - 1)
            v = y / max(1, height - 1)
            band = 0.35 if (x // 24) % 2 == 0 else 0.0
            off = (y * width + x) * 4
            pixels[off] = 0.1 + 0.8 * u
            pixels[off + 1] = 0.25 + 0.55 * v
            pixels[off + 2] = 0.75 - band
            pixels[off + 3] = 1.0
    image.pixels.foreach_set(pixels)
    image.update()
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()


def _save_composited_preview(source: bpy.types.Image, alpha: bpy.types.Image, path: Path) -> None:
    width = int(alpha.size[0])
    height = int(alpha.size[1])
    source_w = int(source.size[0])
    source_h = int(source.size[1])
    source_pixels = list(source.pixels[:])
    alpha_pixels = list(alpha.pixels[:])
    out = bpy.data.images.new("BName_TestPreview_Composited", width=width, height=height, alpha=True)
    pixels = [0.0] * (width * height * 4)
    bg = (0.88, 0.88, 0.88)
    for y in range(height):
        sy = int((y / max(1, height - 1)) * max(0, source_h - 1))
        for x in range(width):
            sx = int((x / max(1, width - 1)) * max(0, source_w - 1))
            src_off = (sy * source_w + sx) * 4
            dst_off = (y * width + x) * 4
            alpha_value = float(alpha_pixels[dst_off + 3])
            pixels[dst_off] = source_pixels[src_off] * alpha_value + bg[0] * (1.0 - alpha_value)
            pixels[dst_off + 1] = source_pixels[src_off + 1] * alpha_value + bg[1] * (1.0 - alpha_value)
            pixels[dst_off + 2] = source_pixels[src_off + 2] * alpha_value + bg[2] * (1.0 - alpha_value)
            pixels[dst_off + 3] = 1.0
    out.pixels.foreach_set(pixels)
    out.update()
    out.filepath_raw = str(path)
    out.file_format = "PNG"
    out.save()


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    from bname_dev_border_texture_visual.utils import coma_border_object, coma_border_texture, coma_plane, paths

    scene = bpy.context.scene
    work = scene.bname_work
    work.loaded = True
    work_dir = Path(tempfile.mkdtemp(prefix="bname_border_texture_visual_")) / "Visual.bname"
    work.work_dir = str(work_dir)
    page = work.pages.add()
    page.id = "p0001"
    page.title = "1ページ"
    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.title = "コマ1"
    coma.rect_width_mm = 120.0
    coma.rect_height_mm = 80.0
    coma.border.style = "brush"
    coma.border.width_mm = 3.0
    coma.border.blur_amount = 1.0
    coma.border.blur_dither = False
    coma.border.color = (0.0, 0.0, 0.0, 1.0)
    _write_preview_image(paths.coma_preview_path(work_dir, page.id, coma.coma_id))

    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    plane = coma_plane.find_coma_plane_object(page.id, coma.id)
    assert obj is plane and plane is not None, "ボカシブラシがコマ面に適用されません"
    plane.hide_render = False
    image = bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id))
    assert image is not None, "コマ面の透明マスク画像が生成されません"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image.filepath_raw = str(OUT_PATH)
    image.file_format = "PNG"
    image.save()
    source = bpy.data.images.get("BName_TestPreview_Texture")
    assert source is not None, "プレビュー画像が読み込まれていません"
    _save_composited_preview(source, image, RENDER_PATH)
    print(f"BNAME_COMA_BORDER_TEXTURE_VISUAL_CHECK_OK alpha={OUT_PATH} render={RENDER_PATH}")


main()
