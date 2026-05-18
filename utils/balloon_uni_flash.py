"""ウニフラッシュフキダシの共通ジオメトリ生成."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from .geom import Rect

Point = tuple[float, float]
Segment = tuple[Point, Point]

SHAPE_ID = "uni_flash"


@dataclass(frozen=True)
class UniFlashGeometry:
    fill_outline_mm: list[Point]
    line_segments_mm: list[Segment]
    outer_outline_mm: list[Point]


def is_uni_flash_shape(shape: str | None) -> bool:
    return str(shape or "") == SHAPE_ID


def is_uni_flash_entry(entry) -> bool:
    return is_uni_flash_shape(getattr(entry, "shape", ""))


def geometry_for_entry(entry, rect: Rect) -> UniFlashGeometry:
    sp = getattr(entry, "shape_params", None)
    spacing = max(0.01, float(getattr(sp, "uni_flash_spacing_mm", 0.4) or 0.4))
    fill_scale = _clamp(
        float(getattr(sp, "uni_flash_fill_scale_percent", 70.0) or 70.0) / 100.0,
        0.20,
        0.95,
    )
    max_count = max(8, int(getattr(sp, "uni_flash_max_line_count", 1000) or 1000))
    rcx, rcy = rect.center
    center = (
        rcx + float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
        rcy + float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
    )
    outer = _outline_rect(rect)
    fill_rect = _scaled_center_rect(rect, fill_scale, center=center)
    inner = _outline_ellipse(fill_rect)
    seed = _stable_seed(str(getattr(entry, "id", "") or SHAPE_ID))
    line_segments = _focus_segments(
        center,
        outer,
        inner,
        spacing,
        max_count,
        True,
        seed,
    )
    fill_segments = _focus_segments(
        center,
        outer,
        inner,
        spacing,
        max_count,
        True,
        seed,
    )
    fill_outline = [segment[1] for segment in fill_segments]
    if len(fill_outline) < 3:
        fill_outline = inner
    return UniFlashGeometry(
        fill_outline_mm=fill_outline,
        line_segments_mm=line_segments,
        outer_outline_mm=outer,
    )


def _focus_segments(
    center: Point,
    outer_outline: Sequence[Point],
    inner_outline: Sequence[Point],
    spacing_mm: float,
    max_count: int,
    density_compensation: bool,
    seed: int,
) -> list[Segment]:
    rng = random.Random(seed)
    if density_compensation:
        starts = _perpendicular_spaced_points(
            center,
            outer_outline,
            spacing_mm,
            max_count,
        )
    else:
        count = _line_count_by_perimeter(outer_outline, spacing_mm, max_count)
        starts = [
            point
            for index in range(count)
            if (point := _outline_point_at_fraction(outer_outline, index / max(1, count))) is not None
        ]
    out: list[Segment] = []
    for index, start in enumerate(starts):
        end = _focus_end_point(center, inner_outline, start)
        end = _jag_end_point(center, end, index)
        # 効果線の集中線と同じく、終点側へ線を引く。
        if _distance(start, end) > 1.0e-6:
            out.append((start, end))
    # seed は将来の乱れ追加に備えて固定しておく。
    rng.random()
    return out


def _scaled_center_rect(rect: Rect, scale: float, *, center: Point | None = None) -> Rect:
    w = max(0.1, rect.width * scale)
    h = max(0.1, rect.height * scale)
    cx, cy = center if center is not None else rect.center
    return Rect(cx - w * 0.5, cy - h * 0.5, w, h)


def _outline_rect(rect: Rect) -> list[Point]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _outline_ellipse(rect: Rect, segments: int = 96) -> list[Point]:
    cx, cy = rect.center
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    return [
        (cx + rx * math.cos(2.0 * math.pi * index / segments), cy + ry * math.sin(2.0 * math.pi * index / segments))
        for index in range(segments)
    ]


def _line_count_by_perimeter(outline: Sequence[Point], spacing_mm: float, max_count: int) -> int:
    perimeter = _poly_perimeter_mm(outline)
    return max(8, min(int(max_count), int(round(perimeter / max(0.01, spacing_mm)))))


def _perpendicular_spaced_points(
    center: Point,
    outline: Sequence[Point],
    spacing_mm: float,
    max_count: int,
) -> list[Point]:
    samples = _radial_metric_samples(center, outline)
    if len(samples) < 2:
        return []
    cumulative, total = _radial_metric_cumulative(samples)
    if total <= 1.0e-9:
        return []
    count = max(8, min(int(max_count), int(round(total / max(0.01, spacing_mm)))))
    points: list[Point] = []
    for index in range(count):
        angle = _radial_angle_at_metric(samples, cumulative, (index / max(1, count)) * total)
        point = _ray_outline_point(center, outline, angle)
        if point is not None:
            points.append(point)
    return points


def _poly_perimeter_mm(points: Sequence[Point]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        total += _distance(point, nxt)
    return total


def _outline_point_at_fraction(outline: Sequence[Point], fraction: float) -> Point | None:
    if len(outline) < 2:
        return None
    perimeter = _poly_perimeter_mm(outline)
    if perimeter <= 1.0e-9:
        return (float(outline[0][0]), float(outline[0][1]))
    target = (float(fraction) % 1.0) * perimeter
    walked = 0.0
    for index, point in enumerate(outline):
        nxt = outline[(index + 1) % len(outline)]
        length = _distance(point, nxt)
        if length <= 1.0e-9:
            continue
        if walked + length >= target:
            t = _clamp((target - walked) / length, 0.0, 1.0)
            return (
                float(point[0]) + (float(nxt[0]) - float(point[0])) * t,
                float(point[1]) + (float(nxt[1]) - float(point[1])) * t,
            )
        walked += length
    return (float(outline[-1][0]), float(outline[-1][1]))


def _focus_end_point(center: Point, inner_outline: Sequence[Point], start: Point) -> Point:
    angle = math.atan2(float(start[1]) - center[1], float(start[0]) - center[0])
    point = _ray_outline_point(center, inner_outline, angle)
    return point if point is not None else center


def _jag_end_point(center: Point, point: Point, index: int) -> Point:
    scale = 0.84 if index % 2 == 0 else 1.10
    return (
        center[0] + (float(point[0]) - center[0]) * scale,
        center[1] + (float(point[1]) - center[1]) * scale,
    )


def _ray_outline_point(center: Point, outline: Sequence[Point], angle: float) -> Point | None:
    if len(outline) < 2:
        return None
    cx, cy = center
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
    return cx + dx * max(0.0, best_t), cy + dy * max(0.0, best_t)


def _radial_metric_samples(center: Point, outline: Sequence[Point]) -> list[tuple[float, float]]:
    sample_count = 4096
    cx, cy = center
    out: list[tuple[float, float]] = []
    for index in range(sample_count):
        angle = (2.0 * math.pi) * index / sample_count
        point = _ray_outline_point(center, outline, angle)
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


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _distance(a: Point, b: Point) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _stable_seed(value: str) -> int:
    seed = 2166136261
    for char in str(value or ""):
        seed ^= ord(char)
        seed = (seed * 16777619) & 0xFFFFFFFF
    return seed
