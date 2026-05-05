"""B-Name-Render panels."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from . import core


class BNAME_RENDER_UL_presets(UIList):
    bl_idname = "BNAME_RENDER_UL_presets"

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        layout.label(text=item.name, icon="PRESET")


class BNAME_RENDER_PT_main(Panel):
    bl_idname = "BNAME_RENDER_PT_main"
    bl_label = "B-Name-Render"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "B-Name-Render"

    def draw(self, context):
        layout = self.layout
        state = core.get_state(context)
        if state is None:
            layout.label(text="設定を初期化できません", icon="ERROR")
            return
        if not state.presets:
            col = layout.column(align=True)
            col.label(text="プリセットがありません")
            op = col.operator("bname_render.load_builtin_presets", text="初期プリセットを読み込み", icon="IMPORT")
            op.reset = True
            return

        row = layout.row()
        row.template_list(
            "BNAME_RENDER_UL_presets",
            "",
            state,
            "presets",
            state,
            "active_preset_index",
            rows=3,
        )
        tools_preset = row.column(align=True)
        tools_preset.operator("bname_render.preset_add", text="", icon="ADD")
        tools_preset.operator("bname_render.preset_remove", text="", icon="REMOVE")
        op = tools_preset.operator("bname_render.load_builtin_presets", text="", icon="FILE_REFRESH")
        op.reset = True

        preset = core.active_preset(context)
        if preset is None:
            return
        layout.prop(preset, "name", text="名前")
        layout.operator("bname_render.preset_run", text="プリセットを実行", icon="RENDER_STILL")

        split = layout.split(factor=0.88)
        cards = split.column(align=True)
        tools = split.column(align=True)
        tools.operator("bname_render.command_add", text="", icon="ADD")
        tools.operator("bname_render.command_remove", text="", icon="REMOVE")
        up = tools.operator("bname_render.command_move", text="", icon="TRIA_UP")
        up.direction = "UP"
        down = tools.operator("bname_render.command_move", text="", icon="TRIA_DOWN")
        down.direction = "DOWN"

        if not preset.commands:
            cards.label(text="カードがありません")
            return

        for index, command in enumerate(preset.commands):
            box = cards.box()
            row = box.row(align=True)
            icon = "CHECKBOX_HLT" if command.enabled else "CHECKBOX_DEHLT"
            selected = index == int(preset.active_command_index)
            label = f"{index + 1:02d}. {command.name}"
            op = row.operator("bname_render.command_card_click", text=label, icon=icon, depress=selected)
            op.index = index
            row.label(text=command.command_type)


_CLASSES = (BNAME_RENDER_UL_presets, BNAME_RENDER_PT_main)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
