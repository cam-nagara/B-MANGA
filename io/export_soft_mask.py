"""Soft mask helpers for page export."""

from __future__ import annotations

from typing import Sequence

from ..utils import coma_blur_curve
from ..utils.geom import mm_to_px


def brush_edge_enabled(entry) -> bool:
    border = getattr(entry, "border", None)
    if border is None:
        return False
    return (
        bool(getattr(entry, "paper_visible", True))
        and bool(getattr(border, "visible", True))
        and str(getattr(border, "style", "solid") or "solid") == "brush"
        and float(getattr(border, "width_mm", 0.0) or 0.0) > 0.0
    )


def brush_soft_width_mm(entry) -> float:
    border = getattr(entry, "border", None)
    if border is None:
        return 0.0
    line_w = max(0.0, float(getattr(border, "width_mm", 0.0) or 0.0))
    blur = max(0.0, min(1.0, float(getattr(border, "blur_amount", 0.0) or 0.0)))
    if line_w <= 0.0:
        return 0.0
    return line_w * 0.5 * blur


def local_points_px(
    points_mm: Sequence[tuple[float, float]],
    bbox_mm: tuple[float, float, float, float],
    size: tuple[int, int],
) -> list[tuple[int, int]]:
    min_x, min_y, max_x, max_y = bbox_mm
    width_px, height_px = size
    box_w = max(max_x - min_x, 1.0e-6)
    box_h = max(max_y - min_y, 1.0e-6)
    out: list[tuple[int, int]] = []
    for x_mm, y_mm in points_mm:
        x = int(round(((float(x_mm) - min_x) / box_w) * max(0, width_px - 1)))
        y = int(round(((max_y - float(y_mm)) / box_h) * max(0, height_px - 1)))
        out.append((x, y))
    return out


def local_box_px(
    inner_bbox_mm: tuple[float, float, float, float],
    outer_bbox_mm: tuple[float, float, float, float],
    size: tuple[int, int],
) -> tuple[int, int, int, int]:
    min_x, min_y, max_x, max_y = inner_bbox_mm
    pts = local_points_px(
        [(min_x, max_y), (max_x, min_y)],
        outer_bbox_mm,
        size,
    )
    left = min(pts[0][0], pts[1][0])
    right = max(pts[0][0], pts[1][0]) + 1
    top = min(pts[0][1], pts[1][1])
    bottom = max(pts[0][1], pts[1][1]) + 1
    width, height = size
    return (
        max(0, min(width, left)),
        max(0, min(height, top)),
        max(0, min(width, right)),
        max(0, min(height, bottom)),
    )


def expand_bbox(
    bbox_mm: tuple[float, float, float, float],
    pad_mm: float,
) -> tuple[float, float, float, float]:
    pad = max(0.0, float(pad_mm))
    return (
        float(bbox_mm[0]) - pad,
        float(bbox_mm[1]) - pad,
        float(bbox_mm[2]) + pad,
        float(bbox_mm[3]) + pad,
    )


def coma_shape_mask(Image, ImageDraw, points_mm, bbox_mm, size) -> object:
    mask = Image.new("L", size, 0)
    pts = local_points_px(points_mm, bbox_mm, size)
    if len(pts) >= 3:
        ImageDraw.Draw(mask).polygon(pts, fill=255)
    return mask


def _blur_curve_points(entry) -> tuple[tuple[float, float], ...]:
    border = getattr(entry, "border", None)
    return coma_blur_curve.parse_points(getattr(border, "blur_curve_points", None))


def _evaluate_blur_curve(points: Sequence[tuple[float, float]], value: float) -> float:
    x = max(0.0, min(1.0, float(value)))
    if not points:
        return x
    if x <= points[0][0]:
        return max(0.0, min(1.0, points[0][1]))
    previous = points[0]
    for current in points[1:]:
        if x <= current[0]:
            span = max(1.0e-6, current[0] - previous[0])
            factor = max(0.0, min(1.0, (x - previous[0]) / span))
            factor = factor * factor * (3.0 - 2.0 * factor)
            y = previous[1] + (current[1] - previous[1]) * factor
            return max(0.0, min(1.0, y))
        previous = current
    return max(0.0, min(1.0, points[-1][1]))


def apply_blur_curve_to_mask(mask, entry) -> object:
    points = _blur_curve_points(entry)
    lut = [int(round(_evaluate_blur_curve(points, value / 255.0) * 255.0)) for value in range(256)]
    return mask.point(lut)


def coma_soft_edge_mask(Image, ImageChops, ImageDraw, ImageFilter, entry, points_mm, bbox_mm, size, dpi: int) -> object:
    hard = coma_shape_mask(Image, ImageDraw, points_mm, bbox_mm, size)
    if not brush_edge_enabled(entry):
        return hard
    soft_width = brush_soft_width_mm(entry)
    if soft_width <= 0.0:
        return hard
    radius_px = max(1, int(round(mm_to_px(soft_width, dpi) * 0.35)))
    soft = hard.filter(ImageFilter.GaussianBlur(radius=radius_px))
    soft = ImageChops.multiply(soft, hard)
    soft = apply_blur_curve_to_mask(soft, entry)
    if bool(getattr(getattr(entry, "border", None), "blur_dither", False)):
        soft = soft.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")
    return soft


def apply_mask_alpha(Image, image, mask, alpha_scale: int = 255):
    out = image.convert("RGBA")
    alpha_scale = max(0, min(255, int(alpha_scale)))
    alpha = mask
    if alpha_scale < 255:
        alpha = mask.point(lambda px: int(round(px * (alpha_scale / 255.0))))
    out.putalpha(alpha)
    return out
