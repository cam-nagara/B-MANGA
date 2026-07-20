"""Blender 実機用: レイヤー一覧のカラースウォッチが正しいマテリアルへ結び付くか確認.

退行の背景: Blender 5.x の ``GreasePencilLayer`` は ID プロパティを保持できない
(``id properties not supported for this type``)。レイヤー側へマテリアル名を保存
していた実装が無言で失敗し、全レイヤーの内部名が ``content`` で共通のため、
フォールバック名 ``BManga_GP_Layer_content`` に全レイヤーが衝突していた。結果、
どのレイヤーのスウォッチを動かしても同じ1個のマテリアルを書き換えてしまい、
描画色が変わらなくなっていた。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _gp_stack_entries(context):
    """レイヤー一覧のGP行を (ラベル, Object, contentレイヤー) で返す."""
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    entries = []
    for item in context.scene.bmanga_layer_stack:
        if item.kind != "gp":
            continue
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        entries.append((item.label, resolved.get("object"), resolved.get("target")))
    return entries


def _check_gp_swatch_binding(context) -> None:
    """各GPレイヤーのスウォッチが、そのレイヤー自身の描画マテリアルを指すこと."""
    from bmanga_dev.panels import gpencil_panel as gpp
    from bmanga_dev.utils import gpencil as gp_utils

    entries = _gp_stack_entries(context)
    assert len(entries) >= 2, f"GPレイヤーが2つ以上必要: {entries}"

    seen_materials = {}
    for label, obj, layer in entries:
        style = gpp._gp_color_style(obj, layer)
        assert style is not None, f"{label}: カラースウォッチのマテリアルが解決できない"

        resolved_mat = gp_utils.resolve_layer_material(obj, layer)
        slots = [m.name for m in obj.data.materials if m is not None]
        assert resolved_mat.name in slots, (
            f"{label}: 解決されたマテリアル {resolved_mat.name} が"
            f"自Objectのスロットに無い ({slots})"
        )

        # 実際に新規ストロークが使うマテリアル (active_material_index) と一致すること。
        active = obj.data.materials[obj.active_material_index]
        assert resolved_mat.name == active.name, (
            f"{label}: スウォッチ対象 {resolved_mat.name} と"
            f"描画に使うマテリアル {active.name} が食い違う"
        )

        # 別レイヤーと同じマテリアルを共有していないこと (これが今回の退行)。
        assert resolved_mat.name not in seen_materials, (
            f"{label}: マテリアル {resolved_mat.name} を"
            f"{seen_materials[resolved_mat.name]} と共有している"
        )
        seen_materials[resolved_mat.name] = label


def _check_gp_swatch_isolation(context) -> None:
    """片方のスウォッチを変えても、もう片方の色が変わらないこと."""
    from bmanga_dev.panels import gpencil_panel as gpp

    entries = _gp_stack_entries(context)
    (label_a, obj_a, layer_a), (label_b, obj_b, layer_b) = entries[0], entries[1]

    style_a = gpp._gp_color_style(obj_a, layer_a)
    style_b = gpp._gp_color_style(obj_b, layer_b)

    before_b = tuple(style_b.color)
    style_a.color = (1.0, 0.0, 0.0, 1.0)
    after_b = tuple(gpp._gp_color_style(obj_b, layer_b).color)

    assert tuple(style_a.color)[:3] == (1.0, 0.0, 0.0), (
        f"{label_a}: カラー変更が反映されていない"
    )
    assert after_b == before_b, (
        f"{label_b}: 別レイヤー({label_a})の色変更に巻き込まれた "
        f"{before_b} -> {after_b}"
    )

    # セカンダリカラー (塗り色) も同様に独立していること。
    before_fill_b = tuple(style_b.fill_color)
    style_a.fill_color = (0.0, 1.0, 0.0, 1.0)
    after_fill_b = tuple(gpp._gp_color_style(obj_b, layer_b).fill_color)
    assert after_fill_b == before_fill_b, (
        f"{label_b}: セカンダリカラーが別レイヤーに巻き込まれた"
    )


def _check_raster_swatch(context) -> None:
    """ラスターレイヤーがカラー/セカンダリカラーを持ち、独立して変更できること."""
    scene = context.scene
    rasters = list(getattr(scene, "bmanga_raster_layers", []) or [])
    assert rasters, "ラスターレイヤーが作成されていない"
    entry = rasters[0]

    assert hasattr(entry, "line_color"), "ラスターに line_color (カラー) が無い"
    assert hasattr(entry, "fill_color"), "ラスターに fill_color (セカンダリカラー) が無い"

    entry.line_color = (1.0, 0.0, 0.0, 1.0)
    entry.fill_color = (0.0, 0.0, 1.0, 1.0)
    assert tuple(entry.line_color)[:3] == (1.0, 0.0, 0.0)
    assert tuple(entry.fill_color)[:3] == (0.0, 0.0, 1.0)


def _check_raster_swatch_in_panel(context) -> None:
    """レイヤー一覧のラスター行が、カラースウォッチ用の entry を露出すること."""
    from bmanga_dev.panels import gpencil_panel as gpp
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    raster_items = [i for i in context.scene.bmanga_layer_stack if i.kind == "raster"]
    assert raster_items, "レイヤー一覧にラスター行が無い"

    controls: dict = {}
    item = raster_items[0]
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    gpp._draw_stack_data_row(
        _NullLayout(), controls, item, resolved, 0,
    )
    entry = controls.get("raster")
    assert entry is not None, "ラスター行がカラースウォッチ用 entry を露出していない"
    assert hasattr(entry, "line_color") and hasattr(entry, "fill_color")


class _NullLayout:
    """UI 描画呼び出しを吸収する最小スタブ (実UIコンテキスト無しで行描画を検証)."""

    def row(self, *args, **kwargs):
        return self

    def column(self, *args, **kwargs):
        return self

    def __getattr__(self, _name):
        def _noop(*args, **kwargs):
            return self
        return _noop


def _check_raster_detail_panel_exposes_both_colors(context) -> None:
    """ラスター詳細パネルがカラーとセカンダリカラーを両方露出すること."""
    from bmanga_dev.panels.detail_drawers import raster_fill

    scene = context.scene
    rasters = list(getattr(scene, "bmanga_raster_layers", []) or [])
    assert rasters, "ラスターが無い"
    entry = rasters[0]

    # raster_fill は prop_if を名前で import しているため、モジュール属性を
    # 差し替えて entry へのプロパティ描画呼び出しを捕捉する。
    captured: list = []
    orig = raster_fill.prop_if

    def _spy(layout, data, prop, **kwargs):
        if data is entry:
            captured.append(prop)
        return orig(layout, data, prop, **kwargs)

    session = type("S", (), {})()
    session.target = type("T", (), {})()
    session.target.data = entry
    session.target.stable_id = entry.id
    session.token = 0
    session.layout = type("L", (), {"column_count": 2, "max_columns": 2})()

    raster_fill.prop_if = _spy
    try:
        raster_fill.draw_raster_body(_NullLayout(), context, session, "detail")
    finally:
        raster_fill.prop_if = orig

    assert "line_color" in captured, f"詳細パネルにカラーが無い: {captured}"
    assert "fill_color" in captured, f"詳細パネルにセカンダリカラーが無い: {captured}"


def _check_raster_paint_survives_object_mode(context) -> None:
    """描画→オブジェクトモード切替でラスター画素とPNGが失われないこと."""
    import numpy as np
    from bmanga_dev.operators import coma_modal_state as cms
    from bmanga_dev.operators import raster_layer_op as rop

    entry, _idx = rop.active_raster_entry(context)
    assert entry is not None, "アクティブなラスターが取得できない"

    assert "FINISHED" in bpy.ops.bmanga.raster_layer_paint_enter()
    assert context.object.mode == "TEXTURE_PAINT", context.object.mode

    image = rop.ensure_raster_image(context, entry)
    buf = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(buf)
    buf[3::4] = 0.0
    buf[3:40000:4] = 1.0  # 一部を不透明 = 描画したことにする
    image.pixels.foreach_set(buf)
    image.update()
    rop.mark_raster_dirty(entry)
    before = float(buf[3::4].sum())
    assert before > 0.0

    cms.exit_drawing_mode(context)

    image2 = rop.ensure_raster_image(context, entry)
    after_buf = np.empty(len(image2.pixels), dtype=np.float32)
    image2.pixels.foreach_get(after_buf)
    after = float(after_buf[3::4].sum())
    assert abs(after - before) < 1.0, f"画素が失われた before={before} after={after}"

    work_dir = Path(context.scene.bmanga_work.work_dir)
    png = work_dir / entry.filepath_rel
    assert png.is_file() and png.stat().st_size > 0, f"PNGが保存されていない: {png}"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_color_swatch_"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()

    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ColorSwatch.bmanga"))
    assert "FINISHED" in result, result
    result = bpy.ops.bmanga.open_page_file(index=0)
    assert "FINISHED" in result, result

    context = bpy.context
    assert "FINISHED" in bpy.ops.bmanga.gpencil_layer_add(layer_name="レイヤーA")
    assert "FINISHED" in bpy.ops.bmanga.gpencil_layer_add(layer_name="レイヤーB")
    assert "FINISHED" in bpy.ops.bmanga.raster_layer_add(enter_paint=False)

    _check_gp_swatch_binding(context)
    _check_gp_swatch_isolation(context)
    _check_raster_swatch(context)
    _check_raster_swatch_in_panel(context)
    _check_raster_detail_panel_exposes_both_colors(context)
    _check_raster_paint_survives_object_mode(context)

    print("OK: blender_layer_color_swatch_check")


if __name__ == "__main__":
    main()
