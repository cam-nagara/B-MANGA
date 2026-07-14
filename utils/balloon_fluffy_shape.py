"""Dedicated geometry helpers for the Meldex-compatible fluffy shape."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from . import balloon_shapes as shapes


BezierAnchor = shapes.BezierAnchor
Rect = shapes.Rect
_DynamicOpts = shapes._DynamicOpts
_base_outward_normal = shapes._base_outward_normal
_base_perimeter = shapes._base_perimeter
_base_position = shapes._base_position
_dynamic_base = shapes._dynamic_base
_height_factor_for_width = shapes._height_factor_for_width
_jitter_factor = shapes._jitter_factor
_sample_cubic = shapes._sample_cubic


def _fluffy_displacement_direction(
    angle: float, rx: float, ry: float, kind: str, radius: float
) -> tuple[float, float]:
    """矩形の角では放射方向を使い、法線の跳びによる交差を防ぐ。"""

    if kind == "rect":
        return math.cos(angle), math.sin(angle)
    return _base_outward_normal(angle, rx, ry, kind, radius)


def _fluffy_midpoint(
    a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _fluffy_lerp(
    a: tuple[float, float], b: tuple[float, float], t: float
) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def base_anchors(rect: Rect, opts: _DynamicOpts) -> list[BezierAnchor] | None:
    """v0.6.166 で承認された主山 2 点/山の曲線を作る."""

    base = _dynamic_base(rect.width, rect.height, opts, fluffy=True)
    if base is None:
        return None
    cx, cy, rx, ry, eff_h = base
    kind = getattr(opts, "base_kind", "ellipse")
    radius = getattr(opts, "base_corner_radius_mm", 0.0)
    perimeter = _base_perimeter(rx, ry, kind, radius)
    width_factor = _jitter_factor(opts.bump_w_jitter, opts.rng, min_factor=0.5)
    bump_count = max(6, round(perimeter / max(0.001, opts.bump_w * width_factor)))
    angle0 = -math.pi * 0.5 + opts.offset * 2.0 * math.pi
    widths = [
        _jitter_factor(opts.bump_w_jitter, opts.rng, min_factor=0.5)
        for _ in range(bump_count)
    ]
    heights = [
        _jitter_factor(opts.bump_h_jitter, opts.rng, min_factor=0.2)
        * _height_factor_for_width(widths[i])
        for i in range(bump_count)
    ]
    raw: list[tuple[float, float]] = []
    for i in range(max(8, bump_count * 2)):
        phase = (i / (bump_count * 2)) * 2.0 * math.pi
        t = angle0 + phase
        main_i = (
            int((phase % (2.0 * math.pi)) / (2.0 * math.pi) * bump_count)
            % bump_count
        )
        bx, by = _base_position(t, cx, cy, rx, ry, base_kind=kind, base_radius=radius)
        nx, ny = _fluffy_displacement_direction(t, rx, ry, kind, radius)
        wave = math.cos(bump_count * phase) * heights[main_i]
        raw.append((bx + eff_h * 0.5 * wave * nx, by + eff_h * 0.5 * wave * ny))
    anchors: list[BezierAnchor] = []
    for i, co in enumerate(raw):
        prev_pt, next_pt = raw[(i - 1) % len(raw)], raw[(i + 1) % len(raw)]
        tangent = ((next_pt[0] - prev_pt[0]) / 6.0, (next_pt[1] - prev_pt[1]) / 6.0)
        anchors.append(
            BezierAnchor(
                co,
                (co[0] - tangent[0], co[1] - tangent[1]),
                (co[0] + tangent[0], co[1] + tangent[1]),
            )
        )
    return anchors


def _fluffy_split_cubic(
    a: BezierAnchor, b: BezierAnchor
) -> tuple[tuple[float, float], ...]:
    """cubic を t=0.5 で正確に分割し、P0..P3 間の 7 点を返す."""

    p0, p1 = a.co, a.handle_right or a.co
    p2, p3 = b.handle_left or b.co, b.co
    p01 = _fluffy_midpoint(p0, p1)
    p12 = _fluffy_midpoint(p1, p2)
    p23 = _fluffy_midpoint(p2, p3)
    p012 = _fluffy_midpoint(p01, p12)
    p123 = _fluffy_midpoint(p12, p23)
    return p0, p01, p012, _fluffy_midpoint(p012, p123), p123, p23, p3


def _fluffy_exact_half_anchors(base: list[BezierAnchor]) -> list[BezierAnchor]:
    """各 cubic を正確に二分する（曲線自体は一切変えない）."""

    segments = [
        _fluffy_split_cubic(a, base[(i + 1) % len(base)])
        for i, a in enumerate(base)
    ]
    anchors: list[BezierAnchor] = []
    for i, segment in enumerate(segments):
        anchors.append(BezierAnchor(segment[0], segments[i - 1][5], segment[1]))
        anchors.append(BezierAnchor(segment[3], segment[2], segment[4]))
    return anchors


def _fluffy_split_controls(
    cubic: tuple[tuple[float, float], ...], t: float
) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]:
    p0, p1, p2, p3 = cubic
    p01 = _fluffy_lerp(p0, p1, t)
    p12 = _fluffy_lerp(p1, p2, t)
    p23 = _fluffy_lerp(p2, p3, t)
    p012, p123 = _fluffy_lerp(p01, p12, t), _fluffy_lerp(p12, p23, t)
    mid = _fluffy_lerp(p012, p123, t)
    return (p0, p01, p012, mid), (mid, p123, p23, p3)


def _fluffy_subdivide_controls(
    cubic: tuple[tuple[float, float], ...], cuts: Sequence[float]
) -> list[tuple[tuple[float, float], ...]]:
    spans: list[tuple[tuple[float, float], ...]] = []
    remaining, previous = cubic, 0.0
    for cut in cuts:
        relative = (cut - previous) / max(1.0e-12, 1.0 - previous)
        left, remaining = _fluffy_split_controls(remaining, relative)
        spans.append(left)
        previous = cut
    spans.append(remaining)
    return spans


def _fluffy_compact_profile(u: float) -> tuple[float, float]:
    """支持域[-1,1]のC2 cubic B-spline隆起とu微分を返す."""

    sign, q = (-1.0 if u < 0.0 else 1.0), 2.0 * abs(float(u))
    if q >= 2.0:
        return 0.0, 0.0
    if q >= 1.0:
        return ((2.0 - q) ** 3 * 0.25, sign * -1.5 * (2.0 - q) ** 2)
    value = 1.0 - 1.5 * q * q + 0.75 * q * q * q
    derivative = sign * 2.0 * (-3.0 * q + 2.25 * q * q)
    return value, derivative


def _fluffy_profile_controls(
    t0: float, t1: float, side: str, width: float
) -> tuple[float, ...]:
    origin = 1.0 if side == "incoming" else 0.0

    def sample(t: float) -> tuple[float, float]:
        value, derivative_u = _fluffy_compact_profile((t - origin) / width)
        return value, derivative_u / width

    v0, d0 = sample(t0)
    v1, d1 = sample(t1)
    span = t1 - t0
    return v0, v0 + d0 * span / 3.0, v1 - d1 * span / 3.0, v1


def _fluffy_valley_specs(
    rect: Rect, opts: _DynamicOpts, base: list[BezierAnchor]
) -> dict[int, tuple[tuple[float, float], float, float]]:
    dynamic = _dynamic_base(rect.width, rect.height, opts, fluffy=True)
    sub_enabled = opts.sub_w > 0.0 or opts.sub_h > 0.0
    if dynamic is None or not sub_enabled:
        return {}
    _cx, _cy, rx, ry, eff_h = dynamic
    base_width = opts.sub_w if opts.sub_w > 0.0 else 50.0
    base_height = opts.sub_h if opts.sub_h > 0.0 else 50.0
    specs: dict[int, tuple[tuple[float, float], float, float]] = {}
    angle0 = -math.pi * 0.5 + opts.offset * 2.0 * math.pi
    for index in range(1, len(base), 2):
        width_jitter = _jitter_factor(
            opts.sub_w_jitter, opts.rng, min_factor=0.5
        )
        height_jitter = _jitter_factor(
            opts.sub_h_jitter, opts.rng, min_factor=0.2
        )
        height_jitter *= _height_factor_for_width(width_jitter)
        actual_width = base_width * width_jitter
        support = max(0.16, min(0.72, actual_width / 100.0))
        height_delta = (base_height - 50.0) / 50.0
        height_gain = (
            height_delta * (0.30 if height_delta < 0.0 else 1.0) * height_jitter
        )
        width_gain = (actual_width - 30.0) / 70.0 * 0.08
        jitter_gain = (height_jitter - 1.0) * 0.25
        amplitude = eff_h * max(
            -0.30, min(0.78, height_gain + width_gain + jitter_gain)
        )
        angle = angle0 + (index / len(base)) * 2.0 * math.pi
        normal = _fluffy_displacement_direction(
            angle, rx, ry, opts.base_kind, opts.base_corner_radius_mm
        )
        specs[index] = (normal, support, amplitude)
    return specs


def _fluffy_perturb_span(
    cubic: tuple[tuple[float, float], ...],
    weights: Sequence[float],
    displacement: tuple[float, float],
) -> tuple[tuple[float, float], ...]:
    dx, dy = displacement
    return tuple(
        (point[0] + dx * weight, point[1] + dy * weight)
        for point, weight in zip(cubic, weights)
    )


def _fluffy_bump_cubics(
    base: list[BezierAnchor],
    specs: dict[int, tuple[tuple[float, float], float, float]],
    scale: float,
) -> list[tuple[tuple[float, float], ...]]:
    cubics: list[tuple[tuple[float, float], ...]] = []
    for index, anchor in enumerate(base):
        next_index = (index + 1) % len(base)
        valley, side = (
            (next_index, "incoming") if next_index % 2 else (index, "outgoing")
        )
        normal, width, amplitude = specs[valley]
        cuts = (
            (1.0 - width, 1.0 - width * 0.5)
            if side == "incoming"
            else (width * 0.5, width)
        )
        bounds = (0.0, *cuts, 1.0)
        raw = (
            anchor.co,
            anchor.handle_right or anchor.co,
            base[next_index].handle_left or base[next_index].co,
            base[next_index].co,
        )
        displacement = (
            normal[0] * amplitude * scale,
            normal[1] * amplitude * scale,
        )
        for span, t0, t1 in zip(
            _fluffy_subdivide_controls(raw, cuts), bounds, bounds[1:]
        ):
            weights = _fluffy_profile_controls(t0, t1, side, width)
            cubics.append(_fluffy_perturb_span(span, weights, displacement))
    return cubics


def _fluffy_anchors_from_cubics(
    cubics: Sequence[tuple[tuple[float, float], ...]],
) -> list[BezierAnchor]:
    return [
        BezierAnchor(cubic[0], cubics[i - 1][2], cubic[1])
        for i, cubic in enumerate(cubics)
    ]


def _fluffy_sample_anchors(
    anchors: Sequence[BezierAnchor], steps: int = 7
) -> list[tuple[float, float]]:
    pts = [anchors[0].co]
    for i, anchor in enumerate(anchors):
        other = anchors[(i + 1) % len(anchors)]
        pts.extend(
            _sample_cubic(
                anchor.co,
                anchor.handle_right or anchor.co,
                other.handle_left or other.co,
                other.co,
                steps=steps,
            )
        )
    return pts


def _fluffy_segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (
            r[0] - p[0]
        )

    def on_segment(p, q, r) -> bool:
        epsilon = 1.0e-10
        return (
            min(p[0], r[0]) - epsilon <= q[0] <= max(p[0], r[0]) + epsilon
            and min(p[1], r[1]) - epsilon
            <= q[1]
            <= max(p[1], r[1]) + epsilon
        )

    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(
        a[0], b[0]
    ):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(
        a[1], b[1]
    ):
        return False
    ab_c = orient(a, b, c)
    ab_d = orient(a, b, d)
    cd_a = orient(c, d, a)
    cd_b = orient(c, d, b)
    epsilon = 1.0e-10
    if (
        (ab_c > epsilon and ab_d < -epsilon)
        or (ab_c < -epsilon and ab_d > epsilon)
    ) and (
        (cd_a > epsilon and cd_b < -epsilon)
        or (cd_a < -epsilon and cd_b > epsilon)
    ):
        return True
    return (
        (abs(ab_c) <= epsilon and on_segment(a, c, b))
        or (abs(ab_d) <= epsilon and on_segment(a, d, b))
        or (abs(cd_a) <= epsilon and on_segment(c, a, d))
        or (abs(cd_b) <= epsilon and on_segment(c, b, d))
    )


def _fluffy_polyline_is_simple(points: Sequence[tuple[float, float]]) -> bool:
    count = len(points)
    span = max(
        max(x for x, _y in points) - min(x for x, _y in points),
        max(y for _x, y in points) - min(y for _x, y in points),
    )
    cell_size = max(1.0e-6, span / max(4.0, math.sqrt(count)))
    cells: dict[tuple[int, int], list[int]] = {}
    checked: set[tuple[int, int]] = set()
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        x0 = math.floor(min(a[0], b[0]) / cell_size)
        x1 = math.floor(max(a[0], b[0]) / cell_size)
        y0 = math.floor(min(a[1], b[1]) / cell_size)
        y1 = math.floor(max(a[1], b[1]) / cell_size)
        keys = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        for key in keys:
            for j in cells.get(key, ()):
                pair = (j, i)
                if pair in checked or i - j <= 1 or (j == 0 and i == count - 1):
                    continue
                checked.add(pair)
                if _fluffy_segments_cross(
                    points[j], points[(j + 1) % count], a, b
                ):
                    return False
        for key in keys:
            cells.setdefault(key, []).append(i)
    return True


def _fluffy_radial_ordered(
    points: Sequence[tuple[float, float]], center: tuple[float, float]
) -> bool:
    angles = [math.atan2(y - center[1], x - center[0]) for x, y in points]
    deltas: list[float] = []
    for angle, following in zip(angles, angles[1:] + angles[:1]):
        delta = (following - angle + math.pi) % (2.0 * math.pi) - math.pi
        deltas.append(delta)
    epsilon = 1.0e-10
    same_direction = all(delta > epsilon for delta in deltas) or all(
        delta < -epsilon for delta in deltas
    )
    return (
        same_direction
        and abs(abs(sum(deltas)) - 2.0 * math.pi) <= 1.0e-6
    )


def _fluffy_curve_is_safe(
    anchors: Sequence[BezierAnchor], center: tuple[float, float]
) -> bool:
    # 実際の効果線輪郭と同じ密度で、細いループも取りこぼさない。
    points = _fluffy_sample_anchors(anchors, steps=10)
    if len(points) > 1 and math.dist(points[0], points[-1]) <= 1.0e-10:
        points.pop()
    if any(not (math.isfinite(x) and math.isfinite(y)) for x, y in points):
        return False
    area = sum(
        a[0] * b[1] - a[1] * b[0]
        for a, b in zip(points, points[1:] + points[:1])
    )
    if abs(area) <= 1.0e-6:
        return False
    if _fluffy_radial_ordered(points, center):
        return True
    return _fluffy_polyline_is_simple(points)


def _fluffy_scale_handles(
    anchors: Sequence[BezierAnchor], scale: float
) -> list[BezierAnchor]:
    scaled: list[BezierAnchor] = []
    for anchor in anchors:
        left = _fluffy_lerp(anchor.co, anchor.handle_left or anchor.co, scale)
        right = _fluffy_lerp(anchor.co, anchor.handle_right or anchor.co, scale)
        scaled.append(
            BezierAnchor(
                anchor.co,
                left,
                right,
                anchor.handle_left_type,
                anchor.handle_right_type,
            )
        )
    return scaled


def _fluffy_make_safe(
    anchors: list[BezierAnchor], center: tuple[float, float]
) -> list[BezierAnchor] | None:
    if _fluffy_curve_is_safe(anchors, center):
        return anchors
    for handle_scale in (0.75, 0.5, 0.25, 0.0):
        candidate = _fluffy_scale_handles(anchors, handle_scale)
        if _fluffy_curve_is_safe(candidate, center):
            return candidate
    return None


def _fluffy_clone_opts(
    opts: _DynamicOpts, rng_state: object, *, base_kind: str, base_radius: float
) -> _DynamicOpts:
    rng = random.Random()
    rng.setstate(rng_state)
    return _DynamicOpts(
        bump_w=opts.bump_w,
        bump_w_jitter=opts.bump_w_jitter,
        bump_h=opts.bump_h,
        bump_h_jitter=opts.bump_h_jitter,
        offset=opts.offset,
        sub_w=opts.sub_w,
        sub_w_jitter=opts.sub_w_jitter,
        sub_h=opts.sub_h,
        sub_h_jitter=opts.sub_h_jitter,
        rng=rng,
        base_kind=base_kind,
        base_corner_radius_mm=base_radius,
    )


def _fluffy_ellipse_fallback(
    rect: Rect, opts: _DynamicOpts, rng_state: object
) -> list[BezierAnchor] | None:
    fallback = _fluffy_clone_opts(
        opts, rng_state, base_kind="ellipse", base_radius=0.0
    )
    return local_anchors(rect, fallback, allow_rect_radius_fallback=False)


def _fluffy_rect_radius_fallback(
    rect: Rect, opts: _DynamicOpts, rng_state: object
) -> list[BezierAnchor] | None:
    dynamic = _dynamic_base(rect.width, rect.height, opts, fluffy=True)
    if dynamic is None:
        return _fluffy_ellipse_fallback(rect, opts, rng_state)
    radius_limit = min(dynamic[2], dynamic[3])
    requested = min(max(0.0, opts.base_corner_radius_mm), radius_limit)
    for factor in (0.5, 0.65, 0.8, 1.0):
        radius = requested + (radius_limit - requested) * factor
        candidate = _fluffy_clone_opts(
            opts, rng_state, base_kind="rect", base_radius=radius
        )
        anchors = local_anchors(
            rect, candidate, allow_rect_radius_fallback=False
        )
        if anchors is not None:
            return anchors
    return _fluffy_ellipse_fallback(rect, opts, rng_state)


def local_anchors(
    rect: Rect,
    opts: _DynamicOpts,
    *,
    allow_rect_radius_fallback: bool = True,
) -> list[BezierAnchor] | None:
    """承認曲線を基底に、谷間へ安全な低い小山を加える."""

    rng_state = opts.rng.getstate()
    base = base_anchors(rect, opts)
    if not base:
        return None
    center = (rect.width * 0.5, rect.height * 0.5)
    exact = _fluffy_exact_half_anchors(base)
    if opts.base_kind == "rect":
        safe_exact = _fluffy_make_safe(exact, center)
        if safe_exact is None:
            if allow_rect_radius_fallback:
                return _fluffy_rect_radius_fallback(rect, opts, rng_state)
            return None
    else:
        safe_exact = _fluffy_make_safe(exact, center)
    specs = _fluffy_valley_specs(rect, opts, base)
    if not specs or all(
        abs(spec[2]) <= 1.0e-12 for spec in specs.values()
    ):
        return safe_exact
    for scale in (1.0, 0.5, 0.25, 0.125, 0.0625):
        anchors = _fluffy_anchors_from_cubics(
            _fluffy_bump_cubics(base, specs, scale)
        )
        safe = _fluffy_make_safe(anchors, center)
        if safe is not None:
            return safe
    if opts.base_kind == "rect":
        if allow_rect_radius_fallback:
            return _fluffy_rect_radius_fallback(rect, opts, rng_state)
        return None
    return safe_exact
