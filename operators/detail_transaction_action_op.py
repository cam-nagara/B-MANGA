"""共通詳細ダイアログ内だけで使う、Undoを持たない入れ子編集操作。"""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..utils import detail_popup


_RUBY_STYLE_ITEMS = (
    ("group", "グループルビ", ""),
    ("mono", "モノルビ", ""),
    ("jukugo", "熟語ルビ", ""),
)
_NO_LINKED_BALLOON_PRESET = "NONE"
_LINKED_BALLOON_PRESET_PREFIX = "PRESET:"
_LINKED_BALLOON_PRESET_ITEMS: list[tuple[str, str, str]] = []


def _linked_balloon_preset_items(_self, _context):
    from . import detail_preset_apply_op

    items = [(_NO_LINKED_BALLOON_PRESET, "なし", "フキダシを連動しません")]
    for identifier, label, description in detail_preset_apply_op._detail_preset_entries(
        _context,
        "balloon",
    ):
        items.append(
            (
                f"{_LINKED_BALLOON_PRESET_PREFIX}{identifier}",
                label,
                description,
            )
        )
    _LINKED_BALLOON_PRESET_ITEMS[:] = items
    return _LINKED_BALLOON_PRESET_ITEMS


def _linked_balloon_preset_name(value: object) -> str:
    selected = str(value or "")
    if selected == _NO_LINKED_BALLOON_PRESET:
        return ""
    if not selected.startswith(_LINKED_BALLOON_PRESET_PREFIX):
        raise ValueError("リンクフキダシプリセットを選んでください")
    return selected[len(_LINKED_BALLOON_PRESET_PREFIX) :]


def _required(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}がありません")
    return text


def _find_fixed_entry(
    context,
    owner_id: str,
    entry_id: str,
    collection_name: str,
    shared_collection_name: str,
):
    """固定済みの所有者IDと項目IDが一致する時だけ対象を返す。"""

    owner_key = _required(owner_id, "所有者ID")
    entry_key = _required(entry_id, "対象ID")
    work = get_work(context)
    if work is None:
        return None, None
    if owner_key in {"outside", "__outside__"}:
        for entry in getattr(work, shared_collection_name, ()) or ():
            if str(getattr(entry, "id", "") or "") == entry_key:
                return None, entry
        return None, None
    for page in getattr(work, "pages", ()) or ():
        if str(getattr(page, "id", "") or "") != owner_key:
            continue
        for entry in getattr(page, collection_name, ()) or ():
            if str(getattr(entry, "id", "") or "") == entry_key:
                return page, entry
        return None, None
    return None, None


def _require_action_session(session_token: str, action_id: str, kind: str, target_id: str):
    from . import detail_dialog_runtime

    token = _required(session_token, "詳細設定セッション")
    stable_id = _required(target_id, "詳細設定の対象ID")
    target = detail_dialog_runtime.detail_action_target(token, kind, stable_id)
    if target is None:
        raise ValueError("詳細設定を開いた対象が変更されています")
    if not detail_dialog_runtime.detail_action_is_allowed(token, action_id, kind, stable_id):
        raise ValueError("この操作は詳細設定から実行できません")
    return target


def _fixed_owner_and_entry(target_id: str, page_id: str, entry_id: str) -> tuple[str, str]:
    owner_id, separator, fixed_entry_id = _required(target_id, "詳細設定の対象ID").partition(":")
    if not separator or not owner_id or not fixed_entry_id:
        raise ValueError("詳細設定の固定対象IDが不正です")
    supplied_page = str(page_id or "").strip()
    if supplied_page and supplied_page not in {owner_id, "__outside__" if owner_id == "outside" else owner_id}:
        raise ValueError("詳細設定を開いたページが変更されています")
    if _required(entry_id, "対象ID") != fixed_entry_id:
        raise ValueError("詳細設定を開いた対象が変更されています")
    return owner_id, fixed_entry_id


def _run_transaction(self, context, kind: str, action):
    from . import detail_dialog_runtime

    return detail_dialog_runtime.execute_transactional_detail_action(
        context,
        self.session_token,
        self.bl_idname,
        kind,
        self.target_id,
        action,
    )


def _tail_target(self, context):
    target = _require_action_session(
        self.session_token, self.bl_idname, "balloon", self.target_id
    )
    owner_id, entry_id = _fixed_owner_and_entry(
        target.stable_id, self.page_id, self.balloon_id
    )
    page, entry = _find_fixed_entry(
        context, owner_id, entry_id, "balloons", "shared_balloons"
    )
    if entry is None:
        raise LookupError("フキダシが見つかりません")
    return page, entry


def _sync_tail(context, page, entry) -> None:
    from .balloon_tail_op import _sync_after_tail_change

    _sync_after_tail_change(context, page, entry)
    if page is None:
        from ..utils import balloon_curve_object

        balloon_curve_object.on_balloon_entry_changed(entry)


class BMANGA_OT_detail_tail_add(Operator):
    bl_idname = "bmanga.detail_tail_add"
    bl_label = "しっぽを追加"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        def add_tail(_target):
            page, entry = _tail_target(self, context)
            tail = entry.tails.add()
            tail.type = "straight"
            tail.direction_deg = 270.0
            tail.length_mm = 6.0
            tail.root_width_mm = 3.0
            tail.tip_width_mm = 0.0
            tail.curve_bend = 0.0
            _sync_tail(context, page, entry)

        try:
            _run_transaction(self, context, "balloon", add_tail)
        except Exception as exc:  # 操作単位の復元後にユーザーへ理由を返す
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_detail_tail_remove(Operator):
    bl_idname = "bmanga.detail_tail_remove"
    bl_label = "しっぽを削除"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    tail_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        def remove_tail(_target):
            page, entry = _tail_target(self, context)
            index = int(self.tail_index)
            if not 0 <= index < len(entry.tails):
                raise LookupError("しっぽが見つかりません")
            entry.tails.remove(index)
            _sync_tail(context, page, entry)

        try:
            _run_transaction(self, context, "balloon", remove_tail)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_detail_tail_preset_apply(Operator):
    bl_idname = "bmanga.detail_tail_preset_apply"
    bl_label = "しっぽプリセットを適用"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    tail_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        from ..io import tail_presets
        from .balloon_tail_detail_op import _work_dir

        def apply_tail_preset(_target):
            page, entry = _tail_target(self, context)
            index = int(self.tail_index)
            if not 0 <= index < len(entry.tails):
                raise LookupError("しっぽが見つかりません")
            name = _required(self.preset_name, "プリセット名")
            if name == "NONE":
                raise ValueError("プリセットを選んでください")
            preset = tail_presets.load_preset_by_name(name, _work_dir(context))
            if preset is None:
                raise LookupError(f"プリセットが見つかりません: {name}")
            tail_presets.apply_preset_to_tail(preset, entry.tails[index])
            _sync_tail(context, page, entry)
            return str(preset.name)

        try:
            applied_name = _run_transaction(
                self, context, "balloon", apply_tail_preset
            )
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"しっぽプリセットを適用しました: {applied_name}")
        return {"FINISHED"}


def _text_target(self, context):
    target = _require_action_session(
        self.session_token, self.bl_idname, "text", self.target_id
    )
    owner_id, entry_id = _fixed_owner_and_entry(
        target.stable_id, self.page_id, self.text_id
    )
    page, entry = _find_fixed_entry(
        context, owner_id, entry_id, "texts", "shared_texts"
    )
    if entry is None:
        raise LookupError("テキストが見つかりません")
    return page, entry


def _text_bounds(entry, start: int, end: int) -> tuple[int, int]:
    body_length = len(str(getattr(entry, "body", "") or ""))
    first = max(0, min(body_length, int(start)))
    last = max(first, min(body_length, int(end)))
    return first, last


def _finish_text_dialog(context) -> None:
    from . import text_edit_runtime

    text_edit_runtime.unsuppress_ime_text()
    text_edit_runtime.set_dialog_cursor_override(context, False)


class BMANGA_OT_detail_text_linked_balloon_set(Operator):
    bl_idname = "bmanga.detail_text_linked_balloon_set"
    bl_label = "リンクフキダシプリセットを設定"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: EnumProperty(  # type: ignore[valid-type]
        name="リンクフキダシプリセット",
        items=_linked_balloon_preset_items,
    )

    def execute(self, context):
        from ..io import balloon_presets
        from ..utils import text_balloon_link

        def apply_preset(target):
            name = _linked_balloon_preset_name(self.preset_name)
            if name.startswith("shape:"):
                from ..core.balloon import _SHAPE_ITEMS

                valid_shapes = {str(item[0]) for item in _SHAPE_ITEMS}
                if name.split(":", 1)[1] not in valid_shapes:
                    raise LookupError(f"フキダシ形状が見つかりません: {name}")
            else:
                custom_name = name.split(":", 1)[1] if name.startswith("custom:") else name
                if custom_name and balloon_presets.load_preset_by_name(custom_name) is None:
                    raise LookupError(f"プリセットが見つかりません: {custom_name}")
            text_balloon_link.apply_linked_balloon_preset(
                get_work(context),
                target.data,
                name,
                stable_id=target.stable_id,
            )
            return name

        try:
            _require_action_session(
                self.session_token, self.bl_idname, "text", self.target_id
            )
            selected = _run_transaction(self, context, "text", apply_preset)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"リンクフキダシ: {selected or 'なし'}")
        return {"FINISHED"}


class BMANGA_OT_detail_text_ruby_add(Operator):
    bl_idname = "bmanga.detail_text_ruby_add"
    bl_label = "ルビを付ける"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    text_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="文字数", default=1, min=1)  # type: ignore[valid-type]
    ruby_text: StringProperty(name="ルビ", default="")  # type: ignore[valid-type]
    style: EnumProperty(name="種類", items=_RUBY_STYLE_ITEMS, default="group")  # type: ignore[valid-type]

    def invoke(self, context, event):
        from . import text_edit_runtime

        try:
            _page, entry = _text_target(self, context)
        except (LookupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        start, end = _text_bounds(entry, self.start, self.start + self.length)
        if start >= end:
            self.report({"WARNING"}, "ルビを付ける文字がありません")
            return {"CANCELLED"}
        self.start, self.length = start, end - start
        self.style = str(getattr(entry, "ruby_default_style", "group") or "group")
        text_edit_runtime.suppress_ime_text()
        text_edit_runtime.set_dialog_cursor_override(context, True)
        return detail_popup.invoke_props_dialog(context, event, self, width=320)

    def draw(self, context):
        from .text_ruby_op import _selected_preview

        layout = self.layout
        try:
            _page, entry = _text_target(self, context)
        except (LookupError, ValueError):
            entry = None
        if entry is not None:
            start, end = _text_bounds(entry, self.start, self.start + self.length)
            layout.label(text=f"親文字: {_selected_preview(entry, start, end)}")
        row = layout.row(align=True)
        row.prop(self, "start")
        row.prop(self, "length")
        layout.prop(self, "ruby_text")
        layout.prop(self, "style")

    def execute(self, context):
        from ..utils import text_style
        from .text_ruby_op import _sync_after_ruby_change

        _finish_text_dialog(context)
        def add_ruby(_target):
            page, entry = _text_target(self, context)
            start, end = _text_bounds(entry, self.start, self.start + self.length)
            if start >= end:
                raise ValueError("ルビを付ける文字範囲を指定してください")
            if not text_style.apply_ruby_span(
                entry, start, end, self.ruby_text, self.style
            ):
                raise ValueError("ルビを入力してください")
            _sync_after_ruby_change(context, page, entry, start, end)

        try:
            _run_transaction(self, context, "text", add_ruby)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "ルビを更新しました")
        return {"FINISHED"}

    def cancel(self, context):
        _finish_text_dialog(context)


class BMANGA_OT_detail_text_ruby_clear(Operator):
    bl_idname = "bmanga.detail_text_ruby_clear"
    bl_label = "ルビを削除"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    text_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    start: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]
    end: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        from ..utils import text_style
        from .text_ruby_op import _sync_after_ruby_change

        def clear_ruby(_target):
            page, entry = _text_target(self, context)
            start, end = _text_bounds(entry, self.start, self.end)
            if start >= end:
                raise ValueError("ルビを削除する文字範囲を指定してください")
            if not text_style.clear_ruby_spans(entry, start, end):
                return False
            _sync_after_ruby_change(context, page, entry, start, end)
            return True

        try:
            changed = _run_transaction(self, context, "text", clear_ruby)
            if not changed:
                self.report({"INFO"}, "削除するルビはありません")
                return {"FINISHED"}
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "ルビを削除しました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_detail_tail_add,
    BMANGA_OT_detail_tail_remove,
    BMANGA_OT_detail_tail_preset_apply,
    BMANGA_OT_detail_text_linked_balloon_set,
    BMANGA_OT_detail_text_ruby_add,
    BMANGA_OT_detail_text_ruby_clear,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "BMANGA_OT_detail_tail_add",
    "BMANGA_OT_detail_tail_preset_apply",
    "BMANGA_OT_detail_tail_remove",
    "BMANGA_OT_detail_text_linked_balloon_set",
    "BMANGA_OT_detail_text_ruby_add",
    "BMANGA_OT_detail_text_ruby_clear",
    "register",
    "unregister",
]
