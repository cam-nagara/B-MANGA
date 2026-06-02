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


def _offset_point(point: tuple[float, float], normal: tuple[float, float], offset: float) -> tuple[float, float]:
    return float(point[0]) + normal[0] * offset, float(point[1]) + normal[1] * offset


def _midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (float(a[0]) + float(b[0])) * 0.5, (float(a[1]) + float(b[1])) * 0.5


def _band_specs(params, count: int, base_width: float, base_length: float, rng: random.Random) -> list[tuple[float, float]]:
    specs: list[tuple[float, float]] = []
    for _index in range(count):
        width = _value_between_min_percent(
            base_width,
            float(getattr(params, "white_outline_width_min_percent", 50.0)),
            bool(getattr(params, "white_outline_width_jitter_enabled", False)),
            rng,
        )
        length = _value_between_min_percent(
            base_length,
            float(getattr(params, "white_outline_length_min_percent", 50.0)),
            bool(getattr(params, "white_outline_length_jitter_enabled", False)),
            rng,
        )
        specs.append((width, length))
    return specs


def _side_offsets(
    *,
    sign: float,
    white_half: float,
    band_half: float,
    brush_mm: float,
    gap_mm: float,
) -> list[float]:
    width = max(0.0, float(band_half) - float(white_half))
    if width <= 1.0e-6:
        return []
    step = max(float(brush_mm), float(brush_mm) + max(0.0, float(gap_mm)))
    count = max(1, min(256, int(math.floor(width / max(0.01, step))) + 1))
    if count <= 1:
        return [sign * (white_half + width * 0.5)]
    return [sign * (white_half + width * (index / (count - 1))) for index in range(count)]


def _append_tapered_line(
    out,
    stroke_cls,
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    normal: tuple[float, float],
    *,
    start_offset: float,
    end_offset: float,
    band_offset: float,
    band_half: float,
    band_length: float,
    brush_mm: float,
    attenuation: float,
) -> None:
    dx = float(end_xy[0]) - float(start_xy[0])
    dy = float(end_xy[1]) - float(start_xy[1])
    length = math.hypot(dx, dy)
    if length <= 1.0e-6:
        return
    effective = _attenuated_length(min(float(band_length), length), band_offset, band_half, attenuation)
    if effective <= 1.0e-6:
        return
    scale = min(1.0, effective / length)
    end_base = (float(start_xy[0]) + dx * scale, float(start_xy[1]) + dy * scale)
    p0 = _offset_point(start_xy, normal, start_offset)
    p1 = _offset_point(end_base, normal, end_offset)
    out.append(
        stroke_cls(
            points_xyz=[(mm_to_m(p0[0]), mm_to_m(p0[1]), 0.0), (mm_to_m(p1[0]), mm_to_m(p1[1]), 0.0)],
            radius=mm_to_m(max(0.01, float(brush_mm)) / 2.0),
            role="white_outline_black",
        )
    )


def _append_white_fill(
    out,
    stroke_cls,
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    normal: tuple[float, float],
    *,
    band_width: float,
    band_length: float,
    white_ratio: float,
    tip_width_mm: float,
    attenuation: float,
) -> tuple[float, float]:
    white_width = max(0.01, float(band_width) * _clamp01(white_ratio))
    start_half = white_width * 0.5
    tip_half = min(start_half, max(0.005, float(tip_width_mm) * 0.5))
    dx = float(end_xy[0]) - float(start_xy[0])
    dy = float(end_xy[1]) - float(start_xy[1])
    length = math.hypot(dx, dy)
    if length <= 1.0e-6:
        return start_half, tip_half
    effective = _attenuated_length(min(float(band_length), length), 0.0, max(0.005, float(band_width) * 0.5), attenuation)
    if effective <= 1.0e-6:
        return start_half, tip_half
    scale = min(1.0, effective / length)
    end_base = (float(start_xy[0]) + dx * scale, float(start_xy[1]) + dy * scale)
    points = [
        _offset_point(start_xy, normal, start_half),
        _offset_point(end_base, normal, tip_half),
        _offset_point(end_base, normal, -tip_half),
        _offset_point(start_xy, normal, -start_half),
    ]
    out.append(
        stroke_cls(
            points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in points],
            radius=mm_to_m(0.01),
            cyclic=True,
            role="white_outline_white",
        )
    )
    return start_half, tip_half


def _append_band(
    out,
    stroke_cls,
    params,
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    normal: tuple[float, float],
    *,
    band_width: float,
    band_length: float,
    white_ratio: float,
    white_tip_width: float,
    black_brush: float,
    black_gap: float,
) -> None:
    white_half, tip_half = _append_white_fill(
        out,
        stroke_cls,
        start_xy,
        end_xy,
        normal,
        band_width=band_width,
        band_length=band_length,
        white_ratio=white_ratio,
        tip_width_mm=white_tip_width,
        attenuation=float(getattr(params, "white_outline_white_attenuation", 0.0)),
    )
    band_half = max(0.005, float(band_width) * 0.5)
    tip_scale = 0.0 if white_half <= 1.0e-6 else min(1.0, tip_half / white_half)
    for sign in (1.0, -1.0):
        for offset in _side_offsets(
            sign=sign,
            white_half=white_half,
            band_half=band_half,
            brush_mm=black_brush,
            gap_mm=black_gap,
        ):
            _append_tapered_line(
                out,
                stroke_cls,
                start_xy,
                end_xy,
                normal,
                start_offset=offset,
                end_offset=offset * tip_scale,
                band_offset=abs(offset) - white_half,
                band_half=max(0.005, band_half - white_half),
                band_length=band_length,
                brush_mm=black_brush,
                attenuation=float(getattr(params, "white_outline_black_attenuation", 0.0)),
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
    white_tip_width = max(0.01, float(getattr(params, "white_outline_white_brush_mm", 0.3)))
    black_brush = max(0.01, float(getattr(params, "white_outline_black_brush_mm", 0.3)))
    black_gap = max(0.0, float(getattr(params, "white_outline_spacing_mm", 0.2)))
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
    base_length = max(0.1, math.hypot(float(radius_x_mm) * 2.0, float(radius_y_mm) * 2.0))
    bands = _band_specs(params, count, base_width, base_length, rng)
    out = []

    base_angle = math.radians(float(getattr(params, "white_outline_angle_deg", 0.0)))
    for index, (band_width, band_length) in enumerate(bands):
        angle = base_angle + (2.0 * math.pi * index) / max(1, count)
        start_xy = effect_line_gen._point_on_outline_or_ellipse(
            center_xy_mm,
            start_outline,
            radius_x_mm * 2.0,
            radius_y_mm * 2.0,
            angle,
            extend_mm=start_extend,
        )
        end_xy = effect_line_gen._focus_end_point(center_xy_mm, end_outline, start_xy)
        dx = float(end_xy[0]) - float(start_xy[0])
        dy = float(end_xy[1]) - float(start_xy[1])
        length = math.hypot(dx, dy)
        if length <= 1.0e-6:
            continue
        normal = (-dy / length, dx / length)
        _append_band(
            out,
            effect_line_gen.EffectLineStroke,
            params,
            start_xy,
            end_xy,
            normal,
            band_width=band_width,
            band_length=band_length,
            white_ratio=white_ratio,
            white_tip_width=white_tip_width,
            black_brush=black_brush,
            black_gap=black_gap,
        )
    return out
