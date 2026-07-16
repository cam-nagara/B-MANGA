"""N-Panel の B-MANGA タブ: 作品情報・作品操作."""

from __future__ import annotations

from pathlib import Path

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


def _work_title(work) -> str:
    info = getattr(work, "work_info", None)
    title = str(getattr(info, "work_name", "") or "").strip()
    if title:
        return title
    work_dir = str(getattr(work, "work_dir", "") or "")
    if work_dir:
        return Path(work_dir).stem
    return "作品名未設定"


def _file_identity_text(work, role: str, page_id: str, coma_id: str) -> str:
    """ファイル遷移パネルへ表示する作品・ページ・コマ識別行を返す."""
    title = _work_title(work)
    if role == page_file_scene.ROLE_WORK:
        return title
    page_number = ""
    if str(page_id or "").startswith("p") and str(page_id)[1:].isdigit():
        page_number = f"p{int(str(page_id)[1:]):04d}"
    if role == page_file_scene.ROLE_PAGE:
        return f"{title} {page_number}".strip()
    if role == page_file_scene.ROLE_COMA:
        coma_number = ""
        if str(coma_id or "").startswith("c") and str(coma_id)[1:].isdigit():
            coma_number = f"コマ{int(str(coma_id)[1:]):02d}"
        return " ".join(part for part in (title, page_number, coma_number) if part)
    return title


def _page_file_nav_specs(work) -> tuple[tuple[str, str], tuple[str, str]]:
    """ページファイル移動ボタンの左右表示と実行先を返す."""
    read_direction = str(getattr(getattr(work, "paper", None), "read_direction", "left") or "left")
    if read_direction == "left":
        return (
            ("bmanga.page_file_next", "◀　次のページへ"),
            ("bmanga.page_file_prev", "前のページへ　▶"),
        )
    return (
        ("bmanga.page_file_prev", "◀　前のページへ"),
        ("bmanga.page_file_next", "次のページへ　▶"),
    )


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
            layout.label(text="作品が開かれていません", icon="FILE_FOLDER")
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

        box = layout.box()
        box.label(text="Meldexシナリオ", icon="FILE_TEXT")
        box.operator(
            "bmanga.meldex_scenario_file_import",
            text="シナリオファイルを読み込む",
            icon="IMPORT",
        )

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

        work = get_work(context)
        role, page_id, coma_id = page_file_scene.current_role(context)
        label, icon = _ROLE_LABELS.get(role, ("不明", "QUESTION"))
        row = layout.row()
        row.alignment = "CENTER"
        row.label(
            text=_file_identity_text(work, role, page_id, coma_id) if work is not None else label,
            icon=icon,
        )
        layout.separator(factor=0.5)

        if get_mode(context) == MODE_COMA or shortcut_visibility.current_blend_is_coma_blend():
            row = layout.row(align=True)
            row.operator(
                "bmanga.exit_coma_mode_safe",
                text="ページに戻る",
                icon="BACK",
            )
            to_work = row.operator(
                "bmanga.exit_coma_mode_safe",
                text="作品に戻る",
                icon="HOME",
            )
            to_work.to_work = True
            op = layout.operator("bmanga.open_current_folder", text="保存フォルダを開く", icon="FILEBROWSER")
            op.target = "COMA"
            return
        if page_file_scene.is_page_edit_scene(context.scene):
            work = get_work(context)
            row = layout.row(align=True)
            row.operator(
                "bmanga.exit_page_file",
                text="作品ファイルに戻る",
                icon="BACK",
            )
            row.operator("bmanga.work_save", text="", icon="FILE_TICK")
            nav = layout.row(align=True)
            left_spec, right_spec = _page_file_nav_specs(work)
            nav.operator(left_spec[0], text=left_spec[1])
            nav.operator(right_spec[0], text=right_spec[1])
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
