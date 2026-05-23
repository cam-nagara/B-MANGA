"""フキダシ多重線用の補助カーブ生成."""

from __future__ import annotations

import math
from typing import Sequence

import bpy

from . import balloon_shapes
from .geom import Rect, mm_to_m


def _point_to_curve_xyz(point: tuple[float, float], offset: tuple[float, float]) -> tuple[float, float, float]:
    return (
        mm_to_m(float(point[0]) + offset[0]),
        mm_to_m(float(point[1]) + offset[1]),
        0.0,
    )


def _cubic_bezier_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    u = 1.0 - float(t)
    tt = float(t) * float(t)
    uu = u * u
    uuu = uu * u
    ttt = tt * float(t)
    return (
        uuu * p0[0] + 3.0 * uu * float(t) * p1[0] + 3.0 * u * tt * p2[0] + ttt * p3[0],
        uuu * p0[1] + 3.0 * uu * float(t) * p1[1] + 3.0 * u * tt * p2[1] + ttt * p3[1],
    )


def sample_bezier_anchors(
    anchors: Sequence[balloon_shapes.BezierAnchor],
    *,
    samples_per_segment: int = 16,
) -> list[tuple[float, float]]:
    if len(anchors) < 3:
        return []
    samples: list[tuple[float, float]] = []
    steps = max(4, int(samples_per_segment))
    for index, anchor in enumerate(anchors):
        next_anchor = anchors[(index + 1) % len(anchors)]
        p0 = anchor.co
        p1 = anchor.handle_right if anchor.handle_right is not None else anchor.co
        p2 = next_anchor.handle_left if next_anchor.handle_left is not None else next_anchor.co
        p3 = next_anchor.co
        for step in range(steps):
            samples.append(_cubic_bezier_point(p0, p1, p2, p3, step / steps))
    return samples


def body_outline_for_entry(entry) -> tuple[list[tuple[float, float]], list[int]]:
    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    return balloon_shapes.outline_with_corners_for_entry(entry, rect)


def body_outline_point_radii(entry, points: Sequence[tuple[float, float]]) -> list[float] | None:
    if balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect")) != "thorn":
        return None
    if str(getattr(entry, "line_style", "") or "") != "double":
        return None
    if not points:
        return None
    base_width = max(1.0e-6, float(getattr(entry, "multi_line_width_mm", 0.3) or 0.3))
    valley = max(0.0, float(getattr(entry, "thorn_multi_line_valley_width_mm", base_width) or 0.0)) / base_width
    peak = max(0.0, float(getattr(entry, "thorn_multi_line_peak_width_mm", base_width) or 0.0)) / base_width
    return [valley if index % 2 == 0 else peak for index in range(len(points))]


def _set_poly_point(
    point,
    xy: tuple[float, float],
    *,
    offset: tuple[float, float],
    point_radius: float,
) -> None:
    x, y, z = _point_to_curve_xyz(xy, offset)
    point.co = (x, y, z, 1.0)
    try:
        point.radius = max(0.0, float(point_radius))
    except Exception:  # noqa: BLE001
        pass


def _add_open_poly_segment(
    curve: bpy.types.Curve,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    offset: tuple[float, float],
    point_radius: float | tuple[float, float],
) -> None:
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.use_cyclic_u = False
    spline.material_index = 0
    if isinstance(point_radius, tuple):
        radii = (float(point_radius[0]), float(point_radius[1]))
    else:
        radii = (float(point_radius), float(point_radius))
    for index, point in enumerate((start, end)):
        _set_poly_point(spline.points[index], point, offset=offset, point_radius=radii[index])


def _add_open_poly_path(
    curve: bpy.types.Curve,
    points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
    point_radius: float,
    close: bool = False,
) -> None:
    if len(points) < 2:
        return
    path = [(float(x), float(y)) for x, y in points]
    if close and math.hypot(path[0][0] - path[-1][0], path[0][1] - path[-1][1]) > 1.0e-6:
        path.append(path[0])
    if len(path) < 2:
        return
    spline = curve.splines.new("POLY")
    spline.points.add(len(path) - 1)
    spline.use_cyclic_u = False
    spline.material_index = 0
    for index, point in enumerate(path):
        _set_poly_point(spline.points[index], point, offset=offset, point_radius=point_radius)


def _polygon_signed_area(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    previous = points[-1]
    for current in points:
        area += (previous[0] * current[1]) - (current[0] * previous[1])
        previous = current
    return area * 0.5


def _normalize_2d(vector: tuple[float, float]) -> tuple[float, float] | None:
    length = math.hypot(float(vector[0]), float(vector[1]))
    if length <= 1.0e-9:
        return None
    return (float(vector[0]) / length, float(vector[1]) / length)


def _line_intersection_2d(
    p1: tuple[float, float],
    d1: tuple[float, float],
    p2: tuple[float, float],
    d2: tuple[float, float],
) -> tuple[float, float] | None:
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) <= 1.0e-9:
        return None
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    t = (dx * d2[1] - dy * d2[0]) / det
    return (p1[0] + d1[0] * t, p1[1] + d1[1] * t)


def _segment_outward_normal(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    clockwise: bool,
) -> tuple[float, float]:
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    length = math.hypot(dx, dy)
    if length <= 1.0e-9:
        return (0.0, 0.0)
    if clockwise:
        return (-dy / length, dx / length)
    return (dy / length, -dx / length)


def _offset_segment_for_side(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    distance_mm: float,
    clockwise: bool,
    side: str,
) -> tuple[tuple[float, float], tuple[float, float]]:
    nx, ny = _segment_outward_normal(start, end, clockwise=clockwise)
    if side == "inside":
        nx = -nx
        ny = -ny
    return (
        (float(start[0]) + nx * distance_mm, float(start[1]) + ny * distance_mm),
        (float(end[0]) + nx * distance_mm, float(end[1]) + ny * distance_mm),
    )


def _offset_closed_outline(
    points: Sequence[tuple[float, float]],
    *,
    distance_mm: float,
    clockwise: bool,
    side: str,
) -> list[tuple[float, float]] | None:
    if len(points) < 3:
        return None
    result: list[tuple[float, float]] = []
    direction_sign = -1.0 if side == "inside" else 1.0
    for index, current in enumerate(points):
        previous = points[index - 1]
        next_point = points[(index + 1) % len(points)]
        d_prev = _normalize_2d((current[0] - previous[0], current[1] - previous[1]))
        d_next = _normalize_2d((next_point[0] - current[0], next_point[1] - current[1]))
        if d_prev is None or d_next is None:
            return None
        n_prev = _segment_outward_normal(previous, current, clockwise=clockwise)
        n_next = _segment_outward_normal(current, next_point, clockwise=clockwise)
        n_prev = (n_prev[0] * direction_sign, n_prev[1] * direction_sign)
        n_next = (n_next[0] * direction_sign, n_next[1] * direction_sign)
        p1 = (current[0] + n_prev[0] * distance_mm, current[1] + n_prev[1] * distance_mm)
        p2 = (current[0] + n_next[0] * distance_mm, current[1] + n_next[1] * distance_mm)
        hit = _line_intersection_2d(p1, d_prev, p2, d_next)
        if hit is not None and math.hypot(hit[0] - current[0], hit[1] - current[1]) <= max(distance_mm * 8.0, distance_mm + 1.0e-6):
            result.append(hit)
            continue
        bis = _normalize_2d((n_prev[0] + n_next[0], n_prev[1] + n_next[1]))
        if bis is None:
            result.append(p1)
        else:
            result.append((current[0] + bis[0] * distance_mm, current[1] + bis[1] * distance_mm))
    return result


def append_closed_multi_line_paths(
    curve: bpy.types.Curve,
    entry,
    body_points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
) -> None:
    shape_name = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    if shape_name == "thorn":
        return
    if str(getattr(entry, "line_style", "") or "") != "double":
        return
    count = max(1, min(12, int(getattr(entry, "multi_line_count", 3) or 3)))
    if count <= 1 or len(body_points) < 3:
        return
    line_width_mm = max(1.0e-6, float(getattr(entry, "line_width_mm", 0.3) or 0.3))
    multi_width_mm = max(0.0, float(getattr(entry, "multi_line_width_mm", 0.3) or 0.0))
    spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
    width_scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    if multi_width_mm <= 0.0:
        return
    direction = str(getattr(entry, "multi_line_direction", "outside") or "outside")
    sides = ("inside", "outside") if direction == "both" else ("inside",) if direction == "inside" else ("outside",)
    clockwise = _polygon_signed_area(body_points) < 0.0
    current_inner_mm = line_width_mm * 0.5 + spacing_mm
    for ring_index in range(1, count):
        ring_width_mm = multi_width_mm * (width_scale ** max(0, ring_index - 1))
        if ring_width_mm <= 0.0:
            current_inner_mm += spacing_mm
            continue
        distance_mm = current_inner_mm + ring_width_mm * 0.5
        point_radius = ring_width_mm / line_width_mm
        for side in sides:
            ring_points = _offset_closed_outline(
                body_points,
                distance_mm=distance_mm,
                clockwise=clockwise,
                side=side,
            )
            if ring_points is None:
                continue
            _add_open_poly_path(
                curve,
                ring_points,
                offset=offset,
                point_radius=point_radius,
                close=True,
            )
        current_inner_mm += ring_width_mm + spacing_mm


def append_thorn_multi_line_segments(
    curve: bpy.types.Curve,
    entry,
    body_points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
) -> None:
    if balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect")) != "thorn":
        return
    if str(getattr(entry, "line_style", "") or "") != "double":
        return
    if len(body_points) < 4:
        return
    count = max(1, min(12, int(getattr(entry, "multi_line_count", 3) or 3)))
    if count <= 1:
        return
    line_width_mm = max(1.0e-6, float(getattr(entry, "line_width_mm", 0.3) or 0.3))
    multi_width_mm = max(0.0, float(getattr(entry, "multi_line_width_mm", 0.3) or 0.0))
    valley_base_width_mm = max(0.0, float(getattr(entry, "thorn_multi_line_valley_width_mm", multi_width_mm) or 0.0))
    peak_base_width_mm = max(0.0, float(getattr(entry, "thorn_multi_line_peak_width_mm", multi_width_mm) or 0.0))
    spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
    width_scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    length_scale = max(0.0, float(getattr(entry, "thorn_multi_line_length_scale_percent", 100.0) or 0.0)) / 100.0
    if multi_width_mm <= 0.0 and valley_base_width_mm <= 0.0 and peak_base_width_mm <= 0.0:
        return
    direction = str(getattr(entry, "multi_line_direction", "outside") or "outside")
    sides = ("inside", "outside") if direction == "both" else ("inside",) if direction == "inside" else ("outside",)
    clockwise = _polygon_signed_area(body_points) < 0.0
    current_inner_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.3)) * 0.5 + spacing_mm
    for ring_index in range(1, count):
        ring_width_mm = multi_width_mm * (width_scale ** max(0, ring_index - 1))
        valley_width_mm = valley_base_width_mm * (width_scale ** max(0, ring_index - 1))
        peak_width_mm = peak_base_width_mm * (width_scale ** max(0, ring_index - 1))
        ring_extent_width_mm = max(ring_width_mm, valley_width_mm, peak_width_mm)
        if ring_extent_width_mm <= 0.0:
            current_inner_mm += spacing_mm
            continue
        center_distance_mm = current_inner_mm + ring_extent_width_mm * 0.5
        point_radii = (valley_width_mm / line_width_mm, peak_width_mm / line_width_mm)
        segment_factor = max(0.0, length_scale ** ring_index)
        for peak_index in range(1, len(body_points), 2):
            peak = body_points[peak_index]
            previous_valley = body_points[(peak_index - 1) % len(body_points)]
            next_valley = body_points[(peak_index + 1) % len(body_points)]
            for valley in (previous_valley, next_valley):
                for side in sides:
                    start, full_end = _offset_segment_for_side(
                        valley,
                        peak,
                        distance_mm=center_distance_mm,
                        clockwise=clockwise,
                        side=side,
                    )
                    end = (
                        start[0] + (full_end[0] - start[0]) * segment_factor,
                        start[1] + (full_end[1] - start[1]) * segment_factor,
                    )
                    if math.hypot(end[0] - start[0], end[1] - start[1]) <= 1.0e-6:
                        continue
                    _add_open_poly_segment(
                        curve,
                        start,
                        end,
                        offset=offset,
                        point_radius=point_radii,
                    )
        current_inner_mm += ring_extent_width_mm + spacing_mm
