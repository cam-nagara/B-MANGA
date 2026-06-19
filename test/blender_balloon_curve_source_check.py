"""Blender 実機用: フキダシが編集可能なカーブ実体として残ることを確認。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_balloon_curve_source",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_curve_source"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mesh_polygon_count(obj) -> int:
    return len(getattr(getattr(obj, "data", None), "polygons", []) or [])


def _mesh_area(obj) -> float:
    return sum(float(p.area) for p in getattr(getattr(obj, "data", None), "polygons", []) or [])


def _render_mesh_objects(module_prefixes, balloon_id: str):
    """フキダシ描画メッシュ (塗り / 主線) の実体オブジェクトを返す."""
    return [bpy.data.objects.get(f"{prefix}{balloon_id}") for prefix in module_prefixes]


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_curve_source_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonCurveSource.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_balloon_curve_source.core.work import get_work
        from bmanga_dev_balloon_curve_source.operators import balloon_op
        from bmanga_dev_balloon_curve_source.utils import balloon_curve_object
        from bmanga_dev_balloon_curve_source.utils import balloon_curve_render_nodes
        from bmanga_dev_balloon_curve_source.utils import balloon_curve_source_state
        from bmanga_dev_balloon_curve_source.utils import balloon_fill_mesh, balloon_line_mesh
        from bmanga_dev_balloon_curve_source.utils import object_naming
        from bmanga_dev_balloon_curve_source.utils.layer_hierarchy import page_stack_key

        mesh_prefixes = (
            balloon_fill_mesh.BALLOON_FILL_MESH_NAME_PREFIX,
            balloon_line_mesh.BALLOON_LINE_MESH_NAME_PREFIX,
        )

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=32.0,
            y=48.0,
            w=58.0,
            h=34.0,
            parent_kind="page",
            parent_key=page_stack_key(page),
        )

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None, "フキダシの実体が作成されていません"
        assert obj.type == "CURVE", f"フキダシがカーブ実体ではありません: {obj.type}"
        assert len(obj.data.splines) >= 1, "フキダシカーブに輪郭がありません"
        assert len(obj.data.materials) >= 2, "フキダシに線と塗りの素材がありません"
        # 現行仕様 (v0.6.132 以降): 描画は Python メッシュ焼き込みで行い、
        # 本体カーブに旧ジオメトリノードの表示補助は付かない。
        assert obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME) is None, (
            "旧ジオメトリノードの表示補助がフキダシ本体に残っています"
        )
        fill_obj, line_obj = _render_mesh_objects(mesh_prefixes, str(entry.id))
        assert fill_obj is not None, "フキダシの塗りメッシュがありません"
        assert _mesh_polygon_count(fill_obj) > 0, "フキダシの塗り表示が空です"
        assert line_obj is not None, "フキダシの主線メッシュがありません"
        assert _mesh_polygon_count(line_obj) > 0, "フキダシの主線表示が空です"
        assert len(getattr(fill_obj.data, "materials", []) or []) >= 1, "塗りメッシュに素材がありません"
        assert len(getattr(line_obj.data, "materials", []) or []) >= 1, "主線メッシュに素材がありません"

        # 「線の太さ」の変更が主線メッシュへ反映されること
        # (現行仕様: 主線は Shapely の均一ストロークで焼き込む。
        #  制御点ごとの太さ radius は主線では使わない)
        entry.line_width_mm = 0.3
        balloon_curve_object.on_balloon_entry_changed(entry)
        bpy.context.view_layer.update()
        _fill_obj, line_obj = _render_mesh_objects(mesh_prefixes, str(entry.id))
        base_area = _mesh_area(line_obj)
        assert base_area > 0.0, "主線メッシュの面積が取得できません"
        entry.line_width_mm = 1.0
        balloon_curve_object.on_balloon_entry_changed(entry)
        bpy.context.view_layer.update()
        _fill_obj, line_obj = _render_mesh_objects(mesh_prefixes, str(entry.id))
        wider_area = _mesh_area(line_obj)
        assert wider_area > base_area * 1.5, (
            f"線の太さの変更が主線メッシュへ反映されていません: {base_area} -> {wider_area}"
        )

        first_point = obj.data.splines[0].bezier_points[0]
        original_x = float(first_point.co.x)
        first_point.co.x += 0.004
        bpy.context.view_layer.update()
        assert balloon_curve_source_state.detect_state(obj) == balloon_curve_source_state.STATE_MANUAL
        entry.line_width_mm = 0.8
        balloon_curve_object.on_balloon_entry_changed(entry)
        obj_after = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        moved_x = float(obj_after.data.splines[0].bezier_points[0].co.x)
        assert abs(moved_x - (original_x + 0.004)) < 1.0e-6, "手編集した制御点が上書きされました"

        entry.width_mm = 62.0
        balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=entry,
            page=page,
            force_regenerate=True,
            preserve_manual_delta=True,
        )
        fill_obj, line_obj = _render_mesh_objects(mesh_prefixes, str(entry.id))
        assert fill_obj is not None and _mesh_polygon_count(fill_obj) > 0, "再生成後のフキダシ塗り表示が空です"
        assert line_obj is not None and _mesh_polygon_count(line_obj) > 0, "再生成後のフキダシ主線表示が空です"

        curve = bpy.data.curves.new("自由カーブ", "CURVE")
        curve.dimensions = "2D"
        spline = curve.splines.new("BEZIER")
        spline.bezier_points.add(3)
        spline.use_cyclic_u = True
        for point, co in zip(
            spline.bezier_points,
            (
                Vector((0.01, 0.02, 0.0)),
                Vector((0.052, 0.018, 0.0)),
                Vector((0.056, 0.044, 0.0)),
                Vector((0.012, 0.048, 0.0)),
            ),
            strict=True,
        ):
            point.co = co
            point.handle_left_type = "AUTO"
            point.handle_right_type = "AUTO"
        raw_obj = bpy.data.objects.new("自由カーブ", curve)
        bpy.context.collection.objects.link(raw_obj)
        bpy.ops.object.select_all(action="DESELECT")
        raw_obj.select_set(True)
        context.view_layer.objects.active = raw_obj
        before_count = len(page.balloons)
        result = bpy.ops.bmanga.balloon_register_selected_curve()
        assert "FINISHED" in result, result
        assert len(page.balloons) == before_count + 1, "選択カーブからフキダシが追加されていません"
        free_entry = page.balloons[-1]
        assert object_naming.get_kind(raw_obj) == "balloon", "選択カーブがフキダシ実体として登録されていません"
        assert object_naming.get_bmanga_id(raw_obj) == free_entry.id, "登録フキダシのIDが一致しません"
        assert balloon_curve_source_state.detect_state(raw_obj) == balloon_curve_source_state.STATE_FREEFORM
        assert raw_obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME) is None, (
            "登録カーブに旧ジオメトリノードの表示補助が付いています"
        )
        free_fill_obj, free_line_obj = _render_mesh_objects(mesh_prefixes, str(free_entry.id))
        assert free_fill_obj is not None and _mesh_polygon_count(free_fill_obj) > 0, (
            "登録フキダシの塗り表示が空です"
        )
        assert free_line_obj is not None and _mesh_polygon_count(free_line_obj) > 0, (
            "登録フキダシの主線表示が空です"
        )
        free_point_x = float(raw_obj.data.splines[0].bezier_points[0].co.x)
        free_entry.width_mm += 10.0
        balloon_curve_object.on_balloon_entry_changed(free_entry)
        assert abs(float(raw_obj.data.splines[0].bezier_points[0].co.x) - free_point_x) < 1.0e-6, (
            "自由形状の制御点が詳細設定変更で上書きされました"
        )
        print("BMANGA_BALLOON_CURVE_SOURCE_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
