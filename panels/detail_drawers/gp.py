"""1オブジェクト＝1レイヤーのGP詳細描画と、GPツールプリセット詳細描画。"""

from __future__ import annotations

from .basic import body_columns, has_field, prop_if, value


def draw_gp_body(layout, _context, session, _mode) -> None:
    """固定されたGPオブジェクトと内容レイヤーだけを読み取って描画する。"""

    target = session.target
    layer = target.data
    obj = target.object_ref
    columns = body_columns(layout, session)
    primary = columns[0]
    secondary = columns[min(1, len(columns) - 1)]

    layer_box = primary.box()
    layer_box.label(text="描画設定", icon="GREASEPENCIL")
    prop_if(layer_box, layer, "opacity", text="不透明度", slider=True)
    prop_if(layer_box, layer, "blend_mode", text="ブレンド")
    prop_if(layer_box, layer, "tint_color", text="色合い")

    style = _grease_pencil_style(obj)
    if style is None:
        layer_box.label(text="線と塗りの色を取得できません", icon="ERROR")
        return
    color_box = secondary.box()
    color_box.label(text="線と塗り")
    prop_if(color_box, style, "color", text="ストローク色")
    prop_if(color_box, style, "fill_color", text="塗り色")
    row = color_box.row(align=True)
    prop_if(row, style, "show_stroke", text="線を描く")
    prop_if(row, style, "show_fill", text="塗りを描く")


def _grease_pencil_style(obj):
    """既存マテリアルを読むだけにし、描画中の生成・割当を行わない。"""

    if obj is None:
        return None
    material = getattr(obj, "active_material", None)
    if material is None:
        slots = getattr(getattr(obj, "data", None), "materials", None)
        if slots is not None and len(slots) > 0:
            material = slots[0]
    style = getattr(material, "grease_pencil", None) if material is not None else None
    if style is None or not has_field(style, "color"):
        return None
    return style


def _size_row(box, settings, *, honor_size_mode: bool) -> None:
    row = box.row(align=True)
    if honor_size_mode and str(value(settings, "size_mode", "SCENE") or "") == "SCENE":
        prop_if(row, settings, "size_mm", text="サイズ (mm)")
    else:
        prop_if(row, settings, "size", text="サイズ (px)")
    prop_if(row, settings, "use_size_pressure", text="", icon="STYLUS_PRESSURE")


def _strength_row(box, settings) -> None:
    row = box.row(align=True)
    prop_if(row, settings, "strength", text="強さ", slider=True)
    prop_if(row, settings, "use_strength_pressure", text="", icon="STYLUS_PRESSURE")


def _size_strength_rows(box, settings, *, honor_size_mode: bool = False) -> None:
    _size_row(box, settings, honor_size_mode=honor_size_mode)
    _strength_row(box, settings)


def draw_gp_tool_body(sidebar_top, _sidebar_below, body_cols, _context, session, _mode) -> None:
    """グリースペンシルツールプリセットの機能選択と詳細設定を描画する。

    他ツールのプリセットと違い、保存対象はレイヤー設定ではなく Blender の
    ドローモード各ツール (ブラシ / フィル / トリム / 消しゴム / グラブ) の
    設定である。適用するとモード・ツール・ブラシが切り替わる。
    """

    settings = session.target.data
    body = body_cols[0] if body_cols else sidebar_top

    tool_box = body.box()
    tool_box.label(text="機能", icon="TOOL_SETTINGS")
    tool_column = tool_box.column(align=True)
    tool_column.prop(settings, "tool", expand=True)

    tool = str(value(settings, "tool", "brush") or "brush")
    detail_box = body.box()
    if tool == "brush":
        detail_box.label(text="ブラシ設定", icon="BRUSH_DATA")
        prop_if(detail_box, settings, "brush_asset", text="使用ブラシ")
        prop_if(detail_box, settings, "size_mode", text="サイズの基準")
        _size_strength_rows(detail_box, settings, honor_size_mode=True)
        prop_if(detail_box, settings, "stroke_type", text="ストロークタイプ")
        row = detail_box.row(align=True)
        row.label(text="キャップ")
        prop_if(row, settings, "caps_type", text="キャップ", expand=True)
        prop_if(detail_box, settings, "hardness", text="硬さ", slider=True)
        prop_if(detail_box, settings, "use_smooth_stroke", text="手ブレ補正")
        smooth_row = detail_box.row(align=True)
        smooth_row.active = bool(value(settings, "use_smooth_stroke", False))
        prop_if(smooth_row, settings, "smooth_stroke_factor", text="補正の強さ", slider=True)
    elif tool == "fill":
        detail_box.label(text="フィル設定", icon="SNAP_FACE")
        row = detail_box.row(align=True)
        row.label(text="方向")
        prop_if(row, settings, "fill_direction", text="方向", expand=True)
        prop_if(detail_box, settings, "fill_solver", text="計算方式")
        if str(value(settings, "fill_solver", "DELAUNAY") or "") == "PIXEL":
            prop_if(detail_box, settings, "fill_factor", text="精度")
            prop_if(detail_box, settings, "fill_dilate", text="拡張")
            prop_if(detail_box, settings, "size", text="線の太さ (px)")
        else:
            prop_if(detail_box, settings, "size_mode", text="サイズの基準")
            if str(value(settings, "size_mode", "SCENE") or "") == "SCENE":
                prop_if(detail_box, settings, "size_mm", text="線の太さ (mm)")
            else:
                prop_if(detail_box, settings, "size", text="線の太さ (px)")
        prop_if(detail_box, settings, "fill_extend_factor", text="すき間閉じサイズ")
        extend_row = detail_box.row(align=True)
        extend_row.active = float(value(settings, "fill_extend_factor", 0.0) or 0.0) > 0.0
        prop_if(extend_row, settings, "fill_extend_mode", text="閉じ方")
    elif tool == "trim":
        detail_box.label(text="トリム設定", icon="GREASEPENCIL")
        prop_if(detail_box, settings, "use_active_layer_only", text="アクティブレイヤーのみ")
        prop_if(detail_box, settings, "use_keep_caps", text="キャップを保持")
        note = detail_box.column(align=True)
        note.enabled = False
        note.label(text="ドラッグした線でストロークを切り取ります")
    elif tool == "erase":
        detail_box.label(text="消しゴム設定", icon="GREASEPENCIL")
        row = detail_box.row(align=True)
        prop_if(row, settings, "eraser_mode", text="消しゴムモード", expand=True)
        _size_strength_rows(detail_box, settings)
        prop_if(detail_box, settings, "use_active_layer_only", text="アクティブレイヤーのみ")
        if str(value(settings, "eraser_mode", "HARD") or "") in {"HARD", "SOFT"}:
            prop_if(detail_box, settings, "use_keep_caps", text="キャップを保持")
    elif tool == "grab":
        detail_box.label(text="グラブ設定", icon="VIEW_PAN")
        _size_strength_rows(detail_box, settings)
        note = detail_box.column(align=True)
        note.enabled = False
        note.label(text="スカルプトモードでストロークをつかんで動かします")


__all__ = ["draw_gp_body", "draw_gp_tool_body"]
