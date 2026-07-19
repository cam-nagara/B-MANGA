"""書き出しプリセット・パネル・全形式書き出しの回帰テスト.

Usage:
    blender --background <work.blend> --python <this_file>

検証項目:
- 書き出しプリセット CRUD (save/list/rename/duplicate/move/delete)
- パネルラベル「書き出し」
- ページ書き出し: PNG / JPEG / TIFF / PSD (全カラーモード・全範囲)
- コマ書き出し: PNG / PSD
- 新オペレーター登録確認
"""

import os
import sys
import tempfile

# テスト用に export_presets の保存先を隔離
_tmpdir = tempfile.mkdtemp()
os.environ["BMANGA_USER_CONFIG_DIR"] = _tmpdir

import importlib
import importlib.util

import bpy

# アドオン登録（開発ディレクトリから直接ロード）
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "b_manga",
    os.path.join(_ADDON_DIR, "__init__.py"),
    submodule_search_locations=[_ADDON_DIR],
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["b_manga"] = _mod
_spec.loader.exec_module(_mod)
_mod.register()

from b_manga.core.work import get_active_page, get_work
from b_manga.io import export_pipeline, export_presets
from b_manga.io.export_pipeline import ExportOptions
from b_manga.utils import coma_content_mask, page_file_scene
from b_manga.utils.geom import mm_to_px

_errors: list[str] = []


def check(condition: bool, msg: str) -> None:
    if not condition:
        _errors.append(msg)
        print(f"  FAIL: {msg}")
    else:
        print(f"  OK: {msg}")


def test_panel_registration():
    print("\n=== Panel Registration ===")
    check(hasattr(bpy.types, "BMANGA_PT_export"), "BMANGA_PT_export exists")
    check(bpy.types.BMANGA_PT_export.bl_label == "書き出し", "Panel label is '書き出し'")


def test_operator_registration():
    print("\n=== Operator Registration ===")
    ops = [
        "BMANGA_OT_export_page",
        "BMANGA_OT_export_all_pages",
        "BMANGA_OT_export_pdf",
        "BMANGA_OT_export_current_page",
        "BMANGA_OT_export_current_coma",
        "BMANGA_OT_export_preset_add_local",
        "BMANGA_OT_export_preset_delete",
        "BMANGA_OT_export_preset_rename",
        "BMANGA_OT_export_preset_duplicate",
        "BMANGA_OT_export_preset_move",
        "BMANGA_OT_export_preset_save",
    ]
    for op in ops:
        check(hasattr(bpy.types, op), f"{op} registered")


def test_preset_properties():
    print("\n=== Preset Properties ===")
    wm = bpy.context.window_manager
    check(hasattr(wm, "bmanga_export_preset_selector"), "export_preset_selector")
    check(hasattr(wm, "bmanga_export_preset_list"), "export_preset_list")
    check(hasattr(wm, "bmanga_export_preset_list_index"), "export_preset_list_index")


def test_preset_crud():
    print("\n=== Preset CRUD ===")
    p = export_presets.save_preset("Test PNG", {"format": "png", "color_mode": "rgb", "area": "finish"})
    check(p is not None and p.path.exists(), "save_preset creates file")

    presets = export_presets.list_all_presets()
    check(len(presets) >= 1, f"list_all_presets returns {len(presets)}")

    r = export_presets.rename_preset("Test PNG", "Web PNG")
    check(r is not None and r.name == "Web PNG", "rename_preset")

    d = export_presets.duplicate_preset("Web PNG", "Web PNG Copy")
    check(d is not None, "duplicate_preset")

    export_presets.move_preset("Web PNG Copy", "UP")
    presets = export_presets.list_all_presets()
    check(presets[0].name == "Web PNG Copy", "move_preset UP")

    export_presets.delete_preset("Web PNG Copy")
    presets = export_presets.list_all_presets()
    check(not any(p.name == "Web PNG Copy" for p in presets), "delete_preset")

    export_presets.delete_preset("Web PNG")


def test_page_export_all_formats():
    print("\n=== Page Export All Formats ===")
    ctx = bpy.context
    work = get_work(ctx)
    if not work or not work.loaded:
        print("  SKIP: work not loaded")
        return
    if not export_pipeline.has_pillow():
        print("  SKIP: Pillow not available")
        return
    page = get_active_page(ctx)
    if page is None:
        print("  SKIP: no active page")
        return

    outdir = tempfile.mkdtemp()
    from pathlib import Path

    for fmt in ["png", "jpeg", "tiff"]:
        for cm in ["rgb", "grayscale", "monochrome"]:
            for area in ["finish", "withBleed", "innerFrame", "canvas"]:
                opt = ExportOptions(
                    color_mode=cm, format=fmt, area=area, dpi_override=72,
                    include_border=True, include_white_margin=True, include_nombre=True,
                    include_work_info=True, include_tombo=False, include_paper_color=True,
                )
                try:
                    img = export_pipeline.render_page(work, page, opt)
                    if fmt == "jpeg" and img and img.mode == "RGBA":
                        from PIL import Image
                        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
                        bg.alpha_composite(img)
                        img = bg.convert("RGB")
                    ok = img is not None and img.size[0] > 0 and img.size[1] > 0
                    check(ok, f"{fmt}/{cm}/{area}")
                except Exception as e:
                    check(False, f"{fmt}/{cm}/{area} (error: {e})")

    if export_pipeline.can_write_layered_psd():
        for area in ["finish", "withBleed", "innerFrame", "canvas"]:
            opt = ExportOptions(
                color_mode="rgb", format="psd", area=area, dpi_override=72,
                include_border=True, include_white_margin=True, include_paper_color=True,
            )
            psd_path = Path(outdir) / f"test_{area}.psd"
            try:
                ok = export_pipeline.save_page_as_psd(work, page, opt, psd_path)
                check(ok and psd_path.exists(), f"psd/rgb/{area}")
            except Exception as e:
                check(False, f"psd/rgb/{area} (error: {e})")


def test_coma_export():
    print("\n=== Coma Export ===")
    ctx = bpy.context
    work = get_work(ctx)
    if not work or not work.loaded:
        print("  SKIP: work not loaded")
        return
    page = get_active_page(ctx)
    if page is None:
        print("  SKIP: no active page")
        return
    comas = getattr(page, "comas", [])
    if not comas:
        print("  SKIP: no comas on page")
        return

    coma = comas[0]
    bbox = coma_content_mask.mask_bbox_mm(coma)
    if bbox is None:
        check(False, "coma bbox retrieval")
        return

    dpi = 72
    canvas_h = int(round(mm_to_px(float(work.paper.canvas_height_mm), dpi)))
    crop_box = (
        max(0, int(round(mm_to_px(bbox[0], dpi)))),
        max(0, canvas_h - int(round(mm_to_px(bbox[3], dpi)))),
        int(round(mm_to_px(bbox[2], dpi))),
        canvas_h - int(round(mm_to_px(bbox[1], dpi))),
    )

    opt = ExportOptions(
        color_mode="rgb", format="png", area="canvas", dpi_override=72,
        include_border=True, include_white_margin=False, include_paper_color=True,
    )
    img = export_pipeline.render_coma(work, page, coma, opt, crop_box)
    check(img is not None and img.size[0] > 0, "coma render_coma PNG")

    if export_pipeline.can_write_layered_psd():
        outdir = tempfile.mkdtemp()
        from pathlib import Path
        psd_path = Path(outdir) / "coma.psd"
        try:
            ok = export_pipeline.save_coma_as_psd(work, page, coma, opt, crop_box, psd_path)
            check(ok and psd_path.exists(), "coma save_coma_as_psd")
        except Exception as e:
            check(False, f"coma PSD (error: {e})")


def main():
    test_panel_registration()
    test_operator_registration()
    test_preset_properties()
    test_preset_crud()
    test_page_export_all_formats()
    test_coma_export()

    print(f"\n{'=' * 50}")
    if _errors:
        print(f"FAILED: {len(_errors)} errors")
        for e in _errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
