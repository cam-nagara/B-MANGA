"""Outliner 中心レイヤー操作パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_outliner_layers(Panel):
    """Outliner ベースのレイヤー操作パネル."""

    bl_idname = "BNAME_PT_outliner_layers"
    bl_label = "Outliner レイヤー"
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

        # Outliner 表示切替
        box = layout.box()
        box.label(text="Outliner 表示", icon="OUTLINER")
        row = box.row(align=True)
        row.operator("bname.outliner_apply_view", text="B-Name 表示へ", icon="VIS_SEL_11")
        row.operator("bname.outliner_restore_view", text="復元", icon="LOOP_BACK")

        # メンテナンス
        box = layout.box()
        box.label(text="メンテナンス", icon="TOOL_SETTINGS")
        col = box.column(align=True)
        col.operator("bname.repair_hierarchy", icon="MODIFIER_DATA")
        col.operator("bname.mask_regenerate_all", icon="FILE_REFRESH")
        col.operator("bname.mask_remove_orphans", icon="TRASH")
        col.operator(
            "bname.coma_renumber_active_page", icon="LINENUMBERS_ON"
        )
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
