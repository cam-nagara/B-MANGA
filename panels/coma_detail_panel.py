"""コマ詳細設定ダイアログ用の描画ヘルパー."""

from __future__ import annotations

from ..utils import coma_blur_curve
from ..utils import object_selection


def draw_coma_shape_settings(layout, context, entry) -> None:
    if entry.shape_type == "rect":
        row = layout.row(align=True)
        row.prop(entry, "rect_x_mm")
        row.prop(entry, "rect_y_mm")
        row = layout.row(align=True)
        row.prop(entry, "rect_width_mm")
        row.prop(entry, "rect_height_mm")

    row = layout.row(align=True)
    row.operator(
        "bmanga.coma_edit_vertices",
        text="頂点/辺をドラッグ編集",
        icon="EDITMODE_HLT",
    )

    row = layout.row(align=True)
    if entry.shape_type == "rect":
        row.operator("bmanga.coma_to_polygon", text="多角形化", icon="MESH_DATA")
    else:
        row.operator("bmanga.coma_to_rect", text="矩形化 (外接)", icon="MESH_PLANE")
    if object_selection.selected_coma_count(context) >= 2:
        layout.operator("bmanga.coma_merge_selected", text="コマ結合", icon="AUTOMERGE_ON")

    row = layout.row(align=True)
    row.prop(entry, "paper_visible", text="背景")
    row.prop(entry, "background_color", text="背景色")


def draw_coma_border_settings(layout, context, entry, *, preset_mode: bool = False) -> None:
    """コマ枠線設定を描画する.

    ``preset_mode=True`` は枠線プリセット詳細編集ダイアログからの呼び出し用で、
    実コマとは無関係なスクラッチ入れ物 (``entry``) を渡す。この場合、実コマ
    前提の要素 (プリセット選択・適用列 = 「別のプリセットをこのコマへ適用」の
    ためのUI) は入れ子になり意味を持たないため描画しない。
    """
    b = entry.border
    wm = getattr(context, "window_manager", None)
    if not preset_mode and wm is not None and hasattr(wm, "bmanga_border_preset_selector"):
        row = layout.row(align=True)
        preset = row.row(align=True)
        preset.label(text="プリセット", icon="PRESET")
        preset.prop(wm, "bmanga_border_preset_selector", text="")
        selected = str(getattr(wm, "bmanga_border_preset_selector", "") or "")
        tools = row.row(align=True)
        tools.alignment = "RIGHT"
        tools.operator("bmanga.border_preset_add_local", text="", icon="ADD")
        op = tools.operator("bmanga.border_preset_rename", text="", icon="GREASEPENCIL")
        op.preset_name = selected
        op = tools.operator("bmanga.border_preset_duplicate", text="", icon="DUPLICATE")
        op.preset_name = selected
        op = tools.operator("bmanga.border_preset_delete", text="", icon="TRASH")
        op.preset_name = selected
        tools.separator()
        op = tools.operator("bmanga.border_preset_move", text="", icon="TRIA_UP")
        op.preset_name = selected
        op.direction = "UP"
        op = tools.operator("bmanga.border_preset_move", text="", icon="TRIA_DOWN")
        op.preset_name = selected
        op.direction = "DOWN"
    row = layout.row(align=True)
    row.prop(b, "visible", text="枠線を表示")
    row.prop(b, "style", text="線種")
    content = layout.column()
    content.active = b.visible
    if b.style == "brush":
        row = content.row(align=True)
        row.prop(b, "blur_amount", text="ボカシ量", slider=True)
        row.prop(b, "blur_dither")
        curve_node = coma_blur_curve.ui_curve_node_for_border(b)
        if curve_node is not None:
            content.label(text="ぼかしカーブ")
            content.template_curve_mapping(curve_node, "mapping", type="NONE")
    row = content.row(align=True)
    row.prop(b, "width_mm", text="線幅")
    row.prop(b, "color", text="線色")
    row = content.row(align=True)
    row.prop(b, "corner_type", text="角")
    sub = row.row(align=True)
    sub.enabled = b.corner_type != "square"
    sub.prop(b, "corner_radius_mm", text="半径")


def draw_coma_white_margin_settings(layout, entry) -> None:
    wm = entry.white_margin
    row = layout.row(align=True)
    row.prop(wm, "enabled", text="フチ", toggle=True)
    sub = row.row(align=True)
    sub.enabled = bool(wm.enabled)
    sub.prop(wm, "placement", text="")
    content = layout.column()
    content.active = wm.enabled
    row = content.row(align=True)
    row.prop(wm, "width_mm", text="幅")
    if str(getattr(wm, "placement", "outside") or "outside") in {"outside", "both"}:
        row.prop(wm, "outer_color", text="外側色")
    if str(getattr(wm, "placement", "outside") or "outside") in {"inside", "both"}:
        row.prop(wm, "inner_color", text="内側色")


def register() -> None:
    pass


def unregister() -> None:
    pass
