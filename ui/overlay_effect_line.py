"""Viewport overlay drawing for selected B-MANGA effect-line layers."""

from __future__ import annotations

from collections.abc import Callable

from ..utils import object_selection, viewport_colors
from ..utils.geom import Rect, m_to_mm

DrawRectFill = Callable[[Rect, tuple[float, float, float, float]], None]
DrawRectOutline = Callable[..., None]
DrawSegmentsMM = Callable[
    [list[tuple[tuple[float, float], tuple[float, float]]], tuple[float, float, float, float], float],
    None,
]

_CENTER_CROSS_SIZE_MM = 8.0
_CENTER_CROSS_WIDTH_MM = 0.6
_SHAPE_GUIDE_WIDTH_MM = 0.18
_START_GUIDE_COLOR = (0.0, 0.82, 0.95, 0.85)
_END_GUIDE_COLOR = (1.0, 0.0, 0.68, 0.90)


def draw_active_effect_line_bounds(
    context,
    *,
    draw_rect_fill: DrawRectFill,
    draw_rect_outline: DrawRectOutline,
    draw_segments_mm: DrawSegmentsMM | None = None,
    logger=None,
) -> None:
    selected_names = object_selection.selected_effect_names(context)
    active_effect = getattr(context.scene, "bmanga_active_layer_kind", "") == "effect"
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
            _draw_shape_guides(
                context,
                obj,
                layer,
                bounds,
                world_bounds,
                center,
                draw_segments_mm=draw_segments_mm,
                logger=logger,
            )
            _draw_center_cross(
                Rect(*map(float, world_bounds)),
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
                    _draw_shape_guides(
                        context,
                        obj,
                        selected_layer,
                        selected_bounds,
                        world_bounds,
                        center,
                        draw_segments_mm=draw_segments_mm,
                        logger=logger,
                    )
                    _draw_center_cross(
                        Rect(*map(float, world_bounds)),
                        center_xy=world_center,
                        draw_rect_fill=draw_rect_fill,
                        draw_rect_outline=draw_rect_outline,
                    )


def _draw_shape_guides(
    context,
    obj,
    layer,
    bounds,
    world_bounds,
    center_xy,
    *,
    draw_segments_mm: DrawSegmentsMM | None,
    logger=None,
) -> None:
    if draw_segments_mm is None or obj is None or layer is None or bounds is None or world_bounds is None:
        return
    try:
        from ..operators import effect_line_gen, effect_line_op

        params = effect_line_op._params_for_write(context, obj, layer)
        if params is None:
            return
        bx, by, bw, bh = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
        shape_center = (bx + bw * 0.5, by + bh * 0.5)
        focus_center = center_xy if center_xy is not None else shape_center
        start_outline, start_extend = effect_line_op._start_frame_outline_for_bounds(context, params, focus_center)
        guides = effect_line_gen.generate_shape_guide_strokes(
            params,
            center_xy_mm=focus_center,
            radius_xy_mm=(bw * 0.5, bh * 0.5),
            start_outline_mm=start_outline,
            start_extend_mm=start_extend,
            seed=effect_line_op._seed_for_layer(obj, layer),
            end_center_xy_mm=shape_center,
        )
    except Exception:  # noqa: BLE001
        if logger is not None:
            logger.exception("effect line shape guide draw failed")
        return

    offset_x = float(world_bounds[0]) - float(bounds[0])
    offset_y = float(world_bounds[1]) - float(bounds[1])
    for guide in guides:
        points = getattr(guide, "points_xyz", None) or []
        if len(points) < 2:
            continue
        pts = [(m_to_mm(float(p[0])) + offset_x, m_to_mm(float(p[1])) + offset_y) for p in points]
        segments = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        if bool(getattr(guide, "cyclic", False)):
            segments.append((pts[-1], pts[0]))
        if not segments:
            continue
        role = str(getattr(guide, "role", "") or "")
        color = _END_GUIDE_COLOR if role == "end_guide" else _START_GUIDE_COLOR
        try:
            width_mm = max(_SHAPE_GUIDE_WIDTH_MM, m_to_mm(float(getattr(guide, "radius", 0.0))) * 2.0)
        except Exception:  # noqa: BLE001
            width_mm = _SHAPE_GUIDE_WIDTH_MM
        draw_segments_mm(segments, color, width_mm)


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
