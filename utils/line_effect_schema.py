"""Shared schemas for path images and line-effect settings.

This module intentionally does not import bpy.  It is used by core property
groups, operators, and pure Python tests to keep duplicated field lists in sync.
"""

from __future__ import annotations

PATH_IMAGE_DRAW_MODE_ITEMS = (
    ("stamp", "スタンプ", "パスに沿って画像をそのまま連続表示します"),
    ("ribbon", "リボン", "パスに沿って画像を滑らかに変形します"),
)

PATH_CONTENT_SOURCE_ITEMS = (
    ("image", "画像", "画像ファイルを使います"),
    ("shape", "生成形状", "円形などの生成形状を使います"),
)

PATH_GENERATED_SHAPE_ITEMS = (
    ("circle", "円形", ""),
    ("square", "四角形", ""),
    ("polygon", "多角形", ""),
    ("star", "星型", ""),
    ("heart", "ハート", ""),
)

PATH_IMAGE_STAMP_ANGLE_MODE_ITEMS = (
    ("fixed", "固定", "指定した角度で固定します"),
    ("line", "線の向き", "パスの向きに合わせます"),
    ("object", "指定オブジェクト方向", "指定したオブジェクトの向きに合わせます"),
)

PATH_IMAGE_RIBBON_REPEAT_MODE_ITEMS = (
    ("repeat", "ブラシサイズの画像を連続", "ブラシサイズを基準に画像を繰り返します"),
    ("stretch", "画像ひとつを伸ばす", "始点から終点まで画像ひとつを伸ばします"),
)

INOUT_APPLY_ITEMS = (
    ("brush_size", "線幅", ""),
    ("opacity", "不透明度", ""),
)

INOUT_APPLY_BRUSH_SIZE_FIELD = "inout_apply_brush_size"
INOUT_APPLY_OPACITY_FIELD = "inout_apply_opacity"

INOUT_RANGE_MODE_ITEMS = (
    ("percent", "％指定", "線全体に対する割合で入り抜きの範囲を指定"),
    ("length", "長さ指定", "mm の長さで入り抜きの範囲を指定"),
)

WHITE_OUTLINE_BLACK_DIRECTION_ITEMS = (
    ("outside", "外側", "白線群の外側へ黒線を重ねる"),
    ("inside", "内側", "白線群の内側へ黒線を重ねる"),
    ("both", "両側", "白線群の外側と内側へ黒線を重ねる"),
)

EFFECT_START_SHAPE_FIELDS = (
    "start_shape",
    "start_to_coma_frame",
    "start_rounded_corner_enabled",
    "start_rounded_corner_radius_mm",
    "start_rounded_corner_radius_unit",
    "start_rounded_corner_radius_percent",
    "start_cloud_bump_width_mm",
    "start_cloud_bump_width_jitter",
    "start_cloud_bump_height_mm",
    "start_cloud_bump_height_jitter",
    "start_cloud_offset_percent",
    "start_cloud_sub_width_ratio",
    "start_cloud_sub_width_jitter",
    "start_cloud_sub_height_ratio",
    "start_cloud_sub_height_jitter",
)

EFFECT_END_SHAPE_FIELDS = (
    "end_shape",
    "end_rounded_corner_enabled",
    "end_rounded_corner_radius_mm",
    "end_rounded_corner_radius_unit",
    "end_rounded_corner_radius_percent",
    "end_cloud_bump_width_mm",
    "end_cloud_bump_width_jitter",
    "end_cloud_bump_height_mm",
    "end_cloud_bump_height_jitter",
    "end_cloud_offset_percent",
    "end_cloud_sub_width_ratio",
    "end_cloud_sub_width_jitter",
    "end_cloud_sub_height_ratio",
    "end_cloud_sub_height_jitter",
)

EFFECT_PATH_IMAGE_FIELDS = (
    "base_path_enabled",
    "base_path_points_json",
    "line_image_source",
    "line_image_path",
    "line_image_shape_kind",
    "line_image_shape_sides",
    "line_image_color",
    "line_image_draw_mode",
    "line_image_brush_size_mm",
    "line_image_aspect_ratio",
    "line_image_angle_deg",
    "line_image_spacing_percent",
    "line_image_stamp_angle_mode",
    "line_image_stamp_angle_object_name",
    "line_image_ribbon_repeat_mode",
    "line_image_inout_size_enabled",
    "line_image_inout_opacity_enabled",
    "line_image_inout_color_enabled",
    "line_image_inout_start_color",
    "line_image_inout_end_color",
)

EFFECT_STROKE_FIELDS = (
    "brush_size_mm",
    "brush_jitter_enabled",
    "brush_jitter_amount",
    "length_jitter_enabled",
    "length_jitter_amount",
    "end_length_jitter_enabled",
    "end_length_jitter_amount",
    "spacing_mode",
    "spacing_angle_deg",
    "spacing_distance_mm",
    "spacing_density_compensation",
    "spacing_jitter_enabled",
    "spacing_jitter_amount",
    "max_line_count",
    "bundle_enabled",
    "bundle_line_count",
    "bundle_line_count_jitter",
    "bundle_gap_mm",
    "bundle_gap_jitter_amount",
    "bundle_jagged_enabled",
    "bundle_jagged_height_percent",
)

EFFECT_INOUT_FIELDS = (
    "inout_apply",
    INOUT_APPLY_BRUSH_SIZE_FIELD,
    INOUT_APPLY_OPACITY_FIELD,
    "in_percent",
    "out_percent",
    "in_start_percent",
    "out_start_percent",
    "in_easing_curve",
    "out_easing_curve",
    "inout_range_mode",
    "in_range_percent",
    "out_range_percent",
    "in_range_mm",
    "out_range_mm",
)


def bool_value(value, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "0", "false", "off", "none", "なし", "no"}:
            return False
        if text in {"1", "true", "on", "yes", "あり"}:
            return True
    return bool(value)


def normalize_inout_apply_flags(data: dict, *, default_apply: str = "brush_size") -> dict:
    """Convert the legacy single in/out target into independent target flags."""
    normalized = dict(data or {})
    legacy = str(normalized.get("inout_apply", default_apply) or default_apply)
    if legacy == "length":
        legacy = "brush_size"
    if legacy not in {"brush_size", "opacity"}:
        legacy = "brush_size"

    has_width = INOUT_APPLY_BRUSH_SIZE_FIELD in normalized
    has_opacity = INOUT_APPLY_OPACITY_FIELD in normalized
    if not has_width and not has_opacity:
        normalized[INOUT_APPLY_BRUSH_SIZE_FIELD] = legacy != "opacity"
        normalized[INOUT_APPLY_OPACITY_FIELD] = legacy == "opacity"
    else:
        normalized[INOUT_APPLY_BRUSH_SIZE_FIELD] = bool_value(
            normalized.get(INOUT_APPLY_BRUSH_SIZE_FIELD),
            legacy == "brush_size",
        )
        normalized[INOUT_APPLY_OPACITY_FIELD] = bool_value(
            normalized.get(INOUT_APPLY_OPACITY_FIELD),
            legacy == "opacity",
        )

    if normalized[INOUT_APPLY_BRUSH_SIZE_FIELD]:
        normalized["inout_apply"] = "brush_size"
    elif normalized[INOUT_APPLY_OPACITY_FIELD]:
        normalized["inout_apply"] = "opacity"
    else:
        normalized["inout_apply"] = "brush_size"
    return normalized

EFFECT_COLOR_FIELDS = (
    "opacity",
    "line_color",
    "fill_color",
    "fill_opacity",
    "fill_base_shape",
    "white_underlay_enabled",
    "white_underlay_width_percent",
    "white_underlay_color",
)

EFFECT_WHITE_OUTLINE_FIELDS = (
    "white_outline_count",
    "white_outline_spacing_mm",
    "white_outline_white_line_count_auto",
    "white_outline_white_line_count",
    "white_outline_width_mm",
    "white_outline_width_jitter_enabled",
    "white_outline_width_min_percent",
    "white_outline_length_jitter_enabled",
    "white_outline_length_min_percent",
    "white_outline_white_ratio_percent",
    "white_outline_white_brush_mm",
    "white_outline_white_attenuation",
    "white_outline_white_in_percent",
    "white_outline_white_out_percent",
    "white_outline_white_inout_range_mode",
    "white_outline_white_in_range_percent",
    "white_outline_white_out_range_percent",
    "white_outline_white_in_range_mm",
    "white_outline_white_out_range_mm",
    "white_outline_black_line_count_auto",
    "white_outline_black_line_count",
    "white_outline_black_direction",
    "white_outline_black_brush_mm",
    "white_outline_black_spacing_mm",
    "white_outline_black_width_scale_percent",
    "white_outline_black_length_scale_near_percent",
    "white_outline_black_length_scale_far_percent",
    "white_outline_black_attenuation",
    "white_outline_angle_deg",
)

EFFECT_PARAM_FIELDS = (
    "effect_type",
    "rotation_deg",
    *EFFECT_START_SHAPE_FIELDS,
    *EFFECT_END_SHAPE_FIELDS,
    "brush_size_mm",
    *EFFECT_PATH_IMAGE_FIELDS,
    *EFFECT_STROKE_FIELDS[1:],
    *EFFECT_INOUT_FIELDS,
    *EFFECT_COLOR_FIELDS,
    "uni_flash_offset_percent",
    "speed_angle_deg",
    "speed_line_count",
    *EFFECT_WHITE_OUTLINE_FIELDS,
)

BALLOON_UNI_FLASH_PARAM_FIELDS = (
    "effect_type",
    "rotation_deg",
    *EFFECT_START_SHAPE_FIELDS,
    *EFFECT_END_SHAPE_FIELDS,
    "brush_size_mm",
    *EFFECT_STROKE_FIELDS[1:],
    *EFFECT_INOUT_FIELDS,
    *EFFECT_COLOR_FIELDS,
    "uni_flash_offset_percent",
    "white_outline_angle_deg",
    "white_outline_width_jitter_enabled",
    "white_outline_width_min_percent",
    "white_outline_length_jitter_enabled",
    "white_outline_length_min_percent",
    "white_outline_white_line_count_auto",
    "white_outline_black_line_count_auto",
    "white_outline_white_ratio_percent",
    "white_outline_white_attenuation",
    "white_outline_black_direction",
    "white_outline_black_width_scale_percent",
    "white_outline_black_length_scale_near_percent",
    "white_outline_black_length_scale_far_percent",
    "white_outline_black_attenuation",
)

EFFECT_LINKED_SHAPE_FIELDS = frozenset(
    (
        "rotation_deg",
        *EFFECT_PATH_IMAGE_FIELDS,
        *EFFECT_START_SHAPE_FIELDS,
        *EFFECT_END_SHAPE_FIELDS,
        "spacing_density_compensation",
        "speed_angle_deg",
        "white_outline_count",
        "white_outline_spacing_mm",
        "white_outline_white_line_count_auto",
        "white_outline_white_line_count",
        "white_outline_width_mm",
        "white_outline_width_jitter_enabled",
        "white_outline_width_min_percent",
        "white_outline_length_jitter_enabled",
        "white_outline_length_min_percent",
        "white_outline_white_ratio_percent",
        "white_outline_white_brush_mm",
        "white_outline_white_attenuation",
        "white_outline_white_in_percent",
        "white_outline_white_out_percent",
        "white_outline_white_inout_range_mode",
        "white_outline_white_in_range_percent",
        "white_outline_white_out_range_percent",
        "white_outline_white_in_range_mm",
        "white_outline_white_out_range_mm",
        "white_outline_black_line_count_auto",
        "white_outline_black_line_count",
        "white_outline_black_direction",
        "white_outline_black_brush_mm",
        "white_outline_black_spacing_mm",
        "white_outline_black_width_scale_percent",
        "white_outline_black_length_scale_near_percent",
        "white_outline_black_length_scale_far_percent",
        "white_outline_black_attenuation",
    )
)
