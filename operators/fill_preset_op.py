"""囲い塗りプリセット CRUD オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.work import get_active_page
from ..io import fill_presets
from ..utils import detail_popup, log

_logger = log.get_logger(__name__)


def _selected_fill_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    value = str(getattr(wm, "bmanga_fill_tool_preset_selector", "") or "") if wm else ""
    if not value:
        return ""
    return value


def _set_fill_preset_selector(context, name: str) -> None:
    """リネーム・削除等の後始末用のセレクタ再設定 (選択中レイヤーへは適用しない)."""
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_fill_tool_preset_selector"):
        return
    from . import preset_op

    try:
        with preset_op.suppress_selector_apply():
            wm.bmanga_fill_tool_preset_selector = name
    except TypeError:
        pass


class BMANGA_OT_fill_preset_add_local(Operator):
    """新しい囲い塗りプリセットを追加する."""

    bl_idname = "bmanga.fill_preset_add_local"
    bl_label = "囲い塗りプリセットを追加"
    bl_description = "新しい囲い塗りプリセットを追加します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="新規囲い塗りプリセット")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return True

    def invoke(self, context, event):
        self.preset_name = fill_presets.unique_preset_name(self.preset_name or "新規囲い塗りプリセット")
        return detail_popup.invoke_props_dialog(context, event, self)

    def execute(self, context):
        name = fill_presets.unique_preset_name(self.preset_name.strip() or "新規囲い塗りプリセット")
        entry_data = {"color": [0, 0, 0, 1], "opacity": 100}
        # Try to snapshot from active fill entry
        page = get_active_page(context)
        if page is not None:
            fills = getattr(context.scene, "bmanga_fill_layers", None)
            if fills:
                for fill in fills:
                    if getattr(fill, "selected", False):
                        entry_data = fill_presets.snapshot_from_entry(fill)
                        break
        try:
            fill_presets.save_local_preset(name, self.description, entry_data)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("fill_preset_add_local failed")
            self.report({"ERROR"}, f"追加失敗: {exc}")
            return {"CANCELLED"}
        _set_fill_preset_selector(context, name)
        self.report({"INFO"}, f"囲い塗りプリセット追加: {name}")
        return {"FINISHED"}


class BMANGA_OT_fill_preset_save(Operator):
    """選択中の囲い塗り設定で現在のプリセットを上書き保存する."""

    bl_idname = "bmanga.fill_preset_save"
    bl_label = "囲い塗りプリセットを上書き保存"
    bl_description = "現在の設定で選択中のプリセットを上書き保存します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_selected_fill_preset_name(context))

    def execute(self, context):
        name = _selected_fill_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        entry_data = {"color": [0, 0, 0, 1], "opacity": 100}
        fills = getattr(context.scene, "bmanga_fill_layers", None)
        if fills:
            for fill in fills:
                if getattr(fill, "selected", False):
                    entry_data = fill_presets.snapshot_from_entry(fill)
                    break
        try:
            fill_presets.save_local_preset(name, "", entry_data)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("fill_preset_save failed")
            self.report({"ERROR"}, f"上書き保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"囲い塗りプリセット上書き保存: {name}")
        return {"FINISHED"}


class BMANGA_OT_fill_preset_rename(Operator):
    """選択中の囲い塗りプリセットを改名する."""

    bl_idname = "bmanga.fill_preset_rename"
    bl_label = "囲い塗りプリセットを改名"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="現在の名前", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_fill_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_fill_preset_name(context)
        self.preset_name = selected
        self.new_name = selected
        return detail_popup.invoke_props_dialog(context, event, self)

    def execute(self, context):
        old_name = self.preset_name.strip() or _selected_fill_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            fill_presets.rename_preset(old_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("fill_preset_rename failed")
            self.report({"ERROR"}, f"改名失敗: {exc}")
            return {"CANCELLED"}
        _set_fill_preset_selector(context, new_name)
        self.report({"INFO"}, f"囲い塗りプリセット改名: {old_name} → {new_name}")
        return {"FINISHED"}


class BMANGA_OT_fill_preset_duplicate(Operator):
    """選択中の囲い塗りプリセットを複製する."""

    bl_idname = "bmanga.fill_preset_duplicate"
    bl_label = "囲い塗りプリセットを複製"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="複製元", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_fill_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_fill_preset_name(context)
        self.preset_name = selected
        self.new_name = fill_presets.unique_preset_name(f"{selected} コピー")
        return detail_popup.invoke_props_dialog(context, event, self)

    def execute(self, context):
        source_name = self.preset_name.strip() or _selected_fill_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            fill_presets.duplicate_preset(source_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("fill_preset_duplicate failed")
            self.report({"ERROR"}, f"複製失敗: {exc}")
            return {"CANCELLED"}
        _set_fill_preset_selector(context, new_name)
        self.report({"INFO"}, f"囲い塗りプリセット複製: {new_name}")
        return {"FINISHED"}


class BMANGA_OT_fill_preset_delete(Operator):
    """選択中の囲い塗りプリセットを削除する."""

    bl_idname = "bmanga.fill_preset_delete"
    bl_label = "囲い塗りプリセットを削除"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_fill_preset_name(context))

    def invoke(self, context, event):
        self.preset_name = self.preset_name or _selected_fill_preset_name(context)
        return detail_popup.invoke_confirm(context, event, self)

    def execute(self, context):
        name = self.preset_name.strip() or _selected_fill_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        all_p = fill_presets.list_all_presets()
        names = [p.name for p in all_p]
        fallback = ""
        if name in names and len(names) > 1:
            idx = names.index(name)
            fallback = names[idx + 1] if idx + 1 < len(names) else names[idx - 1]
        try:
            fill_presets.delete_preset(name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("fill_preset_delete failed")
            self.report({"ERROR"}, f"削除失敗: {exc}")
            return {"CANCELLED"}
        if fallback:
            _set_fill_preset_selector(context, fallback)
        self.report({"INFO"}, f"囲い塗りプリセット削除: {name}")
        return {"FINISHED"}


class BMANGA_OT_fill_preset_move(Operator):
    """選択中の囲い塗りプリセットを並べ替える."""

    bl_idname = "bmanga.fill_preset_move"
    bl_label = "囲い塗りプリセットを並べ替え"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]
    direction: StringProperty(name="方向", default="UP")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_fill_preset_name(context))

    def execute(self, context):
        name = self.preset_name.strip() or _selected_fill_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        try:
            fill_presets.move_preset(name, self.direction)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"並べ替え失敗: {exc}")
            return {"CANCELLED"}
        _set_fill_preset_selector(context, name)
        self.report({"INFO"}, f"囲い塗りプリセット並べ替え: {name}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_fill_preset_add_local,
    BMANGA_OT_fill_preset_save,
    BMANGA_OT_fill_preset_rename,
    BMANGA_OT_fill_preset_duplicate,
    BMANGA_OT_fill_preset_delete,
    BMANGA_OT_fill_preset_move,
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
