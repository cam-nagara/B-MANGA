"""書き出しプリセット管理オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..io import export_presets


def _export_preset_enum_items(self, context):
    presets = export_presets.list_all_presets()
    if not presets:
        return [("NONE", "(プリセットなし)", "", 0)]
    items = []
    for i, p in enumerate(presets):
        items.append((p.name, p.name, p.description or "", i))
    return items


class BMANGA_OT_export_preset_add_local(Operator):
    """新しい書き出しプリセットを追加."""

    bl_idname = "bmanga.export_preset_add_local"
    bl_label = "書き出しプリセット追加"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="名前", default="新規プリセット")  # type: ignore[valid-type]

    def invoke(self, context, event):
        self.preset_name = export_presets.unique_preset_name(None, "新規プリセット")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.preset_name.strip()
        if not name:
            self.report({"ERROR"}, "名前を入力してください")
            return {"CANCELLED"}
        settings = export_presets.get_defaults()
        export_presets.save_preset(name, settings)
        wm = context.window_manager
        if hasattr(wm, "bmanga_export_preset_selector"):
            try:
                wm.bmanga_export_preset_selector = name
            except TypeError:
                pass
        self.report({"INFO"}, f"プリセット追加: {name}")
        return {"FINISHED"}


class BMANGA_OT_export_preset_delete(Operator):
    """書き出しプリセットを削除."""

    bl_idname = "bmanga.export_preset_delete"
    bl_label = "書き出しプリセット削除"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="名前")  # type: ignore[valid-type]

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        name = self.preset_name
        if not name:
            return {"CANCELLED"}
        export_presets.delete_preset(name)
        wm = context.window_manager
        if hasattr(wm, "bmanga_export_preset_selector"):
            presets = export_presets.list_all_presets()
            if presets:
                try:
                    wm.bmanga_export_preset_selector = presets[0].name
                except TypeError:
                    pass
        self.report({"INFO"}, f"プリセット削除: {name}")
        return {"FINISHED"}


class BMANGA_OT_export_preset_rename(Operator):
    """書き出しプリセットの名前を変更."""

    bl_idname = "bmanga.export_preset_rename"
    bl_label = "書き出しプリセット名前変更"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="現在の名前")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前")  # type: ignore[valid-type]

    def invoke(self, context, event):
        self.new_name = self.preset_name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        old = self.preset_name
        new = self.new_name.strip()
        if not old or not new:
            return {"CANCELLED"}
        if old == new:
            return {"FINISHED"}
        result = export_presets.rename_preset(old, new)
        if result is None:
            self.report({"ERROR"}, "名前変更に失敗しました")
            return {"CANCELLED"}
        wm = context.window_manager
        if hasattr(wm, "bmanga_export_preset_selector"):
            try:
                wm.bmanga_export_preset_selector = new
            except TypeError:
                pass
        self.report({"INFO"}, f"名前変更: {old} → {new}")
        return {"FINISHED"}


class BMANGA_OT_export_preset_duplicate(Operator):
    """書き出しプリセットを複製."""

    bl_idname = "bmanga.export_preset_duplicate"
    bl_label = "書き出しプリセット複製"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="元の名前")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前")  # type: ignore[valid-type]

    def invoke(self, context, event):
        self.new_name = export_presets.unique_preset_name(None, f"{self.preset_name} コピー")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        source = self.preset_name
        new = self.new_name.strip()
        if not source or not new:
            return {"CANCELLED"}
        result = export_presets.duplicate_preset(source, new)
        if result is None:
            self.report({"ERROR"}, "複製に失敗しました")
            return {"CANCELLED"}
        wm = context.window_manager
        if hasattr(wm, "bmanga_export_preset_selector"):
            try:
                wm.bmanga_export_preset_selector = new
            except TypeError:
                pass
        self.report({"INFO"}, f"プリセット複製: {new}")
        return {"FINISHED"}


class BMANGA_OT_export_preset_move(Operator):
    """書き出しプリセットの順番を変更."""

    bl_idname = "bmanga.export_preset_move"
    bl_label = "書き出しプリセット移動"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="名前")  # type: ignore[valid-type]
    direction: StringProperty(name="方向", default="UP")  # type: ignore[valid-type]

    def execute(self, context):
        name = self.preset_name
        if not name:
            return {"CANCELLED"}
        export_presets.move_preset(name, self.direction)
        return {"FINISHED"}


def _selected_export_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return ""
    value = str(getattr(wm, "bmanga_export_preset_selector", "") or "")
    return "" if value == "NONE" else value


class BMANGA_OT_export_preset_save(Operator):
    """選択中の書き出しプリセットを上書き保存."""

    bl_idname = "bmanga.export_preset_save"
    bl_label = "書き出しプリセット保存"
    bl_description = "選択中のプリセットの設定を現在の値で上書き保存します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_selected_export_preset_name(context))

    def execute(self, context):
        name = _selected_export_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        preset = export_presets.load_preset_by_name(name)
        if preset is None:
            self.report({"ERROR"}, f"プリセット '{name}' が見つかりません")
            return {"CANCELLED"}
        self.report({"INFO"}, f"プリセット保存: {name}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_export_preset_add_local,
    BMANGA_OT_export_preset_delete,
    BMANGA_OT_export_preset_rename,
    BMANGA_OT_export_preset_duplicate,
    BMANGA_OT_export_preset_move,
    BMANGA_OT_export_preset_save,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bmanga_export_preset_selector = EnumProperty(
        name="書き出しプリセット",
        items=_export_preset_enum_items,
    )


def unregister() -> None:
    try:
        del bpy.types.WindowManager.bmanga_export_preset_selector
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
