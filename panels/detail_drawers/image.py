"""画像レイヤーとパターンカーブの共通詳細描画。"""

from __future__ import annotations

from .basic import body_columns, prop_if, prop_pair, value


def draw_image_body(layout, _context, session, _mode) -> None:
    entry = session.target.data
    columns = body_columns(layout, session)
    primary = columns[0]
    secondary = columns[min(1, len(columns) - 1)]

    source_box = primary.box()
    source_box.label(text="画像", icon="IMAGE_DATA")
    prop_if(source_box, entry, "filepath", text="画像パス")

    display_box = secondary.box()
    display_box.label(text="合成")
    prop_if(display_box, entry, "opacity", text="不透明度", slider=True)
    prop_if(display_box, entry, "blend_mode", text="ブレンド")
    prop_if(display_box, entry, "tint_color", text="色合い")

    correction = secondary.box()
    correction.label(text="補正")
    prop_if(correction, entry, "brightness", text="明るさ")
    prop_if(correction, entry, "contrast", text="コントラスト")
    prop_if(correction, entry, "binarize_enabled", text="2値化")
    threshold = correction.column(align=True)
    threshold.enabled = bool(value(entry, "binarize_enabled", False))
    prop_if(threshold, entry, "binarize_threshold", text="しきい値")


def draw_image_path_body(_sidebar_top, _sidebar_below, body_cols, context, session, mode) -> None:
    """パターンカーブは内容・描画とも全てプリセット保存対象のため、
    左列(サイドバー)は使わず、右列(body_cols)へ全て描画する。
    """

    entry = session.target.data
    primary = body_cols[0]
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    source = str(value(entry, "content_source", "image") or "image")

    content = primary.box()
    content.label(text="内容", icon="CURVE_BEZCURVE")
    prop_if(content, entry, "content_source", text="内容")
    _draw_source(content, entry, source)

    brush = primary.box()
    brush.label(text="描画")
    prop_if(brush, entry, "opacity", text="不透明度", slider=True)
    prop_if(brush, entry, "draw_mode", text="表示方法")
    prop_pair(
        brush,
        entry,
        "brush_size_mm",
        "aspect_ratio",
        brush_size_mm={"text": "ブラシサイズ"},
        aspect_ratio={"text": "縦横比"},
    )
    prop_pair(
        brush,
        entry,
        "image_angle_deg",
        "spacing_percent",
        image_angle_deg={"text": "角度"},
        spacing_percent={"text": "間隔"},
    )
    prop_if(brush, entry, "color", text="色")
    _draw_direction(brush, entry, source)

    _draw_inout(primary, entry, preset_mode)


def _draw_source(layout, entry, source: str) -> None:
    if source == "shape":
        row = layout.row(align=True)
        prop_if(row, entry, "shape_kind", text="生成形状")
        if str(value(entry, "shape_kind", "") or "") == "polygon":
            prop_if(row, entry, "shape_sides", text="角数")
        return
    prop_if(layout, entry, "filepath", text="画像")


def _draw_direction(layout, entry, source: str) -> None:
    if source != "image":
        return
    draw_mode = str(value(entry, "draw_mode", "stamp") or "stamp")
    if draw_mode == "stamp":
        prop_if(layout, entry, "stamp_angle_mode", text="角度")
        if str(value(entry, "stamp_angle_mode", "") or "") == "object":
            prop_if(layout, entry, "stamp_angle_object_name", text="方向オブジェクト")
        return
    prop_if(layout, entry, "ribbon_repeat_mode", text="リボン")


def _draw_inout(layout, entry, preset_mode: bool) -> None:
    box = layout.box()
    box.label(text="入り抜き")
    row = box.row(align=True)
    prop_if(row, entry, "inout_size_enabled", text="サイズ", toggle=True)
    prop_if(row, entry, "inout_opacity_enabled", text="不透明度", toggle=True)
    prop_if(row, entry, "inout_color_enabled", text="色", toggle=True)
    prop_pair(box, entry, "in_percent", "out_percent")
    color = box.row(align=True)
    color.enabled = bool(value(entry, "inout_color_enabled", False))
    prop_if(color, entry, "inout_start_color", text="入り色")
    prop_if(color, entry, "inout_end_color", text="抜き色")
    if not preset_mode:
        from .. import effect_line_panel

        effect_line_panel.draw_inout_curve_mapping(box, entry)


__all__ = ["draw_image_body", "draw_image_path_body"]
