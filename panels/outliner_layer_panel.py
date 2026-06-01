"""Outliner 中心レイヤー操作パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
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
        return get_mode(context) != MODE_COMA

    def draw(self, context):
        layout = self.layout
        in_page_file = page_file_scene.is_page_edit_scene(context.scene)

        col = layout.column(align=True)
        col.operator("bname.repair_hierarchy", icon="MODIFIER_DATA")
        if in_page_file:
            col.operator("bname.mask_regenerate_all", icon="FILE_REFRESH")
            col.operator("bname.mask_remove_orphans", icon="TRASH")
        col.operator(
            "bname.coma_renumber_active_page", icon="LINENUMBERS_ON"
        )
        if not in_page_file:
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
