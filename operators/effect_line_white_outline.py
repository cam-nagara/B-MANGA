"""白抜き線の放射状ストローク生成."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from ..utils.geom import mm_to_m


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _value_between_min_percent(base: float, min_percent: float, enabled: bool, rng: random.Random) -> float:
    base = max(0.0, float(base))
    if not enabled:
        return base
    lo = base * _clamp01(float(min_percent) / 100.0)
    return lo + (base - lo) * rng.random()


def _attenuated_length(base_length: float, offset_from_center: float, half_width: float, attenuation: float) -> float:
    norm = 0.0 if half_width <= 1.0e-6 else min(1.0, abs(float(offset_from_center)) / half_width)
    factor = 1.0 - (float(attenuation) / 100.0) * norm
    return max(0.0, float(base_length) * factor)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _point_at_length(
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    max_length: float,
) -> tuple[float, float]:
    length = _distance(start_xy, end_xy)
    if length <= 1.0e-6:
        return end_xy
    scale = min(1.0, max(0.0, float(max_length)) / length)
    return (
        float(start_xy[0]) + (float(end_xy[0]) - float(start_xy[0])) * scale,
        float(start_xy[1]) + (float(end_xy[1]) - float(start_xy[1])) * scale,
    )


def _band_specs(params, count: int, base_width: float, rng: random.Random) -> list[tuple[float, float]]:
    specs: list[tuple[float, float]] = []
    for _index in range(count):
        width = _value_between_min_percent(
            base_width,
            float(getattr(params, "white_outline_width_min_percent", 50.0)),
            bool(getattr(params, "white_outline_width_jitter_enabled", False)),
            rng,
        )
        length_scale = _value_between_min_percent(
            1.0,
            float(getattr(params, "white_outline_length_min_percent", 50.0)),
            bool(getattr(params, "white_outline_length_jitter_enabled", False)),
            rng,
        )
        specs.append((width, length_scale))
    return specs


def _edge_distances(
    width: float,
    brush_mm: float,
    gap_mm: float,
    *,
    auto_count: bool,
    count: int,
) -> list[tuple[float, float]]:
    if not auto_count:
        total = max(1, min(256, int(count)))
        step = max(0.01, float(brush_mm) + max(0.0, float(gap_mm)))
        denom = max(1, total - 1)
        return [(step * index, index / denom) for index in range(total)]
    width = max(0.0, float(width))
    if width <= 1.0e-6:
        return []
    step = max(float(brush_mm), float(brush_mm) + max(0.0, float(gap_mm)))
    total = max(1, min(256, int(math.floor(width / max(0.01, step))) + 1))
    if total <= 1:
        return [(width * 0.5, 0.0)]
    return [(width * (index / (total - 1)), index / (total - 1)) for index in range(total)]


def _line_offsets(
    width: float,
    brush_mm: float,
    gap_mm: float,
    *,
    auto_count: bool,
    count: int,
) -> list[float]:
    if not auto_count:
        total = max(1, min(512, int(count)))
        if total <= 1:
            return [0.0]
        step = max(0.01, float(brush_mm) + max(0.0, float(gap_mm)))
        start = -step * (total - 1) * 0.5
        return [start + step * index for index in range(total)]
    width = max(0.0, float(width))
    if width <= 1.0e-6:
        return [0.0]
    step = max(0.01, float(brush_mm) + max(0.0, float(gap_mm)))
    count = max(1, min(256, int(math.floor(width / step)) + 1))
    if count <= 1:
        return [0.0]
    start = -width * 0.5
    return [start + width * (index / (count - 1)) for index in range(count)]


def _black_offset_specs(
    *,
    direction: str,
    white_half: float,
    band_half: float,
    brush_mm: float,
    gap_mm: float,
    auto_count: bool,
    count: int,
) -> list[tuple[float, float]]:
    width = max(0.0, float(band_half) - float(white_half))
    distances = _edge_distances(
        width,
        brush_mm,
        gap_mm,
        auto_count=auto_count,
        count=count,
    )
    out: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    modes = []
    if direction in {"outside", "both"}:
        modes.append("outside")
    if direction in {"inside", "both"}:
        modes.append("inside")
    if not modes:
        modes.append("outside")
    for mode in modes:
        for distance, norm in distances:
            for sign in (1.0, -1.0):
                if mode == "inside":
                    offset = sign * max(0.0, float(white_half) - float(distance))
                else:
                    offset = sign * (float(white_half) + float(distance))
                key = (round(offset * 10000), round(norm * 10000))
                if key in seen:
                    continue
                seen.add(key)
                out.append((offset, norm))
    return out


def _offset_extent(offsets: Sequence[float], brush_mm: float) -> float:
    if not offsets:
        return 0.0
    return max(abs(float(offset)) for offset in offsets) + max(0.0, float(brush_mm)) * 0.5


def _line_points_for_offset(
    effect_line_gen,
    center_xy_mm: tuple[float, float],
    start_outline: Sequence[tuple[float, float]],
    end_outline: Sequence[tuple[float, float]],
    radius_x_mm: float,
    radius_y_mm: float,
    base_angle: float,
    offset_mm: float,
    start_extend_mm: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    base_start = effect_line_gen._point_on_outline_or_ellipse(
        center_xy_mm,
        start_outline,
        radius_x_mm * 2.0,
        radius_y_mm * 2.0,
        base_angle,
        extend_mm=start_extend_mm,
    )
    base_distance = max(1.0, _distance(center_xy_mm, base_start))
    angle = base_angle + float(offset_mm) / base_distance
    start_xy = effect_line_gen._point_on_outline_or_ellipse(
        center_xy_mm,
        start_outline,
        radius_x_mm * 2.0,
        radius_y_mm * 2.0,
        angle,
        extend_mm=start_extend_mm,
    )
    return start_xy, effect_line_gen._focus_end_point(center_xy_mm, end_outline, start_xy)


def _range_distance(length_mm: float, mode: str, percent: float, length_value: float) -> float:
    if str(mode or "percent") == "length":
        return max(0.0, min(float(length_mm), float(length_value)))
    return max(0.0, min(float(length_mm), float(length_mm) * _clamp01(float(percent) / 100.0)))


def _profile_factor(
    s_mm: float,
    length_mm: float,
    *,
    in_frac: float,
    out_frac: float,
    in_range_mm: float,
    out_range_mm: float,
) -> float:
    start_value = 1.0
    if in_range_mm > 1.0e-6 and s_mm < in_range_mm:
        start_value = in_frac + (1.0 - in_frac) * _clamp01(s_mm / in_range_mm)
    end_value = 1.0
    end_start = max(0.0, length_mm - out_range_mm)
    if out_range_mm > 1.0e-6 and s_mm > end_start:
        end_value = out_frac + (1.0 - out_frac) * _clamp01((length_mm - s_mm) / out_range_mm)
    return _clamp01(min(start_value, end_value))


def _profile_breakpoints(
    length_mm: float,
    *,
    in_frac: float,
    out_frac: float,
    in_range_mm: float,
    out_range_mm: float,
) -> list[float]:
    points = {0.0, float(length_mm)}
    if 0.0 < in_range_mm < length_mm:
        points.add(float(in_range_mm))
    end_start = max(0.0, length_mm - out_range_mm)
    if 0.0 < end_start < length_mm:
        points.add(float(end_start))
    overlap_start = max(0.0, end_start)
    overlap_end = min(float(length_mm), float(in_range_mm))
    if overlap_start < overlap_end:
        denom = 0.0
        rhs = out_frac - in_frac
        if in_range_mm > 1.0e-6:
            denom += (1.0 - in_frac) / in_range_mm
        if out_range_mm > 1.0e-6:
            denom += (1.0 - out_frac) / out_range_mm
            rhs += (1.0 - out_frac) * length_mm / out_range_mm
        if denom > 1.0e-9:
            cross = rhs / denom
            if overlap_start < cross < overlap_end:
                points.add(float(cross))
    return sorted(points)


def _profiled_line_points(
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    *,
    brush_mm: float,
    in_percent: float,
    out_percent: float,
    range_mode: str,
    in_range_percent: float,
    out_range_percent: float,
    in_range_mm: float,
    out_range_mm: float,
) -> tuple[list[tuple[float, float, float]], list[float]]:
    length = _distance(start_xy, end_xy)
    if length <= 1.0e-6:
        radius = mm_to_m(max(0.01, float(brush_mm)) / 2.0)
        point = (mm_to_m(start_xy[0]), mm_to_m(start_xy[1]), 0.0)
        return [point, point], [radius, radius]
    in_frac = _clamp01(float(in_percent) / 100.0)
    out_frac = _clamp01(float(out_percent) / 100.0)
    d_in = _range_distance(length, range_mode, in_range_percent, in_range_mm)
    d_out = _range_distance(length, range_mode, out_range_percent, out_range_mm)
    breakpoints = _profile_breakpoints(
        length,
        in_frac=in_frac,
        out_frac=out_frac,
        in_range_mm=d_in,
        out_range_mm=d_out,
    )
    base_radius = mm_to_m(max(0.01, float(brush_mm)) / 2.0)
    points: list[tuple[float, float, float]] = []
    radii: list[float] = []
    for distance_mm in breakpoints:
        t = _clamp01(distance_mm / length)
        x = float(start_xy[0]) + (float(end_xy[0]) - float(start_xy[0])) * t
        y = float(start_xy[1]) + (float(end_xy[1]) - float(start_xy[1])) * t
        points.append((mm_to_m(x), mm_to_m(y), 0.0))
        radii.append(
            base_radius
            * _profile_factor(
                distance_mm,
                length,
                in_frac=in_frac,
                out_frac=out_frac,
                in_range_mm=d_in,
                out_range_mm=d_out,
            )
        )
    return points, radii


def _append_line(
    out,
    stroke_cls,
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    *,
    offset_from_center: float,
    band_half: float,
    band_length: float,
    brush_mm: float,
    end_brush_mm: float,
    attenuation: float,
    role: str,
    in_percent: float | None = None,
    out_percent: float | None = None,
    range_mode: str = "percent",
    in_range_percent: float = 100.0,
    out_range_percent: float = 100.0,
    in_range_mm: float = 10.0,
    out_range_mm: float = 10.0,
) -> None:
    effective = _attenuated_length(min(float(band_length), _distance(start_xy, end_xy)), offset_from_center, band_half, attenuation)
    if effective <= 1.0e-6:
        return
    end_trimmed = _point_at_length(start_xy, end_xy, effective)
    radius = mm_to_m(max(0.01, float(brush_mm)) / 2.0)
    if in_percent is not None or out_percent is not None:
        points_xyz, radii = _profiled_line_points(
            start_xy,
            end_trimmed,
            brush_mm=brush_mm,
            in_percent=100.0 if in_percent is None else float(in_percent),
            out_percent=100.0 if out_percent is None else float(out_percent),
            range_mode=range_mode,
            in_range_percent=in_range_percent,
            out_range_percent=out_range_percent,
            in_range_mm=in_range_mm,
            out_range_mm=out_range_mm,
        )
    else:
        end_radius = mm_to_m(max(0.0, float(end_brush_mm)) / 2.0)
        radii = [radius, end_radius] if abs(end_radius - radius) > 1.0e-12 else None
        points_xyz = [
            (mm_to_m(start_xy[0]), mm_to_m(start_xy[1]), 0.0),
            (mm_to_m(end_trimmed[0]), mm_to_m(end_trimmed[1]), 0.0),
        ]
    out.append(
        stroke_cls(
            points_xyz=points_xyz,
            radius=radius,
            radii=radii,
            role=role,
        )
    )


def _append_band(
    out,
    stroke_cls,
    effect_line_gen,
    params,
    center_xy_mm: tuple[float, float],
    start_outline: Sequence[tuple[float, float]],
    end_outline: Sequence[tuple[float, float]],
    radius_x_mm: float,
    radius_y_mm: float,
    base_angle: float,
    start_extend_mm: float,
    *,
    band_width: float,
    band_length_scale: float,
    white_ratio: float,
    white_brush: float,
    black_brush: float,
    white_gap: float,
    black_gap: float,
) -> None:
    white_width = max(0.01, float(band_width) * _clamp01(white_ratio))
    white_offsets = _line_offsets(
        white_width,
        white_brush,
        white_gap,
        auto_count=bool(getattr(params, "white_outline_white_line_count_auto", True)),
        count=int(getattr(params, "white_outline_white_line_count", 24)),
    )
    white_half = max(white_width * 0.5, _offset_extent(white_offsets, white_brush))
    band_half = max(white_half + 0.005, float(band_width) * 0.5)
    for offset in white_offsets:
        start_xy, end_xy = _line_points_for_offset(
            effect_line_gen,
            center_xy_mm,
            start_outline,
            end_outline,
            radius_x_mm,
            radius_y_mm,
            base_angle,
            offset,
            start_extend_mm,
        )
        _append_line(
            out,
            stroke_cls,
            start_xy,
            end_xy,
            offset_from_center=abs(offset),
            band_half=white_half,
            band_length=_distance(start_xy, end_xy) * band_length_scale,
            brush_mm=white_brush,
            end_brush_mm=0.0,
            attenuation=float(getattr(params, "white_outline_white_attenuation", 0.0)),
            role="white_outline_white",
            in_percent=float(getattr(params, "white_outline_white_in_percent", 100.0)),
            out_percent=float(getattr(params, "white_outline_white_out_percent", 0.0)),
            range_mode=str(getattr(params, "white_outline_white_inout_range_mode", "percent") or "percent"),
            in_range_percent=float(getattr(params, "white_outline_white_in_range_percent", 100.0)),
            out_range_percent=float(getattr(params, "white_outline_white_out_range_percent", 100.0)),
            in_range_mm=float(getattr(params, "white_outline_white_in_range_mm", 10.0)),
            out_range_mm=float(getattr(params, "white_outline_white_out_range_mm", 10.0)),
        )
    black_specs = _black_offset_specs(
        direction=str(getattr(params, "white_outline_black_direction", "outside") or "outside"),
        white_half=white_half,
        band_half=band_half,
        brush_mm=black_brush,
        gap_mm=black_gap,
        auto_count=bool(getattr(params, "white_outline_black_line_count_auto", True)),
        count=int(getattr(params, "white_outline_black_line_count", 3)),
    )
    band_half = max(band_half, _offset_extent([offset for offset, _norm in black_specs], black_brush))
    black_width_far = max(0.0, float(getattr(params, "white_outline_black_width_scale_percent", 100.0)) / 100.0)
    black_length_near = max(0.0, float(getattr(params, "white_outline_black_length_scale_near_percent", 100.0)) / 100.0)
    black_length_far = max(0.0, float(getattr(params, "white_outline_black_length_scale_far_percent", 100.0)) / 100.0)
    for offset, norm in black_specs:
        start_xy, end_xy = _line_points_for_offset(
            effect_line_gen,
            center_xy_mm,
            start_outline,
            end_outline,
            radius_x_mm,
            radius_y_mm,
            base_angle,
            offset,
            start_extend_mm,
        )
        width_factor = max(0.0, 1.0 + (black_width_far - 1.0) * _clamp01(norm))
        length_factor = max(0.0, black_length_near + (black_length_far - black_length_near) * _clamp01(norm))
        effective_brush = max(0.01, black_brush * width_factor)
        _append_line(
            out,
            stroke_cls,
            start_xy,
            end_xy,
            offset_from_center=max(0.0, abs(offset) - white_half),
            band_half=max(0.005, band_half - white_half),
            band_length=_distance(start_xy, end_xy) * band_length_scale * length_factor,
            brush_mm=effective_brush,
            end_brush_mm=effective_brush,
            attenuation=float(getattr(params, "white_outline_black_attenuation", 0.0)),
            role="white_outline_black",
            in_percent=float(getattr(params, "white_outline_black_in_percent", 100.0)),
            out_percent=float(getattr(params, "white_outline_black_out_percent", 100.0)),
            range_mode=str(getattr(params, "white_outline_black_inout_range_mode", "percent") or "percent"),
            in_range_percent=float(getattr(params, "white_outline_black_in_range_percent", 100.0)),
            out_range_percent=float(getattr(params, "white_outline_black_out_range_percent", 100.0)),
            in_range_mm=float(getattr(params, "white_outline_black_in_range_mm", 10.0)),
            out_range_mm=float(getattr(params, "white_outline_black_out_range_mm", 10.0)),
        )


def generate_white_outline_strokes(
    effect_line_gen,
    params,
    center_xy_mm: tuple[float, float],
    radius_x_mm: float,
    radius_y_mm: float,
    seed: int = 0,
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
    end_center_xy_mm: tuple[float, float] | None = None,
):
    rng = random.Random(seed)
    count = max(1, min(500, int(getattr(params, "white_outline_count", 5))))
    base_width = max(0.01, float(getattr(params, "white_outline_width_mm", 10.0)))
    white_ratio = _clamp01(float(getattr(params, "white_outline_white_ratio_percent", 30.0)) / 100.0)
    white_brush = max(0.01, float(getattr(params, "white_outline_white_brush_mm", 0.3)))
    black_brush = max(0.01, float(getattr(params, "white_outline_black_brush_mm", 0.3)))
    white_gap = max(0.0, float(getattr(params, "white_outline_spacing_mm", 0.2)))
    black_gap = max(0.0, float(getattr(params, "white_outline_black_spacing_mm", white_gap)))
    shape_center_xy_mm = end_center_xy_mm if end_center_xy_mm is not None else center_xy_mm
    if start_outline_mm is None:
        start_rect = effect_line_gen._scaled_rect(shape_center_xy_mm[0], shape_center_xy_mm[1], radius_x_mm, radius_y_mm, 2.0)
        start_outline = effect_line_gen._shape_outline(params, "start", start_rect, shape_center_xy_mm, seed=seed + 11)
        start_extend = 0.0
    else:
        start_outline = [(float(x), float(y)) for x, y in start_outline_mm]
        start_extend = max(0.0, float(start_extend_mm))
    end_rect = effect_line_gen._scaled_rect(shape_center_xy_mm[0], shape_center_xy_mm[1], radius_x_mm, radius_y_mm, 1.0)
    end_outline = effect_line_gen._shape_outline(params, "end", end_rect, shape_center_xy_mm, seed=seed + 23)
    bands = _band_specs(params, count, base_width, rng)
    out = []

    base_angle = math.radians(float(getattr(params, "white_outline_angle_deg", 0.0)))
    for index, (band_width, band_length_scale) in enumerate(bands):
        angle = base_angle + (2.0 * math.pi * index) / max(1, count)
        _append_band(
            out,
            effect_line_gen.EffectLineStroke,
            effect_line_gen,
            params,
            center_xy_mm,
            start_outline,
            end_outline,
            radius_x_mm,
            radius_y_mm,
            angle,
            start_extend,
            band_width=band_width,
            band_length_scale=band_length_scale,
            white_ratio=white_ratio,
            white_brush=white_brush,
            black_brush=black_brush,
            white_gap=white_gap,
            black_gap=black_gap,
        )
    return out
