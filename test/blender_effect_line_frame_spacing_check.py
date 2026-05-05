"""Blender実機用: 効果線のコマ枠始点間隔と抜き初期値の確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_effect_spacing",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_effect_spacing"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _perimeter_pos(point: tuple[float, float]) -> float:
    x, y = point
    eps = 1.0e-4
    if abs(y) <= eps:
        return x
    if abs(x - 100.0) <= eps:
        return 100.0 + y
    if abs(y - 50.0) <= eps:
        return 150.0 + (100.0 - x)
    if abs(x) <= eps:
        return 250.0 + (50.0 - y)
    raise AssertionError(f"point is not on frame outline: {point}")


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        params = bpy.context.scene.bname_effect_line_params
        assert abs(float(params.out_percent) - 0.0) <= 1.0e-6

        from bname_dev_effect_spacing.operators import effect_line_gen
        from bname_dev_effect_spacing.utils.geom import m_to_mm

        fake = SimpleNamespace(
            spacing_mode="distance",
            spacing_distance_mm=10.0,
            spacing_angle_deg=5.0,
            spacing_jitter_enabled=False,
            spacing_jitter_amount=0.0,
            max_line_count=1000,
            bundle_enabled=False,
            bundle_line_count=4,
            bundle_jitter_amount=0.0,
            bundle_gap_mm=0.0,
            rotation_deg=33.0,
            end_shape="ellipse",
            base_shape="ellipse",
            brush_size_mm=0.4,
            brush_jitter_enabled=False,
            brush_jitter_amount=0.0,
            inout_apply="brush_size",
            in_percent=100.0,
            out_percent=0.0,
        )
        outline = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        strokes = effect_line_gen.generate_focus_strokes(
            fake,
            center_xy_mm=(50.0, 25.0),
            radius_x_mm=8.0,
            radius_y_mm=8.0,
            seed=0,
            start_outline_mm=outline,
            start_extend_mm=0.0,
        )
        assert len(strokes) == 30, len(strokes)
        starts = [
            (m_to_mm(stroke.points_xyz[0][0]), m_to_mm(stroke.points_xyz[0][1]))
            for stroke in strokes
        ]
        distances = sorted(_perimeter_pos(point) for point in starts)
        gaps = [
            distances[(i + 1) % len(distances)] - distances[i]
            if i < len(distances) - 1
            else 300.0 - distances[i] + distances[0]
            for i in range(len(distances))
        ]
        for gap in gaps:
            if abs(gap - 10.0) > 1.0e-4:
                raise AssertionError(f"frame spacing gap expected 10mm, got {gap}")
        print("BNAME_EFFECT_LINE_FRAME_SPACING_OK")
    finally:
        mod.unregister()


if __name__ == "__main__":
    main()
