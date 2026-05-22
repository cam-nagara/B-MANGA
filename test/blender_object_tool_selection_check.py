"""Blender実機用: オブジェクトツール選択とレイヤーリスト表示の確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _add_gp_layer(context, parent_key: str):
    from bname_dev.utils import gp_layer_parenting as gp_parent
    from bname_dev.utils import gpencil as gp_utils
    from bname_dev.utils.geom import mm_to_m

    obj = gp_utils.ensure_master_gpencil(context.scene)
    layer = obj.data.layers.new("object_tool_gp")
    gp_parent.set_parent_key(layer, parent_key)
    frame = gp_utils.ensure_active_frame(layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    ok = gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(24.0), mm_to_m(24.0), 0.0),
            (mm_to_m(40.0), mm_to_m(35.0), 0.0),
        ],
    )
    assert ok
    return layer


def _add_balloon(page, parent_key: str):
    entry = page.balloons.add()
    entry.id = "object_tool_balloon"
    entry.shape = "ellipse"
    entry.x_mm = 28.0
    entry.y_mm = 28.0
    entry.width_mm = 22.0
    entry.height_mm = 16.0
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    return entry


def _add_page_balloon(page, parent_key: str):
    entry = page.balloons.add()
    entry.id = "object_tool_page_balloon"
    entry.shape = "ellipse"
    entry.x_mm = 6.0
    entry.y_mm = 8.0
    entry.width_mm = 20.0
    entry.height_mm = 14.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_text(page, parent_key: str):
    entry = page.texts.add()
    entry.id = "object_tool_text"
    entry.body = "テスト"
    entry.x_mm = 32.0
    entry.y_mm = 32.0
    entry.width_mm = 20.0
    entry.height_mm = 18.0
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    return entry


def _add_image(context, parent_key: str):
    entry = context.scene.bname_image_layers.add()
    entry.id = "object_tool_image"
    entry.title = "画像"
    entry.x_mm = 34.0
    entry.y_mm = 34.0
    entry.width_mm = 18.0
    entry.height_mm = 12.0
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    return entry


def _add_raster(context, parent_key: str):
    result = bpy.ops.bname.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False)
    assert "FINISHED" in result, result
    entry = context.scene.bname_raster_layers[context.scene.bname_active_raster_layer_index]
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    from bname_dev.operators import raster_layer_op

    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=True)
    assert image is not None
    pixels = [0.0] * (int(image.size[0]) * int(image.size[1]) * 4)
    mid = ((int(image.size[1]) // 2) * int(image.size[0]) + int(image.size[0]) // 2) * 4
    pixels[mid:mid + 4] = [0.0, 0.0, 0.0, 1.0]
    image.pixels[:] = pixels
    image.update()
    entry["bname_raster_dirty"] = False
    return entry


def _visible_stack_kinds(context):
    from bname_dev.panels import gpencil_panel
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None and len(stack) > 0
    fake_ui = SimpleNamespace(bitflag_filter_item=1)
    flags, _order = gpencil_panel.BNAME_UL_layer_stack.filter_items(
        fake_ui,
        context,
        context.scene,
        "bname_layer_stack",
    )
    return [getattr(item, "kind", "") for item, flag in zip(stack, flags) if flag]


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_object_tool_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "ObjectTool.bname"))
        assert "FINISHED" in result, result

        from bname_dev.operators import effect_line_op
        from bname_dev.ui import overlay_effect_line, overlay_visibility
        from bname_dev.utils import balloon_shapes
        from bname_dev.utils import layer_stack as layer_stack_utils
        from bname_dev.utils import object_naming as on
        from bname_dev.utils import effect_line_object
        from bname_dev.utils import object_selection
        from bname_dev.utils import text_real_object
        from bname_dev.utils.geom import Rect
        from bname_dev.utils.layer_hierarchy import coma_stack_key, page_stack_key
        from bname_dev.operators import effect_line_gen, object_tool_op, object_tool_selection, page_op

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        panel = page.comas[0]
        panel.shape_type = "rect"
        panel.rect_x_mm = 20.0
        panel.rect_y_mm = 40.0
        panel.rect_width_mm = 160.0
        panel.rect_height_mm = 210.0
        coma_key = coma_stack_key(page, panel)
        object_key = object_selection.coma_key(page, panel)

        tool = SimpleNamespace()
        for method_name in (
            "_clear_drag_state",
            "_clear_click_state",
            "_handle_left_press",
            "_is_manual_coma_double_click",
            "_remember_coma_click",
            "_coma_open_hit_from_hit",
        ):
            setattr(
                tool,
                method_name,
                MethodType(getattr(object_tool_op.BNAME_OT_object_tool, method_name), tool),
            )
        tool._clear_drag_state()
        tool._clear_click_state()
        opened_hits = []
        original_hit_coma = object_tool_op._hit_coma_at_event
        original_extend = object_tool_op.coma_edge_move_op.extend_selected_handle_at_event
        object_tool_op._hit_coma_at_event = lambda _ctx, _event: {
            "kind": "coma",
            "page": 0,
            "coma": 0,
            "part": "body",
            "key": object_key,
        }
        object_tool_op.coma_edge_move_op.extend_selected_handle_at_event = lambda _ctx, _event: False
        tool._hit_object = object_tool_op._hit_coma_at_event
        tool._start_marquee_select = lambda _ctx, _event, _mode: True
        tool._activate_hit = lambda _ctx, _hit, *, mode: None
        tool._start_point_for_hit = lambda _ctx, _event, _hit: (10.0, 10.0)
        tool._start_object_drag = lambda _ctx, _hit, _x, _y: None
        tool._try_enter_coma_from_hit = lambda _ctx, hit: opened_hits.append(hit) or True
        try:
            event1 = SimpleNamespace(type="LEFTMOUSE", value="PRESS", mouse_x=120, mouse_y=130)
            event2 = SimpleNamespace(type="LEFTMOUSE", value="PRESS", mouse_x=123, mouse_y=132)
            assert tool._handle_left_press(context, event1) == {"RUNNING_MODAL"}
            assert not opened_hits, "1回目のクリックでコマファイルを開こうとしています"
            assert tool._handle_left_press(context, event2) == {"FINISHED"}
            assert opened_hits and opened_hits[-1]["key"] == object_key, (
                "オブジェクトツールの連続クリックでコマファイルを開けません"
            )
        finally:
            object_tool_op._hit_coma_at_event = original_hit_coma
            object_tool_op.coma_edge_move_op.extend_selected_handle_at_event = original_extend

        gp_layer = _add_gp_layer(context, coma_key)
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (36.0, 36.0, 24.0, 20.0),
            parent_key=coma_key,
        )
        raster = _add_raster(context, coma_key)
        image = _add_image(context, coma_key)
        balloon = _add_balloon(page, coma_key)
        page_balloon = _add_page_balloon(page, page_stack_key(page))
        text = _add_text(page, coma_key)
        layer_stack_utils.sync_layer_stack_after_data_change(context)

        visible_kinds = set(_visible_stack_kinds(context))
        expected_kinds = {"page", "coma", "gp", "effect", "raster", "image", "balloon", "text"}
        missing = expected_kinds - visible_kinds
        if missing:
            raise AssertionError(f"レイヤーリスト表示不足: {sorted(missing)} / visible={sorted(visible_kinds)}")

        if not overlay_visibility.entry_in_visible_coma(page, balloon):
            raise AssertionError("フキダシが表示対象になっていません")
        if not overlay_visibility.entry_in_visible_coma(page, text):
            raise AssertionError("テキストが表示対象になっていません")

        page_balloon_key = object_selection.balloon_key(page, page_balloon)
        original_pick_layer = page_op._pick_object_layer_at_event
        original_shortcuts_allowed = page_op.shortcut_visibility.shortcuts_allowed
        original_is_browser = page_op.page_browser.is_page_browser_area
        fake_area = SimpleNamespace(type="VIEW_3D")
        fake_context = SimpleNamespace(
            scene=context.scene,
            area=fake_area,
            mode="OBJECT",
            window_manager=context.window_manager,
            view_layer=context.view_layer,
            screen=getattr(context, "screen", None),
        )
        page_balloon_hit = {
            "kind": "balloon",
            "page_id": getattr(page, "id", ""),
            "index": len(page.balloons) - 1,
            "part": "move",
            "key": page_balloon_key,
        }
        try:
            page_op.shortcut_visibility.shortcuts_allowed = lambda _ctx: True
            page_op.page_browser.is_page_browser_area = lambda _ctx: False
            page_op._pick_object_layer_at_event = lambda _ctx, _event: (page_balloon_hit, object_tool_op)
            event = SimpleNamespace(
                value="PRESS",
                alt=False,
                oskey=False,
                ctrl=False,
                shift=False,
            )
            object_selection.clear(context)
            result = page_op.BNAME_OT_page_pick_viewport.invoke(SimpleNamespace(), fake_context, event)
            if result != {"FINISHED"}:
                raise AssertionError(f"コマ外フキダシのクリック選択が処理されません: {result}")
            if object_selection.get_keys(context) != [page_balloon_key]:
                raise AssertionError("コマ外フキダシのクリックがページ選択に奪われています")
            if getattr(context.scene, "bname_active_layer_kind", "") != "balloon":
                raise AssertionError("コマ外フキダシのクリック後、フキダシがアクティブになっていません")
        finally:
            page_op._pick_object_layer_at_event = original_pick_layer
            page_op.shortcut_visibility.shortcuts_allowed = original_shortcuts_allowed
            page_op.page_browser.is_page_browser_area = original_is_browser

        keys = [
            object_selection.gp_key(gp_layer),
            object_selection.effect_key(effect_layer),
            object_selection.raster_key(raster),
            object_selection.image_key(image),
            object_selection.balloon_key(page, balloon),
            page_balloon_key,
            object_selection.text_key(page, text),
        ]
        for key in keys:
            bounds = object_tool_selection.selection_bounds_for_key(context, key)
            if bounds is None or bounds.width <= 0.0 or bounds.height <= 0.0:
                raise AssertionError(f"選択枠の範囲が取れません: {key}")
            object_tool_selection.sync_outliner_selection_for_keys(context, [key])
            active_obj = context.view_layer.objects.active
            if active_obj is None or not active_obj.select_get():
                raise AssertionError(f"アウトライナー選択が同期しません: {key}")
            kind, _page_id, item_id = object_selection.parse_key(key)
            if kind in {"image", "raster", "balloon", "text"}:
                expected_id = item_id
                if kind == "text":
                    expected_id = text_real_object.text_object_bname_id_for_values(
                        page.id, item_id
                    )
                if (
                    not on.is_managed(active_obj)
                    or on.get_kind(active_obj) != kind
                    or on.get_bname_id(active_obj) != expected_id
                ):
                    raise AssertionError(f"対象オブジェクトが一致しません: {key}")

        for key in keys:
            kind, _page_id, item_id = object_selection.parse_key(key)
            fake_op = SimpleNamespace(_drag_action="move")
            snapshots = object_tool_op.BNAME_OT_object_tool._make_snapshots(
                fake_op,
                context,
                [key],
                primary_key=key,
                action="move",
            )
            if not snapshots:
                raise AssertionError(f"オブジェクトツール編集の準備ができません: {key}")
            before = object_tool_selection.selection_bounds_for_key(context, key)
            before_center = None
            if kind == "effect":
                obj, layer = object_tool_selection.find_effect_layer(item_id)
                before_center = effect_line_op.effect_layer_center(obj, layer)
            fake_op._snapshots = snapshots
            object_tool_op.BNAME_OT_object_tool._apply_snapshots(fake_op, context, 4.0, 3.0)
            if kind == "raster":
                _idx, raster_entry = object_tool_selection.find_raster_by_key(context, item_id)
                if raster_entry is None or not bool(raster_entry.get("bname_raster_dirty", False)):
                    raise AssertionError("ラスターのドラッグ編集が画素移動として反映されません")
                continue
            after = object_tool_selection.selection_bounds_for_key(context, key)
            if before is None or after is None or abs(float(after.x) - float(before.x)) < 0.5:
                raise AssertionError(f"オブジェクトツール編集で位置が変わりません: {key}")
            if kind == "effect" and before_center is not None:
                obj, layer = object_tool_selection.find_effect_layer(item_id)
                after_center = effect_line_op.effect_layer_center(obj, layer)
                if after_center is None or abs(after_center[0] - before_center[0] - 4.0) > 0.01:
                    raise AssertionError("効果線の中心点がオブジェクト移動に追従しません")

        page_key = object_selection.page_key(page)
        page_bounds = object_tool_selection.selection_bounds_for_key(context, page_key)
        if page_bounds is None or page_bounds.width <= 0.0 or page_bounds.height <= 0.0:
            raise AssertionError("ページの選択枠の範囲が取れません")
        object_tool_selection.sync_outliner_selection_for_keys(context, [page_key])
        if int(getattr(work, "active_page_index", -1)) != 0:
            raise AssertionError("ページ選択が同期しません")

        selected = set(
            object_tool_selection.select_keys_in_world_rect(
                context,
                Rect(-1000.0, -1000.0, 3000.0, 3000.0),
                mode="single",
            )
        )
        for key in keys:
            if key not in selected:
                raise AssertionError(f"矩形選択から漏れました: {key}")
        if page_key not in selected:
            raise AssertionError("矩形選択からページが漏れました")

        sentinel_key = object_selection.make_key("sentinel", "", "preserve")
        object_selection.set_keys(context, [sentinel_key])

        def _activate_like_object_tool(ctx, hit, hit_mode):
            object_selection.select_key(ctx, hit["key"], mode=hit_mode)

        add_selected = set(
            object_tool_selection.select_keys_in_world_rect(
                context,
                Rect(-1000.0, -1000.0, 3000.0, 3000.0),
                mode="add",
                activate=_activate_like_object_tool,
            )
        )
        if sentinel_key not in add_selected:
            raise AssertionError("追加の矩形選択で既存選択が失われました")

        small_selected = set(
            object_tool_selection.select_keys_in_world_rect(
                context,
                Rect(2.0, 2.0, 3.0, 3.0),
                mode="single",
            )
        )
        if page_key in small_selected:
            raise AssertionError("小さい矩形選択でページが誤選択されました")

        effect_line_op._select_effect_layer(context, effect_obj, effect_layer)
        drawn = []

        def _fill(rect, color):
            drawn.append(("fill", rect, color))

        def _outline(rect, *args, **kwargs):
            drawn.append(("outline", rect, args, kwargs))

        guide_drawn = []

        def _segments(segments, color, width_mm):
            guide_drawn.append((segments, color, width_mm))

        overlay_effect_line.draw_active_effect_line_bounds(
            context,
            draw_rect_fill=_fill,
            draw_rect_outline=_outline,
            draw_segments_mm=_segments,
        )
        if not drawn:
            raise AssertionError("効果線の選択枠が描画されません")
        if len(guide_drawn) < 2:
            raise AssertionError("効果線の始点形状・終点形状ガイドが描画されません")

        params = context.scene.bname_effect_line_params
        params.effect_type = "focus"
        params.start_to_coma_frame = True
        params.spacing_mode = "distance"
        params.spacing_distance_mm = 8.0
        params.max_line_count = 240
        frame_effect_obj, frame_effect_layer = effect_line_op._create_effect_layer(
            context,
            (82.0, 92.0, 22.0, 18.0),
            parent_key=coma_key,
        )
        frame_bounds = effect_line_op.effect_layer_bounds(frame_effect_obj, frame_effect_layer)
        assert frame_bounds is not None
        ox, oy = (
            float(getattr(frame_effect_obj, "location", (0.0, 0.0, 0.0))[0]) * 1000.0,
            float(getattr(frame_effect_obj, "location", (0.0, 0.0, 0.0))[1]) * 1000.0,
        )
        found_stroke_hit = False
        direct_stroke_hit = False
        checked_stroke_points = 0
        outside_bounds_points = 0
        first_debug = None
        bx, by, bw, bh = frame_bounds
        display = effect_line_object.find_effect_display_object(frame_effect_obj)
        if display is not None:
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = display.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                verts = [(float(v.co.x) * 1000.0, float(v.co.y) * 1000.0) for v in mesh.vertices]
                for poly in mesh.polygons:
                    poly_points = [verts[i] for i in poly.vertices if 0 <= i < len(verts)]
                    if not poly_points:
                        continue
                    checked_stroke_points += 1
                    local_x = sum(p[0] for p in poly_points) / len(poly_points)
                    local_y = sum(p[1] for p in poly_points) / len(poly_points)
                    if bx <= local_x <= bx + bw and by <= local_y <= by + bh:
                        continue
                    outside_bounds_points += 1
                    if effect_line_op._display_mesh_hit_part(context, frame_effect_obj, local_x, local_y):
                        direct_stroke_hit = True
                    hit_obj, hit_layer, _hit_bounds, hit_part = effect_line_op._hit_effect_layer(
                        context,
                        ox + local_x,
                        oy + local_y,
                    )
                    if first_debug is None and direct_stroke_hit:
                        first_debug = (
                            getattr(frame_effect_obj, "name", ""),
                            bool(getattr(frame_effect_obj, "hide_viewport", False)),
                            getattr(hit_obj, "name", "") if hit_obj is not None else "",
                            getattr(hit_layer, "name", "") if hit_layer is not None else "",
                            str(hit_part),
                            round(local_x, 3),
                            round(local_y, 3),
                            round(ox, 3),
                            round(oy, 3),
                        )
                    if (
                        hit_obj is frame_effect_obj
                        and getattr(hit_layer, "name", "") == getattr(frame_effect_layer, "name", "")
                        and hit_part
                    ):
                        found_stroke_hit = True
                        break
            finally:
                evaluated.to_mesh_clear()
        if not found_stroke_hit:
            raise AssertionError(
                "効果線の見えている線をクリック対象として拾えません: "
                f"checked={checked_stroke_points} outside={outside_bounds_points} direct={direct_stroke_hit} "
                f"debug={first_debug}"
            )

        end_labels = [str(getattr(item, "name", "") or "") for item in params.bl_rna.properties["end_shape"].enum_items]
        if any("（旧）" in label for label in end_labels):
            raise AssertionError(f"終点形状に旧表記が残っています: {end_labels}")
        old_shape_ids = {"polygon", "pill", "hexagon", "diamond", "star", "spike_straight", "spike_curve", "uni_flash"}
        end_ids = {str(getattr(item, "identifier", "") or "") for item in params.bl_rna.properties["end_shape"].enum_items}
        balloon_ids = {str(getattr(item, "identifier", "") or "") for item in balloon.bl_rna.properties["shape"].enum_items}
        if (end_ids | balloon_ids) & old_shape_ids:
            raise AssertionError(f"旧タイプが形状候補に残っています: effect={end_ids}, balloon={balloon_ids}")
        balloon_labels = {str(getattr(item, "name", "") or "") for item in balloon.bl_rna.properties["shape"].enum_items}
        if any("旧" in label for label in end_labels + list(balloon_labels)):
            raise AssertionError("終点形状または形状に旧表記が残っています")
        if abs(float(params.brush_size_mm) - 0.3) > 1.0e-6 or abs(float(balloon.line_width_mm) - 0.3) > 1.0e-6:
            raise AssertionError("効果線またはフキダシの線幅初期値が0.3ではありません")
        params.effect_type = "focus"
        params.start_shape = "ellipse"
        params.end_shape = "ellipse"
        params.length_jitter_enabled = False
        base_strokes = effect_line_gen.generate_focus_strokes(params, seed=37)
        params.length_jitter_enabled = True
        params.length_jitter_amount = 1.0
        jitter_strokes = effect_line_gen.generate_focus_strokes(params, seed=37)

        def _stroke_len(stroke):
            a, b = stroke.points_xyz[0], stroke.points_xyz[-1]
            return ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5

        if not base_strokes or len(base_strokes) != len(jitter_strokes):
            raise AssertionError("線の長さ乱れの生成本数が不正です")
        if sum(_stroke_len(s) for s in jitter_strokes) >= sum(_stroke_len(s) for s in base_strokes):
            raise AssertionError("線の長さ乱れが反映されていません")

        rect = Rect(0.0, 0.0, 80.0, 40.0)
        plain_outline = balloon_shapes.outline_for_shape(
            "thorn",
            rect,
            cloud_bump_width_jitter=0.0,
            cloud_bump_height_jitter=0.0,
            cloud_sub_width_ratio=50.0,
            cloud_sub_height_ratio=50.0,
            cloud_sub_width_jitter=0.0,
            cloud_sub_height_jitter=0.0,
            jitter_seed=101,
        )
        jitter_outline = balloon_shapes.outline_for_shape(
            "thorn",
            rect,
            cloud_bump_width_jitter=1.0,
            cloud_bump_height_jitter=1.0,
            cloud_sub_width_ratio=50.0,
            cloud_sub_height_ratio=50.0,
            cloud_sub_width_jitter=1.0,
            cloud_sub_height_jitter=1.0,
            jitter_seed=101,
        )
        if plain_outline == jitter_outline:
            raise AssertionError("形状の乱れが輪郭に反映されていません")

        print("BNAME_OBJECT_TOOL_SELECTION_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
