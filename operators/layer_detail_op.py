"""選択中レイヤー Object の詳細設定ダイアログを開く operator.

Outliner / 3D ビュー / 各種ツールから右クリックで呼べる単一エントリポイント。
active_object の ``bmanga_kind`` / ``bmanga_id`` から対応 entry を逆引きし、
kind ごとのフィールドを ``invoke_props_dialog`` で編集可能に表示する。
"""

from __future__ import annotations

from typing import Optional

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core import balloon as balloon_core
from ..utils import log
from ..utils import object_naming as on
from ..utils import balloon_shapes
from ..utils import balloon_curve_object
from ..utils import balloon_curve_source_state

_logger = log.get_logger(__name__)
_DETAIL_DIALOG_DEFAULT_WIDTH = 260
_DETAIL_DIALOG_BALLOON_WIDTH = 1080
_DETAIL_DIALOG_EFFECT_WIDTH = 1320


def _has_safe_gp_layer_prop(layer, prop_name: str) -> bool:
    if prop_name == "tint_factor":
        return False
    props = getattr(getattr(layer, "bl_rna", None), "properties", None)
    if props is None:
        return False
    try:
        props[prop_name]
        return True
    except Exception:  # noqa: BLE001
        return False


def _balloon_source_state_label(entry) -> tuple[str, str]:
    try:
        state = balloon_curve_object.source_state_for_entry(entry)
    except Exception:  # noqa: BLE001
        state = balloon_curve_source_state.STATE_GENERATED
    if state == balloon_curve_source_state.STATE_MANUAL:
        return state, "手編集あり"
    if state == balloon_curve_source_state.STATE_FREEFORM:
        return state, "自由形状"
    return state, "生成形状"


def _draw_balloon_regenerate_buttons(layout, entry, page) -> str:
    state, label = _balloon_source_state_label(entry)
    row = layout.row(align=True)
    row.label(text=f"編集状態: {label}")
    row = layout.row(align=True)
    op = row.operator("bmanga.balloon_regenerate_keep_edit", text="手編集を維持して再生成", icon="MOD_CURVE")
    op.page_id = str(getattr(page, "id", "") or "")
    op.balloon_id = str(getattr(entry, "id", "") or "")
    op = row.operator("bmanga.balloon_regenerate_discard_edit", text="手編集を破棄して再生成", icon="TRASH")
    op.page_id = str(getattr(page, "id", "") or "")
    op.balloon_id = str(getattr(entry, "id", "") or "")
    return state


def _resolve_active_managed_object(context) -> Optional[bpy.types.Object]:
    """B-MANGA 管理下のレイヤー Object を解決する.

    優先順位: active_object → selected_objects → selected_ids (Outliner) →
    view_layer.active。Outliner で選択中の Object も拾えるよう全経路を確認する。
    """
    obj = getattr(context, "active_object", None)
    if obj is not None and on.is_managed(obj):
        return obj
    selected = getattr(context, "selected_objects", None) or ()
    for o in selected:
        if on.is_managed(o):
            return o
    selected_ids = getattr(context, "selected_ids", None) or ()
    for sid in selected_ids:
        if isinstance(sid, bpy.types.Object) and on.is_managed(sid):
            return sid
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        active = getattr(view_layer, "active", None)
        if active is not None and on.is_managed(active):
            return active
    return None


def _find_image_entry(scene, bid: str):
    coll = getattr(scene, "bmanga_image_layers", None)
    if coll is None:
        return None
    for e in coll:
        if str(getattr(e, "id", "") or "") == bid:
            return e
    return None


def _find_image_path_entry(scene, bid: str):
    coll = getattr(scene, "bmanga_image_path_layers", None)
    if coll is None:
        return None
    for e in coll:
        if str(getattr(e, "id", "") or "") == bid:
            return e
    return None


def _find_raster_entry(scene, bid: str):
    coll = getattr(scene, "bmanga_raster_layers", None)
    if coll is None:
        return None
    for e in coll:
        if str(getattr(e, "id", "") or "") == bid:
            return e
    return None


def _find_balloon_entry(scene, bid: str):
    work = getattr(scene, "bmanga_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for e in getattr(page, "balloons", []):
            if str(getattr(e, "id", "") or "") == bid:
                return page, e
    for e in getattr(work, "shared_balloons", []):
        if str(getattr(e, "id", "") or "") == bid:
            return None, e
    return None, None


def _find_text_entry(scene, bid: str):
    try:
        from ..utils import text_real_object

        page, entry = text_real_object.find_text_entry(scene, bid)
        if entry is not None:
            return page, entry
    except Exception:  # noqa: BLE001
        pass
    work = getattr(scene, "bmanga_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for e in getattr(page, "texts", []):
            if str(getattr(e, "id", "") or "") == bid:
                return page, e
    return None, None


def _draw_image_detail(layout, entry) -> None:
    layout.prop(entry, "title", text="表示名")
    layout.prop(entry, "filepath", text="画像パス")
    box = layout.box()
    box.label(text="配置 (mm)")
    row = box.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = box.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    box.prop(entry, "rotation_deg")
    row = box.row(align=True)
    row.prop(entry, "flip_x")
    row.prop(entry, "flip_y")
    box = layout.box()
    box.label(text="表示")
    box.prop(entry, "visible")
    box.prop(entry, "locked")
    box.prop(entry, "opacity")
    box.prop(entry, "blend_mode")
    box.prop(entry, "tint_color")
    box = layout.box()
    box.label(text="補正")
    box.prop(entry, "brightness")
    box.prop(entry, "contrast")
    box.prop(entry, "binarize_enabled")
    if getattr(entry, "binarize_enabled", False):
        box.prop(entry, "binarize_threshold")


def _draw_image_path_detail(layout, context, entry=None) -> None:
    if entry is None:
        entry = context
        context = bpy.context
    layout.prop(entry, "title", text="表示名")
    layout.prop(entry, "content_source", text="内容")
    source = str(getattr(entry, "content_source", "image") or "image")
    if source == "shape":
        row = layout.row(align=True)
        row.prop(entry, "shape_kind", text="生成形状")
        if str(getattr(entry, "shape_kind", "") or "") == "polygon":
            row.prop(entry, "shape_sides", text="角数")
    else:
        layout.prop(entry, "filepath", text="画像")
    box = layout.box()
    box.label(text="表示")
    if source == "image":
        box.prop(entry, "draw_mode", text="表示方法")
    box.prop(entry, "visible", text="表示")
    box.prop(entry, "locked", text="ロック")
    box.prop(entry, "opacity", text="不透明度")
    row = box.row(align=True)
    row.prop(entry, "brush_size_mm", text="ブラシサイズ")
    row.prop(entry, "aspect_ratio", text="縦横比")
    row = box.row(align=True)
    row.prop(entry, "image_angle_deg", text="角度")
    row.prop(entry, "spacing_percent", text="間隔")
    box.prop(entry, "color", text="色")
    if source == "image" and str(getattr(entry, "draw_mode", "stamp") or "stamp") == "stamp":
        box.prop(entry, "stamp_angle_mode", text="角度")
        if str(getattr(entry, "stamp_angle_mode", "") or "") == "object":
            box.prop_search(entry, "stamp_angle_object_name", bpy.data, "objects", text="方向オブジェクト")
    elif source == "image":
        box.prop(entry, "ribbon_repeat_mode", text="リボン")
    inout_box = layout.box()
    inout_box.label(text="入り抜き")
    row = inout_box.row(align=True)
    row.prop(entry, "inout_size_enabled", toggle=True)
    row.prop(entry, "inout_opacity_enabled", toggle=True)
    row.prop(entry, "inout_color_enabled", toggle=True)
    row = inout_box.row(align=True)
    row.prop(entry, "in_percent")
    row.prop(entry, "out_percent")
    color_row = inout_box.row(align=True)
    color_row.enabled = bool(getattr(entry, "inout_color_enabled", False))
    color_row.prop(entry, "inout_start_color", text="入り色")
    color_row.prop(entry, "inout_end_color", text="抜き色")
    from ..panels import effect_line_panel as _effect_line_panel

    _effect_line_panel.draw_inout_curve_mapping(inout_box, entry)
    from ..panels import preset_management_ui

    preset_management_ui.draw_image_path_preset_management(layout, context)


def _draw_raster_detail(layout, entry) -> None:
    layout.prop(entry, "title", text="表示名")
    layout.prop(entry, "image_name", text="Image 名")
    layout.prop(entry, "filepath_rel", text="PNG 相対パス")
    layout.prop(entry, "dpi")
    layout.prop(entry, "bit_depth")
    layout.prop(entry, "line_color")
    layout.prop(entry, "opacity")
    layout.prop(entry, "visible")
    layout.prop(entry, "locked")
    layout.prop(entry, "scope")


def _draw_balloon_tails(layout, entry, page) -> None:
    """しっぽの設定は独立したダイアログへ分離した。ここでは入口だけ置く."""
    box = layout.box()
    row = box.row(align=True)
    row.label(text=f"しっぽ ({len(entry.tails)})")
    open_op = row.operator("bmanga.balloon_tail_detail_open", text="しっぽの詳細設定", icon="PREFERENCES")
    open_op.page_id = str(getattr(page, "id", "") or "")
    open_op.balloon_id = str(getattr(entry, "id", "") or "")


def _draw_corner_radius(layout, owner, *, prefix: str = "rounded_corner", text: str = "角半径") -> None:
    row = layout.row(align=True)
    unit_attr = f"{prefix}_radius_unit"
    if str(getattr(owner, unit_attr, "mm") or "mm") == "percent":
        row.prop(owner, f"{prefix}_radius_percent", text=text)
    else:
        row.prop(owner, f"{prefix}_radius_mm", text=text)
    row.prop(owner, unit_attr, text="")


def _equal_columns(layout, count: int):
    column_count = max(1, int(count))
    grid = layout.grid_flow(
        row_major=True,
        columns=column_count,
        even_columns=True,
        even_rows=False,
        align=True,
    )
    return tuple(grid.column(align=True) for _ in range(column_count))


def _detail_dialog_width_for_kind(_context, kind: str, _bmanga_id: str) -> int:
    if kind == "balloon":
        # Blender の標準プロパティダイアログは、開いた後に幅を変更できない。
        # フキダシは線種変更で通常線からウニフラ/白抜き線へ列数が増えるため、
        # 最初から最大列数に合う幅で開く。
        return _DETAIL_DIALOG_BALLOON_WIDTH
    if kind in {"effect", "effect_legacy"}:
        return _DETAIL_DIALOG_EFFECT_WIDTH
    return _DETAIL_DIALOG_DEFAULT_WIDTH


def _draw_balloon_detail(layout, context, entry=None, page=None) -> None:
    if entry is None or not hasattr(context, "scene"):
        old_entry = context
        old_page = entry
        context = bpy.context
        entry = old_entry
        page = old_page
    if hasattr(entry, "title"):
        layout.prop(entry, "title", text="表示名")
    from ..panels import preset_management_ui

    preset_management_ui.draw_balloon_preset_management(layout, context)

    # 縦長になりすぎるため複数列に分ける。
    # 標準ダイアログは表示中に幅を変えられないため、線種切替で列数が増えても
    # 幅不足にならないよう常に 4 列分の幅で描画する。
    dialog_line_style = balloon_shapes.normalize_line_style(str(getattr(entry, "line_style", "") or ""))
    left_col, right_col, effect_col3, effect_col4 = _equal_columns(layout, 4)
    effect_cols = (effect_col3, effect_col4)
    tail_col = left_col if dialog_line_style in {"uni_flash", "white_outline"} else effect_col3

    # 配置 (mm) を Outliner メタ の次 (最上段) に配置
    box = left_col.box()
    box.label(text="配置 (mm)")
    row = box.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = box.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    box.prop(entry, "rotation_deg")
    row = box.row(align=True)
    row.prop(entry, "flip_h")
    row.prop(entry, "flip_v")

    box = left_col.box()
    box.label(text="形状")
    source_state = _draw_balloon_regenerate_buttons(box, entry, page)
    box.prop(entry, "shape")
    if str(getattr(entry, "shape", "")) == "custom":
        box.prop(entry, "custom_preset_name")
    if balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or "")) == "rect":
        balloon_core.ensure_balloon_corner_type_initialized(entry)
        box.prop(entry, "corner_type")
        sub = box.column(align=True)
        sub.enabled = str(getattr(entry, "corner_type", "square") or "square") != "square"
        _draw_corner_radius(sub, entry)
    sp = getattr(entry, "shape_params", None)
    if (
        sp is not None
        and source_state != balloon_curve_source_state.STATE_FREEFORM
        and balloon_shapes.is_dynamic_meldex_shape(str(getattr(entry, "shape", "") or ""))
    ):
        shape_box = left_col.box()
        shape_box.label(text="形状パラメータ")
        col = shape_box.column(align=True)
        row = col.row(align=True)
        row.prop(sp, "dynamic_shape_base_kind", text="ベース")
        if str(getattr(sp, "dynamic_shape_base_kind", "ellipse") or "ellipse") == "rect":
            row.prop(sp, "dynamic_base_rounded_corner_enabled", text="丸角", toggle=True)
            sub = col.column(align=True)
            sub.enabled = bool(getattr(sp, "dynamic_base_rounded_corner_enabled", False))
            _draw_corner_radius(sub, sp, prefix="dynamic_base_rounded_corner", text="ベース角半径")
        row = col.row(align=True)
        row.prop(sp, "cloud_bump_width_mm")
        row.prop(sp, "cloud_bump_width_jitter", text="乱れ")
        row = col.row(align=True)
        row.prop(sp, "cloud_bump_height_mm")
        row.prop(sp, "cloud_bump_height_jitter", text="乱れ")
        row = col.row(align=True)
        row.prop(sp, "cloud_offset_percent")
        row.prop(sp, "shape_seed")
        row = col.row(align=True)
        row.prop(sp, "cloud_sub_width_ratio")
        row.prop(sp, "cloud_sub_width_jitter", text="乱れ")
        row = col.row(align=True)
        row.prop(sp, "cloud_sub_height_ratio")
        row.prop(sp, "cloud_sub_height_jitter", text="乱れ")
        # 「角を尖らせる」は形状パラメータの一番下に置く。
        # 雲・もやもやの主線は常に尖る確定仕様で切替が効かないため、
        # 効果のあるトゲ系の形状でだけ表示する。
        if balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or "")) in {"thorn", "thorn-curve"}:
            shape_box.prop(sp, "cloud_valley_sharp")

    box = right_col.box()
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
                    box.label(text="鏡像の往復で始点終点をつなげます (柄が途中で左右反転)", icon="INFO")
                elif _seam_fix == "crossfade":
                    box.label(text="始点終点の手前を重ねて馴染ませます (出力で適用)", icon="INFO")
                else:
                    box.label(text="左右がつながらない柄は始点終点で途切れます", icon="INFO")
            else:
                box.label(text="線に沿って貼り、周の長さに合わせて整数枚に調整します", icon="INFO")
        else:
            box.label(text="領域基準で貼るため、閉じた形でも切れ目は出ません", icon="INFO")
    elif line_style == "shape":
        row = box.row(align=True)
        row.prop(entry, "line_shape_kind", text="")
        row.prop(entry, "line_shape_spacing_mm", text="間隔")
        row = box.row(align=True)
        row.prop(entry, "line_shape_angle_deg", text="角度")
        orient_row = box.row(align=True)
        orient_row.label(text="向き")
        orient_row.prop(entry, "line_shape_orient", expand=True)
        row = box.row(align=True)
        row.prop(entry, "line_shape_jitter", text="乱れ", slider=True)
        box.label(text="図形の大きさは「線幅」で決まります", icon="INFO")
    elif line_style == "image":
        box.prop(entry, "line_image_path", text="画像")
        row = box.row(align=True)
        row.prop(entry, "line_image_interval_mm", text="間隔")
        row.prop(entry, "line_image_angle_deg", text="角度")
        box.prop(entry, "line_image_jitter", text="乱れ", slider=True)
        box.label(text="画像は線に沿って引き延ばされます (幅=線幅)", icon="INFO")
    # 主線の谷/山の線幅: % 指定 (動的形状のみ表示, 両方 0% で主線全体消失)
    _shape_norm_main_line = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
    if line_style == "uni_flash":
        from ..panels import effect_line_panel

        # 設定群が長いので「線・塗り」列 + 追加 2 列に分配する
        uni_columns = (box, *effect_cols) if effect_cols else None
        effect_line_panel.draw_effect_params(
            box,
            entry,
            with_generate_button=False,
            fixed_effect_type="uni_flash",
            show_type=False,
            columns=uni_columns,
            show_path_settings=False,
        )
    elif balloon_shapes.is_flash_line_style(line_style):
        row = box.row(align=True)
        row.prop(entry, "flash_line_count", text="線の本数")
        row.prop(entry, "flash_line_spacing_mm", text="線の間隔")
        row = box.row(align=True)
        row.prop(entry, "line_valley_width_pct", text="入り・抜き")
        row.prop(entry, "line_peak_width_pct", text="中間線幅")
        if line_style == "white_outline":
            from ..panels import balloon_panel as _bp

            _bp.draw_white_outline_line_settings(
                box,
                entry,
                columns=((box, *effect_cols) if effect_cols else None),
            )
    elif balloon_shapes.is_dynamic_meldex_shape(_shape_norm_main_line):
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
        # 谷/山を持つ動的形状では
        # 「長さ変化(%)」「谷の線幅」「山の線幅」が有効
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
    # 白抜き線は線群そのものが本体で塗りを持たないため、塗り関連は表示しない
    has_body_fill = line_style not in {"uni_flash", "white_outline"}
    if line_style != "uni_flash":
        row = box.row(align=True)
        row.prop(entry, "line_color")
        if has_body_fill:
            row.prop(entry, "fill_color")
            box.prop(entry, "fill_opacity", slider=True)
    if line_style != "uni_flash":
        if has_body_fill:
            box.prop_search(entry, "fill_material_name", bpy.data, "materials")
            row = box.row(align=True)
            row.prop(entry, "fill_blur_amount", slider=True)
            row.prop(entry, "fill_blur_axis", text="")
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
        box.prop(entry, "opacity", slider=True)

    _draw_balloon_tails(tail_col, entry, page)


def _draw_text_detail(layout, context, entry=None) -> None:
    if entry is None:
        entry = context
        context = bpy.context
    from ..panels import preset_management_ui

    preset_management_ui.draw_text_preset_selection(layout, context)
    box = layout.box()
    box.label(text="配置 (mm)")
    row = box.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = box.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")

    box = layout.box()
    box.label(text="話者")
    box.prop(entry, "speaker_type")
    box.prop(entry, "speaker_name")

    box = layout.box()
    box.label(text="フォント・組版")
    box.prop(entry, "font")
    row = box.row(align=True)
    row.prop(entry, "font_size_unit", text="")
    row.prop(entry, "font_size_value", text="サイズ")
    row = box.row(align=True)
    row.prop(entry, "font_bold")
    row.prop(entry, "font_italic")
    box.prop(entry, "color")
    box.prop(entry, "writing_mode")
    row = box.row(align=True)
    row.prop(entry, "line_height")
    row.prop(entry, "letter_spacing")

    box = layout.box()
    box.label(text="白フチ")
    box.prop(entry, "stroke_enabled")
    sub = box.column(align=True)
    sub.enabled = bool(getattr(entry, "stroke_enabled", False))
    sub.prop(entry, "stroke_width_mm")
    sub.prop(entry, "stroke_color")

    box = layout.box()
    box.label(text="ルビ・部分スタイル")
    row = box.row(align=True)
    row.label(text=f"ルビ: {len(getattr(entry, 'ruby_spans', ()) or ())} 件")
    row.label(text=f"部分フォント: {len(getattr(entry, 'font_spans', ()) or ())} 件")
    row = box.row(align=True)
    row.label(text=f"部分スタイル: {len(getattr(entry, 'style_spans', ()) or ())} 件")
    row.label(text=f"縦中横: {len(getattr(entry, 'tatechuyoko_ranges', ()) or ())} 件")
    row = box.row(align=True)
    row.prop(entry, "ruby_line_height")
    row.prop(entry, "ruby_gap_mm")
    row = box.row(align=True)
    row.prop(entry, "ruby_letter_spacing")
    row.prop(entry, "ruby_size_percent")
    box.prop(entry, "ruby_font")
    row = box.row(align=True)
    row.operator("bmanga.text_ruby_add_dialog", text="ルビを付ける", icon="ADD")
    row.operator("bmanga.text_ruby_clear", text="ルビを削除", icon="TRASH")
    box.operator("bmanga.text_meta_dialog", text="メタ情報を編集", icon="INFO")


def _draw_gp_detail(layout, obj) -> None:
    """GP レイヤー Object の詳細."""
    box = layout.box()
    box.label(text="基本")
    box.prop(obj, '["bmanga_title"]', text="表示名")
    box.prop(obj, '["bmanga_z_index"]', text="z_index")
    box.prop(obj, "hide_viewport", text="非表示 (ビューポート)")
    box.prop(obj, "hide_render", text="非表示 (レンダー)")

    gp_data = getattr(obj, "data", None)
    layers = getattr(gp_data, "layers", None) if gp_data is not None else None
    if layers is not None and len(layers) > 0:
        active_layer = getattr(layers, "active", None)
        if active_layer is not None:
            box = layout.box()
            box.label(text=f"アクティブ GP レイヤー: {active_layer.name}")
            if _has_safe_gp_layer_prop(active_layer, "opacity"):
                box.prop(active_layer, "opacity")
            if _has_safe_gp_layer_prop(active_layer, "tint_color"):
                box.prop(active_layer, "tint_color")
            row = box.row(align=True)
            if _has_safe_gp_layer_prop(active_layer, "hide"):
                row.prop(active_layer, "hide", text="非表示")
            if _has_safe_gp_layer_prop(active_layer, "lock"):
                row.prop(active_layer, "lock", text="ロック")


def _active_gp_layer(obj):
    gp_data = getattr(obj, "data", None)
    layers = getattr(gp_data, "layers", None) if gp_data is not None else None
    return getattr(layers, "active", None) if layers is not None else None


def _mark_effect_detail_target(context, obj, active_layer) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or obj is None or active_layer is None:
        return
    try:
        from ..utils import layer_stack as layer_stack_utils

        scene.bmanga_active_layer_kind = "effect"
        scene.bmanga_active_effect_layer_name = layer_stack_utils._node_stack_key(active_layer)
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.data.layers.active = active_layer
    except Exception:  # noqa: BLE001
        pass


def _effect_detail_target_key(obj, active_layer) -> str:
    if obj is None or active_layer is None:
        return ""
    try:
        from ..utils import layer_stack as layer_stack_utils

        return f"{on.get_bmanga_id(obj)}:{layer_stack_utils._node_stack_key(active_layer)}"
    except Exception:  # noqa: BLE001
        return ""


def _load_effect_detail_params_from_layer(context, obj, active_layer) -> bool:
    if obj is None or active_layer is None:
        return False
    try:
        from . import effect_line_op as _elo

        _elo._load_layer_params_to_scene(context, obj, active_layer)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("effect detail: load params failed")
    return False


def _apply_effect_detail_params_to_layer(context, obj, active_layer) -> bool:
    scene = getattr(context, "scene", None)
    params = getattr(scene, "bmanga_effect_line_params", None) if scene is not None else None
    if obj is None or active_layer is None or params is None:
        return False
    try:
        from . import effect_line_op as _elo
        from ..utils import layer_stack as layer_stack_utils

        bounds = _elo.effect_layer_bounds(obj, active_layer)
        if bounds is None:
            return False
        _elo._write_effect_strokes(context, obj, active_layer, bounds, params_override=params)
        layer_stack_utils.tag_view3d_redraw(context)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("effect detail: apply params failed")
    return False


def _draw_effect_detail(layout, context, obj, *, load_from_layer: bool = True) -> None:
    """効果線 Object の詳細 (params 全表示)."""
    box = layout.box()
    box.label(text="基本")
    box.prop(obj, '["bmanga_title"]', text="表示名")
    box.prop(obj, '["bmanga_z_index"]', text="z_index")
    if "bmanga_effect_target" in obj.keys():
        box.prop(obj, '["bmanga_effect_target"]', text="参照対象")

    # アクティブ GP レイヤーを解決して params をシーン側 PropertyGroup に同期
    active_layer = _active_gp_layer(obj)
    scene = getattr(context, "scene", None)
    params = getattr(scene, "bmanga_effect_line_params", None) if scene is not None else None

    if params is None:
        layout.label(text="効果線パラメータ未初期化", icon="ERROR")
        return
    if active_layer is None:
        layout.label(text="(GP レイヤー未選択)", icon="INFO")
        return

    _mark_effect_detail_target(context, obj, active_layer)

    # 保存済み設定の読み込みは開いた直後だけにする。
    # 線幅グラフは PropertyGroup ではなく CurveMapping を直接編集するため、
    # 再描画のたびに読み直すと、OK 前のグラフ編集が保存済み値で戻ってしまう。
    if load_from_layer:
        _load_effect_detail_params_from_layer(context, obj, active_layer)

    # effect_line_panel の draw_effect_params を再利用。
    # 縦長になりすぎるため、種類/形状・線・入り抜き・色・パス線の 5 列に分配する
    from ..panels import effect_line_panel as _elp

    layout.separator()
    _elp.draw_effect_line_preset_management(layout, context)
    cols = _equal_columns(layout, 5)
    _elp.draw_effect_params(cols[0], params, with_generate_button=True, columns=cols)


def _draw_object_meta(layout, obj) -> None:
    """Object 自身の B-MANGA メタを表示 (Custom Property 直接編集)."""
    box = layout.box()
    box.label(text="Outliner メタ", icon="OUTLINER")
    row = box.row(align=True)
    row.label(text=f"kind: {on.get_kind(obj)}")
    row.label(text=f"id: {on.get_bmanga_id(obj)}")
    row = box.row(align=True)
    row.label(text=f"親: {obj.get('bmanga_parent_key', '')}")
    if obj.get("bmanga_folder_id"):
        row.label(text=f"フォルダ: {obj['bmanga_folder_id']}")
    box.label(text=f"z_index: {obj.get('bmanga_z_index', 0)}")


def _prepare_detail_profile_nodes(context, kind: str, bmanga_id: str) -> None:
    """線幅グラフのノードをダイアログ表示前に作成する.

    draw 中は ID データを作成できないため、operator の invoke (書き込み可能)
    で全体・白線・黒線のグラフノードを先に用意する。
    """
    try:
        from ..utils import effect_inout_curve

        params = None
        is_white_outline = False
        if kind in {"effect", "effect_legacy"}:
            params = getattr(context.scene, "bmanga_effect_line_params", None)
            is_white_outline = (
                str(getattr(params, "effect_type", "") or "") == "white_outline"
            )
        elif kind == "balloon":
            _page, entry = _find_balloon_entry(context.scene, bmanga_id)
            line_style = balloon_shapes.normalize_line_style(
                str(getattr(entry, "line_style", "") or "")
            )
            if line_style in {"uni_flash", "white_outline"}:
                params = entry
                is_white_outline = line_style == "white_outline"
        if params is None:
            return
        effect_inout_curve.ensure_profile_node(params)
        if is_white_outline:
            effect_inout_curve.ensure_profile_node(
                params,
                fields=effect_inout_curve.WHITE_PROFILE_FIELDS,
                node_name=effect_inout_curve.WHITE_PROFILE_NODE_NAME,
                source_prop=effect_inout_curve.WHITE_PROFILE_SOURCE_PROP,
            )
            effect_inout_curve.ensure_profile_node(
                params,
                fields=effect_inout_curve.BLACK_PROFILE_FIELDS,
                node_name=effect_inout_curve.BLACK_PROFILE_NODE_NAME,
                source_prop=effect_inout_curve.BLACK_PROFILE_SOURCE_PROP,
            )
    except Exception:  # noqa: BLE001
        _logger.exception("detail profile nodes prepare failed")


def _sync_detail_profile_curve(context, kind: str, bmanga_id: str) -> bool:
    try:
        from ..utils import effect_inout_curve

        if kind in {"effect", "effect_legacy"}:
            params = getattr(context.scene, "bmanga_effect_line_params", None)
            if params is not None:
                from . import effect_line_op as _elo

                _elo._set_scene_params_syncing(context.scene, True)
                try:
                    return bool(effect_inout_curve.sync_active_profile_nodes_to_params(params))
                finally:
                    _elo._set_scene_params_syncing(context.scene, False)
        elif kind == "balloon":
            _page, params = _find_balloon_entry(context.scene, bmanga_id)
            if params is not None and balloon_shapes.normalize_line_style(
                str(getattr(params, "line_style", "") or "")
            ) not in {"uni_flash", "white_outline"}:
                params = None
        else:
            params = None
        if params is not None:
            return bool(effect_inout_curve.sync_active_profile_nodes_to_params(params))
    except Exception:  # noqa: BLE001
        _logger.exception("detail profile curve sync failed")
    return False


class BMANGA_OT_layer_detail_open(Operator):
    """選択中の B-MANGA レイヤー Object の詳細設定ダイアログを開く."""

    bl_idname = "bmanga.layer_detail_open"
    bl_label = "詳細設定"
    bl_description = (
        "選択中のレイヤー Object (画像/ラスター/フキダシ/テキスト/GP/効果線) "
        "の詳細設定ダイアログを開きます。Outliner / 3D ビュー / 各ツールの "
        "右クリックメニューから呼び出せます。"
    )
    bl_options = {"REGISTER", "UNDO"}

    bmanga_id: StringProperty(name="bmanga_id", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    kind: StringProperty(name="kind", default="", options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _resolve_active_managed_object(context) is not None

    def invoke(self, context, event):
        obj = _resolve_active_managed_object(context)
        if obj is None:
            self.report({"WARNING"}, "B-MANGA 管理レイヤー Object を選択してください")
            return {"CANCELLED"}
        self.bmanga_id = on.get_bmanga_id(obj)
        self.kind = on.get_kind(obj)
        if not self.bmanga_id or not self.kind:
            self.report({"WARNING"}, "選択 Object に B-MANGA ID / kind がありません")
            return {"CANCELLED"}
        from ..utils import detail_popup

        _prepare_detail_profile_nodes(context, self.kind, self.bmanga_id)
        detail_popup.position_dialog_cursor(context, event, key="layer_detail")
        width = _detail_dialog_width_for_kind(context, self.kind, self.bmanga_id)
        return context.window_manager.invoke_props_dialog(self, width=width)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = on.find_object_by_bmanga_id(self.bmanga_id, kind=self.kind)
        if obj is None:
            layout.label(text="対応する Object が見つかりません", icon="ERROR")
            return
        _draw_object_meta(layout, obj)
        layout.separator()

        kind = self.kind
        entry = None
        page = None
        if kind == "image":
            entry = _find_image_entry(scene, self.bmanga_id)
        elif kind == "image_path":
            entry = _find_image_path_entry(scene, self.bmanga_id)
        elif kind == "raster":
            entry = _find_raster_entry(scene, self.bmanga_id)
        elif kind == "balloon":
            page, entry = _find_balloon_entry(scene, self.bmanga_id)
        elif kind == "text":
            page, entry = _find_text_entry(scene, self.bmanga_id)
        elif kind == "gp":
            _draw_gp_detail(layout, obj)
            return
        elif kind in {"effect", "effect_legacy"}:
            active_layer = _active_gp_layer(obj)
            target_key = _effect_detail_target_key(obj, active_layer)
            loaded_key = str(getattr(self, "_effect_detail_loaded_key", "") or "")
            _draw_effect_detail(
                layout,
                context,
                obj,
                load_from_layer=bool(target_key and target_key != loaded_key),
            )
            if target_key:
                self._effect_detail_loaded_key = target_key
            return
        else:
            layout.label(text=f"kind={kind} の詳細表示は未対応", icon="INFO")
            return

        if entry is None:
            layout.label(text="対応 entry が見つかりません", icon="ERROR")
            return

        if kind == "image":
            _draw_image_detail(layout, entry)
        elif kind == "image_path":
            _draw_image_path_detail(layout, context, entry)
        elif kind == "raster":
            _draw_raster_detail(layout, entry)
        elif kind == "balloon":
            _draw_balloon_detail(layout, context, entry, page)
        elif kind == "text":
            _draw_text_detail(layout, context, entry)

    def check(self, context):
        profile_changed = _sync_detail_profile_curve(context, self.kind, self.bmanga_id)
        if profile_changed and self.kind in {"effect", "effect_legacy"}:
            obj = on.find_object_by_bmanga_id(self.bmanga_id, kind=self.kind)
            active_layer = _active_gp_layer(obj)
            _mark_effect_detail_target(context, obj, active_layer)
            _apply_effect_detail_params_to_layer(context, obj, active_layer)
        try:
            for area in context.screen.areas if context.screen else ():
                if area.type in {"VIEW_3D", "PROPERTIES", "OUTLINER"}:
                    area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
        return True

    def execute(self, context):
        _sync_detail_profile_curve(context, self.kind, self.bmanga_id)
        if self.kind in {"effect", "effect_legacy"}:
            obj = on.find_object_by_bmanga_id(self.bmanga_id, kind=self.kind)
            active_layer = _active_gp_layer(obj)
            _mark_effect_detail_target(context, obj, active_layer)
            _apply_effect_detail_params_to_layer(context, obj, active_layer)
        try:
            for area in context.screen.areas if context.screen else ():
                if area.type in {"VIEW_3D", "PROPERTIES", "OUTLINER"}:
                    area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_layer_detail_open,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
