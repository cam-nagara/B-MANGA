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
        "bmanga_dev_underlay",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_underlay"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _pixel_for_mm(x_mm: float, y_mm: float, image_height: int, dpi: int) -> tuple[int, int]:
    from bmanga_dev_underlay.utils.geom import mm_to_px

    return int(round(mm_to_px(x_mm, dpi))), image_height - int(round(mm_to_px(y_mm, dpi)))


def _assert_current_coma_background_transparent(image_path: Path, coma) -> None:
    from bmanga_dev_underlay.io import export_pipeline
    from bmanga_dev_underlay.utils.coma_camera_constants import DEFAULT_REF_DPI

    Image = export_pipeline.Image
    assert Image is not None
    with Image.open(str(image_path)) as opened:
        img = opened.convert("RGBA")
        x_mm = float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5
        y_mm = float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.25
        px = _pixel_for_mm(x_mm, y_mm, img.height, DEFAULT_REF_DPI)
        assert img.getpixel(px)[3] == 0, "current coma background was not transparent in page image"


def _koma_reference_path(work_dir: Path, page_id: str, coma_id: str) -> Path:
    from bmanga_dev_underlay.utils.coma_camera_refs import _koma_ref_path, reference_dir

    return _koma_ref_path(reference_dir(work_dir), page_id, coma_id)


def _managed_page_or_coma_backgrounds(context):
    camera = getattr(context.scene, "camera", None)
    data = getattr(camera, "data", None) if camera is not None else None
    backgrounds = []
    for bg in getattr(data, "background_images", []) or []:
        img = getattr(bg, "image", None)
        if img is None:
            continue
        try:
            kind = str(img.get("bmanga_kind", "") or "")
            full_page = bool(img.get("bmanga_full_page_mask", False))
        except Exception:  # noqa: BLE001
            kind = ""
            full_page = False
        if full_page or kind in {"name", "koma"}:
            backgrounds.append(bg)
    return backgrounds


def _assert_coma_render_resolution_matches_paper(context) -> None:
    from bmanga_dev_underlay.utils.geom import mm_to_px

    work = context.scene.bmanga_work
    paper = work.paper
    expected_x = int(round(mm_to_px(float(paper.canvas_width_mm), int(paper.dpi))))
    expected_y = int(round(mm_to_px(float(paper.canvas_height_mm), int(paper.dpi))))
    actual = (int(context.scene.render.resolution_x), int(context.scene.render.resolution_y))
    expected = (expected_x, expected_y)
    if actual != expected:
        raise AssertionError(f"coma render resolution mismatch: actual={actual} expected={expected}")


def _assert_page_images_not_configured_on_camera(context) -> None:
    backgrounds = _managed_page_or_coma_backgrounds(context)
    if backgrounds:
        names = [getattr(getattr(bg, "image", None), "name", "") for bg in backgrounds]
        raise AssertionError(f"page/coma backgrounds should not be configured on camera: {names}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_underlay_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "Underlay.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev_underlay.utils import coma_camera_refs

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        coma = page.comas[0]
        work_dir = Path(work.work_dir)

        refs = coma_camera_refs.ensure_reference_images(work, page.id, coma.coma_id)
        assert refs, "underlay reference was not generated in work.blend"
        underlay = _koma_reference_path(work_dir, page.id, coma.coma_id)
        assert underlay.is_file(), underlay
        _assert_current_coma_background_transparent(underlay, coma)

        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result
        _assert_coma_render_resolution_matches_paper(bpy.context)
        _assert_page_images_not_configured_on_camera(bpy.context)
        result = bpy.ops.bmanga.coma_camera_ensure()
        assert result == {"FINISHED"}, result
        _assert_page_images_not_configured_on_camera(bpy.context)
        work = bpy.context.scene.bmanga_work
        page_id = str(bpy.context.scene.bmanga_current_coma_page_id)
        coma_id = str(bpy.context.scene.bmanga_current_coma_id)
        underlay = _koma_reference_path(work_dir, page_id, coma_id)
        underlay.unlink(missing_ok=True)
        pageclean = coma_camera_refs._page_coma_ref_path(coma_camera_refs.reference_dir(work_dir), page_id, coma_id)
        pageclean.unlink(missing_ok=True)
        refs = coma_camera_refs.ensure_reference_images(work, page_id, coma_id)
        assert refs, "underlay reference was not regenerated from work.blend"
        assert underlay.is_file(), underlay
        current_page = work.pages[0]
        current_coma = current_page.comas[0]
        _assert_current_coma_background_transparent(underlay, current_coma)

        print("BMANGA_COMA_UNDERLAY_REFERENCE_OK")
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
