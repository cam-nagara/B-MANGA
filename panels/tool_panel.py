"""N-Panel の B-MANGA タブ: 共通ツールボタン."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work
from ..operators import coma_modal_state
from ..utils import page_file_scene

B_NAME_CATEGORY = "B-MANGA"
_MODAL_TOOL_NAMES = (
    "object_tool",
    "knife_cut",
    "edge_move",
    "layer_move",
    "balloon_tool",
    "balloon_tail_tool",
    "balloon_nurbs_tool",
    "text_tool",
    "effect_line_tool",
    "coma_vertex_edit",
    "coma_create",
    "fill_tool",
    "gradient_tool",
)


def _active_stack_kind(context) -> str:
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1)) if scene is not None else -1
    if stack is None or not (0 <= idx < len(stack)):
        return ""
    return str(getattr(stack[idx], "kind", "") or "")


def _any_bmanga_modal_tool_active() -> bool:
    return any(coma_modal_state.is_active(name) for name in _MODAL_TOOL_NAMES)


class BMANGA_PT_tools(Panel):
    bl_idname = "BMANGA_PT_tools"
    bl_label = "ツール"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 13

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work
            and work.loaded
            and get_mode(context) != MODE_COMA
            and page_file_scene.is_page_edit_scene(context.scene)
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "bmanga_interaction_enabled", text="B-MANGA操作")
        obj = None
        try:
            from ..utils import gpencil as gp_utils

            obj = gp_utils.get_master_gpencil()
        except Exception:  # noqa: BLE001
            obj = None
        mode = getattr(obj, "mode", "") if obj is not None else ""
        active_stack_kind = _active_stack_kind(context)
        gp_layer_active = (
            active_stack_kind == "gp"
            and getattr(context.scene, "bmanga_active_layer_kind", "") == "gp"
        )
        raster_layer_active = (
            active_stack_kind == "raster"
            and getattr(context.scene, "bmanga_active_layer_kind", "") == "raster"
        )
        modal_tool_active = _any_bmanga_modal_tool_active()
        active_obj = getattr(getattr(context, "view_layer", None), "objects", None)
        active_obj = getattr(active_obj, "active", None) if active_obj is not None else None
        active_mode = getattr(active_obj, "mode", "")

        row = layout.row(align=True)
        op = row.operator(
            "bmanga.raster_layer_mode_set" if raster_layer_active else "bmanga.gpencil_master_mode_set",
            text="",
            icon="RESTRICT_SELECT_OFF",
            depress=(
                coma_modal_state.is_active("object_tool")
                or (not modal_tool_active and active_mode == "OBJECT")
            ),
        )
        op.mode = "OBJECT"
        gp_draw = row.operator(
            "bmanga.gpencil_master_mode_set",
            text="",
            icon="OUTLINER_OB_GREASEPENCIL",
            depress=(
                not modal_tool_active
                and gp_layer_active
                and mode == "PAINT_GREASE_PENCIL"
            ),
        )
        gp_draw.mode = "PAINT_GREASE_PENCIL"
        raster_draw = row.operator(
            "bmanga.raster_layer_mode_set",
            text="",
            icon="BRUSH_DATA",
            depress=(not modal_tool_active and active_mode == "TEXTURE_PAINT"),
        )
        raster_draw.mode = "TEXTURE_PAINT"
        row.separator()
        row.operator_context = "INVOKE_DEFAULT"
        row.operator(
            "bmanga.coma_create_tool",
            text="",
            icon="MESH_PLANE",
            depress=coma_modal_state.is_active("coma_create"),
        )
        row.operator(
            "bmanga.coma_knife_cut",
            text="",
            icon="MESH_GRID",
            depress=coma_modal_state.is_active("knife_cut"),
        )
        row.operator(
            "bmanga.layer_move_tool",
            text="",
            icon="EMPTY_ARROWS",
            depress=coma_modal_state.is_active("layer_move"),
        )
        row.operator(
            "bmanga.balloon_tool",
            text="",
            icon="MESH_CIRCLE",
            depress=coma_modal_state.is_active("balloon_tool"),
        )
        row.operator(
            "bmanga.balloon_nurbs_tool",
            text="",
            icon="CURVE_NCIRCLE",
            depress=coma_modal_state.is_active("balloon_nurbs_tool"),
        )
        row.operator(
            "bmanga.balloon_tail_tool",
            text="",
            icon="SHARPCURVE",
            depress=coma_modal_state.is_active("balloon_tail_tool"),
        )
        row.operator(
            "bmanga.text_tool",
            text="",
            icon="FONT_DATA",
            depress=coma_modal_state.is_active("text_tool"),
        )
        row.operator(
            "bmanga.effect_line_tool",
            text="",
            icon="FORCE_FORCE",
            depress=coma_modal_state.is_active("effect_line_tool"),
        )
        row.separator()
        row.operator(
            "bmanga.fill_tool",
            text="",
            icon="SNAP_FACE",
            depress=coma_modal_state.is_active("fill_tool"),
        )
        row.operator(
            "bmanga.gradient_tool",
            text="",
            icon="NODE_TEXTURE",
            depress=coma_modal_state.is_active("gradient_tool"),
        )

        _draw_active_tool_preset_row(layout, context)


def _draw_active_tool_preset_row(layout, context) -> None:
    """選択中ツールに対応するプリセット選択を、ボタン群の下に表示する."""
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    if coma_modal_state.is_active("coma_create") and hasattr(wm, "bmanga_border_preset_selector"):
        prow = layout.row(align=True)
        prow.label(text="コマ作成の枠線", icon="MESH_PLANE")
        prow.prop(wm, "bmanga_border_preset_selector", text="")
        return
    if coma_modal_state.is_active("balloon_tool") and hasattr(wm, "bmanga_balloon_tool_preset_selector"):
        prow = layout.row(align=True)
        prow.label(text="フキダシ形状", icon="MESH_CIRCLE")
        prow.prop(wm, "bmanga_balloon_tool_preset_selector", text="")
        return
    if coma_modal_state.is_active("balloon_tail_tool") and hasattr(wm, "bmanga_tail_preset_selector"):
        prow = layout.row(align=True)
        prow.label(text="しっぽプリセット", icon="SHARPCURVE")
        prow.prop(wm, "bmanga_tail_preset_selector", text="")
        return
    if coma_modal_state.is_active("text_tool") and hasattr(wm, "bmanga_text_tool_preset_selector"):
        prow = layout.row(align=True)
        prow.label(text="テキストプリセット", icon="FONT_DATA")
        prow.prop(wm, "bmanga_text_tool_preset_selector", text="")
        return
    if coma_modal_state.is_active("fill_tool") and hasattr(wm, "bmanga_fill_tool_preset_selector"):
        prow = layout.row(align=True)
        prow.label(text="囲い塗りプリセット", icon="MESH_PLANE")
        prow.prop(wm, "bmanga_fill_tool_preset_selector", text="")
        return
    if coma_modal_state.is_active("gradient_tool") and hasattr(wm, "bmanga_gradient_tool_preset_selector"):
        prow = layout.row(align=True)
        prow.label(text="グラデーション", icon="NODE_TEXTURE")
        prow.prop(wm, "bmanga_gradient_tool_preset_selector", text="")
        return


_CLASSES = (BMANGA_PT_tools,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
