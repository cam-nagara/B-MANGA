"""Blender実機用: 効果線のコマ枠始点間隔と抜き初期値の確認."""

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


def _stroke_start_mm(stroke, m_to_mm) -> tuple[float, float]:
    return (m_to_mm(stroke.points_xyz[0][0]), m_to_mm(stroke.points_xyz[0][1]))


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) ** 0.5


def _angle_gap_spread(points: list[tuple[float, float]], center: tuple[float, float]) -> float:
    angles = sorted(math.atan2(y - center[1], x - center[0]) for x, y in points)
    gaps = [
        angles[index + 1] - angles[index]
        for index in range(len(angles) - 1)
    ]
    gaps.append((2.0 * math.pi) - angles[-1] + angles[0])
    return max(gaps) - min(gaps)


def _stroke_point_mm(stroke, m_to_mm, pos: float) -> tuple[float, float]:
    start = _stroke_start_mm(stroke, m_to_mm)
    end = _stroke_endpoint_mm(stroke, m_to_mm)
    return (
        start[0] + (end[0] - start[0]) * float(pos),
        start[1] + (end[1] - start[1]) * float(pos),
    )


def _path_gap_spread(strokes, m_to_mm, center: tuple[float, float]) -> float:
    spreads: list[float] = []
    for pos in (0.0, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90, 0.96, 0.985):
        points = [
            _stroke_point_mm(stroke, m_to_mm, pos)
            for stroke in strokes
        ]
        points.sort(key=lambda point: math.atan2(point[1] - center[1], point[0] - center[0]))
        gaps = [
            _distance(points[index], points[index + 1])
            for index in range(len(points) - 1)
        ]
        gaps.append(_distance(points[-1], points[0]))
        spreads.append(max(gaps) - min(gaps))
    return sum(spreads) / max(1, len(spreads))


def _path_min_gap(strokes, m_to_mm, center: tuple[float, float]) -> float:
    min_gap = float("inf")
    for pos in (0.0, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90, 0.96, 0.985):
        points = [
            _stroke_point_mm(stroke, m_to_mm, pos)
            for stroke in strokes
        ]
        points.sort(key=lambda point: math.atan2(point[1] - center[1], point[0] - center[0]))
        gaps = [
            _distance(points[index], points[index + 1])
            for index in range(len(points) - 1)
        ]
        gaps.append(_distance(points[-1], points[0]))
        min_gap = min(min_gap, min(gaps))
    return min_gap


def _sector_min_gap(strokes, m_to_mm, center: tuple[float, float], angle_min: float, angle_max: float) -> float:
    min_gap = float("inf")
    for pos in (0.0, 0.30, 0.60, 0.90, 0.96, 0.985):
        points = []
        for stroke in strokes:
            point = _stroke_point_mm(stroke, m_to_mm, pos)
            angle = math.atan2(point[1] - center[1], point[0] - center[0])
            if angle_min <= angle <= angle_max:
                points.append(point)
        if len(points) < 2:
            continue
        points.sort(key=lambda point: math.atan2(point[1] - center[1], point[0] - center[0]))
        gaps = [
            _distance(points[index], points[index + 1])
            for index in range(len(points) - 1)
        ]
        min_gap = min(min_gap, min(gaps))
    return min_gap


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        params = bpy.context.scene.bname_effect_line_params
        assert abs(float(params.out_percent) - 0.0) <= 1.0e-6
        assert params.start_frame_density_basis == "rounded_frame"
        assert params.spacing_density_compensation is True

        from bname_dev_effect_spacing.core import effect_line
        from bname_dev_effect_spacing.operators import (
            effect_line_density,
            effect_line_gen,
            effect_line_link_op,
        )
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

        fake = SimpleNamespace(
            spacing_mode="distance",
            spacing_distance_mm=10.0,
            spacing_angle_deg=5.0,
            start_frame_density_basis="frame",
            start_frame_density_rounding_percent=100.0,
            spacing_density_compensation=False,
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
        stretched_none.spacing_density_compensation = False
        stretched_none.rotation_deg = 0.0
        stretched_none.end_shape = "ellipse"
        stretched_strong = SimpleNamespace(**vars(stretched_none))
        stretched_strong.spacing_density_compensation = True
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

        frame_none = SimpleNamespace(**vars(fake))
        frame_none.spacing_distance_mm = 8.0
        frame_none.spacing_density_compensation = False
        frame_none.start_frame_density_basis = "frame"
        frame_none.rotation_deg = 0.0
        frame_none.end_shape = "ellipse"
        frame_strong = SimpleNamespace(**vars(frame_none))
        frame_strong.spacing_density_compensation = True
        skewed_outline = [(0.0, 0.0), (160.0, 0.0), (130.0, 60.0), (0.0, 60.0)]
        skewed_center = (110.0, 30.0)
        frame_none_strokes = effect_line_gen.generate_focus_strokes(
            frame_none,
            center_xy_mm=skewed_center,
            radius_x_mm=55.0,
            radius_y_mm=8.0,
            seed=0,
            start_outline_mm=skewed_outline,
        )
        frame_strong_strokes = effect_line_gen.generate_focus_strokes(
            frame_strong,
            center_xy_mm=skewed_center,
            radius_x_mm=55.0,
            radius_y_mm=8.0,
            seed=0,
            start_outline_mm=skewed_outline,
        )
        if len(frame_strong_strokes) != len(frame_none_strokes):
            raise AssertionError(
                "density compensation should not change the requested frame line count: "
                f"none={len(frame_none_strokes)}, strong={len(frame_strong_strokes)}"
            )
        frame_none_min_gap = _path_min_gap(frame_none_strokes, m_to_mm, skewed_center)
        frame_strong_min_gap = _path_min_gap(frame_strong_strokes, m_to_mm, skewed_center)
        if frame_strong_min_gap <= frame_none_min_gap * 1.5:
            raise AssertionError(
                "frame density compensation should redistribute over-dense drawn-path spacing without thinning: "
                f"none_min_gap={frame_none_min_gap}, strong_min_gap={frame_strong_min_gap}"
            )

        flat_none = SimpleNamespace(**vars(fake))
        flat_none.spacing_distance_mm = 0.4
        flat_none.spacing_density_compensation = False
        flat_none.start_frame_density_basis = "rounded_frame"
        flat_none.start_frame_density_rounding_percent = 100.0
        flat_none.end_shape = "ellipse"
        flat_strong = SimpleNamespace(**vars(flat_none))
        flat_strong.spacing_density_compensation = True
        flat_outline = [(0.0, 0.0), (260.0, 0.0), (260.0, 75.0), (0.0, 75.0)]
        flat_center = (185.0, 37.5)
        flat_none_strokes = effect_line_gen.generate_focus_strokes(
            flat_none,
            center_xy_mm=flat_center,
            radius_x_mm=40.0,
            radius_y_mm=14.0,
            seed=0,
            start_outline_mm=flat_outline,
        )
        flat_strong_strokes = effect_line_gen.generate_focus_strokes(
            flat_strong,
            center_xy_mm=flat_center,
            radius_x_mm=40.0,
            radius_y_mm=14.0,
            seed=0,
            start_outline_mm=flat_outline,
        )
        flat_none_min_gap = _path_min_gap(flat_none_strokes, m_to_mm, flat_center)
        flat_strong_min_gap = _path_min_gap(flat_strong_strokes, m_to_mm, flat_center)
        if len(flat_strong_strokes) != len(flat_none_strokes):
            raise AssertionError(
                "density compensation should keep wide-frame line count controlled by line interval: "
                f"none={len(flat_none_strokes)}, strong={len(flat_strong_strokes)}"
            )
        if flat_strong_min_gap <= flat_none_min_gap * 1.5:
            raise AssertionError(
                "density compensation should redistribute over-dense wide-frame spacing without thinning: "
                f"none_min_gap={flat_none_min_gap}, strong_min_gap={flat_strong_min_gap}"
            )

        user_shape_none = SimpleNamespace(**vars(fake))
        user_shape_none.spacing_distance_mm = 1.0
        user_shape_none.spacing_density_compensation = False
        user_shape_none.start_frame_density_basis = "frame"
        user_shape_none.rotation_deg = 0.0
        user_shape_none.end_shape = "ellipse"
        user_shape_none.length_jitter_enabled = True
        user_shape_none.length_jitter_amount = 0.2
        user_shape_strong = SimpleNamespace(**vars(user_shape_none))
        user_shape_strong.spacing_density_compensation = True
        user_shape_outline = [(38.5, 47.0), (218.5, 47.0), (218.5, 214.309), (38.5, 281.061)]
        user_shape_center = (175.0, 148.0)
        user_shape_none_strokes = effect_line_gen.generate_focus_strokes(
            user_shape_none,
            center_xy_mm=user_shape_center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=user_shape_outline,
        )
        user_shape_strong_strokes = effect_line_gen.generate_focus_strokes(
            user_shape_strong,
            center_xy_mm=user_shape_center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=user_shape_outline,
        )
        if len(user_shape_strong_strokes) != len(user_shape_none_strokes):
            raise AssertionError(
                "density compensation should not lower the actual slanted-frame line count: "
                f"none={len(user_shape_none_strokes)}, strong={len(user_shape_strong_strokes)}"
            )
        user_shape_none_min_gap = _path_min_gap(user_shape_none_strokes, m_to_mm, user_shape_center)
        user_shape_strong_min_gap = _path_min_gap(user_shape_strong_strokes, m_to_mm, user_shape_center)
        if user_shape_strong_min_gap <= user_shape_none_min_gap * 1.5:
            raise AssertionError(
                "density compensation should redistribute over-dense slanted-frame spacing without thinning: "
                f"none_min_gap={user_shape_none_min_gap}, strong_min_gap={user_shape_strong_min_gap}"
            )
        user_shape_dense_none = SimpleNamespace(**vars(user_shape_none))
        user_shape_dense_none.spacing_distance_mm = 0.3
        user_shape_dense_none.spacing_density_compensation = False
        user_shape_dense_none.start_frame_density_basis = "rounded_frame"
        user_shape_dense_none.start_frame_density_rounding_percent = 100.0
        user_shape_dense_none.length_jitter_enabled = False
        user_shape_dense_none_strokes = effect_line_gen.generate_focus_strokes(
            user_shape_dense_none,
            center_xy_mm=user_shape_center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=user_shape_outline,
        )
        user_shape_dense = SimpleNamespace(**vars(user_shape_none))
        user_shape_dense.spacing_distance_mm = 0.3
        user_shape_dense.spacing_density_compensation = True
        user_shape_dense.start_frame_density_basis = "rounded_frame"
        user_shape_dense.start_frame_density_rounding_percent = 100.0
        user_shape_dense.length_jitter_enabled = False
        user_shape_dense_strokes = effect_line_gen.generate_focus_strokes(
            user_shape_dense,
            center_xy_mm=user_shape_center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=user_shape_outline,
        )
        user_shape_dense_none_min_gap = _path_min_gap(user_shape_dense_none_strokes, m_to_mm, user_shape_center)
        user_shape_dense_min_gap = _path_min_gap(user_shape_dense_strokes, m_to_mm, user_shape_center)
        if len(user_shape_dense_strokes) <= len(user_shape_strong_strokes) * 1.25:
            raise AssertionError(
                "strong density compensation should still let dense 0.30mm spacing increase line count: "
                f"strong_1mm={len(user_shape_strong_strokes)}, strong_dense={len(user_shape_dense_strokes)}"
            )
        if len(user_shape_dense_strokes) != len(user_shape_dense_none_strokes):
            raise AssertionError(
                "density compensation should not thin dense 0.30mm spacing: "
                f"none_dense={len(user_shape_dense_none_strokes)}, strong_dense={len(user_shape_dense_strokes)}"
            )
        if user_shape_dense_min_gap <= user_shape_dense_none_min_gap * 1.5:
            raise AssertionError(
                "density compensation should improve dense 0.30mm spacing without reducing count: "
                f"none_min_gap={user_shape_dense_none_min_gap}, strong_min_gap={user_shape_dense_min_gap}"
            )
        user_shape_min = SimpleNamespace(**vars(user_shape_none))
        user_shape_min.spacing_distance_mm = 0.01
        user_shape_min.spacing_density_compensation = True
        user_shape_min_strokes = effect_line_gen.generate_focus_strokes(
            user_shape_min,
            center_xy_mm=user_shape_center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=user_shape_outline,
        )
        if len(user_shape_min_strokes) < int(user_shape_min.max_line_count):
            raise AssertionError(
                "minimum line interval should not be widened by density compensation: "
                f"count={len(user_shape_min_strokes)}, max={user_shape_min.max_line_count}"
            )
        screenshot_none = SimpleNamespace(**vars(user_shape_none))
        screenshot_none.spacing_distance_mm = 0.40
        screenshot_none.spacing_density_compensation = False
        screenshot_none.start_frame_density_basis = "rounded_frame"
        screenshot_none.start_frame_density_rounding_percent = 100.0
        screenshot_none.length_jitter_enabled = False
        screenshot_none_strokes = effect_line_gen.generate_focus_strokes(
            screenshot_none,
            center_xy_mm=user_shape_center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=user_shape_outline,
        )
        screenshot_shape = SimpleNamespace(**vars(user_shape_none))
        screenshot_shape.spacing_distance_mm = 0.40
        screenshot_shape.spacing_density_compensation = True
        screenshot_shape.start_frame_density_basis = "rounded_frame"
        screenshot_shape.start_frame_density_rounding_percent = 100.0
        screenshot_shape.length_jitter_enabled = False
        screenshot_strokes = effect_line_gen.generate_focus_strokes(
            screenshot_shape,
            center_xy_mm=user_shape_center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=user_shape_outline,
        )
        if len(screenshot_strokes) != len(screenshot_none_strokes):
            raise AssertionError(
                "density compensation should keep screenshot-equivalent line count: "
                f"none={len(screenshot_none_strokes)}, strong={len(screenshot_strokes)}"
            )
        screenshot_none_min_gap = _path_min_gap(screenshot_none_strokes, m_to_mm, user_shape_center)
        screenshot_none_lower_right_min_gap = _sector_min_gap(
            screenshot_none_strokes,
            m_to_mm,
            user_shape_center,
            0.0,
            1.35,
        )
        screenshot_min_gap = _path_min_gap(screenshot_strokes, m_to_mm, user_shape_center)
        screenshot_lower_right_min_gap = _sector_min_gap(
            screenshot_strokes,
            m_to_mm,
            user_shape_center,
            0.0,
            1.35,
        )
        if screenshot_min_gap <= screenshot_none_min_gap * 1.5:
            raise AssertionError(
                "density compensation should improve screenshot-equivalent spacing without thinning: "
                f"none_min_gap={screenshot_none_min_gap}, strong_min_gap={screenshot_min_gap}, count={len(screenshot_strokes)}"
            )
        if screenshot_lower_right_min_gap <= screenshot_none_lower_right_min_gap * 1.25:
            raise AssertionError(
                "density compensation should improve lower-right spacing without thinning: "
                f"none_lower_right_min_gap={screenshot_none_lower_right_min_gap}, "
                f"strong_lower_right_min_gap={screenshot_lower_right_min_gap}, count={len(screenshot_strokes)}"
            )
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
