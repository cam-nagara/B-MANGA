"""Blender 実機用: コマ枠の選択辺ハイライトを確認。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_edge_highlight",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_edge_highlight"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _FakeShader:
    def __init__(self) -> None:
        self.colors: list[tuple[float, float, float, float]] = []

    def bind(self) -> None:
        return None

    def uniform_float(self, name: str, value) -> None:
        if name == "color":
            self.colors.append(tuple(float(v) for v in value))


class _FakeBatch:
    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls

    def draw(self, _shader) -> None:
        self._calls[-1]["drawn"] = True


class _FakeContext:
    def __init__(self, scene, wm) -> None:
        self.scene = scene
        self.window_manager = wm
        self.area = None


class _FakeScene:
    bmanga_active_layer_kind = "coma"


class _FakeWindowManager:
    bmanga_edge_select_kind = "border"
    bmanga_edge_select_page = 0
    bmanga_edge_select_coma = 0
    bmanga_edge_select_edge = -1
    bmanga_edge_select_vertex = -1


class _FakeWork:
    def __init__(self, page) -> None:
        self.pages = [page]
        self.paper = type(
            "Paper",
            (),
            {
                "canvas_width_mm": 100.0,
                "canvas_height_mm": 100.0,
                "start_side": "right",
                "read_direction": "left",
            },
        )()


class _FakePage:
    def __init__(self, panel) -> None:
        self.comas = [panel]


class _FakePanel:
    shape_type = "rect"
    rect_x_mm = 0.0
    rect_y_mm = 0.0
    rect_width_mm = 10.0
    rect_height_mm = 10.0


def _assert_band(module, function_name: str) -> None:
    calls: list[dict] = []

    def fake_batch_for_shader(_shader, mode, attrs, indices=None):
        calls.append({"mode": mode, "attrs": attrs, "indices": indices, "drawn": False})
        return _FakeBatch(calls)

    original = module.batch_for_shader
    module.batch_for_shader = fake_batch_for_shader
    try:
        shader = _FakeShader()
        getattr(module, function_name)(
            shader,
            (10.0, 20.0),
            (110.0, 20.0),
            color=(1.0, 0.0, 0.68, 0.42),
            width_px=10.0,
        )
    finally:
        module.batch_for_shader = original
    assert len(calls) == 1, "選択辺のハイライト帯が描画されていません"
    call = calls[0]
    assert call["mode"] == "TRIS", "選択辺のハイライト帯が面として描画されていません"
    assert call["drawn"], "選択辺のハイライト帯がdrawされていません"
    positions = call["attrs"]["pos"]
    assert len(positions) == 4, "選択辺のハイライト帯の頂点数が不正です"
    y_values = sorted(round(float(p[1]), 3) for p in positions)
    assert y_values == [15.0, 15.0, 25.0, 25.0], f"選択辺のハイライト帯幅が不正です: {y_values}"


def _assert_active_coma_draws_outer_once(module) -> None:
    panel = _FakePanel()
    page = _FakePage(panel)
    work = _FakeWork(page)
    context = _FakeContext(_FakeScene(), _FakeWindowManager())
    edge_polys: list[list[tuple[float, float]]] = []

    originals = {
        "selected_coma_refs": module.object_selection.selected_coma_refs,
        "page_visible": module.overlay_visibility.page_visible,
        "coma_visible": module.overlay_visibility.coma_visible,
        "get_overlay_pointer": module.edge_selection.get_overlay_pointer,
        "from_builtin": module.gpu.shader.from_builtin,
        "_page_offset": module._page_offset,
        "_draw_edge": module._draw_edge,
    }

    def fake_draw_edge(_shader, _region, _rv3d, poly, edge_index, *, pointer=None):
        del _shader, _region, _rv3d, edge_index, pointer
        edge_polys.append([(round(float(x), 3), round(float(y), 3)) for x, y in poly])

    try:
        module.object_selection.selected_coma_refs = lambda _context: [(0, page, 0, panel)]
        module.overlay_visibility.page_visible = lambda _page: True
        module.overlay_visibility.coma_visible = lambda _panel: True
        module.edge_selection.get_overlay_pointer = lambda _context: None
        module.gpu.shader.from_builtin = lambda _name: _FakeShader()
        module._page_offset = lambda _context, _work, _page_index: (0.0, 0.0)
        module._draw_edge = fake_draw_edge
        module.draw(context, work, object(), object())
    finally:
        module.object_selection.selected_coma_refs = originals["selected_coma_refs"]
        module.overlay_visibility.page_visible = originals["page_visible"]
        module.overlay_visibility.coma_visible = originals["coma_visible"]
        module.edge_selection.get_overlay_pointer = originals["get_overlay_pointer"]
        module.gpu.shader.from_builtin = originals["from_builtin"]
        module._page_offset = originals["_page_offset"]
        module._draw_edge = originals["_draw_edge"]

    assert len(edge_polys) == 4, f"コマ選択ハンドルが二重描画されています: {len(edge_polys)}"
    expected = [(-3.0, -3.0), (13.0, -3.0), (13.0, 13.0), (-3.0, 13.0)]
    assert all(poly == expected for poly in edge_polys), edge_polys


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        from bmanga_dev_edge_highlight.operators import coma_edge_move_op
        from bmanga_dev_edge_highlight.ui import overlay_coma_selection

        _assert_band(overlay_coma_selection, "_draw_screen_segment_band")
        _assert_band(coma_edge_move_op, "_draw_screen_segment_band")
        _assert_active_coma_draws_outer_once(overlay_coma_selection)
        print("BMANGA_COMA_EDGE_HIGHLIGHT_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
