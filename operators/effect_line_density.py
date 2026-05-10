"""効果線の密度補正用ジオメトリ."""

from __future__ import annotations

import math
from collections.abc import Sequence

_ANGLE_TABLE_CACHE_MAX = 32
_ANGLE_TABLE_CACHE: dict[
    tuple,
    tuple[list[float], list[float], float],
] = {}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def poly_perimeter_mm(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        total += math.hypot(nxt[0] - point[0], nxt[1] - point[1])
    return total


def outline_point_at_fraction(
    outline: Sequence[tuple[float, float]],
    fraction: float,
) -> tuple[float, float] | None:
    if len(outline) < 2:
        return None
    perimeter = poly_perimeter_mm(outline)
    if perimeter <= 1.0e-9:
        return (float(outline[0][0]), float(outline[0][1]))
    target = (float(fraction) % 1.0) * perimeter
    walked = 0.0
    for index, point in enumerate(outline):
        nxt = outline[(index + 1) % len(outline)]
        length = math.hypot(nxt[0] - point[0], nxt[1] - point[1])
        if length <= 1.0e-9:
            continue
        if walked + length >= target:
            t = max(0.0, min(1.0, (target - walked) / length))
            return (
                float(point[0]) + (float(nxt[0]) - float(point[0])) * t,
                float(point[1]) + (float(nxt[1]) - float(point[1])) * t,
            )
        walked += length
    point = outline[-1]
    return (float(point[0]), float(point[1]))


def _rounded_corner_points(
    prev_pt: tuple[float, float],
    corner: tuple[float, float],
    next_pt: tuple[float, float],
    amount: float,
    samples: int,
) -> list[tuple[float, float]]:
    px, py = prev_pt
    cx, cy = corner
    nx, ny = next_pt
    len_prev = math.hypot(px - cx, py - cy)
    len_next = math.hypot(nx - cx, ny - cy)
    if len_prev <= 1.0e-9 or len_next <= 1.0e-9 or amount <= 0.0:
        return [(cx, cy)]
    cut = min(len_prev, len_next) * min(0.5, amount * 0.5)
    start = (cx + (px - cx) * (cut / len_prev), cy + (py - cy) * (cut / len_prev))
    end = (cx + (nx - cx) * (cut / len_next), cy + (ny - cy) * (cut / len_next))
    out: list[tuple[float, float]] = []
    steps = max(2, int(samples))
    for index in range(steps + 1):
        t = index / steps
        mt = 1.0 - t
        x = mt * mt * start[0] + 2.0 * mt * t * cx + t * t * end[0]
        y = mt * mt * start[1] + 2.0 * mt * t * cy + t * t * end[1]
        out.append((x, y))
    return out


def rounded_outline(
    outline: Sequence[tuple[float, float]],
    rounding_percent: float,
    *,
    samples_per_corner: int = 12,
) -> list[tuple[float, float]]:
    points = [(float(x), float(y)) for x, y in outline]
    if len(points) < 3:
        return points
    amount = _clamp01(float(rounding_percent) / 100.0)
    if amount <= 0.0:
        return points
    out: list[tuple[float, float]] = []
    for index, corner in enumerate(points):
        prev_pt = points[index - 1]
        next_pt = points[(index + 1) % len(points)]
        out.extend(_rounded_corner_points(prev_pt, corner, next_pt, amount, samples_per_corner))
    return out


def ellipse_outline_from_bounds(
    outline: Sequence[tuple[float, float]],
    *,
    samples: int = 192,
) -> list[tuple[float, float]]:
    points = [(float(x), float(y)) for x, y in outline]
    if len(points) < 2:
        return points
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    rx = max(0.001, (max(xs) - min(xs)) * 0.5)
    ry = max(0.001, (max(ys) - min(ys)) * 0.5)
    count = max(24, int(samples))
    return [
        (cx + math.cos(2.0 * math.pi * i / count) * rx, cy + math.sin(2.0 * math.pi * i / count) * ry)
        for i in range(count)
    ]


def frame_density_outline(params, actual_outline: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    mode = str(getattr(params, "start_frame_density_basis", "frame") or "frame")
    if mode == "ellipse":
        return ellipse_outline_from_bounds(actual_outline)
    if mode == "rounded_frame":
        percent = float(getattr(params, "start_frame_density_rounding_percent", 100.0))
        return rounded_outline(actual_outline, percent)
    return [(float(x), float(y)) for x, y in actual_outline]


def density_compensation_strength(params) -> float:
    mode = str(getattr(params, "spacing_density_compensation", "none") or "none")
    if mode == "weak":
        return 0.35
    if mode == "medium":
        return 0.65
    if mode == "strong":
        return 1.0
    return 0.0


def _outline_points(outline: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in outline]


def _angle(center: tuple[float, float], point: tuple[float, float]) -> float:
    return math.atan2(float(point[1]) - center[1], float(point[0]) - center[0])


def _unwrapped_next(prev_angle: float, next_angle: float) -> float:
    delta = math.atan2(math.sin(next_angle - prev_angle), math.cos(next_angle - prev_angle))
    return prev_angle + delta


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _ray_outline_point(
    center: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    angle: float,
) -> tuple[float, float] | None:
    points = _outline_points(outline)
    if len(points) < 2:
        return None
    cx, cy = center
    dx = math.cos(angle)
    dy = math.sin(angle)
    best_t: float | None = None
    for index, a in enumerate(points):
        b = points[(index + 1) % len(points)]
        sx = b[0] - a[0]
        sy = b[1] - a[1]
        denom = _cross(dx, dy, sx, sy)
        if abs(denom) <= 1.0e-9:
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
    return cx + dx * max(0.0, best_t), cy + dy * max(0.0, best_t)


def _mix_points(
    a: tuple[float, float],
    b: tuple[float, float],
    amount: float,
) -> tuple[float, float]:
    return (
        float(a[0]) + (float(b[0]) - float(a[0])) * float(amount),
        float(a[1]) + (float(b[1]) - float(a[1])) * float(amount),
    )


def _segment_separation_metric(
    center: tuple[float, float],
    start_outline: Sequence[tuple[float, float]],
    end_outline: Sequence[tuple[float, float]],
    prev_angle: float,
    angle: float,
    fallback: float,
) -> float:
    prev_start = _ray_outline_point(center, start_outline, prev_angle)
    start = _ray_outline_point(center, start_outline, angle)
    prev_end = _ray_outline_point(center, end_outline, prev_angle)
    end = _ray_outline_point(center, end_outline, angle)
    if prev_start is None or start is None or prev_end is None or end is None:
        return float(fallback)
    # 線端だけを均すと途中に帯が出るため、見た目で目立つ中間域を主に見る。
    gaps: list[float] = []
    for pos in (
        0.00,
        0.20,
        0.40,
        0.60,
        0.80,
        1.00,
    ):
        a = _mix_points(prev_start, prev_end, pos)
        b = _mix_points(start, end, pos)
        gaps.append(max(0.001, math.hypot(b[0] - a[0], b[1] - a[1])))
    if not gaps:
        return float(fallback)
    return min(gaps)


def _outline_cache_key(outline: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    return tuple((round(float(x), 5), round(float(y), 5)) for x, y in outline)


def _cache_get(key: tuple) -> tuple[list[float], list[float], float] | None:
    return _ANGLE_TABLE_CACHE.get(key)


def _cache_put(key: tuple, value: tuple[list[float], list[float], float]) -> None:
    if len(_ANGLE_TABLE_CACHE) >= _ANGLE_TABLE_CACHE_MAX:
        _ANGLE_TABLE_CACHE.pop(next(iter(_ANGLE_TABLE_CACHE)))
    _ANGLE_TABLE_CACHE[key] = value


def _angular_metric_table(
    center: tuple[float, float],
    basis_outline: Sequence[tuple[float, float]],
    start_outline: Sequence[tuple[float, float]],
    end_outline: Sequence[tuple[float, float]],
    amount: float,
    *,
    sample_count: int = 1440,
) -> tuple[list[float], list[float], float]:
    samples = max(360, int(sample_count))
    key = (
        round(float(center[0]), 5),
        round(float(center[1]), 5),
        _outline_cache_key(basis_outline),
        _outline_cache_key(start_outline),
        _outline_cache_key(end_outline),
        round(float(amount), 5),
        samples,
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached

    angles = [(2.0 * math.pi * index) / samples for index in range(samples + 1)]
    basis_radii = []
    for angle in angles[:-1]:
        point = _ray_outline_point(center, basis_outline, angle)
        if point is not None:
            basis_radii.append(math.hypot(point[0] - center[0], point[1] - center[1]))
    mean_radius = max(0.001, sum(basis_radii) / max(1, len(basis_radii)))

    cumulative = [0.0]
    total = 0.0
    prev_basis = _ray_outline_point(center, basis_outline, angles[0])
    for index in range(1, len(angles)):
        prev_angle = angles[index - 1]
        angle = angles[index]
        basis = _ray_outline_point(center, basis_outline, angle)
        angular_fallback = mean_radius * (angle - prev_angle)
        if prev_basis is None or basis is None:
            basis_metric = angular_fallback
        else:
            basis_metric = math.hypot(basis[0] - prev_basis[0], basis[1] - prev_basis[1])
        segment_metric = _segment_separation_metric(
            center,
            start_outline,
            end_outline,
            prev_angle,
            angle,
            angular_fallback,
        )
        total += max(0.0, basis_metric * (1.0 - amount) + segment_metric * amount)
        cumulative.append(total)
        prev_basis = basis

    value = (angles, cumulative, total)
    _cache_put(key, value)
    return value


def _sample_closed_outline(
    outline: Sequence[tuple[float, float]],
    *,
    center: tuple[float, float] | None = None,
    samples_per_edge: int = 10,
    max_step_mm: float = 1.0,
    max_angle_step_rad: float = math.radians(0.5),
) -> list[tuple[float, float]]:
    points = _outline_points(outline)
    if len(points) < 2:
        return points
    samples: list[tuple[float, float]] = [points[0]]
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        steps = max(1, int(samples_per_edge))
        if max_step_mm > 0.0:
            steps = max(steps, int(math.ceil(length / float(max_step_mm))))
        if center is not None and max_angle_step_rad > 0.0:
            start_angle = _angle(center, start)
            end_angle = _unwrapped_next(start_angle, _angle(center, end))
            steps = max(steps, int(math.ceil(abs(end_angle - start_angle) / float(max_angle_step_rad))))
        for step in range(1, steps + 1):
            t = step / steps
            samples.append((start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
    return samples


def compensated_frame_angle(
    center: tuple[float, float],
    start_outline: Sequence[tuple[float, float]],
    end_outline: Sequence[tuple[float, float]],
    fraction: float,
    strength: float,
    *,
    actual_start_outline: Sequence[tuple[float, float]] | None = None,
) -> float | None:
    """Return a frame angle corrected by radial visual density.

    ``strength`` 0 keeps equal distance on the chosen frame basis. Stronger
    values measure the actual generated line segments at several positions and
    reduce slots where the line path becomes crowded.
    """
    amount = _clamp01(strength)
    if amount <= 0.0:
        point = outline_point_at_fraction(start_outline, fraction)
        return None if point is None else _angle(center, point)
    line_start_outline = actual_start_outline or start_outline
    angles, cumulative, total = _angular_metric_table(
        center,
        start_outline,
        line_start_outline,
        end_outline,
        amount,
    )
    if total <= 1.0e-9:
        point = outline_point_at_fraction(start_outline, fraction)
        return None if point is None else _angle(center, point)
    target = (float(fraction) % 1.0) * total
    for index in range(1, len(cumulative)):
        if cumulative[index] < target:
            continue
        span = cumulative[index] - cumulative[index - 1]
        t = 0.0 if span <= 1.0e-9 else (target - cumulative[index - 1]) / span
        return angles[index - 1] + (angles[index] - angles[index - 1]) * t
    return angles[-1]


def compensated_frame_metric_total(
    center: tuple[float, float],
    start_outline: Sequence[tuple[float, float]],
    end_outline: Sequence[tuple[float, float]],
    strength: float,
    *,
    actual_start_outline: Sequence[tuple[float, float]] | None = None,
) -> float:
    amount = _clamp01(strength)
    if amount <= 0.0:
        return poly_perimeter_mm(start_outline)
    line_start_outline = actual_start_outline or start_outline
    _angles, _cumulative, total = _angular_metric_table(
        center,
        start_outline,
        line_start_outline,
        end_outline,
        amount,
    )
    return total if total > 1.0e-9 else poly_perimeter_mm(start_outline)


def blend_angles(base: float, target: float, amount: float) -> float:
    amount = _clamp01(amount)
    if amount <= 0.0:
        return float(base)
    delta = math.atan2(math.sin(float(target) - float(base)), math.cos(float(target) - float(base)))
    return float(base) + delta * amount
