"""Blender実機用: オブジェクトツール選択とレイヤーリスト表示の確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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
        from bname_dev.utils import object_selection
        from bname_dev.utils import text_real_object
        from bname_dev.utils.geom import Rect
        from bname_dev.utils.layer_hierarchy import coma_stack_key
        from bname_dev.operators import effect_line_gen, object_tool_selection

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        panel = page.comas[0]
        coma_key = coma_stack_key(page, panel)

        gp_layer = _add_gp_layer(context, coma_key)
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (36.0, 36.0, 24.0, 20.0),
            parent_key=coma_key,
        )
        raster = _add_raster(context, coma_key)
        image = _add_image(context, coma_key)
        balloon = _add_balloon(page, coma_key)
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

        keys = [
            object_selection.gp_key(gp_layer),
            object_selection.effect_key(effect_layer),
            object_selection.raster_key(raster),
            object_selection.image_key(image),
            object_selection.balloon_key(page, balloon),
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

        overlay_effect_line.draw_active_effect_line_bounds(
            context,
            draw_rect_fill=_fill,
            draw_rect_outline=_outline,
        )
        if not drawn:
            raise AssertionError("効果線の選択枠が描画されません")

        params = context.scene.bname_effect_line_params
        end_labels = [str(getattr(item, "name", "") or "") for item in params.bl_rna.properties["end_shape"].enum_items]
        if any("（旧）" in label for label in end_labels):
            raise AssertionError(f"終点形状に旧表記が残っています: {end_labels}")
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
