"""Blender 実機用: フキダシ形状のウニフラ / 白抜き線を確認。"""

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


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_uni_flash_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonFlash.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_uni_flash.core import balloon as balloon_core
        from bname_dev_balloon_uni_flash.core.work import get_work
        from bname_dev_balloon_uni_flash.io import export_balloon, schema
        from bname_dev_balloon_uni_flash.operators import balloon_op
        from bname_dev_balloon_uni_flash.utils import balloon_curve_object, balloon_line_mesh, balloon_shapes
        from bname_dev_balloon_uni_flash.utils.geom import Rect
        from bname_dev_balloon_uni_flash.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        shape_ids = {item[0] for item in balloon_core._SHAPE_ITEMS}
        assert {"uni_flash", "white_outline"} <= shape_ids, "フキダシ形状候補に追加形状がありません"
        assert balloon_shapes.normalize_shape("uni_flash") == "uni_flash"
        assert balloon_shapes.normalize_shape("white_outline") == "white_outline"

        for index, shape in enumerate(("uni_flash", "white_outline")):
            entry = balloon_op._create_balloon_entry(
                context,
                page,
                shape=shape,
                x=24.0 + index * 70.0,
                y=42.0,
                w=58.0,
                h=42.0,
                parent_kind="page",
                parent_key=page_key,
            )
            _configure_shape_params(entry)
            assert entry.shape == shape
            assert abs(float(entry.line_valley_width_pct) - 0.0) < 1.0e-6, "入り・抜きの初期値が0%ではありません"
            assert abs(float(entry.line_peak_width_pct) - 100.0) < 1.0e-6, "中間線幅の初期値が100%ではありません"
            assert bool(entry.flash_white_line_enabled), "白線が初期状態で有効ではありません"
            assert abs(float(entry.flash_white_line_width_percent) - 100.0) < 1.0e-6
            assert abs(float(entry.flash_white_line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(entry.flash_white_line_peak_width_pct) - 100.0) < 1.0e-6
            assert abs(float(entry.thorn_multi_line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(entry.thorn_multi_line_peak_width_pct) - 100.0) < 1.0e-6
            _assert_flash_outline(entry, balloon_shapes, Rect)

            saved = schema.balloon_entry_to_dict(entry)
            assert saved["shape"] == shape, "保存データに追加形状が残っていません"
            assert saved["lineValleyWidthPct"] == 0.0
            assert saved["linePeakWidthPct"] == 100.0
            assert saved["flashWhiteLineEnabled"] is True
            assert saved["flashWhiteLineWidthPercent"] == 100.0
            assert saved["flashWhiteLineValleyWidthPct"] == 0.0
            assert saved["flashWhiteLinePeakWidthPct"] == 100.0
            restored = page.balloons.add()
            schema.balloon_entry_from_dict(restored, saved)
            assert restored.shape == shape
            assert abs(float(restored.line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(restored.line_peak_width_pct) - 100.0) < 1.0e-6
            assert bool(restored.flash_white_line_enabled)
            assert abs(float(restored.flash_white_line_width_percent) - 100.0) < 1.0e-6
            assert abs(float(restored.flash_white_line_valley_width_pct) - 0.0) < 1.0e-6
            assert abs(float(restored.flash_white_line_peak_width_pct) - 100.0) < 1.0e-6
            page.balloons.remove(len(page.balloons) - 1)

            entry.flash_white_line_width_percent = 175.0
            custom_saved = schema.balloon_entry_to_dict(entry)
            assert custom_saved["flashWhiteLineWidthPercent"] == 175.0
            custom_restored = page.balloons.add()
            schema.balloon_entry_from_dict(custom_restored, custom_saved)
            assert abs(float(custom_restored.flash_white_line_width_percent) - 175.0) < 1.0e-6
            page.balloons.remove(len(page.balloons) - 1)
            entry.flash_white_line_width_percent = 100.0

            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert obj is not None and obj.type == "CURVE", "フキダシのカーブ実体が作成されていません"
            assert len(obj.data.splines) >= 1, "フキダシの輪郭カーブがありません"
            body_spline = obj.data.splines[0]
            assert len(body_spline.bezier_points) == 4, "下地が楕円カーブになっていません"
            assert _evaluated_polygon_count(obj) == 0, "本体カーブ側に表示面が残っています"
            white_obj = bpy.data.objects.get(balloon_line_mesh._flash_white_line_mesh_object_name(entry.id))
            line_obj = bpy.data.objects.get(balloon_line_mesh._line_mesh_object_name(entry.id))
            assert white_obj is not None and _evaluated_polygon_count(white_obj) > 0, "白線実体が作成されていません"
            assert line_obj is not None and _evaluated_polygon_count(line_obj) > 0, "黒線実体が作成されていません"
            body_samples = balloon_line_mesh._sample_body_bezier(body_spline, balloon_line_mesh.SAMPLES_PER_SEGMENT)
            line_samples = balloon_line_mesh._body_samples_for_line_mesh(entry, obj)
            _body_min, body_max = _sample_radius_range(body_samples)
            _line_min, line_max = _sample_radius_range(line_samples)
            assert abs(line_max - body_max) < max(1.0e-6, body_max * 0.03), "黒線が楕円下地から外れてトゲ状になっています"
            white_z = max((float(v.co.z) for v in white_obj.data.vertices), default=0.0)
            line_z = max((float(v.co.z) for v in line_obj.data.vertices), default=0.0)
            assert 0.0 < white_z < line_z, "白線が黒線と下地の間に配置されていません"
            entry.flash_white_line_enabled = False
            balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert bpy.data.objects.get(balloon_line_mesh._flash_white_line_mesh_object_name(entry.id)) is None
            entry.flash_white_line_enabled = True
            balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert bpy.data.objects.get(balloon_line_mesh._flash_white_line_mesh_object_name(entry.id)) is not None
            layer = export_balloon.render_balloon_layer(entry, canvas_height_px=1200, dpi=144)
            assert layer is not None, "フキダシを書き出せません"
            assert layer.image.size[0] > 0 and layer.image.size[1] > 0

        print("BNAME_BALLOON_FLASH_SHAPES_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
