"""書き出しパネル.

作品ファイル・ページファイル・コマファイルそれぞれで書き出し機能を提供する。
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work
from ..io import export_pipeline
from ..utils import page_file_scene
from . import preset_management_ui

B_NAME_CATEGORY = "B-MANGA"


class BMANGA_PT_export(Panel):
    bl_idname = "BMANGA_PT_export"
    bl_label = "書き出し"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 30
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def draw(self, context):
        layout = self.layout
        if not export_pipeline.has_pillow():
            layout.label(text="Pillow 未同梱 - 書き出し無効", icon="ERROR")
            layout.label(text="wheels/ に Pillow を同梱後に有効化", icon="ERROR")
            return

        preset_management_ui.draw_preset_list(layout, context, "export")

        layout.separator()

        role, _page_id, _coma_id = page_file_scene.current_role(context)

        if role == page_file_scene.ROLE_WORK:
            self._draw_work_export(layout)
        elif role == page_file_scene.ROLE_PAGE:
            self._draw_page_export(layout)
        elif role == page_file_scene.ROLE_COMA:
            self._draw_coma_export(layout)
        else:
            if page_file_scene.is_work_list_scene(context.scene):
                self._draw_work_export(layout)
            elif page_file_scene.is_page_edit_scene(context.scene):
                self._draw_page_export(layout)
            else:
                layout.label(text="書き出し対象が見つかりません", icon="INFO")

    def _draw_work_export(self, layout):
        layout.operator("bmanga.export_page", icon="RENDER_STILL")
        layout.operator("bmanga.export_all_pages", icon="RENDER_ANIMATION")
        layout.operator("bmanga.export_pdf", icon="FILE")
        if not export_pipeline.has_pypdf():
            layout.label(text="(pypdf 未同梱のため Pillow 簡易 PDF)", icon="ERROR")
        if not export_pipeline.can_write_layered_psd():
            layout.label(text="(PSD レイヤー出力を利用できません)", icon="ERROR")

    def _draw_page_export(self, layout):
        layout.operator("bmanga.export_current_page", icon="RENDER_STILL")
        if not export_pipeline.can_write_layered_psd():
            layout.label(text="(PSD レイヤー出力を利用できません)", icon="ERROR")

    def _draw_coma_export(self, layout):
        layout.operator("bmanga.export_current_coma", icon="RENDER_STILL")
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
