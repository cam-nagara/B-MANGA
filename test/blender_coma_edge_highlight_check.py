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


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        from bmanga_dev_edge_highlight.operators import coma_edge_move_op
        from bmanga_dev_edge_highlight.ui import overlay_coma_selection

        _assert_band(overlay_coma_selection, "_draw_screen_segment_band")
        _assert_band(coma_edge_move_op, "_draw_screen_segment_band")
        print("BMANGA_COMA_EDGE_HIGHLIGHT_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
