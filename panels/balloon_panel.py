"""フキダシ / テキストパネル (Phase 3 ページ単位対応)."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core import balloon as balloon_core
from ..core.work import get_active_page
from ..utils import balloon_shapes
from . import corner_radius_ui, effect_line_panel
B_NAME_CATEGORY = "B-MANGA"


def draw_white_outline_line_settings(box, entry, columns=None) -> None:
    """線種「白抜き線」の設定一式 (効果線の白抜き線と同等 + ウニフラ同等の入り抜き).

    フキダシのパネルと詳細設定ダイアログで共用する。``columns`` を渡すと
    白線/黒線/入り抜きを列に分配する (縦長になりすぎるダイアログ用)。
    """
    cols = [c for c in (columns or ()) if c is not None] or [box]

    def _col(index: int):
        return cols[min(int(index), len(cols) - 1)]

    row = box.row(align=True)
    row.prop(entry, "flash_white_outline_count")
    row.prop(entry, "white_outline_angle_deg")
    row = box.row(align=True)
    row.prop(entry, "flash_white_outline_width_mm")
    row.prop(entry, "white_outline_black_direction", text="")
    row = box.row(align=True)
    row.prop(entry, "white_outline_width_jitter_enabled")
    sub = row.row(align=True)
    sub.enabled = bool(getattr(entry, "white_outline_width_jitter_enabled", False))
    sub.prop(entry, "white_outline_width_min_percent", text="最小")
    row = box.row(align=True)
    row.prop(entry, "white_outline_length_jitter_enabled")
    sub = row.row(align=True)
    sub.enabled = bool(getattr(entry, "white_outline_length_jitter_enabled", False))
    sub.prop(entry, "white_outline_length_min_percent", text="最小")

    white_box = _col(1).box()
    white_box.label(text="白線")
    row = white_box.row(align=True)
    row.prop(entry, "white_outline_white_line_count_auto", toggle=True)
    sub = row.row(align=True)
    sub.enabled = not bool(getattr(entry, "white_outline_white_line_count_auto", False))
    sub.prop(entry, "flash_white_outline_white_line_count")
    row = white_box.row(align=True)
    row.prop(entry, "flash_white_outline_spacing_mm")
    ratio = row.row(align=True)
    ratio.enabled = bool(getattr(entry, "white_outline_white_line_count_auto", False))
    ratio.prop(entry, "white_outline_white_ratio_percent")
    white_box.prop(entry, "white_outline_white_attenuation", text="減衰")

    black_box = _col(1).box()
    black_box.label(text="黒線")
    row = black_box.row(align=True)
    row.prop(entry, "white_outline_black_line_count_auto", toggle=True)
    sub = row.row(align=True)
    sub.enabled = not bool(getattr(entry, "white_outline_black_line_count_auto", False))
    sub.prop(entry, "flash_white_outline_black_line_count")
    black_box.prop(entry, "flash_white_outline_black_spacing_mm")
    row = black_box.row(align=True)
    row.prop(entry, "white_outline_black_width_scale_percent")
    row.prop(entry, "white_outline_black_attenuation", text="減衰")
    row = black_box.row(align=True)
    row.prop(entry, "white_outline_black_length_scale_near_percent")
    row.prop(entry, "white_outline_black_length_scale_far_percent")

    inout_box = _col(2).box()
    inout_box.label(text="入り抜き")
    inout_box.prop(entry, "inout_apply")
    row = inout_box.row(align=True)
    row.prop(entry, "in_percent")
    row.prop(entry, "out_percent")
    row = inout_box.row(align=True)
    row.prop(entry, "in_start_percent")
    row.prop(entry, "out_start_percent")
    effect_line_panel.draw_inout_curve_mapping(inout_box, entry)


class BMANGA_UL_balloons(UIList):
    bl_idname = "BMANGA_UL_balloons"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            icon_name = "OUTLINER_OB_FONT" if item.shape == "none" else "MOD_FLUID"
            row.label(text=item.id, icon=icon_name)
            row.prop(item, "shape", text="", emboss=False)


class BMANGA_UL_texts(UIList):
    bl_idname = "BMANGA_UL_texts"

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


class BMANGA_PT_balloons(Panel):
    bl_idname = "BMANGA_PT_balloons"
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
            BMANGA_UL_balloons.bl_idname,
            "",
            page,
            "balloons",
            page,
            "active_balloon_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bmanga.balloon_add", text="", icon="ADD")
        col.operator("bmanga.balloon_remove", text="", icon="REMOVE")
        # 自分で描いたパス (B-MANGA 管理外カーブ) を選択した状態で押すと
        # 自由形状フキダシとして取り込める (右クリックメニューにも同項目あり)
        layout.operator(
            "bmanga.balloon_register_selected_curve",
            text="選択カーブをフキダシに登録",
            icon="MOD_CURVE",
        )
        if sum(1 for balloon in page.balloons if getattr(balloon, "selected", False)) >= 2:
            layout.operator("bmanga.balloon_merge_selected", text="フキダシを結合", icon="FILE_FOLDER")

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
            balloon_core.ensure_balloon_corner_type_initialized(entry)
            box.prop(entry, "corner_type")
            sub = box.column(align=True)
            sub.enabled = str(getattr(entry, "corner_type", "square") or "square") != "square"
            corner_radius_ui.draw_corner_radius(sub, entry)

        # 親子連動つき平行移動
        box = layout.box()
        box.label(text="親子連動移動 (子テキストも追随)", icon="CON_TRACKTO")
        row = box.row(align=True)
        op = row.operator("bmanga.balloon_move", text="← 5mm")
        op.delta_x_mm = -5.0
        op = row.operator("bmanga.balloon_move", text="→ 5mm")
        op.delta_x_mm = 5.0
        op = row.operator("bmanga.balloon_move", text="↑ 5mm")
        op.delta_y_mm = 5.0
        op = row.operator("bmanga.balloon_move", text="↓ 5mm")
        op.delta_y_mm = -5.0

        box = layout.box()
        box.label(text="線・塗り")
        row = box.row(align=True)
        row.prop(entry, "line_style")
        line_style = balloon_shapes.normalize_line_style(str(getattr(entry, "line_style", "") or ""))
        if line_style != "uni_flash":
            row.prop(entry, "line_width_mm")
        if line_style == "dashed":
            row = box.row(align=True)
            row.prop(entry, "dashed_segment_length_mm", text="線分")
            row.prop(entry, "dashed_gap_mm", text="間隔")
        elif line_style == "dotted":
            row = box.row(align=True)
            row.prop(entry, "dotted_gap_mm", text="間隔")
        elif line_style == "material":
            box.prop_search(entry, "line_material_name", bpy.data, "materials", text="マテリアル")
            box.prop(entry, "line_material_mapping", text="貼り方")
            if str(getattr(entry, "line_material_mapping", "tile") or "tile") == "ribbon":
                box.prop(entry, "line_material_stretch_single")
                if bool(getattr(entry, "line_material_stretch_single", False)):
                    box.prop(entry, "line_material_seam_fix", text="継ぎ目処理")
                    _seam_fix = str(getattr(entry, "line_material_seam_fix", "none") or "none")
                    if _seam_fix == "mirror":
                        box.label(text="行きと帰りで鏡像にして始点終点をつなげます (柄が途中で左右反転)", icon="INFO")
                    elif _seam_fix == "crossfade":
                        box.label(text="始点終点の手前を重ねて馴染ませます (出力で適用)", icon="INFO")
                    else:
                        box.label(text="1枚を引き伸ばすため、左右がつながらない柄は始点終点で途切れます", icon="INFO")
                else:
                    box.label(text="線に沿って貼り、周の長さに合わせて整数枚に調整するため切れ目は出ません", icon="INFO")
            else:
                box.label(text="フキダシの領域基準で貼るため、閉じた形でも切れ目は出ません", icon="INFO")
        # 主線の谷の線幅/山の線幅: % 指定 (動的形状のみ表示, 両方 0% で主線全体消失)
        _shape_norm_for_main_line = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
        if line_style == "uni_flash":
            effect_line_panel.draw_effect_params(
                box,
                entry,
                with_generate_button=False,
                fixed_effect_type="uni_flash",
                show_type=False,
            )
        elif balloon_shapes.is_flash_line_style(line_style):
            row = box.row(align=True)
            row.prop(entry, "flash_line_count", text="線の本数")
            row.prop(entry, "flash_line_spacing_mm", text="線の間隔")
            row = box.row(align=True)
            row.prop(entry, "line_valley_width_pct", text="入り・抜き")
            row.prop(entry, "line_peak_width_pct", text="中間線幅")
            if line_style == "white_outline":
                draw_white_outline_line_settings(box, entry)
        elif balloon_shapes.is_dynamic_meldex_shape(_shape_norm_for_main_line):
            row = box.row(align=True)
            row.prop(entry, "line_valley_width_pct")
            row.prop(entry, "line_peak_width_pct")
        if line_style == "double":
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
            if balloon_shapes.is_dynamic_meldex_shape(shape_norm):
                row = box.row(align=True)
                row.prop(entry, "thorn_multi_line_length_scale_near_percent")
                row.prop(entry, "thorn_multi_line_length_scale_far_percent")
                row = box.row(align=True)
                row.prop(entry, "thorn_multi_line_cross_enabled", toggle=True)
                row = box.row(align=True)
                row.prop(entry, "thorn_multi_line_valley_width_pct")
                row.prop(entry, "thorn_multi_line_peak_width_pct")
        if line_style != "uni_flash":
            row = box.row(align=True)
            row.prop(entry, "line_color")
            row.prop(entry, "fill_color")
            box.prop(entry, "fill_opacity", slider=True)
        if line_style != "uni_flash":
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
            box.label(text="形状パラメータ")
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
            # 「角を尖らせる」は形状パラメータの一番下に置く。
            # 雲・もやもやでは効かない確定仕様のためトゲ系でだけ表示する。
            if balloon_shapes.normalize_shape(str(entry.shape or "")) in {"thorn", "thorn-curve"}:
                box.prop(sp, "cloud_valley_sharp")

        # 尻尾 (個別の設定は「しっぽの詳細設定」ダイアログに集約)
        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"尻尾 ({len(entry.tails)})")
        add_op = row.operator("bmanga.balloon_tail_add_target", text="", icon="ADD")
        add_op.page_id = str(getattr(page, "id", "") or "")
        add_op.balloon_id = str(getattr(entry, "id", "") or "")
        detail_op = box.operator(
            "bmanga.balloon_tail_detail_open", text="しっぽの詳細設定...", icon="PREFERENCES"
        )
        detail_op.page_id = str(getattr(page, "id", "") or "")
        detail_op.balloon_id = str(getattr(entry, "id", "") or "")


class BMANGA_PT_texts(Panel):
    bl_idname = "BMANGA_PT_texts"
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
            BMANGA_UL_texts.bl_idname,
            "",
            page,
            "texts",
            page,
            "active_text_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bmanga.text_add", text="", icon="ADD")
        col.operator("bmanga.text_remove", text="", icon="REMOVE")

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
        box.operator("bmanga.text_meta_dialog", text="メタ情報を編集", icon="INFO")
        # 既存フキダシ一覧からのクイック選択
        if len(page.balloons) > 0:
            row = box.row(align=True)
            row.label(text="紐付け:")
            for b in page.balloons:
                op = row.operator("bmanga.text_attach_to_balloon", text=b.id)
                op.balloon_id = b.id
            # 独立化ボタン
            op = box.operator(
                "bmanga.text_attach_to_balloon",
                text="独立テキストにする",
                icon="UNLINKED",
            )
            op.balloon_id = ""


_CLASSES = (
    BMANGA_UL_balloons,
    BMANGA_UL_texts,
    BMANGA_PT_balloons,
    BMANGA_PT_texts,
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
