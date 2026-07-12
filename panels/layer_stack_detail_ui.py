"""統合レイヤーリストの選択詳細 UI."""

from __future__ import annotations

import re

import bpy

from ..core import balloon as balloon_core
from ..core.work import get_active_page, get_work
from ..utils import balloon_curve_object
from ..utils import balloon_curve_source_state
from ..utils import balloon_shapes
from ..utils import gpencil as gp_utils
from . import corner_radius_ui, effect_line_panel, line_effect_settings_ui, preset_management_ui


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


def _zero_based_layer_name(prefix: str, value: str, width: int) -> str:
    text = str(value or "")
    match = re.search(r"(\d+)(?!.*\d)", text)
    if match is None:
        return f"{prefix}{text}" if text else f"{prefix}{0:0{width}d}"
    number = max(0, int(match.group(1)) - 1)
    return f"{prefix}{number:0{width}d}"


def page_layer_name(target, work=None) -> str:
    if work is not None:
        target_id = str(getattr(target, "id", "") or "")
        for index, page in enumerate(getattr(work, "pages", []) or []):
            if page == target or (
                target_id and str(getattr(page, "id", "") or "") == target_id
            ):
                info = getattr(work, "work_info", None)
                try:
                    start = int(getattr(info, "page_number_start", 1) or 1)
                except Exception:  # noqa: BLE001
                    start = 1
                return f"ページ{start + index:03d}"
    target_id = str(getattr(target, "id", "") or "")
    m = re.search(r"(\d+)", target_id)
    if m:
        return f"ページ{int(m.group(1)):03d}"
    return target_id or "ページ000"


def coma_layer_name(target) -> str:
    stem = str(getattr(target, "coma_id", "") or getattr(target, "id", "") or "")
    m = re.search(r"(\d+)", stem)
    if m:
        return f"コマ{int(m.group(1)):02d}"
    return stem or "コマ00"


def _draw_gp_selected_settings(box, obj, active_layer) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {active_layer.name}")
    settings.prop(active_layer, "name", text="名前")
    if _has_safe_gp_layer_prop(active_layer, "hide"):
        settings.prop(active_layer, "hide", text="非表示")
    if _has_safe_gp_layer_prop(active_layer, "opacity"):
        settings.prop(active_layer, "opacity", text="不透明度", slider=True)

    mat = None
    try:
        mat = gp_utils.ensure_layer_material(
            obj,
            active_layer,
            activate=True,
            assign_existing=True,
        )
    except Exception:  # noqa: BLE001
        mat = None
    gp_style = getattr(mat, "grease_pencil", None) if mat is not None else None
    if gp_style is not None:
        settings.prop(gp_style, "color", text="ストローク色")
        if hasattr(gp_style, "fill_color"):
            settings.prop(gp_style, "fill_color", text="塗り色")
        flag_row = settings.row(align=True)
        flag_row.prop(gp_style, "show_stroke", text="線を描く")
        if hasattr(gp_style, "show_fill"):
            flag_row.prop(gp_style, "show_fill", text="塗りを描く")
    else:
        settings.label(text="(レイヤー色を取得できません)", icon="ERROR")
    if _has_safe_gp_layer_prop(active_layer, "blend_mode"):
        settings.prop(active_layer, "blend_mode", text="ブレンド")
    if _has_safe_gp_layer_prop(active_layer, "tint_color"):
        settings.prop(active_layer, "tint_color", text="色合い")


def _draw_image_selected_settings(box, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.title} (画像)")
    settings.prop(entry, "title", text="名前")
    settings.prop(entry, "visible", text="表示")
    settings.prop(entry, "opacity", text="不透明度", slider=True)
    settings.prop(entry, "filepath")

    row = settings.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = settings.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    row = settings.row(align=True)
    row.prop(entry, "rotation_deg")
    row.prop(entry, "flip_x", toggle=True)
    row.prop(entry, "flip_y", toggle=True)

    settings.prop(entry, "blend_mode")
    settings.prop(entry, "tint_color")
    settings.prop(entry, "brightness")
    settings.prop(entry, "contrast")
    settings.prop(entry, "binarize_enabled")
    sub = settings.row()
    sub.enabled = entry.binarize_enabled
    sub.prop(entry, "binarize_threshold")


def _draw_image_path_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.title or entry.id} (パターンカーブ)", icon="CURVE_BEZCURVE")
    settings.prop(entry, "title", text="名前")
    settings.prop(entry, "visible", text="表示")
    settings.prop(entry, "locked", text="ロック")
    settings.prop(entry, "opacity", text="不透明度", slider=True)
    settings.prop(entry, "content_source", text="内容")
    source = str(getattr(entry, "content_source", "image") or "image")
    if source == "shape":
        row = settings.row(align=True)
        row.prop(entry, "shape_kind", text="生成形状")
        if str(getattr(entry, "shape_kind", "") or "") == "polygon":
            row.prop(entry, "shape_sides", text="角数")
    else:
        settings.prop(entry, "filepath", text="画像")
        settings.prop(entry, "draw_mode", text="表示方法")

    row = settings.row(align=True)
    row.prop(entry, "brush_size_mm", text="ブラシサイズ")
    row.prop(entry, "aspect_ratio", text="縦横比")
    row = settings.row(align=True)
    row.prop(entry, "image_angle_deg", text="角度")
    row.prop(entry, "spacing_percent", text="間隔")
    settings.prop(entry, "color", text="色")

    mode = str(getattr(entry, "draw_mode", "stamp") or "stamp")
    if source == "image" and mode == "stamp":
        settings.prop(entry, "stamp_angle_mode", text="角度")
        if str(getattr(entry, "stamp_angle_mode", "") or "") == "object":
            settings.prop_search(
                entry,
                "stamp_angle_object_name",
                bpy.data,
                "objects",
                text="方向オブジェクト",
            )
    elif source == "image":
        settings.prop(entry, "ribbon_repeat_mode", text="リボン")

    inout_box = settings.box()
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
    effect_line_panel.draw_inout_curve_mapping(inout_box, entry)

    preset_management_ui.draw_image_path_preset_management(box, context)


def _draw_raster_selected_settings(box, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.title or entry.id} (ラスター)", icon="BRUSH_DATA")
    settings.prop(entry, "title", text="名前")
    settings.prop(entry, "visible", text="表示")
    settings.prop(entry, "locked", text="ロック")
    settings.prop(entry, "opacity", text="不透明度", slider=True)
    settings.label(text=f"DPI: {int(getattr(entry, 'dpi', 0))}")
    settings.operator("bmanga.raster_layer_resample", text="リサンプル...", icon="IMAGE_DATA")

    bit_box = box.box()
    bit_box.label(text=f"階調: {getattr(entry, 'bit_depth', 'gray8')}")
    row = bit_box.row(align=True)
    op = row.operator("bmanga.raster_layer_set_bit_depth", text="グレー 8bit")
    op.bit_depth = "gray8"
    op = row.operator("bmanga.raster_layer_set_bit_depth", text="1bit")
    op.bit_depth = "gray1"

    settings.prop(entry, "line_color", text="線色")
    settings.label(text=f"所属: {entry.scope or 'page'}")
    settings.label(text=f"親: {entry.parent_kind or 'none'} / {entry.parent_key or '-'}")
    row = settings.row(align=True)
    op = row.operator("bmanga.raster_layer_paint_enter", text="Texture Paint へ入る", icon="TPAINT_HLT")
    op.raster_id = entry.id
    op = row.operator("bmanga.raster_layer_save_png", text="", icon="FILE_TICK")
    op.raster_id = entry.id
    op.force = True


def _draw_fill_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    fill_type = str(getattr(entry, "fill_type", "solid") or "solid")
    type_label = "グラデーション" if fill_type == "gradient" else "ベタ塗り"
    settings.label(text=f"選択中: {entry.title or entry.id} ({type_label})", icon="NODE_TEXTURE")
    preset_management_ui.draw_fill_preset_selection(box, context, gradient=fill_type == "gradient")
    settings.prop(entry, "title", text="名前")
    settings.prop(entry, "visible", text="表示")
    settings.prop(entry, "locked", text="ロック")
    settings.prop(entry, "opacity", text="不透明度", slider=True)
    settings.prop(entry, "fill_type", text="タイプ")
    settings.prop(entry, "color", text="色")
    if fill_type == "gradient":
        settings.prop(entry, "color2", text="色2")
        settings.prop(entry, "gradient_type", text="形状")
        grad_type = str(getattr(entry, "gradient_type", "linear") or "linear")
        if grad_type == "linear":
            settings.prop(entry, "gradient_angle", text="角度")
        from ..utils.fill_real_object import get_gradient_curve_node
        curve_node = get_gradient_curve_node(str(getattr(entry, "id", "") or ""))
        if curve_node is not None:
            curve_box = settings.box()
            curve_box.label(text="濃度カーブ", icon="CURVE_DATA")
            curve_box.template_curve_mapping(curve_node, "mapping", type="NONE")
        if getattr(entry, "use_gradient_endpoints", False):
            ep_box = settings.box()
            ep_box.label(text="グラデーション範囲", icon="ARROW_LEFTRIGHT")
            row = ep_box.row(align=True)
            row.prop(entry, "gradient_start_x_mm", text="開始X")
            row.prop(entry, "gradient_start_y_mm", text="Y")
            row = ep_box.row(align=True)
            row.prop(entry, "gradient_end_x_mm", text="終了X")
            row.prop(entry, "gradient_end_y_mm", text="Y")
    if getattr(entry, "use_region", False):
        reg_box = settings.box()
        reg_box.label(text="塗り範囲", icon="SELECT_SET")
        row = reg_box.row(align=True)
        row.prop(entry, "region_x_mm", text="X")
        row.prop(entry, "region_y_mm", text="Y")
        row = reg_box.row(align=True)
        row.prop(entry, "region_width_mm", text="幅")
        row.prop(entry, "region_height_mm", text="高さ")


def _draw_balloon_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {getattr(entry, 'title', '') or entry.id} (フキダシ)")
    preset_management_ui.draw_balloon_preset_management(box, context)
    settings.prop(entry, "title", text="名前")
    source_state = _balloon_source_state(entry)
    settings.label(text=f"編集状態: {_balloon_source_state_label(source_state)}")
    page = _page_for_balloon_entry(context, entry)
    row = settings.row(align=True)
    op = row.operator("bmanga.balloon_regenerate_keep_edit", text="手編集を維持して再生成", icon="MOD_CURVE")
    op.page_id = str(getattr(page, "id", "") or "")
    op.balloon_id = str(getattr(entry, "id", "") or "")
    op = row.operator("bmanga.balloon_regenerate_discard_edit", text="手編集を破棄して再生成", icon="TRASH")
    op.page_id = str(getattr(page, "id", "") or "")
    op.balloon_id = str(getattr(entry, "id", "") or "")
    settings.prop(entry, "shape")
    if balloon_shapes.normalize_shape(entry.shape) == "custom":
        settings.prop(entry, "custom_preset_name")
    row = settings.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = settings.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    settings.prop(entry, "rotation_deg")
    row = settings.row(align=True)
    row.prop(entry, "flip_h", toggle=True)
    row.prop(entry, "flip_v", toggle=True)
    settings.prop(entry, "opacity", slider=True)
    if getattr(entry, "merge_group_id", ""):
        settings.label(text=f"結合: {entry.merge_group_id}", icon="FILE_FOLDER")
    page = get_active_page(context)
    if page is not None and sum(1 for b in page.balloons if getattr(b, "selected", False)) >= 2:
        settings.operator("bmanga.balloon_merge_selected", text="フキダシを結合", icon="FILE_FOLDER")
    if balloon_shapes.normalize_shape(entry.shape) == "rect":
        balloon_core.ensure_balloon_corner_type_initialized(entry)
        settings.prop(entry, "corner_type")
        sub = settings.column(align=True)
        sub.enabled = str(getattr(entry, "corner_type", "square") or "square") != "square"
        corner_radius_ui.draw_corner_radius(sub, entry)

    line_box = box.box()
    line_box.label(text="線・塗り")
    row = line_box.row(align=True)
    row.prop(entry, "line_style")
    line_style = balloon_shapes.normalize_line_style(str(getattr(entry, "line_style", "") or ""))
    if line_style != "uni_flash":
        row.prop(entry, "line_width_mm")
    if line_style == "dashed":
        row = line_box.row(align=True)
        row.prop(entry, "dashed_segment_length_mm", text="線分")
        row.prop(entry, "dashed_gap_mm", text="間隔")
    elif line_style == "dotted":
        row = line_box.row(align=True)
        row.prop(entry, "dotted_gap_mm", text="間隔")
    shape_norm_for_line = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
    if line_style == "uni_flash":
        effect_line_panel.draw_effect_params(
            line_box,
            entry,
            with_generate_button=False,
            fixed_effect_type="uni_flash",
            show_type=False,
            show_path_settings=False,
        )
    elif balloon_shapes.is_flash_line_style(line_style):
        if line_style != "white_outline":
            row = line_box.row(align=True)
            row.prop(entry, "flash_line_count", text="線の本数")
            row.prop(entry, "flash_line_spacing_mm", text="線の間隔")
            row = line_box.row(align=True)
            row.prop(entry, "line_valley_width_pct", text="入り・抜き")
            row.prop(entry, "line_peak_width_pct", text="中間線幅")
        if line_style == "white_outline":
            line_effect_settings_ui.draw_balloon_white_outline_settings(
                line_box,
                entry,
                draw_inout_curve=effect_line_panel.draw_inout_curve_mapping,
            )
    elif balloon_shapes.is_dynamic_meldex_shape(shape_norm_for_line):
        row = line_box.row(align=True)
        row.prop(entry, "line_valley_width_pct")
        row.prop(entry, "line_peak_width_pct")
    if line_style == "double":
        row = line_box.row(align=True)
        row.prop(entry, "multi_line_count")
        row.prop(entry, "multi_line_direction")
        row = line_box.row(align=True)
        row.prop(entry, "multi_line_width_mm")
        row.prop(entry, "multi_line_spacing_mm")
        row = line_box.row(align=True)
        row.prop(entry, "multi_line_width_scale_percent")
        row.prop(entry, "multi_line_spacing_scale_percent")
        shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
        if balloon_shapes.is_dynamic_meldex_shape(shape_norm):
            row = line_box.row(align=True)
            row.prop(entry, "thorn_multi_line_length_scale_near_percent")
            row.prop(entry, "thorn_multi_line_length_scale_far_percent")
            row = line_box.row(align=True)
            row.prop(entry, "thorn_multi_line_cross_enabled", toggle=True)
            row = line_box.row(align=True)
            row.prop(entry, "thorn_multi_line_valley_width_pct")
            row.prop(entry, "thorn_multi_line_peak_width_pct")
    # 白抜き線は線群そのものが本体で塗りを持たないため、塗り関連は表示しない
    has_body_fill = line_style not in {"uni_flash", "white_outline"}
    if line_style != "uni_flash":
        row = line_box.row(align=True)
        row.prop(entry, "line_color")
        if has_body_fill:
            row.prop(entry, "fill_color")
            line_box.prop(entry, "fill_opacity", slider=True)
    if line_style != "uni_flash":
        if has_body_fill:
            line_box.prop_search(entry, "fill_material_name", bpy.data, "materials")
            row = line_box.row(align=True)
            row.prop(entry, "fill_blur_amount", slider=True)
            row.prop(entry, "fill_blur_axis", text="")
            row.prop(entry, "fill_blur_dither", toggle=True)
            line_box.prop(entry, "fill_gradient_enabled")
            sub = line_box.column(align=True)
            sub.enabled = bool(getattr(entry, "fill_gradient_enabled", False))
            row = sub.row(align=True)
            row.prop(entry, "fill_gradient_start_color")
            row.prop(entry, "fill_gradient_end_color")
            sub.prop(entry, "fill_gradient_angle_deg")
        if has_body_fill:
            # フチは白抜き線では画面にも出力にも描かれないため表示しない
            row = line_box.row(align=True)
            row.prop(entry, "outer_white_margin_enabled", text="外側フチ", toggle=True)
            sub = row.row(align=True)
            sub.enabled = bool(getattr(entry, "outer_white_margin_enabled", False))
            sub.prop(entry, "outer_white_margin_width_mm", text="幅")
            sub.prop(entry, "outer_white_margin_color", text="")
            row = line_box.row(align=True)
            row.prop(entry, "inner_white_margin_enabled", text="内側フチ", toggle=True)
            sub = row.row(align=True)
            sub.enabled = bool(getattr(entry, "inner_white_margin_enabled", False))
            sub.prop(entry, "inner_white_margin_width_mm", text="幅")
            sub.prop(entry, "inner_white_margin_color", text="")

    sp = entry.shape_params
    if (
        source_state != balloon_curve_source_state.STATE_FREEFORM
        and balloon_shapes.is_dynamic_meldex_shape(entry.shape)
    ):
        shape_box = box.box()
        shape_box.label(text="形状パラメータ")
        row = shape_box.row(align=True)
        row.prop(sp, "dynamic_shape_base_kind", text="ベース")
        row = shape_box.row(align=True)
        row.prop(sp, "cloud_bump_width_mm")
        row.prop(sp, "cloud_bump_width_jitter", text="乱れ")
        row = shape_box.row(align=True)
        row.prop(sp, "cloud_bump_height_mm")
        row.prop(sp, "cloud_bump_height_jitter", text="乱れ")
        row = shape_box.row(align=True)
        row.prop(sp, "cloud_offset_percent")
        row.prop(sp, "shape_seed")
        row = shape_box.row(align=True)
        row.prop(sp, "cloud_sub_width_ratio")
        row.prop(sp, "cloud_sub_width_jitter", text="乱れ")
        row = shape_box.row(align=True)
        row.prop(sp, "cloud_sub_height_ratio")
        row.prop(sp, "cloud_sub_height_jitter", text="乱れ")
        # 雲・もやもやでは効かない確定仕様のためトゲ系でだけ表示する
        if balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or "")) in {"thorn", "thorn-curve"}:
            shape_box.prop(sp, "cloud_valley_sharp")

    move_box = box.box()
    move_box.label(text="親子連動移動", icon="CON_TRACKTO")
    row = move_box.row(align=True)
    op = row.operator("bmanga.balloon_move", text="← 5mm")
    op.delta_x_mm = -5.0
    op = row.operator("bmanga.balloon_move", text="→ 5mm")
    op.delta_x_mm = 5.0
    op = row.operator("bmanga.balloon_move", text="↑ 5mm")
    op.delta_y_mm = 5.0
    op = row.operator("bmanga.balloon_move", text="↓ 5mm")
    op.delta_y_mm = -5.0

    tail_box = box.box()
    row = tail_box.row(align=True)
    row.label(text=f"尻尾 ({len(entry.tails)})")
    page = _page_for_balloon_entry(context, entry)
    op = row.operator("bmanga.balloon_tail_add_target", text="", icon="ADD")
    op.page_id = str(getattr(page, "id", "") or "")
    op.balloon_id = str(getattr(entry, "id", "") or "")
    for i, tail in enumerate(entry.tails):
        sub = tail_box.box()
        header = sub.row(align=True)
        header.label(text=f"尻尾 {i + 1}")
        op = header.operator("bmanga.balloon_tail_remove", text="", icon="X")
        op.page_id = str(getattr(page, "id", "") or "")
        op.balloon_id = str(getattr(entry, "id", "") or "")
        op.tail_index = i
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


def _balloon_source_state(entry) -> str:
    try:
        return balloon_curve_object.source_state_for_entry(entry)
    except Exception:  # noqa: BLE001
        return balloon_curve_source_state.STATE_GENERATED


def _balloon_source_state_label(state: str) -> str:
    if state == balloon_curve_source_state.STATE_MANUAL:
        return "手編集あり"
    if state == balloon_curve_source_state.STATE_FREEFORM:
        return "自由形状"
    return "生成形状"


def _page_for_balloon_entry(context, entry):
    work = get_work(context)
    if work is None or entry is None:
        return None
    entry_id = str(getattr(entry, "id", "") or "")
    try:
        entry_ptr = int(entry.as_pointer())
    except Exception:  # noqa: BLE001
        entry_ptr = 0
    for page in getattr(work, "pages", []):
        for candidate in getattr(page, "balloons", []):
            try:
                same_ptr = bool(entry_ptr) and int(candidate.as_pointer()) == entry_ptr
            except Exception:  # noqa: BLE001
                same_ptr = False
            if same_ptr or (entry_id and str(getattr(candidate, "id", "") or "") == entry_id):
                return page
    return None


def _draw_text_selected_settings(box, context, entry) -> None:
    page = get_active_page(context)
    settings = box.column(align=True)
    settings.label(text=f"選択中: {getattr(entry, 'title', '') or entry.id} (テキスト)")
    preset_management_ui.draw_text_preset_selection(box, context)
    settings.prop(entry, "title", text="名前")
    row = settings.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = settings.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")

    type_box = box.box()
    type_box.label(text="組版", icon="FONT_DATA")
    type_box.prop(entry, "writing_mode")
    type_box.prop(entry, "font", text="基本フォント")
    row = type_box.row(align=True)
    row.prop(entry, "font_size_unit", text="")
    row.prop(entry, "font_size_value", text="サイズ")
    row = type_box.row(align=True)
    row.prop(entry, "font_bold", toggle=True)
    row.prop(entry, "font_italic", toggle=True)
    type_box.prop(entry, "color")
    row = type_box.row(align=True)
    row.prop(entry, "line_height")
    row.prop(entry, "letter_spacing")

    ruby_box = box.box()
    ruby_box.label(text="ルビ", icon="FONT_DATA")
    ruby_box.label(text=f"{len(getattr(entry, 'ruby_spans', ()) or ())} 件")
    row = ruby_box.row(align=True)
    row.prop(entry, "ruby_size_percent")
    row.prop(entry, "ruby_gap_mm")
    row = ruby_box.row(align=True)
    row.prop(entry, "ruby_letter_spacing")
    row.prop(entry, "ruby_line_height")
    row = ruby_box.row(align=True)
    row.prop(entry, "ruby_align", text="")
    row.prop(entry, "ruby_small_kana", text="")
    ruby_box.prop(entry, "ruby_font")
    row = ruby_box.row(align=True)
    row.operator("bmanga.text_ruby_add_dialog", text="ルビを付ける", icon="ADD")
    row.operator("bmanga.text_ruby_clear", text="すべて削除", icon="TRASH")

    stroke_box = box.box()
    stroke_box.prop(entry, "stroke_enabled")
    sub = stroke_box.column()
    sub.enabled = entry.stroke_enabled
    sub.prop(entry, "stroke_width_mm")
    sub.prop(entry, "stroke_color")

    parent_box = box.box()
    parent_box.label(text="親フキダシ", icon="LINKED")
    parent_box.prop(entry, "parent_balloon_id", text="ID")
    parent_box.operator("bmanga.text_meta_dialog", text="メタ情報を編集", icon="INFO")
    if page is not None and len(page.balloons) > 0:
        row = parent_box.row(align=True)
        row.label(text="紐付け:")
        for balloon in page.balloons:
            op = row.operator("bmanga.text_attach_to_balloon", text=balloon.id)
            op.balloon_id = balloon.id
        op = parent_box.operator(
            "bmanga.text_attach_to_balloon",
            text="独立テキストにする",
            icon="UNLINKED",
        )
        op.balloon_id = ""


def _draw_effect_type_settings(box, params) -> None:
    param_box = box.box()
    param_box.label(text="種類", icon="STROKE")
    param_box.prop(params, "effect_type")
    if params.effect_type != "speed":
        param_box.prop(params, "rotation_deg")


def _draw_effect_shape_settings(box, params, prefix: str, label: str, *, frame_toggle: bool = False) -> None:
    shape_box = box.box()
    shape_box.label(text=label)
    if frame_toggle:
        shape_box.prop(params, "start_to_coma_frame")
    content = shape_box.column(align=True)
    if frame_toggle:
        content.enabled = not bool(params.start_to_coma_frame)
    shape_attr = f"{prefix}_shape"
    content.prop(params, shape_attr)
    shape = balloon_shapes.normalize_shape(getattr(params, shape_attr))
    if shape == "rect":
        corner_attr = f"{prefix}_corner_type"
        content.prop(params, corner_attr)
        sub = content.column(align=True)
        sub.enabled = str(getattr(params, corner_attr, "square") or "square") != "square"
        corner_radius_ui.draw_corner_radius(sub, params, prefix=f"{prefix}_rounded_corner")
    if balloon_shapes.is_dynamic_meldex_shape(shape):
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_bump_width_mm")
        row.prop(params, f"{prefix}_cloud_bump_width_jitter", text="乱れ")
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_bump_height_mm")
        row.prop(params, f"{prefix}_cloud_bump_height_jitter", text="乱れ")
        content.prop(params, f"{prefix}_cloud_offset_percent")
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_sub_width_ratio")
        row.prop(params, f"{prefix}_cloud_sub_width_jitter", text="乱れ")
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_sub_height_ratio")
        row.prop(params, f"{prefix}_cloud_sub_height_jitter", text="乱れ")


def _draw_effect_line_settings(box, params) -> None:
    line_box = box.box()
    line_box.label(text="線")
    line_box.prop(params, "brush_size_mm")
    row = line_box.row(align=True)
    row.prop(params, "brush_jitter_enabled", text="乱れ")
    sub = row.row()
    sub.enabled = params.brush_jitter_enabled
    sub.prop(params, "brush_jitter_amount", text="")
    row = line_box.row(align=True)
    row.prop(params, "length_jitter_enabled", text="外端乱れ")
    sub = row.row()
    sub.enabled = params.length_jitter_enabled
    sub.prop(params, "length_jitter_amount", text="")
    row = line_box.row(align=True)
    row.prop(params, "end_length_jitter_enabled", text="内端乱れ")
    sub = row.row()
    sub.enabled = params.end_length_jitter_enabled
    sub.prop(params, "end_length_jitter_amount", text="")


def _draw_effect_interval_settings(box, params) -> None:
    interval_box = box.box()
    interval_box.label(text="描画間隔")
    interval_box.prop(params, "spacing_mode")
    if params.spacing_mode == "angle":
        interval_box.prop(params, "spacing_angle_deg")
    else:
        interval_box.prop(params, "spacing_distance_mm")
    row = interval_box.row(align=True)
    row.prop(params, "spacing_jitter_enabled", text="乱れ")
    sub = row.row()
    sub.enabled = params.spacing_jitter_enabled
    sub.prop(params, "spacing_jitter_amount", text="")
    interval_box.prop(params, "bundle_enabled")
    sub = interval_box.column(align=True)
    sub.enabled = params.bundle_enabled
    row = sub.row(align=True)
    row.prop(params, "bundle_line_count")
    row.prop(params, "bundle_line_count_jitter", text="乱れ")
    row = sub.row(align=True)
    row.prop(params, "bundle_gap_mm")
    row.prop(params, "bundle_gap_jitter_amount", text="乱れ")
    row = sub.row(align=True)
    row.prop(params, "bundle_jagged_enabled")
    jag = row.row()
    jag.enabled = params.bundle_jagged_enabled
    jag.prop(params, "bundle_jagged_height_percent", text="高さ")
    interval_box.prop(params, "max_line_count")


def _draw_effect_tail_settings(box, params) -> None:
    if params.effect_type == "speed":
        speed_box = box.box()
        speed_box.label(text="流線")
        speed_box.prop(params, "speed_angle_deg")
        speed_box.prop(params, "speed_line_count")

    inout_box = box.box()
    inout_box.label(text="入り抜き")
    line_effect_settings_ui.draw_inout_apply_toggles(inout_box, params)
    row = inout_box.row(align=True)
    row.prop(params, "in_percent")
    row.prop(params, "out_percent")
    effect_line_panel.draw_inout_curve_mapping(inout_box, params)

    color_box = box.box()
    color_box.label(text="色")
    color_box.prop(params, "line_color")
    if params.effect_type not in {"speed", "white_outline"}:
        color_box.prop(params, "fill_color")
        color_box.prop(params, "fill_opacity")
        color_box.prop(params, "fill_base_shape")
    if params.effect_type in {"focus", "uni_flash"}:
        row = color_box.row(align=True)
        row.prop(params, "white_underlay_enabled", toggle=True)
        sub = row.row(align=True)
        sub.enabled = bool(params.white_underlay_enabled)
        sub.prop(params, "white_underlay_width_percent", text="幅")
        sub.prop(params, "white_underlay_color", text="")
    if params.effect_type == "uni_flash":
        color_box.prop(params, "uni_flash_offset_percent")


def _draw_effect_white_outline_settings(box, params) -> None:
    line_effect_settings_ui.draw_effect_white_outline_settings(
        box,
        params,
        show_opacity=False,
        draw_inout_curve=effect_line_panel.draw_inout_curve_mapping,
    )


def _draw_effect_selected_settings(box, context, obj, active_layer, *, wide: bool = False) -> None:
    settings = box.column(align=True)
    name = getattr(active_layer, "name", "効果線")
    settings.label(text=f"選択中: {name} (効果線)")
    if active_layer is not None and hasattr(active_layer, "name"):
        settings.prop(active_layer, "name", text="名前")
    params = getattr(context.scene, "bmanga_effect_line_params", None)
    if params is None:
        settings.label(text="効果線パラメータが未初期化です", icon="ERROR")
        return
    settings.prop(params, "opacity", text="不透明度", slider=True)
    if active_layer is not None and hasattr(active_layer, "hide"):
        settings.prop(active_layer, "hide", text="非表示")
    if wide:
        grid = box.grid_flow(
            row_major=True,
            columns=2,
            even_columns=True,
            even_rows=False,
            align=True,
        )
        cols = tuple(grid.column(align=True) for _ in range(2))
        effect_line_panel.draw_effect_line_preset_management(cols[0], context)
        effect_line_panel.draw_effect_params(cols[0], params, with_generate_button=True, columns=cols)
    else:
        effect_line_panel.draw_effect_line_preset_management(box, context)
        _draw_effect_type_settings(box, params)
        if params.effect_type == "white_outline":
            _draw_effect_shape_settings(box, params, "start", "外端形状", frame_toggle=True)
            _draw_effect_shape_settings(box, params, "end", "内端形状")
            _draw_effect_white_outline_settings(box, params)
            effect_line_panel.draw_effect_path_settings(box, params)
            box.operator("bmanga.effect_line_generate", text="効果線を追加", icon="STROKE")
            return
        if params.effect_type != "speed":
            _draw_effect_shape_settings(box, params, "start", "外端形状", frame_toggle=True)
            _draw_effect_shape_settings(box, params, "end", "内端形状")
        _draw_effect_line_settings(box, params)
        if params.effect_type != "beta_flash":
            _draw_effect_interval_settings(box, params)
        _draw_effect_tail_settings(box, params)
        effect_line_panel.draw_effect_path_settings(box, params)
        box.operator("bmanga.effect_line_generate", text="効果線を追加", icon="STROKE")


def _draw_layer_folder_selected_settings(box, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {getattr(entry, 'title', '') or getattr(entry, 'id', '')} (汎用フォルダ)", icon="FILE_FOLDER")
    settings.prop(entry, "title", text="名前")
    settings.prop(entry, "expanded", text="展開")
    settings.label(text=f"親: {getattr(entry, 'parent_key', '') or 'ページ外'}")


def _draw_page_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(
        text=f"選択中: {page_layer_name(entry, get_work(context))} (ページ)",
        icon="FILE_BLANK",
    )
    settings.prop(entry, "coma_count", text="コマ数")
    if hasattr(entry, "visible"):
        settings.prop(entry, "visible", text="表示")
    row = settings.row(align=True)
    row.prop(entry, "offset_x_mm", text="表示X")
    row.prop(entry, "offset_y_mm", text="表示Y")


def _draw_coma_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {coma_layer_name(entry)} (コマ)", icon="MOD_WIREFRAME")

    blend_box = box.box()
    blend_box.label(text="コマ用blendファイル (このコマのみ)", icon="FILE_BLEND")
    blend_box.prop(entry, "coma_blend_template_path", text="")

    # INVOKE だとマウス直下のコマ逆引きに失敗してボタンが無反応になるため、
    # 選択中コマを対象に execute する EXEC_DEFAULT で呼ぶ (専用 row でスコープ)。
    enter_row = box.row(align=True)
    enter_row.operator_context = "EXEC_DEFAULT"
    enter_row.operator("bmanga.enter_coma_mode", text="コマ編集へ", icon="PLAY")

    from . import coma_detail_panel

    shape_box = box.box()
    shape_box.label(text="形状")
    coma_detail_panel.draw_coma_shape_settings(shape_box, context, entry)

    border_box = box.box()
    border_box.label(text="枠線")
    coma_detail_panel.draw_coma_border_settings(border_box, context, entry)

    white_box = box.box()
    white_box.label(text="フチ")
    coma_detail_panel.draw_coma_white_margin_settings(white_box, entry)


def draw_stack_item_detail(layout, context, item, resolved, *, wide: bool = False) -> bool:
    if resolved is None or resolved.get("target") is None:
        return False
    box = layout.box()
    kind = item.kind
    target = resolved["target"]
    obj = resolved.get("object")
    if kind == "page":
        _draw_page_selected_settings(box, context, target)
    elif kind == "coma":
        _draw_coma_selected_settings(box, context, target)
    elif kind == "gp":
        _draw_gp_selected_settings(box, obj, target)
    elif kind == "image":
        _draw_image_selected_settings(box, target)
    elif kind == "image_path":
        _draw_image_path_selected_settings(box, context, target)
    elif kind == "raster":
        _draw_raster_selected_settings(box, target)
    elif kind == "fill":
        _draw_fill_selected_settings(box, context, target)
    elif kind == "balloon":
        if wide:
            # レイヤーリストから開くダイアログも、右クリックの詳細設定と同じ
            # 横長 4 列レイアウトに揃える (縦長・横長の不一致をなくす)
            from ..operators import layer_detail_op as _ldo

            box.label(text=f"選択中: {getattr(target, 'title', '') or target.id} (フキダシ)")
            _ldo._draw_balloon_detail(box, context, target, _page_for_balloon_entry(context, target))
        else:
            _draw_balloon_selected_settings(box, context, target)
    elif kind == "text":
        _draw_text_selected_settings(box, context, target)
    elif kind == "effect":
        _draw_effect_selected_settings(box, context, obj, target, wide=wide)
    elif kind == "layer_folder":
        _draw_layer_folder_selected_settings(box, target)
    elif kind == "gp_folder":
        box.label(text=f"選択中: {target.name} (フォルダ)", icon="FILE_FOLDER")
        box.prop(target, "name", text="名前")
        if hasattr(target, "hide"):
            box.prop(target, "hide", text="非表示")
        if hasattr(target, "lock"):
            box.prop(target, "lock", text="ロック")
    _draw_linked_layers_box(box, context, item)
    return True


def _draw_linked_layers_box(box, context, item) -> None:
    """「リンク中のレイヤー」box (相手が無ければ何も描画しない).

    リンク相手の定義はレイヤー一覧のマーク表示 (gpencil_panel の
    _link_state_icon) と同じ (utils.layer_links.related_uids_for_item)。
    """
    from ..utils import layer_display, layer_links

    partner_uids = layer_links.related_uids_for_item(context, item)
    layer_display.draw_linked_layers_box(box, context, partner_uids)
