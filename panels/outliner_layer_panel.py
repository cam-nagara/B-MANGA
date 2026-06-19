"""Outliner 中心レイヤー操作パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work
from ..utils import page_file_scene

B_NAME_CATEGORY = "B-MANGA"


class BMANGA_PT_outliner_layers(Panel):
    """B-MANGA のメンテナンス操作パネル."""

    bl_idname = "BMANGA_PT_outliner_layers"
    bl_label = "メンテナンス"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 22

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        return role in {page_file_scene.ROLE_WORK, page_file_scene.ROLE_PAGE}

    def draw(self, context):
        layout = self.layout
        role, _page_id, _coma_id = page_file_scene.current_role(context)

        col = layout.column(align=True)
        if role == page_file_scene.ROLE_PAGE:
            col.operator("bmanga.coma_split_ratio", icon="MOD_EDGESPLIT")
            col.operator("bmanga.auto_ruby_apply", icon="FONT_DATA")
            col.separator()
            col.operator("bmanga.repair_hierarchy", icon="MODIFIER_DATA")
            col.operator("bmanga.mask_regenerate_all", icon="FILE_REFRESH")
            col.operator("bmanga.mask_remove_orphans", icon="TRASH")
            col.operator(
                "bmanga.coma_renumber_active_page", icon="LINENUMBERS_ON"
            )
        elif role == page_file_scene.ROLE_WORK:
            col.operator("bmanga.organize_data_names", icon="FILE_REFRESH")


_CLASSES = (BMANGA_PT_outliner_layers,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
