"""効果線 (集中線/ウニフラ/ベタフラ/流線/白抜き線) の PropertyGroup.

計画書 3.1.6 参照。ツール起動時のパラメータセットと、生成済み効果線
レイヤーのメタデータを保持する。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
)

from . import balloon
from ..utils import corner_radius, log

_logger = log.get_logger(__name__)


_EFFECT_TYPE_ITEMS = (
    ("focus", "集中線", "放射状の集中線"),
    ("uni_flash", "ウニフラ", "ギザギザ基準図形の集中線"),
    ("beta_flash", "ベタフラ", "塗りつぶし版ウニフラ"),
    ("speed", "流線", "動き・速度表現の平行線"),
    ("white_outline", "白抜き線", "白線群の両側に黒線群を重ねた効果線"),
)

def _clean_effect_shape_label(label: str) -> str:
    return str(label or "").replace("（旧）", "").replace("・旧", "")


_LEGACY_SHAPE_TO_CURRENT = {
    "polygon": "octagon",
    "pill": "ellipse",
    "hexagon": "octagon",
    "diamond": "octagon",
    "star": "thorn",
    "spike_straight": "thorn",
    "spike_curve": "thorn-curve",
}

_EFFECT_SHAPE_ITEMS = tuple(
    (item[0], _clean_effect_shape_label(item[1]), item[2])
    for item in balloon._SHAPE_ITEMS
    if item[0] not in {"custom", "none", "uni_flash", "white_outline"}
)

_SPACING_MODE_ITEMS = (
    ("angle", "角度指定", ""),
    ("distance", "距離指定", ""),
)

_INOUT_APPLY_ITEMS = (
    ("brush_size", "線幅", ""),
    ("opacity", "不透明度", ""),
)

_INOUT_RANGE_MODE_ITEMS = (
    ("percent", "％指定", "線全体に対する割合で入り抜きの範囲を指定"),
    ("length", "長さ指定", "mm の長さで入り抜きの範囲を指定"),
)

_WHITE_OUTLINE_BLACK_DIRECTION_ITEMS = (
    ("outside", "外側", "白線群の外側へ黒線を重ねる"),
    ("inside", "内側", "白線群の内側へ黒線を重ねる"),
    ("both", "両側", "白線群の外側と内側へ黒線を重ねる"),
)

_LEGACY_BASE_SHAPE_TO_EFFECT_SHAPE = {
    "rect": "rect",
    "ellipse": "ellipse",
    "polygon": "octagon",
}

EFFECT_PARAM_SCHEMA_VERSION = 14
_LEGACY_DEFAULT_MAX_LINE_COUNT = 300
_DEFAULT_MAX_LINE_COUNT = 1000
_LEGACY_DEFAULT_SPEED_LINE_COUNT = 20
_DEFAULT_SPEED_LINE_COUNT = 300
# 入り始点の既定が 0% だと「入り (%)」を変えても見た目が変わらない
# (入り区間の長さがゼロ) ため、既定で始点から 50% を入り区間にして
# 「入り%」を動かせばすぐ効く状態にする。既定の入り% は 100 なので
# 既存・新規とも見た目は変わらない。
_DEFAULT_IN_START_PERCENT = 50.0
_DEFAULT_OUT_START_PERCENT = 100.0

EFFECT_PARAM_FIELDS = (
    "effect_type",
    "rotation_deg",
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
    "inout_apply",
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
    "opacity",
    "line_color",
    "fill_color",
    "fill_opacity",
    "fill_base_shape",
    "white_underlay_enabled",
    "white_underlay_width_percent",
    "white_underlay_color",
    "uni_flash_offset_percent",
    "speed_angle_deg",
    "speed_line_count",
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


def _on_params_changed(self, context) -> None:
    """選択中の効果線レイヤーへ詳細設定の変更を即時反映する。"""
    if context is None:
        return
    try:
        from ..operators import effect_line_op

        effect_line_op.on_effect_params_changed(context, self)
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line params update failed")


def _on_in_start_changed(self, context) -> None:
    in_start = float(getattr(self, "in_start_percent", _DEFAULT_IN_START_PERCENT))
    out_start = float(getattr(self, "out_start_percent", _DEFAULT_OUT_START_PERCENT))
    if in_start + out_start > 100.0:
        self.out_start_percent = max(0.0, 100.0 - in_start)
    _on_params_changed(self, context)


def _on_out_start_changed(self, context) -> None:
    in_start = float(getattr(self, "in_start_percent", _DEFAULT_IN_START_PERCENT))
    out_start = float(getattr(self, "out_start_percent", _DEFAULT_OUT_START_PERCENT))
    if in_start + out_start > 100.0:
        self.in_start_percent = max(0.0, 100.0 - out_start)
    _on_params_changed(self, context)


def _color_value(value) -> list[float]:
    try:
        return [float(value[i]) for i in range(4)]
    except Exception:  # noqa: BLE001
        return [0.0, 0.0, 0.0, 1.0]


def _density_compensation_enabled(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off", "none", "なし"}
    return bool(value)


def _normalize_start_percent_pair(data: dict) -> None:
    has_in = "in_start_percent" in data
    has_out = "out_start_percent" in data
    if not has_in and not has_out:
        return
    try:
        in_value = max(0.0, min(100.0, float(data.get("in_start_percent", _DEFAULT_IN_START_PERCENT))))
    except Exception:  # noqa: BLE001
        in_value = _DEFAULT_IN_START_PERCENT
    try:
        out_value = max(0.0, min(100.0, float(data.get("out_start_percent", _DEFAULT_OUT_START_PERCENT))))
    except Exception:  # noqa: BLE001
        out_value = _DEFAULT_OUT_START_PERCENT
    total = in_value + out_value
    if total > 100.0:
        if has_in and not has_out:
            out_value = max(0.0, 100.0 - in_value)
        elif has_out and not has_in:
            in_value = max(0.0, 100.0 - out_value)
        else:
            scale = 100.0 / max(total, 1.0e-9)
            in_value *= scale
            out_value *= scale
    data["in_start_percent"] = in_value
    data["out_start_percent"] = out_value


def effect_params_to_dict(params) -> dict:
    """BNameEffectLineParams をレイヤーメタデータ保存用 dict に変換する。"""
    data = {"schema_version": EFFECT_PARAM_SCHEMA_VERSION}
    for field in EFFECT_PARAM_FIELDS:
        if not hasattr(params, field):
            continue
        value = getattr(params, field)
        if field == "spacing_density_compensation":
            spacing_mode = str(getattr(params, "spacing_mode", "") or "")
            data[field] = True if spacing_mode == "distance" else _density_compensation_enabled(value)
        elif field in {"line_color", "fill_color", "white_underlay_color"}:
            data[field] = _color_value(value)
        elif field == "inout_apply":
            data[field] = str(value) if str(value) in {"brush_size", "opacity"} else "brush_size"
        elif isinstance(value, bool):
            data[field] = bool(value)
        elif isinstance(value, int):
            data[field] = int(value)
        elif isinstance(value, float):
            data[field] = float(value)
        else:
            data[field] = str(value)
    return data


def effect_params_from_dict(params, data: dict) -> None:
    """保存済み dict を BNameEffectLineParams へ戻す。未知項目は無視する。"""
    data = dict(data or {})
    try:
        schema_version = int(data.get("schema_version", 1) or 1)
    except Exception:  # noqa: BLE001
        schema_version = 1
    if "end_shape" not in data and "base_shape" in data:
        data["end_shape"] = _LEGACY_BASE_SHAPE_TO_EFFECT_SHAPE.get(str(data["base_shape"]), "rect")
    for shape_field in ("start_shape", "end_shape"):
        if shape_field in data:
            data[shape_field] = _LEGACY_SHAPE_TO_CURRENT.get(str(data[shape_field]), data[shape_field])
    if str(data.get("inout_apply", "")) == "length":
        data["inout_apply"] = "brush_size"
    if (
        schema_version < EFFECT_PARAM_SCHEMA_VERSION
        and int(data.get("max_line_count", _LEGACY_DEFAULT_MAX_LINE_COUNT) or 0)
        == _LEGACY_DEFAULT_MAX_LINE_COUNT
    ):
        data["max_line_count"] = _DEFAULT_MAX_LINE_COUNT
    if (
        schema_version < EFFECT_PARAM_SCHEMA_VERSION
        and int(data.get("speed_line_count", _LEGACY_DEFAULT_SPEED_LINE_COUNT) or 0)
        == _LEGACY_DEFAULT_SPEED_LINE_COUNT
    ):
        data["speed_line_count"] = _DEFAULT_SPEED_LINE_COUNT
    if schema_version < EFFECT_PARAM_SCHEMA_VERSION:
        data.setdefault("spacing_density_compensation", True)
        if schema_version < 8:
            if "in_start_percent" not in data and "in_range_percent" in data:
                data["in_start_percent"] = data["in_range_percent"]
            if "out_start_percent" not in data and "out_range_percent" in data:
                data["out_start_percent"] = data["out_range_percent"]
        if schema_version < 9:
            from ..utils import percentage

            for percent_field in (
                "length_jitter_amount",
                "end_length_jitter_amount",
                "opacity",
                "fill_opacity",
            ):
                if percent_field in data:
                    data[percent_field] = percentage.legacy_factor_to_percent(data[percent_field])
    _normalize_start_percent_pair(data)
    if str(data.get("spacing_mode", getattr(params, "spacing_mode", "")) or "") == "distance":
        data["spacing_density_compensation"] = True
    if "spacing_density_compensation" in data:
        data["spacing_density_compensation"] = _density_compensation_enabled(data["spacing_density_compensation"])
    for field in EFFECT_PARAM_FIELDS:
        if field not in data or not hasattr(params, field):
            continue
        value = data[field]
        try:
            if field in {"line_color", "fill_color", "white_underlay_color"}:
                setattr(params, field, tuple(float(v) for v in value[:4]))
            else:
                setattr(params, field, value)
        except Exception:  # noqa: BLE001
            _logger.debug("effect_line param restore skipped: %s=%r", field, value)


class BNameEffectLineParams(bpy.types.PropertyGroup):
    """効果線ツールのパラメータ (プリセット保存対象)."""

    effect_type: EnumProperty(name="種類", items=_EFFECT_TYPE_ITEMS, default="focus", update=_on_params_changed)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="全体回転", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]

    start_shape: EnumProperty(name="始点形状", items=_EFFECT_SHAPE_ITEMS, default="rect", update=_on_params_changed)  # type: ignore[valid-type]
    start_to_coma_frame: BoolProperty(name="始点をコマ枠に設定", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_unit: EnumProperty(name="単位", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", default=10.0, min=2.0, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", default=4.0, min=0.5, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]

    end_shape: EnumProperty(name="終点形状", items=_EFFECT_SHAPE_ITEMS, default="ellipse", update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_unit: EnumProperty(name="単位", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", default=10.0, min=2.0, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", default=4.0, min=0.5, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]

    brush_size_mm: FloatProperty(name="線幅 (mm)", default=0.30, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    brush_jitter_enabled: BoolProperty(name="乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    brush_jitter_amount: FloatProperty(name="乱れ量", default=0.2, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    length_jitter_enabled: BoolProperty(name="始点乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    length_jitter_amount: FloatProperty(name="始点乱れ (%)", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    end_length_jitter_enabled: BoolProperty(name="終点乱れ", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    end_length_jitter_amount: FloatProperty(name="終点乱れ (%)", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]

    spacing_mode: EnumProperty(name="線の間隔", items=_SPACING_MODE_ITEMS, default="distance", update=_on_params_changed)  # type: ignore[valid-type]
    spacing_angle_deg: FloatProperty(name="線の間隔 (角度)", default=5.0, min=0.1, soft_max=90.0, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_distance_mm: FloatProperty(name="線の間隔 (距離 mm)", default=1.00, min=0.01, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_density_compensation: BoolProperty(name="密度補正", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_jitter_enabled: BoolProperty(name="乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_jitter_amount: FloatProperty(name="間隔乱れ量", default=0.2, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    max_line_count: IntProperty(name="最大本数", default=_DEFAULT_MAX_LINE_COUNT, min=1, soft_max=2000, update=_on_params_changed)  # type: ignore[valid-type]

    bundle_enabled: BoolProperty(name="まとまり", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_line_count: IntProperty(name="数", default=5, min=1, soft_max=50, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_line_count_jitter: FloatProperty(name="数の乱れ", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    bundle_gap_mm: FloatProperty(name="まとまり間隔 (mm)", default=5.0, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_gap_jitter_amount: FloatProperty(name="まとまり間隔の乱れ", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    bundle_jagged_enabled: BoolProperty(name="ギザギザにする", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_jagged_height_percent: FloatProperty(name="ギザギザ高さ (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]

    inout_apply: EnumProperty(name="適用先", items=_INOUT_APPLY_ITEMS, default="brush_size", update=_on_params_changed)  # type: ignore[valid-type]
    in_percent: FloatProperty(name="入り (%)", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    out_percent: FloatProperty(name="抜き (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    in_start_percent: FloatProperty(name="入り始点 (%)", description="線の始点側から、線幅が一定になる位置を指定します", default=_DEFAULT_IN_START_PERCENT, min=0.0, max=100.0, update=_on_in_start_changed)  # type: ignore[valid-type]
    out_start_percent: FloatProperty(name="抜き始点 (%)", description="線の終点側から、抜きが始まる長さを指定します", default=_DEFAULT_OUT_START_PERCENT, min=0.0, max=100.0, update=_on_out_start_changed)  # type: ignore[valid-type]
    in_easing_curve: bpy.props.StringProperty(name="入りカーブ", default="0.0000,0.0000;1.0000,1.0000", update=_on_params_changed)  # type: ignore[valid-type]
    out_easing_curve: bpy.props.StringProperty(name="抜きカーブ", default="0.0000,0.0000;1.0000,1.0000", update=_on_params_changed)  # type: ignore[valid-type]
    inout_range_mode: EnumProperty(name="範囲", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_params_changed)  # type: ignore[valid-type]
    in_range_percent: FloatProperty(name="入りの範囲 (%)", description="始点からこの割合の長さを入りの変化区間にする", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    out_range_percent: FloatProperty(name="抜きの範囲 (%)", description="終点からこの割合の長さを抜きの変化区間にする", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    in_range_mm: FloatProperty(name="入りの範囲 (mm)", description="始点からこの長さを入りの変化区間にする", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    out_range_mm: FloatProperty(name="抜きの範囲 (mm)", description="終点からこの長さを抜きの変化区間にする", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]

    opacity: FloatProperty(name="不透明度", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    line_color: FloatVectorProperty(name="線色", subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(name="塗り色", subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    fill_opacity: FloatProperty(name="塗り不透明度", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    fill_base_shape: BoolProperty(name="終点形状を下地として塗る", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_underlay_enabled: BoolProperty(name="白抜き", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_underlay_width_percent: FloatProperty(name="白抜き幅 (%)", default=150.0, min=-300.0, max=300.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_underlay_color: FloatVectorProperty(name="白抜き色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    # ウニフラ固有: 線の終点を交互に出し入れする量 (50% = 従来の固定量)
    uni_flash_offset_percent: FloatProperty(name="ズラし量 (%)", description="線の終点を交互に出し入れして、長さをずらします", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]

    # 流線固有
    speed_angle_deg: FloatProperty(name="流線の角度", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]
    speed_line_count: IntProperty(name="流線の本数上限", default=_DEFAULT_SPEED_LINE_COUNT, min=1, soft_max=1000, update=_on_params_changed)  # type: ignore[valid-type]

    # 白抜き線固有
    white_outline_count: IntProperty(name="束の数", default=5, min=1, soft_max=100, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_spacing_mm: FloatProperty(name="白線間隔 (mm)", default=0.2, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_line_count_auto: BoolProperty(name="白線本数を自動計算", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_line_count: IntProperty(name="白線本数", default=24, min=1, soft_max=200, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_mm: FloatProperty(name="束の幅 (mm)", default=10.0, min=0.01, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_jitter_enabled: BoolProperty(name="太さ乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_min_percent: FloatProperty(name="最小太さ (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_length_jitter_enabled: BoolProperty(name="長さ乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_length_min_percent: FloatProperty(name="最小長さ (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_ratio_percent: FloatProperty(name="白線割合 (%)", default=30.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_brush_mm: FloatProperty(name="白線太さ (mm)", default=0.3, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_attenuation: FloatProperty(name="白線減衰", default=0.0, min=-100.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_in_percent: FloatProperty(name="白線入り (%)", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_out_percent: FloatProperty(name="白線抜き (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_inout_range_mode: EnumProperty(name="白線入り抜き範囲", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_in_range_percent: FloatProperty(name="白線入り範囲 (%)", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_out_range_percent: FloatProperty(name="白線抜き範囲 (%)", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_in_range_mm: FloatProperty(name="白線入り範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_out_range_mm: FloatProperty(name="白線抜き範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_line_count_auto: BoolProperty(name="黒線本数を自動計算", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_line_count: IntProperty(name="黒線本数", default=3, min=1, soft_max=50, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_direction: EnumProperty(name="黒線方向", items=_WHITE_OUTLINE_BLACK_DIRECTION_ITEMS, default="outside", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_brush_mm: FloatProperty(name="黒線太さ (mm)", default=0.3, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_spacing_mm: FloatProperty(name="黒線間隔 (mm)", default=0.2, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_width_scale_percent: FloatProperty(name="黒線幅変化 (%)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_near_percent: FloatProperty(name="黒線長さ変化 (内側)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_far_percent: FloatProperty(name="黒線長さ変化 (外側)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_attenuation: FloatProperty(name="黒線減衰", default=0.0, min=-100.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_angle_deg: FloatProperty(name="角度", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]


_CLASSES = (BNameEffectLineParams,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("effect_line registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
