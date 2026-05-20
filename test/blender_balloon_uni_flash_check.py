"""Blender 実機用: フキダシのウニフラッシュ形状を確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_uni_flash",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_uni_flash"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _enum_ids(prop) -> set[str]:
    return {str(getattr(item, "identifier", "") or "") for item in prop.enum_items}


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_uni_flash_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "UniFlash.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_uni_flash.core.work import get_work
        from bname_dev_balloon_uni_flash.io import export_balloon, schema
        from bname_dev_balloon_uni_flash.operators import balloon_op
        from bname_dev_balloon_uni_flash.ui import overlay_balloon
        from bname_dev_balloon_uni_flash.utils import balloon_curve_object, balloon_uni_flash
        from bname_dev_balloon_uni_flash.utils.geom import Rect
        from bname_dev_balloon_uni_flash.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="uni_flash",
            x=32.0,
            y=48.0,
            w=90.0,
            h=46.0,
            parent_kind="page",
            parent_key=page_key,
        )
        entry.line_width_mm = 0.45
        entry.shape_params.uni_flash_spacing_mm = 1.2
        entry.shape_params.uni_flash_fill_scale_percent = 72.0
        entry.shape_params.uni_flash_line_density_compensation = True
        entry.shape_params.uni_flash_fill_density_compensation = True
        entry.shape_params.uni_flash_max_line_count = 400
        entry.shape_params.uni_flash_line_in_percent = 100.0
        entry.shape_params.uni_flash_line_out_percent = 0.0

        balloon_shape_ids = _enum_ids(entry.bl_rna.properties["shape"])
        assert "uni_flash" in balloon_shape_ids, "フキダシ形状にウニフラッシュがありません"
        effect_ids = _enum_ids(context.scene.bname_effect_line_params.bl_rna.properties["end_shape"])
        assert "uni_flash" not in effect_ids, "効果線の始点/終点形状へフキダシ専用形状が混入しています"

        rect = Rect(0.0, 0.0, entry.width_mm, entry.height_mm)
        geom_density = balloon_uni_flash.geometry_for_entry(entry, rect)
        assert len(geom_density.line_segments_mm) > 20, "ウニフラッシュの線が生成されていません"
        assert len(geom_density.fill_outline_mm) > 20, "ウニフラッシュの下地が生成されていません"
        entry.shape_params.uni_flash_line_density_compensation = False
        entry.shape_params.uni_flash_fill_density_compensation = False
        geom_plain = balloon_uni_flash.geometry_for_entry(entry, rect)
        assert geom_plain.line_segments_mm == geom_density.line_segments_mm, "距離指定では線の密度補正が常時ONになっていません"
        assert geom_plain.fill_outline_mm == geom_density.fill_outline_mm, "距離指定では下地の密度補正が常時ONになっていません"

        entry.shape_params.uni_flash_line_density_compensation = False
        entry.shape_params.uni_flash_fill_density_compensation = False
        saved = schema.balloon_entry_to_dict(entry)
        assert saved["shape"] == "uni_flash"
        params = saved["shapeParams"]
        assert params["uniFlashLineDensityCompensation"] is True
        assert params["uniFlashFillDensityCompensation"] is True
        assert abs(params["uniFlashLineInPercent"] - 100.0) < 1.0e-6
        assert abs(params["uniFlashLineOutPercent"] - 0.0) < 1.0e-6
        restored = page.balloons.add()
        schema.balloon_entry_from_dict(restored, saved)
        assert restored.shape == "uni_flash"
        assert restored.shape_params.uni_flash_line_density_compensation is True
        assert restored.shape_params.uni_flash_fill_density_compensation is True
        assert abs(restored.shape_params.uni_flash_spacing_mm - 1.2) < 1.0e-6
        assert abs(restored.shape_params.uni_flash_line_in_percent - 100.0) < 1.0e-6
        assert abs(restored.shape_params.uni_flash_line_out_percent - 0.0) < 1.0e-6
        page.balloons.remove(len(page.balloons) - 1)

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None and obj.type == "MESH", "ウニフラッシュのオブジェクトが作成されていません"
        assert len(obj.data.polygons) > 0, "ウニフラッシュのメッシュが空です"
        assert len(obj.data.materials) >= 2, "ウニフラッシュの線と下地のマテリアルがまとまっていません"
        assert any(poly.material_index == 1 for poly in obj.data.polygons), (
            "ウニフラッシュの下地が同じオブジェクト内にありません"
        )
        assert any(poly.material_index == 0 and len(poly.vertices) == 3 for poly in obj.data.polygons), (
            "ウニフラッシュ線の抜きが三角形メッシュになっていません"
        )
        fill_obj = bpy.data.objects.get(f"{balloon_curve_object.BALLOON_FILL_NAME_PREFIX}{entry.id}")
        assert fill_obj is None, "ウニフラッシュの下地が別オブジェクトとして残っています"

        fills = []
        lines = []
        line_width_args = []

        def _draw_rect_outline(*_args, **_kwargs):
            return None

        def _draw_polygon_fill(points, _color):
            fills.append(points)

        def _draw_polyline_loop(*_args, **_kwargs):
            raise AssertionError("ウニフラッシュは通常の輪郭線ループで描画しません")

        def _draw_line_segments(segments, _color, **_kwargs):
            assert "width_mm" in _kwargs, "ウニフラッシュ線の太さがmm指定で渡されていません"
            assert "start_width_mm" in _kwargs, "ウニフラッシュ線の入り幅が渡されていません"
            assert "end_width_mm" in _kwargs, "ウニフラッシュ線の抜き幅が渡されていません"
            line_width_args.append(_kwargs["width_mm"])
            lines.extend(segments)

        overlay_balloon.draw_balloons(
            page,
            draw_rect_outline=_draw_rect_outline,
            draw_polygon_fill=_draw_polygon_fill,
            draw_polyline_loop=_draw_polyline_loop,
            draw_line_segments=_draw_line_segments,
            is_entry_visible=lambda _entry: True,
        )
        assert fills, "オーバーレイの下地が描画されていません"
        assert lines, "オーバーレイの線が描画されていません"
        assert all(width > 0 for width in line_width_args)

        layer = export_balloon.render_balloon_layer(entry, canvas_height_px=1200, dpi=144)
        assert layer is not None, "ウニフラッシュを書き出せません"
        assert layer.image.size[0] > 0 and layer.image.size[1] > 0
        print("BNAME_BALLOON_UNI_FLASH_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
