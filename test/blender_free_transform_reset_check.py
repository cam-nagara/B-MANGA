"""Blender実機用: 自由変形を右クリックメニューからリセットできることを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _select_stack_item(context, kind: str, key: str) -> None:
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context)
    for index, item in enumerate(stack or []):
        if str(getattr(item, "kind", "") or "") == kind and str(getattr(item, "key", "") or "") == key:
            assert layer_stack_utils.select_stack_index(context, index), (kind, key)
            return
    raise AssertionError(f"stack item not found: {kind} {key}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_free_transform_reset_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "FreeTransformReset.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev.operators import balloon_op, coma_modal_state, effect_line_op, text_op
        from bmanga_dev.utils import balloon_line_mesh, free_transform, layer_hierarchy, layer_stack as layer_stack_utils
        from bmanga_dev.utils import text_real_object

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        offsets = free_transform.zero_offsets()
        offsets[free_transform.TOP_RIGHT] = (8.0, 5.0)

        balloon = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=40.0,
            y=60.0,
            w=50.0,
            h=30.0,
            parent_kind="page",
            parent_key=layer_hierarchy.page_stack_key(page),
        )
        free_transform.set_entry_offsets(balloon, offsets, enabled=True)
        page.active_balloon_index = 0
        context.scene.bmanga_active_layer_kind = "balloon"
        _select_stack_item(context, "balloon", f"{page.id}:{balloon.id}")
        assert bpy.ops.bmanga.reset_free_transform() == {"FINISHED"}
        assert not free_transform.entry_enabled(balloon)

        balloon.line_width_mm = 1.0
        assert bpy.ops.bmanga.balloon_free_transform_scale(
            "EXEC_DEFAULT",
            scale_percent=200.0,
            keep_line_width=True,
        ) == {"FINISHED"}
        scaled_offsets = free_transform.entry_offsets(balloon)
        bottom_left = scaled_offsets[free_transform.BOTTOM_LEFT]
        if abs(bottom_left[0] + 25.0) > 1.0e-6 or abs(bottom_left[1] + 15.0) > 1.0e-6:
            raise AssertionError(f"フキダシ拡大の自由変形値が不正です: {scaled_offsets}")
        if abs(float(balloon.free_transform_line_width_scale) - 1.0) > 1.0e-6:
            raise AssertionError("線幅を維持した拡大で線幅倍率が変わっています")
        if abs(balloon_line_mesh.scaled_entry_width_mm(balloon, "line_width_mm", 0.3) - 1.0) > 1.0e-6:
            raise AssertionError("線幅を維持した拡大で描画線幅が変わっています")

        assert bpy.ops.bmanga.reset_free_transform() == {"FINISHED"}
        assert bpy.ops.bmanga.balloon_free_transform_scale(
            "EXEC_DEFAULT",
            scale_percent=200.0,
            keep_line_width=False,
        ) == {"FINISHED"}
        if abs(float(balloon.free_transform_line_width_scale) - 2.0) > 1.0e-6:
            raise AssertionError("線幅を維持しない拡大で線幅倍率が反映されていません")
        if abs(balloon_line_mesh.scaled_entry_width_mm(balloon, "line_width_mm", 0.3) - 2.0) > 1.0e-6:
            raise AssertionError("線幅を維持しない拡大で描画線幅が太くなっていません")
        assert bpy.ops.bmanga.balloon_free_transform_rotate(
            "EXEC_DEFAULT",
            angle_deg=90.0,
        ) == {"FINISHED"}
        if not free_transform.entry_enabled(balloon):
            raise AssertionError("フキダシ回転で自由変形が有効になっていません")
        assert bpy.ops.bmanga.reset_free_transform() == {"FINISHED"}
        assert not free_transform.entry_enabled(balloon)
        if abs(float(balloon.free_transform_line_width_scale) - 1.0) > 1.0e-6:
            raise AssertionError("自由変形リセットで線幅倍率が戻っていません")

        text, missing = text_op._create_text_entry(
            context,
            page,
            body="reset",
            x_mm=50.0,
            y_mm=100.0,
            width_mm=50.0,
            height_mm=20.0,
            parent_kind="page",
            parent_key=layer_hierarchy.page_stack_key(page),
        )
        assert not missing
        free_transform.set_entry_offsets(text, offsets, enabled=True)
        page.active_text_index = 0
        context.scene.bmanga_active_layer_kind = "text"
        _select_stack_item(context, "text", f"{page.id}:{text.id}")
        assert bpy.ops.bmanga.reset_free_transform() == {"FINISHED"}
        assert not free_transform.entry_enabled(text)

        class TextEditProbe:
            _editing = True

            def __init__(self, page_id: str, text_id: str) -> None:
                self._page_id = page_id
                self._text_id = text_id

            def finish_from_external(self, context, *, keep_selection: bool) -> None:
                _ = context
                _ = keep_selection

        free_transform.set_entry_offsets(text, offsets, enabled=True)
        text_real_object.set_text_object_preview_hidden(text, page=page, hidden=False)
        assert text_real_object.has_visible_text_object(text, page=page)
        edit_probe = TextEditProbe(page.id, text.id)
        coma_modal_state.set_active("text_tool", edit_probe, context)
        assert bpy.ops.bmanga.reset_free_transform() == {"FINISHED"}
        assert not free_transform.entry_enabled(text)
        assert not text_real_object.has_visible_text_object(text, page=page)
        coma_modal_state.clear_active("text_tool", edit_probe, context)

        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (40.0, 120.0, 50.0, 30.0),
            parent_key=layer_hierarchy.page_stack_key(page),
        )
        assert effect_obj is not None and effect_layer is not None
        meta = effect_line_op._effect_meta(effect_obj)
        meta_key = effect_line_op._layer_meta_key(effect_layer)
        entry = dict(meta.get(meta_key, {}) if isinstance(meta.get(meta_key, {}), dict) else {})
        free_transform.set_effect_payload_on_meta_entry(entry, {"enabled": True, "offsets": offsets})
        meta[meta_key] = entry
        effect_line_op._write_effect_meta(effect_obj, meta)
        context.scene.bmanga_active_layer_kind = "effect"
        effect_id = str(effect_obj.get("bmanga_id", "") or "")
        context.scene.bmanga_active_effect_layer_name = effect_id
        _select_stack_item(context, "effect", effect_id)
        assert bpy.ops.bmanga.reset_free_transform() == {"FINISHED"}
        assert not free_transform.effect_payload_enabled(free_transform.effect_payload_for_layer(effect_obj, effect_layer))

        print("BMANGA_FREE_TRANSFORM_RESET_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
