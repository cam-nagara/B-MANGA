"""N-Panel の B-Name タブ: 共通ツールボタン."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work
from ..operators import coma_modal_state

B_NAME_CATEGORY = "B-Name"
_MODAL_TOOL_NAMES = (
    "object_tool",
    "knife_cut",
    "edge_move",
    "layer_move",
    "balloon_tool",
    "text_tool",
    "effect_line_tool",
    "coma_vertex_edit",
)


def _any_bname_modal_tool_active() -> bool:
    return any(coma_modal_state.is_active(name) for name in _MODAL_TOOL_NAMES)


class BNAME_PT_tools(Panel):
    bl_idname = "BNAME_PT_tools"
    bl_label = "ツール"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 3

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and get_mode(context) != MODE_COMA)

    def draw(self, context):
        layout = self.layout
        modal_tool_active = _any_bname_modal_tool_active()
        active_obj = getattr(getattr(context, "view_layer", None), "objects", None)
        active_obj = getattr(active_obj, "active", None) if active_obj is not None else None
        active_mode = getattr(active_obj, "mode", "")
        is_object_mode = (
            coma_modal_state.is_active("object_tool")
            or (not modal_tool_active and active_mode == "OBJECT")
        )
        is_gp_paint = not modal_tool_active and active_mode == "PAINT_GREASE_PENCIL"
        is_raster_paint = not modal_tool_active and active_mode == "TEXTURE_PAINT"
        is_gp_edit = not modal_tool_active and active_mode == "EDIT"

        row = layout.row(align=True)
        # オブジェクトツール: 常時選択可
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="OBJECT_DATAMODE",
            depress=is_object_mode,
        )
        op.mode = "OBJECT"
        # GP 描画: 常時選択可。 内部で他描画モードの自動退出を行う。
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="OUTLINER_OB_GREASEPENCIL",
            depress=is_gp_paint,
        )
        op.mode = "PAINT_GREASE_PENCIL"
        # ラスター描画: 常時選択可。 内部で他描画モードの自動退出を行う。
        op = row.operator(
            "bname.raster_layer_mode_set",
            text="",
            icon="BRUSH_DATA",
            depress=is_raster_paint,
        )
        op.mode = "TEXTURE_PAINT"
        # GP 線編集: 常時選択可。
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="EDITMODE_HLT",
            depress=is_gp_edit,
        )
        op.mode = "EDIT"

        row.separator()
        row.operator_context = "INVOKE_DEFAULT"
        row.operator(
            "bname.coma_knife_cut",
            text="",
            icon="SCULPTMODE_HLT",
            depress=coma_modal_state.is_active("knife_cut"),
        )
        row.operator(
            "bname.coma_edge_move",
            text="",
            icon="EMPTY_ARROWS",
            depress=coma_modal_state.is_active("edge_move"),
        )
        row.operator(
            "bname.layer_move_tool",
            text="",
            icon="DRIVER_TRANSFORM",
            depress=coma_modal_state.is_active("layer_move"),
        )
        row.operator(
            "bname.balloon_tool",
            text="",
            icon="MOD_FLUID",
            depress=coma_modal_state.is_active("balloon_tool"),
        )
        row.operator(
            "bname.text_tool",
            text="",
            icon="FONT_DATA",
            depress=coma_modal_state.is_active("text_tool"),
        )
        row.operator(
            "bname.effect_line_tool",
            text="",
            icon="STROKE",
            depress=coma_modal_state.is_active("effect_line_tool"),
        )


_CLASSES = (BNAME_PT_tools,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
