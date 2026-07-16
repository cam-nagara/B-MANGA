"""コマ一覧パネル (UIList) + Z順序/モード切替 UI."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.paper import format_coma_display_label
from ..core.work import get_active_page, get_work

B_NAME_CATEGORY = "B-MANGA"


class BMANGA_OT_coma_enter_from_list(bpy.types.Operator):
    """UIList 行の「コマ編集へ」ボタン用: 指定 index のコマを選択してから enter_coma_mode."""

    bl_idname = "bmanga.coma_enter_from_list"
    bl_label = "このコマを編集"
    bl_options = {"REGISTER"}

    index: bpy.props.IntProperty(default=-1)  # type: ignore[valid-type]

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        if not (0 <= self.index < len(page.comas)):
            self.report({"ERROR"}, "コマ index が不正です")
            return {"CANCELLED"}
        page.active_coma_index = self.index
        # enter_coma_mode.execute は active panel を対象にするので、
        # ここで invoke ではなく execute 経由で呼び出せば ok。
        return bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")


class BMANGA_UL_comas(UIList):
    bl_idname = "BMANGA_UL_comas"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            work = get_work(context)
            paper = getattr(work, "paper", None) if work is not None else None
            coma_num = int(getattr(item, "coma_number", 0) or 0)
            if coma_num < 1:
                from ..utils.coma_id_edit import coma_number_from_id
                coma_num = coma_number_from_id(item.coma_id)
            coma_label = format_coma_display_label(paper, coma_num) if paper is not None else item.coma_id
            row.label(text=coma_label, icon="IMAGE_DATA")
            row.prop(item, "title", text="", emboss=False)
            row.label(text=f"z={item.z_order}")
            op = row.operator(
                "bmanga.coma_enter_from_list",
                text="",
                icon="PLAY",
                emboss=False,
            )
            op.index = index
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            work = get_work(context)
            paper = getattr(work, "paper", None) if work is not None else None
            coma_num = int(getattr(item, "coma_number", 0) or 0)
            if coma_num < 1:
                from ..utils.coma_id_edit import coma_number_from_id
                coma_num = coma_number_from_id(item.coma_id)
            coma_label = format_coma_display_label(paper, coma_num) if paper is not None else item.coma_id
            layout.label(text=coma_label)


class BMANGA_PT_comas(Panel):
    bl_idname = "BMANGA_PT_comas"
    bl_label = "コマ一覧"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 6
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def draw(self, context):
        layout = self.layout
        page = get_active_page(context)
        if page is None:
            layout.label(text="ページを選択してください", icon="QUESTION")
            return

        # モード表示
        mode = get_mode(context)
        box = layout.box()
        row = box.row(align=True)
        if mode == MODE_PAGE:
            row.label(text="紙面編集モード", icon="FILE_IMAGE")
            # INVOKE だとマウス直下のコマ逆引きに失敗してボタンが無反応に
            # なるため、選択中コマを対象に execute する EXEC_DEFAULT で呼ぶ。
            row.operator_context = "EXEC_DEFAULT"
            row.operator("bmanga.enter_coma_mode", text="コマ編集へ", icon="PLAY")
        else:
            stem = getattr(context.scene, "bmanga_current_coma_id", "")
            work_mode = get_work(context)
            paper_mode = getattr(work_mode, "paper", None) if work_mode is not None else None
            if paper_mode is not None and stem:
                from ..utils.coma_id_edit import coma_number_from_id
                stem_label = format_coma_display_label(paper_mode, coma_number_from_id(stem))
            else:
                stem_label = stem
            row.label(text=f"コマ編集モード: {stem_label}", icon="IMAGE_DATA")
            row.operator("bmanga.exit_coma_mode", text="戻る (Esc)", icon="BACK")

        row = layout.row()
        row.template_list(
            BMANGA_UL_comas.bl_idname,
            "",
            page,
            "comas",
            page,
            "active_coma_index",
            rows=6,
        )
        col = row.column(align=True)
        col.operator("bmanga.coma_add", text="", icon="ADD")
        col.operator("bmanga.coma_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("bmanga.coma_duplicate", text="", icon="DUPLICATE")
        col.operator("bmanga.coma_move_to_page", text="", icon="FORWARD")

        # Z順序操作
        box = layout.box()
        box.label(text="Z順序")
        row = box.row(align=True)
        op = row.operator("bmanga.coma_z_order", text="最背面", icon="TRIA_DOWN_BAR")
        op.direction = "BACK"
        op = row.operator("bmanga.coma_z_order", text="背面へ", icon="TRIA_DOWN")
        op.direction = "BACKWARD"
        op = row.operator("bmanga.coma_z_order", text="前面へ", icon="TRIA_UP")
        op.direction = "FORWARD"
        op = row.operator("bmanga.coma_z_order", text="最前面", icon="TRIA_UP_BAR")
        op.direction = "FRONT"


_CLASSES = (
    BMANGA_OT_coma_enter_from_list,
    BMANGA_UL_comas,
    BMANGA_PT_comas,
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
