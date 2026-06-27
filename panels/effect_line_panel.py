"""効果線ツールパネル (Phase 3 骨格)."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..utils import balloon_shapes, effect_inout_curve
from . import corner_radius_ui

B_NAME_CATEGORY = "B-MANGA"


def _draw_shape_settings(layout, params, prefix: str, label: str, *, frame_toggle: bool = False) -> None:
    box = layout.box()
    box.label(text=label)
    if frame_toggle:
        box.prop(params, "start_to_coma_frame")
    content = box.column(align=True)
    if frame_toggle:
        content.enabled = not bool(params.start_to_coma_frame)
    shape_attr = f"{prefix}_shape"
    content.prop(params, shape_attr)
    shape = balloon_shapes.normalize_shape(getattr(params, shape_attr))
    if shape == "rect":
        rounded_attr = f"{prefix}_rounded_corner_enabled"
        content.prop(params, rounded_attr)
        sub = content.column(align=True)
        sub.enabled = bool(getattr(params, rounded_attr))
        corner_radius_ui.draw_corner_radius(sub, params, prefix=f"{prefix}_rounded_corner")
    if balloon_shapes.is_dynamic_meldex_shape(shape):
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_bump_width_mm")
        row.prop(params, f"{prefix}_cloud_bump_width_jitter", text="乱れ")
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_bump_height_mm")
        row.prop(params, f"{prefix}_cloud_bump_height_jitter", text="乱れ")
        content.prop(params, f"{prefix}_cloud_offset_percent")
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_sub_width_ratio")
        row.prop(params, f"{prefix}_cloud_sub_width_jitter", text="乱れ")
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_sub_height_ratio")
        row.prop(params, f"{prefix}_cloud_sub_height_jitter", text="乱れ")


def _draw_white_outline_settings(layout, params) -> None:
    box = layout.box()
    box.label(text="白抜き線")
    box.prop(params, "opacity", slider=True)
    row = box.row(align=True)
    row.prop(params, "white_outline_count")
    row.prop(params, "white_outline_angle_deg")
    box.prop(params, "white_outline_width_mm")
    row = box.row(align=True)
    row.prop(params, "white_outline_width_jitter_enabled")
    sub = row.row()
    sub.enabled = params.white_outline_width_jitter_enabled
    sub.prop(params, "white_outline_width_min_percent", text="最小")
    row = box.row(align=True)
    row.prop(params, "white_outline_length_jitter_enabled")
    sub = row.row()
    sub.enabled = params.white_outline_length_jitter_enabled
    sub.prop(params, "white_outline_length_min_percent", text="最小")

    white_box = layout.box()
    white_box.label(text="白線")
    white_box.prop(params, "white_outline_white_ratio_percent")
    row = white_box.row(align=True)
    row.prop(params, "white_outline_white_line_count_auto", toggle=True)
    count_row = row.row()
    count_row.enabled = not bool(params.white_outline_white_line_count_auto)
    count_row.prop(params, "white_outline_white_line_count", text="本数")
    row = white_box.row(align=True)
    row.prop(params, "white_outline_spacing_mm")
    row.prop(params, "white_outline_white_brush_mm")
    white_box.prop(params, "white_outline_white_attenuation")
    row = white_box.row(align=True)
    row.prop(params, "white_outline_white_in_percent")
    row.prop(params, "white_outline_white_out_percent")
    white_box.prop(params, "white_outline_white_inout_range_mode")
    range_row = white_box.row(align=True)
    if params.white_outline_white_inout_range_mode == "length":
        range_row.prop(params, "white_outline_white_in_range_mm")
        range_row.prop(params, "white_outline_white_out_range_mm")
    else:
        range_row.prop(params, "white_outline_white_in_range_percent")
        range_row.prop(params, "white_outline_white_out_range_percent")

    black_box = layout.box()
    black_box.label(text="黒線")
    row = black_box.row(align=True)
    row.prop(params, "white_outline_black_line_count_auto", toggle=True)
    count_row = row.row()
    count_row.enabled = not bool(params.white_outline_black_line_count_auto)
    count_row.prop(params, "white_outline_black_line_count", text="本数")
    black_box.prop(params, "white_outline_black_direction")
    row = black_box.row(align=True)
    row.prop(params, "white_outline_black_brush_mm")
    row.prop(params, "white_outline_black_spacing_mm")
    row = black_box.row(align=True)
    row.prop(params, "white_outline_black_width_scale_percent")
    row.prop(params, "white_outline_black_attenuation")
    row = black_box.row(align=True)
    row.prop(params, "white_outline_black_length_scale_near_percent")
    row.prop(params, "white_outline_black_length_scale_far_percent")


def _inout_curve_nodes_for_draw(params):
    try:
        effect_inout_curve.sync_ui_nodes_to_params(params)
    except Exception:  # noqa: BLE001
        pass
    in_node, out_node = effect_inout_curve.get_ui_nodes()
    if in_node is not None or out_node is not None:
        return in_node, out_node
    try:
        return effect_inout_curve.ensure_ui_nodes(params)
    except Exception:  # noqa: BLE001
        return effect_inout_curve.get_ui_nodes()


def draw_inout_curve_mapping(layout, params) -> None:
    in_node, out_node = _inout_curve_nodes_for_draw(params)
    if in_node is not None:
        layout.label(text="入りカーブ")
        layout.template_curve_mapping(in_node, "mapping", type="NONE")
    if out_node is not None:
        layout.label(text="抜きカーブ")
        layout.template_curve_mapping(out_node, "mapping", type="NONE")


def draw_effect_path_settings(layout, params) -> None:
    path_box = layout.box()
    path_box.label(text="パス")
    row = path_box.row(align=True)
    row.prop(params, "base_path_enabled", text="基準パス")
    edit = row.row(align=True)
    edit.enabled = bool(getattr(params, "base_path_enabled", False))
    edit.operator("bmanga.effect_line_base_path_edit", text="編集", icon="CURVE_BEZCURVE")

    image_box = layout.box()
    image_box.label(text="画像線")
    image_box.prop(params, "line_image_path", text="画像")
    image_box.prop(params, "line_image_draw_mode", text="表示方法")
    row = image_box.row(align=True)
    row.prop(params, "line_image_brush_size_mm", text="ブラシサイズ")
    row.prop(params, "line_image_aspect_ratio", text="縦横比")
    row = image_box.row(align=True)
    row.prop(params, "line_image_angle_deg", text="画像の角度")
    row.prop(params, "line_image_spacing_percent", text="間隔")
    if str(getattr(params, "line_image_draw_mode", "ribbon") or "ribbon") == "stamp":
        image_box.prop(params, "line_image_stamp_angle_mode", text="角度")
        if str(getattr(params, "line_image_stamp_angle_mode", "") or "") == "object":
            image_box.prop_search(
                params,
                "line_image_stamp_angle_object_name",
                bpy.data,
                "objects",
                text="方向オブジェクト",
            )
    else:
        image_box.prop(params, "line_image_ribbon_repeat_mode", text="リボン")


def draw_effect_params(
    layout,
    params,
    *,
    with_generate_button: bool = True,
    fixed_effect_type: str | None = None,
    show_type: bool = True,
    show_rotation: bool = True,
    show_opacity: bool = True,
    columns=None,
) -> None:
    """効果線パラメータを ``layout`` に描画 (パネル / 詳細設定ダイアログ共通).

    ``with_generate_button=True`` で末尾に「効果線を追加」 ボタンを追加。
    ``params`` は ``scene.bmanga_effect_line_params`` (BMangaEffectLineParams)。
    ``columns`` に複数の column を渡すと、設定群を列に分配する
    (縦長になりすぎる詳細設定ダイアログ用。None なら従来どおり縦一列)。
    """
    if params is None:
        layout.label(text="未初期化", icon="ERROR")
        return

    cols = [c for c in (columns or ()) if c is not None] or [layout]

    def _col(index: int):
        return cols[min(int(index), len(cols) - 1)]

    effect_type = str(fixed_effect_type or getattr(params, "effect_type", "focus") or "focus")
    if show_type:
        box = _col(0).box()
        box.label(text="種類")
        box.prop(params, "effect_type")
        if effect_type != "speed" and show_rotation:
            box.prop(params, "rotation_deg")
    elif effect_type != "speed" and show_rotation:
        box = _col(0).box()
        box.label(text="向き")
        box.prop(params, "rotation_deg")

    if effect_type == "white_outline":
        _draw_shape_settings(_col(0), params, "start", "始点形状", frame_toggle=True)
        _draw_shape_settings(_col(0), params, "end", "終点形状")
        _draw_white_outline_settings(_col(1), params)
        draw_effect_path_settings(_col(2), params)
        if with_generate_button:
            _col(0).operator("bmanga.effect_line_generate", icon="STROKE")
        return

    if effect_type != "speed":
        _draw_shape_settings(_col(0), params, "start", "始点形状", frame_toggle=True)
        _draw_shape_settings(_col(0), params, "end", "終点形状")

    box = _col(1).box()
    box.label(text="線")
    box.prop(params, "brush_size_mm")
    row = box.row(align=True)
    row.prop(params, "brush_jitter_enabled", text="乱れ")
    sub = row.row()
    sub.enabled = params.brush_jitter_enabled
    sub.prop(params, "brush_jitter_amount", text="")
    row = box.row(align=True)
    row.prop(params, "length_jitter_enabled", text="始点乱れ")
    sub = row.row()
    sub.enabled = params.length_jitter_enabled
    sub.prop(params, "length_jitter_amount", text="")
    row = box.row(align=True)
    row.prop(params, "end_length_jitter_enabled", text="終点乱れ")
    sub = row.row()
    sub.enabled = params.end_length_jitter_enabled
    sub.prop(params, "end_length_jitter_amount", text="")

    if effect_type != "beta_flash":
        box.prop(params, "spacing_mode")
        if params.spacing_mode == "angle":
            box.prop(params, "spacing_angle_deg")
        else:
            box.prop(params, "spacing_distance_mm")
            box.prop(params, "spacing_density_compensation")
        row = box.row(align=True)
        row.prop(params, "spacing_jitter_enabled", text="間隔乱れ")
        sub = row.row()
        sub.enabled = params.spacing_jitter_enabled
        sub.prop(params, "spacing_jitter_amount", text="")
        box.prop(params, "max_line_count")

        bundle_box = _col(1).box()
        bundle_box.label(text="まとまり")
        bundle_box.prop(params, "bundle_enabled")
        sub = bundle_box.column(align=True)
        sub.enabled = bool(params.bundle_enabled)
        row = sub.row(align=True)
        row.prop(params, "bundle_line_count")
        row.prop(params, "bundle_line_count_jitter", text="乱れ")
        row = sub.row(align=True)
        row.prop(params, "bundle_gap_mm")
        row.prop(params, "bundle_gap_jitter_amount", text="乱れ")
        row = sub.row(align=True)
        row.prop(params, "bundle_jagged_enabled")
        jag = row.row()
        jag.enabled = params.bundle_jagged_enabled
        jag.prop(params, "bundle_jagged_height_percent", text="高さ")

    box = _col(2).box()
    box.label(text="入り抜き")
    box.prop(params, "inout_apply")
    row = box.row(align=True)
    row.prop(params, "in_percent")
    row.prop(params, "out_percent")
    row = box.row(align=True)
    row.prop(params, "in_start_percent")
    row.prop(params, "out_start_percent")
    draw_inout_curve_mapping(box, params)
    draw_effect_path_settings(_col(2), params)

    box = _col(1).box()
    box.label(text="色")
    if show_opacity:
        box.prop(params, "opacity", slider=True)
    box.prop(params, "line_color")
    if effect_type not in {"speed", "white_outline"}:
        box.prop(params, "fill_color")
        box.prop(params, "fill_opacity")
        box.prop(params, "fill_base_shape")
    if effect_type in {"focus", "uni_flash"}:
        row = box.row(align=True)
        row.prop(params, "white_underlay_enabled", toggle=True)
        sub = row.row(align=True)
        sub.enabled = bool(params.white_underlay_enabled)
        sub.prop(params, "white_underlay_width_percent", text="幅")
        sub.prop(params, "white_underlay_color", text="")
    if effect_type == "uni_flash":
        box.prop(params, "uni_flash_offset_percent")

    if effect_type == "speed":
        box = _col(1).box()
        box.label(text="流線")
        box.prop(params, "speed_angle_deg")
        box.prop(params, "speed_line_count")

    if with_generate_button:
        _col(0).operator("bmanga.effect_line_generate", icon="STROKE")


class BMANGA_PT_effect_line(Panel):
    bl_idname = "BMANGA_PT_effect_line"
    bl_label = "効果線"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 11
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        params = getattr(context.scene, "bmanga_effect_line_params", None)
        draw_effect_params(layout, params)


_CLASSES = (BMANGA_PT_effect_line,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
