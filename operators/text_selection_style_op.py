"""Inline text selection style popup."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..utils import layer_stack as layer_stack_utils
from ..utils import text_real_object, text_style
from ..utils.geom import pt_to_q, q_to_pt
from . import coma_modal_state
from . import text_edit_runtime

_FONT_SIZE_UNIT_ITEMS = (
    ("q", "Q", "Q 数"),
    ("pt", "pt", "ポイント"),
)
_RUBY_STYLE_ITEMS = (
    ("group", "グループルビ", ""),
    ("mono", "モノルビ", ""),
    ("jukugo", "熟語ルビ", ""),
)
_font_size_sync_depth = 0


def _on_font_size_q_changed(self, _context) -> None:
    global _font_size_sync_depth
    if _font_size_sync_depth > 0:
        return
    _font_size_sync_depth += 1
    try:
        self.font_size_pt = max(0.1, float(q_to_pt(float(getattr(self, "font_size_q", 20.0) or 20.0))))
    finally:
        _font_size_sync_depth -= 1


def _on_font_size_pt_changed(self, _context) -> None:
    global _font_size_sync_depth
    if _font_size_sync_depth > 0:
        return
    _font_size_sync_depth += 1
    try:
        self.font_size_q = max(0.1, float(pt_to_q(float(getattr(self, "font_size_pt", 9.0) or 9.0))))
    finally:
        _font_size_sync_depth -= 1


def _get_font_size_value(self) -> float:
    if str(getattr(self, "font_size_unit", "q") or "q") == "pt":
        return float(getattr(self, "font_size_pt", q_to_pt(float(getattr(self, "font_size_q", 20.0)))) or 0.0)
    return float(getattr(self, "font_size_q", 20.0) or 0.0)


def _set_font_size_value(self, value: float) -> None:
    size = max(0.1, float(value or 0.0))
    if str(getattr(self, "font_size_unit", "q") or "q") == "pt":
        self.font_size_pt = size
    else:
        self.font_size_q = size


def _find_page_by_id(context, page_id: str):
    work = get_work(context)
    if work is None:
        return None
    for page in work.pages:
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return page
    return None


def _find_text_entry(context, page_id: str, text_id: str):
    page = _find_page_by_id(context, page_id)
    if page is None:
        return None, None, -1
    for index, entry in enumerate(page.texts):
        if str(getattr(entry, "id", "") or "") == str(text_id or ""):
            return page, entry, index
    return page, None, -1


def _active_text_tool_matches(page_id: str, text_id: str) -> bool:
    op = coma_modal_state.get_active("text_tool")
    return (
        op is not None
        and bool(getattr(op, "_editing", False))
        and str(getattr(op, "_page_id", "") or "") == str(page_id or "")
        and str(getattr(op, "_text_id", "") or "") == str(text_id or "")
    )


class BMANGA_OT_text_selection_style_popup(Operator):
    """選択中のインラインテキスト範囲へ文字スタイルを適用する."""

    bl_idname = "bmanga.text_selection_style_popup"
    bl_label = "選択文字設定"
    bl_options = {"REGISTER", "UNDO"}

    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    text_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    start: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]
    end: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    font_bold: BoolProperty(name="太字", default=False)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False)  # type: ignore[valid-type]
    font_size_q: FloatProperty(name="サイズ (Q)", default=20.0, min=1.0, soft_max=200.0, update=_on_font_size_q_changed)  # type: ignore[valid-type]
    font_size_pt: FloatProperty(name="サイズ (pt)", default=q_to_pt(20.0), min=0.1, soft_max=200.0, update=_on_font_size_pt_changed)  # type: ignore[valid-type]
    font_size_unit: EnumProperty(name="サイズ単位", items=_FONT_SIZE_UNIT_ITEMS, default="q")  # type: ignore[valid-type]
    font_size_value: FloatProperty(name="サイズ", default=20.0, min=0.1, soft_max=200.0, precision=3, get=_get_font_size_value, set=_set_font_size_value)  # type: ignore[valid-type]
    font_choice: EnumProperty(name="フォント", items=text_style.font_dropdown_items)  # type: ignore[valid-type]
    ruby_text: StringProperty(name="ルビ", default="")  # type: ignore[valid-type]
    ruby_style: EnumProperty(name="ルビ種類", items=_RUBY_STYLE_ITEMS, default="group")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return get_work(context) is not None

    def invoke(self, context, event):
        page, entry, _idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            self.report({"ERROR"}, "選択中のテキストが見つかりません")
            return {"CANCELLED"}
        start, end = self._bounds(entry)
        if start >= end:
            self.report({"ERROR"}, "文字範囲を選択してください")
            return {"CANCELLED"}
        self.start = start
        self.end = end
        style = text_style.style_for_index(entry, start)
        self.font_choice = text_style.dropdown_choice_for_font_path(style[0])
        self.font_size_q = float(style[1])
        self.font_size_pt = float(q_to_pt(float(style[1])))
        self.color = style[2]
        self.font_bold = bool(style[3])
        self.font_italic = bool(style[4])
        self.ruby_text = ""
        self.ruby_style = "group"
        for r_start, r_end, ruby_text, ruby_sty in text_style.ruby_spans_snapshot(entry):
            if r_start == start and r_end == end:
                self.ruby_text = ruby_text
                self.ruby_style = ruby_sty
                break
        text_edit_runtime.suppress_ime_text()
        return context.window_manager.invoke_props_popup(self, event)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "color")
        row = layout.row(align=True)
        row.prop(self, "font_bold", toggle=True)
        row.prop(self, "font_italic", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "font_size_unit", text="")
        row.prop(self, "font_size_value", text="サイズ")
        layout.prop(self, "font_choice")
        layout.separator()
        layout.prop(self, "ruby_text")
        row = layout.row(align=True)
        row.prop(self, "ruby_style", text="")
        op_clear = row.operator("bmanga.text_ruby_clear_inline", text="", icon="X")
        op_clear.page_id = self.page_id
        op_clear.text_id = self.text_id
        op_clear.start = self.start
        op_clear.end = self.end

    def check(self, context):
        page, entry, _idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            return True
        start, end = self._bounds(entry)
        if start >= end:
            return True
        font = text_style.font_path_from_dropdown_choice(self.font_choice)
        text_style.apply_style_span(
            entry, start, end,
            font=font, font_size_q=self.font_size_q,
            color=self.color, bold=self.font_bold, italic=self.font_italic,
        )
        ruby_text = str(self.ruby_text or "").strip()
        if ruby_text:
            text_style.apply_ruby_span(entry, start, end, ruby_text, self.ruby_style)
        else:
            text_style.clear_ruby_spans(entry, start, end)
        layer_stack_utils.tag_view3d_redraw(context)
        return True

    def execute(self, context):
        text_edit_runtime.unsuppress_ime_text()
        page, entry, idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            return {"FINISHED"}
        start, end = self._bounds(entry)
        if start >= end:
            return {"FINISHED"}
        font = text_style.font_path_from_dropdown_choice(self.font_choice)
        text_style.apply_style_span(
            entry, start, end,
            font=font, font_size_q=self.font_size_q,
            color=self.color, bold=self.font_bold, italic=self.font_italic,
        )
        ruby_text = str(self.ruby_text or "").strip()
        if ruby_text:
            text_style.apply_ruby_span(entry, start, end, ruby_text, self.ruby_style)
        else:
            text_style.clear_ruby_spans(entry, start, end)
        page.active_text_index = idx
        self._sync_visual(context, page, entry, start, end)
        return {"FINISHED"}

    def cancel(self, context):
        text_edit_runtime.unsuppress_ime_text()

    def _bounds(self, entry) -> tuple[int, int]:
        body_len = len(str(getattr(entry, "body", "") or ""))
        start = max(0, min(body_len, int(self.start)))
        end = max(start, min(body_len, int(self.end)))
        return start, end

    def _apply_ruby(self, context) -> None:
        page, entry, _idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            return
        start, end = self._bounds(entry)
        if start >= end:
            return
        ruby_text = str(self.ruby_text or "").strip()
        if ruby_text:
            text_style.apply_ruby_span(entry, start, end, ruby_text, self.ruby_style)
        else:
            text_style.clear_ruby_spans(entry, start, end)
        self._sync_visual(context, page, entry, start, end)

    def _apply(self, context) -> bool:
        page, entry, idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            return False
        start, end = self._bounds(entry)
        if start >= end:
            return False
        font = text_style.font_path_from_dropdown_choice(self.font_choice)
        if not text_style.apply_style_span(
            entry,
            start,
            end,
            font=font,
            font_size_q=self.font_size_q,
            color=self.color,
            bold=self.font_bold,
            italic=self.font_italic,
        ):
            return False
        page.active_text_index = idx
        self._sync_visual(context, page, entry, start, end)
        return True

    def _sync_visual(self, context, page, entry, start: int, end: int) -> None:
        active = coma_modal_state.get_active("text_tool") if _active_text_tool_matches(self.page_id, self.text_id) else None
        if active is not None:
            active._selection_anchor = start
            active._cursor_index = end
            idx = _find_text_entry(context, self.page_id, self.text_id)[2]
            active._touch_current_text(context, page, entry, idx)
            text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=True)
        else:
            try:
                text_real_object.ensure_text_real_object(
                    scene=context.scene,
                    entry=entry,
                    page=page,
                )
            except Exception:  # noqa: BLE001
                pass
        layer_stack_utils.tag_view3d_redraw(context)


class BMANGA_OT_text_ruby_clear_inline(Operator):
    """選択範囲のルビを削除する (ポップアップ用)."""

    bl_idname = "bmanga.text_ruby_clear_inline"
    bl_label = "ルビを削除"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    text_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    start: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]
    end: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return get_work(context) is not None

    def execute(self, context):
        page, entry, _idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            return {"CANCELLED"}
        if not text_style.clear_ruby_spans(entry, self.start, self.end):
            self.report({"INFO"}, "削除するルビはありません")
            return {"FINISHED"}
        active = coma_modal_state.get_active("text_tool") if _active_text_tool_matches(self.page_id, self.text_id) else None
        if active is not None:
            active._touch_current_text(context, page, entry, _idx)
            text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=True)
        else:
            try:
                text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
            except Exception:  # noqa: BLE001
                pass
        layer_stack_utils.tag_view3d_redraw(context)
        self.report({"INFO"}, "ルビを削除しました")
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_text_selection_style_popup, BMANGA_OT_text_ruby_clear_inline)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
