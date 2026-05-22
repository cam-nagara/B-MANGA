"""Viewport overlay for drag-create ranges."""

from __future__ import annotations

from collections.abc import Callable

from ..utils import viewport_colors
from ..utils.geom import Rect

DrawRectFill = Callable[[Rect, tuple[float, float, float, float]], None]
DrawRectOutline = Callable[..., None]

_HANDLE_SIZE_MM = 2.0
_creation_bounds: tuple[float, float, float, float] | None = None


def set_bounds(bounds: tuple[float, float, float, float] | None) -> None:
    global _creation_bounds
    if bounds is None:
        _creation_bounds = None
        return
    x, y, w, h = bounds
    _creation_bounds = (float(x), float(y), max(0.001, float(w)), max(0.001, float(h)))


def clear() -> None:
    set_bounds(None)


def current_bounds() -> tuple[float, float, float, float] | None:
    return _creation_bounds


def _handle_rects(rect: Rect) -> list[Rect]:
    half = _HANDLE_SIZE_MM * 0.5
    points = (
        (rect.x, rect.y),
        (rect.x + rect.width * 0.5, rect.y),
        (rect.x2, rect.y),
        (rect.x, rect.y + rect.height * 0.5),
        (rect.x2, rect.y + rect.height * 0.5),
        (rect.x, rect.y2),
        (rect.x + rect.width * 0.5, rect.y2),
        (rect.x2, rect.y2),
    )
    return [Rect(x - half, y - half, _HANDLE_SIZE_MM, _HANDLE_SIZE_MM) for x, y in points]


def draw(
    *,
    draw_rect_fill: DrawRectFill,
    draw_rect_outline: DrawRectOutline,
) -> None:
    bounds = _creation_bounds
    if bounds is None:
        return
    rect = Rect(float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    draw_rect_outline(rect.inset(-1.0), viewport_colors.SELECTION, width_mm=0.50)
    for handle in _handle_rects(rect):
        draw_rect_fill(handle, viewport_colors.HANDLE_FILL)
        draw_rect_outline(handle, viewport_colors.HANDLE_OUTLINE, width_mm=0.25)
