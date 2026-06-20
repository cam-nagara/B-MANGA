"""Blender 実機用: セーフライン外/裁ち落とし枠外のPSDレイヤー出力確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import time
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_overlay_fill_psd",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_overlay_fill_psd"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _layer_by_name(layers, name: str):
    matches = [layer for layer in layers if layer.name == name]
    if len(matches) != 1:
        raise AssertionError(f"{name} layer count mismatch: {len(matches)}")
    return matches[0]


def _pixel_at(layer, x_mm: float, y_mm: float, dpi: int) -> tuple[int, int, int, int]:
    from bmanga_dev_overlay_fill_psd.utils.geom import mm_to_px

    image = layer.image
    x = int(round(mm_to_px(x_mm, dpi))) - int(layer.left)
    y = image.height - int(round(mm_to_px(y_mm, dpi))) - int(layer.top) - 1
    x = max(0, min(image.width - 1, x))
    y = max(0, min(image.height - 1, y))
    return tuple(int(v) for v in image.getpixel((x, y)))


def _assert_fill_sample(layer, point: tuple[float, float], dpi: int, expected: tuple[int, int, int, int]) -> None:
    actual = _pixel_at(layer, point[0], point[1], dpi)
    if actual != expected:
        raise AssertionError(f"{layer.name} pixel mismatch at {point}: {actual} != {expected}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_overlay_fill_psd_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "OverlayFillPSD.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bmanga_dev_overlay_fill_psd.core.work import get_work
        from bmanga_dev_overlay_fill_psd.io import export_pipeline
        from bmanga_dev_overlay_fill_psd.io import work_io
        from bmanga_dev_overlay_fill_psd.ui import overlay_shared
        from bmanga_dev_overlay_fill_psd.utils import color_space
        from bmanga_dev_overlay_fill_psd.utils import page_preview_object
        from bmanga_dev_overlay_fill_psd.utils import paper_guide_object

        work = get_work(bpy.context)
        if work is None or not work.loaded:
            raise AssertionError("作品データが読み込まれていません")
        page = work.pages[0]
        dpi = 72
        work.safe_area_overlay.enabled = True
        work.safe_area_overlay.opacity = 50.0
        work.safe_area_overlay.color = color_space.srgb_to_linear_rgb((0.20, 0.60, 0.80))
        work.safe_area_overlay.bleed_outer_enabled = True
        work.safe_area_overlay.bleed_outer_opacity = 100.0
        work.safe_area_overlay.bleed_outer_color = color_space.srgb_to_linear_rgb(
            (0x40 / 255.0, 0x40 / 255.0, 0x40 / 255.0)
        )

        options = export_pipeline.ExportOptions(
            format="psd",
            area="canvas",
            dpi_override=dpi,
            include_border=False,
            include_white_margin=False,
            include_nombre=False,
            include_work_info=False,
            include_tombo=False,
            include_paper_color=False,
            include_coma_previews=False,
        )
        layers = export_pipeline.build_page_layers(work, page, options)
        names = [layer.name for layer in layers]
        if "セーフライン外の塗り" not in names:
            raise AssertionError(f"セーフライン外の塗りレイヤーがありません: {names}")
        if "裁ち落とし枠外の塗り" not in names:
            raise AssertionError(f"裁ち落とし枠外の塗りレイヤーがありません: {names}")
        if names.index("セーフライン外の塗り") > names.index("裁ち落とし枠外の塗り"):
            raise AssertionError("裁ち落とし枠外の塗りがセーフライン外の塗りより下にあります")
        safe_layer = _layer_by_name(layers, "セーフライン外の塗り")
        bleed_layer = _layer_by_name(layers, "裁ち落とし枠外の塗り")
        if safe_layer.image is bleed_layer.image:
            raise AssertionError("2つの塗りレイヤーが同じ画像を共有しています")

        rects = overlay_shared.compute_paper_rects(
            work.paper,
            is_left_half=export_pipeline._is_left_half_page(work, page),
        )
        x = rects.canvas.center[0]
        outside_bleed_top = (x, (rects.canvas.y2 + rects.bleed.y2) * 0.5)
        between_safe_and_bleed_top = (x, (rects.safe.y2 + rects.bleed.y2) * 0.5)
        inside_safe = rects.safe.center

        _assert_fill_sample(bleed_layer, outside_bleed_top, dpi, (64, 64, 64, 255))
        _assert_fill_sample(bleed_layer, between_safe_and_bleed_top, dpi, (0, 0, 0, 0))
        _assert_fill_sample(bleed_layer, inside_safe, dpi, (0, 0, 0, 0))
        _assert_fill_sample(safe_layer, between_safe_and_bleed_top, dpi, (51, 153, 204, 128))
        _assert_fill_sample(safe_layer, inside_safe, dpi, (0, 0, 0, 0))

        bleed_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page.id}")
        if bleed_obj is None:
            raise AssertionError("ページ上の裁ち落とし枠外塗りが作られていません")
        if bleed_obj.type != "MESH":
            raise AssertionError(f"裁ち落とし枠外塗りはメッシュである必要があります: {bleed_obj.type}")
        if bool(getattr(bleed_obj, "show_in_front", False)):
            raise AssertionError("裁ち落とし枠外塗りが最前面ワイヤ表示に依存しています")
        bleed_mat = bleed_obj.active_material
        if bleed_mat is None or not bleed_mat.name.startswith(paper_guide_object.PAPER_BLEED_OUTER_FILL_VIEW_MATERIAL):
            raise AssertionError("裁ち落とし枠外塗りの表示素材がありません")

        work.safe_area_overlay.bleed_outer_enabled = False
        names = [layer.name for layer in export_pipeline.build_page_layers(work, page, options)]
        if "裁ち落とし枠外の塗り" in names:
            raise AssertionError("裁ち落とし枠外の塗りをオフにしてもPSDレイヤーが出ています")
        if "セーフライン外の塗り" not in names:
            raise AssertionError("裁ち落とし枠外の塗りをオフにしただけでセーフライン外の塗りが消えました")

        png_names = [
            layer.name
            for layer in export_pipeline.build_page_layers(work, page, options.__class__(**{**options.__dict__, "format": "png"}))
        ]
        if "セーフライン外の塗り" in png_names or "裁ち落とし枠外の塗り" in png_names:
            raise AssertionError("PSD以外に塗りレイヤーが追加されています")

        work.safe_area_overlay.bleed_outer_enabled = True
        preview_path = page_preview_object.ensure_preview_png(
            work,
            page,
            0,
            current=False,
            scene=bpy.context.scene,
            force=True,
        )
        if preview_path is None or not Path(preview_path).is_file():
            raise AssertionError("ページ一覧プレビュー画像が作られていません")
        preview = export_pipeline.Image.open(str(preview_path)).convert("RGBA")

        def preview_pixel_at(point: tuple[float, float]) -> tuple[int, int, int, int]:
            x = int(round(point[0] / float(work.paper.canvas_width_mm) * preview.width))
            y = preview.height - int(round(point[1] / float(work.paper.canvas_height_mm) * preview.height)) - 1
            x = max(0, min(preview.width - 1, x))
            y = max(0, min(preview.height - 1, y))
            return tuple(int(v) for v in preview.getpixel((x, y)))

        safe_px = preview_pixel_at(between_safe_and_bleed_top)
        inner_px = preview_pixel_at(inside_safe)
        dark_bleed_pixels = 0
        for r, g, b, a in preview.getdata():
            if a == 255 and 45 <= r <= 90 and 45 <= g <= 90 and 45 <= b <= 90:
                dark_bleed_pixels += 1
        if dark_bleed_pixels <= 0:
            raise AssertionError(
                f"ページ一覧プレビューに裁ち落とし枠外の塗りが反映されていません: dark={dark_bleed_pixels}"
            )
        if not (safe_px[2] > safe_px[0] and safe_px[1] > safe_px[0] and safe_px[3] == 255):
            raise AssertionError(f"ページ一覧プレビューにセーフライン外の塗りが反映されていません: {safe_px}")
        if not (inner_px[0] > 220 and inner_px[1] > 220 and inner_px[2] > 220):
            raise AssertionError(f"ページ一覧プレビューのセーフライン内まで塗られています: {inner_px}")

        old_preview_mtime = Path(preview_path).stat().st_mtime
        time.sleep(1.1)
        work.safe_area_overlay.opacity = 55.0
        work_io.save_work_json(Path(work.work_dir), work)
        refreshed_preview_path = page_preview_object.ensure_preview_png(
            work,
            page,
            0,
            current=False,
            scene=bpy.context.scene,
            force=False,
        )
        refreshed_mtime = Path(refreshed_preview_path).stat().st_mtime if refreshed_preview_path else 0.0
        if refreshed_mtime <= old_preview_mtime:
            raise AssertionError("作品設定の更新後にページ一覧プレビュー画像が再生成されていません")

        work.safe_area_overlay.bleed_outer_enabled = True
        paper_guide_object.regenerate_all_paper_guides(bpy.context.scene, work)
        bleed_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page.id}")
        if bleed_obj is None:
            raise AssertionError("再表示後の裁ち落とし枠外塗りが作られていません")
        bleed_obj_name = bleed_obj.name
        reopen_path = temp_root / "overlay_fill_reopen.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(reopen_path))
        bpy.ops.wm.open_mainfile(filepath=str(reopen_path))
        work_after = get_work(bpy.context)
        if work_after is None or not work_after.loaded:
            raise AssertionError("再読み込み後の作品データが読み込まれていません")
        page_after = work_after.pages[0]
        reloaded_bleed_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page_after.id}")
        if reloaded_bleed_obj is None:
            paper_guide_object.repair_loaded_work_paper_guides(bpy.context.scene, work_after)
            reloaded_bleed_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page_after.id}")
        if reloaded_bleed_obj is None:
            raise AssertionError("ファイル再読み込み後に裁ち落とし枠外塗りが再生成されません")

        print("BMANGA_PAGE_OVERLAY_FILL_PSD_LAYERS_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
