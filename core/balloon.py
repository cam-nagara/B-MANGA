"""フキダシ (Balloon) の PropertyGroup.

計画書 3.1.4 参照。Meldex ボードカード互換の形状プリセット +
角丸オプション + 尻尾 3 種 + カスタム形状参照。

描画ロジックは ui/overlay.py および書き出し側 (Phase 6) で扱う。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import corner_radius, line_effect_schema, log

_logger = log.get_logger(__name__)


_SHAPE_ITEMS = (
    ("rect", "矩形", "Meldex ボードカードと同じ矩形"),
    ("ellipse", "楕円", "Meldex ボードカードと同じ楕円"),
    ("cloud", "雲", "Meldex ボードカードと同じ雲形"),
    ("fluffy", "もやもや", "Meldex ボードカードと同じもやもや形"),
    ("thorn", "トゲ（直線）", "Meldex ボードカードと同じ直線トゲ形"),
    ("thorn-curve", "トゲ（曲線）", "Meldex ボードカードと同じ曲線トゲ形"),
    ("octagon", "八角形", "Meldex ボードカードと同じ八角形"),
    ("custom", "カスタム", "カスタム形状プリセット参照"),
    ("none", "本体なし", "テキスト単体 (擬音/ナレーション用)"),
)

_TAIL_TYPE_ITEMS = (
    ("straight", "直線", "三角形の直線状尻尾"),
    ("curve", "曲線", "ベジェで膨らませた曲線状尻尾"),
    ("sticky", "付箋", "矩形タブ状の尻尾"),
)

_TAIL_POINT_CORNER_ITEMS = (
    ("line", "直線", "角を直線でつなぐ"),
    ("curve", "曲線", "角を曲線でつなぐ"),
)

_TAIL_CURVE_MODE_ITEMS = (
    ("polyline", "折れ線", "ポイントを直線でつなぐ"),
    ("curve", "曲線", "ポイントをなめらかな曲線でつなぐ"),
)

_TAIL_LINE_TYPE_ITEMS = (
    ("wedge", "三角", "根元から先端へ細くなる従来のしっぽ"),
    ("ellipse_chain", "楕円", "心の声のように、先端へ向かって小さくなる楕円を連ねる"),
    ("line", "線", "1本の線を描く (入り抜きを設定可能)"),
)

_TAIL_ELLIPSE_ORIENT_ITEMS = (
    ("start_end", "始点終点", "しっぽの始点と終点を結ぶ直線に全楕円の角度を揃える"),
    ("line", "線の向き", "しっぽの線 (カーブ) の進行方向に各楕円を沿わせる"),
    ("fixed", "固定", "線の向きに関わらず、どの楕円も一律の角度にする"),
)

_LINE_STYLE_ITEMS = (
    ("none", "線なし", ""),
    ("solid", "実線", ""),
    ("dashed", "破線", ""),
    ("dotted", "点線", ""),
    ("double", "多重線", ""),
    ("uni_flash", "ウニフラ", "フキダシの形状に沿って放射状の線を並べる"),
    ("white_outline", "白抜き線", "フキダシの形状に沿って白抜き線を放射状に並べる"),
    ("shape", "図形", "●や★などの図形を線に沿って連続配置する"),
    ("image", "画像", "画像を線に沿って引き延ばして描く"),
    ("material", "マテリアル", "線の帯をマテリアルで塗る (フキダシの領域基準で貼るため、閉じた形でも切れ目が出ない)"),
)

_LINE_SHAPE_ORIENT_ITEMS = (
    ("line", "線の向き", "線の進行方向に沿って図形を回転させる"),
    ("center", "中心点", "常にフキダシの中心点の方向を向かせる"),
)

_LINE_SHAPE_KIND_ITEMS = (
    ("circle", "● 丸", ""),
    ("star", "★ 星", ""),
    ("triangle", "▲ 三角", ""),
    ("diamond", "◆ ひし形", ""),
    ("heart", "♥ ハート", ""),
)

_CORNER_TYPE_ITEMS = (
    ("square", "直角", ""),
    ("rounded", "丸角", ""),
    ("bevel", "面取り", ""),
)

_MULTI_LINE_DIRECTION_ITEMS = (
    ("outside", "外側", ""),
    ("inside", "内側", ""),
    ("both", "両方向", ""),
)

_FILL_BLUR_AXIS_ITEMS = (
    ("inside", "内側", "輪郭から内側だけへぼかす"),
    ("center", "輪郭", "輪郭を中心に内外へぼかす"),
    ("outside", "外側", "輪郭から外側だけへぼかす"),
)

_BLEND_MODE_ITEMS = (
    ("normal", "通常", ""),
    ("lighten", "比較 (明)", ""),
)

_FLASH_LINE_STYLE_IDS = {"uni_flash", "white_outline"}
_UNI_FLASH_EFFECT_TYPE_ITEMS = (
    ("uni_flash", "ウニフラ", "フキダシの形状に沿って放射状の線を並べる"),
)
_EFFECT_SHAPE_ITEMS = tuple(
    (item[0], item[1], item[2])
    for item in _SHAPE_ITEMS
    if item[0] not in {"custom", "none"}
)
_SPACING_MODE_ITEMS = (
    ("angle", "角度指定", ""),
    ("distance", "距離指定", ""),
)
_INOUT_APPLY_ITEMS = line_effect_schema.INOUT_APPLY_ITEMS
_INOUT_RANGE_MODE_ITEMS = line_effect_schema.INOUT_RANGE_MODE_ITEMS
UNI_FLASH_PARAM_FIELDS = line_effect_schema.BALLOON_UNI_FLASH_PARAM_FIELDS


def _color_value(value) -> list[float]:
    try:
        return [float(value[i]) for i in range(4)]
    except Exception:  # noqa: BLE001
        return [0.0, 0.0, 0.0, 1.0]


def uni_flash_params_to_dict(entry) -> dict:
    data = {}
    for field in UNI_FLASH_PARAM_FIELDS:
        if not hasattr(entry, field):
            continue
        value = getattr(entry, field)
        if field in {"line_color", "fill_color", "white_underlay_color"}:
            data[field] = _color_value(value)
        elif field == "inout_apply":
            data[field] = _inout_apply_value_from_flags(entry)
        elif isinstance(value, bool):
            data[field] = bool(value)
        elif isinstance(value, int):
            data[field] = int(value)
        elif isinstance(value, float):
            data[field] = float(value)
        else:
            data[field] = str(value)
    data["effect_type"] = "uni_flash"
    return data


def uni_flash_params_from_dict(entry, data: dict) -> None:
    if entry is None or not isinstance(data, dict):
        return
    data = line_effect_schema.normalize_inout_apply_flags(data)
    for field in UNI_FLASH_PARAM_FIELDS:
        if field == "effect_type" or not hasattr(entry, field) or field not in data:
            continue
        try:
            setattr(entry, field, data[field])
        except Exception:  # noqa: BLE001
            pass
    try:
        entry.effect_type = "uni_flash"
    except Exception:  # noqa: BLE001
        pass


def _tag_balloon_redraw(context) -> None:
    try:
        screen = getattr(context, "screen", None) if context is not None else None
        if screen is not None:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def _sync_balloon_curve(entry) -> None:
    try:
        from ..utils import balloon_curve_object

        balloon_curve_object.on_balloon_entry_changed(entry)
    except Exception:  # noqa: BLE001
        pass


def _sync_linked_balloon_transform(entry, context) -> None:
    try:
        from ..operators import layer_link_duplicate_op
        from ..utils import balloon_curve_object

        if balloon_curve_object.auto_sync_is_paused():
            return
        scene = getattr(context, "scene", None) if context is not None else None
        if scene is None:
            return
        page, resolved = balloon_curve_object.find_balloon_entry(scene, str(getattr(entry, "id", "") or ""))
        if resolved is None:
            return
        layer_link_duplicate_op.propagate_linked_balloon_transform_absolute(context, page, resolved)
    except Exception:  # noqa: BLE001
        pass


def _on_balloon_entry_changed(_self, context) -> None:
    _sync_balloon_curve(_self)
    _sync_linked_balloon_transform(_self, context)
    _tag_balloon_redraw(context)


def _inout_apply_value_from_flags(entry) -> str:
    legacy = str(getattr(entry, "inout_apply", "brush_size") or "brush_size")
    width = line_effect_schema.bool_value(
        getattr(entry, line_effect_schema.INOUT_APPLY_BRUSH_SIZE_FIELD, None),
        legacy == "brush_size",
    )
    opacity = line_effect_schema.bool_value(
        getattr(entry, line_effect_schema.INOUT_APPLY_OPACITY_FIELD, None),
        legacy == "opacity",
    )
    if width:
        return "brush_size"
    if opacity:
        return "opacity"
    return "brush_size"


def _on_balloon_inout_apply_changed(self, context) -> None:
    legacy = str(getattr(self, "inout_apply", "brush_size") or "brush_size")
    try:
        self.inout_apply_brush_size = legacy != "opacity"
        self.inout_apply_opacity = legacy == "opacity"
    except Exception:  # noqa: BLE001
        pass
    _on_balloon_entry_changed(self, context)


def apply_balloon_line_style_defaults(entry, *, force: bool = False) -> None:
    """線種ごとの初期線幅を既存設定を壊さない範囲で適用する."""

    if entry is None or str(getattr(entry, "line_style", "") or "") not in _FLASH_LINE_STYLE_IDS:
        return

    def _set_if_default(attr: str, value: float, default: float = 100.0) -> None:
        try:
            current = float(getattr(entry, attr, default))
        except Exception:  # noqa: BLE001
            current = default
        if force or abs(current - default) < 1.0e-6:
            try:
                setattr(entry, attr, float(value))
            except Exception:  # noqa: BLE001
                pass

    def _set_bool_if_default(attr: str, value: bool, default: bool = False) -> None:
        try:
            current = bool(getattr(entry, attr, default))
        except Exception:  # noqa: BLE001
            current = bool(default)
        if force or current == bool(default):
            try:
                setattr(entry, attr, bool(value))
            except Exception:  # noqa: BLE001
                pass

    _set_if_default("line_valley_width_pct", 0.0)
    _set_if_default("line_peak_width_pct", 100.0)
    _set_if_default("thorn_multi_line_valley_width_pct", 0.0)
    _set_if_default("thorn_multi_line_peak_width_pct", 100.0)
    _set_if_default("brush_size_mm", float(getattr(entry, "line_width_mm", 0.3) or 0.3), default=0.3)
    _set_if_default("in_percent", 0.0, default=100.0)
    _set_if_default("out_percent", 0.0, default=0.0)
    _set_if_default("in_start_percent", 50.0, default=0.0)
    _set_if_default("out_start_percent", 50.0, default=100.0)
    _set_if_default("white_underlay_width_percent", 100.0, default=150.0)
    _set_if_default("flash_white_line_width_percent", 100.0)
    _set_if_default("flash_white_line_valley_width_pct", 0.0)
    _set_if_default("flash_white_line_peak_width_pct", 100.0)
    if str(getattr(entry, "line_style", "") or "") == "uni_flash":
        _set_bool_if_default("length_jitter_enabled", True, default=False)
    try:
        if force or not bool(getattr(entry, "flash_white_line_enabled", True)):
            entry.flash_white_line_enabled = True
    except Exception:  # noqa: BLE001
        pass
    try:
        entry.effect_type = "uni_flash"
        # 白抜き線の初期値はオフ (ユーザーが明示的にオンにしたものは保持)
        if force:
            entry.white_underlay_enabled = False
    except Exception:  # noqa: BLE001
        pass


def apply_balloon_shape_defaults(entry, *, force: bool = False) -> None:
    """後方互換: 旧呼び出し元からも線種別初期値を適用する."""

    apply_balloon_line_style_defaults(entry, force=force)


def _on_balloon_shape_changed(_self, context) -> None:
    _on_balloon_entry_changed(_self, context)


def _on_balloon_line_style_changed(_self, context) -> None:
    apply_balloon_line_style_defaults(_self)
    _on_balloon_entry_changed(_self, context)


def _on_balloon_corner_type_changed(_self, context) -> None:
    try:
        _self.corner_type_initialized = True
        _self.rounded_corner_enabled = str(getattr(_self, "corner_type", "square") or "square") != "square"
    except Exception:  # noqa: BLE001
        pass
    _on_balloon_entry_changed(_self, context)


def ensure_balloon_corner_type_initialized(entry) -> None:
    if entry is None or bool(getattr(entry, "corner_type_initialized", False)):
        return
    try:
        entry.corner_type = "rounded" if bool(getattr(entry, "rounded_corner_enabled", False)) else "square"
        entry.corner_type_initialized = True
    except Exception:  # noqa: BLE001
        pass


def _sync_layer_stack_title(context, entry=None) -> None:
    if entry is not None and not str(getattr(entry, "id", "") or "").strip():
        return
    try:
        from ..utils import layer_stack as layer_stack_utils

        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        pass


def _on_balloon_title_changed(_self, context) -> None:
    _sync_balloon_curve(_self)
    _sync_layer_stack_title(context, _self)
    _tag_balloon_redraw(context)


def _on_balloon_tail_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        _tag_balloon_redraw(context)
        return
    try:
        target_ptr = int(_self.as_pointer())
    except Exception:  # noqa: BLE001
        target_ptr = 0
    for page in getattr(work, "pages", []) or []:
        for entry in getattr(page, "balloons", []) or []:
            for tail in getattr(entry, "tails", []) or []:
                try:
                    if int(tail.as_pointer()) != target_ptr:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                _sync_balloon_curve(entry)
                _tag_balloon_redraw(context)
                return
    for entry in getattr(work, "shared_balloons", []) or []:
        for tail in getattr(entry, "tails", []) or []:
            try:
                if int(tail.as_pointer()) != target_ptr:
                    continue
            except Exception:  # noqa: BLE001
                continue
            _sync_balloon_curve(entry)
            _tag_balloon_redraw(context)
            return
    _tag_balloon_redraw(context)


def _on_balloon_tail_point_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        _tag_balloon_redraw(context)
        return
    try:
        target_ptr = int(_self.as_pointer())
    except Exception:  # noqa: BLE001
        target_ptr = 0
    if not target_ptr:
        _tag_balloon_redraw(context)
        return

    def _tail_has_point(tail) -> bool:
        for point in getattr(tail, "points", []) or []:
            try:
                if int(point.as_pointer()) == target_ptr:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    for page in getattr(work, "pages", []) or []:
        for entry in getattr(page, "balloons", []) or []:
            for tail in getattr(entry, "tails", []) or []:
                if _tail_has_point(tail):
                    _sync_balloon_curve(entry)
                    _tag_balloon_redraw(context)
                    return
    for entry in getattr(work, "shared_balloons", []) or []:
        for tail in getattr(entry, "tails", []) or []:
            if _tail_has_point(tail):
                _sync_balloon_curve(entry)
                _tag_balloon_redraw(context)
                return
    _tag_balloon_redraw(context)


def _on_balloon_shape_params_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        _tag_balloon_redraw(context)
        return
    try:
        target_ptr = int(_self.as_pointer())
    except Exception:  # noqa: BLE001
        target_ptr = 0
    for page in getattr(work, "pages", []) or []:
        for entry in getattr(page, "balloons", []) or []:
            try:
                if int(getattr(entry, "shape_params").as_pointer()) != target_ptr:
                    continue
            except Exception:  # noqa: BLE001
                continue
            _sync_balloon_curve(entry)
            _tag_balloon_redraw(context)
            return
    for entry in getattr(work, "shared_balloons", []) or []:
        try:
            if int(getattr(entry, "shape_params").as_pointer()) != target_ptr:
                continue
        except Exception:  # noqa: BLE001
            continue
        _sync_balloon_curve(entry)
        _tag_balloon_redraw(context)
        return
    _tag_balloon_redraw(context)


class BMangaBalloonTailPoint(bpy.types.PropertyGroup):
    x_mm: FloatProperty(name="X", default=0.0, update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0, update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]
    corner_type: EnumProperty(name="角のタイプ", items=_TAIL_POINT_CORNER_ITEMS, default="line", update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]


class BMangaBalloonTail(bpy.types.PropertyGroup):
    type: EnumProperty(items=_TAIL_TYPE_ITEMS, default="straight", update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    curve_mode: EnumProperty(  # type: ignore[valid-type]
        name="線のつなぎ",
        description="ポイントを折れ線でつなぐか、なめらかな曲線でつなぐか",
        items=_TAIL_CURVE_MODE_ITEMS,
        default="polyline",
        update=_on_balloon_tail_changed,
    )
    line_type: EnumProperty(  # type: ignore[valid-type]
        name="線種",
        description="しっぽの描き方 (三角のくさび / 連続する楕円)",
        items=_TAIL_LINE_TYPE_ITEMS,
        default="wedge",
        update=_on_balloon_tail_changed,
    )
    ellipse_gap_mm: FloatProperty(  # type: ignore[valid-type]
        name="楕円の間隔 (mm)",
        description="線種が楕円のとき、楕円どうしの間隔",
        default=1.5,
        min=0.0,
        soft_max=20.0,
        update=_on_balloon_tail_changed,
    )
    ellipse_angle_deg: FloatProperty(  # type: ignore[valid-type]
        name="楕円の角度 (度)",
        description="連続スタンプする楕円 1 つ 1 つの追加回転角",
        default=0.0,
        soft_min=-360.0,
        soft_max=360.0,
        update=_on_balloon_tail_changed,
    )
    ellipse_orient: EnumProperty(  # type: ignore[valid-type]
        name="楕円の向き",
        description="しっぽのカーブに対して楕円をどう連動させるか",
        items=_TAIL_ELLIPSE_ORIENT_ITEMS,
        default="start_end",
        update=_on_balloon_tail_changed,
    )
    sharp_corners: BoolProperty(  # type: ignore[valid-type]
        name="角を尖らせる",
        description="しっぽの角 (先端や折れ角) を鋭く尖らせる (OFF: 線幅分だけ丸まる)",
        default=True,
        update=_on_balloon_tail_changed,
    )
    taper_in_percent: FloatProperty(  # type: ignore[valid-type]
        name="入り (%)",
        description="線種が線のとき、根元側から細く入る範囲 (しっぽの長さに対する割合)",
        default=0.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_balloon_tail_changed,
    )
    taper_out_percent: FloatProperty(  # type: ignore[valid-type]
        name="抜き (%)",
        description="線種が線のとき、先端側へ細く抜ける範囲 (しっぽの長さに対する割合)",
        default=0.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_balloon_tail_changed,
    )
    direction_deg: FloatProperty(name="方向 (度)", default=270.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    length_mm: FloatProperty(name="長さ (mm)", default=6.0, min=0.0, soft_max=50.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    root_width_mm: FloatProperty(name="根元幅 (mm)", default=3.0, min=0.0, soft_max=20.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    tip_width_mm: FloatProperty(name="先端幅 (mm)", default=0.0, min=0.0, soft_max=20.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    curve_bend: FloatProperty(  # type: ignore[valid-type]
        name="曲げ",
        description="曲線尻尾のみ: -1.0〜1.0 で曲がり具合",
        default=0.0,
        soft_min=-1.0,
        soft_max=1.0,
        update=_on_balloon_tail_changed,
    )
    custom_points_enabled: BoolProperty(name="始点・終点を固定", default=False, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    start_x_mm: FloatProperty(name="始点 X", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    start_y_mm: FloatProperty(name="始点 Y", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    end_x_mm: FloatProperty(name="終点 X", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    end_y_mm: FloatProperty(name="終点 Y", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    points: CollectionProperty(type=BMangaBalloonTailPoint)  # type: ignore[valid-type]


class BMangaBalloonShapeParams(bpy.types.PropertyGroup):
    """形状固有パラメータ."""

    cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", default=10.0, min=2.0, soft_max=200.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", default=4.0, min=0.5, soft_max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_offset_percent: FloatProperty(name="ズラし量 (%)", default=50.0, min=0.0, max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    shape_seed: IntProperty(name="シード", default=0, min=0, soft_max=9999, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_valley_sharp: BoolProperty(  # type: ignore[valid-type]
        name="角を尖らせる",
        description="フキダシ主線の角 (山と谷) を鋭く尖らせる (OFF: 滑らかに丸める). 全形状で有効",
        default=False,
        update=_on_balloon_shape_params_changed,
    )
    dynamic_shape_base_kind: EnumProperty(  # type: ignore[valid-type]
        name="ベース形状",
        description="雲・モフモフ・トゲ系のベース輪郭を 楕円 / 矩形 から選ぶ",
        items=(
            ("ellipse", "楕円", ""),
            ("rect", "矩形", ""),
        ),
        default="ellipse",
        update=_on_balloon_shape_params_changed,
    )

    # Legacy parameters kept for older B-MANGA files/presets.
    cloud_wave_count: IntProperty(name="雲の波数", default=12, min=3, soft_max=60, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_wave_amplitude_mm: FloatProperty(name="波の振幅 (mm)", default=3.0, min=0.0, soft_max=20.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    spike_count: IntProperty(name="トゲ数", default=24, min=3, soft_max=80, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    spike_depth_mm: FloatProperty(name="トゲの深さ (mm)", default=6.0, min=0.0, soft_max=30.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    spike_jitter: FloatProperty(  # type: ignore[valid-type]
        name="トゲのばらつき",
        description="0.0-1.0 で形状不規則さ",
        default=0.2,
        min=0.0,
        max=1.0,
        update=_on_balloon_shape_params_changed,
    )

class BMangaBalloonEntry(bpy.types.PropertyGroup):
    """フキダシ 1 件."""

    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="名前", default="", update=_on_balloon_title_changed)  # type: ignore[valid-type]
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        default=True,
        update=_on_balloon_entry_changed,
    )
    shape: EnumProperty(name="形状", items=_SHAPE_ITEMS, default="rect", update=_on_balloon_shape_changed)  # type: ignore[valid-type]
    custom_preset_name: StringProperty(  # type: ignore[valid-type]
        name="カスタム形状名",
        description="shape=custom のとき参照するプリセット名",
        default="",
        update=_on_balloon_entry_changed,
    )

    # 配置 (mm)
    x_mm: FloatProperty(name="X", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅 (mm)", default=40.0, min=0.1, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ (mm)", default=20.0, min=0.1, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="回転", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    center_offset_x_mm: FloatProperty(name="中心点 X", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    center_offset_y_mm: FloatProperty(name="中心点 Y", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_enabled: BoolProperty(name="自由変形", default=False, options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_bottom_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_bottom_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_top_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_top_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_line_width_scale: FloatProperty(default=1.0, min=0.01, options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]

    # 角処理 (旧 rounded_corner_enabled は既存ファイル互換のため保持)
    corner_type: EnumProperty(name="角", items=_CORNER_TYPE_ITEMS, default="square", update=_on_balloon_corner_type_changed)  # type: ignore[valid-type]
    corner_type_initialized: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]
    rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rounded_corner_radius_unit: EnumProperty(name="単位", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]

    # 線・塗り
    effect_type: EnumProperty(items=_UNI_FLASH_EFFECT_TYPE_ITEMS, default="uni_flash", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_style: EnumProperty(name="線種", items=_LINE_STYLE_ITEMS, default="solid", update=_on_balloon_line_style_changed)  # type: ignore[valid-type]
    line_width_mm: FloatProperty(name="線幅 (mm)", default=0.3, min=0.0, soft_max=10.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    dashed_segment_length_mm: FloatProperty(name="破線 線分 (mm)", default=3.6, min=0.05, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    dashed_gap_mm: FloatProperty(name="破線 間隔 (mm)", default=2.4, min=0.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    dotted_gap_mm: FloatProperty(name="点線 間隔 (mm)", default=0.45, min=0.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 線種「図形」: 図形を線に沿って連続配置
    line_shape_kind: EnumProperty(name="図形", items=_LINE_SHAPE_KIND_ITEMS, default="circle", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_spacing_mm: FloatProperty(name="図形の間隔 (mm)", default=1.5, min=0.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_angle_deg: FloatProperty(name="図形の角度 (度)", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_orient: EnumProperty(  # type: ignore[valid-type]
        name="図形の向き",
        description="線に沿わせるか、常にフキダシの中心点の方向を向かせるか",
        items=_LINE_SHAPE_ORIENT_ITEMS,
        default="line",
        update=_on_balloon_entry_changed,
    )
    line_shape_jitter: FloatProperty(name="図形の乱れ", description="位置・角度・大きさのばらつき", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_seed: IntProperty(name="乱れシード", default=0, min=0, soft_max=9999, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 線種「画像」: 画像を線に沿って引き延ばして描く
    line_image_path: StringProperty(name="画像", description="線に沿って引き延ばす画像ファイル", default="", subtype="FILE_PATH", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_image_interval_mm: FloatProperty(name="画像の間隔 (mm)", description="画像 1 枚分を線に沿って繰り返す長さ", default=20.0, min=0.5, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_image_angle_deg: FloatProperty(name="画像の角度 (度)", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_image_jitter: FloatProperty(name="画像の乱れ", description="線に対する画像の揺らぎ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 自由形状 (登録カーブ / 手編集) の輪郭キャッシュ。出力・プレビューが
    # カーブ実体の無いファイルでも実形状を描けるよう JSON で保持する。
    custom_outline_json: StringProperty(name="自由形状輪郭", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    multi_line_count: IntProperty(name="線の本数", default=3, min=1, max=12, soft_max=12, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_width_mm: FloatProperty(name="多重線幅 (mm)", default=0.3, min=0.0, soft_max=10.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_spacing_mm: FloatProperty(name="多重線間隔 (mm)", default=0.4, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_width_scale_percent: FloatProperty(name="線幅変化 (%)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_spacing_scale_percent: FloatProperty(name="間隔変化 (%)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 主線の谷/山の線幅: 主線の基本線幅 (line_width_mm) を 100% として % 指定。
    # 100% = 同じ太さ, 0% = その頂点で消える。辺全体で線形補間。
    line_valley_width_pct: FloatProperty(name="主線・谷の線幅 (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_peak_width_pct: FloatProperty(name="主線・山の線幅 (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_line_count: IntProperty(name="線の本数", default=120, min=1, max=1000, soft_max=300, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_line_spacing_mm: FloatProperty(name="線の間隔 (mm)", default=1.0, min=0.01, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_enabled: BoolProperty(name="白線", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_width_percent: FloatProperty(name="白線幅 (%)", default=100.0, min=0.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_valley_width_pct: FloatProperty(name="白線・入り抜き (%)", default=0.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_peak_width_pct: FloatProperty(name="白線・中間線幅 (%)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_count: IntProperty(name="束の数", default=5, min=1, max=100, soft_max=30, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_width_mm: FloatProperty(name="束の幅 (mm)", default=10.0, min=0.01, soft_max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_spacing_mm: FloatProperty(name="白線間隔 (mm)", default=0.25, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_white_line_count: IntProperty(name="白線本数", default=24, min=1, max=200, soft_max=80, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_black_line_count: IntProperty(name="黒線本数", default=3, min=1, max=50, soft_max=12, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_black_spacing_mm: FloatProperty(name="黒線間隔 (mm)", default=0.25, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 白抜き線の詳細 (効果線の白抜き線と同じ項目。既定値 = 従来の固定値なので
    # 既存フキダシの見た目は変わらない)
    white_outline_angle_deg: FloatProperty(name="角度", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_width_jitter_enabled: BoolProperty(name="幅の乱れ", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_width_min_percent: FloatProperty(name="最小幅 (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_length_jitter_enabled: BoolProperty(name="長さの乱れ", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_length_min_percent: FloatProperty(name="最小長さ (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_line_count_auto: BoolProperty(name="白線本数を自動", description="束の幅と白線割合から白線の本数を決める", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_line_count_auto: BoolProperty(name="黒線本数を自動", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_ratio_percent: FloatProperty(name="白線割合 (%)", default=70.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_attenuation: FloatProperty(name="白線減衰", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_direction: EnumProperty(name="重ねる方向", items=_MULTI_LINE_DIRECTION_ITEMS, default="outside", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_width_scale_percent: FloatProperty(name="黒線幅スケール (%)", default=100.0, min=0.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_near_percent: FloatProperty(name="長さ変化 (主線寄り)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_far_percent: FloatProperty(name="長さ変化 (遠い側)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_attenuation: FloatProperty(name="黒線減衰", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_shape: EnumProperty(name="始点形状", items=_EFFECT_SHAPE_ITEMS, default="ellipse", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_to_coma_frame: BoolProperty(name="始点をコマ枠に設定", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_unit: EnumProperty(name="単位", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", default=10.0, min=2.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", default=4.0, min=0.5, soft_max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", default=50.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_shape: EnumProperty(name="終点形状", items=_EFFECT_SHAPE_ITEMS, default="ellipse", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_unit: EnumProperty(name="単位", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", default=10.0, min=2.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", default=4.0, min=0.5, soft_max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", default=50.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    brush_size_mm: FloatProperty(name="線幅 (mm)", default=0.3, min=0.01, soft_max=5.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    brush_jitter_enabled: BoolProperty(name="乱れ", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    brush_jitter_amount: FloatProperty(name="乱れ量", default=0.2, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    length_jitter_enabled: BoolProperty(name="始点乱れ", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    length_jitter_amount: FloatProperty(name="始点乱れ (%)", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_length_jitter_enabled: BoolProperty(name="終点乱れ", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_length_jitter_amount: FloatProperty(name="終点乱れ (%)", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_mode: EnumProperty(name="線の間隔", items=_SPACING_MODE_ITEMS, default="distance", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_angle_deg: FloatProperty(name="線の間隔 (角度)", default=5.0, min=0.1, soft_max=90.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_distance_mm: FloatProperty(name="線の間隔 (距離 mm)", default=1.0, min=0.01, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_density_compensation: BoolProperty(name="密度補正", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_jitter_enabled: BoolProperty(name="乱れ", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_jitter_amount: FloatProperty(name="間隔乱れ量", default=0.2, min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    max_line_count: IntProperty(name="最大本数", default=1000, min=1, soft_max=2000, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_enabled: BoolProperty(name="まとまり", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_line_count: IntProperty(name="数", default=5, min=1, soft_max=50, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_line_count_jitter: FloatProperty(name="数の乱れ", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_gap_mm: FloatProperty(name="まとまり間隔 (mm)", default=5.0, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_gap_jitter_amount: FloatProperty(name="まとまり間隔の乱れ", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_jagged_enabled: BoolProperty(name="ギザギザにする", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_jagged_height_percent: FloatProperty(name="ギザギザ高さ (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inout_apply: EnumProperty(name="適用先", items=_INOUT_APPLY_ITEMS, default="brush_size", update=_on_balloon_inout_apply_changed)  # type: ignore[valid-type]
    inout_apply_brush_size: BoolProperty(name="線幅", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inout_apply_opacity: BoolProperty(name="不透明度", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_percent: FloatProperty(name="入り (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_percent: FloatProperty(name="抜き (%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_start_percent: FloatProperty(name="入り始点 (%)", description="線の始点側から、線幅が一定になる位置を指定します", default=50.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_start_percent: FloatProperty(name="抜き始点 (%)", description="線の終点側から、抜きが始まる長さを指定します", default=50.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_easing_curve: StringProperty(name="入りカーブ", default="0.0000,0.0000;1.0000,1.0000", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_easing_curve: StringProperty(name="抜きカーブ", default="0.0000,0.0000;1.0000,1.0000", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inout_range_mode: EnumProperty(name="範囲", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_range_percent: FloatProperty(name="入りの範囲 (%)", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_range_percent: FloatProperty(name="抜きの範囲 (%)", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_range_mm: FloatProperty(name="入りの範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_range_mm: FloatProperty(name="抜きの範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_base_shape: BoolProperty(name="終点形状を下地として塗る", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_underlay_enabled: BoolProperty(name="白抜き", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_underlay_width_percent: FloatProperty(name="白抜き幅 (%)", default=100.0, min=-300.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_underlay_color: FloatVectorProperty(name="白抜き色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    uni_flash_offset_percent: FloatProperty(name="ズラし量 (%)", description="線の終点を交互に出し入れして、長さをずらします", default=0.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_direction: EnumProperty(name="重ねる方向", items=_MULTI_LINE_DIRECTION_ITEMS, default="outside", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 多重線の谷/山の線幅: 多重線の基本線幅 (multi_line_width_mm) を 100% として % 指定。
    # 100% = 同じ太さ, 0% = その頂点で消える。辺全体に渡って隣接頂点間で
    # 線形補間される (谷から山に向かって 100%→0% など)。
    thorn_multi_line_valley_width_pct: FloatProperty(name="谷の線幅 (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_peak_width_pct: FloatProperty(name="山の線幅 (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 「長さ変化」を 2 段階に分けて、主線に最も近いリング (near) と最も遠い
    # リング (far) で別々の % を指定できる。リング間は線形補間。
    # 後方互換: 旧 `thorn_multi_line_length_scale_percent` は `..._far` のエイリアスとして
    # 残し、UI/シリアライズは新 2 プロパティを使う。
    thorn_multi_line_length_scale_near_percent: FloatProperty(name="長さ変化 (主線寄り)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_length_scale_far_percent: FloatProperty(name="長さ変化 (遠い側)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_length_scale_percent: FloatProperty(name="長さ変化 (%)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_cross_enabled: BoolProperty(name="山谷を延ばして交差", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_color: FloatVectorProperty(name="線色", subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(name="塗り色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_opacity: FloatProperty(name="塗り不透明度", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_material_name: StringProperty(name="塗りマテリアル", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_material_name: StringProperty(name="線マテリアル", description="線種「マテリアル」で線の帯に使うマテリアル", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_material_mapping: EnumProperty(
        name="貼り方",
        description="線種「マテリアル」のテクスチャの貼り方",
        items=[
            ("tile", "そのまま (タイル)", "ページ全体に敷き詰めた模様を帯の形に切り抜く"),
            ("ribbon", "線に沿う (リボン)", "テクスチャを線の向きに沿って変形して貼る。閉じた図形では周の長さに合わせて整数枚に調整し、始点終点に継ぎ目を出さない"),
        ],
        default="tile",
        update=_on_balloon_entry_changed,
    )  # type: ignore[valid-type]
    line_material_stretch_single: BoolProperty(
        name="1枚を全周に引き伸ばす",
        description="タイルを繰り返さず、テクスチャ1枚を線の始点から終点まで引き伸ばして貼る。左右の端がつながらない柄では、始点終点の接続点で柄が途切れて見える",
        default=False,
        update=_on_balloon_entry_changed,
    )  # type: ignore[valid-type]
    line_material_seam_fix: EnumProperty(
        name="継ぎ目処理",
        description="「1枚を全周に引き伸ばす」で左右がつながらない柄の始点終点を馴染ませる方法",
        items=[
            ("none", "そのまま", "始点終点で柄が途切れたまま貼る"),
            ("mirror", "ミラー往復", "行きは普通に、帰りは鏡像で貼る。始点終点が同じ端になり途切れないが、柄が途中で左右反転する"),
            ("crossfade", "クロスフェード", "始点終点の手前の短い区間で柄を重ねて馴染ませる (出力で馴染ませる。画面の簡易表示では途切れて見えることがある)"),
        ],
        default="none",
        update=_on_balloon_entry_changed,
    )  # type: ignore[valid-type]
    fill_blur_amount: FloatProperty(name="塗り輪郭ぼかし", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_blur_axis: EnumProperty(name="ぼかす軸", items=_FILL_BLUR_AXIS_ITEMS, default="inside", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_blur_dither: BoolProperty(name="塗りぼかしをディザ化", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_enabled: BoolProperty(name="塗りグラデーション", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_start_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_end_color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.82, 0.82, 0.82, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_angle_deg: FloatProperty(name="グラデーション角度", default=90.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    outer_white_margin_enabled: BoolProperty(name="外側フチ", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    outer_white_margin_width_mm: FloatProperty(name="外側フチ幅 (mm)", default=1.0, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    outer_white_margin_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inner_white_margin_enabled: BoolProperty(name="内側フチ", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inner_white_margin_width_mm: FloatProperty(name="内側フチ幅 (mm)", default=1.0, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inner_white_margin_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    blend_mode: EnumProperty(name="", items=_BLEND_MODE_ITEMS, default="normal", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    merge_group_id: StringProperty(name="結合フォルダ ID", default="")  # type: ignore[valid-type]
    parent_kind: StringProperty(name="親種別", default="page", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    selected: BoolProperty(name="選択", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]

    # 反転 / 不透明度 (Meldex flipH/flipV/opacity 相当)
    flip_h: BoolProperty(name="水平反転", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flip_v: BoolProperty(name="垂直反転", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_balloon_entry_changed,
    )

    # 形状固有パラメータ・尻尾
    shape_params: PointerProperty(type=BMangaBalloonShapeParams)  # type: ignore[valid-type]
    tails: CollectionProperty(type=BMangaBalloonTail)  # type: ignore[valid-type]

    # テキスト (実内容は TextEntry)
    text_id: StringProperty(name="Text ID", default="")  # type: ignore[valid-type]


_CLASSES = (
    BMangaBalloonTailPoint,
    BMangaBalloonTail,
    BMangaBalloonShapeParams,
    BMangaBalloonEntry,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("balloon registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
