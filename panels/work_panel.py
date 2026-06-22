"""N-Panel の B-MANGA タブ: 作品情報・作品操作."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import page_file_scene
from ..utils import shortcut_visibility

B_NAME_CATEGORY = "B-MANGA"

_ROLE_LABELS = {
    "work": ("作品ファイル", "HOME"),
    "page": ("ページファイル", "FILE"),
    "coma": ("コマファイル", "OUTLINER_OB_CAMERA"),
}


class BMANGA_PT_work(Panel):
    bl_idname = "BMANGA_PT_work"
    bl_label = "作品"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 11

    @classmethod
    def poll(cls, context):
        return (
            get_mode(context) != MODE_COMA
            and not page_file_scene.is_page_edit_scene(context.scene)
        )

    def draw(self, context):
        shortcut_visibility.mark_bmanga_panel_drawn(context)
        layout = self.layout
        work = get_work(context)

        # ツールバー: 新規 / 開く
        row = layout.row(align=True)
        row.operator("bmanga.work_new", text="新規", icon="FILE_NEW")
        row.operator("bmanga.work_open", text="開く", icon="FILE_FOLDER")
        row.operator("bmanga.open_current_folder", text="", icon="FILEBROWSER")

        if work is None or not work.loaded:
            layout.label(text="作品が開かれていません", icon="INFO")
            box = layout.box()
            box.label(
                text="ページ一覧でもコマでもない場合",
                icon="QUESTION",
            )
            box.operator(
                "bmanga.work_make_coma_file",
                text="このファイルをコマファイルにする",
                icon="FILE_BLEND",
            )
            return

        mode = get_mode(context)
        info = work.work_info

        box = layout.box()
        box.label(text="作品情報", icon="WORDWRAP_ON")
        box.prop(info, "work_name", text="作品名")
        box.prop(info, "episode_number", text="話数")
        box.prop(info, "subtitle", text="サブタイトル")
        box.prop(info, "author", text="作者名")
        box.label(text="ページ数")
        row = box.row(align=True)
        row.enabled = mode == MODE_PAGE
        row.prop(info, "page_number_start", text="開始")
        row.prop(info, "page_number_end", text="終了")

        box = layout.box()
        box.label(text="ページ一覧プレビュー", icon="RENDERLAYERS")
        preview = box.column(align=True)
        preview.enabled = mode == MODE_PAGE
        preview.prop(work, "auto_render_coma_thumb_on_return", text="戻る時に更新")

        box = layout.box()
        box.label(text="コマ用blendファイル (この作品のみ)", icon="FILE_BLEND")
        box.enabled = mode == MODE_PAGE
        box.prop(work, "coma_blend_template_path", text="")
        sub = box.column(align=True)
        sub.scale_y = 0.85
        sub.label(text="コマごとの設定が空のときに使われる", icon="INFO")
        sub.label(text="さらに空のときはプリファレンスの共通設定が使われる")


class BMANGA_PT_coma_return(Panel):
    bl_idname = "BMANGA_PT_coma_return"
    bl_label = "ファイル遷移"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 10

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if work and work.loaded:
            return True
        return shortcut_visibility.current_blend_is_coma_blend()

    def draw(self, context):
        shortcut_visibility.mark_bmanga_panel_drawn(context)
        layout = self.layout

        role, _pid, _cid = page_file_scene.current_role(context)
        label, icon = _ROLE_LABELS.get(role, ("不明", "QUESTION"))
        row = layout.row()
        row.alignment = "CENTER"
        row.label(text=label, icon=icon)
        layout.separator(factor=0.5)

        if get_mode(context) == MODE_COMA or shortcut_visibility.current_blend_is_coma_blend():
            layout.operator(
                "bmanga.exit_coma_mode_safe",
                text="ページに戻る",
                icon="BACK",
            )
            op = layout.operator("bmanga.open_current_folder", text="保存フォルダを開く", icon="FILEBROWSER")
            op.target = "COMA"
            return
        if page_file_scene.is_page_edit_scene(context.scene):
            row = layout.row(align=True)
            row.operator(
                "bmanga.exit_page_file",
                text="作品ファイルに戻る",
                icon="BACK",
            )
            row.operator("bmanga.work_save", text="", icon="FILE_TICK")
            nav = layout.row(align=True)
            nav.operator("bmanga.page_file_prev", text="前のページへ", icon="TRIA_LEFT")
            nav.operator("bmanga.page_file_next", text="次のページへ", icon="TRIA_RIGHT")
            op = layout.operator("bmanga.open_current_folder", text="保存フォルダを開く", icon="FILEBROWSER")
            op.target = "WORK"
            return


_CLASSES = (
    BMANGA_PT_work,
    BMANGA_PT_coma_return,
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
