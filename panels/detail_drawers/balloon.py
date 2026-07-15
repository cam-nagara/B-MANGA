"""フキダシとしっぽの共通詳細描画。"""

from __future__ import annotations

from collections.abc import Mapping

from .basic import (
    body_columns,
    detail_operator,
    prop_if,
    prop_pair,
    set_operator_fields,
    value,
)


def draw_balloon_body(layout, context, session, mode) -> None:
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    kind = session.target.kind
    if kind == "balloon_tail":
        draw_tail_body(layout, context, session, mode)
        return
    if preset_mode and str(getattr(session.target, "namespace", "") or "") == "balloon":
        return

    from ...utils import balloon_shapes

    entry = session.target.data
    columns = body_columns(layout, session)
    shape_column = columns[0]
    line_column = columns[min(1, len(columns) - 1)]
    effect_columns = columns[1:] if len(columns) > 1 else columns

    _draw_placement(shape_column, entry)
    _draw_shape(shape_column, session, entry, balloon_shapes)
    _draw_line(line_column, entry, balloon_shapes, effect_columns, preset_mode)
    _draw_tails(shape_column, context, session, entry, preset_mode)


def _draw_placement(layout, entry) -> None:
    box = layout.box()
    box.label(text="配置 (mm)")
    prop_pair(box, entry, "x_mm", "y_mm")
    prop_pair(box, entry, "width_mm", "height_mm")
    prop_if(box, entry, "rotation_deg", text="回転")
    prop_pair(
        box,
        entry,
        "flip_h",
        "flip_v",
        flip_h={"text": "水平反転", "toggle": True},
        flip_v={"text": "垂直反転", "toggle": True},
    )


def _draw_shape(layout, session, entry, balloon_shapes) -> None:
    box = layout.box()
    box.label(text="形状")
    _draw_regenerate_controls(box, session, entry)
    prop_if(box, entry, "shape", text="形状")
    if str(value(entry, "shape", "") or "") == "custom":
        preset_name = str(value(entry, "custom_preset_name", "") or "")
        box.label(text=f"使用中プリセット: {preset_name or '未指定'}", icon="PRESET")
    shape = balloon_shapes.normalize_shape(str(value(entry, "shape", "") or ""))
    if shape == "rect":
        prop_if(box, entry, "corner_type", text="角")
        radius = box.column(align=True)
        radius.enabled = str(value(entry, "corner_type", "square") or "square") != "square"
        _draw_corner_radius(radius, entry)
    shape_params = value(entry, "shape_params", None)
    if shape_params is not None and balloon_shapes.is_dynamic_meldex_shape(shape):
        _draw_dynamic_shape(layout, shape_params, shape)


def _draw_regenerate_controls(layout, session, entry) -> None:
    if not bool(value(entry, "id", None)):
        return
    layout.label(
        text="形状の再生成はフキダシツール側から実行してください",
        icon="INFO",
    )


def _draw_corner_radius(layout, owner, prefix: str = "rounded_corner") -> None:
    row = layout.row(align=True)
    unit = f"{prefix}_radius_unit"
    suffix = "percent" if str(value(owner, unit, "mm") or "mm") == "percent" else "mm"
    prop_if(row, owner, f"{prefix}_radius_{suffix}", text="角半径")
    prop_if(row, owner, unit, text="")


def _draw_dynamic_shape(layout, params, shape: str) -> None:
    box = layout.box()
    box.label(text="形状パラメータ")
    prop_if(box, params, "dynamic_shape_base_kind", text="ベース")
    if str(value(params, "dynamic_shape_base_kind", "ellipse")) == "rect":
        prop_if(box, params, "dynamic_base_rounded_corner_enabled", text="丸角", toggle=True)
        radius = box.column(align=True)
        radius.enabled = bool(value(params, "dynamic_base_rounded_corner_enabled", False))
        _draw_corner_radius(radius, params, "dynamic_base_rounded_corner")
    _draw_shape_jitter_fields(box, params)
    if shape in {"thorn", "thorn-curve"}:
        prop_if(box, params, "cloud_valley_sharp", text="角を尖らせる")


def _draw_shape_jitter_fields(layout, params) -> None:
    pairs = (
        ("cloud_bump_width_mm", "cloud_bump_width_jitter"),
        ("cloud_bump_height_mm", "cloud_bump_height_jitter"),
        ("cloud_offset_percent", "shape_seed"),
        ("cloud_sub_width_ratio", "cloud_sub_width_jitter"),
        ("cloud_sub_height_ratio", "cloud_sub_height_jitter"),
    )
    for first, second in pairs:
        prop_pair(layout, params, first, second)


def _draw_line(layout, entry, balloon_shapes, effect_columns, preset_mode: bool) -> None:
    box = layout.box()
    box.label(text="線・塗り")
    row = box.row(align=True)
    prop_if(row, entry, "line_style", text="線種")
    line_style = balloon_shapes.normalize_line_style(str(value(entry, "line_style", "") or ""))
    if line_style != "uni_flash":
        prop_if(row, entry, "line_width_mm", text="線幅")
    _draw_line_style_fields(box, entry, balloon_shapes, line_style, effect_columns, preset_mode)
    _draw_line_colors(box, entry, line_style)


def _draw_line_style_fields(layout, entry, balloon_shapes, line_style, columns, preset_mode) -> None:
    if line_style == "dashed":
        prop_pair(layout, entry, "dashed_segment_length_mm", "dashed_gap_mm")
    elif line_style == "dotted":
        prop_if(layout, entry, "dotted_gap_mm", text="間隔")
    elif line_style == "material":
        _draw_material_line(layout, entry)
    elif line_style == "shape":
        _draw_shape_line(layout, entry)
    elif line_style == "image":
        _draw_image_line(layout, entry)

    shape = balloon_shapes.normalize_shape(str(value(entry, "shape", "") or ""))
    if line_style == "uni_flash":
        _draw_uni_flash(layout, entry, columns, preset_mode)
    elif line_style == "white_outline":
        _draw_white_outline(layout, entry, columns)
    elif balloon_shapes.is_flash_line_style(line_style):
        prop_pair(layout, entry, "flash_line_count", "flash_line_spacing_mm")
        prop_pair(layout, entry, "line_valley_width_pct", "line_peak_width_pct")
    elif balloon_shapes.is_dynamic_meldex_shape(shape):
        prop_pair(layout, entry, "line_valley_width_pct", "line_peak_width_pct")
    if line_style == "double":
        _draw_double_line(layout, entry, balloon_shapes, shape)


def _draw_material_line(layout, entry) -> None:
    prop_if(layout, entry, "line_material_name", text="マテリアル")
    prop_if(layout, entry, "line_material_mapping", text="貼り方")
    if str(value(entry, "line_material_mapping", "tile") or "tile") == "ribbon":
        prop_if(layout, entry, "line_material_stretch_single", text="1枚でつなぐ")
        prop_if(layout, entry, "line_material_seam_fix", text="継ぎ目処理")


def _draw_shape_line(layout, entry) -> None:
    prop_pair(layout, entry, "line_shape_kind", "line_shape_spacing_mm")
    prop_pair(layout, entry, "line_shape_angle_deg", "line_shape_jitter")
    prop_if(layout, entry, "line_shape_orient", text="向き", expand=True)


def _draw_image_line(layout, entry) -> None:
    prop_if(layout, entry, "line_image_path", text="画像")
    prop_pair(layout, entry, "line_image_interval_mm", "line_image_angle_deg")
    prop_if(layout, entry, "line_image_jitter", text="乱れ", slider=True)


def _draw_uni_flash(layout, entry, columns, preset_mode: bool) -> None:
    from .. import effect_line_panel

    effect_line_panel.draw_effect_params(
        layout,
        entry,
        with_generate_button=False,
        fixed_effect_type="uni_flash",
        show_type=False,
        show_path_settings=False,
        columns=columns,
        preset_mode=preset_mode,
    )


def _draw_white_outline(layout, entry, columns) -> None:
    from .. import balloon_panel

    balloon_panel.draw_white_outline_line_settings(layout, entry, columns=columns)


def _draw_double_line(layout, entry, balloon_shapes, shape: str) -> None:
    prop_pair(layout, entry, "multi_line_count", "multi_line_direction")
    prop_pair(layout, entry, "multi_line_width_mm", "multi_line_spacing_mm")
    prop_pair(layout, entry, "multi_line_width_scale_percent", "multi_line_spacing_scale_percent")
    if not balloon_shapes.is_dynamic_meldex_shape(shape):
        return
    prop_pair(
        layout,
        entry,
        "thorn_multi_line_length_scale_near_percent",
        "thorn_multi_line_length_scale_far_percent",
    )
    prop_if(layout, entry, "thorn_multi_line_cross_enabled", text="交差", toggle=True)
    prop_pair(layout, entry, "thorn_multi_line_valley_width_pct", "thorn_multi_line_peak_width_pct")


def _draw_line_colors(layout, entry, line_style: str) -> None:
    if line_style == "uni_flash":
        return
    has_fill = line_style != "white_outline"
    row = layout.row(align=True)
    prop_if(row, entry, "line_color", text="線色")
    if has_fill:
        prop_if(row, entry, "fill_color", text="塗り色")
        prop_if(layout, entry, "fill_opacity", text="塗り不透明度", slider=True)
        _draw_fill_style(layout, entry)
        _draw_margins(layout, entry)
    prop_if(layout, entry, "opacity", text="不透明度", slider=True)


def _draw_fill_style(layout, entry) -> None:
    prop_if(layout, entry, "fill_material_name", text="塗りマテリアル")
    row = layout.row(align=True)
    prop_if(row, entry, "fill_blur_amount", text="ボカシ", slider=True)
    prop_if(row, entry, "fill_blur_axis", text="")
    prop_if(row, entry, "fill_blur_dither", text="ディザ", toggle=True)
    prop_if(layout, entry, "fill_gradient_enabled", text="グラデーション")
    gradient = layout.column(align=True)
    gradient.enabled = bool(value(entry, "fill_gradient_enabled", False))
    prop_pair(gradient, entry, "fill_gradient_start_color", "fill_gradient_end_color")
    prop_if(gradient, entry, "fill_gradient_angle_deg", text="角度")


def _draw_margins(layout, entry) -> None:
    for prefix, label in (("outer", "外側フチ"), ("inner", "内側フチ")):
        row = layout.row(align=True)
        enabled_name = f"{prefix}_white_margin_enabled"
        prop_if(row, entry, enabled_name, text=label, toggle=True)
        content = row.row(align=True)
        content.enabled = bool(value(entry, enabled_name, False))
        prop_if(content, entry, f"{prefix}_white_margin_width_mm", text="幅")
        prop_if(content, entry, f"{prefix}_white_margin_color", text="")


def _draw_tails(layout, context, session, entry, preset_mode: bool) -> None:
    tails = list(value(entry, "tails", ()) or ())
    box = layout.box()
    row = box.row(align=True)
    row.label(text=f"しっぽ ({len(tails)})", icon="SHARPCURVE")
    if not preset_mode:
        add = detail_operator(row, "bmanga.detail_tail_add", text="", icon="ADD")
        set_operator_fields(
            add,
            session_token=session.token,
            target_id=session.target.stable_id,
            page_id=_page_id(session.target.params),
            balloon_id=str(value(entry, "id", "") or ""),
        )
    for index, tail in enumerate(tails):
        _draw_one_tail(box, context, session, tail, index, preset_mode)


def _draw_one_tail(layout, context, session, tail, index: int, preset_mode: bool) -> None:
    box = layout.box()
    if not preset_mode:
        header = box.row(align=True)
        header.label(text=f"しっぽ {index + 1}")
        remove = detail_operator(header, "bmanga.detail_tail_remove", text="", icon="X")
        set_operator_fields(
            remove,
            session_token=session.token,
            target_id=session.target.stable_id,
            page_id=_page_id(session.target.params),
            balloon_id=str(value(session.target.data, "id", "") or ""),
            tail_index=index,
        )
    if not preset_mode:
        _draw_tail_preset(box, context, session, index)
    _draw_tail_fields(box, tail)


def _draw_tail_fields(layout, tail) -> None:
    prop_if(layout, tail, "line_type", text="線", expand=True)
    prop_if(layout, tail, "type", text="種類")
    prop_pair(layout, tail, "direction_deg", "length_mm")
    prop_if(layout, tail, "curve_bend", text="曲げ", slider=True)
    prop_pair(layout, tail, "root_width_mm", "tip_width_mm")
    prop_pair(layout, tail, "taper_in_percent", "taper_out_percent")
    prop_pair(layout, tail, "ellipse_gap_mm", "ellipse_angle_deg")
    prop_if(layout, tail, "ellipse_orient", text="楕円の向き", expand=True)
    prop_if(layout, tail, "sharp_corners", text="角を尖らせる")
    prop_if(layout, tail, "curve_mode", text="曲線", expand=True)


def _draw_tail_preset(layout, context, session, index: int) -> None:
    from .. import preset_management_ui

    preset_management_ui.draw_preset_list(
        layout,
        context,
        "tail",
        compact=True,
    )
    row = layout.row(align=True)
    wm = getattr(context, "window_manager", None)
    apply = detail_operator(
        row,
        "bmanga.detail_tail_preset_apply",
        text="選択プリセットを適用",
        icon="PRESET",
    )
    set_operator_fields(
        apply,
        session_token=session.token,
        target_id=session.target.stable_id,
        page_id=_page_id(session.target.params),
        balloon_id=str(value(session.target.data, "id", "") or ""),
        tail_index=index,
        preset_name=(
            str(getattr(wm, "bmanga_tail_preset_selector", "") or "")
            if wm is not None
            else ""
        ),
    )


def draw_tail_body(layout, _context, session, mode) -> None:
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    box = body_columns(layout, session)[0].box()
    box.label(text="しっぽ設定", icon="SHARPCURVE")
    _draw_tail_fields(box, session.target.data)
    if not preset_mode:
        box.label(text="プリセット適用は各しっぽから行います", icon="INFO")


def _page_id(params) -> str:
    if isinstance(params, Mapping):
        return str(params.get("page_id", "") or "")
    return str(getattr(params, "page_id", "") or "") if params is not None else ""


__all__ = ["draw_balloon_body", "draw_tail_body"]
