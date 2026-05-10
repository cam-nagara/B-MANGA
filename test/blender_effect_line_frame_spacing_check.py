from __future__ import annotations

import importlib.util
import math
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


def _base_params() -> SimpleNamespace:
    return SimpleNamespace(
        spacing_mode="distance",
        spacing_distance_mm=1.0,
        spacing_angle_deg=5.0,
        start_frame_density_basis="rounded_frame",
        start_frame_density_rounding_percent=100.0,
        spacing_density_compensation=True,
        spacing_jitter_enabled=False,
        spacing_jitter_amount=0.0,
        max_line_count=1000,
        bundle_enabled=False,
        bundle_line_count=4,
        bundle_jitter_amount=0.0,
        bundle_gap_mm=0.0,
        rotation_deg=0.0,
        end_shape="ellipse",
        base_shape="ellipse",
        brush_size_mm=0.4,
        brush_jitter_enabled=False,
        brush_jitter_amount=0.0,
        inout_apply="brush_size",
        in_percent=100.0,
        out_percent=0.0,
        length_jitter_enabled=False,
        length_jitter_amount=0.2,
    )


def _stroke_points_mm(stroke, m_to_mm) -> list[tuple[float, float]]:
    return [(m_to_mm(point[0]), m_to_mm(point[1])) for point in stroke.points_xyz]


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    vx = float(end[0]) - float(start[0])
    vy = float(end[1]) - float(start[1])
    length_sq = vx * vx + vy * vy
    if length_sq <= 1.0e-9:
        return _distance(point, start)
    t = ((float(point[0]) - float(start[0])) * vx + (float(point[1]) - float(start[1])) * vy) / length_sq
    t = max(0.0, min(1.0, t))
    closest = (float(start[0]) + vx * t, float(start[1]) + vy * t)
    return _distance(point, closest)


def _assert_on_outline(point: tuple[float, float], outline: list[tuple[float, float]]) -> None:
    distance = min(
        _point_segment_distance(point, outline[index], outline[(index + 1) % len(outline)])
        for index in range(len(outline))
    )
    if distance > 1.0e-4:
        raise AssertionError(f"focus line start should stay on frame outline: point={point}, distance={distance}")


def _assert_simple_radial_strokes(strokes, m_to_mm, center: tuple[float, float], *, outline=None) -> None:
    if not strokes:
        raise AssertionError("focus line generation returned no strokes")
    for index, stroke in enumerate(strokes):
        points = _stroke_points_mm(stroke, m_to_mm)
        if len(points) != 2:
            raise AssertionError(f"focus line should be one straight segment: index={index}, points={len(points)}")
        if abs(float(getattr(stroke, "density_end", 1.0)) - 1.0) > 1.0e-9:
            raise AssertionError(f"focus line should not be shortened: index={index}")
        if _distance(points[-1], center) > 1.0e-4:
            raise AssertionError(f"focus line should end at center point: index={index}, end={points[-1]}")
        if outline is not None:
            _assert_on_outline(points[0], outline)


def _angle(center: tuple[float, float], point: tuple[float, float]) -> float:
    return math.atan2(float(point[1]) - center[1], float(point[0]) - center[0])


def _perpendicular_gaps(strokes, m_to_mm, center: tuple[float, float]) -> list[float]:
    starts = [_stroke_points_mm(stroke, m_to_mm)[0] for stroke in strokes]
    starts.sort(key=lambda point: _angle(center, point))
    gaps: list[float] = []
    for index, point in enumerate(starts):
        next_point = starts[(index + 1) % len(starts)]
        angle = _angle(center, point)
        next_angle = _angle(center, next_point)
        delta = next_angle - angle
        if index == len(starts) - 1:
            delta += 2.0 * math.pi
        delta = max(0.0, delta)
        radius = _distance(point, center)
        gaps.append(radius * math.tan(delta))
    return gaps


def _assert_perpendicular_spacing(strokes, m_to_mm, center: tuple[float, float], expected_mm: float) -> None:
    gaps = _perpendicular_gaps(strokes, m_to_mm, center)
    average = sum(gaps) / max(1, len(gaps))
    if abs(average - expected_mm) > max(0.02, expected_mm * 0.08):
        raise AssertionError(
            f"focus line spacing should follow perpendicular distance: avg={average}, expected={expected_mm}"
        )
    if min(gaps) <= expected_mm * 0.70 or max(gaps) >= expected_mm * 1.30:
        raise AssertionError(
            "focus line spacing should stay even by perpendicular distance: "
            f"min={min(gaps)}, max={max(gaps)}, expected={expected_mm}"
        )


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        params = bpy.context.scene.bname_effect_line_params
        assert abs(float(params.out_percent) - 0.0) <= 1.0e-6
        assert params.start_frame_density_basis == "rounded_frame"
        assert params.spacing_density_compensation is True

        from bname_dev_effect_spacing.core import effect_line
        from bname_dev_effect_spacing.operators import effect_line_gen, effect_line_link_op
        from bname_dev_effect_spacing.utils.geom import m_to_mm

        params.start_frame_density_basis = "ellipse"
        params.start_frame_density_rounding_percent = 25.0
        params.spacing_density_compensation = False
        effect_line.effect_params_from_dict(params, {"schema_version": 3, "effect_type": "focus"})
        assert params.start_frame_density_basis == "rounded_frame"
        assert abs(float(params.start_frame_density_rounding_percent) - 100.0) <= 1.0e-6
        assert params.spacing_density_compensation is True

        effect_line.effect_params_from_dict(
            params,
            {"schema_version": 5, "effect_type": "focus", "spacing_density_compensation": "none"},
        )
        assert params.spacing_density_compensation is False
        effect_line.effect_params_from_dict(
            params,
            {"schema_version": 5, "effect_type": "focus", "spacing_density_compensation": "strong"},
        )
        assert params.spacing_density_compensation is True
        legacy_saved = effect_line.effect_params_to_dict(SimpleNamespace(spacing_density_compensation="medium"))
        assert legacy_saved["spacing_density_compensation"] is True

        outline = [(38.5, 47.0), (218.5, 47.0), (218.5, 214.309), (38.5, 281.061)]
        center = (175.0, 148.0)

        frame_params = _base_params()
        frame_params.spacing_distance_mm = 1.0
        frame_strokes = effect_line_gen.generate_focus_strokes(
            frame_params,
            center_xy_mm=center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=outline,
        )
        _assert_simple_radial_strokes(frame_strokes, m_to_mm, center, outline=outline)
        _assert_perpendicular_spacing(frame_strokes, m_to_mm, center, 1.0)

        dense_params = SimpleNamespace(**vars(frame_params))
        dense_params.spacing_distance_mm = 0.3
        dense_strokes = effect_line_gen.generate_focus_strokes(
            dense_params,
            center_xy_mm=center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=outline,
        )
        _assert_simple_radial_strokes(dense_strokes, m_to_mm, center, outline=outline)
        if len(dense_strokes) <= len(frame_strokes):
            raise AssertionError(
                f"smaller line spacing should increase focus line count: normal={len(frame_strokes)}, dense={len(dense_strokes)}"
            )

        no_density_params = SimpleNamespace(**vars(frame_params))
        no_density_params.spacing_density_compensation = False
        no_density_strokes = effect_line_gen.generate_focus_strokes(
            no_density_params,
            center_xy_mm=center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=outline,
        )
        _assert_simple_radial_strokes(no_density_strokes, m_to_mm, center, outline=outline)
        if len(no_density_strokes) <= 0:
            raise AssertionError(
                f"density toggle off should keep simple focus lines: off={len(no_density_strokes)}"
            )

        ellipse_params = _base_params()
        ellipse_params.spacing_distance_mm = 2.0
        ellipse_strokes = effect_line_gen.generate_focus_strokes(
            ellipse_params,
            center_xy_mm=center,
            radius_x_mm=30.0,
            radius_y_mm=12.0,
            seed=0,
        )
        _assert_simple_radial_strokes(ellipse_strokes, m_to_mm, center)

        linked_params = effect_line_link_op._copy_linked_shape_params(
            {
                "start_frame_density_basis": "ellipse",
                "start_frame_density_rounding_percent": 75.0,
                "spacing_density_compensation": True,
            },
            {
                "start_frame_density_basis": "frame",
                "start_frame_density_rounding_percent": 0.0,
                "spacing_density_compensation": False,
            },
        )
        assert linked_params["start_frame_density_basis"] == "ellipse"
        assert linked_params["start_frame_density_rounding_percent"] == 75.0
        assert linked_params["spacing_density_compensation"] is True
        print("BNAME_EFFECT_LINE_FRAME_SPACING_OK")
    finally:
        mod.unregister()


if __name__ == "__main__":
    main()
