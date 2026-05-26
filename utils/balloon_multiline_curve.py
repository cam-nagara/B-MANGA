"""フキダシ多重線用の補助カーブ生成."""

from __future__ import annotations

import math
from typing import Sequence

import bpy

from . import balloon_render_contract as render_contract
from . import balloon_shapes
from .geom import Rect, mm_to_m

MULTI_LINE_ROLE_RADIUS_OFFSET = render_contract.MULTI_LINE_ROLE_RADIUS_OFFSET
OUTER_EDGE_ROLE_RADIUS = render_contract.OUTER_EDGE_ROLE_RADIUS
INNER_EDGE_ROLE_RADIUS = render_contract.INNER_EDGE_ROLE_RADIUS
MAIN_LINE_FILL_ROLE_RADIUS = render_contract.MAIN_LINE_FILL_ROLE_RADIUS
_MATERIAL_SLOT_OUTER_EDGE = render_contract.MATERIAL_SLOT_OUTER_EDGE
_MATERIAL_SLOT_INNER_EDGE = render_contract.MATERIAL_SLOT_INNER_EDGE
_MATERIAL_SLOT_LINE = render_contract.MATERIAL_SLOT_LINE
_EDGE_OVERLAP_RATIO = 0.06
_THORN_EDGE_OVERLAP_RATIO = 0.06
_THORN_MULTI_LINE_LENGTH_DISTANCE_GAIN = 5.0


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
    points, corners = balloon_shapes.outline_with_corners_for_entry(entry, rect)
    return _strip_duplicate_closure(points, corners)


def _strip_duplicate_closure(
    points: Sequence[tuple[float, float]],
    corners: Sequence[int] | None = None,
) -> tuple[list[tuple[float, float]], list[int]]:
    path = [(float(x), float(y)) for x, y in points]
    corner_list = [int(index) for index in (corners or [])]
    if len(path) > 2 and math.hypot(path[0][0] - path[-1][0], path[0][1] - path[-1][1]) <= 1.0e-6:
        removed_index = len(path) - 1
        path.pop()
        corner_list = [index for index in corner_list if index != removed_index]
    return path, corner_list


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


def _add_open_poly_path(
    curve: bpy.types.Curve,
    points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
    point_radius: float | Sequence[float],
    close: bool = False,
    material_index: int = 0,
) -> None:
    if len(points) < 2:
        return
    path = [(float(x), float(y)) for x, y in points]
    if close and len(path) > 2 and math.hypot(path[0][0] - path[-1][0], path[0][1] - path[-1][1]) <= 1.0e-6:
        path.pop()
    if len(path) < 2:
        return
    spline = curve.splines.new("POLY")
    spline.points.add(len(path) - 1)
    spline.use_cyclic_u = bool(close)
    spline.material_index = int(material_index)
    for index, point in enumerate(path):
        if isinstance(point_radius, (list, tuple)) and point_radius:
            radius = float(point_radius[min(index, len(point_radius) - 1)])
        else:
            radius = float(point_radius)
        _set_poly_point(spline.points[index], point, offset=offset, point_radius=radius)


def _add_closed_polygon(
    curve: bpy.types.Curve,
    points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
    point_radius: float,
    material_index: int = 0,
) -> None:
    if len(points) < 3:
        return
    _add_open_poly_path(
        curve,
        points,
        offset=offset,
        point_radius=point_radius,
        close=True,
        material_index=material_index,
    )


def _orient_loop(points: Sequence[tuple[float, float]], *, ccw: bool) -> list[tuple[float, float]]:
    path = [(float(x), float(y)) for x, y in points]
    if len(path) < 3:
        return path
    is_ccw = _polygon_signed_area(path) > 0.0
    if is_ccw != bool(ccw):
        path.reverse()
    return path


def _add_filled_band(
    curve: bpy.types.Curve,
    outer: Sequence[tuple[float, float]],
    inner: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
    role_radius: float,
    material_index: int,
) -> None:
    """Fill Curve で穴あき帯になるよう、外周 CCW / 内周 CW の面線を追加する."""
    if len(outer) < 3 or len(inner) < 3:
        return
    outer_loop = _orient_loop(outer, ccw=True)
    inner_loop = _orient_loop(inner, ccw=False)
    _add_closed_polygon(
        curve,
        outer_loop,
        offset=offset,
        point_radius=role_radius,
        material_index=material_index,
    )
    _add_closed_polygon(
        curve,
        inner_loop,
        offset=offset,
        point_radius=role_radius,
        material_index=material_index,
    )


def _add_bezier_closed_loop(
    curve: bpy.types.Curve,
    points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
    point_radius: float,
    material_index: int,
) -> None:
    """点列から AUTO ハンドルの閉じたベジエスプラインを追加する.

    Fill Curve で面化したとき、点列を直線で結ぶ POLY と違って、
    Blender のカーブ解像度に応じた滑らかな曲線として塗られる。
    """
    path = [(float(x), float(y)) for x, y in points]
    if len(path) > 2 and math.hypot(path[0][0] - path[-1][0], path[0][1] - path[-1][1]) <= 1.0e-6:
        path.pop()
    if len(path) < 3:
        return
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(path) - 1)
    spline.use_cyclic_u = True
    spline.material_index = int(material_index)
    for index, point in enumerate(path):
        bp = spline.bezier_points[index]
        x, y, z = _point_to_curve_xyz(point, offset)
        bp.co = (x, y, z)
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"
        try:
            bp.radius = max(0.0, float(point_radius))
        except Exception:  # noqa: BLE001
            pass


def _add_filled_band_bezier(
    curve: bpy.types.Curve,
    outer: Sequence[tuple[float, float]],
    inner: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
    role_radius: float,
    material_index: int,
) -> None:
    """B-Name 側で算出した外周/内周点列を、滑らかなベジエ閉ループ 2 本として
    追加する。Fill Curve が「外周 - 内周」の穴あき面 (= 帯) として塗る。"""
    if len(outer) < 3 or len(inner) < 3:
        return
    outer_loop = _orient_loop(outer, ccw=True)
    inner_loop = _orient_loop(inner, ccw=False)
    _add_bezier_closed_loop(
        curve,
        outer_loop,
        offset=offset,
        point_radius=role_radius,
        material_index=material_index,
    )
    _add_bezier_closed_loop(
        curve,
        inner_loop,
        offset=offset,
        point_radius=role_radius,
        material_index=material_index,
    )


def _add_centered_capsule_line(
    curve: bpy.types.Curve,
    points: Sequence[tuple[float, float]],
    *,
    width_mm: float,
    offset: tuple[float, float],
    role_radius: float,
    material_index: int,
) -> None:
    path, _corners = _strip_duplicate_closure(points)
    if len(path) < 2:
        return
    radius = max(0.0, float(width_mm)) * 0.5
    if radius <= 1.0e-7:
        return
    count = len(path)
    for index in range(count):
        a = path[index]
        b = path[(index + 1) % count]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1.0e-7:
            continue
        nx = -dy / length
        ny = dx / length
        _add_closed_polygon(
            curve,
            (
                (a[0] + nx * radius, a[1] + ny * radius),
                (b[0] + nx * radius, b[1] + ny * radius),
                (b[0] - nx * radius, b[1] - ny * radius),
                (a[0] - nx * radius, a[1] - ny * radius),
            ),
            offset=offset,
            point_radius=role_radius,
            material_index=material_index,
        )
    cap_steps = 12
    for center in path:
        cap = [
            (
                center[0] + math.cos((math.tau * step) / cap_steps) * radius,
                center[1] + math.sin((math.tau * step) / cap_steps) * radius,
            )
            for step in range(cap_steps)
        ]
        _add_closed_polygon(
            curve,
            cap,
            offset=offset,
            point_radius=role_radius,
            material_index=material_index,
        )


def _orient_loop_with_radii(
    points: Sequence[tuple[float, float]],
    radii: Sequence[float],
    *,
    ccw: bool,
) -> tuple[list[tuple[float, float]], list[float]]:
    path = [(float(x), float(y)) for x, y in points]
    radius_values = [float(value) for value in radii]
    if len(path) < 3:
        return path, radius_values
    is_ccw = _polygon_signed_area(path) > 0.0
    if is_ccw != bool(ccw):
        path.reverse()
        radius_values.reverse()
    return path, radius_values


def _add_filled_band_with_radii(
    curve: bpy.types.Curve,
    outer: Sequence[tuple[float, float]],
    inner: Sequence[tuple[float, float]],
    outer_radii: Sequence[float],
    inner_radii: Sequence[float],
    *,
    offset: tuple[float, float],
    material_index: int,
) -> None:
    if len(outer) < 3 or len(inner) < 3:
        return
    outer_loop, outer_radius_values = _orient_loop_with_radii(outer, outer_radii, ccw=True)
    inner_loop, inner_radius_values = _orient_loop_with_radii(inner, inner_radii, ccw=False)
    _add_open_poly_path(
        curve,
        outer_loop,
        offset=offset,
        point_radius=outer_radius_values,
        close=True,
        material_index=material_index,
    )
    _add_open_poly_path(
        curve,
        inner_loop,
        offset=offset,
        point_radius=inner_radius_values,
        close=True,
        material_index=material_index,
    )


def _band_loops_for_side(
    body_points: Sequence[tuple[float, float]],
    *,
    center_distance_mm: float,
    width_mm: float,
    clockwise: bool,
    side: str,
    smooth: bool,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    half = max(0.0, float(width_mm)) * 0.5
    near_distance = max(0.0, float(center_distance_mm) - half)
    far_distance = max(0.0, float(center_distance_mm) + half)
    offset_fn = _offset_closed_outline_smooth if smooth else _offset_closed_outline
    if side == "inside":
        outer = offset_fn(body_points, distance_mm=near_distance, clockwise=clockwise, side="inside")
        inner = offset_fn(body_points, distance_mm=far_distance, clockwise=clockwise, side="inside")
    else:
        outer = offset_fn(body_points, distance_mm=far_distance, clockwise=clockwise, side="outside")
        inner = offset_fn(body_points, distance_mm=near_distance, clockwise=clockwise, side="outside")
    if outer is None or inner is None:
        return None
    return outer, inner


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


def _offset_closed_outline(
    points: Sequence[tuple[float, float]],
    *,
    distance_mm: float,
    clockwise: bool,
    side: str,
) -> list[tuple[float, float]] | None:
    points, _corners = _strip_duplicate_closure(points)
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


def _offset_closed_outline_arc(
    points: Sequence[tuple[float, float]],
    *,
    distance_mm: float,
    clockwise: bool,
    side: str,
    sharp_skip_deg: float = 60.0,
    miter_limit: float = 2.0,
) -> list[tuple[float, float]] | None:
    """点列の輪郭をオフセットする。鋭い角は制御点をスキップして、両隣の
    オフセット点を BEZIER+AUTO で直接繋ぐ。緩やかな角は隣接オフセット線の
    交点 (miter, clamp あり) で詰める。

    BEZIER 閉ループに乗せると AUTO ハンドル補間で滑らかな丸みになる。
    body の鋭い谷 (雲のバンプ間) でオフセットが深く突き出さず、線幅が一定
    に近い帯になる。
    """
    points, _corners = _strip_duplicate_closure(points)
    n = len(points)
    if n < 3:
        return None
    distance = float(distance_mm)
    if distance <= 1.0e-9:
        return [(float(x), float(y)) for x, y in points]

    direction_sign = -1.0 if side == "inside" else 1.0
    sharp_skip = math.radians(max(1.0, sharp_skip_deg))
    result: list[tuple[float, float]] = []

    for index, current in enumerate(points):
        previous = points[index - 1]
        nxt = points[(index + 1) % n]

        d_prev_x = current[0] - previous[0]
        d_prev_y = current[1] - previous[1]
        d_next_x = nxt[0] - current[0]
        d_next_y = nxt[1] - current[1]
        prev_len = math.hypot(d_prev_x, d_prev_y)
        next_len = math.hypot(d_next_x, d_next_y)
        if prev_len <= 1.0e-9 or next_len <= 1.0e-9:
            continue

        n_prev = _segment_outward_normal(previous, current, clockwise=clockwise)
        n_next = _segment_outward_normal(current, nxt, clockwise=clockwise)
        n_prev = (n_prev[0] * direction_sign, n_prev[1] * direction_sign)
        n_next = (n_next[0] * direction_sign, n_next[1] * direction_sign)

        cross_n = n_prev[0] * n_next[1] - n_prev[1] * n_next[0]
        dot_n = n_prev[0] * n_next[0] + n_prev[1] * n_next[1]
        delta = math.atan2(cross_n, dot_n)

        # 鋭角はオフセット点を 1 つ追加するとどちら向きでも深く突き出すので、
        # この制御点はスキップして両隣のオフセット点を BEZIER で直接繋ぐ。
        if abs(delta) >= sharp_skip:
            continue

        if abs(delta) < math.radians(1.0):
            mid = _normalize_2d((n_prev[0] + n_next[0], n_prev[1] + n_next[1]))
            if mid is None:
                continue
            result.append((current[0] + mid[0] * distance, current[1] + mid[1] * distance))
            continue

        p1 = (current[0] + n_prev[0] * distance, current[1] + n_prev[1] * distance)
        p2 = (current[0] + n_next[0] * distance, current[1] + n_next[1] * distance)
        d_prev_dir = (d_prev_x / prev_len, d_prev_y / prev_len)
        d_next_dir = (d_next_x / next_len, d_next_y / next_len)
        hit = _line_intersection_2d(p1, d_prev_dir, p2, d_next_dir)
        if hit is not None and math.hypot(hit[0] - current[0], hit[1] - current[1]) <= distance * miter_limit:
            result.append(hit)
        else:
            mid = _normalize_2d((n_prev[0] + n_next[0], n_prev[1] + n_next[1]))
            if mid is None:
                continue
            result.append((current[0] + mid[0] * distance, current[1] + mid[1] * distance))

    return result


def _offset_closed_outline_smooth(
    points: Sequence[tuple[float, float]],
    *,
    distance_mm: float,
    clockwise: bool,
    side: str,
) -> list[tuple[float, float]] | None:
    points, _corners = _strip_duplicate_closure(points)
    if len(points) < 3:
        return None
    result: list[tuple[float, float]] = []
    direction_sign = -1.0 if side == "inside" else 1.0
    for index, current in enumerate(points):
        previous = points[index - 1]
        next_point = points[(index + 1) % len(points)]
        n_prev = _segment_outward_normal(previous, current, clockwise=clockwise)
        n_next = _segment_outward_normal(current, next_point, clockwise=clockwise)
        n_prev = (n_prev[0] * direction_sign, n_prev[1] * direction_sign)
        n_next = (n_next[0] * direction_sign, n_next[1] * direction_sign)
        normal = _normalize_2d((n_prev[0] + n_next[0], n_prev[1] + n_next[1]))
        if normal is None:
            normal = n_next if math.hypot(n_next[0], n_next[1]) > 0.0 else n_prev
        dot_prev = max(0.35, min(1.0, normal[0] * n_prev[0] + normal[1] * n_prev[1]))
        offset = min(distance_mm / dot_prev, distance_mm * 2.0)
        result.append((current[0] + normal[0] * offset, current[1] + normal[1] * offset))
    return result


def _offset_closed_outline_variable_width(
    points: Sequence[tuple[float, float]],
    widths_mm: Sequence[float],
    *,
    clockwise: bool,
    side: str,
) -> list[tuple[float, float]] | None:
    points, _corners = _strip_duplicate_closure(points)
    if len(points) < 3:
        return None
    if not widths_mm:
        return None
    result: list[tuple[float, float]] = []
    direction_sign = -1.0 if side == "inside" else 1.0
    count = len(points)
    for index, current in enumerate(points):
        previous = points[index - 1]
        next_point = points[(index + 1) % count]
        d_prev = _normalize_2d((current[0] - previous[0], current[1] - previous[1]))
        d_next = _normalize_2d((next_point[0] - current[0], next_point[1] - current[1]))
        if d_prev is None or d_next is None:
            return None
        distance_mm = max(0.0, float(widths_mm[index % len(widths_mm)])) * 0.5
        if distance_mm <= 1.0e-9:
            result.append((float(current[0]), float(current[1])))
            continue
        n_prev = _segment_outward_normal(previous, current, clockwise=clockwise)
        n_next = _segment_outward_normal(current, next_point, clockwise=clockwise)
        n_prev = (n_prev[0] * direction_sign, n_prev[1] * direction_sign)
        n_next = (n_next[0] * direction_sign, n_next[1] * direction_sign)
        p1 = (current[0] + n_prev[0] * distance_mm, current[1] + n_prev[1] * distance_mm)
        p2 = (current[0] + n_next[0] * distance_mm, current[1] + n_next[1] * distance_mm)
        hit = _line_intersection_2d(p1, d_prev, p2, d_next)
        if hit is not None and math.hypot(hit[0] - current[0], hit[1] - current[1]) <= max(distance_mm * 16.0, distance_mm + 1.0e-6):
            result.append(hit)
            continue
        bis = _normalize_2d((n_prev[0] + n_next[0], n_prev[1] + n_next[1]))
        if bis is None:
            result.append(p1)
        else:
            result.append((current[0] + bis[0] * distance_mm, current[1] + bis[1] * distance_mm))
    return result


def _scaled_thorn_ring_points(
    ring_points: Sequence[tuple[float, float]],
    *,
    length_scale: float,
    cross_enabled: bool,
) -> list[tuple[float, float]]:
    points = [(float(x), float(y)) for x, y in ring_points]
    if len(points) < 4:
        return points
    clamped = max(0.0, min(1.0, float(length_scale)))
    factor = 1.18 + (1.0 - clamped) if cross_enabled else clamped
    out = list(points)
    count = len(points)
    for index in range(1, count, 2):
        valley_a = points[(index - 1) % count]
        peak = points[index]
        valley_b = points[(index + 1) % count]
        base = ((valley_a[0] + valley_b[0]) * 0.5, (valley_a[1] + valley_b[1]) * 0.5)
        out[index] = (
            base[0] + (peak[0] - base[0]) * factor,
            base[1] + (peak[1] - base[1]) * factor,
        )
    if not cross_enabled and clamped < 0.999:
        center = (
            sum(point[0] for point in points) / max(1, count),
            sum(point[1] for point in points) / max(1, count),
        )
        whole_scale = max(0.02, clamped)
        out = [
            (
                center[0] + (point[0] - center[0]) * whole_scale,
                center[1] + (point[1] - center[1]) * whole_scale,
            )
            for point in out
        ]
    return out


def _append_thorn_multiline_band(
    curve: bpy.types.Curve,
    ring_points: Sequence[tuple[float, float]],
    *,
    valley_width_mm: float,
    peak_width_mm: float,
    line_width_mm: float,
    offset: tuple[float, float],
    role_radius: float,
    material_index: int,
) -> None:
    if len(ring_points) < 4:
        return
    widths = [
        max(0.0, float(valley_width_mm)) if index % 2 == 0 else max(0.0, float(peak_width_mm))
        for index in range(len(ring_points))
    ]
    if max(widths, default=0.0) <= 1.0e-7:
        return
    clockwise = _polygon_signed_area(ring_points) < 0.0
    outer = _offset_closed_outline_variable_width(ring_points, widths, clockwise=clockwise, side="outside")
    inner = _offset_closed_outline_variable_width(ring_points, widths, clockwise=clockwise, side="inside")
    if outer is None or inner is None:
        return
    radius_values = [
        role_radius + max(0.0, width) / max(1.0e-6, float(line_width_mm))
        for width in widths
    ]
    _add_filled_band_with_radii(
        curve,
        outer,
        inner,
        radius_values,
        radius_values,
        offset=offset,
        material_index=material_index,
    )


def append_closed_multi_line_paths(
    curve: bpy.types.Curve,
    entry,
    body_points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
) -> None:
    shape_name = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
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
    base_distance_mm = line_width_mm * 0.5
    base_length_scale = max(0.0, min(1.0, float(getattr(entry, "thorn_multi_line_length_scale_percent", 100.0) or 0.0) / 100.0))
    cross_enabled = bool(getattr(entry, "thorn_multi_line_cross_enabled", False))
    for ring_index in range(1, count):
        ring_width_mm = multi_width_mm * (width_scale ** max(0, ring_index - 1))
        valley_width_mm = max(0.0, float(getattr(entry, "thorn_multi_line_valley_width_mm", ring_width_mm) or 0.0)) * (
            width_scale ** max(0, ring_index - 1)
        )
        peak_width_mm = max(0.0, float(getattr(entry, "thorn_multi_line_peak_width_mm", ring_width_mm) or 0.0)) * (
            width_scale ** max(0, ring_index - 1)
        )
        ring_extent_width_mm = max(ring_width_mm, valley_width_mm, peak_width_mm) if shape_name == "thorn" else ring_width_mm
        if ring_extent_width_mm <= 0.0:
            continue
        distance_mm = base_distance_mm + spacing_mm * ring_index
        for side in sides:
            offset_fn = _offset_closed_outline if shape_name in {"rect", "octagon", "thorn"} else _offset_closed_outline_smooth
            ring_points = offset_fn(body_points, distance_mm=distance_mm, clockwise=clockwise, side=side)
            if ring_points is None:
                continue
            if shape_name == "thorn":
                ring_length_scale = base_length_scale ** (max(1, ring_index) * _THORN_MULTI_LINE_LENGTH_DISTANCE_GAIN)
                ring_points = _scaled_thorn_ring_points(
                    ring_points,
                    length_scale=ring_length_scale,
                    cross_enabled=cross_enabled,
                )
                _append_thorn_multiline_band(
                    curve,
                    ring_points,
                    valley_width_mm=valley_width_mm,
                    peak_width_mm=peak_width_mm,
                    line_width_mm=line_width_mm,
                    offset=offset,
                    role_radius=MULTI_LINE_ROLE_RADIUS_OFFSET,
                    material_index=_MATERIAL_SLOT_LINE,
                )
            else:
                band = _band_loops_for_side(
                    body_points,
                    center_distance_mm=distance_mm,
                    width_mm=ring_width_mm,
                    clockwise=clockwise,
                    side=side,
                    smooth=True,
                )
                if band is None:
                    continue
                outer, inner = band
                _add_filled_band(
                    curve,
                    outer,
                    inner,
                    offset=offset,
                    role_radius=MULTI_LINE_ROLE_RADIUS_OFFSET + (ring_width_mm / line_width_mm),
                    material_index=_MATERIAL_SLOT_LINE,
                )


def append_main_line_fill_paths(
    curve: bpy.types.Curve,
    entry,
    body_points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
) -> None:
    """主線を、中心線ではなく線幅ぶんの面として追加する."""
    if str(getattr(entry, "line_style", "") or "") == "none":
        return
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    if line_width_mm <= 0.0 or len(body_points) < 3:
        return
    shape_name = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    clockwise = _polygon_signed_area(body_points) < 0.0
    half_width = line_width_mm * 0.5
    if shape_name == "ellipse":
        return
    if shape_name not in {"rect", "octagon", "thorn"}:
        # 滑らか形状 (雲・モフモフ・トゲ曲線) は B-Name 側で外周/内周を算出し、
        # 滑らかなベジエ閉ループ 2 本として追加する (Fill Curve が穴あき帯にする).
        # 鋭い谷を持つ body でも線幅が一定になるよう、広がる側は円弧で丸め、
        # 狭まる側は隣接オフセット線の交点で詰める (右直角・自己交差を回避).
        outer = _offset_closed_outline_arc(body_points, distance_mm=half_width, clockwise=clockwise, side="outside")
        inner = _offset_closed_outline_arc(body_points, distance_mm=half_width, clockwise=clockwise, side="inside")
        if outer is None or inner is None:
            return
        _add_filled_band_bezier(
            curve,
            outer,
            inner,
            offset=offset,
            role_radius=MAIN_LINE_FILL_ROLE_RADIUS,
            material_index=_MATERIAL_SLOT_LINE,
        )
        return
    offset_fn = _offset_closed_outline
    outer = offset_fn(body_points, distance_mm=half_width, clockwise=clockwise, side="outside")
    inner = offset_fn(body_points, distance_mm=half_width, clockwise=clockwise, side="inside")
    if outer is None or inner is None:
        return
    _add_filled_band(
        curve,
        outer,
        inner,
        offset=offset,
        role_radius=MAIN_LINE_FILL_ROLE_RADIUS,
        material_index=_MATERIAL_SLOT_LINE,
    )


def append_sharp_main_line_fill_paths(
    curve: bpy.types.Curve,
    entry,
    body_points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
) -> None:
    """互換用。現行は鋭角以外も主線を面として追加する."""
    append_main_line_fill_paths(curve, entry, body_points, offset=offset)


def _append_polyline_segment_bands(
    curve: bpy.types.Curve,
    points: Sequence[tuple[float, float]],
    radius_scales: Sequence[float],
    *,
    line_width_mm: float,
    offset: tuple[float, float],
    role_radius: float,
    material_index: int,
) -> None:
    if len(points) < 2:
        return
    count = len(points)
    for index in range(count):
        next_index = (index + 1) % count
        width_a = max(0.0, float(radius_scales[index % len(radius_scales)] if radius_scales else 1.0)) * line_width_mm
        width_b = max(0.0, float(radius_scales[next_index % len(radius_scales)] if radius_scales else 1.0)) * line_width_mm
        if width_a <= 1.0e-7 or width_b <= 1.0e-7:
            continue
        a = points[index]
        b = points[next_index]
        dx = float(b[0]) - float(a[0])
        dy = float(b[1]) - float(a[1])
        length = math.hypot(dx, dy)
        if length <= 1.0e-7:
            continue
        nx = -dy / length
        ny = dx / length
        ha = width_a * 0.5
        hb = width_b * 0.5
        _add_closed_polygon(
            curve,
            (
                (a[0] + nx * ha, a[1] + ny * ha),
                (b[0] + nx * hb, b[1] + ny * hb),
                (b[0] - nx * hb, b[1] - ny * hb),
                (a[0] - nx * ha, a[1] - ny * ha),
            ),
            offset=offset,
            point_radius=role_radius + max(width_a, width_b) / max(1.0e-6, line_width_mm),
            material_index=material_index,
        )


def _thorn_multiline_length_points(
    ring_points: Sequence[tuple[float, float]],
    *,
    valley_width_mm: float,
    peak_width_mm: float,
    line_width_mm: float,
    length_scale: float,
    cross_enabled: bool = False,
) -> tuple[list[tuple[float, float]], list[float]]:
    if len(ring_points) < 4:
        width_scale = max(0.0, float(peak_width_mm)) / max(1.0e-6, float(line_width_mm))
        return list(ring_points), [width_scale] * len(ring_points)
    valley_scale = max(0.0, float(valley_width_mm)) / max(1.0e-6, float(line_width_mm))
    peak_scale = max(0.0, float(peak_width_mm)) / max(1.0e-6, float(line_width_mm))
    if length_scale >= 0.999 and not cross_enabled:
        return (
            list(ring_points),
            [valley_scale if index % 2 == 0 else peak_scale for index in range(len(ring_points))],
        )
    path: list[tuple[float, float]] = []
    radii: list[float] = []

    def add(point: tuple[float, float], radius: float) -> None:
        if path and math.hypot(path[-1][0] - point[0], path[-1][1] - point[1]) <= 1.0e-7 and abs(radii[-1] - radius) <= 1.0e-7:
            return
        path.append((float(point[0]), float(point[1])))
        radii.append(max(0.0, float(radius)))

    def point_by_distance(start: tuple[float, float], end: tuple[float, float], distance: float) -> tuple[float, float]:
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        length = math.hypot(dx, dy)
        if length <= 1.0e-9:
            return (float(start[0]), float(start[1]))
        scale = float(distance) / length
        return (float(start[0]) + dx * scale, float(start[1]) + dy * scale)

    side_lengths: list[float] = []
    for valley_index in range(0, len(ring_points), 2):
        valley = ring_points[valley_index % len(ring_points)]
        peak = ring_points[(valley_index + 1) % len(ring_points)]
        next_valley = ring_points[(valley_index + 2) % len(ring_points)]
        side_lengths.append(math.hypot(peak[0] - valley[0], peak[1] - valley[1]))
        side_lengths.append(math.hypot(peak[0] - next_valley[0], peak[1] - next_valley[1]))
    side_lengths = sorted(length for length in side_lengths if length > 1.0e-9)
    reference_length = side_lengths[len(side_lengths) // 2] if side_lengths else 0.0
    visible_length = max(0.0, reference_length * max(0.0, min(1.0, float(length_scale))))
    cross_extension = max(0.0, reference_length * max(0.0, 1.0 - min(1.0, float(length_scale))))

    count = len(ring_points)
    first_valley = ring_points[0]
    add(first_valley, valley_scale)
    for valley_index in range(0, count, 2):
        valley = ring_points[valley_index % count]
        peak = ring_points[(valley_index + 1) % count]
        next_valley = ring_points[(valley_index + 2) % count]
        if valley_index > 0:
            add(valley, valley_scale)
        if cross_enabled:
            left_end = point_by_distance(peak, valley, -cross_extension)
            right_end = point_by_distance(peak, next_valley, -cross_extension)
            add(left_end, peak_scale)
            add(right_end, peak_scale)
        else:
            left_end = point_by_distance(valley, peak, visible_length)
            right_end = point_by_distance(next_valley, peak, visible_length)
            add(left_end, peak_scale)
            add(left_end, 0.0)
            add(peak, 0.0)
            add(right_end, 0.0)
            add(right_end, peak_scale)
    return path, radii


def append_edge_paths(
    curve: bpy.types.Curve,
    entry,
    body_points: Sequence[tuple[float, float]],
    *,
    offset: tuple[float, float],
) -> None:
    if len(body_points) < 3:
        return
    line_width_mm = 0.0 if str(getattr(entry, "line_style", "") or "") == "none" else float(getattr(entry, "line_width_mm", 0.3) or 0.3)
    shape_name = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    clockwise = _polygon_signed_area(body_points) < 0.0
    for enabled_attr, width_attr, side, material_index, role_radius in (
        ("outer_white_margin_enabled", "outer_white_margin_width_mm", "outside", _MATERIAL_SLOT_OUTER_EDGE, OUTER_EDGE_ROLE_RADIUS),
        ("inner_white_margin_enabled", "inner_white_margin_width_mm", "inside", _MATERIAL_SLOT_INNER_EDGE, INNER_EDGE_ROLE_RADIUS),
    ):
        if not bool(getattr(entry, enabled_attr, False)):
            continue
        width_mm = max(0.0, float(getattr(entry, width_attr, 0.0) or 0.0))
        if width_mm <= 0.0:
            continue
        overlap_ratio = _THORN_EDGE_OVERLAP_RATIO if shape_name == "thorn" else _EDGE_OVERLAP_RATIO
        overlap_mm = min(max(0.0, line_width_mm), width_mm) * overlap_ratio
        near = max(0.0, max(0.0, line_width_mm * 0.5) - overlap_mm)
        far = max(0.0, max(0.0, line_width_mm * 0.5) + width_mm)
        offset_fn = _offset_closed_outline_smooth if shape_name not in {"rect", "octagon", "thorn"} else _offset_closed_outline
        if side == "inside":
            outer = offset_fn(body_points, distance_mm=near, clockwise=clockwise, side=side)
            inner = offset_fn(body_points, distance_mm=far, clockwise=clockwise, side=side)
        else:
            outer = offset_fn(body_points, distance_mm=far, clockwise=clockwise, side=side)
            inner = offset_fn(body_points, distance_mm=near, clockwise=clockwise, side=side)
        if outer is None or inner is None:
            continue
        _add_filled_band(
            curve,
            outer,
            inner,
            offset=offset,
            role_radius=role_radius,
            material_index=material_index,
        )


def has_filled_line_paths(curve: bpy.types.Curve | None) -> bool:
    if curve is None:
        return False
    for spline in getattr(curve, "splines", []) or []:
        if str(getattr(spline, "type", "") or "") != "POLY":
            continue
        points = list(getattr(spline, "points", []) or [])
        if not points:
            continue
        role = float(getattr(points[0], "radius", 0.0) or 0.0)
        if role >= 50.0:
            return True
    return False


def outer_render_margin_mm(entry, line_width_mm: float) -> float:
    margin = max(0.0, float(line_width_mm) * 0.5)
    if bool(getattr(entry, "outer_white_margin_enabled", False)):
        margin = max(margin, float(line_width_mm) * 0.5 + max(0.0, float(getattr(entry, "outer_white_margin_width_mm", 0.0) or 0.0)))
    if str(getattr(entry, "line_style", "") or "") == "double":
        shape_name = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
        count = max(1, min(12, int(getattr(entry, "multi_line_count", 3) or 3)))
        spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
        width_mm = max(0.0, float(getattr(entry, "multi_line_width_mm", 0.3) or 0.0))
        valley_width_mm = max(0.0, float(getattr(entry, "thorn_multi_line_valley_width_mm", width_mm) or 0.0))
        peak_width_mm = max(0.0, float(getattr(entry, "thorn_multi_line_peak_width_mm", width_mm) or 0.0))
        scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
        if str(getattr(entry, "multi_line_direction", "outside") or "outside") in {"outside", "both"}:
            current = max(0.0, float(line_width_mm) * 0.5) + spacing_mm
            for ring_index in range(1, count):
                ring_width = width_mm * (scale ** max(0, ring_index - 1))
                if shape_name == "thorn":
                    ring_width = max(
                        ring_width,
                        valley_width_mm * (scale ** max(0, ring_index - 1)),
                        peak_width_mm * (scale ** max(0, ring_index - 1)),
                    )
                if ring_width <= 0.0:
                    current += spacing_mm
                    continue
                margin = max(margin, current + ring_width)
                current += ring_width + spacing_mm
    return margin
