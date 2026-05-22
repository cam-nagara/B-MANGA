"""Blender 実機用: ジオメトリノード設定が表示結果へ効くことを確認。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_gn_functional",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_gn_functional"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mesh_stats(obj) -> dict:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        verts = [(round(v.co.x, 5), round(v.co.y, 5), round(v.co.z, 5)) for v in mesh.vertices]
        if verts:
            xs = [v[0] for v in verts]
            ys = [v[1] for v in verts]
            bounds = (min(xs), min(ys), max(xs), max(ys))
        else:
            bounds = (0.0, 0.0, 0.0, 0.0)
        material_counts: dict[int, int] = {}
        for poly in mesh.polygons:
            material_counts[int(poly.material_index)] = material_counts.get(int(poly.material_index), 0) + 1
        material_names = {}
        for index, count in material_counts.items():
            name = ""
            if 0 <= index < len(mesh.materials) and mesh.materials[index] is not None:
                name = mesh.materials[index].name
            material_names[name] = material_names.get(name, 0) + count
        return {
            "verts": len(mesh.vertices),
            "polys": len(mesh.polygons),
            "bounds": bounds,
            "materials": material_counts,
            "material_names": material_names,
            "hash": hash(tuple(verts[:400]) + tuple(sorted(material_counts.items()))),
        }
    finally:
        evaluated.to_mesh_clear()


def _assert_changed(before: dict, after: dict, label: str) -> None:
    if before["hash"] == after["hash"] and before["bounds"] == after["bounds"] and before["polys"] == after["polys"]:
        raise AssertionError(f"{label} が表示結果に反映されていません")


def _assert_material_alpha(obj, slot: int, expected: float, label: str, eps: float = 1.0e-4) -> None:
    mat = obj.data.materials[slot]
    actual = float(mat.diffuse_color[3])
    if abs(actual - expected) > eps:
        raise AssertionError(f"{label}: expected alpha {expected}, got {actual}")


def _modifier_input_value(obj, modifier_name: str, socket_name: str):
    modifier = obj.modifiers.get(modifier_name)
    if modifier is None or modifier.node_group is None:
        return None
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "in_out", "") != "INPUT":
            continue
        if getattr(item, "name", "") != socket_name:
            continue
        return modifier.get(item.identifier)
    return None


def _material_name_count(stats: dict, pattern: str) -> int:
    return sum(count for name, count in stats.get("material_names", {}).items() if pattern in name)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_gn_functional_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "GeometryNodesFunctional.bname"))
        assert "FINISHED" in result, result

        from bname_dev_gn_functional.core.work import get_work
        from bname_dev_gn_functional.operators import balloon_op, effect_line_op
        from bname_dev_gn_functional.utils import balloon_curve_object, effect_line_object
        from bname_dev_gn_functional.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        balloon = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=20.0,
            y=30.0,
            w=50.0,
            h=24.0,
            parent_kind="page",
            parent_key=page_key,
        )
        balloon_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        assert balloon_obj is not None
        ellipse_stats = _mesh_stats(balloon_obj)

        balloon.shape = "rect"
        balloon.rounded_corner_enabled = False
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        rect_stats = _mesh_stats(balloon_obj)
        _assert_changed(ellipse_stats, rect_stats, "フキダシ 形状")

        balloon.rounded_corner_enabled = True
        balloon.rounded_corner_radius_mm = 8.0
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        rounded_stats = _mesh_stats(balloon_obj)
        _assert_changed(rect_stats, rounded_stats, "フキダシ 角丸")

        balloon.shape = "cloud"
        balloon.shape_params.cloud_bump_height_mm = 2.0
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        cloud_low = _mesh_stats(balloon_obj)
        balloon.shape_params.cloud_bump_height_mm = 9.0
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        cloud_high = _mesh_stats(balloon_obj)
        _assert_changed(cloud_low, cloud_high, "フキダシ 山の高さ")

        balloon.tails.clear()
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        no_tail = _mesh_stats(balloon_obj)
        tail = balloon.tails.add()
        tail.type = "straight"
        tail.direction_deg = 270.0
        tail.length_mm = 20.0
        tail.root_width_mm = 6.0
        tail.tip_width_mm = 0.0
        tail.curve_bend = 0.6
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        tail_stats = _mesh_stats(balloon_obj)
        if tail_stats["bounds"][1] >= no_tail["bounds"][1] - 0.005:
            raise AssertionError("フキダシ しっぽの長さが表示範囲へ反映されていません")

        tail.type = "curve"
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        tail_curve_stats = _mesh_stats(balloon_obj)
        _assert_changed(tail_stats, tail_curve_stats, "フキダシ しっぽ 曲線")

        tail.type = "sticky"
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        tail_sticky_stats = _mesh_stats(balloon_obj)
        _assert_changed(tail_curve_stats, tail_sticky_stats, "フキダシ しっぽ 付箋")

        balloon.rotation_deg = 31.0
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        rotated_stats = _mesh_stats(balloon_obj)
        _assert_changed(tail_sticky_stats, rotated_stats, "フキダシ 回転")

        balloon.line_color = (0.2, 0.4, 0.6, 1.0)
        balloon.fill_opacity = 0.35
        balloon.opacity = 0.8
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
        _assert_material_alpha(balloon_obj, 0, 0.8, "フキダシ 線の不透明度")
        _assert_material_alpha(balloon_obj, 1, 0.28, "フキダシ 塗りの不透明度")

        params = context.scene.bname_effect_line_params
        params.effect_type = "focus"
        params.spacing_mode = "angle"
        params.spacing_angle_deg = 30.0
        params.max_line_count = 200
        params.brush_size_mm = 0.5
        params.fill_base_shape = False
        effect_obj, effect_layer = effect_line_op._create_effect_layer(context, (20.0, 40.0, 60.0, 48.0), parent_key=page_key)
        assert effect_obj is not None and effect_layer is not None
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        display = effect_line_object.find_effect_display_object(effect_obj)
        assert display is not None
        focus_sparse = _mesh_stats(display)

        params.spacing_angle_deg = 10.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        focus_dense = _mesh_stats(display)
        if focus_dense["polys"] <= focus_sparse["polys"]:
            raise AssertionError("効果線 線の間隔が本数へ反映されていません")
        if _modifier_input_value(display, "B-Name Geometry Nodes", "密度補正") is not None:
            raise AssertionError("効果線 密度補正が独立した設定欄として残っています")

        params.spacing_mode = "distance"
        params.spacing_distance_mm = 6.0
        params.start_frame_density_basis = "frame"
        params.start_frame_density_rounding_percent = 0.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        distance_sparse = _mesh_stats(display)
        params.spacing_distance_mm = 1.5
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        distance_dense = _mesh_stats(display)
        if distance_dense["polys"] <= distance_sparse["polys"]:
            raise AssertionError("効果線 距離指定の線間隔がノード内本数計算へ反映されていません")
        params.start_frame_density_basis = "ellipse"
        params.start_frame_density_rounding_percent = 100.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        distance_density_basis = _mesh_stats(display)
        _assert_changed(distance_dense, distance_density_basis, "効果線 距離指定の密度基準")

        params.fill_base_shape = True
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        focus_fill = _mesh_stats(display)
        if _material_name_count(focus_fill, "_Fill_") <= 0:
            fill_value = _modifier_input_value(display, "B-Name Geometry Nodes", "終点形状を下地として塗る")
            raise AssertionError(f"効果線 終点形状の下地塗りが表示されていません: value={fill_value}, stats={focus_fill}")

        params.end_shape = "rect"
        params.end_rounded_corner_enabled = False
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        end_rect = _mesh_stats(display)
        _assert_changed(focus_fill, end_rect, "効果線 終点形状 矩形")

        params.end_rounded_corner_enabled = True
        params.end_rounded_corner_radius_mm = 8.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        end_rounded = _mesh_stats(display)
        _assert_changed(end_rect, end_rounded, "効果線 終点形状 角丸")

        params.end_shape = "octagon"
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        end_octagon = _mesh_stats(display)
        _assert_changed(end_rounded, end_octagon, "効果線 終点形状 八角形")

        params.effect_type = "speed"
        params.speed_line_count = 9
        params.speed_angle_deg = 0.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        speed_0 = _mesh_stats(display)
        _assert_changed(focus_fill, speed_0, "効果線 流線")
        params.speed_angle_deg = 35.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        speed_35 = _mesh_stats(display)
        _assert_changed(speed_0, speed_35, "効果線 流線の角度")
        params.inout_apply = "brush_size"
        params.in_percent = 100.0
        params.out_percent = 100.0
        params.in_start_percent = 50.0
        params.out_start_percent = 50.0
        params.inout_range_mode = "percent"
        params.in_range_percent = 100.0
        params.out_range_percent = 100.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        speed_full_width = _mesh_stats(display)
        params.in_percent = 15.0
        params.out_percent = 20.0
        params.in_start_percent = 35.0
        params.out_start_percent = 30.0
        params.in_range_percent = 50.0
        params.out_range_percent = 55.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        speed_tapered = _mesh_stats(display)
        _assert_changed(speed_full_width, speed_tapered, "効果線 入り抜き")

        params.effect_type = "beta_flash"
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        beta = _mesh_stats(display)
        if _material_name_count(beta, "_Fill_") <= 0:
            raise AssertionError("効果線 ベタフラの塗りが表示されていません")

        params.effect_type = "white_outline"
        params.white_outline_count = 4
        params.white_outline_angle_deg = 12.0
        params.white_outline_black_brush_mm = 1.0
        params.white_outline_white_brush_mm = 0.35
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        white_4 = _mesh_stats(display)
        params.white_outline_count = 9
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        white_9 = _mesh_stats(display)
        if white_9["polys"] <= white_4["polys"]:
            raise AssertionError("効果線 白抜き線の本数が表示結果へ反映されていません")
        if _material_name_count(white_9, "_Line_") <= 0 or _material_name_count(white_9, "_Fill_") <= 0:
            raise AssertionError("効果線 白抜き線の黒線/白線が両方表示されていません")
        params.white_outline_spacing_mm = 3.0
        params.white_outline_width_mm = 18.0
        params.white_outline_width_jitter_enabled = True
        params.white_outline_width_min_percent = 35.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        white_width_spacing = _mesh_stats(display)
        _assert_changed(white_9, white_width_spacing, "効果線 白抜き線の間隔と太さ")
        params.white_outline_length_jitter_enabled = True
        params.white_outline_length_min_percent = 45.0
        params.white_outline_white_ratio_percent = 70.0
        params.white_outline_white_attenuation = 4.0
        params.white_outline_black_attenuation = -3.0
        effect_line_op._write_effect_strokes(context, effect_obj, effect_layer, (20.0, 40.0, 60.0, 48.0), seed=8, params_override=params)
        white_detail = _mesh_stats(display)
        _assert_changed(white_width_spacing, white_detail, "効果線 白抜き線の長さ・割合・減衰")

        print("BNAME_GEOMETRY_NODES_FUNCTIONAL_SETTINGS_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
