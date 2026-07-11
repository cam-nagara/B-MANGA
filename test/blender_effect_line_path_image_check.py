"""Blender実機用: 効果線の基準パス編集と画像線を確認。"""

from __future__ import annotations

import importlib.util
import json
import base64
import math
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_effect_path_image",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_effect_path_image"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_png(path: Path) -> None:
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAQCAYAAACm53kpAAAASklEQVR4nO3SQQrAIBAEQe9/"
            "6daFEFqRbJvCYxgMWVFhZpOZzGxsBHB23rpp8vQJhQmE/QoKCgoKCgoKCgoKCgpKB4b1"
            "BCQdbp8JAAAAAElFTkSuQmCC"
        )
    )


def _material_has_image_and_mask(mat) -> tuple[bool, bool]:
    has_image = False
    has_mask = False
    for node in getattr(getattr(mat, "node_tree", None), "nodes", []) or []:
        if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
            has_image = True
        if getattr(node, "label", "") == "コマ内容マスク":
            has_mask = True
    return has_image, has_mask


def _poly_count(obj) -> int:
    data = getattr(obj, "data", None)
    return len(getattr(data, "polygons", []) or [])


def _mesh_signature(obj) -> tuple[tuple[float, float, float], ...]:
    data = getattr(obj, "data", None)
    return tuple(
        (round(float(vertex.co.x), 5), round(float(vertex.co.y), 5), round(float(vertex.co.z), 5))
        for vertex in (getattr(data, "vertices", []) or [])
    )


def _uv_values(obj) -> list[tuple[float, float]]:
    layer = getattr(getattr(obj, "data", None), "uv_layers", None)
    assert layer is not None and layer.active is not None, "画像線にUVがありません"
    return [(round(float(data.uv.x), 4), round(float(data.uv.y), 4)) for data in layer.active.data]


def _point_colors(obj) -> list[tuple[float, float, float, float]]:
    attr = getattr(getattr(obj, "data", None), "attributes", None)
    assert attr is not None, "色属性コンテナがありません"
    layer = attr.get("bmanga_path_content_color")
    assert layer is not None, "色属性がありません"
    return [tuple(data.color) for data in layer.data]


def _polygon_widths(obj) -> list[float]:
    widths: list[float] = []
    for poly in obj.data.polygons:
        xs = [float(obj.data.vertices[i].co.x) for i in poly.vertices]
        widths.append(max(xs) - min(xs))
    return widths


def _set_curve_points(source, points_mm: list[tuple[float, float]]) -> None:
    curve = source.data
    while len(curve.splines):
        curve.splines.remove(curve.splines[0])
    spline = curve.splines.new("POLY")
    spline.points.add(len(points_mm) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, points_mm, strict=False):
        point.co = (x_mm * 0.001, y_mm * 0.001, 0.0, 1.0)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_path_image_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        image_path = temp_root / "effect_line_image.png"
        _write_png(image_path)
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "EffectPathImage.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_effect_path_image.core.work import get_work
        from bmanga_dev_effect_path_image.operators import effect_line_op
        from bmanga_dev_effect_path_image.utils import coma_border_object, coma_plane
        from bmanga_dev_effect_path_image.utils import effect_line_object, effect_line_path
        from bmanga_dev_effect_path_image.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        if len(page.comas) == 0:
            assert "FINISHED" in bpy.ops.bmanga.coma_add()
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 25.0
        coma.rect_y_mm = 40.0
        coma.rect_width_mm = 120.0
        coma.rect_height_mm = 120.0
        coma_plane.ensure_coma_plane(scene, work, page, coma)
        coma_plane.ensure_coma_mask(scene, work, page, coma)
        coma_border_object.ensure_coma_border_object(scene, work, page, coma)

        params = scene.bmanga_effect_line_params
        effect_line_op._set_scene_params_syncing(scene, True)
        try:
            params.effect_type = "focus"
            params.spacing_mode = "angle"
            params.spacing_angle_deg = 18.0
            params.max_line_count = 32
            params.brush_size_mm = 0.5
            params.base_path_enabled = True
            params.line_image_path = str(image_path)
            params.line_image_draw_mode = "ribbon"
            params.line_image_brush_size_mm = 2.0
            params.line_image_aspect_ratio = 3.0
            params.line_image_spacing_percent = 100.0
            params.line_image_ribbon_repeat_mode = "repeat"
        finally:
            effect_line_op._set_scene_params_syncing(scene, False)

        parent_key = coma_stack_key(page, coma)
        obj, layer = effect_line_op._create_effect_layer(
            context,
            (45.0, 65.0, 70.0, 60.0),
            parent_key=parent_key,
        )
        assert obj is not None and layer is not None
        display = effect_line_object.find_effect_display_object(obj)
        assert display is not None, "効果線の表示実体がありません"
        source = effect_line_path.find_effect_base_path_object(obj)
        assert source is not None and source.type == "CURVE", "基準パスがカーブとして作られていません"
        image_obj = effect_line_path.find_effect_line_image_object(obj)
        assert image_obj is not None, "画像線の表示実体がありません"
        assert _poly_count(image_obj) > 0, "画像線メッシュが空です"
        assert image_obj.data.uv_layers.active is not None, "画像線にUVがありません"
        has_image, has_mask = _material_has_image_and_mask(image_obj.data.materials[0])
        assert has_image, "画像線素材に画像が接続されていません"
        assert has_mask, "コマ内の画像線にコマ内容マスクが接続されていません"
        ribbon_signature = _mesh_signature(image_obj)

        raw_points = json.loads(params.base_path_points_json)
        assert len(raw_points) >= 2, "基準パスの点が保存されていません"
        start = tuple(raw_points[0])
        end = tuple(raw_points[-1])
        mid = ((start[0] + end[0]) * 0.5, (start[1] + end[1]) * 0.5 + 18.0)
        _set_curve_points(source, [start, mid, end])
        assert effect_line_path.sync_from_base_path_object(scene, source), "基準パス編集が反映されません"
        image_obj = effect_line_path.find_effect_line_image_object(obj)
        assert image_obj is not None
        after_signature = _mesh_signature(image_obj)
        assert _poly_count(image_obj) > 0, "基準パス編集後の画像線メッシュが空です"
        assert after_signature != ribbon_signature, "曲げた基準パスが他の線へ反映されていません"
        saved = json.loads(effect_line_op._layer_params_data(obj, layer)["base_path_points_json"])
        assert len(saved) == 3, "編集後の基準パスが保存されていません"

        direction_obj = bpy.data.objects.new("画像線方向", None)
        scene.collection.objects.link(direction_obj)
        direction_obj.rotation_euler[2] = math.radians(45.0)
        effect_line_op._set_scene_params_syncing(scene, True)
        try:
            params.line_image_source = "image"
            params.line_image_draw_mode = "stamp"
            params.line_image_stamp_angle_mode = "object"
            params.line_image_stamp_angle_object_name = direction_obj.name
            params.line_image_angle_deg = 30.0
            params.line_image_inout_size_enabled = True
            params.line_image_inout_opacity_enabled = True
            params.line_image_inout_color_enabled = True
            params.in_percent = 25.0
            params.out_percent = 35.0
            params.in_start_percent = 50.0
            params.out_start_percent = 50.0
            params.line_image_inout_start_color = (1.0, 0.0, 0.0, 1.0)
            params.line_image_inout_end_color = (0.0, 0.0, 1.0, 0.35)
        finally:
            effect_line_op._set_scene_params_syncing(scene, False)
        effect_line_op._write_effect_strokes(context, obj, layer, (45.0, 65.0, 70.0, 60.0), params_override=params)
        image_obj = effect_line_path.find_effect_line_image_object(obj)
        assert image_obj is not None and _poly_count(image_obj) > 0, "スタンプ画像線が表示されていません"
        image_colors = _point_colors(image_obj)
        assert min(c[3] for c in image_colors) < max(c[3] for c in image_colors), "画像線の不透明度入り抜きが効いていません"
        assert any(c[0] > c[2] + 0.2 for c in image_colors), "画像線の入り色が反映されていません"
        assert any(c[2] > c[0] + 0.2 for c in image_colors), "画像線の抜き色が反映されていません"
        assert min(_polygon_widths(image_obj)) < max(_polygon_widths(image_obj)), "画像線のサイズ入り抜きが効いていません"

        effect_line_op._set_scene_params_syncing(scene, True)
        try:
            params.line_image_inout_size_enabled = False
            params.line_image_inout_opacity_enabled = False
            params.line_image_inout_color_enabled = False
            params.line_image_draw_mode = "ribbon"
            params.line_image_ribbon_repeat_mode = "stretch"
            params.line_image_angle_deg = 0.0
        finally:
            effect_line_op._set_scene_params_syncing(scene, False)
        effect_line_op._write_effect_strokes(context, obj, layer, (45.0, 65.0, 70.0, 60.0), params_override=params)
        image_obj = effect_line_path.find_effect_line_image_object(obj)
        assert image_obj is not None and _poly_count(image_obj) > 0, "リボン画像線が表示されていません"
        assert len(set(_uv_values(image_obj))) > 2, "リボン画像線のUVが更新されていません"

        effect_line_op._set_scene_params_syncing(scene, True)
        try:
            params.line_image_source = "shape"
            params.line_image_draw_mode = "stamp"
            params.line_image_shape_kind = "circle"
            params.line_image_shape_sides = 6
            params.line_image_color = (0.0, 0.0, 0.0, 1.0)
            params.line_image_inout_size_enabled = True
            params.line_image_inout_opacity_enabled = True
            params.line_image_inout_color_enabled = True
            params.in_percent = 10.0
            params.out_percent = 40.0
            params.in_start_percent = 50.0
            params.out_start_percent = 50.0
            params.line_image_inout_start_color = (0.0, 1.0, 0.0, 1.0)
            params.line_image_inout_end_color = (1.0, 0.0, 1.0, 0.3)
        finally:
            effect_line_op._set_scene_params_syncing(scene, False)
        # 円形とハートは v0.6.408 で輪郭を滑らかにするため64点化済み。
        expected_vertices = {"circle": 64, "square": 4, "polygon": 6, "star": 10, "heart": 64}
        for kind, vertex_count in expected_vertices.items():
            effect_line_op._set_scene_params_syncing(scene, True)
            try:
                params.line_image_shape_kind = kind
                params.line_image_shape_sides = 6
            finally:
                effect_line_op._set_scene_params_syncing(scene, False)
            effect_line_op._write_effect_strokes(context, obj, layer, (45.0, 65.0, 70.0, 60.0), params_override=params)
            image_obj = effect_line_path.find_effect_line_image_object(obj)
            assert image_obj is not None and _poly_count(image_obj) > 0, f"{kind} の生成形状線が表示されていません"
            assert len(image_obj.data.polygons[0].vertices) == vertex_count, f"{kind} の頂点数が不正です"
        effect_line_op._set_scene_params_syncing(scene, True)
        try:
            params.line_image_shape_kind = "polygon"
            params.line_image_shape_sides = 7
        finally:
            effect_line_op._set_scene_params_syncing(scene, False)
        effect_line_op._write_effect_strokes(context, obj, layer, (45.0, 65.0, 70.0, 60.0), params_override=params)
        image_obj = effect_line_path.find_effect_line_image_object(obj)
        assert image_obj is not None
        assert len(image_obj.data.polygons[0].vertices) == 7, "効果線の多角形角数が反映されていません"
        shape_colors = _point_colors(image_obj)
        assert min(c[3] for c in shape_colors) < max(c[3] for c in shape_colors), "生成形状線の不透明度入り抜きが効いていません"
        assert any(c[1] > c[0] + 0.2 and c[1] > c[2] + 0.2 for c in shape_colors), "生成形状線の入り色が反映されていません"
        assert any(c[0] > c[1] + 0.2 and c[2] > c[1] + 0.2 for c in shape_colors), "生成形状線の抜き色が反映されていません"
        assert min(_polygon_widths(image_obj)) < max(_polygon_widths(image_obj)), "生成形状線のサイズ入り抜きが効いていません"
        saved_params = effect_line_op._layer_params_data(obj, layer)
        assert saved_params["line_image_source"] == "shape"
        assert saved_params["line_image_shape_sides"] == 7

        layer.hide = True
        effect_line_op._write_effect_strokes(context, obj, layer, (45.0, 65.0, 70.0, 60.0), params_override=params)
        image_obj = effect_line_path.find_effect_line_image_object(obj)
        assert image_obj is not None and image_obj.hide_viewport, "効果線非表示が画像線へ反映されていません"
        source = effect_line_path.find_effect_base_path_object(obj)
        assert source is not None and source.hide_viewport and source.hide_select, "効果線非表示が基準パスへ反映されていません"
        assert display.hide_viewport, "効果線非表示が表示実体へ反映されていません"

        print("BMANGA_EFFECT_LINE_PATH_IMAGE_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
