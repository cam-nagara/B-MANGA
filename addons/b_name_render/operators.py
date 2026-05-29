"""B-Name-Render operators."""

from __future__ import annotations

import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from . import command_runner, command_ui, core, defaults_store, preset_library


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
        state = core.get_state(context)
        core.ensure_default_categories(state)
        core.migrate_preset_categories(state)
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
        core.set_active_preset_index(context, len(state.presets) - 1)
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_remove(Operator):
    bl_idname = "bname_render.preset_remove"
    bl_label = "プリセットを削除"

    def execute(self, context):
        state = core.get_state(context)
        if state is None or not state.presets:
            return {"CANCELLED"}
        idx = min(core.get_active_preset_index(context), len(state.presets) - 1)
        state.presets.remove(idx)
        core.set_active_preset_index(context, max(0, idx - 1))
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_move(Operator):
    bl_idname = "bname_render.preset_move"
    bl_label = "プリセットを移動"

    direction: EnumProperty(name="方向", items=(("UP", "上", ""), ("DOWN", "下", "")), default="UP")  # type: ignore[valid-type]

    def execute(self, context):
        state = core.get_state(context)
        if state is None or len(state.presets) < 2:
            return {"CANCELLED"}
        idx = min(core.get_active_preset_index(context), len(state.presets) - 1)
        new_idx = idx - 1 if self.direction == "UP" else idx + 1
        if new_idx < 0 or new_idx >= len(state.presets):
            return {"CANCELLED"}
        state.presets.move(idx, new_idx)
        core.set_active_preset_index(context, new_idx)
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_settings(Operator):
    bl_idname = "bname_render.preset_settings"
    bl_label = "プリセット設定"

    @classmethod
    def poll(cls, context):
        return core.active_preset(context) is not None

    def invoke(self, context, _event):
        # ドロップダウンに候補が並ぶよう、カテゴリのデータだけ用意する。
        # ここでは各プリセットへの自動移行 (preset.category 書き込み) はしない
        # (削除で意図的に未分類にしたものを開く度に再分類してしまうのと、
        #  重いシーンでの一括書き込みを避けるため)。名前からの既定は表示時に
        #  effective_preset_category がフォールバックとして補う。
        state = core.get_state(context)
        core.ensure_default_categories(state)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        preset = core.active_preset(context)
        state = core.get_state(context)
        if preset is None or state is None:
            layout.label(text="プリセットが選択されていません", icon="INFO")
            return
        box = layout.box()
        box.label(text="プリセット", icon="PRESET")
        box.prop(preset, "name", text="名前")
        box.prop_search(preset, "category", state, "categories", text="カテゴリ")
        box.label(text=f"コマンド数: {len(preset.commands)}")

    def execute(self, context):
        return {"FINISHED"} if core.active_preset(context) is not None else {"CANCELLED"}


class BNAME_RENDER_OT_category_add(Operator):
    bl_idname = "bname_render.category_add"
    bl_label = "カテゴリを追加"
    bl_description = "プリセットの表示カテゴリを追加する"

    category_name: StringProperty(name="カテゴリ名", default="新規カテゴリ")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        state = core.get_state(context)
        if state is None:
            return {"CANCELLED"}
        core.ensure_default_categories(state)
        name = self.category_name.strip()
        if not name or name in ("すべて", core._ALL_CATEGORY):
            self.report({"ERROR"}, "その名前は使えません")
            return {"CANCELLED"}
        if any(str(getattr(c, "name", "") or "") == name for c in state.categories):
            self.report({"WARNING"}, f"カテゴリ「{name}」は既にあります")
            try:
                context.window_manager.bname_render_preset_category = name
            except (TypeError, ValueError):
                pass
            return {"CANCELLED"}
        state.categories.add().name = name
        try:
            context.window_manager.bname_render_preset_category = name
        except (TypeError, ValueError):
            pass
        self.report({"INFO"}, f"カテゴリを追加: {name}")
        return {"FINISHED"}


class BNAME_RENDER_OT_category_remove(Operator):
    bl_idname = "bname_render.category_remove"
    bl_label = "カテゴリを削除"
    bl_description = "選択中のカテゴリを削除する (所属プリセットは未分類になります)"

    @classmethod
    def poll(cls, context):
        wm = getattr(context, "window_manager", None)
        cat = str(getattr(wm, "bname_render_preset_category", "") or "")
        return cat not in ("", core._ALL_CATEGORY)

    def invoke(self, context, _event):
        cat = str(getattr(context.window_manager, "bname_render_preset_category", "") or "")
        return context.window_manager.invoke_confirm(
            self, _event,
            title="カテゴリを削除",
            message=f"カテゴリ「{cat}」を削除します。所属プリセットは未分類になります。",
            confirm_text="削除",
        )

    def execute(self, context):
        state = core.get_state(context)
        wm = context.window_manager
        if state is None:
            return {"CANCELLED"}
        core.ensure_default_categories(state)
        target = str(getattr(wm, "bname_render_preset_category", "") or "")
        if target in ("", core._ALL_CATEGORY):
            return {"CANCELLED"}
        for preset in state.presets:
            if str(getattr(preset, "category", "") or "") == target:
                preset.category = ""
        idx = next((i for i, c in enumerate(state.categories) if str(getattr(c, "name", "") or "") == target), -1)
        if idx >= 0:
            state.categories.remove(idx)
        try:
            wm.bname_render_preset_category = core._ALL_CATEGORY
        except (TypeError, ValueError):
            pass
        self.report({"INFO"}, f"カテゴリを削除: {target}")
        return {"FINISHED"}


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
        self.report({"INFO"}, f"実行完了: {count} コマンド")
        return {"FINISHED"}


class BNAME_RENDER_OT_command_add(Operator):
    bl_idname = "bname_render.command_add"
    bl_label = "コマンドを追加"

    command_type: EnumProperty(name="種類", items=core.COMMAND_TYPE_ITEMS, default="RENDER")  # type: ignore[valid-type]
    card_name: StringProperty(name="コマンド名", default="新規コマンド")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None:
            return {"CANCELLED"}
        idx = min(max(0, core.get_active_command_index(context) + 1), len(preset.commands))
        item = preset.commands.add()
        if idx < len(preset.commands) - 1:
            preset.commands.move(len(preset.commands) - 1, idx)
            item = preset.commands[idx]
        item.command_type = self.command_type
        item.name = self.card_name.strip() or self.command_type
        core.set_active_command_index(context, idx)
        return {"FINISHED"}


class BNAME_RENDER_OT_command_remove(Operator):
    bl_idname = "bname_render.command_remove"
    bl_label = "コマンドを削除"

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None or not preset.commands:
            return {"CANCELLED"}
        idx = min(core.get_active_command_index(context), len(preset.commands) - 1)
        preset.commands.remove(idx)
        core.set_active_command_index(context, max(0, idx - 1))
        return {"FINISHED"}


class BNAME_RENDER_OT_command_duplicate(Operator):
    bl_idname = "bname_render.command_duplicate"
    bl_label = "コマンドを複製"
    bl_description = "選択中のコマンドを複製して直下に挿入"

    @classmethod
    def poll(cls, context):
        preset = core.active_preset(context)
        return preset is not None and len(preset.commands) > 0

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None or not preset.commands:
            return {"CANCELLED"}
        src_idx = max(0, min(core.get_active_command_index(context), len(preset.commands) - 1))
        data = defaults_store._command_to_dict(preset.commands[src_idx])
        new_item = preset.commands.add()
        defaults_store._apply_dict(new_item, data)
        dst_idx = src_idx + 1
        preset.commands.move(len(preset.commands) - 1, dst_idx)
        core.set_active_command_index(context, dst_idx)
        return {"FINISHED"}


class BNAME_RENDER_OT_command_add_block(Operator):
    bl_idname = "bname_render.command_add_block"
    bl_label = "出力ブロックを追加"
    bl_description = "退避→レンダー→復元の1出力ブロックをまとめて末尾に追加"

    @classmethod
    def poll(cls, context):
        return core.active_preset(context) is not None

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None:
            return {"CANCELLED"}
        first_idx = len(preset.commands)
        for command_type in ("STATE_BEGIN", "RENDER_LAYER", "STATE_END"):
            preset.commands.add().command_type = command_type
        core.set_active_command_index(context, first_idx)
        self.report({"INFO"}, "出力ブロックを追加しました")
        return {"FINISHED"}


class BNAME_RENDER_OT_command_move(Operator):
    bl_idname = "bname_render.command_move"
    bl_label = "コマンドを移動"

    direction: EnumProperty(name="方向", items=(("UP", "上", ""), ("DOWN", "下", "")), default="UP")  # type: ignore[valid-type]

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None or len(preset.commands) < 2:
            return {"CANCELLED"}
        idx = min(core.get_active_command_index(context), len(preset.commands) - 1)
        new_idx = idx - 1 if self.direction == "UP" else idx + 1
        if new_idx < 0 or new_idx >= len(preset.commands):
            return {"CANCELLED"}
        preset.commands.move(idx, new_idx)
        core.set_active_command_index(context, new_idx)
        return {"FINISHED"}


class BNAME_RENDER_OT_command_card_click(Operator):
    bl_idname = "bname_render.command_card_click"
    bl_label = "コマンドを選択"

    index: IntProperty(name="コマンド", default=0, min=0)  # type: ignore[valid-type]

    def _select_card(self, context):
        state = core.get_state(context)
        preset = core.active_preset(context)
        if state is None or preset is None or not preset.commands:
            return None, None, None
        idx = max(0, min(int(self.index), len(preset.commands) - 1))
        core.set_active_command_index(context, idx)
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
        command = core.active_command(context)
        if command is None:
            return
        command_ui.draw_command(self.layout, command, context)

    def execute(self, context):
        if self._select_card(context)[0] is None:
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_defaults_register(Operator):
    bl_idname = "bname_render.preset_defaults_register"
    bl_label = "初期設定に登録"
    bl_description = "選択中プリセットの現在のコマンド構成を、ユーザー共通の初期設定として保存"

    @classmethod
    def poll(cls, context):
        return core.active_preset(context) is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(
            self, _event,
            title="初期設定に登録",
            message="このプリセットの現在のコマンド構成を初期設定として保存します。",
            confirm_text="登録",
        )

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None:
            return {"CANCELLED"}
        try:
            defaults_store.save_preset_default(preset.name, preset)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"初期設定に登録: {preset.name}")
        return {"FINISHED"}


class BNAME_RENDER_OT_preset_defaults_restore(Operator):
    bl_idname = "bname_render.preset_defaults_restore"
    bl_label = "初期設定に戻す"
    bl_description = "選択中プリセットのコマンド構成を、登録済みの初期設定（無ければ組み込み既定）へ戻す"

    @classmethod
    def poll(cls, context):
        return core.active_preset(context) is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(
            self, _event,
            title="初期設定に戻す",
            message="このプリセットのコマンド構成を初期設定へ戻します。現在の内容は失われます。",
            confirm_text="戻す",
        )

    def execute(self, context):
        preset = core.active_preset(context)
        if preset is None:
            return {"CANCELLED"}
        cmds = defaults_store.get_preset_default(preset.name)
        source = "登録済み初期設定"
        if cmds is None:
            cmds = preset_library.BUILTIN_PRESETS.get(preset.name)
            source = "組み込み既定"
        if cmds is None:
            self.report({"WARNING"}, "このプリセットの初期設定がありません")
            return {"CANCELLED"}
        try:
            defaults_store.apply_commands(preset, list(cmds))
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"復元失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{source}に戻しました: {preset.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_RENDER_OT_load_builtin_presets,
    BNAME_RENDER_OT_preset_add,
    BNAME_RENDER_OT_preset_remove,
    BNAME_RENDER_OT_preset_move,
    BNAME_RENDER_OT_preset_settings,
    BNAME_RENDER_OT_category_add,
    BNAME_RENDER_OT_category_remove,
    BNAME_RENDER_OT_preset_run,
    BNAME_RENDER_OT_command_add,
    BNAME_RENDER_OT_command_remove,
    BNAME_RENDER_OT_command_duplicate,
    BNAME_RENDER_OT_command_add_block,
    BNAME_RENDER_OT_command_move,
    BNAME_RENDER_OT_command_card_click,
    BNAME_RENDER_OT_preset_defaults_register,
    BNAME_RENDER_OT_preset_defaults_restore,
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
