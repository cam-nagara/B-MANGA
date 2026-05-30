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

from ..utils import log

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

_LINE_STYLE_ITEMS = (
    ("none", "線なし", ""),
    ("solid", "実線", ""),
    ("dashed", "破線", ""),
    ("dotted", "点線", ""),
    ("double", "多重線", ""),
)

_MULTI_LINE_DIRECTION_ITEMS = (
    ("outside", "外側", ""),
    ("inside", "内側", ""),
    ("both", "両方向", ""),
)

_BLEND_MODE_ITEMS = (
    ("normal", "通常", ""),
    ("lighten", "比較 (明)", ""),
)


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


def _on_balloon_entry_changed(_self, context) -> None:
    _sync_balloon_curve(_self)
    _tag_balloon_redraw(context)


def _on_balloon_tail_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    work = getattr(scene, "bname_work", None) if scene is not None else None
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
    work = getattr(scene, "bname_work", None) if scene is not None else None
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
    work = getattr(scene, "bname_work", None) if scene is not None else None
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


class BNameBalloonTailPoint(bpy.types.PropertyGroup):
    x_mm: FloatProperty(name="X", default=0.0, update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0, update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]
    corner_type: EnumProperty(name="角のタイプ", items=_TAIL_POINT_CORNER_ITEMS, default="line", update=_on_balloon_tail_point_changed)  # type: ignore[valid-type]


class BNameBalloonTail(bpy.types.PropertyGroup):
    type: EnumProperty(items=_TAIL_TYPE_ITEMS, default="straight", update=_on_balloon_tail_changed)  # type: ignore[valid-type]
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
    points: CollectionProperty(type=BNameBalloonTailPoint)  # type: ignore[valid-type]


class BNameBalloonShapeParams(bpy.types.PropertyGroup):
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

    # Legacy parameters kept for older B-Name files/presets.
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

class BNameBalloonEntry(bpy.types.PropertyGroup):
    """フキダシ 1 件."""

    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        default=True,
        update=_on_balloon_entry_changed,
    )
    shape: EnumProperty(name="形状", items=_SHAPE_ITEMS, default="rect", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
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

    # 角丸 (全形状共通オプション、計画書 3.1.4.2a)
    rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    rounded_corner_radius_mm: FloatProperty(name="角半径 (mm)", default=3.0, min=0.0, soft_max=30.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]

    # 線・塗り
    line_style: EnumProperty(items=_LINE_STYLE_ITEMS, default="solid", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_width_mm: FloatProperty(name="線幅 (mm)", default=0.3, min=0.0, soft_max=10.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_count: IntProperty(name="線の本数", default=3, min=1, max=12, soft_max=12, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_width_mm: FloatProperty(name="多重線幅 (mm)", default=0.3, min=0.0, soft_max=10.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_spacing_mm: FloatProperty(name="多重線間隔 (mm)", default=0.4, min=0.0, soft_max=20.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_width_scale_percent: FloatProperty(name="線幅変化 (%)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    multi_line_spacing_scale_percent: FloatProperty(name="間隔変化 (%)", default=100.0, min=0.0, max=200.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    # 主線の谷/山の線幅: 主線の基本線幅 (line_width_mm) を 100% として % 指定。
    # 100% = 同じ太さ, 0% = その頂点で消える。辺全体で線形補間。
    line_valley_width_pct: FloatProperty(name="主線・谷の線幅 (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    line_peak_width_pct: FloatProperty(name="主線・山の線幅 (%)", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
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
    line_color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_opacity: FloatProperty(name="塗り不透明度", default=100.0, min=0.0, max=100.0, subtype="PERCENTAGE", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_material_name: StringProperty(name="塗りマテリアル", default="", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
    fill_blur_amount: FloatProperty(name="塗り輪郭ぼかし", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_balloon_entry_changed)  # type: ignore[valid-type]
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
    shape_params: PointerProperty(type=BNameBalloonShapeParams)  # type: ignore[valid-type]
    tails: CollectionProperty(type=BNameBalloonTail)  # type: ignore[valid-type]

    # テキスト (実内容は TextEntry)
    text_id: StringProperty(name="Text ID", default="")  # type: ignore[valid-type]


_CLASSES = (
    BNameBalloonTailPoint,
    BNameBalloonTail,
    BNameBalloonShapeParams,
    BNameBalloonEntry,
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
