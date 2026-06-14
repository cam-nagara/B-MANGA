"""N-Panel の B-Name タブ: 作品情報・作品操作."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import page_file_scene
from ..utils import shortcut_visibility

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_work(Panel):
    bl_idname = "BNAME_PT_work"
    bl_label = "作品"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return (
            get_mode(context) != MODE_COMA
            and not page_file_scene.is_page_edit_scene(context.scene)
        )

    def draw(self, context):
        shortcut_visibility.mark_bname_panel_drawn(context)
        layout = self.layout
        work = get_work(context)

        # ツールバー: 新規 / 開く
        row = layout.row(align=True)
        row.operator("bname.work_new", text="新規", icon="FILE_NEW")
        row.operator("bname.work_open", text="開く", icon="FILE_FOLDER")
        row.operator("bname.open_current_folder", text="", icon="FILEBROWSER")

        if work is None or not work.loaded:
            layout.label(text="作品が開かれていません", icon="INFO")
            box = layout.box()
            box.label(
                text="ページ一覧でもコマでもない場合",
                icon="QUESTION",
            )
            box.operator(
                "bname.work_make_coma_file",
                text="このファイルをコマファイルにする",
                icon="FILE_BLEND",
            )
            return

        mode = get_mode(context)

        box = layout.box()
        box.label(text="作品情報", icon="WORDWRAP_ON")
        info = work.work_info
        box.prop(info, "work_name")
        box.prop(info, "episode_number")
        box.prop(info, "subtitle")
        box.prop(info, "author")
        box.operator("bname.work_meta_dialog", text="メタ情報を編集", icon="INFO")
        box.label(text="ページ数")
        row = box.row(align=True)
        row.enabled = mode == MODE_PAGE
        row.prop(info, "page_number_start", text="開始")
        row.prop(info, "page_number_end", text="終了")
        # 綴じ方向 / 読む方向 (paper の設定だが、作品単位で決める情報なので
        # 作品情報パネルから直接編集できるようにする)
        sub = box.column(align=True)
        sub.enabled = mode == MODE_PAGE
        sub.prop(work.paper, "start_side", text="開始ページ")
        sub.prop(work.paper, "read_direction", text="読む方向")
        box.label(text="ページ一覧プレビュー")
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


class BNAME_PT_coma_return(Panel):
    bl_idname = "BNAME_PT_coma_return"
    bl_label = "ファイル遷移"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 0

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if work and work.loaded and page_file_scene.is_page_edit_scene(context.scene):
            return True
        # 通常: モードが MODE_COMA + work.loaded
        if work and work.loaded and get_mode(context) == MODE_COMA:
            return True
        # フォールバック: load_post の遅延等でモードが同期できなくても、
        # 開いている .blend のパスが cNN.blend ならパネルを表示する。
        return shortcut_visibility.current_blend_is_coma_blend()

    def draw(self, context):
        shortcut_visibility.mark_bname_panel_drawn(context)
        layout = self.layout
        scene = getattr(context, "scene", None)
        if get_mode(context) == MODE_COMA or shortcut_visibility.current_blend_is_coma_blend():
            layout.operator(
                "bname.exit_coma_mode_safe",
                text="ページに戻る",
                icon="BACK",
            )
            op = layout.operator("bname.open_current_folder", text="保存フォルダを開く", icon="FILEBROWSER")
            op.target = "COMA"
            return
        if page_file_scene.is_page_edit_scene(context.scene):
            row = layout.row(align=True)
            row.operator(
                "bname.exit_page_file",
                text="ページ一覧に戻る",
                icon="BACK",
            )
            row.operator("bname.work_save", text="", icon="FILE_TICK")
            _draw_page_browser_fit(layout, scene)
            op = layout.operator("bname.open_current_folder", text="保存フォルダを開く", icon="FILEBROWSER")
            op.target = "WORK"
            return


def _draw_page_browser_fit(layout, scene) -> None:
    if scene is None or not hasattr(scene, "bname_page_browser_fit"):
        return
    box = layout.box()
    box.label(text="ページ一覧ビュー", icon="WINDOW")
    box.prop(scene, "bname_page_browser_fit", text="フィット")


_CLASSES = (
    BNAME_PT_work,
    BNAME_PT_coma_return,
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
