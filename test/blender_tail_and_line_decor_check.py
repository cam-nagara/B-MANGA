"""Blender 実機チェック: しっぽ拡張 (楕円/曲線/ツール/プリセット) と線種 (図形/画像/NURBS).

- しっぽ線種「楕円」: 楕円チェーンのメッシュ生成・出力描画・本体と結合しないこと
- しっぽの折れ線⇔曲線変換
- しっぽプリセットの保存・適用・削除
- フキダシ線種「図形」「画像」のメッシュ生成と出力
- NURBSカーブのフキダシ登録 (サンプリング・輪郭キャッシュ)
- ツールボタンのアイコン名が Blender に存在すること
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_tail_line_decor"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _poly_count(name: str) -> int:
    obj = bpy.data.objects.get(name)
    if obj is None or getattr(obj, "data", None) is None:
        return -1
    return len(obj.data.polygons)


def _make_balloon(page, balloon_op, *, x=60.0, y=150.0, w=50.0, h=40.0):
    entry = balloon_op._create_balloon_entry(
        bpy.context,
        page,
        shape="ellipse",
        x=x,
        y=y,
        w=w,
        h=h,
        parent_kind="page",
        parent_key=str(page.id),
    )
    assert entry is not None
    return entry


def _check_ellipse_tail(page, entry, balloon_op) -> None:
    balloon_curve_object = _sub("utils.balloon_curve_object")
    balloon_tail_geom = _sub("utils.balloon_tail_geom")

    tail_index = balloon_op._add_tail_polyline(entry, [(85.0, 148.0), (95.0, 120.0)])
    assert tail_index >= 0
    tail = entry.tails[tail_index]
    tail.line_type = "ellipse_chain"
    tail.ellipse_gap_mm = 2.0
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)
    bid = str(entry.id)
    assert _poly_count(f"balloon_tail_ellipse_fill_{bid}") > 0, "楕円しっぽの塗りメッシュがありません"
    assert _poly_count(f"balloon_tail_ellipse_line_{bid}") > 0, "楕円しっぽの線メッシュがありません"
    # 楕円チェーンはくさび多角形を持たない (本体と結合しない)
    from bmanga_dev_tail_line_decor.utils.balloon_shapes import Rect

    rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    assert balloon_tail_geom.polygon_for_tail(rect, tail) == []
    ellipses = balloon_tail_geom.ellipse_chain_for_tail(rect, tail)
    assert len(ellipses) >= 2, f"楕円が少なすぎます: {len(ellipses)}"
    # 先端へ向かって小さくなる (太さ連動)
    assert ellipses[0][3] > ellipses[-1][3], "楕円が先細りになっていません"

    # 三角(くさび) に戻すと楕円メッシュは撤去される
    tail.line_type = "wedge"
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)
    assert _poly_count(f"balloon_tail_ellipse_fill_{bid}") == -1, "楕円メッシュが残っています"
    tail.line_type = "ellipse_chain"
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)
    print("ELLIPSE_TAIL_OK", flush=True)


def _check_curve_mode(page, entry, balloon_op) -> None:
    balloon_tail_geom = _sub("utils.balloon_tail_geom")
    from bmanga_dev_tail_line_decor.utils.balloon_shapes import Rect

    tail_index = balloon_op._add_tail_polyline(entry, [(70.0, 148.0), (60.0, 130.0)])
    tail = entry.tails[tail_index]
    balloon_tail_geom.add_polyline_point(tail, (50.0, 120.0))
    rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    poly_points = len(balloon_tail_geom.polygon_for_tail(rect, tail))
    result = bpy.ops.bmanga.balloon_tail_set_curve_mode(
        page_id=str(page.id), balloon_id=str(entry.id), tail_index=tail_index, mode="curve"
    )
    assert "FINISHED" in result, result
    assert str(tail.curve_mode) == "curve"
    curve_points = len(balloon_tail_geom.polygon_for_tail(rect, tail))
    assert curve_points > poly_points, (poly_points, curve_points)
    result = bpy.ops.bmanga.balloon_tail_set_curve_mode(
        page_id=str(page.id), balloon_id=str(entry.id), tail_index=tail_index, mode="polyline"
    )
    assert "FINISHED" in result and str(tail.curve_mode) == "polyline"
    entry.tails.remove(tail_index)
    print("CURVE_MODE_OK", flush=True)


def _check_tail_presets(page, entry) -> None:
    tail_presets = _sub("io.tail_presets")
    work = bpy.context.scene.bmanga_work
    work_dir = Path(str(work.work_dir))
    tail = entry.tails[0]
    tail.ellipse_gap_mm = 3.21
    path = tail_presets.save_local_preset(work_dir, tail, "テストしっぽ", "テスト用")
    assert path.is_file()
    names = [p.name for p in tail_presets.list_all_presets(work_dir)]
    assert "テストしっぽ" in names, names
    assert "心の声 (楕円)" in names, names  # 同梱プリセット
    # 別のしっぽへ適用
    other = entry.tails.add()
    other.line_type = "wedge"
    preset = tail_presets.load_preset_by_name("テストしっぽ", work_dir)
    tail_presets.apply_preset_to_tail(preset, other)
    assert str(other.line_type) == "ellipse_chain"
    assert abs(float(other.ellipse_gap_mm) - 3.21) < 1.0e-4
    entry.tails.remove(len(entry.tails) - 1)
    assert tail_presets.delete_local_preset(work_dir, "テストしっぽ")
    print("TAIL_PRESETS_OK", flush=True)


def _check_line_decor(page, entry) -> None:
    balloon_curve_object = _sub("utils.balloon_curve_object")
    bid = str(entry.id)
    entry.line_width_mm = 1.5
    entry.line_style = "shape"
    entry.line_shape_kind = "star"
    entry.line_shape_spacing_mm = 2.0
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)
    assert _poly_count(f"balloon_line_shape_{bid}") > 0, "図形線メッシュがありません"
    assert _poly_count(f"balloon_line_mesh_{bid}") <= 0, "図形線種で従来の主線が残っています"

    # 画像線種: テスト用 PNG を作って指定
    from PIL import Image

    img_path = Path(tempfile.gettempdir()) / "bmanga_line_decor_test.png"
    Image.new("RGBA", (64, 16), (255, 0, 0, 255)).save(img_path)
    entry.line_style = "image"
    entry.line_image_path = str(img_path)
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)
    assert _poly_count(f"balloon_line_image_{bid}") > 0, "画像線メッシュがありません"
    image_obj = bpy.data.objects.get(f"balloon_line_image_{bid}")
    assert image_obj.data.uv_layers.active is not None, "画像線メッシュに UV がありません"
    assert _poly_count(f"balloon_line_shape_{bid}") == -1, "画像線種で図形メッシュが残っています"

    entry.line_style = "solid"
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)
    assert _poly_count(f"balloon_line_image_{bid}") == -1, "実線に戻しても画像線メッシュが残っています"
    assert _poly_count(f"balloon_line_mesh_{bid}") > 0, "実線へ戻したのに主線メッシュがありません"
    print("LINE_DECOR_OK", flush=True)


def _check_export(page, entry) -> None:
    export_balloon = _sub("io.export_balloon")
    # 楕円しっぽ + 実線
    entry.line_style = "solid"
    layer = export_balloon.render_balloon_layer(entry, 2048, 96)
    assert layer is not None and layer.image is not None
    # 図形線種
    entry.line_style = "shape"
    layer = export_balloon.render_balloon_layer(entry, 2048, 96)
    assert layer is not None and layer.image is not None
    # 画像線種
    entry.line_style = "image"
    layer = export_balloon.render_balloon_layer(entry, 2048, 96)
    assert layer is not None and layer.image is not None
    entry.line_style = "solid"
    print("EXPORT_OK", flush=True)


def _check_nurbs_balloon(page) -> None:
    balloon_curve_object = _sub("utils.balloon_curve_object")
    balloon_shapes = _sub("utils.balloon_shapes")
    geom = _sub("utils.geom")
    page_grid = _sub("utils.page_grid")

    work = bpy.context.scene.bmanga_work
    ox, oy = page_grid.page_total_offset_mm(work, bpy.context.scene, 0)
    pts = [(150.0, 250.0), (190.0, 245.0), (200.0, 280.0), (170.0, 300.0), (145.0, 280.0)]
    curve = bpy.data.curves.new("NURBSフキダシテスト", "CURVE")
    curve.dimensions = "2D"
    spline = curve.splines.new("NURBS")
    spline.points.add(len(pts) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, pts, strict=False):
        point.co = (geom.mm_to_m(ox + x_mm), geom.mm_to_m(oy + y_mm), 0.0, 1.0)
    spline.use_cyclic_u = True
    spline.order_u = 4
    obj = bpy.data.objects.new("NURBSフキダシテスト", curve)
    bpy.context.scene.collection.objects.link(obj)
    for selected in list(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    before = len(page.balloons)
    result = bpy.ops.bmanga.balloon_register_selected_curve()
    assert "FINISHED" in result, result
    assert len(page.balloons) == before + 1
    entry = page.balloons[-1]
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)
    bid = str(entry.id)
    assert _poly_count(f"balloon_fill_mesh_{bid}") > 0, "NURBSフキダシの塗りが空です"
    assert _poly_count(f"balloon_line_mesh_{bid}") > 0, "NURBSフキダシの主線が空です"
    # 自由形状の輪郭キャッシュが保存され、出力用の輪郭計算が実形状を返す
    cached = str(getattr(entry, "custom_outline_json", "") or "")
    assert cached, "自由形状の輪郭キャッシュがありません"
    from bmanga_dev_tail_line_decor.utils.balloon_shapes import Rect

    rect = Rect(float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm))
    outline = balloon_shapes.outline_for_entry(entry, rect)
    assert len(outline) >= 8, f"輪郭キャッシュからの outline が短すぎます: {len(outline)}"
    print("NURBS_BALLOON_OK", flush=True)


def _check_tool_icons_and_presets() -> None:
    icons = set(
        bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items.keys()
    )
    used = {
        "RESTRICT_SELECT_OFF", "OUTLINER_OB_GREASEPENCIL", "BRUSH_DATA",
        "MESH_PLANE", "MESH_GRID", "EMPTY_ARROWS", "MESH_CIRCLE",
        "CURVE_NCIRCLE", "SHARPCURVE", "FONT_DATA", "FORCE_FORCE",
        "IPO_LINEAR", "IPO_BEZIER", "PRESET", "FILE_TICK", "TRASH", "PREFERENCES",
    }
    missing = used - icons
    assert not missing, f"存在しないアイコン名: {sorted(missing)}"
    # ツールプリセット選択の登録と解決
    wm = bpy.context.window_manager
    assert hasattr(wm, "bmanga_tail_preset_selector"), "しっぽプリセット選択が未登録です"
    assert hasattr(wm, "bmanga_balloon_tool_preset_selector"), "フキダシ形状選択が未登録です"
    preset_op = _sub("operators.preset_op")
    wm.bmanga_balloon_tool_preset_selector = "shape:cloud"
    assert preset_op.selected_balloon_tool_shape(bpy.context) == ("cloud", "")
    # 新ツールのオペレーター登録
    assert hasattr(bpy.ops.bmanga, "balloon_tail_tool")
    assert hasattr(bpy.ops.bmanga, "balloon_nurbs_tool")
    assert hasattr(bpy.ops.bmanga, "balloon_tail_detail_open")
    print("ICONS_AND_PRESETS_OK", flush=True)


def _check_schema_roundtrip(page, entry) -> None:
    schema = _sub("io.schema")
    entry.tails[0].line_type = "ellipse_chain"
    entry.tails[0].ellipse_gap_mm = 2.5
    entry.tails[0].curve_mode = "curve"
    entry.line_style = "shape"
    entry.line_shape_kind = "heart"
    data = schema.balloon_entry_to_dict(entry)
    assert data["tails"][0]["lineType"] == "ellipse_chain"
    assert abs(float(data["tails"][0]["ellipseGapMm"]) - 2.5) < 1.0e-4
    assert data["tails"][0]["curveMode"] == "curve"
    assert data["lineShapeKind"] == "heart"
    clone = page.balloons.add()
    schema.balloon_entry_from_dict(clone, data)
    assert str(clone.tails[0].line_type) == "ellipse_chain"
    assert str(clone.tails[0].curve_mode) == "curve"
    assert str(clone.line_shape_kind) == "heart"
    page.balloons.remove(len(page.balloons) - 1)
    entry.line_style = "solid"
    print("SCHEMA_ROUNDTRIP_OK", flush=True)


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_tail_decor_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TailDecor.bmanga"))
    assert result == {"FINISHED"}, result
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    balloon_op = _sub("operators.balloon_op")
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    entry = _make_balloon(page, balloon_op)

    _check_ellipse_tail(page, entry, balloon_op)
    _check_curve_mode(page, entry, balloon_op)
    _check_tail_presets(page, entry)
    _check_line_decor(page, entry)
    _check_export(page, entry)
    _check_nurbs_balloon(page)
    _check_tool_icons_and_presets()
    _check_schema_roundtrip(page, entry)
    print("BMANGA_TAIL_AND_LINE_DECOR_CHECK_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os._exit(1)
    os._exit(0)
