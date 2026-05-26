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
        "bname_dev_balloon_curve_source",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_curve_source"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _evaluated_width(obj) -> float:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coords = [vertex.co for vertex in mesh.vertices]
        assert coords, "表示結果の頂点がありません"
        return max(co.x for co in coords) - min(co.x for co in coords)
    finally:
        evaluated.to_mesh_clear()


def _input_socket_names(modifier, *, visible_only: bool = False) -> set[str]:
    group = modifier.node_group
    assert group is not None
    return {
        str(getattr(item, "name", "") or "")
        for item in group.interface.items_tree
        if getattr(item, "item_type", "") == "SOCKET" and getattr(item, "in_out", "") == "INPUT"
        and (not visible_only or not bool(getattr(item, "hide_in_modifier", False)))
    }


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_curve_source_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonCurveSource.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_curve_source.core.work import get_work
        from bname_dev_balloon_curve_source.operators import balloon_op
        from bname_dev_balloon_curve_source.utils import balloon_curve_object
        from bname_dev_balloon_curve_source.utils import balloon_curve_render_nodes
        from bname_dev_balloon_curve_source.utils import balloon_curve_source_state
        from bname_dev_balloon_curve_source.utils import object_naming
        from bname_dev_balloon_curve_source.utils.layer_hierarchy import page_stack_key

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
        modifier = obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME)
        assert modifier is not None, "フキダシに軽量表示補助がありません"
        assert modifier.node_group is not None and modifier.node_group.name == balloon_curve_render_nodes.GROUP_NAME
        assert obj.get(balloon_curve_render_nodes.PROP_GN_KIND) == balloon_curve_render_nodes.KIND
        assert _evaluated_polygon_count(obj) > 0, "フキダシの表示結果が空です"
        socket_names = _input_socket_names(modifier)
        assert "線幅 (mm)" in socket_names
        assert "線素材" in socket_names
        assert "塗り素材" in socket_names
        visible_socket_names = _input_socket_names(modifier, visible_only=True)
        forbidden = {
            name
            for name in visible_socket_names
            if name.startswith("しっぽ") or "山の" in name or name == "形状"
        }
        assert not forbidden, f"使わない形状設定が軽量表示補助に残っています: {sorted(forbidden)}"

        entry.line_width_mm = 1.0
        balloon_curve_object.on_balloon_entry_changed(entry)
        bpy.context.view_layer.update()
        base_width = _evaluated_width(obj)
        widest_point = max(obj.data.splines[0].bezier_points, key=lambda point: float(point.co.x))
        widest_point.radius = 3.0
        bpy.context.view_layer.update()
        wider_width = _evaluated_width(obj)
        assert wider_width > base_width + 0.0003, "制御点ごとの線幅が表示結果へ反映されていません"

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
        assert _evaluated_polygon_count(obj_after) > 0, "再生成後のフキダシ表示が空です"

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
        result = bpy.ops.bname.balloon_register_selected_curve()
        assert "FINISHED" in result, result
        assert len(page.balloons) == before_count + 1, "選択カーブからフキダシが追加されていません"
        free_entry = page.balloons[-1]
        assert object_naming.get_kind(raw_obj) == "balloon", "選択カーブがフキダシ実体として登録されていません"
        assert object_naming.get_bname_id(raw_obj) == free_entry.id, "登録フキダシのIDが一致しません"
        assert balloon_curve_source_state.detect_state(raw_obj) == balloon_curve_source_state.STATE_FREEFORM
        assert raw_obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME) is not None
        free_point_x = float(raw_obj.data.splines[0].bezier_points[0].co.x)
        free_entry.width_mm += 10.0
        balloon_curve_object.on_balloon_entry_changed(free_entry)
        assert abs(float(raw_obj.data.splines[0].bezier_points[0].co.x) - free_point_x) < 1.0e-6, (
            "自由形状の制御点が詳細設定変更で上書きされました"
        )
        print("BNAME_BALLOON_CURVE_SOURCE_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
