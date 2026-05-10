"""集中線のコマ枠始点間隔を計算する補助ロジック."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _ray_outline_point(
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    angle: float,
    *,
    extend_mm: float = 0.0,
) -> tuple[float, float] | None:
    if len(outline) < 2:
        return None
    cx, cy = center_xy_mm
    dx = math.cos(angle)
    dy = math.sin(angle)
    best_t: float | None = None
    for index, a in enumerate(outline):
        b = outline[(index + 1) % len(outline)]
        sx = b[0] - a[0]
        sy = b[1] - a[1]
        denom = _cross(dx, dy, sx, sy)
        if abs(denom) < 1.0e-9:
            continue
        qx = a[0] - cx
        qy = a[1] - cy
        t = _cross(qx, qy, sx, sy) / denom
        u = _cross(qx, qy, dx, dy) / denom
        if t >= -1.0e-6 and -1.0e-6 <= u <= 1.0 + 1.0e-6:
            if best_t is None or t < best_t:
                best_t = t
    if best_t is None:
        return None
    distance = max(0.0, best_t + float(extend_mm))
    return cx + dx * distance, cy + dy * distance


def _radial_metric_samples(
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    sample_count = 4096
    cx, cy = center_xy_mm
    out: list[tuple[float, float]] = []
    for index in range(sample_count):
        angle = (2.0 * math.pi) * index / sample_count
        point = _ray_outline_point(center_xy_mm, outline, angle)
        if point is None:
            continue
        radius = math.hypot(point[0] - cx, point[1] - cy)
        if radius > 1.0e-6:
            out.append((angle, radius))
    return out


def _radial_metric_cumulative(samples: Sequence[tuple[float, float]]) -> tuple[list[float], float]:
    cumulative = [0.0]
    total = 0.0
    for index, (angle, radius) in enumerate(samples):
        next_angle = samples[(index + 1) % len(samples)][0]
        delta = next_angle - angle
        if index == len(samples) - 1:
            delta += 2.0 * math.pi
        total += max(0.0, float(radius) * math.tan(max(0.0, delta)))
        cumulative.append(total)
    return cumulative, total


def _radial_angle_at_metric(
    samples: Sequence[tuple[float, float]],
    cumulative: Sequence[float],
    target: float,
) -> float:
    total = float(cumulative[-1]) if cumulative else 0.0
    if total <= 1.0e-9:
        return samples[0][0] if samples else 0.0
    target = float(target) % total
    low = 0
    high = len(cumulative) - 1
    while low < high:
        mid = (low + high) // 2
        if cumulative[mid] <= target:
            low = mid + 1
        else:
            high = mid
    index = max(0, min(len(samples) - 1, low - 1))
    start_len = float(cumulative[index])
    segment_len = max(1.0e-9, float(cumulative[index + 1]) - start_len)
    local = (target - start_len) / segment_len
    start_angle = float(samples[index][0])
    end_angle = float(samples[(index + 1) % len(samples)][0])
    if index == len(samples) - 1:
        end_angle += 2.0 * math.pi
    return (start_angle + (end_angle - start_angle) * local) % (2.0 * math.pi)


def frame_points_for_perpendicular_spacing(
    params,
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    start_extend_mm: float,
    rng: random.Random,
    *,
    max_line_count: Callable[[object], int],
    slot_positions: Callable[[int, object, random.Random], list[float]],
    clamp01: Callable[[float], float],
) -> list[tuple[float, float]]:
    samples = _radial_metric_samples(center_xy_mm, outline)
    if len(samples) < 2:
        return []
    cumulative, total = _radial_metric_cumulative(samples)
    if total <= 1.0e-9:
        return []

    step_mm = max(0.01, float(getattr(params, "spacing_distance_mm", 1.0)))
    count = min(max_line_count(params), max(8, int(round(total / step_mm))))
    if count <= 0:
        return []

    points: list[tuple[float, float]] = []
    for slot in slot_positions(count, params, rng):
        slot_for_target = slot
        if bool(getattr(params, "spacing_jitter_enabled", False)):
            amount = clamp01(getattr(params, "spacing_jitter_amount", 0.0))
            slot_for_target += amount * (rng.random() * 2.0 - 1.0)
        target = ((float(slot_for_target) % count) / float(count)) * total
        angle = _radial_angle_at_metric(samples, cumulative, target)
        point = _ray_outline_point(center_xy_mm, outline, angle, extend_mm=start_extend_mm)
        if point is not None:
            points.append(point)
    return points
