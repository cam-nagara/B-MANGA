"""Shared UI drawing helpers for line-effect settings."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any


FieldMap = Mapping[str, str]
CurveDrawCallback = Callable[[Any, Any], None]


EFFECT_WHITE_OUTLINE_UI_FIELDS: FieldMap = {
    "count": "white_outline_count",
    "angle": "white_outline_angle_deg",
    "width": "white_outline_width_mm",
    "width_jitter": "white_outline_width_jitter_enabled",
    "width_min": "white_outline_width_min_percent",
    "length_jitter": "white_outline_length_jitter_enabled",
    "length_min": "white_outline_length_min_percent",
    "white_ratio": "white_outline_white_ratio_percent",
    "white_auto": "white_outline_white_line_count_auto",
    "white_count": "white_outline_white_line_count",
    "white_spacing": "white_outline_spacing_mm",
    "white_brush": "white_outline_white_brush_mm",
    "white_attenuation": "white_outline_white_attenuation",
    "white_in": "white_outline_white_in_percent",
    "white_out": "white_outline_white_out_percent",
    "white_range_mode": "white_outline_white_inout_range_mode",
    "white_in_range_percent": "white_outline_white_in_range_percent",
    "white_out_range_percent": "white_outline_white_out_range_percent",
    "white_in_range_mm": "white_outline_white_in_range_mm",
    "white_out_range_mm": "white_outline_white_out_range_mm",
    "black_auto": "white_outline_black_line_count_auto",
    "black_count": "white_outline_black_line_count",
    "black_direction": "white_outline_black_direction",
    "black_brush": "white_outline_black_brush_mm",
    "black_spacing": "white_outline_black_spacing_mm",
    "black_width_scale": "white_outline_black_width_scale_percent",
    "black_near": "white_outline_black_length_scale_near_percent",
    "black_far": "white_outline_black_length_scale_far_percent",
    "black_attenuation": "white_outline_black_attenuation",
}


BALLOON_WHITE_OUTLINE_UI_FIELDS: FieldMap = {
    "count": "flash_white_outline_count",
    "angle": "white_outline_angle_deg",
    "width": "flash_white_outline_width_mm",
    "width_jitter": "white_outline_width_jitter_enabled",
    "width_min": "white_outline_width_min_percent",
    "length_jitter": "white_outline_length_jitter_enabled",
    "length_min": "white_outline_length_min_percent",
    "white_auto": "white_outline_white_line_count_auto",
    "white_count": "flash_white_outline_white_line_count",
    "white_spacing": "flash_white_outline_spacing_mm",
    "white_ratio": "white_outline_white_ratio_percent",
    "white_attenuation": "white_outline_white_attenuation",
    "black_auto": "white_outline_black_line_count_auto",
    "black_count": "flash_white_outline_black_line_count",
    "black_direction": "white_outline_black_direction",
    "black_spacing": "flash_white_outline_black_spacing_mm",
    "black_width_scale": "white_outline_black_width_scale_percent",
    "black_near": "white_outline_black_length_scale_near_percent",
    "black_far": "white_outline_black_length_scale_far_percent",
    "black_attenuation": "white_outline_black_attenuation",
    "inout_apply": "inout_apply",
    "inout_apply_brush_size": "inout_apply_brush_size",
    "inout_apply_opacity": "inout_apply_opacity",
    "in_percent": "in_percent",
    "out_percent": "out_percent",
    "in_start": "in_start_percent",
    "out_start": "out_start_percent",
}


def _attr(fields: FieldMap, key: str) -> str:
    return fields[key]


def _bool(owner: Any, fields: FieldMap, key: str) -> bool:
    return bool(getattr(owner, _attr(fields, key), False))


def _value(owner: Any, fields: FieldMap, key: str, default: Any = None) -> Any:
    return getattr(owner, _attr(fields, key), default)


def _prop(layout: Any, owner: Any, fields: FieldMap, key: str, **kwargs: Any) -> None:
    layout.prop(owner, _attr(fields, key), **kwargs)


def draw_inout_apply_toggles(layout: Any, owner: Any, fields: FieldMap | None = None) -> None:
    field_map = fields or {}
    width_attr = field_map.get("inout_apply_brush_size", "inout_apply_brush_size")
    opacity_attr = field_map.get("inout_apply_opacity", "inout_apply_opacity")
    if not hasattr(owner, width_attr) or not hasattr(owner, opacity_attr):
        legacy_attr = field_map.get("inout_apply", "inout_apply")
        if hasattr(owner, legacy_attr):
            layout.prop(owner, legacy_attr)
        return
    row = layout.row(align=True)
    row.label(text="適用先")
    row.prop(owner, width_attr, text="線幅", toggle=True)
    row.prop(owner, opacity_attr, text="不透明度", toggle=True)


def _columns(base: Any, columns: Sequence[Any] | None) -> list[Any]:
    return [c for c in (columns or ()) if c is not None] or [base]


def _column(cols: Sequence[Any], index: int) -> Any:
    return cols[min(int(index), len(cols) - 1)]


def _draw_outline_jitter_settings(layout: Any, owner: Any, fields: FieldMap) -> None:
    row = layout.row(align=True)
    _prop(row, owner, fields, "width_jitter")
    sub = row.row(align=True)
    sub.enabled = _bool(owner, fields, "width_jitter")
    _prop(sub, owner, fields, "width_min", text="最小")

    row = layout.row(align=True)
    _prop(row, owner, fields, "length_jitter")
    sub = row.row(align=True)
    sub.enabled = _bool(owner, fields, "length_jitter")
    _prop(sub, owner, fields, "length_min", text="最小")


def _draw_effect_white_settings(layout: Any, params: Any, fields: FieldMap) -> None:
    white_box = layout.box()
    white_box.label(text="白線")
    _prop(white_box, params, fields, "white_ratio")
    row = white_box.row(align=True)
    _prop(row, params, fields, "white_auto", toggle=True)
    count_row = row.row(align=True)
    count_row.enabled = not _bool(params, fields, "white_auto")
    _prop(count_row, params, fields, "white_count", text="本数")
    row = white_box.row(align=True)
    _prop(row, params, fields, "white_spacing")
    _prop(row, params, fields, "white_brush")
    _prop(white_box, params, fields, "white_attenuation")

    row = white_box.row(align=True)
    _prop(row, params, fields, "white_in")
    _prop(row, params, fields, "white_out")
    _prop(white_box, params, fields, "white_range_mode")
    range_row = white_box.row(align=True)
    if _value(params, fields, "white_range_mode", "percent") == "length":
        _prop(range_row, params, fields, "white_in_range_mm")
        _prop(range_row, params, fields, "white_out_range_mm")
    else:
        _prop(range_row, params, fields, "white_in_range_percent")
        _prop(range_row, params, fields, "white_out_range_percent")


def _draw_effect_black_settings(layout: Any, params: Any, fields: FieldMap) -> None:
    black_box = layout.box()
    black_box.label(text="黒線")
    row = black_box.row(align=True)
    _prop(row, params, fields, "black_auto", toggle=True)
    count_row = row.row(align=True)
    count_row.enabled = not _bool(params, fields, "black_auto")
    _prop(count_row, params, fields, "black_count", text="本数")
    _prop(black_box, params, fields, "black_direction")
    row = black_box.row(align=True)
    _prop(row, params, fields, "black_brush")
    _prop(row, params, fields, "black_spacing")
    row = black_box.row(align=True)
    _prop(row, params, fields, "black_width_scale")
    _prop(row, params, fields, "black_attenuation")
    row = black_box.row(align=True)
    _prop(row, params, fields, "black_near")
    _prop(row, params, fields, "black_far")


def draw_effect_white_outline_settings(
    layout: Any,
    params: Any,
    *,
    show_opacity: bool = True,
    columns: Sequence[Any] | None = None,
) -> None:
    fields = EFFECT_WHITE_OUTLINE_UI_FIELDS
    cols = _columns(layout, columns)
    box = _column(cols, 0).box()
    box.label(text="白抜き線")
    if show_opacity:
        box.prop(params, "opacity", slider=True)
    row = box.row(align=True)
    _prop(row, params, fields, "count")
    _prop(row, params, fields, "angle")
    _prop(box, params, fields, "width")
    _draw_outline_jitter_settings(box, params, fields)
    _draw_effect_white_settings(_column(cols, 1), params, fields)
    _draw_effect_black_settings(_column(cols, 2), params, fields)


def _draw_balloon_white_settings(layout: Any, entry: Any, fields: FieldMap) -> None:
    white_box = layout.box()
    white_box.label(text="白線")
    row = white_box.row(align=True)
    _prop(row, entry, fields, "white_auto", toggle=True)
    sub = row.row(align=True)
    sub.enabled = not _bool(entry, fields, "white_auto")
    _prop(sub, entry, fields, "white_count")
    row = white_box.row(align=True)
    _prop(row, entry, fields, "white_spacing")
    ratio = row.row(align=True)
    ratio.enabled = _bool(entry, fields, "white_auto")
    _prop(ratio, entry, fields, "white_ratio")
    _prop(white_box, entry, fields, "white_attenuation", text="減衰")


def _draw_balloon_black_settings(layout: Any, entry: Any, fields: FieldMap) -> None:
    black_box = layout.box()
    black_box.label(text="黒線")
    row = black_box.row(align=True)
    _prop(row, entry, fields, "black_auto", toggle=True)
    sub = row.row(align=True)
    sub.enabled = not _bool(entry, fields, "black_auto")
    _prop(sub, entry, fields, "black_count")
    _prop(black_box, entry, fields, "black_spacing")
    row = black_box.row(align=True)
    _prop(row, entry, fields, "black_width_scale")
    _prop(row, entry, fields, "black_attenuation", text="減衰")
    row = black_box.row(align=True)
    _prop(row, entry, fields, "black_near")
    _prop(row, entry, fields, "black_far")


def _draw_balloon_inout_settings(
    layout: Any,
    entry: Any,
    fields: FieldMap,
    *,
    draw_inout_curve: CurveDrawCallback | None = None,
) -> None:
    inout_box = layout.box()
    inout_box.label(text="入り抜き")
    draw_inout_apply_toggles(inout_box, entry, fields)
    row = inout_box.row(align=True)
    _prop(row, entry, fields, "in_percent")
    _prop(row, entry, fields, "out_percent")
    row = inout_box.row(align=True)
    _prop(row, entry, fields, "in_start")
    _prop(row, entry, fields, "out_start")
    if draw_inout_curve is not None:
        draw_inout_curve(inout_box, entry)


def draw_balloon_white_outline_settings(
    layout: Any,
    entry: Any,
    *,
    columns: Sequence[Any] | None = None,
    draw_inout_curve: CurveDrawCallback | None = None,
) -> None:
    fields = BALLOON_WHITE_OUTLINE_UI_FIELDS
    cols = _columns(layout, columns)

    row = layout.row(align=True)
    _prop(row, entry, fields, "count")
    _prop(row, entry, fields, "angle")
    row = layout.row(align=True)
    _prop(row, entry, fields, "width")
    _prop(row, entry, fields, "black_direction", text="")
    _draw_outline_jitter_settings(layout, entry, fields)
    _draw_balloon_white_settings(_column(cols, 1), entry, fields)
    _draw_balloon_black_settings(_column(cols, 1), entry, fields)
    _draw_balloon_inout_settings(
        _column(cols, 2),
        entry,
        fields,
        draw_inout_curve=draw_inout_curve,
    )
