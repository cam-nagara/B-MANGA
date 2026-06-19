"""作品・用紙メタ情報の編集ダイアログ."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_work


def _paper_unit_label(paper) -> str:
    unit = str(getattr(paper, "unit", "mm") or "mm")
    if unit == "px":
        return "px"
    if unit == "inch":
        return "inch"
    return "mm"


def _draw_display_item(layout, label: str, item) -> None:
    box = layout.box()
    box.prop(item, "enabled", text=label)
    sub = box.column(align=True)
    sub.enabled = bool(getattr(item, "enabled", False))
    row = sub.row(align=True)
    row.prop(item, "position", text="位置")
    row.prop(item, "color", text="")
    row = sub.row(align=True)
    row.prop(item, "font_size_value", text="サイズ")
    row.prop(item, "font_size_unit", text="")


class BMANGA_OT_work_meta_dialog(Operator):
    """作品情報、用紙、原稿上の表示をまとめて編集する."""

    bl_idname = "bmanga.work_meta_dialog"
    bl_label = "作品情報"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded)

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None or not work.loaded:
            layout.label(text="作品が開かれていません", icon="INFO")
            return
        mode = get_mode(context)
        info = work.work_info
        paper = work.paper
        unit = _paper_unit_label(paper)

        box = layout.box()
        box.label(text="作品情報", icon="WORDWRAP_ON")
        box.prop(info, "work_name")
        box.prop(info, "episode_number")
        box.prop(info, "subtitle")
        box.prop(info, "author")
        row = box.row(align=True)
        row.enabled = mode == MODE_PAGE
        row.prop(info, "page_number_start", text="開始")
        row.prop(info, "page_number_end", text="終了")

        box = layout.box()
        box.label(text="用紙", icon="MESH_PLANE")
        row = box.row(align=True)
        row.prop(paper, "unit", text="単位")
        row.prop(paper, "dpi")
        row = box.row(align=True)
        row.prop(paper, "canvas_width_value", text=f"幅 ({unit})")
        row.prop(paper, "canvas_height_value", text=f"高さ ({unit})")
        row = box.row(align=True)
        row.prop(paper, "finish_width_value", text=f"仕上幅 ({unit})")
        row.prop(paper, "finish_height_value", text=f"仕上高 ({unit})")
        box.prop(paper, "bleed_value", text=f"裁ち落とし ({unit})")
        col = box.column(align=True)
        col.prop(paper, "start_side", text="開始ページ")
        col.prop(paper, "read_direction", text="読む方向")

        box = layout.box()
        box.label(text="原稿上の表示", icon="TEXT")
        _draw_display_item(box, "作品名", info.display_work_name)
        _draw_display_item(box, "話数", info.display_episode)
        _draw_display_item(box, "サブタイトル", info.display_subtitle)
        _draw_display_item(box, "作者名", info.display_author)
        _draw_display_item(box, "ページ番号", info.display_page_number)

    def execute(self, context):
        for area in getattr(getattr(context, "screen", None), "areas", []):
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_work_meta_dialog,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
