"""テキストプリセット CRUD オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator

from ..core.work import get_active_page, get_work
from ..io import balloon_presets, text_presets
from ..utils import log

_logger = log.get_logger(__name__)

# BMANGA_MT_linked_balloon_preset.draw() が active_text_index に依存せず対象
# テキストを一意に特定できるようにするための一時受け渡し変数。
# メニュー描画の直前に呼び出し側 (panels/layer_stack_detail_ui.py,
# operators/layer_detail_op.py) が描画対象 entry.id を設定する。
# Blender のメニュー描画は単一スレッドで行われるため、モジュールレベル
# 変数でのコンテキスト受け渡しは安全。
_linked_balloon_target_text_id: str = ""


def linked_balloon_preset_display(value: str) -> str:
    if not value:
        return "なし"
    return value


def _selected_text_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    value = str(getattr(wm, "bmanga_text_tool_preset_selector", "") or "") if wm else ""
    if value == "NONE" or not value:
        return ""
    return value


def _set_text_preset_selector(context, name: str) -> None:
    """リネーム・削除等の後始末用のセレクタ再設定 (選択中レイヤーへは適用しない)."""
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_text_tool_preset_selector"):
        return
    from . import preset_op

    try:
        with preset_op.suppress_selector_apply():
            wm.bmanga_text_tool_preset_selector = name
    except TypeError:
        pass


class BMANGA_OT_text_preset_add_local(Operator):
    """現在のテキスト設定を新しいプリセットとして追加する."""

    bl_idname = "bmanga.text_preset_add_local"
    bl_label = "テキストプリセットを追加"
    bl_description = "現在のテキスト設定を新しいプリセットとして追加します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="新規テキストプリセット")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return True

    def invoke(self, context, event):
        self.preset_name = text_presets.unique_preset_name(self.preset_name or "新規テキストプリセット")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = text_presets.unique_preset_name(self.preset_name.strip() or "新規テキストプリセット")
        # Try to snapshot from active text entry, otherwise use defaults
        entry_data = {}
        page = get_active_page(context)
        if page is not None and 0 <= page.active_text_index < len(page.texts):
            entry = page.texts[page.active_text_index]
            entry_data = text_presets.snapshot_from_entry(entry)
        if not entry_data:
            # Minimal default data
            entry_data = {
                "writing_mode": "vertical",
                "font_size_unit": "q",
                "font_size_value": 20.0,
                "line_height": 1.4,
                "letter_spacing": 0.0,
                "color": [0.0, 0.0, 0.0, 1.0],
            }
        try:
            text_presets.save_local_preset(None, name, self.description, entry_data)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("text_preset_add_local failed")
            self.report({"ERROR"}, f"追加失敗: {exc}")
            return {"CANCELLED"}
        _set_text_preset_selector(context, name)
        self.report({"INFO"}, f"テキストプリセット追加: {name}")
        return {"FINISHED"}


class BMANGA_OT_text_preset_save(Operator):
    """選択中のテキスト設定で現在のプリセットを上書き保存する."""

    bl_idname = "bmanga.text_preset_save"
    bl_label = "テキストプリセットを上書き保存"
    bl_description = "現在のテキスト設定で選択中のプリセットを上書き保存します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _selected_text_preset_name(context):
            return False
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_text_index < len(page.texts)

    def execute(self, context):
        name = _selected_text_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        page = get_active_page(context)
        if page is None or not (0 <= page.active_text_index < len(page.texts)):
            self.report({"ERROR"}, "テキストが選択されていません")
            return {"CANCELLED"}
        entry = page.texts[page.active_text_index]
        entry_data = text_presets.snapshot_from_entry(entry)
        try:
            text_presets.save_local_preset(None, name, "", entry_data)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("text_preset_save failed")
            self.report({"ERROR"}, f"上書き保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"テキストプリセット上書き保存: {name}")
        return {"FINISHED"}


class BMANGA_OT_text_preset_rename(Operator):
    """選択中のテキストプリセットを改名する."""

    bl_idname = "bmanga.text_preset_rename"
    bl_label = "テキストプリセットを改名"
    bl_description = "選択中のテキストプリセットを改名します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="現在の名前", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_text_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_text_preset_name(context)
        self.preset_name = selected
        self.new_name = selected
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        old_name = self.preset_name.strip() or _selected_text_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            text_presets.rename_preset(old_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("text_preset_rename failed")
            self.report({"ERROR"}, f"改名失敗: {exc}")
            return {"CANCELLED"}
        _set_text_preset_selector(context, new_name)
        self.report({"INFO"}, f"テキストプリセット改名: {old_name} → {new_name}")
        return {"FINISHED"}


class BMANGA_OT_text_preset_duplicate(Operator):
    """選択中のテキストプリセットを複製する."""

    bl_idname = "bmanga.text_preset_duplicate"
    bl_label = "テキストプリセットを複製"
    bl_description = "選択中のテキストプリセットを複製します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="複製元", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_text_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_text_preset_name(context)
        self.preset_name = selected
        self.new_name = text_presets.unique_preset_name(f"{selected} コピー")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        source_name = self.preset_name.strip() or _selected_text_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            text_presets.duplicate_preset(source_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("text_preset_duplicate failed")
            self.report({"ERROR"}, f"複製失敗: {exc}")
            return {"CANCELLED"}
        _set_text_preset_selector(context, new_name)
        self.report({"INFO"}, f"テキストプリセット複製: {new_name}")
        return {"FINISHED"}


class BMANGA_OT_text_preset_delete(Operator):
    """選択中のテキストプリセットを削除する."""

    bl_idname = "bmanga.text_preset_delete"
    bl_label = "テキストプリセットを削除"
    bl_description = "選択中のテキストプリセットを削除します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_text_preset_name(context))

    def invoke(self, context, event):
        self.preset_name = self.preset_name or _selected_text_preset_name(context)
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        name = self.preset_name.strip() or _selected_text_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        # Find fallback before deleting
        all_presets = text_presets.list_all_presets(None)
        names = [p.name for p in all_presets]
        fallback = ""
        if name in names and len(names) > 1:
            idx = names.index(name)
            fallback = names[idx + 1] if idx + 1 < len(names) else names[idx - 1]
        try:
            text_presets.delete_preset(name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("text_preset_delete failed")
            self.report({"ERROR"}, f"削除失敗: {exc}")
            return {"CANCELLED"}
        if fallback:
            _set_text_preset_selector(context, fallback)
        self.report({"INFO"}, f"テキストプリセット削除: {name}")
        return {"FINISHED"}


class BMANGA_OT_text_preset_move(Operator):
    """選択中のテキストプリセットを並べ替える."""

    bl_idname = "bmanga.text_preset_move"
    bl_label = "テキストプリセットを並べ替え"
    bl_description = "選択中のテキストプリセットを上下に移動します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]
    direction: StringProperty(name="方向", default="UP")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_text_preset_name(context))

    def execute(self, context):
        name = self.preset_name.strip() or _selected_text_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        try:
            text_presets.move_preset(name, self.direction)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"並べ替え失敗: {exc}")
            return {"CANCELLED"}
        _set_text_preset_selector(context, name)
        self.report({"INFO"}, f"テキストプリセット並べ替え: {name}")
        return {"FINISHED"}


class BMANGA_OT_set_linked_balloon_preset(Operator):
    """アクティブなテキストのリンクフキダシプリセットを設定する."""

    bl_idname = "bmanga.set_linked_balloon_preset"
    bl_label = "リンクフキダシプリセットを設定"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(default="")  # type: ignore[valid-type]
    text_id: StringProperty(default="")  # type: ignore[valid-type]

    def execute(self, context):
        work = get_work(context)
        if work is None:
            self.report({"ERROR"}, "テキストが選択されていません")
            return {"CANCELLED"}
        target_id = self.text_id or _linked_balloon_target_text_id
        if target_id == "__PRESET_SCRATCH__":
            entry = getattr(context.window_manager, "bmanga_preset_scratch_text", None)
            if entry is not None:
                entry.linked_balloon_preset = self.preset_name
                return {"FINISHED"}
            self.report({"WARNING"}, "プリセット編集対象が見つかりません")
            return {"CANCELLED"}
        entry = None
        owner_page = None
        if target_id:
            # text_id が明示されている場合は、全ページの texts と
            # work.shared_texts (ページ外へ出した共有テキスト) を横断して
            # id で解決する。ここでアクティブページの active_text_index へ
            # 盲目的にフォールバックすると、共有テキスト選択時などに無関係な
            # ページテキストとその親フキダシを黙って書き換えてしまうため、
            # 見つからなければフォールバックせずに CANCELLED する。
            from ..utils import text_real_object

            owner_page, entry = text_real_object.find_text_entry(context.scene, target_id)
            if entry is None:
                self.report({"WARNING"}, "対象のテキストが見つかりません")
                return {"CANCELLED"}
        else:
            # text_id 未指定のときだけ、アクティブページのアクティブテキストへ
            # フォールバックする。
            page = get_active_page(context)
            if page is not None and 0 <= page.active_text_index < len(page.texts):
                entry = page.texts[page.active_text_index]
                owner_page = page
        if entry is None:
            self.report({"ERROR"}, "テキストが選択されていません")
            return {"CANCELLED"}
        from ..utils import text_balloon_link

        text_balloon_link.apply_linked_balloon_preset(
            work,
            entry,
            self.preset_name,
            page=owner_page,
            stable_id=target_id,
        )
        return {"FINISHED"}


class BMANGA_MT_linked_balloon_preset(Menu):
    """リンクフキダシプリセットを選択するメニュー."""

    bl_idname = "BMANGA_MT_linked_balloon_preset"
    bl_label = "リンクフキダシプリセット"

    def draw(self, context):
        layout = self.layout
        target_id = _linked_balloon_target_text_id
        op = layout.operator(BMANGA_OT_set_linked_balloon_preset.bl_idname, text="なし")
        op.preset_name = ""
        op.text_id = target_id
        layout.separator()
        for preset in balloon_presets.list_all_presets(None):
            op = layout.operator(BMANGA_OT_set_linked_balloon_preset.bl_idname, text=preset.name)
            op.preset_name = preset.name
            op.text_id = target_id


_CLASSES = (
    BMANGA_OT_text_preset_add_local,
    BMANGA_OT_text_preset_save,
    BMANGA_OT_text_preset_rename,
    BMANGA_OT_text_preset_duplicate,
    BMANGA_OT_text_preset_delete,
    BMANGA_OT_text_preset_move,
    BMANGA_OT_set_linked_balloon_preset,
    BMANGA_MT_linked_balloon_preset,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
