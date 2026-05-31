"""Asset Browser thumbnail generation for B-Name assets."""

from __future__ import annotations

import bpy

from .geom import m_to_mm
from . import log

_logger = log.get_logger(__name__)

ASSET_PREVIEW_SIZE = 128


def set_collection_asset_preview(
    coll: bpy.types.Collection,
    *,
    payload: dict | None = None,
) -> None:
    try:
        pixels = _asset_preview_pixels(
            payload=payload,
            objects=list(getattr(coll, "objects", []) or []),
        )
        preview = coll.preview_ensure()
        preview.image_size = (ASSET_PREVIEW_SIZE, ASSET_PREVIEW_SIZE)
        preview.image_pixels_float = pixels
        preview.icon_size = (ASSET_PREVIEW_SIZE, ASSET_PREVIEW_SIZE)
        preview.icon_pixels_float = pixels
    except Exception:  # noqa: BLE001
        _logger.exception("asset preview generation failed")


def _asset_preview_pixels(
    *,
    payload: dict | None,
    objects: list[bpy.types.Object],
) -> list[float]:
    size = ASSET_PREVIEW_SIZE
    canvas = _preview_canvas(size)
    entries = [
        entry
        for entry in (payload or {}).get("entries", []) or []
        if isinstance(entry, dict)
    ]
    boxes = [_preview_bounds_for_entry(entry) for entry in entries]
    if not any(boxes):
        boxes = [_preview_bounds_for_object(obj) for obj in objects]
    transform = _preview_transform([box for box in boxes if box is not None], size)
    _draw_preview_background(canvas, size)
    if entries:
        for entry in entries:
            box = _preview_bounds_for_entry(entry)
            if box is None:
                continue
            rect = _map_preview_rect(box, transform)
            kind = str(entry.get("kind", "") or "")
            if kind == "balloon":
                _draw_preview_balloon(canvas, size, rect, entry)
            elif kind == "text":
                _draw_preview_text(canvas, size, rect)
            elif kind == "effect":
                _draw_preview_effect(canvas, size, rect)
            else:
                _draw_preview_rect(
                    canvas,
                    size,
                    rect,
                    (0.35, 0.38, 0.42, 1.0),
                    fill=False,
                )
    else:
        for box in boxes:
            if box is not None:
                _draw_preview_rect(
                    canvas,
                    size,
                    _map_preview_rect(box, transform),
                    (0.15, 0.16, 0.18, 1.0),
                    fill=False,
                )
    return canvas


def _preview_canvas(size: int) -> list[float]:
    return [1.0, 1.0, 1.0, 1.0] * (size * size)


def _draw_preview_background(canvas: list[float], size: int) -> None:
    for y in range(size):
        for x in range(size):
            shade = 0.94 if ((x // 12) + (y // 12)) % 2 == 0 else 0.88
            _set_preview_pixel(canvas, size, x, y, (shade, shade, shade, 1.0))
    _draw_preview_rect(
        canvas,
        size,
        (6, 6, size - 7, size - 7),
        (0.78, 0.82, 0.85, 1.0),
        fill=False,
    )


def _preview_bounds_for_entry(entry: dict) -> tuple[float, float, float, float] | None:
    bounds = entry.get("bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
        data = entry.get("data")
        if not isinstance(data, dict):
            return None
        bounds = (
            data.get("x_mm", 0.0),
            data.get("y_mm", 0.0),
            data.get("width_mm", 30.0),
            data.get("height_mm", 20.0),
        )
    try:
        x, y, w, h = (
            float(bounds[0]),
            float(bounds[1]),
            float(bounds[2]),
            float(bounds[3]),
        )
    except Exception:  # noqa: BLE001
        return None
    if w <= 0.0 or h <= 0.0:
        return None
    return x, y, w, h


def _preview_bounds_for_object(obj: bpy.types.Object) -> tuple[float, float, float, float] | None:
    try:
        x = m_to_mm(float(obj.location.x))
        y = m_to_mm(float(obj.location.y))
    except Exception:  # noqa: BLE001
        return None
    return x - 15.0, y - 15.0, 30.0, 30.0


def _preview_transform(
    boxes: list[tuple[float, float, float, float]],
    size: int,
) -> tuple[float, float, float]:
    if not boxes:
        return 1.0, 0.0, 0.0
    min_x = min(x for x, _y, _w, _h in boxes)
    min_y = min(y for _x, y, _w, _h in boxes)
    max_x = max(x + w for x, _y, w, _h in boxes)
    max_y = max(y + h for _x, y, _w, h in boxes)
    span_x = max(1.0, max_x - min_x)
    span_y = max(1.0, max_y - min_y)
    margin = 18.0
    scale = min((size - margin * 2.0) / span_x, (size - margin * 2.0) / span_y)
    offset_x = (size - span_x * scale) * 0.5 - min_x * scale
    offset_y = (size - span_y * scale) * 0.5 - min_y * scale
    return scale, offset_x, offset_y


def _map_preview_rect(
    box: tuple[float, float, float, float],
    transform: tuple[float, float, float],
) -> tuple[int, int, int, int]:
    scale, offset_x, offset_y = transform
    x, y, w, h = box
    left = int(round(x * scale + offset_x))
    right = int(round((x + w) * scale + offset_x))
    bottom = int(round(y * scale + offset_y))
    top = int(round((y + h) * scale + offset_y))
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def _draw_preview_balloon(
    canvas: list[float],
    size: int,
    rect: tuple[int, int, int, int],
    entry: dict,
) -> None:
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    shape = str(data.get("shape", "ellipse") or "ellipse")
    if shape in {"rect", "octagon"}:
        _draw_preview_rect(canvas, size, rect, (1.0, 1.0, 1.0, 1.0), fill=True)
        _draw_preview_rect(canvas, size, rect, (0.05, 0.05, 0.05, 1.0), fill=False)
    else:
        _draw_preview_ellipse(canvas, size, rect, (1.0, 1.0, 1.0, 1.0), fill=True)
        _draw_preview_ellipse(canvas, size, rect, (0.05, 0.05, 0.05, 1.0), fill=False)


def _draw_preview_text(canvas: list[float], size: int, rect: tuple[int, int, int, int]) -> None:
    left, bottom, right, top = rect
    height = max(1, top - bottom)
    count = max(2, min(5, height // 7))
    for i in range(count):
        y = bottom + int(round((i + 1) * height / (count + 1)))
        _draw_preview_line(canvas, size, left + 2, y, right - 2, y, (0.08, 0.08, 0.08, 1.0))


def _draw_preview_effect(canvas: list[float], size: int, rect: tuple[int, int, int, int]) -> None:
    left, bottom, right, top = rect
    cx = (left + right) // 2
    cy = (bottom + top) // 2
    for i in range(18):
        t = i / 18.0
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
        _draw_preview_line(canvas, size, cx, cy, x, y, (0.1, 0.1, 0.1, 1.0))


def _draw_preview_rect(
    canvas: list[float],
    size: int,
    rect: tuple[int, int, int, int],
    color: tuple[float, float, float, float],
    *,
    fill: bool,
) -> None:
    left, bottom, right, top = _clamp_preview_rect(rect, size)
    if fill:
        for y in range(bottom, top + 1):
            for x in range(left, right + 1):
                _set_preview_pixel(canvas, size, x, y, color)
        return
    _draw_preview_line(canvas, size, left, bottom, right, bottom, color)
    _draw_preview_line(canvas, size, right, bottom, right, top, color)
    _draw_preview_line(canvas, size, right, top, left, top, color)
    _draw_preview_line(canvas, size, left, top, left, bottom, color)


def _draw_preview_ellipse(
    canvas: list[float],
    size: int,
    rect: tuple[int, int, int, int],
    color: tuple[float, float, float, float],
    *,
    fill: bool,
) -> None:
    left, bottom, right, top = _clamp_preview_rect(rect, size)
    cx = (left + right) * 0.5
    cy = (bottom + top) * 0.5
    rx = max(1.0, (right - left) * 0.5)
    ry = max(1.0, (top - bottom) * 0.5)
    for y in range(bottom, top + 1):
        for x in range(left, right + 1):
            value = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            if (fill and value <= 1.0) or (not fill and 0.86 <= value <= 1.16):
                _set_preview_pixel(canvas, size, x, y, color)


def _draw_preview_line(
    canvas: list[float],
    size: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[float, float, float, float],
) -> None:
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        _set_preview_pixel(canvas, size, x, y, color)
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def _clamp_preview_rect(rect: tuple[int, int, int, int], size: int) -> tuple[int, int, int, int]:
    left, bottom, right, top = rect
    return (
        max(0, min(size - 1, left)),
        max(0, min(size - 1, bottom)),
        max(0, min(size - 1, right)),
        max(0, min(size - 1, top)),
    )


def _set_preview_pixel(
    canvas: list[float],
    size: int,
    x: int,
    y: int,
    color: tuple[float, float, float, float],
) -> None:
    if not (0 <= x < size and 0 <= y < size):
        return
    idx = (y * size + x) * 4
    canvas[idx:idx + 4] = color
