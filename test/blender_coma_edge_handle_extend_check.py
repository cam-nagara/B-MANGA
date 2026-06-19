"""Blender 実機(背景)用: コマ枠三角ハンドルの裁ち落とし枠外拡張を確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_edge_extend",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_edge_extend"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _shim(work, selection):
    return SimpleNamespace(
        _work=work,
        _selection=selection,
        report=lambda *_args, **_kwargs: None,
        _save_changes=lambda: None,
        _push_undo_step=lambda _message: None,
    )


def _add_panel(page, coma_id: str, points, width_mm: float, *, style: str = "solid", blur_amount: float = 0.0):
    from bmanga_dev_coma_edge_extend.operators import coma_edge_move_op

    panel = page.comas.add()
    panel.id = coma_id
    panel.coma_id = coma_id
    panel.title = coma_id
    panel.border.style = style
    panel.border.width_mm = width_mm
    panel.border.blur_amount = blur_amount
    coma_edge_move_op._set_coma_polygon(panel, list(points))
    return panel


def _assert_close(actual: float, expected: float, label: str) -> None:
    if abs(float(actual) - float(expected)) > 1.0e-5:
        raise AssertionError(f"{label}: actual={actual}, expected={expected}")


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        from bmanga_dev_coma_edge_extend.core.work import get_work
        from bmanga_dev_coma_edge_extend.io import schema
        from bmanga_dev_coma_edge_extend import preferences
        from bmanga_dev_coma_edge_extend.operators import coma_edge_move_op
        from bmanga_dev_coma_edge_extend.utils import coma_border_texture, geom

        work = get_work(bpy.context)
        work.loaded = True
        original_get_preferences = preferences.get_preferences
        preferences.get_preferences = lambda _context=None: None
        if not coma_edge_move_op._snap_gutter_to_finish_enabled(bpy.context):
            raise AssertionError("プリファレンス未設定時のノド側スナップ初期値がオンではありません")
        fake_prefs = SimpleNamespace(snap_gutter_to_finish=True)
        preferences.get_preferences = lambda _context=None: fake_prefs
        paper = work.paper
        paper.canvas_width_mm = 100.0
        paper.canvas_height_mm = 120.0
        paper.finish_width_mm = 80.0
        paper.finish_height_mm = 100.0
        paper.bleed_mm = 5.0
        paper.inner_frame_width_mm = 40.0
        paper.inner_frame_height_mm = 60.0
        paper.inner_frame_offset_x_mm = 0.0
        paper.inner_frame_offset_y_mm = 0.0
        paper.start_side = "right"
        paper.read_direction = "left"

        page = work.pages.add()
        page.id = "p1"
        br = geom.bleed_rect(paper)
        fr = geom.finish_rect(paper)
        ir = geom.inner_frame_rect(paper)
        width_mm = 4.0
        panel = _add_panel(
            page,
            "c1",
            [(ir.x, ir.y), (ir.x2, ir.y), (ir.x2, ir.y2), (ir.x, ir.y2)],
            width_mm,
        )
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 2}),
            2,
        )
        top_y = max(y for _x, y in coma_edge_move_op._coma_polygon(panel))
        _assert_close(top_y, br.y2 + width_mm * 0.5, "基本枠から裁ち落とし枠外への拡張")

        page.comas.clear()
        gutter_default = _add_panel(
            page,
            "gutter_default",
            [(ir.x, ir.y), (ir.x2, ir.y), (ir.x2, ir.y2), (ir.x, ir.y2)],
            width_mm,
        )
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 3}),
            2,
        )
        left_x_default = min(x for x, _y in coma_edge_move_op._coma_polygon(gutter_default))
        _assert_close(
            left_x_default,
            fr.x,
            "初期値ではノド側が仕上がり枠へ拡張",
        )

        page.comas.clear()
        fake_prefs.snap_gutter_to_finish = False
        gutter_off = _add_panel(
            page,
            "gutter_off",
            [(ir.x, ir.y), (ir.x2, ir.y), (ir.x2, ir.y2), (ir.x, ir.y2)],
            width_mm,
        )
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 3}),
            2,
        )
        left_x_off = min(x for x, _y in coma_edge_move_op._coma_polygon(gutter_off))
        _assert_close(
            left_x_off,
            br.x - width_mm * 0.5,
            "プリファレンスをオフにするとノド側も裁ち落とし枠外へ拡張",
        )

        page.comas.clear()
        fake_prefs.snap_gutter_to_finish = True
        gutter_finish = _add_panel(
            page,
            "gutter_finish",
            [(ir.x, ir.y), (ir.x2, ir.y), (ir.x2, ir.y2), (ir.x, ir.y2)],
            width_mm,
        )
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 3}),
            2,
        )
        left_x_finish = min(x for x, _y in coma_edge_move_op._coma_polygon(gutter_finish))
        _assert_close(left_x_finish, fr.x, "ノド側は仕上がり枠にスナップ")
        payload = schema.coma_entry_to_dict(gutter_finish)
        if "snapGutterToFinish" in payload:
            raise AssertionError("ノド側スナップ設定がコマ個別データに残っています")

        page.comas.clear()
        fore_edge = _add_panel(
            page,
            "fore_edge",
            [(ir.x, ir.y), (ir.x2, ir.y), (ir.x2, ir.y2), (ir.x, ir.y2)],
            width_mm,
        )
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 1}),
            2,
        )
        right_x_fore = max(x for x, _y in coma_edge_move_op._coma_polygon(fore_edge))
        _assert_close(
            right_x_fore,
            br.x2 + width_mm * 0.5,
            "小口側は裁ち落とし枠外へ拡張",
        )

        paper.start_side = "left"
        paper.read_direction = "right"
        page.comas.clear()
        left_page_gutter = _add_panel(
            page,
            "left_page_gutter",
            [(ir.x, ir.y), (ir.x2, ir.y), (ir.x2, ir.y2), (ir.x, ir.y2)],
            width_mm,
        )
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 1}),
            2,
        )
        right_x_finish = max(x for x, _y in coma_edge_move_op._coma_polygon(left_page_gutter))
        _assert_close(right_x_finish, fr.x2, "左ページのノド側は仕上がり枠にスナップ")
        paper.start_side = "right"
        paper.read_direction = "left"

        page.comas.clear()
        left = _add_panel(page, "left", [(10.0, 10.0), (50.0, 10.0), (50.0, 50.0), (10.0, 50.0)], width_mm)
        _add_panel(page, "right", [(50.0, 10.0), (90.0, 10.0), (90.0, 50.0), (50.0, 50.0)], width_mm)
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 1}),
            2,
        )
        right_x = max(x for x, _y in coma_edge_move_op._coma_polygon(left))
        _assert_close(right_x, 50.0 + width_mm * 0.5, "隣接コマ内側方向への半線幅拡張")

        # 軽微な数値ズレ (0.1mm) があっても「ピッタリ重なり」扱いで半線幅だけ
        # 拡張する。 ここが厳しすぎると、 通常分岐で隣接コマの遠い辺にスナップ
        # してコマ幅分の大きな拡張が起きてしまう。
        page.comas.clear()
        gap = 0.1
        left_gap = _add_panel(
            page, "left_gap",
            [(10.0, 10.0), (50.0 + gap, 10.0), (50.0 + gap, 50.0), (10.0, 50.0)],
            width_mm,
        )
        _add_panel(
            page, "right_gap",
            [(50.0, 10.0), (90.0, 10.0), (90.0, 50.0), (50.0, 50.0)],
            width_mm,
        )
        coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
            _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 1}),
            2,
        )
        right_x_gap = max(x for x, _y in coma_edge_move_op._coma_polygon(left_gap))
        _assert_close(
            right_x_gap, 50.0 + gap + width_mm * 0.5,
            "0.1mm の数値ズレでも半線幅だけ拡張",
        )

        page.comas.clear()
        brush_total = coma_border_texture.brush_total_width_mm(width_mm, 1.0)
        _assert_close(brush_total, width_mm, "輪郭ぼかしは線幅を超えない")
        original_brush_total = coma_border_texture.brush_total_width_mm
        coma_border_texture.brush_total_width_mm = lambda _width, _blur: width_mm * 3.0
        brush_panel = _add_panel(
            page,
            "brush",
            [(ir.x, ir.y), (ir.x2, ir.y), (ir.x2, ir.y2), (ir.x, ir.y2)],
            width_mm,
            style="brush",
            blur_amount=1.0,
        )
        try:
            coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
                _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 2}),
                2,
            )
            brush_top_y = max(y for _x, y in coma_edge_move_op._coma_polygon(brush_panel))
            _assert_close(brush_top_y, br.y2 + width_mm * 0.5, "輪郭ぼかしの拡張量")
        finally:
            coma_border_texture.brush_total_width_mm = original_brush_total

        page.comas.clear()
        coma_border_texture.brush_total_width_mm = lambda _width, _blur: width_mm * 3.0
        brush_left = _add_panel(
            page,
            "brush_left",
            [(10.0, 10.0), (50.0, 10.0), (50.0, 50.0), (10.0, 50.0)],
            width_mm,
            style="brush",
            blur_amount=1.0,
        )
        _add_panel(page, "brush_right", [(50.0, 10.0), (90.0, 10.0), (90.0, 50.0), (50.0, 50.0)], width_mm)
        try:
            coma_edge_move_op.BMANGA_OT_coma_edge_move._do_extend(
                _shim(work, {"type": "edge", "page": 0, "coma": 0, "edge": 1}),
                2,
            )
            brush_right_x = max(x for x, _y in coma_edge_move_op._coma_polygon(brush_left))
            _assert_close(brush_right_x, 50.0 + width_mm * 0.5, "輪郭ぼかしの隣接コマ内側拡張量")
        finally:
            coma_border_texture.brush_total_width_mm = original_brush_total
        print("BMANGA_COMA_EDGE_HANDLE_EXTEND_OK")
    finally:
        if "preferences" in locals() and "original_get_preferences" in locals():
            preferences.get_preferences = original_get_preferences
        mod.unregister()


if __name__ == "__main__":
    main()
