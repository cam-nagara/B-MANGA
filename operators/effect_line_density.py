"""効果線の密度補正用ジオメトリ."""

from __future__ import annotations

import math
from collections.abc import Sequence


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


def blend_angles(base: float, target: float, amount: float) -> float:
    amount = _clamp01(amount)
    if amount <= 0.0:
        return float(base)
    delta = math.atan2(math.sin(float(target) - float(base)), math.cos(float(target) - float(base)))
    return float(base) + delta * amount
