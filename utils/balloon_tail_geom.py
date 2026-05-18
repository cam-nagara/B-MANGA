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
    angle = math.radians(float(getattr(tail, "direction_deg", 270.0) or 270.0))
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


def polygon_for_tail(rect: Rect, tail: Any) -> list[tuple[float, float]]:
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
    centerline = _smoothed_centerline(centerline, tail)
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
    return left + list(reversed(right))
