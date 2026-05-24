"""効果線ストローク生成ロジック (計画書 3.1.6).

BNameEffectLineParams を受け取り、放射状 / 流線の頂点列を算出する。
Grease Pencil v3 への書き込みは utils/gpencil.py を経由する。

このモジュールは純粋計算 (点列生成) と GP 統合を担う。Operator は
operators/effect_line_op.py から呼ぶ。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from ..utils import balloon_shapes, effect_inout_curve, log
from ..utils.geom import Rect, mm_to_m
from . import effect_line_radial_spacing
_logger = log.get_logger(__name__)
_DEFAULT_IN_START_PERCENT = 0.0
_DEFAULT_OUT_START_PERCENT = 100.0


@dataclass(frozen=True)
class EffectLineStroke:
    points_xyz: list[tuple[float, float, float]]
    radius: float  # m 単位
    cyclic: bool = False
    radii: list[float] | None = None
    opacities: list[float] | None = None
    role: str = "line"
    curve_type: str = "POLY"
    bezier_smooth: bool = False
    density_end: float = 1.0

def _jitter(base: float, amount: float, rng: random.Random) -> float:
    if amount <= 0.0:
        return base
    delta = base * amount * (rng.random() * 2.0 - 1.0)
    return base + delta


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _max_line_count(params) -> int:
    return max(1, int(getattr(params, "max_line_count", 1000)))


def _ellipse_perimeter_mm(rx: float, ry: float) -> float:
    a = max(0.001, abs(float(rx)))
    b = max(0.001, abs(float(ry)))
    h = ((a - b) ** 2) / ((a + b) ** 2)
    return math.pi * (a + b) * (1.0 + (3.0 * h) / (10.0 + math.sqrt(4.0 - 3.0 * h)))


def _poly_perimeter_mm(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i, point in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        total += math.hypot(nxt[0] - point[0], nxt[1] - point[1])
    return total


def _outline_point_at_fraction(
    outline: Sequence[tuple[float, float]],
    fraction: float,
) -> tuple[float, float] | None:
    if len(outline) < 2:
        return None
    perimeter = _poly_perimeter_mm(outline)
    if perimeter <= 1.0e-9:
        return (float(outline[0][0]), float(outline[0][1]))
    target = (float(fraction) % 1.0) * perimeter
    walked = 0.0
    for i, point in enumerate(outline):
        nxt = outline[(i + 1) % len(outline)]
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


def _extend_point_from_center(
    center_xy_mm: tuple[float, float],
    point_xy_mm: tuple[float, float],
    extend_mm: float,
) -> tuple[float, float]:
    if extend_mm <= 0.0:
        return point_xy_mm
    cx, cy = center_xy_mm
    dx = float(point_xy_mm[0]) - cx
    dy = float(point_xy_mm[1]) - cy
    length = math.hypot(dx, dy)
    if length <= 1.0e-9:
        return point_xy_mm
    scale = (length + float(extend_mm)) / length
    return cx + dx * scale, cy + dy * scale


def _scaled_rect(cx: float, cy: float, rx: float, ry: float, scale: float) -> Rect:
    sx = max(0.001, float(rx) * float(scale))
    sy = max(0.001, float(ry) * float(scale))
    return Rect(cx - sx, cy - sy, sx * 2.0, sy * 2.0)


def _rotate_points(
    points: Sequence[tuple[float, float]],
    center: tuple[float, float],
    angle_deg: float,
) -> list[tuple[float, float]]:
    angle = math.radians(float(angle_deg))
    if abs(angle) < 1.0e-9:
        return [(float(x), float(y)) for x, y in points]
    cx, cy = center
    ca = math.cos(angle)
    sa = math.sin(angle)
    out: list[tuple[float, float]] = []
    for x, y in points:
        dx = float(x) - cx
        dy = float(y) - cy
        out.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return out


def _shape_outline(
    params,
    prefix: str,
    rect: Rect,
    center_xy_mm: tuple[float, float],
    *,
    seed: int = 0,
) -> list[tuple[float, float]]:
    shape = getattr(params, f"{prefix}_shape", getattr(params, "base_shape", "rect"))
    if shape == "polygon":
        shape = "octagon"
    points = balloon_shapes.outline_for_shape(
        shape,
        rect,
        rounded_corner_enabled=bool(getattr(params, f"{prefix}_rounded_corner_enabled", False)),
        rounded_corner_radius_mm=float(getattr(params, f"{prefix}_rounded_corner_radius_mm", 0.0)),
        cloud_bump_width_mm=float(getattr(params, f"{prefix}_cloud_bump_width_mm", 10.0)),
        cloud_bump_width_jitter=float(getattr(params, f"{prefix}_cloud_bump_width_jitter", 0.0)),
        cloud_bump_height_mm=float(getattr(params, f"{prefix}_cloud_bump_height_mm", 4.0)),
        cloud_bump_height_jitter=float(getattr(params, f"{prefix}_cloud_bump_height_jitter", 0.0)),
        cloud_offset=float(getattr(params, f"{prefix}_cloud_offset_percent", 50.0)) / 100.0,
        cloud_sub_width_ratio=float(getattr(params, f"{prefix}_cloud_sub_width_ratio", 0.0)),
        cloud_sub_width_jitter=float(getattr(params, f"{prefix}_cloud_sub_width_jitter", 0.0)),
        cloud_sub_height_ratio=float(getattr(params, f"{prefix}_cloud_sub_height_ratio", 0.0)),
        cloud_sub_height_jitter=float(getattr(params, f"{prefix}_cloud_sub_height_jitter", 0.0)),
        jitter_seed=int(seed),
    )
    return _rotate_points(points, center_xy_mm, getattr(params, "rotation_deg", 0.0))


def _jitter_trim_fraction(params, enabled_attr: str, amount_attr: str, rng: random.Random) -> float:
    if not bool(getattr(params, enabled_attr, False)):
        return 0.0
    amount = _clamp01(float(getattr(params, amount_attr, 0.0)) / 100.0)
    if amount <= 0.0:
        return 0.0
    return amount * rng.random()


def _trimmed_segment_points(
    params,
    rng: random.Random,
    start_xy_mm: tuple[float, float],
    end_xy_mm: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    sx, sy = start_xy_mm
    ex, ey = end_xy_mm
    start_trim = _jitter_trim_fraction(params, "length_jitter_enabled", "length_jitter_amount", rng)
    end_trim = _jitter_trim_fraction(params, "end_length_jitter_enabled", "end_length_jitter_amount", rng)
    total_trim = start_trim + end_trim
    if total_trim > 1.0:
        scale = 1.0 / max(total_trim, 1.0e-9)
        start_trim *= scale
        end_trim *= scale
    dx = ex - sx
    dy = ey - sy
    return (
        (sx + dx * start_trim, sy + dy * start_trim),
        (sx + dx * (1.0 - end_trim), sy + dy * (1.0 - end_trim)),
    )


def _shape_guide_uses_smooth_bezier(params, prefix: str, *, frame_outline: bool = False) -> bool:
    if frame_outline:
        return False
    shape = str(getattr(params, f"{prefix}_shape", getattr(params, "base_shape", "rect")) or "rect")
    if shape in {"polygon", "octagon", "diamond", "hexagon", "star", "thorn", "spike_straight"}:
        return False
    if shape == "rect":
        return bool(getattr(params, f"{prefix}_rounded_corner_enabled", False)) and (
            float(getattr(params, f"{prefix}_rounded_corner_radius_mm", 0.0)) > 0.0
        )
    return shape in {"ellipse", "cloud", "fluffy", "thorn-curve", "spike_curve", "pill"}


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
    for i, a in enumerate(outline):
        b = outline[(i + 1) % len(outline)]
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


def _point_on_outline_or_ellipse(
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    rx: float,
    ry: float,
    angle: float,
    *,
    extend_mm: float = 0.0,
) -> tuple[float, float]:
    point = _ray_outline_point(center_xy_mm, outline, angle, extend_mm=extend_mm)
    if point is not None:
        return point
    cx, cy = center_xy_mm
    return (
        cx + math.cos(angle) * (float(rx) + float(extend_mm)),
        cy + math.sin(angle) * (float(ry) + float(extend_mm)),
    )


def _actual_outline_by_rays(
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    *,
    extend_mm: float = 0.0,
    samples: int = 128,
) -> list[tuple[float, float]]:
    if len(outline) < 3:
        return [(float(x), float(y)) for x, y in outline]
    out: list[tuple[float, float]] = []
    for i in range(max(12, int(samples))):
        angle = 2.0 * math.pi * i / max(12, int(samples))
        point = _ray_outline_point(center_xy_mm, outline, angle, extend_mm=extend_mm)
        if point is not None:
            out.append(point)
    return out


def _focus_slot_count(params, radius_x_mm: float, radius_y_mm: float) -> int:
    if params.spacing_mode == "angle":
        step_deg = max(0.1, float(params.spacing_angle_deg))
        raw_count = max(4, int(round(360.0 / step_deg)))
    else:
        step_mm = max(0.01, float(params.spacing_distance_mm))
        raw_count = max(8, int(round(_ellipse_perimeter_mm(radius_x_mm, radius_y_mm) / step_mm)))
    return min(raw_count, _max_line_count(params))


def _focus_slot_count_for_outline(
    params,
    outline: Sequence[tuple[float, float]],
    radius_x_mm: float,
    radius_y_mm: float,
) -> int:
    if params.spacing_mode == "angle":
        return _focus_slot_count(params, radius_x_mm, radius_y_mm)
    step_mm = max(0.01, float(params.spacing_distance_mm))
    perimeter = _poly_perimeter_mm(outline) or _ellipse_perimeter_mm(radius_x_mm, radius_y_mm)
    raw_count = max(8, int(round(perimeter / step_mm)))
    return min(raw_count, _max_line_count(params))


def _spacing_density_compensation_enabled(params) -> bool:
    return str(getattr(params, "spacing_mode", "") or "") == "distance"


def _bundle_gap_slots(params) -> int:
    if not bool(getattr(params, "bundle_enabled", False)):
        return 0
    gap = max(0.0, float(getattr(params, "bundle_gap_mm", 0.0)))
    if params.spacing_mode == "angle":
        unit = max(0.1, float(params.spacing_angle_deg))
    else:
        unit = max(0.01, float(params.spacing_distance_mm))
    return max(1, int(round(gap / unit))) if gap > 0.0 else 1


def _slot_positions(count: int, params, rng: random.Random) -> list[float]:
    count = max(1, int(count))
    if not bool(getattr(params, "bundle_enabled", False)):
        return [float(i) for i in range(count)]
    base_bundle_size = max(1, int(getattr(params, "bundle_line_count", 5)))
    base_gap_slots = _bundle_gap_slots(params)
    count_jitter = _clamp01(getattr(params, "bundle_line_count_jitter", 0.0))
    gap_jitter = _clamp01(getattr(params, "bundle_gap_jitter_amount", 0.0))
    out: list[float] = []
    slot = 0
    while slot < count:
        bundle_size = base_bundle_size
        if count_jitter > 0.0:
            factor = 1.0 + (rng.random() * 2.0 - 1.0) * count_jitter
            bundle_size = max(1, int(round(base_bundle_size * factor)))
        gap_slots = base_gap_slots
        if gap_jitter > 0.0 and base_gap_slots > 0:
            factor = 1.0 + (rng.random() * 2.0 - 1.0) * gap_jitter
            gap_slots = max(0, int(round(base_gap_slots * factor)))
        for i in range(bundle_size):
            pos = float(slot + i)
            if pos >= count:
                break
            out.append(pos)
        slot += bundle_size + gap_slots
    return out


def _slot_fraction(slot: float, count: int, closed: bool) -> float:
    if count <= 1:
        return 0.5
    if closed:
        return (float(slot) % count) / float(count)
    return max(0.0, min(1.0, float(slot) / float(count - 1)))


def _append_focus_stroke(
    out: list[EffectLineStroke],
    params,
    rng: random.Random,
    start_xy_mm: tuple[float, float],
    end_xy_mm: tuple[float, float],
) -> None:
    radius_mm = _jitter(
        params.brush_size_mm,
        params.brush_jitter_amount if params.brush_jitter_enabled else 0.0,
        rng,
    )
    radius, radii = _stroke_radii(params, radius_mm, 2)
    opacities = _stroke_opacities(params, 2)
    (x0, y0), (x1, y1) = _trimmed_segment_points(params, rng, start_xy_mm, end_xy_mm)
    out.append(
        EffectLineStroke(
            points_xyz=[
                (mm_to_m(x0), mm_to_m(y0), 0.0),
                (mm_to_m(x1), mm_to_m(y1), 0.0),
            ],
            radius=radius,
            radii=radii,
            opacities=opacities,
        )
    )


def _focus_end_point(
    center_xy_mm: tuple[float, float],
    end_outline: Sequence[tuple[float, float]],
    start_xy_mm: tuple[float, float],
) -> tuple[float, float]:
    cx, cy = center_xy_mm
    angle = math.atan2(float(start_xy_mm[1]) - cy, float(start_xy_mm[0]) - cx)
    point = _ray_outline_point(center_xy_mm, end_outline, angle)
    return point if point is not None else center_xy_mm


def _stroke_radii(params, radius_mm: float, point_count: int = 2) -> tuple[float, list[float] | None]:
    base = mm_to_m(max(0.01, radius_mm) / 2.0)
    if getattr(params, "inout_apply", "brush_size") != "brush_size" or point_count < 2:
        return base, None
    start = base * (_clamp01(float(getattr(params, "in_percent", 100.0)) / 100.0))
    end = base * (_clamp01(float(getattr(params, "out_percent", 0.0)) / 100.0))
    if point_count == 2:
        return base, [start, end]
    radii = []
    for i in range(point_count):
        t = i / max(1, point_count - 1)
        radii.append(start + (end - start) * t)
    return base, radii


def _stroke_opacities(params, point_count: int = 2) -> list[float] | None:
    if getattr(params, "inout_apply", "brush_size") != "opacity" or point_count < 2:
        return None
    start = _clamp01(float(getattr(params, "in_percent", 100.0)) / 100.0)
    end = _clamp01(float(getattr(params, "out_percent", 0.0)) / 100.0)
    if point_count == 2:
        return [start, end]
    opacities = []
    for i in range(point_count):
        t = i / max(1, point_count - 1)
        opacities.append(start + (end - start) * t)
    return opacities


def _inout_profile(params, length_m: float):
    """入り抜きのプロファイル関数 p(s) を返す (s: 始点からの距離 m, 戻り 0..1).

    入り = 始点から ``D_in`` の区間で in_percent → 100% へ。
    抜き = 終点から ``D_out`` の区間で 100% → out_percent へ。
    両者の min を取る。範囲が 100%(percent) のときは従来の線形変化と一致。
    """
    in_frac = _clamp01(float(getattr(params, "in_percent", 100.0)) / 100.0)
    out_frac = _clamp01(float(getattr(params, "out_percent", 0.0)) / 100.0)
    mode = str(getattr(params, "inout_range_mode", "percent") or "percent")
    has_new_start_controls = hasattr(params, "in_start_percent") or hasattr(params, "out_start_percent")
    if has_new_start_controls:
        d_in = _clamp01(float(getattr(params, "in_start_percent", _DEFAULT_IN_START_PERCENT)) / 100.0) * length_m
        d_out = _clamp01(float(getattr(params, "out_start_percent", _DEFAULT_OUT_START_PERCENT)) / 100.0) * length_m
    elif mode == "length":
        d_in = max(0.0, min(length_m, mm_to_m(float(getattr(params, "in_range_mm", 10.0)))))
        d_out = max(0.0, min(length_m, mm_to_m(float(getattr(params, "out_range_mm", 10.0)))))
    else:
        d_in = _clamp01(float(getattr(params, "in_range_percent", 100.0)) / 100.0) * length_m
        d_out = _clamp01(float(getattr(params, "out_range_percent", 100.0)) / 100.0) * length_m
    if d_in + d_out > length_m:
        excess = d_in + d_out - length_m
        if d_in >= d_out:
            d_in = max(0.0, d_in - excess)
        else:
            d_out = max(0.0, d_out - excess)
    in_curve = effect_inout_curve.parse_points(getattr(params, "in_easing_curve", effect_inout_curve.DEFAULT_CURVE_TEXT))
    out_curve = effect_inout_curve.parse_points(getattr(params, "out_easing_curve", effect_inout_curve.DEFAULT_CURVE_TEXT))

    def profile(s: float) -> float:
        if d_in <= 1.0e-9:
            vi = 1.0
        else:
            vi = in_frac + (1.0 - in_frac) * effect_inout_curve.evaluate(in_curve, s / d_in)
        if d_out <= 1.0e-9:
            vo = 1.0
        else:
            out_t = _clamp01((length_m - s) / d_out)
            vo = out_frac + (1.0 - out_frac) * effect_inout_curve.evaluate(out_curve, out_t)
        return min(vi, vo)

    return profile, d_in, d_out


def _polyline_cumulative(points_xyz):
    dists = [0.0]
    for i in range(1, len(points_xyz)):
        ax, ay, az = points_xyz[i - 1]
        bx, by, bz = points_xyz[i]
        dists.append(dists[-1] + math.sqrt((bx - ax) ** 2 + (by - ay) ** 2 + (bz - az) ** 2))
    return dists


def _point_at_distance(points_xyz, cum, target: float):
    if target <= cum[0]:
        return points_xyz[0]
    if target >= cum[-1]:
        return points_xyz[-1]
    for i in range(1, len(cum)):
        if cum[i] >= target:
            seg = cum[i] - cum[i - 1]
            t = 0.0 if seg <= 1.0e-12 else (target - cum[i - 1]) / seg
            ax, ay, az = points_xyz[i - 1]
            bx, by, bz = points_xyz[i]
            return (ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t)
    return points_xyz[-1]


def _apply_inout_profile(strokes, params):
    """role=='line' の非閉路ストロークに入り抜き範囲プロファイルを適用する."""
    apply = str(getattr(params, "inout_apply", "brush_size") or "brush_size")
    if apply not in {"brush_size", "opacity"}:
        return strokes
    out: list[EffectLineStroke] = []
    for stroke in strokes:
        pts = list(stroke.points_xyz)
        if stroke.role != "line" or stroke.cyclic or len(pts) < 2:
            out.append(stroke)
            continue
        cum = _polyline_cumulative(pts)
        total = cum[-1]
        if total <= 1.0e-9:
            out.append(stroke)
            continue
        profile, d_in, d_out = _inout_profile(params, total)
        breakpoints = set(cum)
        if 0.0 < d_in < total:
            breakpoints.add(d_in)
        if 0.0 < (total - d_out) < total:
            breakpoints.add(total - d_out)
        if hasattr(params, "in_start_percent") or hasattr(params, "out_start_percent"):
            in_points = effect_inout_curve.parse_points(getattr(params, "in_easing_curve", effect_inout_curve.DEFAULT_CURVE_TEXT))
            out_points = effect_inout_curve.parse_points(getattr(params, "out_easing_curve", effect_inout_curve.DEFAULT_CURVE_TEXT))
            if d_in > 1.0e-9:
                breakpoints.update(d_in * x for x, _y in in_points if 0.0 < x < 1.0)
            if d_out > 1.0e-9:
                breakpoints.update(total - d_out * x for x, _y in out_points if 0.0 < x < 1.0)
        # 旧データの範囲指定が重なる場合は交点を追加して細りの折れを保つ。
        if not (hasattr(params, "in_start_percent") or hasattr(params, "out_start_percent")) and d_in > 1.0e-9 and d_out > 1.0e-9 and d_in > (total - d_out):
            in_frac = _clamp01(float(getattr(params, "in_percent", 100.0)) / 100.0)
            out_frac = _clamp01(float(getattr(params, "out_percent", 0.0)) / 100.0)
            a = (1.0 - in_frac) / d_in + (1.0 - out_frac) / d_out
            if abs(a) > 1.0e-12:
                s_cross = (1.0 - out_frac) * total / d_out / a
                if 0.0 < s_cross < total:
                    breakpoints.add(s_cross)
        ordered = sorted(breakpoints)
        new_pts: list[tuple[float, float, float]] = []
        radii: list[float] = []
        opac: list[float] = []
        prev = None
        for d in ordered:
            if prev is not None and (d - prev) <= 1.0e-9:
                continue
            prev = d
            new_pts.append(_point_at_distance(pts, cum, d))
            val = profile(d)
            radii.append(max(0.0, stroke.radius * val))
            opac.append(_clamp01(val))
        if len(new_pts) < 2:
            out.append(stroke)
            continue
        out.append(
            EffectLineStroke(
                points_xyz=new_pts,
                radius=stroke.radius,
                cyclic=stroke.cyclic,
                radii=radii if apply == "brush_size" else None,
                opacities=opac if apply == "opacity" else None,
                role=stroke.role,
                curve_type=stroke.curve_type,
                bezier_smooth=stroke.bezier_smooth,
                density_end=stroke.density_end,
            )
        )
    return out


def generate_focus_strokes(
    params,
    center_xy_mm: tuple[float, float] = (110.0, 160.0),
    radius_x_mm: float = 40.0,
    radius_y_mm: float = 50.0,
    seed: int = 0,
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
    end_center_xy_mm: tuple[float, float] | None = None,
) -> list[EffectLineStroke]:
    """集中線 (focus) のストローク生成.

    始点形状から終点形状へ線を引く。中心点は放射方向の基準として扱う。
    """
    rng = random.Random(seed)
    out: list[EffectLineStroke] = []
    shape_center_xy_mm = end_center_xy_mm if end_center_xy_mm is not None else center_xy_mm
    if start_outline_mm is None:
        cx, cy = shape_center_xy_mm
        start_rect = _scaled_rect(cx, cy, radius_x_mm, radius_y_mm, 2.0)
        start_outline = _shape_outline(params, "start", start_rect, shape_center_xy_mm, seed=seed + 11)
        start_extend = 0.0
    else:
        start_outline = [(float(x), float(y)) for x, y in start_outline_mm]
        start_extend = max(0.0, float(start_extend_mm))
    end_rect = _scaled_rect(shape_center_xy_mm[0], shape_center_xy_mm[1], radius_x_mm, radius_y_mm, 1.0)
    end_outline = _shape_outline(params, "end", end_rect, shape_center_xy_mm, seed=seed + 23)
    distance_outline_available = str(getattr(params, "spacing_mode", "") or "") == "distance" and len(start_outline) >= 2
    if distance_outline_available and _spacing_density_compensation_enabled(params):
        for point in effect_line_radial_spacing.outline_points_for_perpendicular_spacing(
            params,
            center_xy_mm,
            start_outline,
            start_extend,
            rng,
            max_line_count=_max_line_count,
            slot_positions=_slot_positions,
            clamp01=_clamp01,
        ):
            end_point = _focus_end_point(center_xy_mm, end_outline, point)
            _append_focus_stroke(out, params, rng, point, end_point)
        return out

    spacing_from_start_outline = start_outline_mm is not None and distance_outline_available
    count_outline = start_outline
    count = _focus_slot_count_for_outline(params, count_outline, radius_x_mm, radius_y_mm)
    step_angle = (2.0 * math.pi) / max(1, count)

    for slot in _slot_positions(count, params, rng):
        if spacing_from_start_outline:
            slot_for_start = slot
            if bool(getattr(params, "spacing_jitter_enabled", False)):
                amount = _clamp01(getattr(params, "spacing_jitter_amount", 0.0))
                slot_for_start += amount * (rng.random() * 2.0 - 1.0)
            t = _slot_fraction(slot_for_start, count, closed=True)
            frame_point = _outline_point_at_fraction(start_outline, t)
            if frame_point is None:
                continue
            x0, y0 = _extend_point_from_center(center_xy_mm, frame_point, start_extend)
        else:
            t = _slot_fraction(slot, count, closed=True)
            angle = 2.0 * math.pi * t + math.radians(float(params.rotation_deg))
            if bool(getattr(params, "spacing_jitter_enabled", False)):
                amount = _clamp01(getattr(params, "spacing_jitter_amount", 0.0))
                angle += step_angle * amount * (rng.random() * 2.0 - 1.0)
            x0, y0 = _point_on_outline_or_ellipse(
                center_xy_mm,
                start_outline,
                radius_x_mm * 2.0,
                radius_y_mm * 2.0,
                angle,
                extend_mm=start_extend,
            )
        end_point = _focus_end_point(center_xy_mm, end_outline, (x0, y0))
        _append_focus_stroke(out, params, rng, (x0, y0), end_point)
    return out


def generate_speed_strokes(
    params,
    origin_xy_mm: tuple[float, float] = (40.0, 120.0),
    region_width_mm: float = 120.0,
    region_height_mm: float = 80.0,
    fixed_span_mm: float | None = None,
    seed: int = 0,
) -> list[EffectLineStroke]:
    """流線 (speed) のストローク生成."""
    rng = random.Random(seed)
    out: list[EffectLineStroke] = []
    line_cap = min(_max_line_count(params), max(1, int(params.speed_line_count)))
    if params.spacing_mode == "distance":
        step_mm = max(0.01, float(params.spacing_distance_mm))
        count = max(1, int(round(region_height_mm / step_mm)) + 1)
    else:
        step_deg = max(0.1, float(params.spacing_angle_deg))
        count = max(1, int(round(180.0 / step_deg)) + 1)
    count = min(count, line_cap)
    angle = math.radians(params.speed_angle_deg)
    dx = math.cos(angle)
    dy = math.sin(angle)
    nx = -dy
    ny = dx
    span = max(0.1, float(fixed_span_mm if fixed_span_mm is not None else region_width_mm))
    cx, cy = origin_xy_mm
    spacing_step = region_height_mm / max(1, count - 1) if count > 1 else 0.0
    for slot in _slot_positions(count, params, rng):
        t = _slot_fraction(slot, count, closed=False)
        offset = (t - 0.5) * region_height_mm
        if bool(getattr(params, "spacing_jitter_enabled", False)):
            amount = _clamp01(getattr(params, "spacing_jitter_amount", 0.0))
            offset += spacing_step * amount * (rng.random() * 2.0 - 1.0)
        mid_x = cx + nx * offset
        mid_y = cy + ny * offset
        sx = mid_x - dx * span * 0.5
        sy = mid_y - dy * span * 0.5
        ex = mid_x + dx * span * 0.5
        ey = mid_y + dy * span * 0.5
        (sx, sy), (ex, ey) = _trimmed_segment_points(params, rng, (sx, sy), (ex, ey))
        radius_mm = _jitter(
            params.brush_size_mm,
            params.brush_jitter_amount if params.brush_jitter_enabled else 0.0,
            rng,
        )
        radius, radii = _stroke_radii(params, radius_mm, 2)
        opacities = _stroke_opacities(params, 2)
        out.append(
            EffectLineStroke(
                points_xyz=[(mm_to_m(sx), mm_to_m(sy), 0.0), (mm_to_m(ex), mm_to_m(ey), 0.0)],
                radius=radius,
                radii=radii,
                opacities=opacities,
            )
        )
    return out


def _speed_guide_curve_points(
    center_xy_mm: tuple[float, float],
    radius_x_mm: float,
    radius_y_mm: float,
    angle_deg: float,
    side: float,
) -> list[tuple[float, float]]:
    cx, cy = center_xy_mm
    angle = math.radians(float(angle_deg))
    dx = math.cos(angle)
    dy = math.sin(angle)
    nx = -dy
    ny = dx
    half_span = max(0.1, float(radius_x_mm))
    half_height = max(0.1, float(radius_y_mm))
    bend = min(half_span, half_height) * 0.28 * float(side)
    base_x = cx + dx * half_span * float(side)
    base_y = cy + dy * half_span * float(side)
    return [
        (base_x - nx * half_height, base_y - ny * half_height),
        (base_x - nx * half_height * 0.35 + dx * bend, base_y - ny * half_height * 0.35 + dy * bend),
        (base_x + nx * half_height * 0.35 - dx * bend, base_y + ny * half_height * 0.35 - dy * bend),
        (base_x + nx * half_height, base_y + ny * half_height),
    ]


def generate_speed_guide_strokes(
    params,
    center_xy_mm=(110.0, 160.0),
    radius_xy_mm=(40.0, 50.0),
) -> list[EffectLineStroke]:
    """流線の始点線/終点線を、閉じていないベジェ曲線として返す。"""
    rx, ry = radius_xy_mm
    radius = mm_to_m(max(0.05, min(0.25, float(getattr(params, "brush_size_mm", 0.3)) * 0.4)) / 2.0)
    angle_deg = float(getattr(params, "speed_angle_deg", 0.0))
    start_points = _speed_guide_curve_points(center_xy_mm, rx, ry, angle_deg, -1.0)
    end_points = _speed_guide_curve_points(center_xy_mm, rx, ry, angle_deg, 1.0)
    return [
        EffectLineStroke(
            points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in start_points],
            radius=radius,
            cyclic=False,
            role="start_guide",
            curve_type="BEZIER",
            bezier_smooth=True,
        ),
        EffectLineStroke(
            points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in end_points],
            radius=radius,
            cyclic=False,
            role="end_guide",
            curve_type="BEZIER",
            bezier_smooth=True,
        ),
    ]


def generate_beta_flash_strokes(
    params,
    center_xy_mm: tuple[float, float],
    radius_x_mm: float,
    radius_y_mm: float,
    seed: int = 0,
) -> list[EffectLineStroke]:
    """ベタフラ: 終点形状を閉じたストロークとして生成 (塗り設定は別途)."""
    rect = _scaled_rect(center_xy_mm[0], center_xy_mm[1], radius_x_mm, radius_y_mm, 1.0)
    outline = _shape_outline(params, "end", rect, center_xy_mm, seed=seed + 23)
    points = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in outline]
    radius, radii = _stroke_radii(params, params.brush_size_mm, len(points))
    opacities = _stroke_opacities(params, len(points))
    return [
        EffectLineStroke(
            points_xyz=points,
            radius=radius,
            cyclic=True,
            radii=radii,
            opacities=opacities,
            role="end_fill",
            curve_type="BEZIER",
            bezier_smooth=_shape_guide_uses_smooth_bezier(params, "end"),
        )
    ]


def generate_end_shape_fill_stroke(
    params,
    center_xy_mm: tuple[float, float],
    radius_x_mm: float,
    radius_y_mm: float,
    *,
    seed: int = 0,
) -> EffectLineStroke | None:
    """終点形状を下地として塗るための閉じたストロークを返す。"""
    if str(getattr(params, "effect_type", "") or "") in {"speed", "white_outline"}:
        return None
    rect = _scaled_rect(center_xy_mm[0], center_xy_mm[1], radius_x_mm, radius_y_mm, 1.0)
    outline = _shape_outline(params, "end", rect, center_xy_mm, seed=seed + 23)
    if len(outline) < 3:
        return None
    return EffectLineStroke(
        points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in outline],
        radius=mm_to_m(0.01),
        cyclic=True,
        role="end_fill",
        curve_type="BEZIER",
        bezier_smooth=_shape_guide_uses_smooth_bezier(params, "end"),
    )


def _value_between_min_percent(base: float, min_percent: float, enabled: bool, rng: random.Random) -> float:
    base = max(0.0, float(base))
    if not enabled:
        return base
    lo = base * _clamp01(float(min_percent) / 100.0)
    return lo + (base - lo) * rng.random()


def _span_offsets(center: float, width: float, step: float, *, include_edges: bool = False) -> list[float]:
    width = max(0.0, float(width))
    if width <= 1.0e-6:
        return []
    step = max(0.01, float(step))
    if include_edges and width > step:
        count = max(2, int(math.floor(width / step)) + 1)
        count = min(count, 256)
        start = float(center) - width * 0.5
        unit = width / max(1, count - 1)
        return [start + unit * i for i in range(count)]
    count = max(1, int(math.ceil(width / step)))
    count = min(count, 256)
    start = float(center) - width * 0.5
    unit = width / count
    return [start + unit * (i + 0.5) for i in range(count)]


def _attenuated_length(base_length: float, offset_from_center: float, half_width: float, attenuation: float) -> float:
    norm = 0.0 if half_width <= 1.0e-6 else min(1.0, abs(float(offset_from_center)) / half_width)
    factor = 1.0 - (float(attenuation) / 100.0) * norm
    return max(0.0, float(base_length) * factor)


def _white_outline_stroke(
    center_xy_mm: tuple[float, float],
    direction_xy: tuple[float, float],
    normal_xy: tuple[float, float],
    total_offset_mm: float,
    band_offset_mm: float,
    band_half_width_mm: float,
    base_length_mm: float,
    brush_mm: float,
    attenuation: float,
    role: str,
) -> EffectLineStroke | None:
    cx, cy = center_xy_mm
    dx, dy = direction_xy
    nx, ny = normal_xy
    length = _attenuated_length(base_length_mm, band_offset_mm, band_half_width_mm, attenuation)
    if length <= 1.0e-6:
        return None
    mid_x = cx + nx * total_offset_mm
    mid_y = cy + ny * total_offset_mm
    sx = mid_x - dx * length * 0.5
    sy = mid_y - dy * length * 0.5
    ex = mid_x + dx * length * 0.5
    ey = mid_y + dy * length * 0.5
    return EffectLineStroke(
        points_xyz=[(mm_to_m(sx), mm_to_m(sy), 0.0), (mm_to_m(ex), mm_to_m(ey), 0.0)],
        radius=mm_to_m(max(0.01, float(brush_mm)) / 2.0),
        role=role,
    )


def _white_outline_segment_stroke(
    start_xy_mm: tuple[float, float],
    end_xy_mm: tuple[float, float],
    normal_xy: tuple[float, float],
    total_offset_mm: float,
    band_offset_mm: float,
    band_half_width_mm: float,
    base_length_mm: float,
    brush_mm: float,
    attenuation: float,
    role: str,
) -> EffectLineStroke | None:
    sx, sy = start_xy_mm
    ex, ey = end_xy_mm
    length = math.hypot(ex - sx, ey - sy)
    if length <= 1.0e-6:
        return None
    nx, ny = normal_xy
    effective = _attenuated_length(
        min(float(base_length_mm), length),
        band_offset_mm,
        band_half_width_mm,
        attenuation,
    )
    if effective <= 1.0e-6:
        return None
    scale = min(1.0, effective / length)
    shifted_start = (sx + nx * total_offset_mm, sy + ny * total_offset_mm)
    shifted_end = (
        shifted_start[0] + (ex - sx) * scale,
        shifted_start[1] + (ey - sy) * scale,
    )
    return EffectLineStroke(
        points_xyz=[
            (mm_to_m(shifted_start[0]), mm_to_m(shifted_start[1]), 0.0),
            (mm_to_m(shifted_end[0]), mm_to_m(shifted_end[1]), 0.0),
        ],
        radius=mm_to_m(max(0.01, float(brush_mm)) / 2.0),
        role=role,
    )


def _white_outline_bands(
    params,
    count: int,
    base_width: float,
    base_length: float,
    rng: random.Random,
) -> list[tuple[float, float]]:
    bands: list[tuple[float, float]] = []
    for _i in range(count):
        band_width = _value_between_min_percent(
            base_width,
            float(getattr(params, "white_outline_width_min_percent", 50.0)),
            bool(getattr(params, "white_outline_width_jitter_enabled", False)),
            rng,
        )
        band_length = _value_between_min_percent(
            base_length,
            float(getattr(params, "white_outline_length_min_percent", 50.0)),
            bool(getattr(params, "white_outline_length_jitter_enabled", False)),
            rng,
        )
        bands.append((band_width, band_length))
    return bands


def _append_white_outline_region_strokes(
    out: list[EffectLineStroke],
    center_xy_mm: tuple[float, float],
    direction: tuple[float, float],
    normal: tuple[float, float],
    band_center_offset: float,
    band_half: float,
    band_length: float,
    *,
    region_center: float,
    region_width: float,
    brush_mm: float,
    attenuation: float,
    role: str,
    include_edges: bool = False,
) -> None:
    for local_offset in _span_offsets(region_center, region_width, brush_mm, include_edges=include_edges):
        stroke = _white_outline_stroke(
            center_xy_mm,
            direction,
            normal,
            band_center_offset + local_offset,
            local_offset,
            band_half,
            band_length,
            brush_mm,
            attenuation,
            role,
        )
        if stroke is not None:
            out.append(stroke)


def _append_white_outline_segment_region_strokes(
    out: list[EffectLineStroke],
    start_xy_mm: tuple[float, float],
    end_xy_mm: tuple[float, float],
    normal: tuple[float, float],
    band_center_offset: float,
    band_half: float,
    band_length: float,
    *,
    region_center: float,
    region_width: float,
    brush_mm: float,
    attenuation: float,
    role: str,
    include_edges: bool = False,
) -> None:
    for local_offset in _span_offsets(region_center, region_width, brush_mm, include_edges=include_edges):
        stroke = _white_outline_segment_stroke(
            start_xy_mm,
            end_xy_mm,
            normal,
            band_center_offset + local_offset,
            local_offset,
            band_half,
            band_length,
            brush_mm,
            attenuation,
            role,
        )
        if stroke is not None:
            out.append(stroke)


def _append_white_outline_band_strokes(
    black_strokes: list[EffectLineStroke],
    white_strokes: list[EffectLineStroke],
    params,
    center_xy_mm: tuple[float, float],
    direction: tuple[float, float],
    normal: tuple[float, float],
    band_center_offset: float,
    band_width: float,
    band_length: float,
    *,
    white_ratio: float,
    white_brush: float,
    black_brush: float,
) -> None:
    band_half = max(0.005, band_width * 0.5)
    white_width = band_width * white_ratio
    black_width = max(0.0, (band_width - white_width) * 0.5)
    white_half = white_width * 0.5
    black_regions = (
        (-white_half - black_width * 0.5, black_width),
        (white_half + black_width * 0.5, black_width),
    )
    for region_center, region_width in black_regions:
        _append_white_outline_region_strokes(
            black_strokes,
            center_xy_mm,
            direction,
            normal,
            band_center_offset,
            band_half,
            band_length,
            region_center=region_center,
            region_width=region_width,
            brush_mm=black_brush,
            attenuation=float(getattr(params, "white_outline_black_attenuation", 0.0)),
            role="white_outline_black",
            include_edges=True,
        )
    _append_white_outline_region_strokes(
        white_strokes,
        center_xy_mm,
        direction,
        normal,
        band_center_offset,
        band_half,
        band_length,
        region_center=0.0,
        region_width=white_width,
        brush_mm=white_brush,
        attenuation=float(getattr(params, "white_outline_white_attenuation", 0.0)),
        role="white_outline_white",
    )


def _append_white_outline_segment_band_strokes(
    black_strokes: list[EffectLineStroke],
    white_strokes: list[EffectLineStroke],
    params,
    start_xy_mm: tuple[float, float],
    end_xy_mm: tuple[float, float],
    normal: tuple[float, float],
    band_width: float,
    band_length: float,
    *,
    white_ratio: float,
    white_brush: float,
    black_brush: float,
) -> None:
    band_half = max(0.005, band_width * 0.5)
    white_width = band_width * white_ratio
    black_width = max(0.0, (band_width - white_width) * 0.5)
    white_half = white_width * 0.5
    black_regions = (
        (-white_half - black_width * 0.5, black_width),
        (white_half + black_width * 0.5, black_width),
    )
    for region_center, region_width in black_regions:
        _append_white_outline_segment_region_strokes(
            black_strokes,
            start_xy_mm,
            end_xy_mm,
            normal,
            0.0,
            band_half,
            band_length,
            region_center=region_center,
            region_width=region_width,
            brush_mm=black_brush,
            attenuation=float(getattr(params, "white_outline_black_attenuation", 0.0)),
            role="white_outline_black",
            include_edges=True,
        )
    _append_white_outline_segment_region_strokes(
        white_strokes,
        start_xy_mm,
        end_xy_mm,
        normal,
        0.0,
        band_half,
        band_length,
        region_center=0.0,
        region_width=white_width,
        brush_mm=white_brush,
        attenuation=float(getattr(params, "white_outline_white_attenuation", 0.0)),
        role="white_outline_white",
    )


def generate_white_outline_strokes(
    params,
    center_xy_mm: tuple[float, float],
    radius_x_mm: float,
    radius_y_mm: float,
    seed: int = 0,
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
    end_center_xy_mm: tuple[float, float] | None = None,
) -> list[EffectLineStroke]:
    """白抜き線: 始点側から中心側へ伸びる放射状の白線群を生成."""
    rng = random.Random(seed)
    count = max(1, min(500, int(getattr(params, "white_outline_count", 5))))
    base_width = max(0.01, float(getattr(params, "white_outline_width_mm", 10.0)))
    white_ratio = _clamp01(float(getattr(params, "white_outline_white_ratio_percent", 30.0)) / 100.0)
    white_brush = max(0.01, float(getattr(params, "white_outline_white_brush_mm", 0.3)))
    black_brush = max(0.01, float(getattr(params, "white_outline_black_brush_mm", 0.3)))
    shape_center_xy_mm = end_center_xy_mm if end_center_xy_mm is not None else center_xy_mm
    if start_outline_mm is None:
        start_rect = _scaled_rect(shape_center_xy_mm[0], shape_center_xy_mm[1], radius_x_mm, radius_y_mm, 2.0)
        start_outline = _shape_outline(params, "start", start_rect, shape_center_xy_mm, seed=seed + 11)
        start_extend = 0.0
    else:
        start_outline = [(float(x), float(y)) for x, y in start_outline_mm]
        start_extend = max(0.0, float(start_extend_mm))
    end_rect = _scaled_rect(shape_center_xy_mm[0], shape_center_xy_mm[1], radius_x_mm, radius_y_mm, 1.0)
    end_outline = _shape_outline(params, "end", end_rect, shape_center_xy_mm, seed=seed + 23)
    base_length = max(0.1, math.hypot(float(radius_x_mm) * 2.0, float(radius_y_mm) * 2.0))
    bands = _white_outline_bands(params, count, base_width, base_length, rng)
    black_strokes: list[EffectLineStroke] = []
    white_strokes: list[EffectLineStroke] = []

    base_angle = math.radians(float(getattr(params, "white_outline_angle_deg", 0.0)))
    for index, (band_width, band_length) in enumerate(bands):
        angle = base_angle + (2.0 * math.pi * index) / max(1, count)
        start_xy = _point_on_outline_or_ellipse(
            center_xy_mm,
            start_outline,
            radius_x_mm * 2.0,
            radius_y_mm * 2.0,
            angle,
            extend_mm=start_extend,
        )
        end_xy = _focus_end_point(center_xy_mm, end_outline, start_xy)
        dx = float(end_xy[0]) - float(start_xy[0])
        dy = float(end_xy[1]) - float(start_xy[1])
        length = math.hypot(dx, dy)
        if length <= 1.0e-6:
            continue
        normal = (-dy / length, dx / length)
        _append_white_outline_segment_band_strokes(
            black_strokes,
            white_strokes,
            params,
            start_xy,
            end_xy,
            normal,
            band_width,
            band_length,
            white_ratio=white_ratio,
            white_brush=white_brush,
            black_brush=black_brush,
        )
    return black_strokes + white_strokes


def _with_points(stroke: EffectLineStroke, points: list[tuple[float, float, float]], *, role: str | None = None) -> EffectLineStroke:
    return EffectLineStroke(
        points_xyz=points,
        radius=stroke.radius,
        cyclic=stroke.cyclic,
        radii=stroke.radii,
        opacities=stroke.opacities,
        role=stroke.role if role is None else role,
        curve_type=stroke.curve_type,
        bezier_smooth=stroke.bezier_smooth,
        density_end=stroke.density_end,
    )


def _apply_bundle_jagged_start_fixed(strokes: list[EffectLineStroke], params) -> list[EffectLineStroke]:
    if not bool(getattr(params, "bundle_enabled", False)):
        return strokes
    if not bool(getattr(params, "bundle_jagged_enabled", False)):
        return strokes
    bundle_size = max(1, int(getattr(params, "bundle_line_count", 1) or 1))
    if bundle_size <= 1:
        return strokes
    height = _clamp01(float(getattr(params, "bundle_jagged_height_percent", 100.0)) / 100.0)
    if height <= 0.0:
        return strokes
    out: list[EffectLineStroke] = []
    line_index = 0
    for stroke in strokes:
        if stroke.cyclic or len(stroke.points_xyz) < 2 or str(stroke.role or "line") != "line":
            out.append(stroke)
            continue
        pos = line_index % bundle_size
        line_index += 1
        edge = abs((pos / max(1, bundle_size - 1)) * 2.0 - 1.0)
        factor = max(0.0, 1.0 - edge * height)
        if factor >= 0.999999:
            out.append(stroke)
            continue
        sx, sy, sz = stroke.points_xyz[0]
        pts = [
            (
                sx + (float(px) - sx) * factor,
                sy + (float(py) - sy) * factor,
                sz + (float(pz) - sz) * factor,
            )
            for px, py, pz in stroke.points_xyz
        ]
        out.append(_with_points(stroke, pts))
    return out


def _white_underlay_offset_points(
    stroke: EffectLineStroke,
    offset_m: float,
) -> list[tuple[float, float, float]] | None:
    if stroke.cyclic or len(stroke.points_xyz) < 2:
        return None
    sx, sy, _sz = stroke.points_xyz[0]
    ex, ey, _ez = stroke.points_xyz[-1]
    dx = float(ex) - float(sx)
    dy = float(ey) - float(sy)
    length = math.hypot(dx, dy)
    if length <= 1.0e-9:
        return None
    nx = -dy / length
    ny = dx / length
    return [(float(px) + nx * offset_m, float(py) + ny * offset_m, float(pz) - 1.0e-5) for px, py, pz in stroke.points_xyz]


def _apply_white_underlay_strokes(strokes: list[EffectLineStroke], params) -> list[EffectLineStroke]:
    if str(getattr(params, "effect_type", "") or "") in {"speed", "white_outline", "beta_flash"}:
        return strokes
    if not bool(getattr(params, "white_underlay_enabled", False)):
        return strokes
    try:
        width_percent = max(-300.0, min(300.0, float(getattr(params, "white_underlay_width_percent", 150.0))))
    except Exception:  # noqa: BLE001
        width_percent = 150.0
    if abs(width_percent) <= 1.0e-6:
        return strokes
    radius_scale = abs(width_percent) / 100.0
    underlays: list[EffectLineStroke] = []
    rest: list[EffectLineStroke] = []
    for stroke in strokes:
        if str(stroke.role or "line") == "line":
            underlay_radius = max(0.0, float(stroke.radius) * radius_scale)
            offset_m = (1.0 if width_percent >= 0.0 else -1.0) * underlay_radius
            pts = _white_underlay_offset_points(stroke, offset_m)
            if pts is not None:
                radii = None
                if stroke.radii is not None:
                    radii = [max(0.0, float(radius) * radius_scale) for radius in stroke.radii]
                underlays.append(
                    EffectLineStroke(
                        points_xyz=pts,
                        radius=underlay_radius,
                        cyclic=stroke.cyclic,
                        radii=radii,
                        opacities=stroke.opacities,
                        role="underlay",
                        curve_type=stroke.curve_type,
                        bezier_smooth=stroke.bezier_smooth,
                        density_end=stroke.density_end,
                    )
                )
        rest.append(stroke)
    return underlays + rest


def _apply_uni_flash_jag(
    strokes: list[EffectLineStroke],
    center_xy_mm: tuple[float, float],
) -> list[EffectLineStroke]:
    """ウニフラ用に終点側を交互に出入りさせ、通常の集中線と差別化する。"""
    cx = mm_to_m(center_xy_mm[0])
    cy = mm_to_m(center_xy_mm[1])
    out: list[EffectLineStroke] = []
    for i, stroke in enumerate(strokes):
        if len(stroke.points_xyz) < 2:
            out.append(stroke)
            continue
        pts = list(stroke.points_xyz)
        ex, ey, ez = pts[-1]
        scale = 0.84 if i % 2 == 0 else 1.10
        pts[-1] = (cx + (ex - cx) * scale, cy + (ey - cy) * scale, ez)
        out.append(
            EffectLineStroke(
                points_xyz=pts,
                radius=stroke.radius,
                cyclic=stroke.cyclic,
                radii=stroke.radii,
                opacities=stroke.opacities,
                role=stroke.role,
                curve_type=stroke.curve_type,
                bezier_smooth=stroke.bezier_smooth,
            )
        )
    return out


def generate_strokes(
    params,
    center_xy_mm=(110.0, 160.0),
    radius_xy_mm=(40.0, 50.0),
    seed=0,
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
    end_center_xy_mm: tuple[float, float] | None = None,
):
    etype = params.effect_type
    rx, ry = radius_xy_mm
    shape_center_xy_mm = end_center_xy_mm if end_center_xy_mm is not None else center_xy_mm
    if etype == "speed":
        return _apply_inout_profile(
            generate_speed_strokes(
                params,
                origin_xy_mm=shape_center_xy_mm,
                region_width_mm=rx * 2.0,
                region_height_mm=ry * 2.0,
                fixed_span_mm=rx * 2.0,
                seed=seed,
            ),
            params,
        )
    if etype == "beta_flash":
        return generate_beta_flash_strokes(params, shape_center_xy_mm, rx, ry, seed=seed)
    if etype == "white_outline":
        return generate_white_outline_strokes(
            params,
            center_xy_mm,
            rx,
            ry,
            seed=seed,
            start_outline_mm=start_outline_mm,
            start_extend_mm=start_extend_mm,
            end_center_xy_mm=shape_center_xy_mm,
        )
    focus_strokes = generate_focus_strokes(
        params,
        center_xy_mm,
        rx,
        ry,
        seed=seed,
        start_outline_mm=start_outline_mm,
        start_extend_mm=start_extend_mm,
        end_center_xy_mm=shape_center_xy_mm,
    )
    if etype == "uni_flash":
        focus_strokes = _apply_uni_flash_jag(focus_strokes, center_xy_mm)
    focus_strokes = _apply_bundle_jagged_start_fixed(focus_strokes, params)
    focus_strokes = _apply_inout_profile(focus_strokes, params)
    return _apply_white_underlay_strokes(focus_strokes, params)


def generate_shape_guide_strokes(
    params,
    center_xy_mm=(110.0, 160.0),
    radius_xy_mm=(40.0, 50.0),
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
    seed: int = 0,
    end_center_xy_mm: tuple[float, float] | None = None,
) -> list[EffectLineStroke]:
    """始点/終点の形状ラインをガイドストロークとして返す。"""
    etype = getattr(params, "effect_type", "")
    shape_center_xy_mm = end_center_xy_mm if end_center_xy_mm is not None else center_xy_mm
    if etype == "white_outline":
        return []
    if etype == "speed":
        return generate_speed_guide_strokes(params, shape_center_xy_mm, radius_xy_mm)
    rx, ry = radius_xy_mm
    cx, cy = shape_center_xy_mm
    end_rect = _scaled_rect(cx, cy, rx, ry, 1.0)
    end_outline = _shape_outline(params, "end", end_rect, shape_center_xy_mm, seed=seed + 23)
    if start_outline_mm is None:
        start_rect = _scaled_rect(cx, cy, rx, ry, 2.0)
        start_outline = _shape_outline(params, "start", start_rect, shape_center_xy_mm, seed=seed + 11)
        start_smooth = _shape_guide_uses_smooth_bezier(params, "start")
    else:
        start_outline = _actual_outline_by_rays(
            center_xy_mm,
            start_outline_mm,
            extend_mm=max(0.0, float(start_extend_mm)),
        )
        start_smooth = _shape_guide_uses_smooth_bezier(params, "start", frame_outline=True)
    radius = mm_to_m(max(0.05, min(0.25, float(getattr(params, "brush_size_mm", 0.3)) * 0.4)) / 2.0)
    guides: list[EffectLineStroke] = []
    if len(start_outline) >= 2:
        guides.append(
            EffectLineStroke(
                points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in start_outline],
                radius=radius,
                cyclic=True,
                role="start_guide",
                curve_type="BEZIER",
                bezier_smooth=start_smooth,
            )
        )
    if len(end_outline) >= 2:
        guides.append(
            EffectLineStroke(
                points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in end_outline],
                radius=radius,
                cyclic=True,
                role="end_guide",
                curve_type="BEZIER",
                bezier_smooth=_shape_guide_uses_smooth_bezier(params, "end"),
            )
        )
    return guides


def generate_shape_source_outlines(
    params,
    center_xy_mm=(110.0, 160.0),
    radius_xy_mm=(40.0, 50.0),
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    seed: int = 0,
    end_center_xy_mm: tuple[float, float] | None = None,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Geometry Nodes が線端を決めるための始点/終点アウトラインを返す。"""
    rx, ry = radius_xy_mm
    shape_center_xy_mm = end_center_xy_mm if end_center_xy_mm is not None else center_xy_mm
    cx, cy = shape_center_xy_mm
    end_rect = _scaled_rect(cx, cy, rx, ry, 1.0)
    end_outline = _shape_outline(params, "end", end_rect, shape_center_xy_mm, seed=seed + 23)
    if start_outline_mm is None:
        start_rect = _scaled_rect(cx, cy, rx, ry, 2.0)
        start_outline = _shape_outline(params, "start", start_rect, shape_center_xy_mm, seed=seed + 11)
    else:
        start_outline = [(float(x), float(y)) for x, y in start_outline_mm]
    return start_outline, end_outline


def generate_focus_density_points(
    params,
    center_xy_mm: tuple[float, float],
    density_outline_mm: Sequence[tuple[float, float]],
    *,
    start_extend_mm: float = 0.0,
    seed: int = 0,
) -> list[tuple[float, float]]:
    """距離指定の線間隔を旧方式と同じ半径方向の距離で点列化する。"""
    rng = random.Random(seed)
    return effect_line_radial_spacing.outline_points_for_perpendicular_spacing(
        params,
        center_xy_mm,
        density_outline_mm,
        start_extend_mm,
        rng,
        max_line_count=_max_line_count,
        slot_positions=_slot_positions,
        clamp01=_clamp01,
    )
