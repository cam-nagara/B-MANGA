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
    StringProperty,
)

from . import balloon
from ..utils import corner_radius, line_effect_schema, log

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
    "polygon": "rect",
    "pill": "ellipse",
    "hexagon": "rect",
    "diamond": "rect",
    "octagon": "rect",
    "star": "thorn",
    "spike_straight": "thorn",
    "spike_curve": "thorn-curve",
}

_EFFECT_SHAPE_ITEMS = tuple(
    (item[0], _clean_effect_shape_label(item[1]), item[2], item[3], item[4])
    for item in balloon._SHAPE_ITEMS
    if item[0] not in {"custom", "none", "uni_flash", "white_outline"}
)

_SPACING_MODE_ITEMS = (
    ("angle", "角度指定", "線の間隔を角度で指定します"),
    ("distance", "距離指定", "線の間隔を距離（mm）で指定します"),
)

_INOUT_APPLY_ITEMS = line_effect_schema.INOUT_APPLY_ITEMS
_INOUT_RANGE_MODE_ITEMS = line_effect_schema.INOUT_RANGE_MODE_ITEMS
_WHITE_OUTLINE_BLACK_DIRECTION_ITEMS = line_effect_schema.WHITE_OUTLINE_BLACK_DIRECTION_ITEMS
_LINE_IMAGE_DRAW_MODE_ITEMS = line_effect_schema.PATH_IMAGE_DRAW_MODE_ITEMS
_LINE_IMAGE_SOURCE_ITEMS = line_effect_schema.PATH_CONTENT_SOURCE_ITEMS
_LINE_IMAGE_SHAPE_ITEMS = line_effect_schema.PATH_GENERATED_SHAPE_ITEMS
_LINE_IMAGE_STAMP_ANGLE_MODE_ITEMS = line_effect_schema.PATH_IMAGE_STAMP_ANGLE_MODE_ITEMS
_LINE_IMAGE_RIBBON_REPEAT_MODE_ITEMS = line_effect_schema.PATH_IMAGE_RIBBON_REPEAT_MODE_ITEMS

_LEGACY_BASE_SHAPE_TO_EFFECT_SHAPE = {
    "rect": "rect",
    "ellipse": "ellipse",
    "polygon": "rect",
}

EFFECT_PARAM_SCHEMA_VERSION = 20
_LEGACY_DEFAULT_MAX_LINE_COUNT = 300
_DEFAULT_MAX_LINE_COUNT = 1000
_LEGACY_DEFAULT_SPEED_LINE_COUNT = 20
_DEFAULT_SPEED_LINE_COUNT = 300
_DEFAULT_IN_START_PERCENT = 0.0
_DEFAULT_OUT_START_PERCENT = 100.0

EFFECT_PARAM_FIELDS = line_effect_schema.EFFECT_PARAM_FIELDS


def _on_params_changed(self, context) -> None:
    """選択中の効果線レイヤーへ詳細設定の変更を即時反映する。

    ``scene.bmanga_effect_line_params`` (ツールの現在設定) 以外のインスタンス
    (プリセット詳細編集ダイアログのスクラッチ用等) からの変更では、選択中の
    効果線レイヤーを書き換えてはならない。ここで同一性 (as_pointer) を確認
    し、シーン本体のインスタンスでなければ即 return する。
    """
    if context is None:
        return
    scene = getattr(context, "scene", None)
    scene_params = getattr(scene, "bmanga_effect_line_params", None) if scene is not None else None
    if scene_params is not None:
        try:
            if int(self.as_pointer()) != int(scene_params.as_pointer()):
                return
        except Exception:  # noqa: BLE001
            pass
    try:
        from ..operators import effect_line_op

        effect_line_op.on_effect_params_changed(context, self)
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line params update failed")


def _on_start_corner_type_changed(self, context) -> None:
    try:
        self.start_rounded_corner_enabled = (
            str(getattr(self, "start_corner_type", "square") or "square") != "square"
        )
    except Exception:  # noqa: BLE001
        pass
    _on_params_changed(self, context)


def _on_end_corner_type_changed(self, context) -> None:
    try:
        self.end_rounded_corner_enabled = (
            str(getattr(self, "end_corner_type", "square") or "square") != "square"
        )
    except Exception:  # noqa: BLE001
        pass
    _on_params_changed(self, context)


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
    return line_effect_schema.bool_value(value)


def _inout_apply_value_from_flags(params) -> str:
    legacy = str(getattr(params, "inout_apply", "brush_size") or "brush_size")
    width = line_effect_schema.bool_value(
        getattr(params, line_effect_schema.INOUT_APPLY_BRUSH_SIZE_FIELD, None),
        legacy == "brush_size",
    )
    opacity = line_effect_schema.bool_value(
        getattr(params, line_effect_schema.INOUT_APPLY_OPACITY_FIELD, None),
        legacy == "opacity",
    )
    if width:
        return "brush_size"
    if opacity:
        return "opacity"
    return "brush_size"


def _on_inout_apply_changed(self, context) -> None:
    legacy = str(getattr(self, "inout_apply", "brush_size") or "brush_size")
    try:
        self.inout_apply_brush_size = legacy != "opacity"
        self.inout_apply_opacity = legacy == "opacity"
    except Exception:  # noqa: BLE001
        pass
    _on_params_changed(self, context)


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
    """BMangaEffectLineParams をレイヤーメタデータ保存用 dict に変換する。"""
    data = {"schema_version": EFFECT_PARAM_SCHEMA_VERSION}
    for field in EFFECT_PARAM_FIELDS:
        if not hasattr(params, field):
            continue
        value = getattr(params, field)
        if field == "spacing_density_compensation":
            spacing_mode = str(getattr(params, "spacing_mode", "") or "")
            data[field] = True if spacing_mode == "distance" else _density_compensation_enabled(value)
        elif field in {
            "line_color",
            "fill_color",
            "white_underlay_color",
            "line_image_color",
            "line_image_inout_start_color",
            "line_image_inout_end_color",
        }:
            data[field] = _color_value(value)
        elif field == "inout_apply":
            data[field] = _inout_apply_value_from_flags(params)
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
    """保存済み dict を BMangaEffectLineParams へ戻す。未知項目は無視する。"""
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
    data = line_effect_schema.normalize_inout_apply_flags(data)
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
        if schema_version < 17:
            data.setdefault("white_outline_black_in_percent", 100.0)
            data.setdefault("white_outline_black_out_percent", 100.0)
            data.setdefault("white_outline_black_inout_range_mode", "percent")
            data.setdefault("white_outline_black_in_range_percent", 100.0)
            data.setdefault("white_outline_black_out_range_percent", 100.0)
            data.setdefault("white_outline_black_in_range_mm", 10.0)
            data.setdefault("white_outline_black_out_range_mm", 10.0)
        if schema_version < 18:
            data.setdefault("white_outline_bundle_spacing_deg", 0.0)
            data.setdefault("white_outline_bundle_spacing_jitter", 0.0)
            data.setdefault("white_outline_white_spacing_scale_percent", 100.0)
            data.setdefault("white_outline_black_spacing_scale_percent", 100.0)
            data.setdefault("white_outline_white_in_easing_curve", "0.0000,0.0000;1.0000,1.0000")
            data.setdefault("white_outline_white_out_easing_curve", "0.0000,0.0000;1.0000,1.0000")
            data.setdefault("white_outline_black_in_easing_curve", "0.0000,0.0000;1.0000,1.0000")
            data.setdefault("white_outline_black_out_easing_curve", "0.0000,0.0000;1.0000,1.0000")
        if schema_version < 19:
            # 旧保存値の既定は白30%・黒70%。新規作成の50%・50%と
            # 混ざらないよう、欠落している両方を明示して復元する。
            data.setdefault("white_outline_white_ratio_percent", 30.0)
            data.setdefault("white_outline_black_ratio_percent", 70.0)
            data.setdefault("white_outline_length_percent", 100.0)
        if schema_version < 20:
            # 角タイプは旧「角丸」チェックから導出する
            for corner_prefix in ("start", "end"):
                if f"{corner_prefix}_corner_type" not in data:
                    enabled = line_effect_schema.bool_value(
                        data.get(f"{corner_prefix}_rounded_corner_enabled"), False
                    )
                    data[f"{corner_prefix}_corner_type"] = "rounded" if enabled else "square"
            data.setdefault("white_outline_bundle_placement", "spacing")
            data.setdefault("white_outline_position_percent", 100.0)
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
            if field in {
                "line_color",
                "fill_color",
                "white_underlay_color",
                "line_image_color",
                "line_image_inout_start_color",
                "line_image_inout_end_color",
            }:
                setattr(params, field, tuple(float(v) for v in value[:4]))
            else:
                setattr(params, field, value)
        except Exception:  # noqa: BLE001
            _logger.debug("effect_line param restore skipped: %s=%r", field, value)


class BMangaEffectLineParams(bpy.types.PropertyGroup):
    """効果線ツールのパラメータ (プリセット保存対象)."""

    effect_type: EnumProperty(name="種類", description="集中線・ウニフラ・ベタフラ・流線・白抜き線から効果線の種類を選びます", items=_EFFECT_TYPE_ITEMS, default="focus", update=_on_params_changed)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="全体回転", description="効果線全体を回転する角度です（度）", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]

    start_shape: EnumProperty(name="外端形状", description="線が始まる外側の形を選びます", items=_EFFECT_SHAPE_ITEMS, default="rect", update=_on_params_changed)  # type: ignore[valid-type]
    start_to_coma_frame: BoolProperty(name="外端形状をコマ枠に設定", description="オンにすると外端形状をコマ枠の形に合わせます（外端形状の詳細設定は使われなくなります）", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    start_corner_type: EnumProperty(name="角", description="外端形状の角の処理方法（角丸など）を選びます", items=balloon._CORNER_TYPE_ITEMS, default="square", update=_on_start_corner_type_changed)  # type: ignore[valid-type]
    start_rounded_corner_enabled: BoolProperty(name="角丸", description="外端形状の角を丸くします", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", description="外端形状の角を丸める半径です（mm）", default=3.0, min=0.0, soft_max=30.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_unit: EnumProperty(name="単位", description="角半径をmmと%のどちらで指定するかを選びます", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", description="外端形状の角を丸める半径です（辺の長さに対する割合）", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", description="外端形状のギザギザ（山）の幅です（mm）", default=10.0, min=2.0, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", description="外端形状の山の幅をランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", description="外端形状のギザギザ（山）の高さです（mm）", default=4.0, min=0.5, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", description="外端形状の山の高さをランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", description="外端形状の山をずらす量です（%）", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", description="外端形状の小さい山の幅を、大きい山に対する割合で指定します", default=30.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", description="外端形状の小山の幅をランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", description="外端形状の小さい山の高さを、大きい山に対する割合で指定します。0% は自動 (50%) になります", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", description="外端形状の小山の高さをランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    start_distance_enabled: BoolProperty(name="外端までの長さを指定", description="オンにすると内端形状から外端形状までの長さを指定できます。オフでは従来どおり内端形状の2倍の大きさになります", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    start_distance_mm: FloatProperty(name="長さ (mm)", description="内端形状から外端形状までの長さです（mm）", default=20.0, min=0.0, soft_max=300.0, update=_on_params_changed)  # type: ignore[valid-type]

    end_shape: EnumProperty(name="内端形状", description="線が向かう内側の形を選びます", items=_EFFECT_SHAPE_ITEMS, default="ellipse", update=_on_params_changed)  # type: ignore[valid-type]
    end_corner_type: EnumProperty(name="角", description="内端形状の角の処理方法（角丸など）を選びます", items=balloon._CORNER_TYPE_ITEMS, default="square", update=_on_end_corner_type_changed)  # type: ignore[valid-type]
    end_rounded_corner_enabled: BoolProperty(name="角丸", description="内端形状の角を丸くします", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", description="内端形状の角を丸める半径です（mm）", default=3.0, min=0.0, soft_max=30.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_unit: EnumProperty(name="単位", description="角半径をmmと%のどちらで指定するかを選びます", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", description="内端形状の角を丸める半径です（辺の長さに対する割合）", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", description="内端形状のギザギザ（山）の幅です（mm）", default=10.0, min=2.0, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", description="内端形状の山の幅をランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", description="内端形状のギザギザ（山）の高さです（mm）", default=4.0, min=0.5, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", description="内端形状の山の高さをランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", description="内端形状の山をずらす量です（%）", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", description="内端形状の小さい山の幅を、大きい山に対する割合で指定します", default=30.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", description="内端形状の小山の幅をランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", description="内端形状の小さい山の高さを、大きい山に対する割合で指定します。0% は自動 (50%) になります", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", description="内端形状の小山の高さをランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]

    brush_size_mm: FloatProperty(name="線幅 (mm)", description="線の太さです（mm）", default=0.30, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    base_path_enabled: BoolProperty(name="基準パスを編集", description="オンにすると、放射方向の代わりに自分で描いた基準パスに沿って線を配置します。保存済み基準パスの編集は効果線ツールから行います", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    base_path_points_json: StringProperty(name="基準パス", default="", options={"HIDDEN"}, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_source: EnumProperty(name="内容", description="パス線に使う内容を画像ファイルか生成形状かで選びます", items=_LINE_IMAGE_SOURCE_ITEMS, default="image", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_path: StringProperty(name="画像", description="パス線に使う画像ファイルのパスです", default="", subtype="FILE_PATH", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_shape_kind: EnumProperty(name="生成形状", description="パス線に使う生成形状の種類を選びます", items=_LINE_IMAGE_SHAPE_ITEMS, default="circle", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_shape_sides: IntProperty(name="角数", description="多角形の角の数です", default=6, min=3, max=16, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_color: FloatVectorProperty(name="色", description="パス線に使う画像・生成形状の色です", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_draw_mode: EnumProperty(name="画像の表示方法", description="パスに沿って画像をスタンプ状に並べるか、リボン状に変形するかを選びます", items=_LINE_IMAGE_DRAW_MODE_ITEMS, default="ribbon", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_brush_size_mm: FloatProperty(name="画像ブラシサイズ", description="パス線の画像・生成形状の大きさです（mm）", default=3.0, min=0.1, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_aspect_ratio: FloatProperty(name="画像の縦横比", description="パス線の画像・生成形状の縦横比です", default=1.0, min=0.01, soft_min=0.1, soft_max=10.0, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_angle_deg: FloatProperty(name="画像の角度", description="パス線の画像・生成形状の角度です", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_spacing_percent: FloatProperty(name="画像の間隔 (%)", description="パスに並べる画像・生成形状の間隔です（ブラシサイズに対する割合）", default=100.0, min=1.0, soft_max=400.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_stamp_angle_mode: EnumProperty(name="画像の角度", description="スタンプ表示時の画像の向きを、固定角度・線の向き・指定オブジェクトの向きから選びます", items=_LINE_IMAGE_STAMP_ANGLE_MODE_ITEMS, default="line", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_stamp_angle_object_name: StringProperty(name="方向オブジェクト", description="画像の向きの基準にするオブジェクトの名前です", default="", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_ribbon_repeat_mode: EnumProperty(name="リボン", description="リボン表示時に、画像をブラシサイズ基準で繰り返すか、始点から終点まで1枚を伸ばすかを選びます", items=_LINE_IMAGE_RIBBON_REPEAT_MODE_ITEMS, default="repeat", update=_on_params_changed)  # type: ignore[valid-type]
    line_image_inout_size_enabled: BoolProperty(name="サイズ", description="入り抜きでパス線のサイズを変化させます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_inout_opacity_enabled: BoolProperty(name="不透明度", description="入り抜きでパス線の不透明度を変化させます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_inout_color_enabled: BoolProperty(name="色", description="入り抜きでパス線の色を変化させます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_inout_start_color: FloatVectorProperty(name="入り色", description="入り側で使うパス線の色です", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    line_image_inout_end_color: FloatVectorProperty(name="抜き色", description="抜き側で使うパス線の色です", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    brush_jitter_enabled: BoolProperty(name="乱れ", description="線の太さをランダムに変化させます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    brush_jitter_amount: FloatProperty(name="乱れ量", description="線の太さをランダムに変化させる度合いです", default=0.2, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    length_jitter_enabled: BoolProperty(name="外端乱れ", description="外端側の長さをランダムに短くします", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    length_jitter_amount: FloatProperty(name="外端乱れ (%)", description="外端側を短くする最大量です（線の長さに対する割合）", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    end_length_jitter_enabled: BoolProperty(name="内端乱れ", description="内端側の長さをランダムに短くします", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    end_length_jitter_amount: FloatProperty(name="内端乱れ (%)", description="内端側を短くする最大量です（線の長さに対する割合）", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]

    spacing_mode: EnumProperty(name="線の間隔", description="線の間隔を角度で指定するか、距離（mm）で指定するかを選びます", items=_SPACING_MODE_ITEMS, default="distance", update=_on_params_changed)  # type: ignore[valid-type]
    spacing_angle_deg: FloatProperty(name="線の間隔 (角度)", description="線と線の間隔です（度単位）", default=5.0, min=0.1, soft_max=90.0, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_distance_mm: FloatProperty(name="線の間隔 (距離 mm)", description="線と線の間隔です（mm単位）", default=1.00, min=0.01, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_density_compensation: BoolProperty(name="密度補正", description="中心からの距離によって線の密度が変わらないよう補正します", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_jitter_enabled: BoolProperty(name="乱れ", description="線の間隔をランダムに変化させます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_jitter_amount: FloatProperty(name="間隔乱れ量", description="線の間隔をランダムに変化させる度合いです", default=0.2, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    max_line_count: IntProperty(name="最大本数", description="生成する線の本数の上限です", default=_DEFAULT_MAX_LINE_COUNT, min=1, soft_max=2000, update=_on_params_changed)  # type: ignore[valid-type]

    bundle_enabled: BoolProperty(name="まとまり", description="線を数本ずつのまとまり（房）にして、間に隙間を作ります", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_line_count: IntProperty(name="数", description="まとまり1つあたりの線の本数です", default=5, min=1, soft_max=50, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_line_count_jitter: FloatProperty(name="数の乱れ", description="まとまりの本数をランダムに変化させる度合いです", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    bundle_gap_mm: FloatProperty(name="まとまり間隔 (mm)", description="まとまりとまとまりの間の隙間です（mm）", default=5.0, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_gap_jitter_amount: FloatProperty(name="まとまり間隔の乱れ", description="まとまりの間隔をランダムに変化させる度合いです", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    bundle_jagged_enabled: BoolProperty(name="ギザギザにする", description="まとまりの外側の線ほど外端側を短くして、房の端をギザギザにします", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_jagged_height_percent: FloatProperty(name="ギザギザ高さ (%)", description="まとまりのギザギザの高さです（線の長さに対する割合）", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]

    inout_apply: EnumProperty(name="適用先", description="入り抜きを線幅と不透明度のどちらに適用するかを選びます（旧設定）", items=_INOUT_APPLY_ITEMS, default="brush_size", update=_on_inout_apply_changed)  # type: ignore[valid-type]
    inout_apply_brush_size: BoolProperty(name="線幅", description="入り抜きを線幅に適用します", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    inout_apply_opacity: BoolProperty(name="不透明度", description="入り抜きを不透明度に適用します", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    in_percent: FloatProperty(name="入り (%)", description="入り側（外端側）の線幅・不透明度です（100% で細くしません）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    out_percent: FloatProperty(name="抜き (%)", description="抜き側（内端側）の線幅・不透明度です（100% で細くしません）", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    in_start_percent: FloatProperty(name="外端側グラフ位置", default=_DEFAULT_IN_START_PERCENT, min=0.0, max=100.0, options={"HIDDEN"}, update=_on_in_start_changed)  # type: ignore[valid-type]
    out_start_percent: FloatProperty(name="内端側グラフ位置", default=_DEFAULT_OUT_START_PERCENT, min=0.0, max=100.0, options={"HIDDEN"}, update=_on_out_start_changed)  # type: ignore[valid-type]
    in_easing_curve: bpy.props.StringProperty(name="入りカーブ", description="入りの変化のかかり方を調整するカーブです", default="0.0000,0.0000;1.0000,1.0000", update=_on_params_changed)  # type: ignore[valid-type]
    out_easing_curve: bpy.props.StringProperty(name="抜きカーブ", description="抜きの変化のかかり方を調整するカーブです", default="0.0000,0.0000;1.0000,1.0000", update=_on_params_changed)  # type: ignore[valid-type]
    inout_range_mode: EnumProperty(name="範囲", description="入り抜きの範囲を割合（%）で指定するか、長さ（mm）で指定するかを選びます（旧設定）", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_params_changed)  # type: ignore[valid-type]
    in_range_percent: FloatProperty(name="入りの範囲 (%)", description="外端からこの割合の長さを入りの変化区間にする", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    out_range_percent: FloatProperty(name="抜きの範囲 (%)", description="内端からこの割合の長さを抜きの変化区間にする", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    in_range_mm: FloatProperty(name="入りの範囲 (mm)", description="外端からこの長さを入りの変化区間にする", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    out_range_mm: FloatProperty(name="抜きの範囲 (mm)", description="内端からこの長さを抜きの変化区間にする", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]

    opacity: FloatProperty(name="不透明度", description="効果線全体の不透明度です", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    line_color: FloatVectorProperty(name="線色", description="線の色です", subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(name="塗り色", description="塗りの色です", subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    fill_opacity: FloatProperty(name="塗り不透明度", description="塗りの不透明度です", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    fill_base_shape: BoolProperty(name="内端形状を下地として塗る", description="内端形状の内側を塗りつぶします", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_underlay_enabled: BoolProperty(name="白抜き", description="線の下に白い縁取りを敷いて、線を目立たせます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_underlay_width_percent: FloatProperty(name="白抜き幅 (%)", description="白い縁取りの幅です（線の太さに対する割合）", default=150.0, min=-300.0, max=300.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_underlay_color: FloatVectorProperty(name="白抜き色", description="白い縁取りの色です", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    # ウニフラ固有: 線の終点を交互に出し入れする量 (50% = 従来の固定量)
    uni_flash_offset_percent: FloatProperty(name="ズラし量 (%)", description="線の内端を交互に出し入れして、長さをずらします", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]

    # 流線固有
    speed_angle_deg: FloatProperty(name="流線の角度", description="流線が流れる方向の角度です", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]
    speed_line_count: IntProperty(name="流線の本数上限", description="流線の本数の上限です", default=_DEFAULT_SPEED_LINE_COUNT, min=1, soft_max=1000, update=_on_params_changed)  # type: ignore[valid-type]

    # 白抜き線固有
    white_outline_count: IntProperty(name="束の数", description="束（白線と黒線のまとまり）の数です", default=5, min=1, soft_max=100, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_bundle_placement: EnumProperty(name="束の配置", description="束を配置する基準を、間隔指定・内端形状の角・角と角の中間から選びます", items=line_effect_schema.WHITE_OUTLINE_BUNDLE_PLACEMENT_ITEMS, default="spacing", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_bundle_spacing_deg: FloatProperty(name="束の間隔 (角度)", description="0 の場合は全周へ等間隔に配置します", default=0.0, min=0.0, max=360.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_bundle_spacing_jitter: FloatProperty(name="間隔乱れ", description="束の間隔をランダムに変化させる度合いです", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_position_percent: FloatProperty(name="位置 (%)", description="内端形状に対する位置。100% で線の長さ分ぴったり内端形状の外側、0% で線の中心が内端形状上", default=100.0, min=-200.0, max=200.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_spacing_mm: FloatProperty(name="間隔 (mm)", description="白線どうしの間隔です（mm）", default=0.2, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_spacing_scale_percent: FloatProperty(name="間隔変化 (%)", description="束の外側ほど白線の間隔をどれだけ広げるかです（%）", default=100.0, min=0.0, max=500.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_line_count_auto: BoolProperty(name="本数を自動計算", description="白線の本数を束の太さから自動計算します", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_line_count: IntProperty(name="本数", description="白線の本数です（自動計算をオフにした場合に使います）", default=24, min=1, soft_max=200, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_mm: FloatProperty(name="束の太さ (mm)", description="束（白線と黒線）全体の太さです（mm）", default=10.0, min=0.01, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_jitter_enabled: BoolProperty(name="太さ乱れ", description="束ごとに太さをランダムに変化させます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_min_percent: FloatProperty(name="最小値 (%)", description="束の太さをランダムに変化させる下限です（基準の太さに対する割合）", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_length_jitter_enabled: BoolProperty(name="長さ乱れ", description="束ごとに長さをランダムに変化させます", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_length_min_percent: FloatProperty(name="最小値 (%)", description="束の長さをランダムに変化させる下限です（基準の長さに対する割合）", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_length_percent: FloatProperty(name="長さ (%)", description="100% で内端形状まで伸ばし、小さくするほど内端形状から離れます", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_ratio_percent: FloatProperty(name="白線割合 (%)", description="束の太さのうち、白線が占める割合です", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_ratio_percent: FloatProperty(name="黒線割合 (%)", description="束の太さのうち左右の黒線領域に使う割合", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_brush_mm: FloatProperty(name="太さ (mm)", description="白線1本の太さです（mm）", default=0.3, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_attenuation: FloatProperty(name="減衰", description="束の中心から外側の白線ほど、線を短くする度合いです。マイナスは元の長さまで伸ばします (長さ100%では変化しません)", default=0.0, min=-100.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_in_percent: FloatProperty(name="入り (%)", description="白線の入り側（外端側）の線幅です（100% で細くしません）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_out_percent: FloatProperty(name="抜き (%)", description="白線の抜き側（内端側）の線幅です（100% で細くしません）", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_inout_range_mode: EnumProperty(name="入り抜き範囲", description="白線の入り抜きの範囲を割合（%）で指定するか、長さ（mm）で指定するかを選びます", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_in_range_percent: FloatProperty(name="入り範囲 (%)", description="白線の入り側の変化区間です（線の長さに対する割合）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_out_range_percent: FloatProperty(name="抜き範囲 (%)", description="白線の抜き側の変化区間です（線の長さに対する割合）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_in_range_mm: FloatProperty(name="入り範囲 (mm)", description="白線の入り側の変化区間です（mm）", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_out_range_mm: FloatProperty(name="抜き範囲 (mm)", description="白線の抜き側の変化区間です（mm）", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_in_easing_curve: StringProperty(name="入りカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_out_easing_curve: StringProperty(name="抜きカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_line_count_auto: BoolProperty(name="本数を自動計算", description="黒線の本数を自動計算します", default=True, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_line_count: IntProperty(name="本数", description="黒線の本数です（自動計算をオフにした場合に使います）", default=3, min=1, soft_max=50, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_direction: EnumProperty(name="重ねる方向", description="黒線を白線群の外側・内側・両側のどこに重ねるかを選びます", items=_WHITE_OUTLINE_BLACK_DIRECTION_ITEMS, default="outside", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_brush_mm: FloatProperty(name="太さ (mm)", description="黒線1本の太さです（mm）", default=0.3, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_spacing_mm: FloatProperty(name="間隔 (mm)", description="黒線どうしの間隔です（mm）", default=0.2, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_spacing_scale_percent: FloatProperty(name="間隔変化 (%)", description="束の外側ほど黒線の間隔をどれだけ広げるかです（%）", default=100.0, min=0.0, max=500.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_width_scale_percent: FloatProperty(name="幅変化 (%)", description="束の外側の黒線ほど太さをどれだけ変えるかです（%）", default=100.0, min=0.0, max=300.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_near_percent: FloatProperty(name="長さ変化 (内側)", description="内端形状に近い側の黒線の長さです（基準の長さに対する割合）。100%超は長さを縮めている場合に元の長さまで伸ばします", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_far_percent: FloatProperty(name="長さ変化 (外側)", description="内端形状から遠い側の黒線の長さです（基準の長さに対する割合）。100%超は長さを縮めている場合に元の長さまで伸ばします", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_attenuation: FloatProperty(name="減衰", description="領域の端の黒線ほど、線を短くする度合いです。マイナスは元の長さまで伸ばします (長さ100%では変化しません)", default=0.0, min=-100.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_in_percent: FloatProperty(name="入り (%)", description="黒線の入り側（外端側）の線幅です（100% で細くしません）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_out_percent: FloatProperty(name="抜き (%)", description="黒線の抜き側（内端側）の線幅です（100% で細くしません）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_inout_range_mode: EnumProperty(name="入り抜き範囲", description="黒線の入り抜きの範囲を割合（%）で指定するか、長さ（mm）で指定するかを選びます", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_in_range_percent: FloatProperty(name="入り範囲 (%)", description="黒線の入り側の変化区間です（線の長さに対する割合）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_out_range_percent: FloatProperty(name="抜き範囲 (%)", description="黒線の抜き側の変化区間です（線の長さに対する割合）", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_in_range_mm: FloatProperty(name="入り範囲 (mm)", description="黒線の入り側の変化区間です（mm）", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_out_range_mm: FloatProperty(name="抜き範囲 (mm)", description="黒線の抜き側の変化区間です（mm）", default=10.0, min=0.0, soft_max=200.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_in_easing_curve: StringProperty(name="入りカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_out_easing_curve: StringProperty(name="抜きカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_angle_deg: FloatProperty(name="角度", description="束の配置に使う基準角度です（度）", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_params_changed)  # type: ignore[valid-type]


_CLASSES = (BMangaEffectLineParams,)


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
