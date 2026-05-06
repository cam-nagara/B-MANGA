"""B-Name-Render operators."""

from __future__ import annotations

import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from . import command_runner, command_ui, core, preset_library


def _play_completion_sound() -> None:
    state = core.get_state(bpy.context)
    if state is None or not bool(state.sound_enabled):
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_OK)
    except Exception:  # noqa: BLE001
        pass


class BNAME_RENDER_OT_load_builtin_presets(Operator):
    bl_idname = "bname_render.load_builtin_presets"
    bl_label = "初期プリセットを読み込み"

    reset: BoolProperty(name="現在のプリセットを置き換える", default=False)  # type: ignore[valid-type]

    def execute(self, context):
        count = preset_library.load_builtin_presets(context, reset=bool(self.reset))
        self.report({"INFO"}, f"初期プリセット: {count}")
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_add(Operator):
    bl_idname = "bname_render.preset_add"
    bl_label = "プリセットを追加"

    preset_name: StringProperty(name="プリセット名", default="新規プリセット")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        state = core.get_state(context)
        if state is None:
            return {"CANCELLED"}
        item = state.presets.add()
        item.name = self.preset_name.strip() or "新規プリセット"
        state.active_preset_index = len(state.presets) - 1
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_remove(Operator):
    bl_idname = "bname_render.preset_remove"
    bl_label = "プリセットを削除"

    def execute(self, context):
        state = core.get_state(context)
        if state is None or not state.presets:
            return {"CANCELLED"}
        idx = min(int(state.active_preset_index), len(state.presets) - 1)
        state.presets.remove(idx)
        state.active_preset_index = max(0, idx - 1)
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_settings(Operator):
    bl_idname = "bname_render.preset_settings"
    bl_label = "プリセット設定"

    @classmethod
    def poll(cls, context):
        return core.active_preset(context) is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        preset = core.active_preset(context)
        if preset is None:
            layout.label(text="プリセットが選択されていません", icon="INFO")
            return
        box = layout.box()
        box.label(text="プリセット", icon="PRESET")
        box.prop(preset, "name", text="名前")
        box.label(text=f"カード数: {len(preset.commands)}")

    def execute(self, context):
        return {"FINISHED"} if core.active_preset(context) is not None else {"CANCELLED"}


class BNAME_RENDER_OT_preset_run(Operator):
    bl_idname = "bname_render.preset_run"
    bl_label = "プリセットを実行"

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        try:
            count = command_runner.run_active_preset(context)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"実行失敗: {exc}")
            return {"CANCELLED"}
        _play_completion_sound()
        self.report({"INFO"}, f"実行完了: {count} カード")
        return {"FINISHED"}


class BNAME_RENDER_OT_command_add(Operator):
    bl_idname = "bname_render.command_add"
    bl_label = "カードを追加"

    command_type: EnumProperty(name="種類", items=core.COMMAND_TYPE_ITEMS, default="RENDER")  # type: ignore[valid-type]
    card_name: StringProperty(name="カード名", default="新規カード")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None:
            return {"CANCELLED"}
        idx = min(max(0, int(preset.active_command_index) + 1), len(preset.commands))
        item = preset.commands.add()
        if idx < len(preset.commands) - 1:
            preset.commands.move(len(preset.commands) - 1, idx)
            item = preset.commands[idx]
        item.command_type = self.command_type
        item.name = self.card_name.strip() or self.command_type
        preset.active_command_index = idx
        return {"FINISHED"}


class BNAME_RENDER_OT_command_remove(Operator):
    bl_idname = "bname_render.command_remove"
    bl_label = "カードを削除"

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None or not preset.commands:
            return {"CANCELLED"}
        idx = min(int(preset.active_command_index), len(preset.commands) - 1)
        preset.commands.remove(idx)
        preset.active_command_index = max(0, idx - 1)
        return {"FINISHED"}


class BNAME_RENDER_OT_command_move(Operator):
    bl_idname = "bname_render.command_move"
    bl_label = "カードを移動"

    direction: EnumProperty(name="方向", items=(("UP", "上", ""), ("DOWN", "下", "")), default="UP")  # type: ignore[valid-type]

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None or len(preset.commands) < 2:
            return {"CANCELLED"}
        idx = min(int(preset.active_command_index), len(preset.commands) - 1)
        new_idx = idx - 1 if self.direction == "UP" else idx + 1
        if new_idx < 0 or new_idx >= len(preset.commands):
            return {"CANCELLED"}
        preset.commands.move(idx, new_idx)
        preset.active_command_index = new_idx
        return {"FINISHED"}


class BNAME_RENDER_OT_command_card_click(Operator):
    bl_idname = "bname_render.command_card_click"
    bl_label = "カードを選択"

    index: IntProperty(name="カード", default=0, min=0)  # type: ignore[valid-type]

    def _select_card(self, context):
        state = core.get_state(context)
        preset = core.active_preset(context)
        if state is None or preset is None or not preset.commands:
            return None, None, None
        idx = max(0, min(int(self.index), len(preset.commands) - 1))
        preset.active_command_index = idx
        return state, preset, idx

    def invoke(self, context, _event):
        state, _preset, idx = self._select_card(context)
        if state is None:
            return {"CANCELLED"}
        now = time.monotonic()
        is_double = (
            int(state.last_card_click_index) == idx
            and now - float(state.last_card_click_time) <= 0.45
        )
        state.last_card_click_index = idx
        state.last_card_click_time = now
        if is_double:
            return context.window_manager.invoke_props_dialog(self, width=520)
        return {"FINISHED"}

    def draw(self, context):
        preset = core.active_preset(context)
        if preset is None or not preset.commands:
            return
        command_ui.draw_command(self.layout, preset.commands[preset.active_command_index], context)

    def execute(self, context):
        if self._select_card(context)[0] is None:
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (
    BNAME_RENDER_OT_load_builtin_presets,
    BNAME_RENDER_OT_preset_add,
    BNAME_RENDER_OT_preset_remove,
    BNAME_RENDER_OT_preset_settings,
    BNAME_RENDER_OT_preset_run,
    BNAME_RENDER_OT_command_add,
    BNAME_RENDER_OT_command_remove,
    BNAME_RENDER_OT_command_move,
    BNAME_RENDER_OT_command_card_click,
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
