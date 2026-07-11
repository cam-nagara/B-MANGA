"""Shared UI drawing helpers for line-effect settings."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any


FieldMap = Mapping[str, str]
CurveDrawCallback = Callable[[Any, Any], None]


EFFECT_WHITE_OUTLINE_UI_FIELDS: FieldMap = {
    "count": "white_outline_count",
    "bundle_spacing": "white_outline_bundle_spacing_deg",
    "bundle_spacing_jitter": "white_outline_bundle_spacing_jitter",
    "angle": "white_outline_angle_deg",
    "width": "white_outline_width_mm",
    "width_jitter": "white_outline_width_jitter_enabled",
    "width_min": "white_outline_width_min_percent",
    "length_jitter": "white_outline_length_jitter_enabled",
    "length_min": "white_outline_length_min_percent",
    "length": "white_outline_length_percent",
    "white_ratio": "white_outline_white_ratio_percent",
    "black_ratio": "white_outline_black_ratio_percent",
    "white_auto": "white_outline_white_line_count_auto",
    "white_count": "white_outline_white_line_count",
    "white_spacing": "white_outline_spacing_mm",
    "white_spacing_scale": "white_outline_white_spacing_scale_percent",
    "white_brush": "white_outline_white_brush_mm",
    "white_attenuation": "white_outline_white_attenuation",
    "white_in": "white_outline_white_in_percent",
    "white_out": "white_outline_white_out_percent",
    "white_range_mode": "white_outline_white_inout_range_mode",
    "white_in_range_percent": "white_outline_white_in_range_percent",
    "white_out_range_percent": "white_outline_white_out_range_percent",
    "white_in_range_mm": "white_outline_white_in_range_mm",
    "white_out_range_mm": "white_outline_white_out_range_mm",
    "white_in_curve": "white_outline_white_in_easing_curve",
    "white_out_curve": "white_outline_white_out_easing_curve",
    "black_auto": "white_outline_black_line_count_auto",
    "black_count": "white_outline_black_line_count",
    "black_direction": "white_outline_black_direction",
    "black_brush": "white_outline_black_brush_mm",
    "black_spacing": "white_outline_black_spacing_mm",
    "black_spacing_scale": "white_outline_black_spacing_scale_percent",
    "black_width_scale": "white_outline_black_width_scale_percent",
    "black_near": "white_outline_black_length_scale_near_percent",
    "black_far": "white_outline_black_length_scale_far_percent",
    "black_attenuation": "white_outline_black_attenuation",
    "black_in": "white_outline_black_in_percent",
    "black_out": "white_outline_black_out_percent",
    "black_range_mode": "white_outline_black_inout_range_mode",
    "black_in_range_percent": "white_outline_black_in_range_percent",
    "black_out_range_percent": "white_outline_black_out_range_percent",
    "black_in_range_mm": "white_outline_black_in_range_mm",
    "black_out_range_mm": "white_outline_black_out_range_mm",
    "black_in_curve": "white_outline_black_in_easing_curve",
    "black_out_curve": "white_outline_black_out_easing_curve",
}


BALLOON_WHITE_OUTLINE_UI_FIELDS: FieldMap = {
    "count": "flash_white_outline_count",
    "bundle_spacing": "white_outline_bundle_spacing_deg",
    "bundle_spacing_jitter": "white_outline_bundle_spacing_jitter",
    "angle": "white_outline_angle_deg",
    "width": "flash_white_outline_width_mm",
    "width_jitter": "white_outline_width_jitter_enabled",
    "width_min": "white_outline_width_min_percent",
    "length_jitter": "white_outline_length_jitter_enabled",
    "length_min": "white_outline_length_min_percent",
    "length": "white_outline_length_percent",
    "white_auto": "white_outline_white_line_count_auto",
    "white_count": "flash_white_outline_white_line_count",
    "white_spacing": "flash_white_outline_spacing_mm",
    "white_spacing_scale": "white_outline_white_spacing_scale_percent",
    "white_ratio": "white_outline_white_ratio_percent",
    "black_ratio": "white_outline_black_ratio_percent",
    "white_attenuation": "white_outline_white_attenuation",
    "white_in": "white_outline_white_in_percent",
    "white_out": "white_outline_white_out_percent",
    "white_range_mode": "white_outline_white_inout_range_mode",
    "white_in_range_percent": "white_outline_white_in_range_percent",
    "white_out_range_percent": "white_outline_white_out_range_percent",
    "white_in_range_mm": "white_outline_white_in_range_mm",
    "white_out_range_mm": "white_outline_white_out_range_mm",
    "white_in_curve": "white_outline_white_in_easing_curve",
    "white_out_curve": "white_outline_white_out_easing_curve",
    "black_auto": "white_outline_black_line_count_auto",
    "black_count": "flash_white_outline_black_line_count",
    "black_direction": "white_outline_black_direction",
    "black_spacing": "flash_white_outline_black_spacing_mm",
    "black_spacing_scale": "white_outline_black_spacing_scale_percent",
    "black_width_scale": "white_outline_black_width_scale_percent",
    "black_near": "white_outline_black_length_scale_near_percent",
    "black_far": "white_outline_black_length_scale_far_percent",
    "black_attenuation": "white_outline_black_attenuation",
    "black_in": "white_outline_black_in_percent",
    "black_out": "white_outline_black_out_percent",
    "black_range_mode": "white_outline_black_inout_range_mode",
    "black_in_range_percent": "white_outline_black_in_range_percent",
    "black_out_range_percent": "white_outline_black_out_range_percent",
    "black_in_range_mm": "white_outline_black_in_range_mm",
    "black_out_range_mm": "white_outline_black_out_range_mm",
    "black_in_curve": "white_outline_black_in_easing_curve",
    "black_out_curve": "white_outline_black_out_easing_curve",
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


def _profile_fields(fields: FieldMap, prefix: str) -> dict[str, str]:
    """白線/黒線のUI属性表を線幅グラフ共通属性表へ変換する。"""
    return {
        "in_percent": fields[f"{prefix}_in"],
        "out_percent": fields[f"{prefix}_out"],
        "range_mode": fields[f"{prefix}_range_mode"],
        "in_range_percent": fields[f"{prefix}_in_range_percent"],
        "out_range_percent": fields[f"{prefix}_out_range_percent"],
        "in_range_mm": fields[f"{prefix}_in_range_mm"],
        "out_range_mm": fields[f"{prefix}_out_range_mm"],
        "in_curve": fields[f"{prefix}_in_curve"],
        "out_curve": fields[f"{prefix}_out_curve"],
    }


def _draw_outline_jitter_settings(layout: Any, owner: Any, fields: FieldMap) -> None:
    row = layout.row(align=True)
    _prop(row, owner, fields, "width_jitter")
    sub = row.row(align=True)
    sub.enabled = _bool(owner, fields, "width_jitter")
    _prop(sub, owner, fields, "width_min", text="最小値")

    row = layout.row(align=True)
    _prop(row, owner, fields, "length_jitter")
    sub = row.row(align=True)
    sub.enabled = _bool(owner, fields, "length_jitter")
    _prop(sub, owner, fields, "length_min", text="最小値")


def _draw_outline_band_ratio_settings(layout: Any, owner: Any, fields: FieldMap) -> None:
    row = layout.row(align=True)
    _prop(row, owner, fields, "white_ratio")
    _prop(row, owner, fields, "black_ratio")
    _prop(layout, owner, fields, "length")


def _draw_effect_white_settings(
    layout: Any,
    params: Any,
    fields: FieldMap,
    draw_inout_curve: CurveDrawCallback | None = None,
) -> None:
    white_box = layout.box()
    white_box.label(text="白線")
    row = white_box.row(align=True)
    _prop(row, params, fields, "white_auto", toggle=True)
    count_row = row.row(align=True)
    count_row.enabled = not _bool(params, fields, "white_auto")
    _prop(count_row, params, fields, "white_count", text="本数")
    row = white_box.row(align=True)
    _prop(row, params, fields, "white_spacing")
    _prop(row, params, fields, "white_brush")
    _prop(white_box, params, fields, "white_spacing_scale")
    _prop(white_box, params, fields, "white_attenuation")

    row = white_box.row(align=True)
    _prop(row, params, fields, "white_in")
    _prop(row, params, fields, "white_out")
    if draw_inout_curve is not None:
        draw_inout_curve(
            white_box,
            params,
            fields=_profile_fields(fields, "white"),
            profile_key="white",
        )


def _draw_effect_black_settings(
    layout: Any,
    params: Any,
    fields: FieldMap,
    draw_inout_curve: CurveDrawCallback | None = None,
) -> None:
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
    _prop(black_box, params, fields, "black_spacing_scale")
    row = black_box.row(align=True)
    _prop(row, params, fields, "black_width_scale")
    _prop(row, params, fields, "black_attenuation")
    row = black_box.row(align=True)
    _prop(row, params, fields, "black_near")
    _prop(row, params, fields, "black_far")
    row = black_box.row(align=True)
    _prop(row, params, fields, "black_in")
    _prop(row, params, fields, "black_out")
    if draw_inout_curve is not None:
        draw_inout_curve(
            black_box,
            params,
            fields=_profile_fields(fields, "black"),
            profile_key="black",
        )


def draw_effect_white_outline_settings(
    layout: Any,
    params: Any,
    *,
    show_opacity: bool = True,
    columns: Sequence[Any] | None = None,
    draw_inout_curve: CurveDrawCallback | None = None,
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
    row = box.row(align=True)
    _prop(row, params, fields, "bundle_spacing")
    _prop(row, params, fields, "bundle_spacing_jitter")
    _prop(box, params, fields, "width")
    _draw_outline_band_ratio_settings(box, params, fields)
    _draw_outline_jitter_settings(box, params, fields)
    _draw_effect_black_settings(_column(cols, 1), params, fields, draw_inout_curve)
    _draw_effect_white_settings(_column(cols, 2), params, fields, draw_inout_curve)


def _draw_balloon_white_settings(
    layout: Any,
    entry: Any,
    fields: FieldMap,
    draw_inout_curve: CurveDrawCallback | None = None,
) -> None:
    white_box = layout.box()
    white_box.label(text="白線")
    row = white_box.row(align=True)
    _prop(row, entry, fields, "white_auto", toggle=True)
    sub = row.row(align=True)
    sub.enabled = not _bool(entry, fields, "white_auto")
    _prop(sub, entry, fields, "white_count")
    _prop(white_box, entry, fields, "white_spacing")
    _prop(white_box, entry, fields, "white_spacing_scale")
    _prop(white_box, entry, fields, "white_attenuation", text="減衰")
    row = white_box.row(align=True)
    _prop(row, entry, fields, "white_in")
    _prop(row, entry, fields, "white_out")
    if draw_inout_curve is not None:
        draw_inout_curve(
            white_box,
            entry,
            fields=_profile_fields(fields, "white"),
            profile_key="white",
        )


def _draw_balloon_black_settings(
    layout: Any,
    entry: Any,
    fields: FieldMap,
    draw_inout_curve: CurveDrawCallback | None = None,
) -> None:
    black_box = layout.box()
    black_box.label(text="黒線")
    row = black_box.row(align=True)
    _prop(row, entry, fields, "black_auto", toggle=True)
    sub = row.row(align=True)
    sub.enabled = not _bool(entry, fields, "black_auto")
    _prop(sub, entry, fields, "black_count")
    _prop(black_box, entry, fields, "black_spacing")
    _prop(black_box, entry, fields, "black_spacing_scale")
    row = black_box.row(align=True)
    _prop(row, entry, fields, "black_width_scale")
    _prop(row, entry, fields, "black_attenuation", text="減衰")
    row = black_box.row(align=True)
    _prop(row, entry, fields, "black_near")
    _prop(row, entry, fields, "black_far")
    row = black_box.row(align=True)
    _prop(row, entry, fields, "black_in")
    _prop(row, entry, fields, "black_out")
    if draw_inout_curve is not None:
        draw_inout_curve(
            black_box,
            entry,
            fields=_profile_fields(fields, "black"),
            profile_key="black",
        )


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
    _prop(row, entry, fields, "bundle_spacing")
    _prop(row, entry, fields, "bundle_spacing_jitter")
    row = layout.row(align=True)
    _prop(row, entry, fields, "width")
    _prop(row, entry, fields, "black_direction", text="")
    _draw_outline_band_ratio_settings(layout, entry, fields)
    _draw_outline_jitter_settings(layout, entry, fields)
    _draw_balloon_black_settings(
        _column(cols, 1), entry, fields, draw_inout_curve
    )
    _draw_balloon_white_settings(
        _column(cols, 1), entry, fields, draw_inout_curve
    )
    _draw_balloon_inout_settings(
        _column(cols, 2),
        entry,
        fields,
        draw_inout_curve=draw_inout_curve,
    )
