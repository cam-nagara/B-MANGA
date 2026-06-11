from __future__ import annotations

import math
from typing import Any

from .balloon_shapes import Rect


def uses_custom_points(tail: Any) -> bool:
    return len(tail_local_points(tail)) >= 2 or bool(getattr(tail, "custom_points_enabled", False))


def tail_local_points(tail: Any) -> list[tuple[float, float]]:
    points = getattr(tail, "points", None)
    if points is not None and len(points) >= 2:
        return [
            (
                float(getattr(point, "x_mm", 0.0) or 0.0),
                float(getattr(point, "y_mm", 0.0) or 0.0),
            )
            for point in points
        ]
    if bool(getattr(tail, "custom_points_enabled", False)):
        return [
            (
                float(getattr(tail, "start_x_mm", 0.0) or 0.0),
                float(getattr(tail, "start_y_mm", 0.0) or 0.0),
            ),
            (
                float(getattr(tail, "end_x_mm", 0.0) or 0.0),
                float(getattr(tail, "end_y_mm", 0.0) or 0.0),
            ),
        ]
    return []


def tail_world_points(rect: Rect, tail: Any) -> list[tuple[float, float]]:
    pts = tail_local_points(tail)
    if len(pts) >= 2:
        return [(rect.x + x, rect.y + y) for x, y in pts]
    root, tip = default_axis_points(rect, tail)
    return [root, tip]


def sync_legacy_axis_fields(tail: Any) -> None:
    pts = tail_local_points(tail)
    if len(pts) < 2:
        return
    sx, sy = pts[0]
    ex, ey = pts[-1]
    tail.custom_points_enabled = True
    tail.start_x_mm = float(sx)
    tail.start_y_mm = float(sy)
    tail.end_x_mm = float(ex)
    tail.end_y_mm = float(ey)
    dx = ex - sx
    dy = ey - sy
    tail.length_mm = math.hypot(dx, dy)
    if abs(dx) > 1.0e-6 or abs(dy) > 1.0e-6:
        tail.direction_deg = math.degrees(math.atan2(dy, dx))


def axis_points(rect: Rect, tail: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    pts = tail_world_points(rect, tail)
    if len(pts) >= 2:
        return pts[0], pts[-1]
    return default_axis_points(rect, tail)


def default_axis_points(rect: Rect, tail: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    # `or 270.0` だと direction_deg=0.0 (右) で falsy のため 270.0 (下) に
    # フォールバックしてしまう不具合があった。 None / 未設定のときだけ既定値を
    # 採用するように修正。
    raw_dir = getattr(tail, "direction_deg", 270.0)
    if raw_dir is None:
        raw_dir = 270.0
    angle = math.radians(float(raw_dir))
    dx = math.cos(angle)
    dy = math.sin(angle)
    cx = rect.x + rect.width * 0.5
    cy = rect.y + rect.height * 0.5
    rx = max(rect.width * 0.5, 0.001)
    ry = max(rect.height * 0.5, 0.001)
    denom = math.sqrt((dx / rx) ** 2 + (dy / ry) ** 2) if (dx or dy) else 1.0
    radius = 1.0 / denom if denom > 0.0 else 0.0
    root = (cx + dx * radius, cy + dy * radius)
    length = max(0.0, float(getattr(tail, "length_mm", 0.0) or 0.0))
    tip = (root[0] + dx * length, root[1] + dy * length)
    return root, tip


def local_axis_points(entry: Any, tail: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    rect = Rect(0.0, 0.0, float(getattr(entry, "width_mm", 0.0) or 0.0), float(getattr(entry, "height_mm", 0.0) or 0.0))
    return axis_points(rect, tail)


def world_axis_points(entry: Any, tail: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    rect = Rect(
        float(getattr(entry, "x_mm", 0.0) or 0.0),
        float(getattr(entry, "y_mm", 0.0) or 0.0),
        float(getattr(entry, "width_mm", 0.0) or 0.0),
        float(getattr(entry, "height_mm", 0.0) or 0.0),
    )
    return axis_points(rect, tail)


def write_axis_points(tail: Any, root: tuple[float, float], tip: tuple[float, float]) -> None:
    write_polyline_points(tail, [root, tip])


def write_polyline_points(tail: Any, points: list[tuple[float, float]]) -> None:
    if len(points) < 2:
        return
    if hasattr(tail, "points"):
        tail.points.clear()
        for x, y in points:
            point = tail.points.add()
            point.x_mm = float(x)
            point.y_mm = float(y)
            point.corner_type = "line"
    sync_legacy_axis_fields(tail)


def add_polyline_point(tail: Any, point_xy: tuple[float, float], *, insert_index: int | None = None) -> int:
    pts = tail_local_points(tail)
    if len(pts) < 2:
        return -1
    if not hasattr(tail, "points"):
        return -1
    if len(tail.points) < 2:
        write_polyline_points(tail, pts)
    index = len(tail.points) if insert_index is None else max(1, min(int(insert_index), len(tail.points)))
    point = tail.points.add()
    for i in range(len(tail.points) - 1, index, -1):
        prev = tail.points[i - 1]
        tail.points[i].x_mm = prev.x_mm
        tail.points[i].y_mm = prev.y_mm
        tail.points[i].corner_type = prev.corner_type
    point = tail.points[index]
    point.x_mm = float(point_xy[0])
    point.y_mm = float(point_xy[1])
    point.corner_type = "line"
    sync_legacy_axis_fields(tail)
    return index


def set_point(tail: Any, index: int, point_xy: tuple[float, float]) -> bool:
    if not hasattr(tail, "points"):
        return False
    if len(tail.points) < 2:
        pts = tail_local_points(tail)
        if len(pts) >= 2:
            write_polyline_points(tail, pts)
    if not (0 <= int(index) < len(tail.points)):
        return False
    point = tail.points[int(index)]
    point.x_mm = float(point_xy[0])
    point.y_mm = float(point_xy[1])
    sync_legacy_axis_fields(tail)
    return True


def is_ellipse_chain(tail: Any) -> bool:
    """しっぽの線種が「楕円」(連続楕円) かを返す."""
    return str(getattr(tail, "line_type", "wedge") or "wedge") == "ellipse_chain"


def is_curve_mode(tail: Any) -> bool:
    """しっぽのポイント列を曲線でつなぐ設定かを返す."""
    return str(getattr(tail, "curve_mode", "polyline") or "polyline") == "curve"


def _catmull_rom_centerline(
    points: list[tuple[float, float]],
    samples_per_segment: int = 6,
) -> list[tuple[float, float]]:
    """ポイント列を通るなめらかな曲線 (Catmull-Rom 補間) でサンプリングする."""
    n = len(points)
    if n < 3:
        return list(points)
    out: list[tuple[float, float]] = [points[0]]
    for i in range(n - 1):
        p0 = points[max(0, i - 1)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(n - 1, i + 2)]
        for step in range(1, samples_per_segment + 1):
            t = step / samples_per_segment
            t2 = t * t
            t3 = t2 * t
            out.append((
                0.5 * ((2.0 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * t3),
                0.5 * ((2.0 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * t3),
            ))
    return out


def centerline_for_tail(rect: Rect, tail: Any) -> list[tuple[float, float]]:
    """しっぽの中心線 (くさび・楕円チェーン共通) を返す."""
    tail_type = str(getattr(tail, "type", "straight") or "straight")
    centerline = tail_world_points(rect, tail)
    if tail_type == "curve" and not uses_custom_points(tail) and len(centerline) == 2:
        root, tip = centerline
        vx = tip[0] - root[0]
        vy = tip[1] - root[1]
        length = math.hypot(vx, vy)
        if length > 0.0:
            nx = -vy / length
            ny = vx / length
            bend = float(getattr(tail, "curve_bend", 0.0) or 0.0) * length * 0.4
            centerline = [root, ((root[0] + tip[0]) * 0.5 + nx * bend, (root[1] + tip[1]) * 0.5 + ny * bend), tip]
    if is_curve_mode(tail) and len(centerline) >= 3:
        return _catmull_rom_centerline(centerline)
    return _smoothed_centerline(centerline, tail)


def ellipse_chain_for_tail(rect: Rect, tail: Any) -> list[tuple[float, float, float, float, float]]:
    """連続楕円しっぽの楕円列 [(cx, cy, rx, ry, angle_rad), ...] を返す.

    - 各楕円の大きさはしっぽの太さ (根元幅→先端幅の補間) に連動する。
    - 楕円どうしの間隔は ellipse_gap_mm。
    - 長軸は進行方向に対して垂直 (心の声らしい縦長の見た目)。
    """
    centerline = centerline_for_tail(rect, tail)
    if len(centerline) < 2:
        return []
    distances, total = _polyline_lengths(centerline)
    if total <= 1.0e-6:
        return []
    rw = max(0.1, float(getattr(tail, "root_width_mm", 0.0) or 0.0)) * 0.5
    tw = max(0.0, float(getattr(tail, "tip_width_mm", 0.0) or 0.0)) * 0.5
    gap = max(0.0, float(getattr(tail, "ellipse_gap_mm", 1.5) or 0.0))

    def _point_at(dist: float) -> tuple[float, float, float]:
        """中心線上の距離 dist の位置と接線角を返す."""
        dist = max(0.0, min(total, dist))
        for i in range(1, len(centerline)):
            if distances[i] >= dist or i == len(centerline) - 1:
                seg = distances[i] - distances[i - 1]
                t = (dist - distances[i - 1]) / seg if seg > 1.0e-9 else 0.0
                x0, y0 = centerline[i - 1]
                x1, y1 = centerline[i]
                return (
                    x0 + (x1 - x0) * t,
                    y0 + (y1 - y0) * t,
                    math.atan2(y1 - y0, x1 - x0),
                )
        x0, y0 = centerline[-1]
        return (x0, y0, 0.0)

    ellipses: list[tuple[float, float, float, float, float]] = []
    min_radius = 0.15
    dist = 0.0
    for _ in range(200):
        t = dist / total
        radius = rw + (tw - rw) * t
        if radius < min_radius and ellipses:
            break
        radius = max(min_radius, radius)
        # 進行方向に沿った半径は少し短く (縦長の楕円)
        along = radius * 0.75
        center_dist = dist + along
        if center_dist + along > total + radius * 0.5:
            break
        cx, cy, angle = _point_at(center_dist)
        ellipses.append((cx, cy, along, radius, angle))
        dist = center_dist + along + gap
        if dist >= total:
            break
    return ellipses


def ellipse_polygon(
    ellipse: tuple[float, float, float, float, float],
    segments: int = 24,
) -> list[tuple[float, float]]:
    """楕円 (cx, cy, rx, ry, angle) を多角形の点列にする."""
    cx, cy, rx, ry, angle = ellipse
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    pts: list[tuple[float, float]] = []
    for i in range(max(8, int(segments))):
        theta = (i / segments) * math.tau
        x = math.cos(theta) * rx
        y = math.sin(theta) * ry
        pts.append((cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a))
    return pts


def _smoothed_centerline(points: list[tuple[float, float]], tail: Any) -> list[tuple[float, float]]:
    if len(points) < 3 or not hasattr(tail, "points"):
        return points
    out: list[tuple[float, float]] = [points[0]]
    for i in range(1, len(points) - 1):
        p0 = points[i - 1]
        p1 = points[i]
        p2 = points[i + 1]
        if not hasattr(tail, "points") or i >= len(tail.points):
            out.append(p1)
            continue
        corner_type = str(getattr(tail.points[i], "corner_type", "line") or "line")
        if corner_type != "curve":
            out.append(p1)
            continue
        a = ((p0[0] + p1[0]) * 0.5, (p0[1] + p1[1]) * 0.5)
        b = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
        if out[-1] != a:
            out.append(a)
        for step in range(1, 5):
            t = step / 4.0
            mt = 1.0 - t
            out.append((
                mt * mt * a[0] + 2.0 * mt * t * p1[0] + t * t * b[0],
                mt * mt * a[1] + 2.0 * mt * t * p1[1] + t * t * b[1],
            ))
    out.append(points[-1])
    return out


def _polyline_lengths(points: list[tuple[float, float]]) -> tuple[list[float], float]:
    distances = [0.0]
    total = 0.0
    for p0, p1 in zip(points, points[1:]):
        total += math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        distances.append(total)
    return distances, total


def _root_join_overlap_mm(tail: Any) -> float:
    root_width = max(0.0, float(getattr(tail, "root_width_mm", 0.0) or 0.0))
    return max(2.0, min(12.0, root_width * 0.65))


def polygon_for_tail(rect: Rect, tail: Any, *, join_overlap_mm: float = 0.0) -> list[tuple[float, float]]:
    # 連続楕円しっぽは「くさび多角形」を持たない (ellipse_chain_for_tail で描く)。
    # 空を返すことで、本体との結合・くさび描画の全経路から自然に外れる。
    if is_ellipse_chain(tail):
        return []
    tail_type = str(getattr(tail, "type", "straight") or "straight")
    centerline = centerline_for_tail(rect, tail)
    if len(centerline) < 2:
        return []
    distances, total_length = _polyline_lengths(centerline)
    if total_length <= 0.0:
        return []
    rw = max(0.0, float(getattr(tail, "root_width_mm", 0.0) or 0.0)) * 0.5
    tw = max(0.0, float(getattr(tail, "tip_width_mm", 0.0) or 0.0)) * 0.5
    if tail_type == "sticky":
        tw = max(tw, rw * 0.5)
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for i, point in enumerate(centerline):
        if i == 0:
            tangent = (centerline[1][0] - point[0], centerline[1][1] - point[1])
        elif i == len(centerline) - 1:
            tangent = (point[0] - centerline[i - 1][0], point[1] - centerline[i - 1][1])
        else:
            tangent = (centerline[i + 1][0] - centerline[i - 1][0], centerline[i + 1][1] - centerline[i - 1][1])
        length = math.hypot(tangent[0], tangent[1])
        if length <= 0.0:
            continue
        nx = -tangent[1] / length
        ny = tangent[0] / length
        t = distances[i] / total_length if total_length > 0.0 else 0.0
        half_width = rw + (tw - rw) * t
        left.append((point[0] + nx * half_width, point[1] + ny * half_width))
        right.append((point[0] - nx * half_width, point[1] - ny * half_width))
    if len(left) < 2 or len(right) < 2:
        return []
    polygon = left + list(reversed(right))
    overlap = max(0.0, float(join_overlap_mm))
    if overlap > 1.0e-6 and len(centerline) >= 2 and len(polygon) >= 3:
        root = centerline[0]
        next_point = centerline[1]
        dx = next_point[0] - root[0]
        dy = next_point[1] - root[1]
        length = math.hypot(dx, dy)
        if length > 1.0e-9:
            ix = -dx / length * overlap
            iy = -dy / length * overlap
            polygon[0] = (polygon[0][0] + ix, polygon[0][1] + iy)
            polygon[-1] = (polygon[-1][0] + ix, polygon[-1][1] + iy)
    return polygon


def joined_polygon_for_tail(rect: Rect, tail: Any) -> list[tuple[float, float]]:
    return polygon_for_tail(rect, tail, join_overlap_mm=_root_join_overlap_mm(tail))
