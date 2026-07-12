"""フキダシプリセット CRUD オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..io import balloon_presets
from ..utils import log

_logger = log.get_logger(__name__)


def _selected_balloon_preset_name(context) -> str:
    """Get the currently selected balloon preset name from the tool selector.

    The balloon selector uses composite ids like "custom:<name>" for custom presets.
    """
    wm = getattr(context, "window_manager", None)
    value = str(getattr(wm, "bmanga_balloon_tool_preset_selector", "") or "") if wm else ""
    if value.startswith("custom:"):
        return value.split(":", 1)[1]
    return ""


def _set_balloon_preset_selector(context, name: str) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_balloon_tool_preset_selector"):
        return
    try:
        wm.bmanga_balloon_tool_preset_selector = f"custom:{name}" if name else "DEFAULT"
    except TypeError:
        pass


class BMANGA_OT_balloon_preset_rename(Operator):
    """選択中のフキダシプリセットを改名する."""

    bl_idname = "bmanga.balloon_preset_rename"
    bl_label = "フキダシプリセットを改名"
    bl_description = "選択中のフキダシプリセットを改名します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="現在の名前", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_balloon_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_balloon_preset_name(context)
        self.preset_name = selected
        self.new_name = selected
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        old_name = self.preset_name.strip() or _selected_balloon_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            balloon_presets.rename_preset(old_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("balloon_preset_rename failed")
            self.report({"ERROR"}, f"改名失敗: {exc}")
            return {"CANCELLED"}
        _set_balloon_preset_selector(context, new_name)
        self.report({"INFO"}, f"フキダシプリセット改名: {old_name} → {new_name}")
        return {"FINISHED"}


class BMANGA_OT_balloon_preset_duplicate(Operator):
    """選択中のフキダシプリセットを複製する."""

    bl_idname = "bmanga.balloon_preset_duplicate"
    bl_label = "フキダシプリセットを複製"
    bl_description = "選択中のフキダシプリセットを複製します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="複製元", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_balloon_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_balloon_preset_name(context)
        self.preset_name = selected
        self.new_name = balloon_presets.unique_preset_name(f"{selected} コピー")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        source_name = self.preset_name.strip() or _selected_balloon_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            balloon_presets.duplicate_preset(source_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("balloon_preset_duplicate failed")
            self.report({"ERROR"}, f"複製失敗: {exc}")
            return {"CANCELLED"}
        _set_balloon_preset_selector(context, new_name)
        self.report({"INFO"}, f"フキダシプリセット複製: {new_name}")
        return {"FINISHED"}


class BMANGA_OT_balloon_preset_delete(Operator):
    """選択中のフキダシプリセットを削除する."""

    bl_idname = "bmanga.balloon_preset_delete"
    bl_label = "フキダシプリセットを削除"
    bl_description = "選択中のフキダシプリセットを削除します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_balloon_preset_name(context))

    def invoke(self, context, event):
        self.preset_name = self.preset_name or _selected_balloon_preset_name(context)
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        name = self.preset_name.strip() or _selected_balloon_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        try:
            balloon_presets.delete_preset(name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("balloon_preset_delete failed")
            self.report({"ERROR"}, f"削除失敗: {exc}")
            return {"CANCELLED"}
        _set_balloon_preset_selector(context, "")
        self.report({"INFO"}, f"フキダシプリセット削除: {name}")
        return {"FINISHED"}


class BMANGA_OT_balloon_preset_move(Operator):
    """選択中のフキダシプリセットを並べ替える."""

    bl_idname = "bmanga.balloon_preset_move"
    bl_label = "フキダシプリセットを並べ替え"
    bl_description = "選択中のフキダシプリセットを上下に移動します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]
    direction: StringProperty(name="方向", default="UP")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_balloon_preset_name(context))

    def execute(self, context):
        name = self.preset_name.strip() or _selected_balloon_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        try:
            balloon_presets.move_preset(name, self.direction)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"並べ替え失敗: {exc}")
            return {"CANCELLED"}
        _set_balloon_preset_selector(context, name)
        self.report({"INFO"}, f"フキダシプリセット並べ替え: {name}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_balloon_preset_rename,
    BMANGA_OT_balloon_preset_duplicate,
    BMANGA_OT_balloon_preset_delete,
    BMANGA_OT_balloon_preset_move,
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
