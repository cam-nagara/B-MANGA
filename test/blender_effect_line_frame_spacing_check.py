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
        "bmanga_dev_effect_spacing",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_effect_spacing"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _base_params() -> SimpleNamespace:
    return SimpleNamespace(
        spacing_mode="distance",
        spacing_distance_mm=1.0,
        spacing_angle_deg=5.0,
        spacing_density_compensation=True,
        spacing_jitter_enabled=False,
        spacing_jitter_amount=0.0,
        max_line_count=1000,
        bundle_enabled=False,
        bundle_line_count=4,
        bundle_line_count_jitter=0.0,
        bundle_gap_mm=0.0,
        bundle_gap_jitter_amount=0.0,
        rotation_deg=0.0,
        end_shape="ellipse",
        base_shape="ellipse",
        brush_size_mm=0.3,
        brush_jitter_enabled=False,
        brush_jitter_amount=0.0,
        inout_apply="brush_size",
        in_percent=100.0,
        out_percent=0.0,
        length_jitter_enabled=False,
        length_jitter_amount=50.0,
        end_length_jitter_enabled=False,
        end_length_jitter_amount=50.0,
        in_start_percent=50.0,
        out_start_percent=50.0,
        in_easing_curve="0.0000,0.0000;1.0000,1.0000",
        out_easing_curve="0.0000,0.0000;1.0000,1.0000",
        white_underlay_enabled=False,
        white_underlay_width_percent=150.0,
        white_underlay_color=(1.0, 1.0, 1.0, 1.0),
    )


def _stroke_points_mm(stroke, m_to_mm) -> list[tuple[float, float]]:
    return [(m_to_mm(point[0]), m_to_mm(point[1])) for point in stroke.points_xyz]


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (float(a[0]) + float(b[0])) * 0.5, (float(a[1]) + float(b[1])) * 0.5


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


def _assert_on_ellipse(
    point: tuple[float, float],
    center: tuple[float, float],
    radius_xy: tuple[float, float],
) -> None:
    rx = max(1.0e-6, float(radius_xy[0]))
    ry = max(1.0e-6, float(radius_xy[1]))
    value = ((float(point[0]) - center[0]) / rx) ** 2 + ((float(point[1]) - center[1]) / ry) ** 2
    if abs(value - 1.0) > 0.04:
        raise AssertionError(f"focus line end should stay on end shape: point={point}, ellipse_value={value}")


def _assert_points_collinear_to_center(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    *,
    label: str,
) -> None:
    full_length = _distance(start, center)
    dx = center[0] - start[0]
    dy = center[1] - start[1]
    ex = end[0] - start[0]
    ey = end[1] - start[1]
    cross = abs(dx * ey - dy * ex)
    if cross > max(1.0e-4, full_length * 1.0e-5):
        raise AssertionError(f"{label} should point toward center: cross={cross}")


def _assert_zero_out_radius(stroke, *, label: str) -> None:
    radii = getattr(stroke, "radii", None)
    if radii is None or len(radii) < 2:
        raise AssertionError(f"{label} should have per-point radii")
    if abs(float(radii[-1])) > 1.0e-9:
        raise AssertionError(f"{label} should taper to zero at the stroke end: radius={radii[-1]}")


def _assert_simple_radial_strokes(
    strokes,
    m_to_mm,
    center: tuple[float, float],
    *,
    outline=None,
    end_radius_xy: tuple[float, float] | None = None,
    end_center: tuple[float, float] | None = None,
) -> None:
    if not strokes:
        raise AssertionError("focus line generation returned no strokes")
    for index, stroke in enumerate(strokes):
        points = _stroke_points_mm(stroke, m_to_mm)
        if len(points) != 2:
            raise AssertionError(f"focus line should be one straight segment: index={index}, points={len(points)}")
        if abs(float(getattr(stroke, "density_end", 1.0)) - 1.0) > 1.0e-9:
            raise AssertionError(f"focus line should not be shortened: index={index}")
        _assert_points_collinear_to_center(points[0], points[-1], center, label=f"focus line {index}")
        if _distance(points[-1], center) <= 1.0e-4:
            raise AssertionError(f"focus line should end at the end shape, not at the center point: index={index}")
        if end_radius_xy is not None:
            _assert_on_ellipse(points[-1], end_center or center, end_radius_xy)
        _assert_zero_out_radius(stroke, label=f"focus line {index}")
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


def _ellipse_radius_at_angle(radius_xy: tuple[float, float], angle: float) -> float:
    rx = max(1.0e-6, float(radius_xy[0]))
    ry = max(1.0e-6, float(radius_xy[1]))
    c = math.cos(angle)
    s = math.sin(angle)
    return 1.0 / math.sqrt((c / rx) ** 2 + (s / ry) ** 2)


def _assert_length_jitter_shortens_lines(
    strokes,
    m_to_mm,
    center: tuple[float, float],
    end_radius_xy: tuple[float, float],
) -> None:
    shortened = 0
    for index, stroke in enumerate(strokes):
        points = _stroke_points_mm(stroke, m_to_mm)
        if len(points) != 2:
            raise AssertionError(f"jittered focus line should be one straight segment: index={index}")
        start, end = points
        full_length = _distance(start, center)
        end_to_center = _distance(end, center)
        if end_to_center > full_length + 1.0e-4:
            raise AssertionError(f"jittered focus line should not pass beyond center: index={index}")
        angle = _angle(center, start)
        normal_end_distance = _ellipse_radius_at_angle(end_radius_xy, angle)
        if end_to_center > normal_end_distance + 0.05:
            shortened += 1
        _assert_points_collinear_to_center(start, end, center, label=f"jittered focus line {index}")
        _assert_zero_out_radius(stroke, label=f"jittered focus line {index}")
    if shortened <= 0:
        raise AssertionError("end jitter should shorten at least one focus line")


def _assert_full_end_jitter_can_collapse_line(effect_line_gen) -> None:
    class MaxRandom:
        def random(self) -> float:
            return 1.0

    params = _base_params()
    params.end_length_jitter_enabled = True
    params.end_length_jitter_amount = 100.0
    start, end = effect_line_gen._trimmed_segment_points(params, MaxRandom(), (0.0, 0.0), (10.0, 0.0))
    if _distance(start, end) > 1.0e-9:
        raise AssertionError(f"終点乱れ100%で線がゼロ長まで短くなりません: start={start}, end={end}")


def _assert_start_jitter_moves_starts_inward(strokes, m_to_mm, center, outline) -> None:
    moved = 0
    for index, stroke in enumerate(strokes):
        points = _stroke_points_mm(stroke, m_to_mm)
        if len(points) != 2:
            raise AssertionError(f"start jittered focus line should be one straight segment: index={index}")
        start, end = points
        _assert_points_collinear_to_center(start, end, center, label=f"start jittered focus line {index}")
        distance = min(
            _point_segment_distance(start, outline[i], outline[(i + 1) % len(outline)])
            for i in range(len(outline))
        )
        if distance > 0.05:
            moved += 1
    if moved <= 0:
        raise AssertionError("start jitter should move at least one start point inward")


def _assert_white_outline_is_radial(
    strokes,
    m_to_mm,
    center: tuple[float, float],
    expected_count: int,
    *,
    expected_white_lines_per_band: int | None = None,
    expected_black_lines_per_band: int | None = None,
) -> None:
    white = [stroke for stroke in strokes if getattr(stroke, "role", "") == "white_outline_white"]
    if not white:
        raise AssertionError("白抜き線の白線が生成されていません")
    if expected_white_lines_per_band is not None:
        expected_total = expected_count * expected_white_lines_per_band
        if len(white) != expected_total:
            raise AssertionError(f"白線本数が指定と違います: actual={len(white)} expected={expected_total}")
    buckets: set[int] = set()
    for index, stroke in enumerate(white):
        points = _stroke_points_mm(stroke, m_to_mm)
        if len(points) < 2 or bool(getattr(stroke, "cyclic", False)):
            raise AssertionError(f"白抜き線は抜きのある直線である必要があります: index={index}, points={len(points)}")
        start, end = points[0], points[-1]
        radii = list(getattr(stroke, "radii", None) or [])
        if len(radii) < 2 or not float(radii[0]) > float(radii[-1]):
            raise AssertionError(f"白抜き線の終点に抜きがありません: index={index}, radii={radii}")
        if len(radii) != len(points):
            raise AssertionError(f"白線の入り抜き点と線幅点の数が一致しません: index={index}")
        if _distance(start, center) <= _distance(end, center):
            raise AssertionError(f"白抜き線が始点側から中心方向へ伸びていません: start={start}, end={end}")
        _assert_points_collinear_to_center(start, end, center, label=f"white outline {index}")
        angle = (math.degrees(_angle(center, start)) + 360.0) % 360.0
        buckets.add(int(round(angle / (360.0 / expected_count))) % expected_count)
    if len(buckets) != expected_count:
        raise AssertionError(f"白抜き線が本数分の放射方向に配置されていません: buckets={sorted(buckets)}")
    black = [stroke for stroke in strokes if getattr(stroke, "role", "") == "white_outline_black"]
    if not black:
        raise AssertionError("白抜き線の左右に黒線が生成されていません")
    if expected_black_lines_per_band is not None:
        expected_total = expected_count * expected_black_lines_per_band
        if len(black) != expected_total:
            raise AssertionError(f"黒線本数が指定と違います: actual={len(black)} expected={expected_total}")


def _assert_bundle_jagged_keeps_start(effect_line_gen, m_to_mm) -> None:
    params = _base_params()
    params.effect_type = "focus"
    params.spacing_mode = "angle"
    params.spacing_angle_deg = 20.0
    params.bundle_enabled = True
    params.bundle_line_count = 5
    params.bundle_line_count_jitter = 0.0
    params.bundle_gap_mm = 0.0
    params.bundle_gap_jitter_amount = 0.0
    params.bundle_jagged_enabled = False
    params.bundle_jagged_height_percent = 100.0
    center = (100.0, 100.0)
    base = [
        stroke for stroke in effect_line_gen.generate_strokes(params, center, (50.0, 30.0), seed=4)
        if getattr(stroke, "role", "line") == "line"
    ]
    params.bundle_jagged_enabled = True
    jagged = [
        stroke for stroke in effect_line_gen.generate_strokes(params, center, (50.0, 30.0), seed=4)
        if getattr(stroke, "role", "line") == "line"
    ]
    if len(base) != len(jagged) or not base:
        raise AssertionError("まとまりギザギザの比較対象が生成されていません")
    shortened = 0
    for index, (before, after) in enumerate(zip(base, jagged)):
        before_points = _stroke_points_mm(before, m_to_mm)
        after_points = _stroke_points_mm(after, m_to_mm)
        if _distance(before_points[0], after_points[0]) > 1.0e-5:
            raise AssertionError(f"まとまりギザギザで始点が動いています: index={index}")
        before_length = _distance(before_points[0], before_points[-1])
        after_length = _distance(after_points[0], after_points[-1])
        if after_length < before_length - 1.0e-5:
            shortened += 1
    if shortened <= 0:
        raise AssertionError("まとまりギザギザで終点側が短くなっていません")


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        params = bpy.context.scene.bmanga_effect_line_params
        assert abs(float(params.brush_size_mm) - 0.3) <= 1.0e-6
        assert params.bl_rna.properties["brush_size_mm"].name == "線幅 (mm)"
        assert abs(float(params.out_percent) - 0.0) <= 1.0e-6
        if "start_frame_density_basis" in params.bl_rna.properties:
            raise AssertionError("密度基準が効果線設定に残っています")
        if "start_frame_density_rounding_percent" in params.bl_rna.properties:
            raise AssertionError("角丸率が効果線設定に残っています")
        assert params.spacing_density_compensation is True

        from bmanga_dev_effect_spacing.core import balloon, effect_line
        from bmanga_dev_effect_spacing.operators import effect_line_gen, effect_line_link_op, effect_line_op
        from bmanga_dev_effect_spacing.ui import overlay_effect_line
        from bmanga_dev_effect_spacing.utils import balloon_shapes, effect_line_object
        from bmanga_dev_effect_spacing.utils.geom import m_to_mm

        old_shapes = {"polygon", "pill", "hexagon", "diamond", "star", "spike_straight", "spike_curve"}
        effect_shape_ids = {
            str(getattr(item, "identifier", "") or "")
            for item in params.bl_rna.properties["end_shape"].enum_items
        }
        balloon_shape_ids = {item[0] for item in balloon._SHAPE_ITEMS}
        balloon_line_style_ids = {item[0] for item in balloon._LINE_STYLE_ITEMS}
        if effect_shape_ids & old_shapes or balloon_shape_ids & old_shapes:
            raise AssertionError(f"旧タイプが形状候補に残っています: effect={effect_shape_ids}, balloon={balloon_shape_ids}")
        if {"uni_flash", "white_outline"} & effect_shape_ids:
            raise AssertionError(f"フキダシ専用形状が効果線の始点・終点形状に混入しています: {effect_shape_ids}")
        if {"uni_flash", "white_outline"} & balloon_shape_ids:
            raise AssertionError(f"ウニフラ / 白抜き線がフキダシ形状候補に残っています: {balloon_shape_ids}")
        if not {"uni_flash", "white_outline"} <= balloon_line_style_ids:
            raise AssertionError(f"フキダシ線種候補にウニフラ / 白抜き線がありません: {balloon_line_style_ids}")
        effect_shape_labels = {
            str(getattr(item, "name", "") or "")
            for item in params.bl_rna.properties["end_shape"].enum_items
        }
        balloon_shape_labels = {item[1] for item in balloon._SHAPE_ITEMS}
        if any("旧" in label for label in effect_shape_labels | balloon_shape_labels):
            raise AssertionError("形状候補に旧表記が残っています")
        if balloon_shapes.normalize_shape("pill") != "ellipse":
            raise AssertionError("旧フキダシ形状の読み替えが機能していません")
        if balloon_shapes.normalize_shape("spike_curve") != "thorn-curve":
            raise AssertionError("旧トゲ形状の読み替えが機能していません")
        if balloon_shapes.normalize_shape("uni_flash") != "ellipse":
            raise AssertionError("旧ウニフラ形状の読み替えが機能していません")
        if balloon_shapes.normalize_line_style("uni_flash") != "uni_flash":
            raise AssertionError("フキダシのウニフラ線種が維持されていません")
        assert params.bl_rna.properties["bundle_line_count_jitter"].name == "数の乱れ"
        assert params.bl_rna.properties["bundle_gap_jitter_amount"].name == "まとまり間隔の乱れ"

        params.spacing_density_compensation = False
        effect_line.effect_params_from_dict(params, {"schema_version": 3, "effect_type": "focus"})
        assert params.spacing_density_compensation is True

        effect_line.effect_params_from_dict(
            params,
            {"schema_version": 5, "effect_type": "focus", "spacing_mode": "distance", "spacing_density_compensation": "none"},
        )
        assert params.spacing_density_compensation is True
        effect_line.effect_params_from_dict(
            params,
            {"schema_version": 5, "effect_type": "focus", "spacing_mode": "angle", "spacing_density_compensation": "none"},
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
        _assert_simple_radial_strokes(frame_strokes, m_to_mm, center, outline=outline, end_radius_xy=(20.0, 7.0))
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
        _assert_simple_radial_strokes(dense_strokes, m_to_mm, center, outline=outline, end_radius_xy=(20.0, 7.0))
        if len(dense_strokes) <= len(frame_strokes):
            raise AssertionError(
                f"smaller line spacing should increase focus line count: normal={len(frame_strokes)}, dense={len(dense_strokes)}"
            )

        forced_density_params = SimpleNamespace(**vars(frame_params))
        forced_density_params.spacing_density_compensation = False
        forced_density_strokes = effect_line_gen.generate_focus_strokes(
            forced_density_params,
            center_xy_mm=center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=0,
            start_outline_mm=outline,
        )
        _assert_simple_radial_strokes(forced_density_strokes, m_to_mm, center, outline=outline, end_radius_xy=(20.0, 7.0))
        _assert_perpendicular_spacing(forced_density_strokes, m_to_mm, center, 1.0)

        ellipse_params = _base_params()
        ellipse_params.spacing_distance_mm = 2.0
        ellipse_strokes = effect_line_gen.generate_focus_strokes(
            ellipse_params,
            center_xy_mm=center,
            radius_x_mm=30.0,
            radius_y_mm=12.0,
            seed=0,
        )
        _assert_simple_radial_strokes(ellipse_strokes, m_to_mm, center, end_radius_xy=(30.0, 12.0))
        _assert_perpendicular_spacing(ellipse_strokes, m_to_mm, center, 2.0)

        shifted_center = (160.0, 145.0)
        end_center = (175.0, 148.0)
        shifted_strokes = effect_line_gen.generate_focus_strokes(
            ellipse_params,
            center_xy_mm=shifted_center,
            radius_x_mm=30.0,
            radius_y_mm=12.0,
            seed=0,
            end_center_xy_mm=end_center,
        )
        _assert_simple_radial_strokes(
            shifted_strokes,
            m_to_mm,
            shifted_center,
            end_radius_xy=(30.0, 12.0),
            end_center=end_center,
        )

        shape_params = _base_params()
        shape_params.start_shape = "rect"
        shape_params.base_shape = "rect"
        shape_params.spacing_distance_mm = 2.0
        shape_strokes = effect_line_gen.generate_focus_strokes(
            shape_params,
            center_xy_mm=center,
            radius_x_mm=30.0,
            radius_y_mm=12.0,
            seed=0,
        )
        _assert_simple_radial_strokes(shape_strokes, m_to_mm, center, end_radius_xy=(30.0, 12.0))
        _assert_perpendicular_spacing(shape_strokes, m_to_mm, center, 2.0)

        length_jitter_params = SimpleNamespace(**vars(frame_params))
        length_jitter_params.length_jitter_enabled = True
        length_jitter_params.length_jitter_amount = 45.0
        length_jitter_strokes = effect_line_gen.generate_focus_strokes(
            length_jitter_params,
            center_xy_mm=center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=12,
            start_outline_mm=outline,
        )
        _assert_start_jitter_moves_starts_inward(length_jitter_strokes, m_to_mm, center, outline)

        end_jitter_params = SimpleNamespace(**vars(frame_params))
        end_jitter_params.end_length_jitter_enabled = True
        end_jitter_params.end_length_jitter_amount = 45.0
        end_jitter_strokes = effect_line_gen.generate_focus_strokes(
            end_jitter_params,
            center_xy_mm=center,
            radius_x_mm=20.0,
            radius_y_mm=7.0,
            seed=12,
            start_outline_mm=outline,
        )
        _assert_length_jitter_shortens_lines(end_jitter_strokes, m_to_mm, center, (20.0, 7.0))
        _assert_full_end_jitter_can_collapse_line(effect_line_gen)

        bundle_params = _base_params()
        bundle_params.bundle_enabled = True
        bundle_params.bundle_line_count = 4
        bundle_params.bundle_line_count_jitter = 1.0
        bundle_params.bundle_gap_mm = 2.0
        bundle_params.bundle_gap_jitter_amount = 1.0
        plain_bundle_params = SimpleNamespace(**vars(bundle_params))
        plain_bundle_params.bundle_line_count_jitter = 0.0
        plain_bundle_params.bundle_gap_jitter_amount = 0.0
        rng_mod = __import__("random")
        jitter_slots = effect_line_gen._slot_positions(80, bundle_params, rng_mod.Random(8))
        plain_slots = effect_line_gen._slot_positions(80, plain_bundle_params, rng_mod.Random(8))
        if jitter_slots == plain_slots or not jitter_slots:
            raise AssertionError("まとまりの数の乱れ・まとまり間隔の乱れが配置に反映されていません")
        _assert_bundle_jagged_keeps_start(effect_line_gen, m_to_mm)

        if "underlay_line_offset_percent" in params.bl_rna.properties:
            raise AssertionError("旧下地線ズラし設定が効果線設定に残っています")
        if "underlay_line_align_endpoints" in params.bl_rna.properties:
            raise AssertionError("旧下地線の終点揃え設定が効果線設定に残っています")
        assert params.white_underlay_enabled is False
        assert abs(float(params.white_underlay_width_percent) - 150.0) <= 1.0e-6

        underlay_params = _base_params()
        underlay_params.effect_type = "focus"
        no_underlay = effect_line_gen.generate_strokes(
            underlay_params,
            center_xy_mm=center,
            radius_xy_mm=(30.0, 12.0),
            seed=5,
        )
        if any(getattr(stroke, "role", "") == "underlay" for stroke in no_underlay):
            raise AssertionError("白抜き線オフで白抜き線ストロークが生成されています")
        underlay_params.white_underlay_enabled = True
        underlay_params.white_underlay_width_percent = 150.0
        right_underlay = effect_line_gen.generate_strokes(
            underlay_params,
            center_xy_mm=center,
            radius_xy_mm=(30.0, 12.0),
            seed=5,
        )
        line_strokes = [stroke for stroke in right_underlay if getattr(stroke, "role", "") == "line"]
        underlay_strokes = [stroke for stroke in right_underlay if getattr(stroke, "role", "") == "underlay"]
        if len(line_strokes) != len(underlay_strokes) or not underlay_strokes:
            raise AssertionError("白抜き線オンで主線と同数の白抜き線が生成されていません")
        if abs(float(underlay_strokes[0].radius) - float(line_strokes[0].radius) * 3.0) > 1.0e-9:
            raise AssertionError("白抜き線幅が効果線の線幅比率で反映されていません")
        line_points = _stroke_points_mm(line_strokes[0], m_to_mm)
        right_points = _stroke_points_mm(underlay_strokes[0], m_to_mm)
        if any(_distance(rp, lp) > 1.0e-6 for rp, lp in zip(right_points, line_points, strict=False)):
            raise AssertionError("白抜き線の中心線が主線からずれています")
        if not effect_line_object._stroke_z_offset(underlay_strokes[0]) < effect_line_object._stroke_z_offset(line_strokes[0]):  # noqa: SLF001
            raise AssertionError("白抜き線が主線より背面になる順序になっていません")
        if any(
            abs(float(up[2]) - float(lp[2])) > 1.0e-10
            for up, lp in zip(underlay_strokes[0].points_xyz, line_strokes[0].points_xyz, strict=False)
        ):
            raise AssertionError("白抜き線の元ストローク座標が主線からずれています")
        if float(getattr(underlay_strokes[0], "side", 0.0) or 0.0) <= 0.0:
            raise AssertionError("白抜き線幅の正値が右側指定として記録されていません")
        underlay_params.white_underlay_width_percent = -150.0
        left_underlay = [
            stroke for stroke in effect_line_gen.generate_strokes(
                underlay_params,
                center_xy_mm=center,
                radius_xy_mm=(30.0, 12.0),
                seed=5,
            )
            if getattr(stroke, "role", "") == "underlay"
        ]
        if float(getattr(left_underlay[0], "side", 0.0) or 0.0) >= 0.0:
            raise AssertionError("白抜き線幅の負値が左側指定として記録されていません")
        mesh = bpy.data.meshes.new("underlay_material_test_mesh")
        material_obj = bpy.data.objects.new("underlay_material_test", mesh)
        effect_line_object._ensure_display_material(  # noqa: SLF001
            material_obj,
            (0.0, 0.0, 0.0, 1.0),
            opacity=1.0,
            underlay_color=(0.25, 0.5, 0.75, 0.6),
        )
        if len(material_obj.data.materials) < 3:
            raise AssertionError("白抜き線用の素材スロットが作成されていません")
        underlay_mat = material_obj.data.materials[2]
        rgba = tuple(float(v) for v in underlay_mat.diffuse_color)
        if any(abs(a - b) > 1.0e-6 for a, b in zip(rgba, (0.25, 0.5, 0.75, 0.6), strict=False)):
            raise AssertionError(f"白抜き線色が素材に反映されていません: {rgba}")

        white_params = _base_params()
        white_params.effect_type = "white_outline"
        white_params.white_outline_count = 6
        white_params.white_outline_width_mm = 10.0
        white_params.white_outline_spacing_mm = 0.4
        white_params.white_outline_white_line_count_auto = False
        white_params.white_outline_white_line_count = 5
        white_params.white_outline_white_ratio_percent = 70.0
        white_params.white_outline_white_brush_mm = 0.3
        white_params.white_outline_white_in_percent = 80.0
        white_params.white_outline_white_out_percent = 0.0
        white_params.white_outline_white_inout_range_mode = "length"
        white_params.white_outline_white_in_range_mm = 4.0
        white_params.white_outline_white_out_range_mm = 6.0
        white_params.white_outline_black_line_count_auto = False
        white_params.white_outline_black_line_count = 2
        white_params.white_outline_black_direction = "outside"
        white_params.white_outline_black_brush_mm = 0.3
        white_params.white_outline_black_spacing_mm = 0.5
        white_params.white_outline_black_width_scale_percent = 80.0
        white_params.white_outline_black_length_scale_near_percent = 100.0
        white_params.white_outline_black_length_scale_far_percent = 75.0
        white_params.white_outline_angle_deg = 0.0
        white_strokes = effect_line_gen.generate_strokes(
            white_params,
            center_xy_mm=center,
            radius_xy_mm=(30.0, 12.0),
            seed=5,
        )
        _assert_white_outline_is_radial(
            white_strokes,
            m_to_mm,
            center,
            6,
            expected_white_lines_per_band=5,
            expected_black_lines_per_band=4,
        )

        if effect_line_op._effect_hit_part((10.0, 20.0, 40.0, 24.0), 30.0, 32.0) != "center":
            raise AssertionError("effect center cross should be draggable as a center hit")
        if effect_line_op._effect_hit_part(
            (10.0, 20.0, 40.0, 24.0),
            46.0,
            48.0,
            center_xy_mm=(46.0, 48.0),
        ) != "center":
            raise AssertionError("effect center cross should be draggable away from the end shape")
        if effect_line_op._effect_hit_part(
            (10.0, 20.0, 40.0, 24.0),
            30.0,
            32.0,
            center_xy_mm=(46.0, 48.0),
        ) == "center":
            raise AssertionError("effect center hit should follow the stored center, not the end shape center")
        if effect_line_op._effect_hit_part((10.0, 20.0, 40.0, 24.0), 10.0, 44.0) != "top_left":
            raise AssertionError("effect corner handles should keep priority over the center hit")
        drag = SimpleNamespace(
            _drag_action="center",
            _drag_orig_x=10.0,
            _drag_orig_y=20.0,
            _drag_orig_w=40.0,
            _drag_orig_h=24.0,
            _drag_orig_center_x=46.0,
            _drag_orig_center_y=48.0,
        )
        drag_bounds = effect_line_op.BMANGA_OT_effect_line_tool._drag_result_bounds(drag, 5.0, -3.0)
        drag_center = effect_line_op.BMANGA_OT_effect_line_tool._drag_result_center(drag, drag_bounds, 5.0, -3.0)
        if drag_bounds != (10.0, 20.0, 40.0, 24.0) or drag_center != (51.0, 45.0):
            raise AssertionError(f"中心点ドラッグで終点形状が動いています: bounds={drag_bounds}, center={drag_center}")
        fills = []
        outlines = []

        def _fill(rect, color):
            fills.append((rect, color))

        def _outline(rect, *args, **kwargs):
            outlines.append((rect, args, kwargs))

        overlay_effect_line._draw_bounds(
            (10.0, 20.0, 40.0, 24.0),
            center_xy=(46.0, 48.0),
            draw_rect_fill=_fill,
            draw_rect_outline=_outline,
        )
        center_fills = [
            rect for rect, _color in fills
            if abs((rect.x + rect.width * 0.5) - 46.0) < 0.01
            and abs((rect.y + rect.height * 0.5) - 48.0) < 0.01
        ]
        if len(center_fills) < 2:
            raise AssertionError("effect center cross should draw horizontal and vertical bars")

        linked_params = effect_line_link_op._copy_linked_shape_params(
            {
                "spacing_density_compensation": True,
                "white_outline_white_line_count": 11,
                "white_outline_white_out_range_mm": 7.5,
                "white_outline_black_line_count": 4,
                "white_outline_black_direction": "outside",
                "white_outline_angle_deg": 33.0,
            },
            {
                "spacing_density_compensation": False,
                "white_outline_white_line_count": 3,
                "white_outline_black_line_count": 1,
                "white_outline_angle_deg": 0.0,
            },
        )
        if "start_frame_density_basis" in linked_params or "start_frame_density_rounding_percent" in linked_params:
            raise AssertionError("リンク効果線に密度基準の同期項目が残っています")
        assert linked_params["spacing_density_compensation"] is True
        assert linked_params["white_outline_white_line_count"] == 11
        assert abs(float(linked_params["white_outline_white_out_range_mm"]) - 7.5) < 1.0e-6
        assert linked_params["white_outline_black_line_count"] == 4
        assert linked_params["white_outline_black_direction"] == "outside"
        assert linked_params["white_outline_angle_deg"] == 0.0
        print("BMANGA_EFFECT_LINE_FRAME_SPACING_OK")
    finally:
        mod.unregister()


if __name__ == "__main__":
    main()
