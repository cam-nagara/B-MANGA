"""Blender 実機用: テキストツールのIMEランタイム確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace

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


def _new_text_entry(work_dir: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    entry = page.texts.add()
    entry.id = "text_ime_check"
    entry.body = "abcdef"
    entry.x_mm = 0.0
    entry.y_mm = 0.0
    entry.width_mm = 40.0
    entry.height_mm = 20.0
    entry.writing_mode = "horizontal"
    entry.font_size_q = 20.0
    return entry


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_ime_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        entry = _new_text_entry(temp_root / "Text_IME.bmanga")

        from bmanga_dev.operators import object_tool_op, text_edit_runtime, text_op
        from bmanga_dev.operators import text_edit_history
        from bmanga_dev.utils import object_selection, text_real_object
        from bmanga_dev.ui import overlay_text

        class Event:
            def __init__(self, event_type: str, value: str = "PRESS") -> None:
                self.type = event_type
                self.value = value

        text_edit_runtime._clear_ime_text_queue()
        assert not text_op._event_consumed_by_ime(Event("RET"))
        text_edit_runtime._set_ime_composition_text("日本")
        assert text_op._event_consumed_by_ime(Event("RET"))
        assert text_op._event_consumed_by_ime(Event("TEXTINPUT", "NOTHING"))
        assert not text_op._event_consumed_by_ime(Event("LEFTMOUSE"))
        # 変換中のキーは B-MANGA 単キーショートカットへ流れてはならない
        # (K/F/Z/X 等がテキストを勝手に確定・Undo する回帰の防止)。
        from bmanga_dev.operators import coma_modal_state as _cms

        class _EditingOpStub:
            _editing = True

        _cms._ACTIVE_REFS["text_tool"] = lambda: _EditingOpStub()
        try:
            assert _cms.inline_text_edit_active()
            assert _cms.event_blocked_by_inline_text_edit(Event("K"))
            assert _cms.event_blocked_by_inline_text_edit(Event("T"))
            assert not _cms.event_blocked_by_inline_text_edit(Event("LEFTMOUSE"))
        finally:
            _cms._ACTIVE_REFS["text_tool"] = None
        assert not _cms.event_blocked_by_inline_text_edit(Event("K"))
        text_edit_runtime._clear_ime_text_queue()
        text_edit_runtime._set_ime_composition_text("日本")
        preview, caret, bounds = text_edit_runtime.preview_entry_with_composition(entry, 2, -1)
        assert entry.body == "abcdef"
        assert preview.body == "ab日本cdef"
        assert caret == 4
        assert bounds == (2, 4)
        assert text_edit_runtime.text_body(preview) == "ab日本cdef"
        assert text_edit_runtime.caret_rect(preview, text_edit_runtime.text_rect(entry), caret).width > 0

        entry.body = "日本語"
        entry.writing_mode = "vertical"
        vertical_rect = text_edit_runtime.text_rect(entry)
        vertical_region = text_edit_runtime.text_inner_rect(vertical_rect)
        vertical_em = text_edit_runtime.text_em_mm(entry)
        vertical_caret = text_edit_runtime.caret_rect(entry, vertical_rect, 0)
        from bmanga_dev.typography import layout as text_layout
        from bmanga_dev.ui import overlay_text

        layout_result = text_layout.typeset(
            entry,
            vertical_region.x,
            vertical_region.y,
            vertical_region.width,
            vertical_region.height,
        )
        first_glyph = layout_result.placements[0]
        # キャレットバーは字列中心を挟んで左右対称 (caret.x は左端)。
        # バー中心 = 字列中心 = 先頭グリフ em ボックス中心、が仕様。
        caret_center_x = vertical_caret.x + vertical_caret.width * 0.5
        expected_center_x = vertical_region.x2 - vertical_em * 0.5
        assert abs(caret_center_x - expected_center_x) < 1e-6, (caret_center_x, expected_center_x)
        glyph_center_x = first_glyph.x_mm + vertical_em * 0.5
        assert abs(caret_center_x - glyph_center_x) < 1e-6, (caret_center_x, glyph_center_x)
        selection_rect = overlay_text._selection_rects(entry, vertical_rect, 1, 0)[0]
        assert abs(selection_rect.x - vertical_caret.x) < 1e-6, (selection_rect.x, vertical_caret.x)

        entry.body = "abcdef"
        entry.writing_mode = "horizontal"

        text_edit_runtime._set_ime_composition_text("語")
        preview, caret, bounds = text_edit_runtime.preview_entry_with_composition(entry, 4, 1)
        assert preview.body == "a語ef"
        assert caret == 2
        assert bounds == (1, 2)

        text_edit_runtime._begin_ime_composition()
        assert text_edit_runtime.ime_composition_active()
        text_edit_runtime._append_ime_text("日本語")
        assert text_edit_runtime.poll_ime_text() == "日本語"
        assert not text_edit_runtime.ime_composition_active()

        entry.body = "abcdef"
        cursor = text_edit_runtime.replace_selection(entry, 3, 1, "日本語")
        assert entry.body == "a日本語def"
        assert cursor == 4

        work = bpy.context.scene.bmanga_work
        page = work.pages[0]
        page.active_text_index = 0

        class EditProbe:
            _editing = True
            _cursor_index = 0
            _selection_anchor = -1

            def _current_text_entry(self, _context):
                return page, entry, 0

        probe = EditProbe()
        entry.body = "abc"
        entry.x_mm = 0.0
        entry.y_mm = 0.0
        entry.width_mm = 40.0
        entry.height_mm = 20.0
        probe._cursor_index = len(entry.body)
        text_edit_history.begin(probe, entry)
        probe._cursor_index = text_edit_runtime.replace_selection(entry, probe._cursor_index, -1, "d")
        text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        text_edit_history.record(probe, entry)
        assert entry.body == "abcd"
        assert text_edit_history.restore_previous(probe, bpy.context)
        assert entry.body == "abc"
        assert text_edit_history.restore_next(probe, bpy.context)
        assert entry.body == "abcd"

        probe._selection_anchor = 0
        probe._cursor_index = 2
        rect = text_edit_runtime.text_rect(entry)
        caret = overlay_text.text_caret_rect(entry, rect, probe._cursor_index)
        selection_rects = overlay_text._selection_rects(entry, rect, probe._cursor_index, probe._selection_anchor)
        assert caret.width > 0 and caret.height > 0
        assert selection_rects, "selection highlight rects were not generated"
        text_edit_runtime.set_view_edit_state(
            bpy.context,
            getattr(page, "id", ""),
            getattr(entry, "id", ""),
            probe._cursor_index,
            probe._selection_anchor,
        )
        state = text_edit_runtime.view_edit_state_for_entry(bpy.context, page, entry)
        assert state is not None
        assert state._selection_anchor == 0, "selection anchor at the first character was lost"
        assert overlay_text._editing_operator(bpy.context, page, entry) is not None
        text_edit_runtime.clear_view_edit_state(bpy.context)

        entry.body = "固定"
        entry.x_mm = 0.0
        entry.y_mm = 0.0
        entry.width_mm = 80.0
        entry.height_mm = 30.0
        assert text_op._text_hit_part(entry, entry.x_mm + entry.width_mm, entry.y_mm + entry.height_mm) == "body"
        text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0, allow_shrink=True)
        assert entry.width_mm < 80.0 and entry.height_mm < 30.0

        key = object_selection.text_key(page, entry)
        before_rect = (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm))
        dummy = SimpleNamespace(_drag_action="top_right")
        dummy._snapshots = object_tool_op.BMANGA_OT_object_tool._make_snapshots(
            dummy,
            bpy.context,
            [key],
            primary_key=key,
            action="top_right",
        )
        object_tool_op.BMANGA_OT_object_tool._apply_snapshots(dummy, bpy.context, 12.0, 8.0)
        after_resize_attempt = (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm))
        assert after_resize_attempt == before_rect, "text handle drag changed the fixed text rect"

        dummy._drag_action = "move"
        dummy._snapshots = object_tool_op.BMANGA_OT_object_tool._make_snapshots(
            dummy,
            bpy.context,
            [key],
            primary_key=key,
            action="move",
        )
        object_tool_op.BMANGA_OT_object_tool._apply_snapshots(dummy, bpy.context, 3.0, 4.0)
        assert round(entry.x_mm - before_rect[0], 3) == 3.0
        assert round(entry.y_mm - before_rect[1], 3) == 4.0
        assert round(entry.width_mm, 3) == round(before_rect[2], 3)
        assert round(entry.height_mm, 3) == round(before_rect[3], 3)

        obj = text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
        assert obj is not None
        text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=True)
        entry.body = "固定確認"
        hidden_obj = text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
        assert hidden_obj is not None and hidden_obj.hide_viewport, "editing preview object became visible"
        text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=False)
        shown_obj = text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
        assert shown_obj is not None and not shown_obj.hide_viewport, "text object did not show after edit finish"

        text_hit = {
            "kind": "text",
            "page_id": getattr(page, "id", ""),
            "index": 0,
            "part": "move",
            "key": object_selection.text_key(page, entry),
        }
        routed: list[tuple[str, str]] = []
        old_start = text_op.start_editing_existing_from_object_tool
        text_op.start_editing_existing_from_object_tool = (
            lambda _ctx, page_id, text_id: routed.append((page_id, text_id)) or True
        )
        try:
            tool = SimpleNamespace(finished=False)
            tool.finish_from_external = lambda _ctx, *, keep_selection: setattr(tool, "finished", True)
            method = MethodType(object_tool_op.BMANGA_OT_object_tool._try_enter_text_edit_from_hit, tool)
            assert method(bpy.context, text_hit)
        finally:
            text_op.start_editing_existing_from_object_tool = old_start
        assert tool.finished
        assert routed == [(getattr(page, "id", ""), getattr(entry, "id", ""))]

        # IME 候補ウィンドウ位置用キャレット矩形 API
        text_edit_runtime.set_ime_caret_client_rect(120, 40, 3, 18)
        assert text_edit_runtime._IME_CARET_CLIENT_RECT == (120, 40, 3, 18)
        text_edit_runtime.set_ime_caret_client_rect(5, 6, 0, 0)
        assert text_edit_runtime._IME_CARET_CLIENT_RECT == (5, 6, 1, 1), "最小1pxが保証されていない"
        text_edit_runtime.clear_ime_caret_client_rect()
        assert text_edit_runtime._IME_CARET_CLIENT_RECT is None

        text_edit_runtime.set_ime_caret_client_rect(10, 20, 2, 16)
        text_edit_runtime.begin_ime_capture()
        text_edit_runtime.end_ime_capture()
        assert text_edit_runtime._IME_CARET_CLIENT_RECT is None, "end_ime_capture がキャレット矩形を掃除していない"
        print("BMANGA_TEXT_IME_RUNTIME_OK")
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
