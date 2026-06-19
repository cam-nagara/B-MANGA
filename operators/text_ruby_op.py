"""Ruby editing operators for text entries."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..utils import layer_stack as layer_stack_utils
from ..utils import text_real_object, text_style
from . import coma_modal_state
from .text_meta_op import _resolve_text_entry

_RUBY_STYLE_ITEMS = (
    ("group", "グループルビ", ""),
    ("mono", "モノルビ", ""),
    ("jukugo", "熟語ルビ", ""),
)


def _find_text_index(page, entry) -> int:
    if page is None or entry is None:
        return -1
    target_id = str(getattr(entry, "id", "") or "")
    for index, candidate in enumerate(getattr(page, "texts", []) or []):
        if str(getattr(candidate, "id", "") or "") == target_id:
            return index
    return -1


def _entry_bounds(entry, start: int, end: int) -> tuple[int, int]:
    body_len = len(str(getattr(entry, "body", "") or ""))
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    return start, end


def _active_selection_bounds(context, page, entry) -> tuple[int, int] | None:
    if page is None or entry is None:
        return None
    try:
        from . import text_op

        return text_op._active_text_selection_bounds(context, page, entry)
    except Exception:  # noqa: BLE001
        return None


def _selected_preview(entry, start: int, end: int) -> str:
    body = str(getattr(entry, "body", "") or "")
    text = body[start:end].replace("\n", " / ")
    if len(text) > 24:
        return text[:23] + "..."
    return text


def _editing_operator_for(page, entry):
    if page is None or entry is None:
        return None
    op = coma_modal_state.get_active("text_tool")
    if (
        op is not None
        and bool(getattr(op, "_editing", False))
        and str(getattr(op, "_page_id", "") or "") == str(getattr(page, "id", "") or "")
        and str(getattr(op, "_text_id", "") or "") == str(getattr(entry, "id", "") or "")
    ):
        return op
    return None


def _sync_after_ruby_change(context, page, entry, start: int, end: int) -> None:
    idx = _find_text_index(page, entry)
    op = _editing_operator_for(page, entry)
    if op is not None and idx >= 0:
        op._selection_anchor = start
        op._cursor_index = end
        op._touch_current_text(context, page, entry, idx)
        text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=True)
    else:
        try:
            text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
            text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=False)
        except Exception:  # noqa: BLE001
            pass
        layer_stack_utils.sync_layer_stack_after_data_change(context)
    if page is not None and idx >= 0:
        page.active_text_index = idx
    layer_stack_utils.tag_view3d_redraw(context)


class BMANGA_OT_text_ruby_add_dialog(Operator):
    """選択中の文字にルビを付ける."""

    bl_idname = "bmanga.text_ruby_add_dialog"
    bl_label = "ルビを付ける"
    bl_options = {"REGISTER", "UNDO"}

    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    text_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="文字数", default=1, min=1)  # type: ignore[valid-type]
    ruby_text: StringProperty(name="ルビ", default="")  # type: ignore[valid-type]
    style: EnumProperty(name="種類", items=_RUBY_STYLE_ITEMS, default="group")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return get_work(context) is not None

    def _resolve_target(self, context):
        if self.page_id or self.text_id:
            work = get_work(context)
            if work is not None:
                for page in getattr(work, "pages", []) or []:
                    if self.page_id and str(getattr(page, "id", "") or "") != self.page_id:
                        continue
                    for entry in getattr(page, "texts", []) or []:
                        if str(getattr(entry, "id", "") or "") == self.text_id:
                            return page, entry
        return _resolve_text_entry(context)

    def invoke(self, context, event):
        page, entry = self._resolve_target(context)
        if entry is None:
            self.report({"ERROR"}, "テキストが選択されていません")
            return {"CANCELLED"}
        bounds = _active_selection_bounds(context, page, entry)
        body_len = len(str(getattr(entry, "body", "") or ""))
        if bounds is not None:
            start, end = bounds
        elif self.length > 0:
            start, end = _entry_bounds(entry, self.start, self.start + self.length)
        else:
            start, end = 0, body_len
        if start >= end and body_len > 0:
            start, end = 0, body_len
        self.start = start
        self.length = max(1, end - start)
        if not self.ruby_text:
            for r_start, r_end, ruby_text, style in text_style.ruby_spans_snapshot(entry):
                if r_start == start and r_end == end:
                    self.ruby_text = ruby_text
                    self.style = style
                    break
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        _page, entry = self._resolve_target(context)
        if entry is not None:
            start, end = _entry_bounds(entry, self.start, self.start + self.length)
            layout.label(text=f"親文字: {_selected_preview(entry, start, end)}")
        row = layout.row(align=True)
        row.prop(self, "start")
        row.prop(self, "length")
        layout.prop(self, "ruby_text")
        layout.prop(self, "style")

    def execute(self, context):
        page, entry = self._resolve_target(context)
        if entry is None:
            self.report({"ERROR"}, "テキストが選択されていません")
            return {"CANCELLED"}
        start, end = _entry_bounds(entry, self.start, self.start + self.length)
        if start >= end:
            self.report({"ERROR"}, "ルビを付ける文字範囲を指定してください")
            return {"CANCELLED"}
        if not text_style.apply_ruby_span(entry, start, end, self.ruby_text, self.style):
            self.report({"ERROR"}, "ルビを入力してください")
            return {"CANCELLED"}
        _sync_after_ruby_change(context, page, entry, start, end)
        self.report({"INFO"}, "ルビを更新しました")
        return {"FINISHED"}


class BMANGA_OT_text_ruby_clear(Operator):
    """選択中のテキストからルビを削除する."""

    bl_idname = "bmanga.text_ruby_clear"
    bl_label = "ルビを削除"
    bl_options = {"REGISTER", "UNDO"}

    selected_only: BoolProperty(name="選択範囲のみ", default=False)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        _page, entry = _resolve_text_entry(context)
        return entry is not None

    def execute(self, context):
        page, entry = _resolve_text_entry(context)
        if entry is None:
            self.report({"ERROR"}, "テキストが選択されていません")
            return {"CANCELLED"}
        bounds = _active_selection_bounds(context, page, entry) if self.selected_only else None
        if bounds is None:
            changed = text_style.clear_ruby_spans(entry)
            start, end = 0, len(str(getattr(entry, "body", "") or ""))
        else:
            start, end = bounds
            changed = text_style.clear_ruby_spans(entry, start, end)
        if not changed:
            self.report({"INFO"}, "削除するルビはありません")
            return {"FINISHED"}
        _sync_after_ruby_change(context, page, entry, start, end)
        self.report({"INFO"}, "ルビを削除しました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_text_ruby_add_dialog,
    BMANGA_OT_text_ruby_clear,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
