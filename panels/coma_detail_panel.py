"""コマ詳細設定ダイアログ用の描画ヘルパー."""

from __future__ import annotations

from ..utils import object_selection


def draw_coma_shape_settings(layout, context, entry) -> None:
    shape_label = "矩形" if entry.shape_type == "rect" else "多角形"
    layout.label(text=f"形状: {shape_label}")
    if entry.shape_type == "rect":
        row = layout.row(align=True)
        row.prop(entry, "rect_x_mm")
        row.prop(entry, "rect_y_mm")
        row = layout.row(align=True)
        row.prop(entry, "rect_width_mm")
        row.prop(entry, "rect_height_mm")
    else:
        layout.label(text=f"頂点数: {len(entry.vertices)}", icon="VERTEXSEL")

    row = layout.row(align=True)
    row.operator(
        "bname.coma_edit_vertices",
        text="頂点/辺をドラッグ編集",
        icon="EDITMODE_HLT",
    )
    layout.label(text="(Enter=確定 / ESC=キャンセル / 緑線=スナップ)", icon="INFO")

    row = layout.row(align=True)
    if entry.shape_type == "rect":
        row.operator("bname.coma_to_polygon", text="多角形化", icon="MESH_DATA")
    else:
        row.operator("bname.coma_to_rect", text="矩形化 (外接)", icon="MESH_PLANE")
    if object_selection.selected_coma_count(context) >= 2:
        layout.operator("bname.coma_merge_selected", text="コマ結合", icon="AUTOMERGE_ON")

    row = layout.row(align=True)
    row.prop(entry, "paper_visible", text="用紙")
    row.prop(entry, "background_color", text="用紙色")
    row = layout.row(align=True)
    row.prop(entry, "coma_gap_vertical_mm", text="上下 (個別)")
    row.prop(entry, "coma_gap_horizontal_mm", text="左右 (個別)")
    layout.label(text="(負値は作品共通ルールを継承)", icon="INFO")
    layout.prop(entry, "overlap_clipping")


def draw_coma_border_settings(layout, context, entry) -> None:
    b = entry.border
    wm = getattr(context, "window_manager", None)
    if wm is not None and hasattr(wm, "bname_border_preset_selector"):
        row = layout.row(align=True)
        row.label(text="プリセット", icon="PRESET")
        row.prop(wm, "bname_border_preset_selector", text="")
        row.operator("bname.border_preset_save_local", text="", icon="FILE_TICK")
    layout.prop(b, "visible", text="枠線を表示")
    content = layout.column()
    content.active = b.visible
    content.prop(b, "style")
    if b.style == "brush":
        content.prop(b, "blur_amount", slider=True)
        content.prop(b, "blur_dither")
    content.prop(b, "width_mm")
    content.prop(b, "color")
    row = content.row(align=True)
    row.prop(b, "corner_type")
    sub = row.row(align=True)
    sub.enabled = b.corner_type != "square"
    sub.prop(b, "corner_radius_mm", text="半径")


def draw_coma_white_margin_settings(layout, entry) -> None:
    wm = entry.white_margin
    layout.prop(wm, "enabled", text="白フチを表示")
    content = layout.column()
    content.active = wm.enabled
    content.prop(wm, "width_mm")
    content.prop(wm, "color")


def register() -> None:
    pass


def unregister() -> None:
    pass
