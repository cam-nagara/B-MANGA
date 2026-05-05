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


def _stroke_endpoint_mm(stroke, m_to_mm) -> tuple[float, float]:
    return (m_to_mm(stroke.points_xyz[1][0]), m_to_mm(stroke.points_xyz[1][1]))


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) ** 0.5


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        params = bpy.context.scene.bname_effect_line_params
        assert abs(float(params.out_percent) - 0.0) <= 1.0e-6
        assert params.start_frame_density_basis == "rounded_frame"
        assert params.spacing_density_compensation == "medium"

        from bname_dev_effect_spacing.core import effect_line
        from bname_dev_effect_spacing.operators import (
            effect_line_density,
            effect_line_gen,
            effect_line_link_op,
        )
        from bname_dev_effect_spacing.utils.geom import m_to_mm

        params.start_frame_density_basis = "ellipse"
        params.start_frame_density_rounding_percent = 25.0
        params.spacing_density_compensation = "none"
        effect_line.effect_params_from_dict(params, {"schema_version": 3, "effect_type": "focus"})
        assert params.start_frame_density_basis == "rounded_frame"
        assert abs(float(params.start_frame_density_rounding_percent) - 100.0) <= 1.0e-6
        assert params.spacing_density_compensation == "medium"

        fake = SimpleNamespace(
            spacing_mode="distance",
            spacing_distance_mm=10.0,
            spacing_angle_deg=5.0,
            start_frame_density_basis="frame",
            start_frame_density_rounding_percent=100.0,
            spacing_density_compensation="none",
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

        rounded_fake = SimpleNamespace(**vars(fake))
        rounded_fake.start_frame_density_basis = "rounded_frame"
        rounded_fake.start_frame_density_rounding_percent = 100.0
        rounded_strokes = effect_line_gen.generate_focus_strokes(
            rounded_fake,
            center_xy_mm=(50.0, 25.0),
            radius_x_mm=8.0,
            radius_y_mm=8.0,
            seed=0,
            start_outline_mm=outline,
            start_extend_mm=0.0,
        )
        rounded_starts = [
            (round(m_to_mm(stroke.points_xyz[0][0]), 4), round(m_to_mm(stroke.points_xyz[0][1]), 4))
            for stroke in rounded_strokes
        ]
        corners = {(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)}
        if any(start in corners for start in rounded_starts):
            raise AssertionError(f"rounded density basis should avoid exact frame corners: {rounded_starts}")
        for point in rounded_starts:
            _perimeter_pos(point)

        stretched_none = SimpleNamespace(**vars(fake))
        stretched_none.spacing_distance_mm = 8.0
        stretched_none.spacing_density_compensation = "none"
        stretched_none.rotation_deg = 0.0
        stretched_none.end_shape = "ellipse"
        stretched_strong = SimpleNamespace(**vars(stretched_none))
        stretched_strong.spacing_density_compensation = "strong"
        stretched_none_strokes = effect_line_gen.generate_focus_strokes(
            stretched_none,
            center_xy_mm=(50.0, 25.0),
            radius_x_mm=80.0,
            radius_y_mm=8.0,
            seed=0,
        )
        stretched_strong_strokes = effect_line_gen.generate_focus_strokes(
            stretched_strong,
            center_xy_mm=(50.0, 25.0),
            radius_x_mm=80.0,
            radius_y_mm=8.0,
            seed=0,
        )
        assert len(stretched_none_strokes) == len(stretched_strong_strokes)
        end_rect = effect_line_gen._scaled_rect(50.0, 25.0, 80.0, 8.0, 1.0)
        end_outline = effect_line_gen._shape_outline(
            stretched_strong,
            "end",
            end_rect,
            (50.0, 25.0),
            seed=23,
        )
        none_error = 0.0
        strong_error = 0.0
        count = len(stretched_strong_strokes)
        for index in range(count):
            expected = effect_line_density.outline_point_at_fraction(end_outline, index / count)
            assert expected is not None
            none_error += _distance(_stroke_endpoint_mm(stretched_none_strokes[index], m_to_mm), expected)
            strong_error += _distance(_stroke_endpoint_mm(stretched_strong_strokes[index], m_to_mm), expected)
        if strong_error >= none_error * 0.25:
            raise AssertionError(
                f"density compensation should follow end outline spacing: none={none_error}, strong={strong_error}"
            )
        linked_params = effect_line_link_op._copy_linked_shape_params(
            {
                "start_frame_density_basis": "ellipse",
                "start_frame_density_rounding_percent": 75.0,
                "spacing_density_compensation": "strong",
            },
            {
                "start_frame_density_basis": "frame",
                "start_frame_density_rounding_percent": 0.0,
                "spacing_density_compensation": "none",
            },
        )
        assert linked_params["start_frame_density_basis"] == "ellipse"
        assert linked_params["start_frame_density_rounding_percent"] == 75.0
        assert linked_params["spacing_density_compensation"] == "strong"
        print("BNAME_EFFECT_LINE_FRAME_SPACING_OK")
    finally:
        mod.unregister()


if __name__ == "__main__":
    main()
