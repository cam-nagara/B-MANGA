"""Blender実機用: コマ用blend下絵にページ一覧の要素が残ることの確認."""

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
        "bname_dev_underlay",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_underlay"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _pixel_for_mm(x_mm: float, y_mm: float, image_height: int, dpi: int) -> tuple[int, int]:
    from bname_dev_underlay.utils.geom import mm_to_px

    return int(round(mm_to_px(x_mm, dpi))), image_height - int(round(mm_to_px(y_mm, dpi)))


def _assert_current_coma_visible(image_path: Path, coma) -> None:
    from bname_dev_underlay.io import export_pipeline
    from bname_dev_underlay.utils.coma_camera_constants import DEFAULT_REF_DPI

    Image = export_pipeline.Image
    assert Image is not None
    with Image.open(str(image_path)) as opened:
        img = opened.convert("RGBA")
        x_mm = float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5
        y_mm = float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5
        px = _pixel_for_mm(x_mm, y_mm, img.height, DEFAULT_REF_DPI)
        assert img.getpixel(px)[3] > 0, "current coma area was transparent in underlay"


def _assert_gp_stroke_rendered(image_path: Path, coma) -> None:
    from bname_dev_underlay.io import export_pipeline
    from bname_dev_underlay.utils.coma_camera_constants import DEFAULT_REF_DPI

    Image = export_pipeline.Image
    assert Image is not None
    y_mm = float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5
    x0 = float(coma.rect_x_mm) + 10.0
    x1 = float(coma.rect_x_mm) + float(coma.rect_width_mm) - 10.0
    with Image.open(str(image_path)) as opened:
        img = opened.convert("RGBA")
        dark = 0
        for index in range(20):
            x_mm = x0 + (x1 - x0) * index / 19.0
            px, py = _pixel_for_mm(x_mm, y_mm, img.height, DEFAULT_REF_DPI)
            for dy in range(-2, 3):
                r, g, b, a = img.getpixel((px, py + dy))
                if a > 0 and r < 80 and g < 80 and b < 80:
                    dark += 1
                    break
        if dark < 8:
            raise AssertionError(f"page-list GP stroke was not rendered into underlay: dark={dark}")


def _draw_page_gp_stroke(context, page, coma) -> None:
    from bname_dev_underlay.core.work import get_work
    from bname_dev_underlay.utils import gp_layer_parenting, gpencil, layer_hierarchy, page_grid
    from bname_dev_underlay.utils.geom import mm_to_m

    obj = gpencil.ensure_master_gpencil(context.scene)
    layer = obj.data.layers.new("下絵確認GP")
    gp_layer_parenting.set_parent_key(layer, layer_hierarchy.coma_stack_key(page, coma))
    frame = gpencil.ensure_active_frame(layer)
    assert frame is not None and frame.drawing is not None
    gpencil.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
    work = get_work(context)
    page_index = next(i for i, candidate in enumerate(work.pages) if candidate.id == page.id)
    ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
    y = oy + float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5
    x0 = ox + float(coma.rect_x_mm) + 10.0
    x1 = ox + float(coma.rect_x_mm) + float(coma.rect_width_mm) - 10.0
    assert gpencil.add_stroke_to_drawing(
        frame.drawing,
        [(mm_to_m(x0), mm_to_m(y), 0.0), (mm_to_m(x1), mm_to_m(y), 0.0)],
        radius=0.002,
    )


def _koma_reference_path(work_dir: Path, page_id: str, coma_id: str) -> Path:
    from bname_dev_underlay.utils.coma_camera_refs import _koma_ref_path, reference_dir

    return _koma_ref_path(reference_dir(work_dir), page_id, coma_id)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_underlay_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "Underlay.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev_underlay.utils import coma_camera_refs

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        coma = page.comas[0]
        work_dir = Path(work.work_dir)
        _draw_page_gp_stroke(context, page, coma)

        refs = coma_camera_refs.ensure_reference_images(work, page.id, coma.coma_id)
        assert refs, "underlay reference was not generated in work.blend"
        underlay = _koma_reference_path(work_dir, page.id, coma.coma_id)
        assert underlay.is_file(), underlay
        _assert_current_coma_visible(underlay, coma)
        _assert_gp_stroke_rendered(underlay, coma)

        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bname_work
        page_id = str(bpy.context.scene.bname_current_coma_page_id)
        coma_id = str(bpy.context.scene.bname_current_coma_id)
        underlay = _koma_reference_path(work_dir, page_id, coma_id)
        underlay.unlink(missing_ok=True)
        pageclean = coma_camera_refs._page_ref_path(coma_camera_refs.reference_dir(work_dir), page_id)
        pageclean.unlink(missing_ok=True)
        refs = coma_camera_refs.ensure_reference_images(work, page_id, coma_id)
        assert refs, "underlay reference was not regenerated from work.blend"
        assert underlay.is_file(), underlay
        current_page = work.pages[0]
        current_coma = current_page.comas[0]
        _assert_current_coma_visible(underlay, current_coma)
        _assert_gp_stroke_rendered(underlay, current_coma)

        print("BNAME_COMA_UNDERLAY_REFERENCE_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
