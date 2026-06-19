"""Blender 実機用: テキスト入力中の自動枠調整と移動時の軽量更新確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


class _FinishProbe:
    _editing_created_new = True
    _edit_original_body = ""
    _edit_original_font_spans = ()
    _edit_original_rect = (10.0, 10.0, 100.0, 80.0)
    _cursor_index = 0
    _selection_anchor = -1

    def __init__(self, page, entry):
        self._page = page
        self._entry = entry
        self._page_id = str(getattr(page, "id", "") or "")
        self._text_id = str(getattr(entry, "id", "") or "")
        self.undo_message = ""

    def _current_text_entry(self, _context):
        return self._page, self._entry, 0

    def _push_undo_step(self, message: str) -> None:
        self.undo_message = message

    def _end_inline_input(self, _context) -> None:
        return None

    def _clear_click_state(self) -> None:
        return None

    def report(self, _level, _message) -> None:
        return None


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


def _new_entry(work_dir: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    from bmanga_dev.utils import text_real_object

    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    entry = page.texts.add()
    with text_real_object.suspend_auto_sync():
        entry.id = "text_autofit_perf"
        entry.body = "ABC"
        entry.x_mm = 20.0
        entry.y_mm = 30.0
        entry.width_mm = 8.0
        entry.height_mm = 6.0
        entry.writing_mode = "horizontal"
        entry.font_size_q = 20.0
    return work, page, entry


def _assert_no_overflow(entry):
    from bmanga_dev.operators import text_edit_runtime
    from bmanga_dev.typography import layout as text_layout

    rect = text_edit_runtime.text_rect(entry)
    region = text_edit_runtime.text_inner_rect(rect)
    result = text_layout.typeset(entry, region.x, region.y, region.width, region.height)
    assert not result.overflow, (entry.body, rect, result)


def _assert_text_has_handle_gap(entry):
    from bmanga_dev.operators import text_edit_runtime

    rect = text_edit_runtime.text_rect(entry)
    region = text_edit_runtime.text_inner_rect(rect)
    assert region.x - rect.x >= 2.49, (rect, region)
    assert rect.x2 - region.x2 >= 2.49, (rect, region)
    assert region.y - rect.y >= 2.49, (rect, region)
    assert rect.y2 - region.y2 >= 2.49, (rect, region)


def _assert_shift_enter_does_not_move_up_or_right(entry, *, body: str, mode: str):
    from bmanga_dev.operators import text_edit_runtime
    from bmanga_dev.utils import text_real_object

    with text_real_object.suspend_auto_sync():
        entry.body = body
        entry.writing_mode = mode
        entry.x_mm = 20.0
        entry.y_mm = 30.0
        entry.width_mm = 6.0
        entry.height_mm = 6.0
        text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        before = text_edit_runtime.text_rect(entry)
        entry.body = body[:2] + "\n" + body[2:]
        text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        after = text_edit_runtime.text_rect(entry)

    assert abs(after.y2 - before.y2) < 1.0e-5, (before, after)
    assert after.y <= before.y + 1.0e-5, (before, after)
    if mode == "horizontal":
        assert abs(after.x - before.x) < 1.0e-5, (before, after)
        assert abs(after.x2 - before.x2) < 1.0e-5, (before, after)
    else:
        assert abs(after.x2 - before.x2) < 1.0e-5, (before, after)
        assert after.x <= before.x + 1.0e-5, (before, after)


def _assert_text_tool_initial_field_size():
    from bmanga_dev.operators import text_op

    x, y, width, height = text_op._default_text_rect_for_click("vertical", 50.0, 60.0)
    assert (x, y, width, height) == (40.0, 40.0, 20.0, 30.0)

    x, y, width, height = text_op._default_text_rect_for_click("horizontal", 50.0, 60.0)
    assert (x, y, width, height) == (40.0, 50.0, 30.0, 20.0)


def _assert_pillow_text_origin_matches_layout_top():
    from bmanga_dev.typography import export_renderer

    top_px = export_renderer._layout_bottom_to_pillow_top(830, 740.0, 50)
    assert abs(top_px - 40.0) < 1.0e-5


def _assert_finish_fit_shrinks_input_field(context, page, entry):
    from bmanga_dev.operators import text_edit_runtime, text_op
    from bmanga_dev.utils import text_real_object

    with text_real_object.suspend_auto_sync():
        entry.body = "ABCDEF\nGH"
        entry.writing_mode = "horizontal"
        entry.x_mm = 10.0
        entry.y_mm = 10.0
        entry.width_mm = 100.0
        entry.height_mm = 80.0
        expected_width, expected_height = text_edit_runtime.natural_text_outer_size(entry)

    top_before = entry.y_mm + entry.height_mm
    probe = _FinishProbe(page, entry)
    text_op.BMANGA_OT_text_tool._finish_current_text_edit(probe, context)

    assert probe.undo_message == "B-MANGA: テキスト編集"
    assert abs(entry.x_mm - 10.0) < 1.0e-5
    assert abs(entry.width_mm - expected_width) < 1.0e-5
    assert abs(entry.height_mm - expected_height) < 1.0e-5
    assert abs((entry.y_mm + entry.height_mm) - top_before) < 1.0e-5

    with text_real_object.suspend_auto_sync():
        entry.body = "日本\n語"
        entry.writing_mode = "vertical"
        entry.x_mm = 10.0
        entry.y_mm = 10.0
        entry.width_mm = 100.0
        entry.height_mm = 80.0
        expected_width, expected_height = text_edit_runtime.natural_text_outer_size(entry)

    right_before = entry.x_mm + entry.width_mm
    top_before = entry.y_mm + entry.height_mm
    probe = _FinishProbe(page, entry)
    text_op.BMANGA_OT_text_tool._finish_current_text_edit(probe, context)

    assert abs(entry.width_mm - expected_width) < 1.0e-5
    assert abs(entry.height_mm - expected_height) < 1.0e-5
    assert abs((entry.x_mm + entry.width_mm) - right_before) < 1.0e-5
    assert abs((entry.y_mm + entry.height_mm) - top_before) < 1.0e-5


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_autofit_perf_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _work, page, entry = _new_entry(temp_root / "TextAutofitPerf.bmanga")

        from bmanga_dev.operators import text_edit_runtime
        from bmanga_dev.utils import page_grid, text_real_object

        _assert_text_tool_initial_field_size()
        _assert_pillow_text_origin_matches_layout_top()

        with text_real_object.suspend_auto_sync():
            entry.body = "ABCDEFGHIJKL"
            entry.width_mm = 6.0
            entry.height_mm = 6.0
            entry.writing_mode = "horizontal"
            changed = text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        assert changed
        assert entry.width_mm > 6.0, entry.width_mm
        _assert_text_has_handle_gap(entry)
        _assert_no_overflow(entry)

        with text_real_object.suspend_auto_sync():
            entry.body = "日本語テキスト長め"
            entry.width_mm = 6.0
            entry.height_mm = 6.0
            entry.writing_mode = "vertical"
            changed = text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        assert changed
        assert entry.height_mm > 6.0, entry.height_mm
        _assert_text_has_handle_gap(entry)
        _assert_no_overflow(entry)
        _assert_shift_enter_does_not_move_up_or_right(
            entry,
            body="ABCDEFGHIJKL",
            mode="horizontal",
        )
        _assert_shift_enter_does_not_move_up_or_right(
            entry,
            body="日本語テキスト長め",
            mode="vertical",
        )

        calls = {"render": 0}
        original_render = text_real_object._render_entry_to_pillow

        def _counted_render(target_entry):
            calls["render"] += 1
            return original_render(target_entry)

        text_real_object._render_entry_to_pillow = _counted_render
        try:
            obj = text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
            assert obj is not None
            assert calls["render"] == 1, calls
            entry.x_mm += 12.0
            entry.y_mm += 8.0
            assert calls["render"] == 1, calls
            ox, oy = page_grid.page_total_offset_mm(_work, bpy.context.scene, 0)
            assert abs(obj.location.x * 1000.0 - (entry.x_mm + ox)) < 1.0e-4
            assert abs(obj.location.y * 1000.0 - (entry.y_mm + oy)) < 1.0e-4
            text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
            assert calls["render"] == 1, calls
            entry.body += "Z"
            assert calls["render"] == 2, calls
            with text_real_object.suspend_auto_sync():
                entry.body += "Y"
                text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
            assert calls["render"] == 2, calls
            text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
            assert calls["render"] == 3, calls
        finally:
            text_real_object._render_entry_to_pillow = original_render

        _assert_finish_fit_shrinks_input_field(bpy.context, page, entry)

        print("BMANGA_TEXT_AUTOFIT_PERF_OK")
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
