"""operators — bpy.types.Operator 群."""

from __future__ import annotations

import bpy

from . import (
    alt_reparent_op,
    asset_op,
    balloon_op,
    balloon_text_curve_op,
    brush_size_op,
    effect_line_op,
    effect_line_object_op,
    effect_line_link_op,
    fisheye_op,
    gp_layer_op,
    gpencil_op,
    image_layer_op,
    layer_detail_op,
    layer_move_op,
    mask_object_op,
    mode_op,
    object_tool_op,
    outliner_view_op,
    overlay_toggle_op,
    page_op,
    repair_op,
    coma_edge_move_op,
    coma_edge_style_op,
    coma_edit_op,
    coma_knife_cut_op,
    coma_op,
    coma_camera_op,
    coma_picker,  # noqa: F401 — ヘルパのみ (register 対象外)
    coma_renumber_op,
    coma_vertex_edit_op,
    preset_op,
    raster_layer_op,
    shortcut_op,
    snap_op,
    spread_op,
    text_selection_style_op,
    text_op,
    thumbnail_op,
    view_op,
    work_op,
)

_MODULES = (
    work_op,
    page_op,
    spread_op,
    coma_op,
    coma_edit_op,
    coma_camera_op,
    coma_renumber_op,
    coma_vertex_edit_op,
    coma_knife_cut_op,
    coma_edge_move_op,
    coma_edge_style_op,
    fisheye_op,
    snap_op,
    balloon_op,
    balloon_text_curve_op,
    text_selection_style_op,
    text_op,
    effect_line_op,
    effect_line_object_op,
    effect_line_link_op,
    brush_size_op,
    image_layer_op,
    raster_layer_op,
    layer_detail_op,
    layer_move_op,
    mask_object_op,
    object_tool_op,
    outliner_view_op,
    overlay_toggle_op,
    alt_reparent_op,
    asset_op,
    repair_op,
    thumbnail_op,
    mode_op,
    preset_op,
    gpencil_op,
    gp_layer_op,
    view_op,
    shortcut_op,
)


def _unregister_legacy_output_operators() -> None:
    """B-Name-Render 分離前の書き出し Operator を確実に外す."""
    for class_name in (
        "BNAME_OT_export_page",
        "BNAME_OT_export_all_pages",
        "BNAME_OT_export_pdf",
    ):
        cls = getattr(bpy.types, class_name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def register() -> None:
    _unregister_legacy_output_operators()
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
    _unregister_legacy_output_operators()
