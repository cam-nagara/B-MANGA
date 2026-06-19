"""Fallback preview drawing for extended B-MANGA asset payloads."""

from __future__ import annotations

import math
from typing import Callable


Color = tuple[float, float, float, float]
Rect = tuple[int, int, int, int]
Point = tuple[int, int]


def draw_preview_text(
    canvas: list[float],
    size: int,
    rect: Rect,
    *,
    draw_line: Callable[[list[float], int, int, int, int, int, Color], None],
) -> None:
    left, bottom, right, top = rect
    height = max(1, top - bottom)
    count = max(2, min(5, height // 7))
    for i in range(count):
        y = bottom + int(round((i + 1) * height / (count + 1)))
        draw_line(canvas, size, left + 2, y, right - 2, y, (0.08, 0.08, 0.08, 1.0))


def draw_preview_effect(
    canvas: list[float],
    size: int,
    rect: Rect,
    entry: dict | None,
    *,
    draw_polygon: Callable[..., None],
    draw_line: Callable[[list[float], int, int, int, int, int, Color], None],
    draw_line_thick: Callable[[list[float], int, int, int, int, int, Color, int], None],
) -> None:
    left, bottom, right, top = rect
    cx = (left + right) // 2
    cy = (bottom + top) // 2
    meta = entry.get("meta") if isinstance(entry, dict) and isinstance(entry.get("meta"), dict) else {}
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    effect_type = str(params.get("effect_type", "") or "")
    if effect_type == "speed":
        _draw_speed_preview(canvas, size, rect, draw_line_thick)
        return
    ray_count = 28 if effect_type in {"uni_flash", "beta_flash"} else 18
    if effect_type == "beta_flash":
        _draw_beta_flash_preview(canvas, size, (cx, cy), rect, ray_count, draw_polygon)
        return
    for i in range(ray_count):
        t = i / float(ray_count)
        if i % 4 == 0:
            x = left + int((right - left) * t)
            y = top
        elif i % 4 == 1:
            x = right
            y = bottom + int((top - bottom) * t)
        elif i % 4 == 2:
            x = right - int((right - left) * t)
            y = bottom
        else:
            x = left
            y = top - int((top - bottom) * t)
        draw_line(canvas, size, cx, cy, x, y, (0.1, 0.1, 0.1, 1.0))


def _draw_speed_preview(
    canvas: list[float],
    size: int,
    rect: Rect,
    draw_line_thick: Callable[[list[float], int, int, int, int, int, Color, int], None],
) -> None:
    left, bottom, right, top = rect
    for i in range(12):
        y = bottom + int(round((i + 1) * (top - bottom) / 13.0))
        skew = int(round((right - left) * 0.18))
        draw_line_thick(
            canvas,
            size,
            left + 2,
            y - skew // 4,
            right - 2,
            y + skew // 4,
            (0.1, 0.1, 0.1, 1.0),
            1,
        )


def _draw_beta_flash_preview(
    canvas: list[float],
    size: int,
    center: tuple[int, int],
    rect: Rect,
    ray_count: int,
    draw_polygon: Callable[..., None],
) -> None:
    left, bottom, right, top = rect
    cx, cy = center
    star = []
    radius = min(right - left, top - bottom) * 0.48
    for i in range(ray_count):
        t = i / float(ray_count)
        angle = t * math.tau
        r = radius if i % 2 == 0 else radius * 0.58
        star.append((int(round(cx + r * math.cos(angle))), int(round(cy + r * math.sin(angle)))))
    draw_polygon(canvas, size, star, (0.05, 0.05, 0.05, 1.0), fill=True)


def draw_extended_preview(
    canvas: list[float],
    size: int,
    rect: Rect,
    transform: tuple[float, float, float],
    entry: dict,
    *,
    map_points: Callable[[list[tuple[float, float]], Rect, float, float], list[Point]],
    draw_polygon: Callable[[list[float], int, list[Point], Color], None],
    draw_rect: Callable[..., None],
    draw_line: Callable[[list[float], int, int, int, int, int, Color], None],
    draw_line_thick: Callable[[list[float], int, int, int, int, int, Color, int], None],
    clamp_rect: Callable[[Rect, int], Rect],
) -> bool:
    kind = str(entry.get("kind", "") or "")
    if kind == "coma":
        _draw_preview_coma(canvas, size, rect, entry, map_points, draw_polygon, draw_rect)
        return True
    if kind == "gp":
        _draw_preview_gp(canvas, size, transform, entry, draw_line_thick)
        return True
    if kind == "raster":
        _draw_preview_raster(canvas, size, rect, draw_rect, draw_line, clamp_rect)
        return True
    return False


def _draw_preview_coma(
    canvas: list[float],
    size: int,
    rect: Rect,
    entry: dict,
    map_points: Callable[[list[tuple[float, float]], Rect, float, float], list[Point]],
    draw_polygon: Callable[..., None],
    draw_rect: Callable[..., None],
) -> None:
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    shape_data = data.get("shape") if isinstance(data.get("shape"), dict) else {}
    shape = str(shape_data.get("type", data.get("shapeType", data.get("shape_type", "rect"))) or "rect")
    points: list[tuple[float, float]] = []
    if shape == "rect":
        rect_data = shape_data.get("rect") if isinstance(shape_data.get("rect"), dict) else {}
        width = _float(rect_data.get("widthMm", data.get("rectWidthMm", data.get("rect_width_mm", 1.0))), 1.0)
        height = _float(rect_data.get("heightMm", data.get("rectHeightMm", data.get("rect_height_mm", 1.0))), 1.0)
        points = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    else:
        points = _coma_vertices(shape_data.get("vertices", data.get("vertices", [])) or [])
    if len(points) < 3:
        draw_rect(canvas, size, rect, (0.04, 0.04, 0.04, 1.0), fill=False)
        return
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x = min(xs)
    min_y = min(ys)
    width = max(1.0, max(xs) - min_x)
    height = max(1.0, max(ys) - min_y)
    normalized = [(x - min_x, y - min_y) for x, y in points]
    mapped = map_points(normalized, rect, width, height)
    draw_polygon(canvas, size, mapped, (1.0, 1.0, 1.0, 1.0), fill=True)
    draw_polygon(canvas, size, mapped, (0.04, 0.04, 0.04, 1.0), fill=False, thickness=2)


def _coma_vertices(items) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in items:
        if isinstance(item, dict):
            x = _float(item.get("xMm", item.get("x_mm", 0.0)), 0.0)
            y = _float(item.get("yMm", item.get("y_mm", 0.0)), 0.0)
            points.append((x, y))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append((_float(item[0], 0.0), _float(item[1], 0.0)))
    return points


def _draw_preview_gp(
    canvas: list[float],
    size: int,
    transform: tuple[float, float, float],
    entry: dict,
    draw_line_thick: Callable[[list[float], int, int, int, int, int, Color, int], None],
) -> None:
    for frame in entry.get("frames", []) or []:
        if not isinstance(frame, dict):
            continue
        for stroke in frame.get("strokes", []) or []:
            if isinstance(stroke, dict):
                _draw_gp_stroke(canvas, size, transform, stroke, draw_line_thick)


def _draw_gp_stroke(
    canvas: list[float],
    size: int,
    transform: tuple[float, float, float],
    stroke: dict,
    draw_line_thick: Callable[[list[float], int, int, int, int, int, Color, int], None],
) -> None:
    scale, offset_x, offset_y = transform
    mapped: list[Point] = []
    for point in stroke.get("points", []) or []:
        if not isinstance(point, dict):
            continue
        x = _float(point.get("x", 0.0), 0.0)
        y = _float(point.get("y", 0.0), 0.0)
        mapped.append((int(round(x * scale + offset_x)), int(round(y * scale + offset_y))))
    for start, end in zip(mapped, mapped[1:], strict=False):
        draw_line_thick(canvas, size, start[0], start[1], end[0], end[1], (0.05, 0.05, 0.05, 1.0), 2)
    if bool(stroke.get("cyclic", False)) and len(mapped) >= 3:
        start, end = mapped[-1], mapped[0]
        draw_line_thick(canvas, size, start[0], start[1], end[0], end[1], (0.05, 0.05, 0.05, 1.0), 2)


def _draw_preview_raster(
    canvas: list[float],
    size: int,
    rect: Rect,
    draw_rect: Callable[..., None],
    draw_line: Callable[[list[float], int, int, int, int, int, Color], None],
    clamp_rect: Callable[[Rect, int], Rect],
) -> None:
    draw_rect(canvas, size, rect, (0.70, 0.75, 0.78, 1.0), fill=False)
    left, bottom, right, top = clamp_rect(rect, size)
    for i in range(5):
        y = bottom + int(round((i + 1) * max(1, top - bottom) / 6.0))
        y_offset = 2 if i % 2 else -2
        draw_line(canvas, size, left + 4, y, right - 4, y + y_offset, (0.12, 0.12, 0.12, 1.0))


def _float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return default
