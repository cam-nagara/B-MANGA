"""Blender実機用: 効果線の単一ハンドル表示と集中線初期値を確認する。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_effect_handle_defaults"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _check_initial_values(context) -> None:
    params = context.scene.bmanga_effect_line_params
    assert params.effect_type == "focus", params.effect_type
    assert abs(float(params.in_start_percent) - 0.0) < 1.0e-6, params.in_start_percent
    assert abs(float(params.out_start_percent) - 100.0) < 1.0e-6, params.out_start_percent


def _check_effect_overlay(context) -> None:
    from bmanga_dev_effect_handle_defaults.operators import effect_line_op
    from bmanga_dev_effect_handle_defaults.ui import overlay_effect_line
    from bmanga_dev_effect_handle_defaults.utils import object_selection

    obj = SimpleNamespace()
    layer = SimpleNamespace(name="効果線_focus")
    bounds = (10.0, 20.0, 40.0, 30.0)
    original_selected_names = object_selection.selected_effect_names
    original_active_bounds = effect_line_op.active_effect_layer_bounds
    original_world_bounds = effect_line_op.effect_layer_world_bounds
    original_center = effect_line_op.effect_layer_center
    original_world_point = effect_line_op.effect_layer_world_point
    drawn: list[tuple[str, object]] = []
    try:
        object_selection.selected_effect_names = lambda _context: []
        effect_line_op.active_effect_layer_bounds = lambda _context: (obj, layer, bounds)
        effect_line_op.effect_layer_world_bounds = lambda *_args: bounds
        effect_line_op.effect_layer_center = lambda *_args: (30.0, 35.0)
        effect_line_op.effect_layer_world_point = lambda _context, _obj, point, _layer: point
        context.scene.bmanga_active_layer_kind = "effect"
        overlay_effect_line.draw_active_effect_line_bounds(
            context,
            draw_rect_fill=lambda rect, _color: drawn.append(("fill", rect)),
            draw_rect_outline=lambda rect, *_args, **_kwargs: drawn.append(("outline", rect)),
            draw_segments_mm=None,
        )
    finally:
        object_selection.selected_effect_names = original_selected_names
        effect_line_op.active_effect_layer_bounds = original_active_bounds
        effect_line_op.effect_layer_world_bounds = original_world_bounds
        effect_line_op.effect_layer_center = original_center
        effect_line_op.effect_layer_world_point = original_world_point

    fill_count = sum(1 for kind, _rect in drawn if kind == "fill")
    outline_count = sum(1 for kind, _rect in drawn if kind == "outline")
    assert (fill_count, outline_count) == (2, 2), drawn


def _check_outer_handle_hits() -> None:
    from bmanga_dev_effect_handle_defaults.operators import effect_line_op
    from bmanga_dev_effect_handle_defaults.utils import object_selection

    bounds = (10.0, 20.0, 40.0, 30.0)
    outset = object_selection.SELECTION_HANDLE_OUTSET_MM
    expected = {
        (10.0 - outset, 20.0 - outset): "bottom_left",
        (50.0 + outset, 20.0 - outset): "bottom_right",
        (10.0 - outset, 50.0 + outset): "top_left",
        (50.0 + outset, 50.0 + outset): "top_right",
        (10.0 - outset, 35.0): "left",
        (50.0 + outset, 35.0): "right",
        (30.0, 20.0 - outset): "bottom",
        (30.0, 50.0 + outset): "top",
    }
    for point, part in expected.items():
        actual = effect_line_op._effect_hit_part(
            bounds,
            *point,
            allow_center=False,
            handle_outset_mm=outset,
        )
        assert actual == part, (point, part, actual)
    assert effect_line_op._effect_hit_part(
        bounds,
        10.0,
        20.0,
        allow_center=False,
        handle_outset_mm=outset,
    ) == "body"
    assert effect_line_op._effect_hit_part(
        bounds,
        10.0 - outset,
        20.0 - outset,
        allow_center=False,
    ) == ""


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _check_initial_values(bpy.context)
        _check_effect_overlay(bpy.context)
        _check_outer_handle_hits()
        print("BMANGA_EFFECT_LINE_HANDLE_DEFAULTS_OK")
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
