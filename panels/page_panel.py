"""N-Panel の B-MANGA タブ: ページ一覧 (UIList) + 操作ボタン."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.mode import MODE_COMA, get_mode
from ..core.paper import format_page_entry_display_label
from ..core.work import get_work
from ..utils import page_file_scene

B_NAME_CATEGORY = "B-MANGA"


class BMANGA_UL_pages(UIList):
    """ページ一覧 (サムネイルは Phase 1-E でテクスチャ化、Phase 1-D は文字表示)."""

    bl_idname = "BMANGA_UL_pages"

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
            row.operator_context = "EXEC_DEFAULT"
            icon_name = "FILE_IMAGE" if not item.spread else "IMGDISPLAY"
            work = get_work(context)
            paper = getattr(work, "paper", None) if work is not None else None
            label = format_page_entry_display_label(paper, item) if paper is not None else item.id
            row.label(text=label, icon=icon_name)
            row.prop(item, "title", text="", emboss=False)
            if item.spread:
                row.label(text="見開き", icon="ARROW_LEFTRIGHT")
            op = row.operator("bmanga.open_page_file", text="", icon="FILE_BLEND")
            op.index = index
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            work = get_work(context)
            paper = getattr(work, "paper", None) if work is not None else None
            label = format_page_entry_display_label(paper, item) if paper is not None else item.id
            layout.label(text=label)


class BMANGA_PT_pages(Panel):
    bl_idname = "BMANGA_PT_pages"
    bl_label = "ページ一覧"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 15
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return False

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None:
            return
        is_coma_mode = get_mode(context) == MODE_COMA

        if is_coma_mode:
            box = layout.box()
            box.label(text="コマ編集モード中は紙面操作できません", icon="ERROR")

        row = layout.row()
        row.enabled = not is_coma_mode
        row.template_list(
            BMANGA_UL_pages.bl_idname,
            "",
            work,
            "pages",
            work,
            "active_page_index",
            rows=6,
        )
        col = row.column(align=True)
        col.enabled = not is_coma_mode
        col.operator("bmanga.open_page_file", text="", icon="FILE_BLEND")
        col.separator()
        col.operator("bmanga.page_add", text="", icon="ADD")
        col.operator("bmanga.page_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("bmanga.page_duplicate", text="", icon="DUPLICATE")
        col.separator()
        op = col.operator("bmanga.page_move", text="", icon="TRIA_UP")
        op.direction = -1
        op = col.operator("bmanga.page_move", text="", icon="TRIA_DOWN")
        op.direction = 1

        # 見開き操作
        box = layout.box()
        box.enabled = not is_coma_mode
        box.label(text="見開き")
        row = box.row(align=True)
        row.operator("bmanga.pages_merge_spread", text="変更", icon="ARROW_LEFTRIGHT")
        row.operator("bmanga.pages_split_spread", text="解除", icon="UNLINKED")

        # アクティブページ情報（見開きのみ表示）
        idx = work.active_page_index
        if 0 <= idx < len(work.pages):
            entry = work.pages[idx]
            if entry.spread:
                box = layout.box()
                box.label(text=f"見開き: 間隔 {entry.tombo_gap_mm:.2f}mm")


_CLASSES = (
    BMANGA_UL_pages,
    BMANGA_PT_pages,
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
