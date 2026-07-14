"""1オブジェクト＝1レイヤーのGP詳細描画。"""

from __future__ import annotations

from .basic import body_columns, has_field, prop_if


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
        layer_box.label(text="線と塗りの色を取得できません", icon="INFO")
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


__all__ = ["draw_gp_body"]
