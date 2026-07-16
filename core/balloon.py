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


# number (第5要素) は .blend に保存される enum の整数値。項目を削除しても既存
# データの値がズレないよう明示する。6 は旧「八角形」(削除済み) の欠番。
_SHAPE_ITEMS = (
    ("rect", "矩形", "Meldex ボードカードと同じ矩形", "NONE", 0),
    ("ellipse", "楕円", "Meldex ボードカードと同じ楕円", "NONE", 1),
    ("cloud", "雲", "Meldex ボードカードと同じ雲形", "NONE", 2),
    ("fluffy", "もやもや", "Meldex ボードカードと同じもやもや形", "NONE", 3),
    ("thorn", "トゲ（直線）", "Meldex ボードカードと同じ直線トゲ形", "NONE", 4),
    ("thorn-curve", "トゲ（曲線）", "Meldex ボードカードと同じ曲線トゲ形", "NONE", 5),
    ("custom", "カスタム", "カスタム形状プリセット参照", "NONE", 7),
    ("none", "本体なし", "テキスト単体 (擬音/ナレーション用)", "NONE", 8),
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
    ("none", "線なし", "主線を描かない"),
    ("solid", "実線", "一本のつながった線で描く"),
    ("dashed", "破線", "一定間隔で途切れる線で描く"),
    ("dotted", "点線", "点を連ねた線で描く"),
    ("double", "多重線", "複数本の線を重ねて描く"),
    ("uni_flash", "ウニフラ", "フキダシの形状を内端輪郭として使い、放射状の線を並べる"),
    ("white_outline", "白抜き線", "フキダシの形状を内端輪郭として使い、白抜き線を放射状に並べる"),
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
    ("square", "直角", "角を丸めずそのまま直角にする"),
    ("rounded", "丸角", "角を丸くする"),
    ("bevel", "面取り", "角を斜めにカットする"),
)

_MULTI_LINE_DIRECTION_ITEMS = (
    ("outside", "外側", "主線の外側に重ねる"),
    ("inside", "内側", "主線の内側に重ねる"),
    ("both", "両方向", "主線の内外両方に重ねる"),
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
    item
    for item in _SHAPE_ITEMS
    if item[0] not in {"custom", "none"}
)
_SPACING_MODE_ITEMS = (
    ("angle", "角度指定", "角度で線の間隔を指定する"),
    ("distance", "距離指定", "距離 (mm) で線の間隔を指定する"),
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


def _on_linked_text_fit_changed(_self, context) -> None:
    """リンク余白・位置を変えた時、テキストを固定してフキダシを再配置する。"""

    try:
        from ..utils import balloon_curve_object, text_balloon_link

        if not balloon_curve_object.auto_sync_is_paused():
            scene = getattr(context, "scene", None) if context is not None else None
            work = getattr(scene, "bmanga_work", None) if scene is not None else None
            page, resolved = balloon_curve_object.find_balloon_entry(
                scene,
                str(getattr(_self, "id", "") or ""),
            ) if scene is not None else (None, None)
            if work is not None and resolved is not None:
                with balloon_curve_object.defer_auto_sync():
                    text_balloon_link.fit_balloon_to_linked_text(
                        work,
                        resolved,
                        page=page,
                    )
    except Exception:  # UI入力は維持し、通常のフキダシ同期へ安全にフォールバックする
        _logger.exception("linked text balloon fit failed")
    _on_balloon_entry_changed(_self, context)


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
    # トゲ形状は曲線・直線とも、先端と多重線を丸めない状態を初期値にする。
    # 共有プロパティ自体の既定値は False のままにし、トゲ以外へ切り替えた
    # ときはユーザーが明示した値を上書きしない。
    shape = str(getattr(_self, "shape", "") or "")
    shape_params = getattr(_self, "shape_params", None)
    if shape_params is not None and shape in {"thorn", "thorn-curve"}:
        shape_params.cloud_valley_sharp = True
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


def _on_balloon_effect_start_corner_changed(_self, context) -> None:
    try:
        _self.start_rounded_corner_enabled = (
            str(getattr(_self, "start_corner_type", "square") or "square") != "square"
        )
    except Exception:  # noqa: BLE001
        pass
    _on_balloon_entry_changed(_self, context)


def _on_balloon_effect_end_corner_changed(_self, context) -> None:
    try:
        _self.end_rounded_corner_enabled = (
            str(getattr(_self, "end_corner_type", "square") or "square") != "square"
        )
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
    x_mm: FloatProperty(name="X", description="しっぽの折れ線ポイントの X 座標 (mm)", default=0.0, update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", description="しっぽの折れ線ポイントの Y 座標 (mm)", default=0.0, update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]
    corner_type: EnumProperty(name="角のタイプ", description="このポイントの角を直線でつなぐか曲線でつなぐか", items=_TAIL_POINT_CORNER_ITEMS, default="line", update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]


class BMangaBalloonTail(bpy.types.PropertyGroup):
    type: EnumProperty(description="しっぽの形の種類 (直線 / 曲線 / 付箋)", items=_TAIL_TYPE_ITEMS, default="straight", update=_on_balloon_tail_changed)  # type: ignore[valid-type]
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
    direction_deg: FloatProperty(name="方向 (度)", description="しっぽが伸びる方向の角度", default=270.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    length_mm: FloatProperty(name="長さ (mm)", description="しっぽの根元から先端までの長さ", default=6.0, min=0.0, soft_max=50.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    root_width_mm: FloatProperty(name="根元幅 (mm)", description="しっぽの根元 (フキダシ側) の幅", default=3.0, min=0.0, soft_max=20.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    tip_width_mm: FloatProperty(name="先端幅 (mm)", description="しっぽの先端の幅", default=0.0, min=0.0, soft_max=20.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    curve_bend: FloatProperty(  # type: ignore[valid-type]
        name="曲げ",
        description="曲線尻尾のみ: -1.0〜1.0 で曲がり具合",
        default=0.0,
        soft_min=-1.0,
        soft_max=1.0,
        update=_on_balloon_tail_changed,
    )
    custom_points_enabled: BoolProperty(name="始点・終点を固定", description="ONにすると、しっぽの根元と先端の位置を自動計算ではなく指定した座標に固定する", default=False, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    start_x_mm: FloatProperty(name="始点 X", description="固定した根元位置の X 座標 (mm)", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    start_y_mm: FloatProperty(name="始点 Y", description="固定した根元位置の Y 座標 (mm)", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    end_x_mm: FloatProperty(name="終点 X", description="固定した先端位置の X 座標 (mm)", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    end_y_mm: FloatProperty(name="終点 Y", description="固定した先端位置の Y 座標 (mm)", default=0.0, update=_on_balloon_tail_changed)  # type: ignore[valid-type]
    points: CollectionProperty(type=BMangaBalloonTailPoint, description="しっぽの形を折れ線・曲線で描くための中間ポイントの一覧")  # type: ignore[valid-type]


class BMangaBalloonShapeParams(bpy.types.PropertyGroup):
    """形状固有パラメータ."""

    cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", description="雲・トゲなど形状の輪郭にできる山1つ分の幅", default=10.0, min=2.0, soft_max=200.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", description="山の幅のばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", description="輪郭の山1つ分の高さ (外向きの出っ張り量)", default=4.0, min=0.5, soft_max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", description="山の高さのばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_offset_percent: FloatProperty(name="ズラし量 (%)", description="山と山の間にできる小山の位置をずらす量", default=50.0, min=0.0, max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    shape_seed: IntProperty(name="シード", description="形状のランダムな乱れを決める値。同じ値なら毎回同じ形になる", default=0, min=0, soft_max=9999, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", description="山の間にできる小山の幅を、山の幅に対する割合で指定", default=30.0, min=0.0, max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", description="小山の幅のばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", description="小山の高さを、山の高さに対する割合で指定", default=50.0, min=0.0, max=100.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", description="小山の高さのばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
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
            ("ellipse", "楕円", "楕円をベースにした輪郭にする"),
            ("rect", "矩形", "矩形をベースにした輪郭にする"),
        ),
        default="ellipse",
        update=_on_balloon_shape_params_changed,
    )
    dynamic_base_rounded_corner_enabled: BoolProperty(name="ベース丸角", description="ベース形状が矩形のとき、角を丸めるかどうか", default=False, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    dynamic_base_rounded_corner_radius_mm: FloatProperty(name="ベース角半径 (mm)", description="ベース形状の角を丸める半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    dynamic_base_rounded_corner_radius_unit: EnumProperty(name="単位", description="角の半径を mm と % のどちらで指定するか", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    dynamic_base_rounded_corner_radius_percent: FloatProperty(name="ベース角半径 (%)", description="ベース形状の角を丸める半径を、辺の長さに対する割合で指定", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]

    # Legacy parameters kept for older B-MANGA files/presets.
    cloud_wave_count: IntProperty(name="雲の波数", description="雲形状の輪郭にできる波の数", default=12, min=3, soft_max=60, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    cloud_wave_amplitude_mm: FloatProperty(name="波の振幅 (mm)", description="雲形状の波の高さ (mm)", default=3.0, min=0.0, soft_max=20.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    spike_count: IntProperty(name="トゲ数", description="トゲ形状のトゲの本数", default=24, min=3, soft_max=80, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
    spike_depth_mm: FloatProperty(name="トゲの深さ (mm)", description="トゲ形状のトゲの深さ (mm)", default=6.0, min=0.0, soft_max=30.0, update=_on_balloon_shape_params_changed)  # type: ignore[valid-type]
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

    id: StringProperty(name="ID", description="このフキダシを識別する内部ID (自動採番、通常は編集不要)", default="")  # type: ignore[valid-type]
    meldex_source_document_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    meldex_source_row_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    meldex_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    title: StringProperty(name="名前", description="レイヤー一覧に表示するこのフキダシの名前", default="", update=_on_balloon_title_changed)  # type: ignore[valid-type]
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        description="ONでこのフキダシを表示、OFFで非表示にする",
        default=True,
        update=_on_balloon_entry_changed,
    )
    shape: EnumProperty(name="形状", description="フキダシ本体の形 (矩形・楕円・雲形など)。保存済み実形状の再生成はフキダシツール側から実行する", items=_SHAPE_ITEMS, default="rect", update=_on_balloon_shape_changed)  # type: ignore[valid-type]
    custom_preset_name: StringProperty(  # type: ignore[valid-type]
        name="カスタム形状名",
        description="shape=custom のとき参照するプリセット名",
        default="",
        update=_on_balloon_entry_changed,
    )

    # 配置 (mm)
    x_mm: FloatProperty(name="X", description="フキダシ中心のページ上での X 座標 (mm)", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", description="フキダシ中心のページ上での Y 座標 (mm)", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅 (mm)", description="フキダシ本体の幅", default=40.0, min=0.1, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ (mm)", description="フキダシ本体の高さ", default=20.0, min=0.1, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="回転", description="フキダシ全体の回転角度", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    linked_text_offset_x_mm: FloatProperty(name="横位置 (mm)", description="リンクしたテキストの中心からフキダシ中心を横へずらす量", default=0.0, update=_on_linked_text_fit_changed)  # type: ignore[valid-type]
    linked_text_offset_y_mm: FloatProperty(name="縦位置 (mm)", description="リンクしたテキストの中心からフキダシ中心を縦へずらす量", default=0.0, update=_on_linked_text_fit_changed)  # type: ignore[valid-type]
    linked_text_padding_x_mm: FloatProperty(name="横余白 (mm)", description="リンクしたテキストの左右へ確保するフキダシの余白", default=6.0, min=0.0, soft_max=50.0, update=_on_linked_text_fit_changed)  # type: ignore[valid-type]
    linked_text_padding_y_mm: FloatProperty(name="縦余白 (mm)", description="リンクしたテキストの上下へ確保するフキダシの余白", default=6.0, min=0.0, soft_max=50.0, update=_on_linked_text_fit_changed)  # type: ignore[valid-type]
    center_offset_x_mm: FloatProperty(name="中心点 X", description="回転の中心点を、フキダシ中心から X 方向にずらす量", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    center_offset_y_mm: FloatProperty(name="中心点 Y", description="回転の中心点を、フキダシ中心から Y 方向にずらす量", default=0.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_enabled: BoolProperty(name="自由変形", default=False, options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_bottom_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_bottom_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_top_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_top_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    free_transform_line_width_scale: FloatProperty(default=1.0, min=0.01, options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]

    # 角処理 (旧 rounded_corner_enabled は既存ファイル互換のため保持)
    corner_type: EnumProperty(name="角", description="フキダシ本体の角の処理 (直角 / 丸角 / 面取り)", items=_CORNER_TYPE_ITEMS, default="square", update=_on_balloon_corner_type_changed)  # type: ignore[valid-type]
    corner_type_initialized: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]
    rounded_corner_enabled: BoolProperty(name="角丸", description="ONで矩形フキダシの角を丸める", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", description="角を丸める半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rounded_corner_radius_unit: EnumProperty(name="単位", description="角の半径を mm と % のどちらで指定するか", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", description="角を丸める半径を、辺の長さに対する割合で指定", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]

    # 線・塗り
    effect_type: EnumProperty(items=_UNI_FLASH_EFFECT_TYPE_ITEMS, default="uni_flash", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_style: EnumProperty(name="線種", description="フキダシ主線の種類 (実線・破線・ウニフラなど)", items=_LINE_STYLE_ITEMS, default="solid", update=_on_balloon_line_style_changed)  # type: ignore[valid-type]
    line_width_mm: FloatProperty(name="線幅 (mm)", description="フキダシ主線の太さ", default=0.3, min=0.0, soft_max=10.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    dashed_segment_length_mm: FloatProperty(name="破線 線分 (mm)", description="破線の線分1本分の長さ", default=3.6, min=0.05, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    dashed_gap_mm: FloatProperty(name="破線 間隔 (mm)", description="破線の線分どうしの間隔", default=2.4, min=0.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    dotted_gap_mm: FloatProperty(name="点線 間隔 (mm)", description="点線の点どうしの間隔", default=0.45, min=0.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 線種「図形」: 図形を線に沿って連続配置
    line_shape_kind: EnumProperty(name="図形", description="線種「図形」で連続配置する図形の種類", items=_LINE_SHAPE_KIND_ITEMS, default="circle", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_spacing_mm: FloatProperty(name="図形の間隔 (mm)", description="図形と図形の間隔", default=1.5, min=0.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_angle_deg: FloatProperty(name="図形の角度 (度)", description="図形1つ1つに加える追加回転角", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_orient: EnumProperty(  # type: ignore[valid-type]
        name="図形の向き",
        description="線に沿わせるか、常にフキダシの中心点の方向を向かせるか",
        items=_LINE_SHAPE_ORIENT_ITEMS,
        default="line",
        update=_on_balloon_entry_changed,
    )
    line_shape_jitter: FloatProperty(name="図形の乱れ", description="位置・角度・大きさのばらつき", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_shape_seed: IntProperty(name="乱れシード", description="図形のばらつきを決める乱数の種。同じ値なら毎回同じ配置になる", default=0, min=0, soft_max=9999, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 線種「画像」: 画像を線に沿って引き延ばして描く
    line_image_path: StringProperty(name="画像", description="線に沿って引き延ばす画像ファイル", default="", subtype="FILE_PATH", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_image_interval_mm: FloatProperty(name="画像の間隔 (mm)", description="画像 1 枚分を線に沿って繰り返す長さ", default=20.0, min=0.5, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_image_angle_deg: FloatProperty(name="画像の角度 (度)", description="線に貼り付ける画像1つ1つに加える追加回転角", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_image_jitter: FloatProperty(name="画像の乱れ", description="線に対する画像の揺らぎ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 自由形状 (登録カーブ / 手編集) の輪郭キャッシュ。出力・プレビューが
    # カーブ実体の無いファイルでも実形状を描けるよう JSON で保持する。
    custom_outline_json: StringProperty(name="自由形状輪郭", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    multi_line_count: IntProperty(name="線の本数", description="線種「多重線」で重ねる線の本数", default=3, min=1, max=12, soft_max=12, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_width_mm: FloatProperty(name="多重線幅 (mm)", description="多重線1本ごとの太さ", default=0.3, min=0.0, soft_max=10.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_spacing_mm: FloatProperty(name="多重線間隔 (mm)", description="多重線どうしの間隔", default=0.4, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_width_scale_percent: FloatProperty(name="線幅変化 (%)", description="外側の線ほど太さをどれだけ変化させるか (100% で変化なし)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_spacing_scale_percent: FloatProperty(name="間隔変化 (%)", description="外側の線ほど間隔をどれだけ変化させるか (100% で変化なし)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 主線の谷/山の線幅: 主線の基本線幅 (line_width_mm) を 100% として % 指定。
    # 100% = 同じ太さ, 0% = その頂点で消える。辺全体で線形補間。
    line_valley_width_pct: FloatProperty(name="主線・谷の線幅 (%)", description="輪郭の谷 (へこみ) での主線の太さを、通常幅に対する割合で指定 (0% で消える)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_peak_width_pct: FloatProperty(name="主線・山の線幅 (%)", description="通常は輪郭の山での主線の太さ、白抜き線では黒線の太さを、通常幅に対する割合で指定 (0% で消える)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_line_count: IntProperty(name="線の本数", description="ウニフラで放射状に並べる線の本数", default=120, min=1, max=1000, soft_max=300, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_line_spacing_mm: FloatProperty(name="線の間隔 (mm)", description="ウニフラの線どうしの間隔", default=1.0, min=0.01, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_enabled: BoolProperty(name="白線", description="ONでウニフラの線の内側に白い縁取りを入れる", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_width_percent: FloatProperty(name="白線幅 (%)", description="白線の太さを、線幅に対する割合で指定", default=100.0, min=0.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_valley_width_pct: FloatProperty(name="白線・入り抜き (%)", description="線の根元・先端での白線の太さの割合", default=0.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_line_peak_width_pct: FloatProperty(name="白線・中間線幅 (%)", description="線の中間部分での白線の太さの割合", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_count: IntProperty(name="束の数", description="白抜き線をまとめる束の数", default=5, min=1, max=100, soft_max=30, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_bundle_placement: EnumProperty(name="束の配置", description="白抜き線の束を、間隔指定か本数指定のどちらで配置するか", items=line_effect_schema.WHITE_OUTLINE_BUNDLE_PLACEMENT_ITEMS, default="spacing", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_bundle_spacing_deg: FloatProperty(name="束の間隔 (角度)", description="0 の場合は全周へ等間隔に配置します", default=0.0, min=0.0, max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_bundle_spacing_jitter: FloatProperty(name="間隔乱れ", description="束の間隔のばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_position_percent: FloatProperty(name="位置 (%)", description="内端形状に対する位置。100% で線の長さ分ぴったり内端形状の外側、0% で線の中心が内端形状上", default=100.0, min=-200.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_width_mm: FloatProperty(name="束の太さ (mm)", description="白抜き線1束分の太さ", default=10.0, min=0.01, soft_max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_spacing_mm: FloatProperty(name="間隔 (mm)", description="束の中の白線どうしの間隔", default=0.2, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_spacing_scale_percent: FloatProperty(name="間隔変化 (%)", description="外側の白線ほど間隔をどれだけ変化させるか (100% で変化なし)", default=100.0, min=0.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_white_brush_mm: FloatProperty(name="太さ (mm)", description="束の中の白線1本の太さ", default=0.3, min=0.01, soft_max=5.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_white_line_count: IntProperty(name="本数", description="束の中に並べる白線の本数", default=24, min=1, max=200, soft_max=80, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_black_line_count: IntProperty(name="本数", description="束の中に並べる黒線の本数", default=3, min=1, max=50, soft_max=12, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flash_white_outline_black_spacing_mm: FloatProperty(name="間隔 (mm)", description="束の中の黒線どうしの間隔", default=0.2, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_spacing_scale_percent: FloatProperty(name="間隔変化 (%)", description="外側の黒線ほど間隔をどれだけ変化させるか (100% で変化なし)", default=100.0, min=0.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 白抜き線の詳細 (効果線の白抜き線と同じ項目)。新規作成は共通の
    # 初期値を使い、旧作品は io の設定版移行で当時の実効値を復元する。
    white_outline_angle_deg: FloatProperty(name="角度", description="白抜き線の束1つ1つに加える追加回転角", default=0.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_width_jitter_enabled: BoolProperty(name="太さ乱れ", description="ONで白抜き線の太さにばらつきを付ける", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_width_min_percent: FloatProperty(name="最小値 (%)", description="太さのばらつきの最小値 (通常の太さに対する割合)", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_length_jitter_enabled: BoolProperty(name="長さ乱れ", description="ONで白抜き線の長さにばらつきを付ける", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_length_min_percent: FloatProperty(name="最小値 (%)", description="長さのばらつきの最小値 (通常の長さに対する割合)", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_length_percent: FloatProperty(name="長さ (%)", description="100% で内端形状まで伸ばし、小さくするほど内端形状から離れます", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_line_count_auto: BoolProperty(name="本数を自動計算", description="束の太さと白線割合から白線の本数を決める", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_line_count_auto: BoolProperty(name="本数を自動計算", description="ONで束の太さと黒線割合から黒線の本数を自動的に決める", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_ratio_percent: FloatProperty(name="白線割合 (%)", description="束の太さのうち白線に使う割合", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_ratio_percent: FloatProperty(name="黒線割合 (%)", description="束の太さのうち左右の黒線領域に使う割合", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_attenuation: FloatProperty(name="減衰", description="束の端の白線ほど短くする度合い (%)。マイナスは元の長さまで伸ばします", default=0.0, min=-100.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_in_percent: FloatProperty(name="入り (%)", description="白線の根元側を細める強さ", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_out_percent: FloatProperty(name="抜き (%)", description="白線の先端側を細める強さ", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_inout_range_mode: EnumProperty(name="入り抜き範囲", description="入り抜きの範囲を、線の長さに対する割合と mm のどちらで指定するか", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_in_range_percent: FloatProperty(name="入り範囲 (%)", description="白線の根元側を細める範囲を、線の長さに対する割合で指定", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_out_range_percent: FloatProperty(name="抜き範囲 (%)", description="白線の先端側を細める範囲を、線の長さに対する割合で指定", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_in_range_mm: FloatProperty(name="入り範囲 (mm)", description="白線の根元側を細める範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_out_range_mm: FloatProperty(name="抜き範囲 (mm)", description="白線の先端側を細める範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_in_easing_curve: StringProperty(name="入りカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_white_out_easing_curve: StringProperty(name="抜きカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_direction: EnumProperty(name="重ねる方向", description="黒線を主線の外側・内側・両方向のどこに重ねるか", items=_MULTI_LINE_DIRECTION_ITEMS, default="outside", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_width_scale_percent: FloatProperty(name="幅変化 (%)", description="黒線の太さを、通常太さに対する割合で変化させる", default=100.0, min=0.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_near_percent: FloatProperty(name="長さ変化 (主線寄り)", description="主線に近い側の黒線の長さ変化", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_length_scale_far_percent: FloatProperty(name="長さ変化 (遠い側)", description="主線から遠い側の黒線の長さ変化", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_attenuation: FloatProperty(name="減衰", description="領域の端の黒線ほど短くする度合い (%)。マイナスは元の長さまで伸ばします", default=0.0, min=-100.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_in_percent: FloatProperty(name="入り (%)", description="黒線の根元側を細める強さ", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_out_percent: FloatProperty(name="抜き (%)", description="黒線の先端側を細める強さ", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_inout_range_mode: EnumProperty(name="入り抜き範囲", description="入り抜きの範囲を、線の長さに対する割合と mm のどちらで指定するか", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_in_range_percent: FloatProperty(name="入り範囲 (%)", description="黒線の根元側を細める範囲を、線の長さに対する割合で指定", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_out_range_percent: FloatProperty(name="抜き範囲 (%)", description="黒線の先端側を細める範囲を、線の長さに対する割合で指定", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_in_range_mm: FloatProperty(name="入り範囲 (mm)", description="黒線の根元側を細める範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_out_range_mm: FloatProperty(name="抜き範囲 (mm)", description="黒線の先端側を細める範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_in_easing_curve: StringProperty(name="入りカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_outline_black_out_easing_curve: StringProperty(name="抜きカーブ", default="0.0000,0.0000;1.0000,1.0000", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_shape: EnumProperty(name="外端形状", description="ウニフラなどの線の外端 (先端側) の基準形状", items=_EFFECT_SHAPE_ITEMS, default="ellipse", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_to_coma_frame: BoolProperty(name="外端形状をコマ枠に設定", description="ONで外端の基準形状をこのコマの外枠に合わせる", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_corner_type: EnumProperty(name="角", description="外端の基準形状が矩形のときの角の処理", items=_CORNER_TYPE_ITEMS, default="square", update=_on_balloon_effect_start_corner_changed)  # type: ignore[valid-type]
    start_rounded_corner_enabled: BoolProperty(name="角丸", description="ONで外端の基準形状の角を丸める", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", description="外端の基準形状の角を丸める半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_unit: EnumProperty(name="単位", description="外端の角の半径を mm と % のどちらで指定するか", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", description="外端の基準形状の角を丸める半径を、辺の長さに対する割合で指定", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", description="外端の基準形状が雲形のときの、山1つ分の幅", default=10.0, min=2.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", description="外端の基準形状の山の幅のばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", description="外端の基準形状が雲形のときの、山1つ分の高さ", default=4.0, min=0.5, soft_max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", description="外端の基準形状の山の高さのばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", description="外端の基準形状の小山の位置をずらす量", default=50.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", description="外端の基準形状の小山の幅を、山の幅に対する割合で指定", default=30.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", description="外端の基準形状の小山の幅のばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", description="外端の基準形状の小山の高さを、山の高さに対する割合で指定。0% は自動 (50%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", description="外端の基準形状の小山の高さのばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_shape: EnumProperty(name="内端形状", description="ウニフラなどの線の内端 (根元側) の基準形状", items=_EFFECT_SHAPE_ITEMS, default="ellipse", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_corner_type: EnumProperty(name="角", description="内端の基準形状が矩形のときの角の処理", items=_CORNER_TYPE_ITEMS, default="square", update=_on_balloon_effect_end_corner_changed)  # type: ignore[valid-type]
    end_rounded_corner_enabled: BoolProperty(name="角丸", description="ONで内端の基準形状の角を丸める", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", description="内端の基準形状の角を丸める半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_unit: EnumProperty(name="単位", description="内端の角の半径を mm と % のどちらで指定するか", items=corner_radius.RADIUS_UNIT_ITEMS, default="mm", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_percent: FloatProperty(name="角半径 (%)", description="内端の基準形状の角を丸める半径を、辺の長さに対する割合で指定", default=30.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_mm: FloatProperty(name="山の幅 (mm)", description="内端の基準形状が雲形のときの、山1つ分の幅", default=10.0, min=2.0, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_jitter: FloatProperty(name="山の幅 乱れ", description="内端の基準形状の山の幅のばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_mm: FloatProperty(name="山の高さ (mm)", description="内端の基準形状が雲形のときの、山1つ分の高さ", default=4.0, min=0.5, soft_max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_jitter: FloatProperty(name="山の高さ 乱れ", description="内端の基準形状の山の高さのばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", description="内端の基準形状の小山の位置をずらす量", default=50.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", description="内端の基準形状の小山の幅を、山の幅に対する割合で指定", default=30.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_jitter: FloatProperty(name="小山幅 乱れ", description="内端の基準形状の小山の幅のばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", description="内端の基準形状の小山の高さを、山の高さに対する割合で指定。0% は自動 (50%)", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_jitter: FloatProperty(name="小山高 乱れ", description="内端の基準形状の小山の高さのばらつき具合", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    brush_size_mm: FloatProperty(name="線幅 (mm)", description="ウニフラなど放射状の線1本分の太さ", default=0.3, min=0.01, soft_max=5.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    brush_jitter_enabled: BoolProperty(name="乱れ", description="ONで線の太さにばらつきを付ける", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    brush_jitter_amount: FloatProperty(name="乱れ量", description="線の太さのばらつき量", default=0.2, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    length_jitter_enabled: BoolProperty(name="外端乱れ", description="ONで線の外端 (先端) の長さにばらつきを付ける", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    length_jitter_amount: FloatProperty(name="外端乱れ (%)", description="線の外端のばらつき量", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_length_jitter_enabled: BoolProperty(name="内端乱れ", description="ONで線の内端 (根元) の長さにばらつきを付ける", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    end_length_jitter_amount: FloatProperty(name="内端乱れ (%)", description="線の内端のばらつき量", default=20.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_mode: EnumProperty(name="線の間隔", description="放射状の線の間隔を、角度と距離のどちらで指定するか", items=_SPACING_MODE_ITEMS, default="distance", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_angle_deg: FloatProperty(name="線の間隔 (角度)", description="線と線の間隔を角度で指定", default=5.0, min=0.1, soft_max=90.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_distance_mm: FloatProperty(name="線の間隔 (距離 mm)", description="線と線の間隔を距離 (mm) で指定", default=1.0, min=0.01, soft_max=50.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_density_compensation: BoolProperty(name="密度補正", description="ONで輪郭の曲率に応じて線の密度を均一に補正する", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_jitter_enabled: BoolProperty(name="乱れ", description="ONで線の間隔にばらつきを付ける", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    spacing_jitter_amount: FloatProperty(name="間隔乱れ量", description="線の間隔のばらつき量", default=0.2, min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    max_line_count: IntProperty(name="最大本数", description="放射状に並べる線の本数の上限", default=1000, min=1, soft_max=2000, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_enabled: BoolProperty(name="まとまり", description="ONで線を数本ずつのまとまりにして配置する", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_line_count: IntProperty(name="数", description="1つのまとまりに含める線の本数", default=5, min=1, soft_max=50, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_line_count_jitter: FloatProperty(name="数の乱れ", description="まとまりごとの本数のばらつき", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_gap_mm: FloatProperty(name="まとまり間隔 (mm)", description="まとまりとまとまりの間隔", default=5.0, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_gap_jitter_amount: FloatProperty(name="まとまり間隔の乱れ", description="まとまりの間隔のばらつき", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_jagged_enabled: BoolProperty(name="ギザギザにする", description="ONでまとまりの高さをギザギザに変化させる", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    bundle_jagged_height_percent: FloatProperty(name="ギザギザ高さ (%)", description="ギザギザの高さを、通常の高さに対する割合で指定", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inout_apply: EnumProperty(name="適用先", description="入り抜きの効果を線幅と不透明度のどちらに適用するか", items=_INOUT_APPLY_ITEMS, default="brush_size", update=_on_balloon_inout_apply_changed)  # type: ignore[valid-type]
    inout_apply_brush_size: BoolProperty(name="線幅", description="ONで入り抜きの効果を線幅に適用する", default=True, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inout_apply_opacity: BoolProperty(name="不透明度", description="ONで入り抜きの効果を不透明度に適用する", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_percent: FloatProperty(name="入り (%)", description="線の根元側を細める (薄くする) 強さ", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_percent: FloatProperty(name="抜き (%)", description="線の先端側を細める (薄くする) 強さ", default=0.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_start_percent: FloatProperty(name="外端側グラフ位置", default=50.0, min=0.0, max=100.0, options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_start_percent: FloatProperty(name="内端側グラフ位置", default=50.0, min=0.0, max=100.0, options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_easing_curve: StringProperty(name="入りカーブ", description="入りの変化カーブを表す内部データ (グラフエディタで編集)", default="0.0000,0.0000;1.0000,1.0000", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_easing_curve: StringProperty(name="抜きカーブ", description="抜きの変化カーブを表す内部データ (グラフエディタで編集)", default="0.0000,0.0000;1.0000,1.0000", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inout_range_mode: EnumProperty(name="範囲", description="入り抜きの範囲を、線の長さに対する割合と mm のどちらで指定するか", items=_INOUT_RANGE_MODE_ITEMS, default="percent", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_range_percent: FloatProperty(name="入りの範囲 (%)", description="根元側を細める範囲を、線の長さに対する割合で指定", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_range_percent: FloatProperty(name="抜きの範囲 (%)", description="先端側を細める範囲を、線の長さに対する割合で指定", default=100.0, min=0.0, max=100.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    in_range_mm: FloatProperty(name="入りの範囲 (mm)", description="根元側を細める範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    out_range_mm: FloatProperty(name="抜きの範囲 (mm)", description="先端側を細める範囲 (mm)", default=10.0, min=0.0, soft_max=200.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_base_shape: BoolProperty(name="フキダシの形状を下地として塗る", description="ONでフキダシの形状を下地として塗りつぶす", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_underlay_enabled: BoolProperty(name="白抜き", description="ONで線の内側に白い下地を敷く", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_underlay_width_percent: FloatProperty(name="白抜き幅 (%)", description="白い下地の幅を、線幅に対する割合で指定", default=100.0, min=-300.0, max=300.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    white_underlay_color: FloatVectorProperty(name="白抜き色", description="白い下地の色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    uni_flash_offset_percent: FloatProperty(name="ズラし量 (%)", description="線の内端を交互に出し入れして、長さをずらします", default=0.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_direction: EnumProperty(name="重ねる方向", description="多重線を主線の外側・内側・両方向のどこに重ねるか", items=_MULTI_LINE_DIRECTION_ITEMS, default="outside", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 多重線の谷/山の線幅: 多重線の基本線幅 (multi_line_width_mm) を 100% として % 指定。
    # 100% = 同じ太さ, 0% = その頂点で消える。辺全体に渡って隣接頂点間で
    # 線形補間される (谷から山に向かって 100%→0% など)。
    thorn_multi_line_valley_width_pct: FloatProperty(name="谷の線幅 (%)", description="輪郭の谷での多重線の太さを、通常幅に対する割合で指定 (0% で消える)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_peak_width_pct: FloatProperty(name="山の線幅 (%)", description="輪郭の山での多重線の太さを、通常幅に対する割合で指定 (0% で消える)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 「長さ変化」を 2 段階に分けて、主線に最も近いリング (near) と最も遠い
    # リング (far) で別々の % を指定できる。リング間は線形補間。
    # 後方互換: 旧 `thorn_multi_line_length_scale_percent` は `..._far` のエイリアスとして
    # 残し、UI/シリアライズは新 2 プロパティを使う。
    thorn_multi_line_length_scale_near_percent: FloatProperty(name="長さ変化 (主線寄り)", description="主線に近いリングの多重線の長さ変化", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_length_scale_far_percent: FloatProperty(name="長さ変化 (遠い側)", description="主線から遠いリングの多重線の長さ変化", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_length_scale_percent: FloatProperty(name="長さ変化 (%)", description="多重線の長さ変化 (旧バージョン互換用。現在は主線寄り/遠い側の2項目を使用)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    thorn_multi_line_cross_enabled: BoolProperty(name="山谷を延ばして交差", description="ONでトゲの山と谷の多重線を延長して交差させる", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_color: FloatVectorProperty(name="線色", description="フキダシ主線の色", subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(name="塗り色", description="フキダシ内部の塗りの色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_opacity: FloatProperty(name="塗り不透明度", description="フキダシ内部の塗りの不透明度", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_material_name: StringProperty(name="塗りマテリアル", description="フキダシ内部の塗りに使うマテリアル名 (指定時は塗り色より優先)", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_material_name: StringProperty(name="線マテリアル", description="線種「マテリアル」で線の帯に使うマテリアル", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_material_mapping: EnumProperty(
        name="貼り方",
        description="線種「マテリアル」のテクスチャの貼り方",
        items=[
            ("tile", "そのまま (タイル)", "フキダシの領域基準で敷き詰めた模様を帯の形に切り抜く。閉じた形でも切れ目は出ない"),
            ("ribbon", "線に沿う (リボン)", "線の向きに沿って貼る。通常は周の長さに合わせて整数枚に調整するため切れ目は出ない"),
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
            ("none", "そのまま", "1枚を引き伸ばすため、左右がつながらない柄は始点終点で途切れる"),
            ("mirror", "ミラー往復", "行きと帰りで鏡像にして始点終点をつなぐ。柄は途中で左右反転する"),
            ("crossfade", "クロスフェード", "始点終点の手前を重ねて馴染ませる。出力時に適用し、画面の簡易表示では途切れて見えることがある"),
        ],
        default="none",
        update=_on_balloon_entry_changed,
    )  # type: ignore[valid-type]
    fill_blur_amount: FloatProperty(name="塗り輪郭ぼかし", description="塗りの輪郭をぼかす強さ", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_blur_axis: EnumProperty(name="ぼかす軸", description="輪郭のどちら側 (内側 / 輪郭上 / 外側) をぼかすか", items=_FILL_BLUR_AXIS_ITEMS, default="inside", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_blur_dither: BoolProperty(name="塗りぼかしをディザ化", description="ONで塗りのぼかしにディザ (砂目状のノイズ) をかけて階調を滑らかに見せる", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_enabled: BoolProperty(name="塗りグラデーション", description="ONで塗りをグラデーションにする", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_start_color: FloatVectorProperty(description="グラデーションの開始色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_end_color: FloatVectorProperty(description="グラデーションの終了色", subtype="COLOR", size=4, default=(0.82, 0.82, 0.82, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_gradient_angle_deg: FloatProperty(name="グラデーション角度", description="グラデーションの方向の角度", default=90.0, soft_min=-360.0, soft_max=360.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    outer_white_margin_enabled: BoolProperty(name="外側フチ", description="ONでフキダシの外側に縁取りを付ける", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    outer_white_margin_width_mm: FloatProperty(name="外側フチ幅 (mm)", description="外側の縁取りの幅", default=1.0, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    outer_white_margin_color: FloatVectorProperty(description="外側の縁取りの色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inner_white_margin_enabled: BoolProperty(name="内側フチ", description="ONでフキダシの内側に縁取りを付ける", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inner_white_margin_width_mm: FloatProperty(name="内側フチ幅 (mm)", description="内側の縁取りの幅", default=1.0, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    inner_white_margin_color: FloatVectorProperty(description="内側の縁取りの色", subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    blend_mode: EnumProperty(name="", items=_BLEND_MODE_ITEMS, default="normal", options={"HIDDEN"}, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    merge_group_id: StringProperty(name="結合フォルダ ID", description="同じIDのフキダシどうしを結合フォルダとしてまとめて表示するためのID (空なら結合なし)", default="")  # type: ignore[valid-type]
    parent_kind: StringProperty(name="親種別", description="レイヤー階層上の親の種類 (ページ / コマなど)。空の場合は配置位置から推定します", default="page", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", description="レイヤー階層上の親を指すID", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", description="所属するレイヤーフォルダのID (未所属なら空)", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    selected: BoolProperty(name="選択", description="レイヤー一覧やビューポートでの複数選択状態 (内部管理用)", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]

    # 反転 / 不透明度 (Meldex flipH/flipV/opacity 相当)
    flip_h: BoolProperty(name="水平反転", description="ONでフキダシを左右反転する", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    flip_v: BoolProperty(name="垂直反転", description="ONでフキダシを上下反転する", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        description="フキダシ全体の不透明度",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_balloon_entry_changed,
    )

    # 形状固有パラメータ・尻尾
    shape_params: PointerProperty(type=BMangaBalloonShapeParams, description="雲・トゲなど形状ごとの詳細パラメータ")  # type: ignore[valid-type]
    tails: CollectionProperty(type=BMangaBalloonTail, description="このフキダシに追加したしっぽの一覧")  # type: ignore[valid-type]

    # テキスト (実内容は TextEntry)
    text_id: StringProperty(name="Text ID", description="このフキダシに対応するテキストデータ (TextEntry) を指す内部ID", default="")  # type: ignore[valid-type]


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
