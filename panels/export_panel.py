"""ページ出力パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work
from ..io import export_pipeline
from ..utils import page_file_scene

B_NAME_CATEGORY = "B-MANGA"


class BMANGA_PT_export(Panel):
    bl_idname = "BMANGA_PT_export"
    bl_label = "ページ出力"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 30
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(
            w
            and w.loaded
            and get_mode(context) != MODE_COMA
            and page_file_scene.is_work_list_scene(context.scene)
        )

    def draw(self, context):
        layout = self.layout
        if not export_pipeline.has_pillow():
            layout.label(text="Pillow 未同梱 - ページ出力無効", icon="ERROR")
            layout.label(text="wheels/ に Pillow を同梱後に有効化", icon="ERROR")
            return
        layout.operator("bmanga.export_page", icon="RENDER_STILL")
        layout.operator("bmanga.export_all_pages", icon="RENDER_ANIMATION")
        layout.operator("bmanga.export_pdf", icon="FILE")
        if not export_pipeline.has_pypdf():
            layout.label(text="(pypdf 未同梱のため Pillow 簡易 PDF)", icon="ERROR")
        if not export_pipeline.can_write_layered_psd():
            layout.label(text="(PSD レイヤー出力を利用できません)", icon="ERROR")


_CLASSES = (BMANGA_PT_export,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
