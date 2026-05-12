"""Viewport overlay drawing for selected B-Name effect-line layers."""

from __future__ import annotations

from collections.abc import Callable

from ..utils import object_selection, viewport_colors
from ..utils.geom import Rect

DrawRectFill = Callable[[Rect, tuple[float, float, float, float]], None]
DrawRectOutline = Callable[..., None]

_HANDLE_SIZE_MM = 2.0
_CENTER_CROSS_SIZE_MM = 8.0
_CENTER_CROSS_WIDTH_MM = 0.6


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


def draw_active_effect_line_bounds(
    context,
    *,
    draw_rect_fill: DrawRectFill,
    draw_rect_outline: DrawRectOutline,
    logger=None,
) -> None:
    selected_names = object_selection.selected_effect_names(context)
    active_effect = getattr(context.scene, "bname_active_layer_kind", "") == "effect"
    if not active_effect and not selected_names:
        return
    try:
        from ..operators import effect_line_op

        obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
    except Exception:  # noqa: BLE001
        if logger is not None:
            logger.exception("active effect bounds resolve failed")
        return
    drawn: set[str] = set()
    if active_effect and bounds is not None:
        world_bounds = effect_line_op.effect_layer_world_bounds(context, obj, layer, bounds)
        if world_bounds is not None:
            center = effect_line_op.effect_layer_center(obj, layer, bounds)
            world_center = effect_line_op.effect_layer_world_point(context, obj, center, layer)
            _draw_bounds(
                world_bounds,
                center_xy=world_center,
                draw_rect_fill=draw_rect_fill,
                draw_rect_outline=draw_rect_outline,
            )
        if layer is not None:
            drawn.add(str(getattr(layer, "name", "") or ""))
            drawn.add(object_selection.parse_key(object_selection.effect_key(layer))[2])
    if selected_names:
        for selected_name in selected_names:
            if selected_name in drawn:
                continue
            obj, selected_layer = effect_line_op.layer_stack_utils._find_effect_layer_by_key(selected_name)
            if obj is None or selected_layer is None:
                continue
            selected_bounds = effect_line_op.effect_layer_bounds(obj, selected_layer)
            if selected_bounds is not None:
                world_bounds = effect_line_op.effect_layer_world_bounds(
                    context,
                    obj,
                    selected_layer,
                    selected_bounds,
                )
                if world_bounds is not None:
                    center = effect_line_op.effect_layer_center(obj, selected_layer, selected_bounds)
                    world_center = effect_line_op.effect_layer_world_point(context, obj, center, selected_layer)
                    _draw_bounds(
                        world_bounds,
                        center_xy=world_center,
                        draw_rect_fill=draw_rect_fill,
                        draw_rect_outline=draw_rect_outline,
                    )


def _draw_bounds(
    bounds,
    *,
    center_xy=None,
    draw_rect_fill: DrawRectFill,
    draw_rect_outline: DrawRectOutline,
) -> None:
    rect = Rect(float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    draw_rect_outline(rect.inset(-1.0), viewport_colors.SELECTION, width_mm=0.50)
    _draw_center_cross(
        rect,
        center_xy=center_xy,
        draw_rect_fill=draw_rect_fill,
        draw_rect_outline=draw_rect_outline,
    )
    for handle in _handle_rects(rect):
        draw_rect_fill(handle, viewport_colors.HANDLE_FILL)
        draw_rect_outline(handle, viewport_colors.HANDLE_OUTLINE, width_mm=0.25)


def _draw_center_cross(
    rect: Rect,
    *,
    center_xy=None,
    draw_rect_fill: DrawRectFill,
    draw_rect_outline: DrawRectOutline,
) -> None:
    if center_xy is None:
        cx = rect.x + rect.width * 0.5
        cy = rect.y + rect.height * 0.5
    else:
        cx = float(center_xy[0])
        cy = float(center_xy[1])
    half = _CENTER_CROSS_SIZE_MM * 0.5
    bar = max(0.2, _CENTER_CROSS_WIDTH_MM)
    horizontal = Rect(cx - half, cy - bar * 0.5, _CENTER_CROSS_SIZE_MM, bar)
    vertical = Rect(cx - bar * 0.5, cy - half, bar, _CENTER_CROSS_SIZE_MM)
    for marker in (horizontal, vertical):
        draw_rect_fill(marker, viewport_colors.SELECTION_STRONG)
        draw_rect_outline(marker, viewport_colors.HANDLE_OUTLINE, width_mm=0.12)
