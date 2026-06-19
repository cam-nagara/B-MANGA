"""Blender実機用: コマ内容の透明度マスクを表示・書き出しで検証。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_COMA_CONTENT_OPACITY_MASK_OUT", "")
OUTPUT_PATH = Path(_OUT_ENV) if _OUT_ENV else None


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_content_mask",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_content_mask"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _point_px(work, dpi: int, x_mm: float, y_mm: float) -> tuple[int, int]:
    from bmanga_dev_coma_content_mask.utils.geom import mm_to_px

    return (
        int(round(mm_to_px(x_mm, dpi))),
        int(round(mm_to_px(float(work.paper.canvas_height_mm) - y_mm, dpi))),
    )


def _rgb_at(image, x: int, y: int) -> tuple[int, int, int]:
    pixel = image.convert("RGBA").getpixel((x, y))
    return (pixel[0], pixel[1], pixel[2])


def _assert_material_mask(obj) -> None:
    found = False
    for mat in getattr(getattr(obj, "data", None), "materials", []) or []:
        if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if getattr(node, "label", "") == "コマ内容マスク":
                found = True
                break
    assert found, "フキダシの表示材料にコマ内容マスクがありません"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_content_mask_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ComaContentMask.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_coma_content_mask.io import export_pipeline
        from bmanga_dev_coma_content_mask.io import export_soft_mask
        from bmanga_dev_coma_content_mask.utils import balloon_curve_object, coma_content_mask
        from bmanga_dev_coma_content_mask.utils.layer_hierarchy import coma_stack_key
        from PIL import Image, ImageChops, ImageDraw, ImageFilter

        scene = bpy.context.scene
        work = scene.bmanga_work
        page = work.pages[0]
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 20.0
        coma.rect_y_mm = 40.0
        coma.rect_width_mm = 80.0
        coma.rect_height_mm = 80.0
        coma.background_color = (1.0, 1.0, 1.0, 1.0)
        coma.border.visible = True
        coma.border.style = "brush"
        coma.border.width_mm = 6.0
        coma.border.blur_amount = 0.85
        coma.border.blur_curve_points = "0.0000,0.0000;1.0000,1.0000"
        parent_key = coma_stack_key(page, coma)

        linear_mask_info = coma_content_mask.ensure_viewport_mask(scene, work, page, coma)
        assert linear_mask_info is not None, "線形ぼかしカーブのコマ内容マスク画像が作成されていません"
        linear_signature = str(linear_mask_info.image.get(coma_content_mask.PROP_MASK_SIGNATURE, "") or "")
        steep_curve = "0.0000,0.0000;0.7500,0.0100;0.7600,0.9828;1.0000,1.0000"
        coma.border.blur_curve_points = steep_curve
        curved_mask_info = coma_content_mask.ensure_viewport_mask(scene, work, page, coma)
        assert curved_mask_info is not None, "ぼかしカーブ反映後のコマ内容マスク画像が作成されていません"
        curved_signature = str(curved_mask_info.image.get(coma_content_mask.PROP_MASK_SIGNATURE, "") or "")
        assert curved_signature and curved_signature != linear_signature, "ぼかしカーブ変更でマスク画像が更新されていません"

        bbox = coma_content_mask.mask_bbox_mm(coma)
        assert bbox is not None, "マスク範囲が取得できません"
        poly = coma_content_mask.coma_polygon_mm(coma)
        size = (360, 360)
        coma.border.blur_curve_points = "0.0000,0.0000;1.0000,1.0000"
        linear_mask = export_soft_mask.coma_soft_edge_mask(
            Image, ImageChops, ImageDraw, ImageFilter, coma, poly, bbox, size, 144
        )
        coma.border.blur_curve_points = steep_curve
        curved_mask = export_soft_mask.coma_soft_edge_mask(
            Image, ImageChops, ImageDraw, ImageFilter, coma, poly, bbox, size, 144
        )
        row = size[1] // 2
        diffs = []
        for x in range(size[0]):
            base = int(linear_mask.getpixel((x, row)))
            shaped = int(curved_mask.getpixel((x, row)))
            if 20 < base < 235:
                diffs.append(abs(base - shaped))
        assert diffs and max(diffs) >= 20, "ぼかしカーブがマスク濃度に反映されていません"

        balloon = page.balloons.add()
        balloon.id = "balloon_export_mask"
        balloon.title = "書き出しマスク確認"
        balloon.shape = "ellipse"
        balloon.x_mm = 70.0
        balloon.y_mm = 60.0
        balloon.width_mm = 80.0
        balloon.height_mm = 50.0
        balloon.parent_kind = "coma"
        balloon.parent_key = parent_key
        balloon.line_style = "none"
        balloon.fill_color = (0.0, 0.0, 0.0, 1.0)
        balloon.fill_opacity = 100.0
        balloon.opacity = 100.0

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=balloon, page=page)
        assert obj is not None, "フキダシが作成されていません"
        _assert_material_mask(obj)
        mask = coma_content_mask.ensure_viewport_mask_for_entry(scene, work, page, balloon)
        assert mask is not None, "コマ内容マスク画像が作成されていません"
        assert str(page.id) in mask.name and str(coma.id) in mask.name and "0001" in mask.name, (
            f"マスク画像名にページ番号とコマIDが含まれていません: {mask.name}"
        )

        options = export_pipeline.ExportOptions(
            dpi_override=72,
            area="canvas",
            include_coma_previews=False,
            include_border=True,
            include_paper_color=True,
            include_nombre=False,
            include_work_info=False,
        )
        group_masks = export_pipeline._coma_group_masks(work, page, options)
        content_path = export_pipeline._coma_content_group_path(coma)
        assert content_path in group_masks, "書き出し用のコマ内容マスクがありません"
        assert str(page.id) in group_masks[content_path].name and str(coma.id) in group_masks[content_path].name, (
            f"書き出し用マスク名にページとコマが含まれていません: {group_masks[content_path].name}"
        )
        image = export_pipeline.render_page(work, page, options)
        assert image is not None, "ページ画像を書き出せません"
        if OUTPUT_PATH is not None:
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            image.save(OUTPUT_PATH)
        inside = _rgb_at(image, *_point_px(work, 72, 90.0, 85.0))
        outside = _rgb_at(image, *_point_px(work, 72, 135.0, 85.0))
        assert max(inside) < 40, f"コマ内のフキダシが表示されていません: rgb={inside}"
        assert min(outside) > 210, f"コマ外のフキダシが透明度マスクで消えていません: rgb={outside}"
        print(f"BMANGA_COMA_CONTENT_OPACITY_MASK_OK mask={mask.name} inside={inside} outside={outside}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
