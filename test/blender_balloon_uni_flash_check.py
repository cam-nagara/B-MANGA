"""Blender 実機用: フキダシ線種のウニフラ / 白抜き線を確認。"""

from __future__ import annotations

import importlib.util
import math
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


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _assert_flash_outline(entry, balloon_shapes, Rect) -> None:
    rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    points, corners = balloon_shapes.outline_with_corners_for_entry(entry, rect)
    assert len(points) >= 8, "楕円輪郭の点が不足しています"
    assert not corners, "ウニフラ / 白抜き線の輪郭にトゲ状の角が残っています"
    base = balloon_shapes.flash_base_outline_for_entry(entry, rect)
    assert base is not None and len(base) >= 8, "ベース楕円が作成されていません"
    min_x = min(x for x, _y in base)
    max_x = max(x for x, _y in base)
    min_y = min(y for _x, y in base)
    max_y = max(y for _x, y in base)
    center = ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
    rx = max(1.0e-6, (max_x - min_x) * 0.5)
    ry = max(1.0e-6, (max_y - min_y) * 0.5)
    norms = [((x - center[0]) / rx) ** 2 + ((y - center[1]) / ry) ** 2 for x, y in points]
    assert max(abs(value - 1.0) for value in norms) < 0.08, "輪郭が楕円から外れています"


def _sample_radius_range(samples) -> tuple[float, float]:
    cx = sum(float(s[0]) for s in samples) / len(samples)
    cy = sum(float(s[1]) for s in samples) / len(samples)
    radii = [math.hypot(float(s[0]) - cx, float(s[1]) - cy) for s in samples]
    return min(radii), max(radii)


def _configure_shape_params(entry) -> None:
    sp = entry.shape_params
    sp.dynamic_shape_base_kind = "ellipse"
    sp.cloud_bump_width_mm = 5.0
    sp.cloud_bump_height_mm = 8.0
    sp.cloud_offset_percent = 12.0
    sp.cloud_bump_width_jitter = 0.0
    sp.cloud_bump_height_jitter = 0.0
    sp.cloud_sub_width_ratio = 0.0
    sp.cloud_sub_height_ratio = 0.0
    sp.shape_seed = 0


class _RecordingLayout:
    def __init__(self, props: list[str] | None = None):
        self.props = [] if props is None else props
        self.enabled = True

    def box(self):
        return _RecordingLayout(self.props)

    def row(self, align: bool = False):
        return _RecordingLayout(self.props)

    def column(self, align: bool = False):
        return _RecordingLayout(self.props)

    def split(self, factor: float = 0.5, align: bool = False):
        return _RecordingLayout(self.props)

    def separator(self, **_kwargs):
        return None

    def label(self, **_kwargs):
        return None

    def prop(self, _owner, attr: str, **_kwargs):
        self.props.append(str(attr))
        return None

    def prop_search(self, _owner, attr: str, *_args, **_kwargs):
        self.props.append(str(attr))
        return None

    def operator(self, *_args, **_kwargs):
        return _RecordingOperator()

    def template_curve_mapping(self, *_args, **_kwargs):
        return None


class _RecordingOperator:
    pass


def _effect_setting_props(effect_line_panel, params, effect_type: str) -> list[str]:
    layout = _RecordingLayout()
    effect_line_panel.draw_effect_params(
        layout,
        params,
        with_generate_button=False,
        fixed_effect_type=effect_type,
        show_type=False,
    )
    return layout.props


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_uni_flash_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonFlash.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_uni_flash.core import balloon as balloon_core
        from bname_dev_balloon_uni_flash.core import effect_line as effect_line_core
        from bname_dev_balloon_uni_flash.core.work import get_work
        from bname_dev_balloon_uni_flash.io import export_balloon, schema
        from bname_dev_balloon_uni_flash.operators import layer_detail_op
        from bname_dev_balloon_uni_flash.operators import balloon_op
        from bname_dev_balloon_uni_flash.panels import effect_line_panel
        from bname_dev_balloon_uni_flash.panels import layer_stack_detail_ui
        from bname_dev_balloon_uni_flash.utils import (
            balloon_curve_object,
            balloon_flash_effect_line_mesh,
            balloon_line_mesh,
            balloon_shapes,
        )
        from bname_dev_balloon_uni_flash.utils.geom import Rect
        from bname_dev_balloon_uni_flash.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        flash_styles = {"uni_flash", "white_outline"}
        shape_ids = {item[0] for item in balloon_core._SHAPE_ITEMS}
        line_style_ids = {item[0] for item in balloon_core._LINE_STYLE_ITEMS}
        assert not (flash_styles & shape_ids), "ウニフラ / 白抜き線が形状候補に残っています"
        assert flash_styles <= line_style_ids, "ウニフラ / 白抜き線が線種候補にありません"
        assert balloon_shapes.normalize_shape("uni_flash") == "ellipse"
        assert balloon_shapes.normalize_shape("white_outline") == "ellipse"
        assert balloon_shapes.normalize_line_style("uni_flash") == "uni_flash"
        assert balloon_shapes.normalize_line_style("white_outline") == "white_outline"
        expected_uni_flash_fields = tuple(
            field
            for field in effect_line_core.EFFECT_PARAM_FIELDS
            if field not in {"speed_angle_deg", "speed_line_count"}
            and not field.startswith("white_outline_")
        )
        # v0.6.290: 白抜き線の詳細フィールドもフキダシ側の同じ保存リストに同居する
        balloon_uni_fields = tuple(
            field
            for field in balloon_core.UNI_FLASH_PARAM_FIELDS
            if not field.startswith("white_outline_")
        )
        assert balloon_uni_fields == expected_uni_flash_fields, (
            "ウニフラ線種の設定項目が効果線の集中線と一致していません"
        )
        assert any(
            field.startswith("white_outline_")
            for field in balloon_core.UNI_FLASH_PARAM_FIELDS
        ), "白抜き線の詳細フィールドが保存リストにありません"

        for index, line_style in enumerate(("uni_flash", "white_outline")):
            entry = balloon_op._create_balloon_entry(
                context,
                page,
                shape="ellipse",
                x=24.0 + index * 70.0,
                y=42.0,
                w=58.0,
                h=42.0,
                parent_kind="page",
                parent_key=page_key,
            )
            _configure_shape_params(entry)
            entry.line_style = line_style
            balloon_core.apply_balloon_line_style_defaults(entry, force=True)
            assert entry.shape == "ellipse"
            assert entry.line_style == line_style
            assert abs(float(entry.line_valley_width_pct) - 0.0) < 1.0e-6, "入り・抜きの初期値が0%ではありません"
            assert abs(float(entry.line_peak_width_pct) - 100.0) < 1.0e-6, "中間線幅の初期値が100%ではありません"
            if line_style == "uni_flash":
                for field in expected_uni_flash_fields:
                    assert hasattr(entry, field), f"ウニフラ線種に集中線の設定項目 {field} がありません"
                assert entry.effect_type == "uni_flash"
                assert entry.start_shape == "ellipse"
                assert entry.end_shape == "ellipse"
                assert abs(float(entry.brush_size_mm) - 0.3) < 1.0e-6
                assert int(entry.max_line_count) == 1000
                assert entry.spacing_mode == "distance"
                assert abs(float(entry.spacing_distance_mm) - 1.0) < 1.0e-6
                assert abs(float(entry.in_percent) - 0.0) < 1.0e-6
                assert abs(float(entry.out_percent) - 0.0) < 1.0e-6
                assert abs(float(entry.in_start_percent) - 50.0) < 1.0e-6
                assert abs(float(entry.out_start_percent) - 50.0) < 1.0e-6
                # v0.6.286: 白抜き線の初期値はオフ
                assert not bool(entry.white_underlay_enabled)
                assert abs(float(entry.white_underlay_width_percent) - 100.0) < 1.0e-6
                # v0.6.286: ウニフラに「ズラし量」が追加されている
                assert hasattr(entry, "uni_flash_offset_percent")
                assert abs(float(entry.uni_flash_offset_percent) - 0.0) < 1.0e-6
                focus_params = context.scene.bname_effect_line_params
                focus_params.start_shape = entry.start_shape
                focus_params.end_shape = entry.end_shape
                focus_params.spacing_mode = entry.spacing_mode
                focus_params.bundle_enabled = entry.bundle_enabled
                focus_props = _effect_setting_props(
                    effect_line_panel,
                    focus_params,
                    "focus",
                )
                uni_props = _effect_setting_props(effect_line_panel, entry, "uni_flash")
                # 「ズラし量」はウニフラ専用なので、それ以外が集中線と一致していればよい
                assert set(uni_props) - {"uni_flash_offset_percent"} == set(focus_props), (
                    "ウニフラ線種の表示項目が集中線と一致していません"
                )
                assert "spacing_density_compensation" in uni_props, "ウニフラ設定に密度補正が表示されていません"
                assert entry.bl_rna.properties["line_style"].name == "線種"
                assert entry.bl_rna.properties["line_color"].name == "線色"
                assert entry.bl_rna.properties["fill_color"].name == "塗り色"
                assert "flash_line_count" not in uni_props
                assert "flash_line_spacing_mm" not in uni_props
                assert "flash_white_line_width_percent" not in uni_props
                popup_layout = _RecordingLayout()
                layer_detail_op._draw_balloon_detail(popup_layout, entry, page)
                stack_layout = _RecordingLayout()
                layer_stack_detail_ui._draw_balloon_selected_settings(stack_layout, context, entry)
                for detail_props in (popup_layout.props, stack_layout.props):
                    for old_attr in (
                        "flash_line_count",
                        "flash_line_spacing_mm",
                        "flash_white_line_width_percent",
                        "fill_material_name",
                        "fill_blur_amount",
                        "fill_gradient_enabled",
                        "outer_white_margin_enabled",
                        "inner_white_margin_enabled",
                    ):
                        assert old_attr not in detail_props, f"ウニフラ詳細に余分な項目 {old_attr} が出ています"
            assert int(entry.flash_line_count) == 120
            assert abs(float(entry.flash_line_spacing_mm) - 1.0) < 1.0e-6
            assert bool(entry.flash_white_line_enabled), "白線が初期状態で有効ではありません"
            assert abs(float(entry.flash_white_line_width_percent) - 100.0) < 1.0e-6
            assert abs(float(entry.flash_white_line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(entry.flash_white_line_peak_width_pct) - 100.0) < 1.0e-6
            assert int(entry.flash_white_outline_count) == 5
            assert int(entry.flash_white_outline_white_line_count) == 24
            assert int(entry.flash_white_outline_black_line_count) == 3
            assert abs(float(entry.thorn_multi_line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(entry.thorn_multi_line_peak_width_pct) - 100.0) < 1.0e-6
            _assert_flash_outline(entry, balloon_shapes, Rect)

            saved = schema.balloon_entry_to_dict(entry)
            assert saved["shape"] == "ellipse", "保存データの形状が通常形状になっていません"
            assert saved["lineStyle"] == line_style, "保存データの線種にウニフラ / 白抜き線が残っていません"
            assert saved["lineValleyWidthPct"] == 0.0
            assert saved["linePeakWidthPct"] == 100.0
            assert saved["flashLineCount"] == 120
            assert saved["flashLineSpacingMm"] == 1.0
            assert saved["flashWhiteLineEnabled"] is True
            assert saved["flashWhiteLineWidthPercent"] == 100.0
            assert saved["flashWhiteLineValleyWidthPct"] == 0.0
            assert saved["flashWhiteLinePeakWidthPct"] == 100.0
            assert saved["flashWhiteOutlineCount"] == 5
            assert saved["flashWhiteOutlineWhiteLineCount"] == 24
            assert saved["flashWhiteOutlineBlackLineCount"] == 3
            if line_style == "uni_flash":
                saved_uni_keys = tuple(
                    key
                    for key in saved["uniFlashParams"].keys()
                    if not key.startswith("white_outline_")
                )
                assert saved_uni_keys == expected_uni_flash_fields
                assert saved["uniFlashParams"]["start_shape"] == "ellipse"
                assert saved["uniFlashParams"]["end_shape"] == "ellipse"
                assert saved["uniFlashParams"]["in_percent"] == 0.0
                assert saved["uniFlashParams"]["out_percent"] == 0.0
            restored = page.balloons.add()
            schema.balloon_entry_from_dict(restored, saved)
            assert restored.shape == "ellipse"
            assert restored.line_style == line_style
            assert abs(float(restored.line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(restored.line_peak_width_pct) - 100.0) < 1.0e-6
            assert int(restored.flash_line_count) == 120
            assert abs(float(restored.flash_line_spacing_mm) - 1.0) < 1.0e-6
            assert bool(restored.flash_white_line_enabled)
            assert abs(float(restored.flash_white_line_width_percent) - 100.0) < 1.0e-6
            assert abs(float(restored.flash_white_line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(restored.flash_white_line_peak_width_pct) - 100.0) < 1.0e-6
            assert int(restored.flash_white_outline_count) == 5
            assert int(restored.flash_white_outline_white_line_count) == 24
            assert int(restored.flash_white_outline_black_line_count) == 3
            if line_style == "uni_flash":
                assert restored.start_shape == "ellipse"
                assert restored.end_shape == "ellipse"
                assert abs(float(restored.brush_size_mm) - 0.3) < 1.0e-6
                assert int(restored.max_line_count) == 1000
                assert abs(float(restored.in_percent) - 0.0) < 1.0e-6
                assert abs(float(restored.out_percent) - 0.0) < 1.0e-6
            page.balloons.remove(len(page.balloons) - 1)

            legacy_saved = dict(saved)
            legacy_saved["shape"] = line_style
            legacy_saved["lineStyle"] = "solid"
            legacy_restored = page.balloons.add()
            schema.balloon_entry_from_dict(legacy_restored, legacy_saved)
            assert legacy_restored.shape == "ellipse"
            assert legacy_restored.line_style == line_style
            page.balloons.remove(len(page.balloons) - 1)

            if line_style == "uni_flash":
                entry.start_shape = "rect"
                entry.start_rounded_corner_enabled = True
                entry.spacing_mode = "angle"
                entry.spacing_angle_deg = 12.5
                entry.bundle_enabled = True
                entry.bundle_line_count = 4
                entry.in_percent = 15.0
                entry.out_percent = 25.0
                entry.white_underlay_width_percent = 175.0
            else:
                entry.flash_white_line_width_percent = 175.0
            custom_saved = schema.balloon_entry_to_dict(entry)
            if line_style == "uni_flash":
                assert custom_saved["uniFlashParams"]["start_shape"] == "rect"
                assert custom_saved["uniFlashParams"]["spacing_mode"] == "angle"
                assert custom_saved["uniFlashParams"]["bundle_enabled"] is True
                assert custom_saved["uniFlashParams"]["white_underlay_width_percent"] == 175.0
            else:
                assert custom_saved["flashWhiteLineWidthPercent"] == 175.0
            custom_restored = page.balloons.add()
            schema.balloon_entry_from_dict(custom_restored, custom_saved)
            if line_style == "uni_flash":
                assert custom_restored.start_shape == "rect"
                assert custom_restored.spacing_mode == "angle"
                assert custom_restored.bundle_enabled is True
                assert abs(float(custom_restored.white_underlay_width_percent) - 175.0) < 1.0e-6
            else:
                assert abs(float(custom_restored.flash_white_line_width_percent) - 175.0) < 1.0e-6
            page.balloons.remove(len(page.balloons) - 1)
            if line_style == "uni_flash":
                entry.start_shape = "ellipse"
                entry.start_rounded_corner_enabled = False
                entry.spacing_mode = "distance"
                entry.spacing_distance_mm = 1.0
                entry.bundle_enabled = False
                entry.in_percent = 0.0
                entry.out_percent = 0.0
                # v0.6.286 で初期値オフになったため、白抜き線の検証区画では明示的にオン
                entry.white_underlay_enabled = True
                entry.white_underlay_width_percent = 100.0
            else:
                entry.flash_white_line_width_percent = 100.0

            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert obj is not None and obj.type == "CURVE", "フキダシのカーブ実体が作成されていません"
            assert len(obj.data.splines) >= 1, "フキダシの輪郭カーブがありません"
            body_spline = obj.data.splines[0]
            assert len(body_spline.bezier_points) == 4, "下地が楕円カーブになっていません"
            assert _evaluated_polygon_count(obj) == 0, "本体カーブ側に表示面が残っています"
            flash_obj = bpy.data.objects.get(balloon_flash_effect_line_mesh._flash_effect_line_mesh_object_name(entry.id))
            white_obj = bpy.data.objects.get(balloon_line_mesh._flash_white_line_mesh_object_name(entry.id))
            line_obj = bpy.data.objects.get(balloon_line_mesh._line_mesh_object_name(entry.id))
            assert flash_obj is not None and _evaluated_polygon_count(flash_obj) > 0, "放射状の線実体が作成されていません"
            assert white_obj is None, "古い閉じた白線実体が残っています"
            assert line_obj is None, "古い閉じた黒線実体が残っています"
            body_samples = balloon_line_mesh._sample_body_bezier(body_spline, balloon_line_mesh.SAMPLES_PER_SEGMENT)
            _body_min, body_max = _sample_radius_range(body_samples)
            verts = [(float(v.co.x), float(v.co.y), float(v.co.z)) for v in flash_obj.data.vertices]
            assert len(verts) > 100, "放射状の線本数が不足しています"
            center_x = sum(float(s[0]) for s in body_samples) / len(body_samples)
            center_y = sum(float(s[1]) for s in body_samples) / len(body_samples)
            flash_max = max(math.hypot(x - center_x, y - center_y) for x, y, _z in verts)
            assert flash_max > body_max * 1.45, "線が楕円の外側から終点へ向かっていません"
            material_indices = {int(poly.material_index) for poly in flash_obj.data.polygons}
            if line_style == "uni_flash":
                assert {0, 2} <= material_indices, "黒線と白線の重なりが作成されていません"
            else:
                assert {0, 1} <= material_indices, "白抜き線の白線と黒線が作成されていません"
            black_z = max((z for _x, _y, z in verts), default=0.0)
            white_z = min((z for _x, _y, z in verts if z > 0.0), default=0.0)
            assert 0.0 < white_z < black_z, "白線が黒線と下地の間に配置されていません"
            if line_style == "uni_flash":
                entry.white_underlay_enabled = False
            else:
                entry.flash_white_line_enabled = False
            balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert bpy.data.objects.get(balloon_line_mesh._flash_white_line_mesh_object_name(entry.id)) is None
            if line_style == "uni_flash":
                entry.white_underlay_enabled = True
            else:
                entry.flash_white_line_enabled = True
            balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert bpy.data.objects.get(balloon_flash_effect_line_mesh._flash_effect_line_mesh_object_name(entry.id)) is not None
            layer = export_balloon.render_balloon_layer(entry, canvas_height_px=1200, dpi=144)
            assert layer is not None, "フキダシを書き出せません"
            assert layer.image.size[0] > 0 and layer.image.size[1] > 0

        print("BNAME_BALLOON_FLASH_LINE_STYLES_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
