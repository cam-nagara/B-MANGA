"""フキダシ / テキストパネル (Phase 3 ページ単位対応)."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.work import get_active_page
from ..utils import balloon_shapes
from . import corner_radius_ui
B_NAME_CATEGORY = "B-Name"


class BNAME_UL_balloons(UIList):
    bl_idname = "BNAME_UL_balloons"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            icon_name = "OUTLINER_OB_FONT" if item.shape == "none" else "MOD_FLUID"
            row.label(text=item.id, icon=icon_name)
            row.prop(item, "shape", text="", emboss=False)


class BNAME_UL_texts(UIList):
    bl_idname = "BNAME_UL_texts"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.label(text=item.id, icon="FONT_DATA")
            body = str(getattr(item, "body", "") or "")
            row.label(text=body if body else "本文なし")
            if item.parent_balloon_id:
                row.label(text=f"→{item.parent_balloon_id}", icon="LINKED")
            else:
                row.label(text="独立", icon="UNLINKED")


class BNAME_PT_balloons(Panel):
    bl_idname = "BNAME_PT_balloons"
    bl_label = "フキダシ"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 10
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def draw(self, context):
        layout = self.layout
        page = get_active_page(context)
        if page is None:
            layout.label(text="ページを選択してください", icon="INFO")
            return

        layout.label(
            text=f"ページ {page.id} のフキダシ: {len(page.balloons)} 件",
            icon="FILE_IMAGE",
        )

        row = layout.row()
        row.template_list(
            BNAME_UL_balloons.bl_idname,
            "",
            page,
            "balloons",
            page,
            "active_balloon_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bname.balloon_add", text="", icon="ADD")
        col.operator("bname.balloon_remove", text="", icon="REMOVE")
        if sum(1 for balloon in page.balloons if getattr(balloon, "selected", False)) >= 2:
            layout.operator("bname.balloon_merge_selected", text="フキダシを結合", icon="FILE_FOLDER")

        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return
        entry = page.balloons[idx]

        # 親子連動ヘルプ
        child_count = sum(1 for t in page.texts if t.parent_balloon_id == entry.id)
        if child_count > 0:
            layout.label(
                text=f"子テキスト {child_count} 件が連動",
                icon="LINKED",
            )

        box = layout.box()
        box.prop(entry, "shape")
        if balloon_shapes.normalize_shape(entry.shape) == "custom":
            box.prop(entry, "custom_preset_name")
        row = box.row(align=True)
        row.prop(entry, "x_mm")
        row.prop(entry, "y_mm")
        row = box.row(align=True)
        row.prop(entry, "width_mm")
        row.prop(entry, "height_mm")
        box.prop(entry, "rotation_deg")
        # Meldex flipH/flipV/opacity 相当
        row = box.row(align=True)
        row.prop(entry, "flip_h", toggle=True)
        row.prop(entry, "flip_v", toggle=True)
        box.prop(entry, "opacity", slider=True)
        if balloon_shapes.normalize_shape(entry.shape) == "rect":
            box.prop(entry, "rounded_corner_enabled")
            sub = box.column(align=True)
            sub.enabled = entry.rounded_corner_enabled
            corner_radius_ui.draw_corner_radius(sub, entry)

        # 親子連動つき平行移動
        box = layout.box()
        box.label(text="親子連動移動 (子テキストも追随)", icon="CON_TRACKTO")
        row = box.row(align=True)
        op = row.operator("bname.balloon_move", text="← 5mm")
        op.delta_x_mm = -5.0
        op = row.operator("bname.balloon_move", text="→ 5mm")
        op.delta_x_mm = 5.0
        op = row.operator("bname.balloon_move", text="↑ 5mm")
        op.delta_y_mm = 5.0
        op = row.operator("bname.balloon_move", text="↓ 5mm")
        op.delta_y_mm = -5.0

        box = layout.box()
        box.label(text="線・塗り")
        row = box.row(align=True)
        row.prop(entry, "line_style")
        row.prop(entry, "line_width_mm")
        # 主線の谷の線幅/山の線幅: % 指定 (動的形状のみ表示, 両方 0% で主線全体消失)
        _shape_norm_for_main_line = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
        if _shape_norm_for_main_line in {"cloud", "fluffy", "thorn", "thorn-curve"}:
            row = box.row(align=True)
            row.prop(entry, "line_valley_width_pct")
            row.prop(entry, "line_peak_width_pct")
        if str(getattr(entry, "line_style", "") or "") == "double":
            row = box.row(align=True)
            row.prop(entry, "multi_line_count")
            row.prop(entry, "multi_line_direction")
            row = box.row(align=True)
            row.prop(entry, "multi_line_width_mm")
            row.prop(entry, "multi_line_spacing_mm")
            row = box.row(align=True)
            row.prop(entry, "multi_line_width_scale_percent")
            row.prop(entry, "multi_line_spacing_scale_percent")
            # 谷/山を持つ動的形状 (雲・モフモフ・トゲ直線・トゲ曲線) では
            # 「長さ変化 (%)」「谷の線幅」「山の線幅」が有効
            shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
            if shape_norm in {"cloud", "fluffy", "thorn", "thorn-curve"}:
                row = box.row(align=True)
                row.prop(entry, "thorn_multi_line_length_scale_near_percent")
                row.prop(entry, "thorn_multi_line_length_scale_far_percent")
                row = box.row(align=True)
                row.prop(entry, "thorn_multi_line_cross_enabled", toggle=True)
                row = box.row(align=True)
                row.prop(entry, "thorn_multi_line_valley_width_pct")
                row.prop(entry, "thorn_multi_line_peak_width_pct")
        row = box.row(align=True)
        row.prop(entry, "line_color")
        row.prop(entry, "fill_color")
        box.prop(entry, "fill_opacity", slider=True)
        box.prop_search(entry, "fill_material_name", bpy.data, "materials")
        row = box.row(align=True)
        row.prop(entry, "fill_blur_amount", slider=True)
        row.prop(entry, "fill_blur_dither", toggle=True)
        box.prop(entry, "fill_gradient_enabled")
        sub = box.column(align=True)
        sub.enabled = bool(getattr(entry, "fill_gradient_enabled", False))
        row = sub.row(align=True)
        row.prop(entry, "fill_gradient_start_color")
        row.prop(entry, "fill_gradient_end_color")
        sub.prop(entry, "fill_gradient_angle_deg")
        row = box.row(align=True)
        row.prop(entry, "outer_white_margin_enabled", text="外側フチ", toggle=True)
        sub = row.row(align=True)
        sub.enabled = bool(getattr(entry, "outer_white_margin_enabled", False))
        sub.prop(entry, "outer_white_margin_width_mm", text="幅")
        sub.prop(entry, "outer_white_margin_color", text="")
        row = box.row(align=True)
        row.prop(entry, "inner_white_margin_enabled", text="内側フチ", toggle=True)
        sub = row.row(align=True)
        sub.enabled = bool(getattr(entry, "inner_white_margin_enabled", False))
        sub.prop(entry, "inner_white_margin_width_mm", text="幅")
        sub.prop(entry, "inner_white_margin_color", text="")

        # 形状別パラメータ
        sp = entry.shape_params
        if balloon_shapes.is_dynamic_meldex_shape(entry.shape):
            box = layout.box()
            box.label(text="Meldex形状パラメータ")
            row = box.row(align=True)
            row.prop(sp, "dynamic_shape_base_kind", text="ベース")
            row = box.row(align=True)
            row.prop(sp, "cloud_bump_width_mm")
            row.prop(sp, "cloud_bump_width_jitter", text="乱れ")
            row = box.row(align=True)
            row.prop(sp, "cloud_bump_height_mm")
            row.prop(sp, "cloud_bump_height_jitter", text="乱れ")
            row = box.row(align=True)
            row.prop(sp, "cloud_offset_percent")
            row.prop(sp, "shape_seed")
            row = box.row(align=True)
            row.prop(sp, "cloud_sub_width_ratio")
            row.prop(sp, "cloud_sub_width_jitter", text="乱れ")
            row = box.row(align=True)
            row.prop(sp, "cloud_sub_height_ratio")
            row.prop(sp, "cloud_sub_height_jitter", text="乱れ")
            # 「角を尖らせる」は形状パラメータの一番下に置く
            box.prop(sp, "cloud_valley_sharp")

        # 尻尾
        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"尻尾 ({len(entry.tails)})")
        add_op = row.operator("bname.balloon_tail_add_target", text="", icon="ADD")
        add_op.page_id = str(getattr(page, "id", "") or "")
        add_op.balloon_id = str(getattr(entry, "id", "") or "")
        for i, tail in enumerate(entry.tails):
            sub = box.box()
            header = sub.row(align=True)
            header.label(text=f"尻尾 {i + 1}")
            remove_op = header.operator("bname.balloon_tail_remove", text="", icon="X")
            remove_op.page_id = str(getattr(page, "id", "") or "")
            remove_op.balloon_id = str(getattr(entry, "id", "") or "")
            remove_op.tail_index = i
            sub.prop(tail, "type", text="種類")
            sub.prop(tail, "direction_deg", text="方向")
            sub.prop(tail, "length_mm", text="長さ (mm)")
            row = sub.row(align=True)
            row.prop(tail, "root_width_mm", text="根元幅 (mm)")
            row.prop(tail, "tip_width_mm", text="先端幅 (mm)")
            bend = sub.row()
            bend.enabled = str(getattr(tail, "type", "") or "") == "curve"
            bend.prop(tail, "curve_bend", text="曲げ")
            sub.prop(tail, "custom_points_enabled")
            point_box = sub.column(align=True)
            point_box.enabled = bool(getattr(tail, "custom_points_enabled", False))
            row = point_box.row(align=True)
            row.prop(tail, "start_x_mm")
            row.prop(tail, "start_y_mm")
            row = point_box.row(align=True)
            row.prop(tail, "end_x_mm")
            row.prop(tail, "end_y_mm")


class BNAME_PT_texts(Panel):
    bl_idname = "BNAME_PT_texts"
    bl_label = "テキスト"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 11
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def draw(self, context):
        layout = self.layout
        page = get_active_page(context)
        if page is None:
            layout.label(text="ページを選択してください", icon="INFO")
            return

        layout.label(
            text=f"ページ {page.id} のテキスト: {len(page.texts)} 件",
            icon="FONT_DATA",
        )

        row = layout.row()
        row.template_list(
            BNAME_UL_texts.bl_idname,
            "",
            page,
            "texts",
            page,
            "active_text_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bname.text_add", text="", icon="ADD")
        col.operator("bname.text_remove", text="", icon="REMOVE")

        idx = page.active_text_index
        if not (0 <= idx < len(page.texts)):
            return
        entry = page.texts[idx]

        box = layout.box()
        box.prop(entry, "speaker_type")
        row = box.row(align=True)
        row.prop(entry, "x_mm")
        row.prop(entry, "y_mm")
        row = box.row(align=True)
        row.prop(entry, "width_mm")
        row.prop(entry, "height_mm")

        # 組版
        box = layout.box()
        box.label(text="組版", icon="FONT_DATA")
        box.prop(entry, "writing_mode")
        box.prop(entry, "font", text="基本フォント")
        row = box.row(align=True)
        row.prop(entry, "font_size_unit", text="")
        row.prop(entry, "font_size_value", text="サイズ")
        # Meldex fontBold/fontItalic 相当
        row = box.row(align=True)
        row.prop(entry, "font_bold", toggle=True)
        row.prop(entry, "font_italic", toggle=True)
        box.prop(entry, "color")
        row = box.row(align=True)
        row.prop(entry, "line_height")
        row.prop(entry, "letter_spacing")

        # 白フチ
        box = layout.box()
        box.prop(entry, "stroke_enabled")
        sub = box.column()
        sub.enabled = entry.stroke_enabled
        sub.prop(entry, "stroke_width_mm")
        sub.prop(entry, "stroke_color")

        # 親子連動
        box = layout.box()
        box.label(text="親フキダシ", icon="LINKED")
        row = box.row(align=True)
        row.prop(entry, "parent_balloon_id", text="ID")
        box.operator("bname.text_meta_dialog", text="メタ情報を編集", icon="INFO")
        # 既存フキダシ一覧からのクイック選択
        if len(page.balloons) > 0:
            row = box.row(align=True)
            row.label(text="紐付け:")
            for b in page.balloons:
                op = row.operator("bname.text_attach_to_balloon", text=b.id)
                op.balloon_id = b.id
            # 独立化ボタン
            op = box.operator(
                "bname.text_attach_to_balloon",
                text="独立テキストにする",
                icon="UNLINKED",
            )
            op.balloon_id = ""


_CLASSES = (
    BNAME_UL_balloons,
    BNAME_UL_texts,
    BNAME_PT_balloons,
    BNAME_PT_texts,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
