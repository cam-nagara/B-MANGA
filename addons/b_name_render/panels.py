"""B-Name-Render panels."""

from __future__ import annotations

import math

import bpy
from bpy.types import Panel, UIList

from . import command_ui, core


class BNAME_RENDER_UL_presets(UIList):
    bl_idname = "BNAME_RENDER_UL_presets"

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        layout.label(text=item.name, icon="PRESET")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        if self.filter_name:
            flags = bpy.types.UI_UL_list.filter_items_by_name(
                self.filter_name, self.bitflag_filter_item, items, "name"
            )
        wm = getattr(context, "window_manager", None)
        category = str(getattr(wm, "bname_render_preset_category", "ALL") or "ALL")
        if category != "ALL":
            for i, item in enumerate(items):
                if not core.preset_matches_category(item, category):
                    flags[i] &= ~self.bitflag_filter_item
        return flags, []


class BNAME_RENDER_UL_commands(UIList):
    bl_idname = "BNAME_RENDER_UL_commands"

    def draw_item(self, _context, layout, data, item, _icon, _active_data, _active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            commands = getattr(data, "commands", None)
            kind = str(getattr(item, "command_type", "") or "")
            # 深さは filter_items で 1 回だけ計算した配列から引く (各行で
            # 計算し直すと O(N^2) になり、コマンドの多いプリセットで重い)。
            depths = getattr(self, "_bnr_depths", None)
            if depths is not None and 0 <= index < len(depths):
                depth = depths[index]
            else:
                depth = command_ui.block_depth_before(commands, index)
            row = layout.row(align=True)
            if kind == "STATE_BEGIN":
                # 入れ子の出力ブロック見出しもインデントして階層を揃える
                for _ in range(depth):
                    row.label(text="", icon="BLANK1")
                # 出力ブロックの見出し行 (▼/▶ で折りたたみ)
                collapsed = bool(getattr(item, "collapsed", False))
                row.prop(
                    item,
                    "collapsed",
                    text="",
                    emboss=False,
                    icon="DISCLOSURE_TRI_RIGHT" if collapsed else "DISCLOSURE_TRI_DOWN",
                )
                name = command_ui.block_label(commands, index)
                head = f"出力ブロック: {name}" if name else "出力ブロック"
                if collapsed:
                    head += f" （{command_ui.block_inner_count(commands, index)}件）"
                row.label(text=head)
                row.prop(item, "enabled", text="")
                return
            # STATE_END は対応する BEGIN と同じ高さ (深さ-1) に戻す
            indent = max(0, depth - 1) if kind == "STATE_END" else depth
            for _ in range(indent):
                row.label(text="", icon="BLANK1")
            row.prop(item, "enabled", text="")
            sub = row.row(align=True)
            # 無効コマンドは行をグレー表示 (チェックボックスは押せるまま)。
            sub.active = bool(getattr(item, "enabled", False))
            sub.label(
                text=command_ui.display_name(item),
                icon=command_ui.command_icon(kind),
            )
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="RENDER_STILL")

    def filter_items(self, _context, data, propname):
        commands = getattr(data, propname)
        # 深さ配列と非表示集合を 1 回だけ計算し、draw_item へキャッシュ渡しする。
        depths, hidden = command_ui.compute_command_layout(commands)
        self._bnr_depths = depths
        flags = [self.bitflag_filter_item] * len(commands)
        for i in hidden:
            if 0 <= i < len(flags):
                flags[i] &= ~self.bitflag_filter_item
        return flags, []


class BNAME_RENDER_PT_main(Panel):
    bl_idname = "BNAME_RENDER_PT_main"
    bl_label = "B-Name-Render"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "B-Name-Render"

    def draw(self, context):
        draw_main_panel(self.layout, context)


class BNAME_RENDER_PT_node(Panel):
    bl_idname = "BNAME_RENDER_PT_node"
    bl_label = "B-Name-Render"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "B-Name-Render"

    def draw(self, context):
        draw_main_panel(self.layout, context)


def draw_main_panel(layout, context) -> None:
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

    _draw_fisheye_box(layout, context, state)
    _draw_preset_list(layout, context, state)

    preset = core.active_preset(context)
    if preset is None:
        return
    _draw_command_list(layout, context, preset)
    _draw_active_command_detail(layout, preset, context)
    layout.separator()
    layout.operator(
        "bname_render.preset_run", text="プリセットを実行", icon="RENDER_STILL"
    )


def _draw_fisheye_box(layout, context, state) -> None:
    scene = context.scene
    fish = layout.box()
    fish.label(text="魚眼出力", icon="CAMERA_DATA")
    # 魚眼モード / 魚眼FOV は B-Name のコマファイル側で調節する。
    # B-Name-Render は単体運用を想定しないため、ここでは編集させず、
    # 出力に使われる値を読み取り専用で表示する (B-Name と自動同期)。
    fisheye_on = core.fisheye_enabled(scene)
    info = fish.column(align=True)
    info.label(
        text=f"魚眼モード: {'オン' if fisheye_on else 'オフ'}（B-Name側で調節）",
    )
    if fisheye_on:
        fov_deg = round(
            math.degrees(float(core.fisheye_fov(scene)))
        )
        info.label(text=f"魚眼FOV: {fov_deg}°")
    row = fish.row(align=True)
    row.prop(scene, "reduction_mode", text="縮小モード")
    sub = fish.row(align=True)
    sub.enabled = core.reduction_enabled(scene)
    sub.prop(scene, "preview_scale_percentage", text="縮小率")
    fish.label(text=f"現在の出力解像度: {scene.render.resolution_x} x {scene.render.resolution_y}")
    original_x, original_y = core.original_resolution(scene)
    if original_x > 0 and original_y > 0:
        fish.label(text=f"元解像度: {original_x} x {original_y}")
    fish.prop(state, "sound_enabled", text="出力完了アラーム")


def _draw_preset_list(layout, context, state) -> None:
    wm = context.window_manager
    cat = layout.row(align=True)
    cat.prop(wm, "bname_render_preset_category", expand=True)
    row = layout.row()
    row.template_list(
        "BNAME_RENDER_UL_presets",
        "",
        state,
        "presets",
        wm,
        "bname_render_active_preset_index",
        rows=3,
    )
    tools_preset = row.column(align=True)
    tools_preset.operator("bname_render.preset_add", text="", icon="ADD")
    tools_preset.operator("bname_render.preset_remove", text="", icon="REMOVE")
    tools_preset.operator("bname_render.preset_settings", text="", icon="PREFERENCES")
    op = tools_preset.operator("bname_render.load_builtin_presets", text="", icon="FILE_REFRESH")
    op.reset = True


def _draw_command_list(layout, context, preset) -> None:
    wm = context.window_manager
    box = layout.box()
    box.label(text="コマンドリスト", icon="SEQ_STRIP_DUPLICATE")
    row = box.row()
    row.template_list(
        "BNAME_RENDER_UL_commands",
        "",
        preset,
        "commands",
        wm,
        "bname_render_active_command_index",
        rows=max(3, min(8, len(preset.commands))),
    )
    tools = row.column(align=True)
    tools.operator("bname_render.command_add", text="", icon="ADD")

    edit_tools = tools.column(align=True)
    edit_tools.enabled = bool(preset.commands)
    edit_tools.operator("bname_render.command_remove", text="", icon="REMOVE")
    edit_tools.operator("bname_render.command_duplicate", text="", icon="DUPLICATE")

    move_tools = tools.column(align=True)
    move_tools.enabled = len(preset.commands) > 1
    up = move_tools.operator("bname_render.command_move", text="", icon="TRIA_UP")
    up.direction = "UP"
    down = move_tools.operator("bname_render.command_move", text="", icon="TRIA_DOWN")
    down.direction = "DOWN"
    tools.separator()
    tools.operator("bname_render.preset_defaults_restore", text="", icon="LOOP_BACK")
    tools.operator("bname_render.preset_defaults_register", text="", icon="PINNED")

    if not preset.commands:
        box.label(text="コマンドがありません")

    box.operator("bname_render.command_add_block", text="出力ブロックを追加", icon="COLLECTION_NEW")


def _draw_active_command_detail(layout, preset, context) -> None:
    if not preset.commands:
        return
    # active_command_index は描画中に書き戻せない (ID 書き込み禁止) ため
    # 範囲外のまま残ることがある。直接添字すると IndexError でパネル描画が
    # 中断するので、ここでローカルにクランプして安全に取り出す。
    # 折りたたみで隠れた選択は、囲う出力ブロックの見出しに寄せて表示する
    # (一覧の見え方と設定欄を一致させる)。
    idx = command_ui.effective_detail_index(
        preset.commands, core.get_active_command_index(context)
    )
    command = preset.commands[idx]
    box = layout.box()
    box.label(text="選択コマンド設定", icon="PREFERENCES")
    command_ui.draw_command(box, command, context)


_CLASSES = (BNAME_RENDER_UL_presets, BNAME_RENDER_UL_commands, BNAME_RENDER_PT_main, BNAME_RENDER_PT_node)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
