"""ラスター、囲い塗り、グラデーションの共通詳細描画。"""

from __future__ import annotations

from .basic import body_columns, detail_operator, prop_if, prop_pair, set_operator_fields, value


def draw_raster_body(layout, _context, session, _mode) -> None:
    entry = session.target.data
    stable_id = session.target.stable_id
    columns = body_columns(layout, session)
    primary = columns[0]
    secondary = columns[min(1, len(columns) - 1)]

    settings = primary.box()
    settings.label(text="ラスター設定", icon="BRUSH_DATA")
    prop_if(settings, entry, "opacity", text="不透明度", slider=True)
    dpi = int(value(entry, "dpi", 0) or 0)
    settings.label(text=f"DPI: {dpi}")
    # カラー(濃い側) と セカンダリカラー(薄い側) を対で並べる。
    # レイヤー一覧のスウォッチと同じ2色をこの詳細パネルからも編集できる。
    prop_if(settings, entry, "line_color", text="カラー")
    prop_if(settings, entry, "fill_color", text="セカンダリカラー")

    depth = primary.box()
    depth.label(text="保存する階調")
    # 階調はPNG保存時の設定値であり、この画面では通常の編集値として扱う。
    # ボタン型の子オペレーターにすると親キャンセルとUndo境界が二重になる。
    prop_if(depth, entry, "bit_depth", text="階調")

    actions = secondary.box()
    actions.label(text="編集と保存")
    row = actions.row(align=True)
    save = detail_operator(
        row,
        "bmanga.detail_raster_save_png",
        text="PNGを保存",
        icon="FILE_TICK",
    )
    set_operator_fields(
        save,
        force=True,
        session_token=session.token,
        target_id=stable_id,
    )

def draw_fill_body(sidebar_top, _sidebar_below, body_cols, context, session, mode) -> None:
    """左列(サイドバー、プリセット一覧より上)=回転・端点指定・塗り範囲
    (配置依存の非プリセット設定)、右列(body_cols)=不透明度・色・グラデー
    ションのプリセット保存対象設定。プリセット一覧より下の列は使わない。
    """

    entry = session.target.data
    primary = body_cols[0]
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    namespace = str(getattr(session.target, "namespace", "") or "")
    fill_type = (
        "gradient"
        if preset_mode and namespace == "gradient"
        else str(value(entry, "fill_type", "solid") or "solid")
    )

    if not preset_mode:
        placement_box = sidebar_top.box()
        placement_box.label(text="配置")
        _draw_actual_fill_controls(placement_box, entry, fill_type)
        if bool(value(entry, "use_region", False)):
            _draw_fill_region(sidebar_top, entry)

    basic_box = primary.box()
    basic_box.label(text="グラデーション" if fill_type == "gradient" else "ベタ塗り")
    prop_if(basic_box, entry, "opacity", text="不透明度", slider=True)
    prop_if(basic_box, entry, "color", text="色")

    if fill_type == "gradient":
        _draw_gradient(primary, session, entry, preset_mode)


def _draw_actual_fill_controls(layout, entry, fill_type: str) -> None:
    rotation = layout.row()
    endpoint_mode = fill_type == "gradient" and bool(
        value(entry, "use_gradient_endpoints", False)
    )
    rotation.enabled = not endpoint_mode
    prop_if(
        rotation,
        entry,
        "rotation_deg",
        text="回転 (端点指定時は非対応)" if endpoint_mode else "回転",
    )
    prop_if(layout, entry, "fill_type", text="タイプ")


def _draw_gradient(layout, session, entry, preset_mode: bool) -> None:
    box = layout.box()
    box.label(text="グラデーション設定", icon="NODE_TEXTURE")
    prop_if(box, entry, "color2", text="色2")
    prop_if(box, entry, "gradient_type", text="形状")
    if not preset_mode and str(value(entry, "gradient_type", "linear")) == "linear":
        prop_if(box, entry, "gradient_angle", text="角度")
    if preset_mode:
        return
    _draw_gradient_curve(box, session.target.stable_id)
    if bool(value(entry, "use_gradient_endpoints", False)):
        endpoint = box.box()
        endpoint.label(text="グラデーション範囲", icon="ARROW_LEFTRIGHT")
        prop_pair(endpoint, entry, "gradient_start_x_mm", "gradient_start_y_mm")
        prop_pair(endpoint, entry, "gradient_end_x_mm", "gradient_end_y_mm")


def _draw_gradient_curve(layout, stable_id: str) -> None:
    from ...utils.fill_real_object import get_gradient_curve_node

    curve_node = get_gradient_curve_node(stable_id)
    if curve_node is None:
        return
    layout.label(text="濃度カーブ", icon="CURVE_DATA")
    layout.template_curve_mapping(curve_node, "mapping", type="NONE")


def _draw_fill_region(layout, entry) -> None:
    box = layout.box()
    box.label(text="塗り範囲", icon="SELECT_SET")
    prop_pair(box, entry, "region_x_mm", "region_y_mm")
    prop_pair(box, entry, "region_width_mm", "region_height_mm")


__all__ = ["draw_fill_body", "draw_raster_body"]
