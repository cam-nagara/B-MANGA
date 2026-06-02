"""Outliner 中心レイヤー操作パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work
from ..utils import page_file_scene

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_outliner_layers(Panel):
    """B-Name のメンテナンス操作パネル."""

    bl_idname = "BNAME_PT_outliner_layers"
    bl_label = "メンテナンス"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 12

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
            col.operator("bname.repair_hierarchy", icon="MODIFIER_DATA")
            col.operator("bname.mask_regenerate_all", icon="FILE_REFRESH")
            col.operator("bname.mask_remove_orphans", icon="TRASH")
            col.operator(
                "bname.coma_renumber_active_page", icon="LINENUMBERS_ON"
            )
        elif role == page_file_scene.ROLE_WORK:
            col.operator("bname.organize_data_names", icon="FILE_REFRESH")


_CLASSES = (BNAME_PT_outliner_layers,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
