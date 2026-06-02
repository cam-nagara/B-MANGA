"""フキダシのラスタ書き出しヘルパ."""

from __future__ import annotations

import math
from typing import Sequence

from ..utils import balloon_shapes, balloon_tail_geom, line_pattern, percentage
from ..utils.geom import Rect, mm_to_px


def _ep():
    from . import export_pipeline

    return export_pipeline


def _outline_rect(rect: Rect) -> list[tuple[float, float]]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _outline_rounded_rect(rect: Rect, radius_mm: float, segments: int = 8) -> list[tuple[float, float]]:
    radius = max(0.0, min(float(radius_mm), rect.width * 0.5, rect.height * 0.5))
    if radius <= 0.0:
        return _outline_rect(rect)
    corners = (
        (rect.x2 - radius, rect.y2 - radius, 0.0),
        (rect.x + radius, rect.y2 - radius, math.pi * 0.5),
        (rect.x + radius, rect.y + radius, math.pi),
        (rect.x2 - radius, rect.y + radius, math.pi * 1.5),
    )
    pts: list[tuple[float, float]] = []
    for cx, cy, start in corners:
        for step in range(segments + 1):
            angle = start + (math.pi * 0.5) * (step / segments)
            pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _outline_ellipse(rect: Rect, segments: int = 64) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    return [
        (cx + rx * math.cos(2 * math.pi * i / segments),
         cy + ry * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def _outline_cloud(rect: Rect, wave_count: int, amplitude_mm: float,
                   segments_per_wave: int = 6) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5 - amplitude_mm)
    ry = max(1.0, rect.height * 0.5 - amplitude_mm)
    total = max(8, int(wave_count) * max(1, int(segments_per_wave)))
    pts: list[tuple[float, float]] = []
    for i in range(total):
        angle = 2 * math.pi * i / total
        bump = amplitude_mm * (0.5 + 0.5 * math.cos(wave_count * angle))
        radius_factor = 1.0 + bump / max(1.0, min(rx, ry))
        pts.append((cx + rx * math.cos(angle) * radius_factor, cy + ry * math.sin(angle) * radius_factor))
    return pts


def _outline_spike(rect: Rect, spike_count: int, depth_mm: float, *, smooth: bool) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5)
    ry = max(1.0, rect.height * 0.5)
    total = max(6, int(spike_count) * 2)
    pts: list[tuple[float, float]] = []
    for i in range(total):
        angle = 2 * math.pi * i / total
        factor = 1.0 if i % 2 == 0 else max(0.05, 1.0 - depth_mm / max(rx, ry))
        pts.append((cx + rx * math.cos(angle) * factor, cy + ry * math.sin(angle) * factor))
    if smooth and len(pts) >= 3:
        smoothed = []
        for i in range(len(pts)):
            prev_pt = pts[(i - 1) % len(pts)]
            cur_pt = pts[i]
            next_pt = pts[(i + 1) % len(pts)]
            smoothed.append(((prev_pt[0] + 2 * cur_pt[0] + next_pt[0]) * 0.25,
                             (prev_pt[1] + 2 * cur_pt[1] + next_pt[1]) * 0.25))
        pts = smoothed
    return pts


def _outline_polygon_pct(rect: Rect, pct_pts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [
        (rect.x + rect.width * (px / 100.0),
         rect.y + rect.height * ((100.0 - py) / 100.0))
        for px, py in pct_pts
    ]


def _outline_pill(rect: Rect, segments: int = 16) -> list[tuple[float, float]]:
    radius = min(rect.width, rect.height) * 0.5
    if radius <= 0.0:
        return _outline_rect(rect)
    cx_left = rect.x + radius
    cx_right = rect.x2 - radius
    cy = (rect.y + rect.y2) * 0.5
    pts: list[tuple[float, float]] = []
    for step in range(segments + 1):
        angle = -math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_right + radius * math.cos(angle), cy + radius * math.sin(angle)))
    for step in range(segments + 1):
        angle = math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_left + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _outline_diamond(rect: Rect) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    return [(cx, rect.y2), (rect.x2, cy), (cx, rect.y), (rect.x, cy)]


def _outline_hexagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(25, 0), (75, 0), (100, 50), (75, 100), (25, 100), (0, 50)])


def _outline_octagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(12, 0), (88, 0), (100, 12), (100, 88), (88, 100), (12, 100), (0, 88), (0, 12)])


def _outline_star(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(
        rect,
        [(50, 0), (61, 35), (98, 35), (68, 57), (79, 91),
         (50, 70), (21, 91), (32, 57), (2, 35), (39, 35)],
    )


def _outline_fluffy(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(
        rect,
        [(50, 3), (70, 8), (88, 16), (96, 30), (92, 50), (96, 70),
         (88, 84), (70, 92), (50, 97), (30, 92), (12, 84), (4, 70),
         (8, 50), (4, 30), (12, 16), (30, 8)],
    )


def _balloon_outline_mm(entry, rect: Rect) -> list[tuple[float, float]]:
    return balloon_shapes.outline_for_entry(entry, rect)


def _balloon_fill_outline_mm(entry, rect: Rect) -> list[tuple[float, float]]:
    return _balloon_outline_mm(entry, rect)


def _apply_balloon_transforms(
    pts: Sequence[tuple[float, float]],
    rect: Rect,
    flip_h: bool,
    flip_v: bool,
    rotation_deg: float,
) -> list[tuple[float, float]]:
    if not (flip_h or flip_v or abs(rotation_deg) > 1e-6):
        return list(pts)
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    sx = -1.0 if flip_h else 1.0
    sy = -1.0 if flip_v else 1.0
    cos_r = math.cos(math.radians(rotation_deg))
    sin_r = math.sin(math.radians(rotation_deg))
    out = []
    for x, y in pts:
        dx = (x - cx) * sx
        dy = (y - cy) * sy
        rx = dx * cos_r - dy * sin_r
        ry = dx * sin_r + dy * cos_r
        out.append((cx + rx, cy + ry))
    return out


def _balloon_tail_polygon(rect: Rect, tail) -> list[tuple[float, float]]:
    return balloon_tail_geom.polygon_for_tail(rect, tail)


def _entry_opacity(entry) -> float:
    return percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)


def _fill_opacity(entry) -> float:
    return _entry_opacity(entry) * percentage.percent_to_factor(getattr(entry, "fill_opacity", 100.0), 100.0)


def _fill_source_image(size: tuple[int, int], entry):
    ep = _ep()
    if not bool(getattr(entry, "fill_gradient_enabled", False)):
        color = ep._rgb255(getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0)), alpha=1.0)
        return ep.Image.new("RGBA", size, color)
    start = ep._rgb255(getattr(entry, "fill_gradient_start_color", getattr(entry, "fill_color", (1, 1, 1, 1))), alpha=1.0)
    end = ep._rgb255(getattr(entry, "fill_gradient_end_color", getattr(entry, "fill_color", (1, 1, 1, 1))), alpha=1.0)
    width, height = size
    image = ep.Image.new("RGBA", size, start)
    if width <= 0 or height <= 0:
        return image
    angle = math.radians(float(getattr(entry, "fill_gradient_angle_deg", 90.0) or 90.0))
    ux = math.cos(angle)
    uy = -math.sin(angle)
    corners = [(0.0, 0.0), (float(width - 1), 0.0), (0.0, float(height - 1)), (float(width - 1), float(height - 1))]
    dots = [x * ux + y * uy for x, y in corners]
    mn = min(dots)
    span = max(1.0e-6, max(dots) - mn)
    pixels = []
    for y in range(height):
        for x in range(width):
            t = max(0.0, min(1.0, ((x * ux + y * uy) - mn) / span))
            pixels.append(tuple(int(round(start[i] + (end[i] - start[i]) * t)) for i in range(4)))
    image.putdata(pixels)
    return image


def _fill_mask(canvas, polygons_px: list[list[tuple[int, int]]]):
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None:
        return None
    mask = ep.Image.new("L", canvas.image.size, 0)
    draw_mask = ep.ImageDraw.Draw(mask)
    for pts in polygons_px:
        if len(pts) >= 3:
            draw_mask.polygon(pts, fill=255)
    return mask


def _draw_fill_layer(canvas, entry, polygons_px: list[list[tuple[int, int]]], dpi: int):
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None:
        return None
    hard = _fill_mask(canvas, polygons_px)
    if hard is None:
        return None
    mask = hard
    blur = max(0.0, min(1.0, float(getattr(entry, "fill_blur_amount", 0.0) or 0.0)))
    if blur > 0.0 and ep.ImageFilter is not None and ep.ImageChops is not None:
        line_w = max(0.3, float(getattr(entry, "line_width_mm", 0.3) or 0.3))
        radius_px = max(1, int(round(mm_to_px(max(0.15, line_w * (0.65 + 3.35 * blur)), dpi) * 0.35)))
        mask = hard.filter(ep.ImageFilter.GaussianBlur(radius=radius_px))
        mask = ep.ImageChops.multiply(mask, hard)
        if bool(getattr(entry, "fill_blur_dither", False)):
            mask = mask.convert("1", dither=ep.Image.FLOYDSTEINBERG).convert("L")
    fill_alpha = float(getattr(entry, "fill_color", (1, 1, 1, 1))[3])
    alpha_scale = max(0, min(255, int(round(255.0 * _fill_opacity(entry) * fill_alpha))))
    if alpha_scale < 255:
        mask = mask.point(lambda px: int(round(px * (alpha_scale / 255.0))))
    fill_image = _fill_source_image(canvas.image.size, entry)
    fill_image.putalpha(mask)
    canvas.image.alpha_composite(fill_image)
    return hard


def _draw_white_loop(draw, pts, color, width_px: int, style: str) -> None:
    if width_px <= 0 or len(pts) < 2:
        return
    _ep()._draw_styled_loop(draw, pts, color, width_px, style)


def _draw_inner_white_loop(canvas, clip_mask, pts, color, width_px: int, style: str) -> None:
    ep = _ep()
    if clip_mask is None or ep.Image is None or ep.ImageDraw is None:
        return
    temp = ep.Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
    draw = ep.ImageDraw.Draw(temp)
    _draw_white_loop(draw, pts, color, width_px, style)
    alpha = temp.getchannel("A")
    if ep.ImageChops is not None:
        alpha = ep.ImageChops.multiply(alpha, clip_mask)
    else:
        alpha = alpha.point(lambda px: px)
    temp.putalpha(alpha)
    canvas.image.alpha_composite(temp)


def _flash_white_line_width_px(entry, line_w_mm: float, dpi: int) -> int:
    if not balloon_shapes.is_flash_line_style(getattr(entry, "line_style", "")):
        return 0
    if not bool(getattr(entry, "flash_white_line_enabled", True)):
        return 0
    white_width_pct = max(0.0, min(300.0, float(getattr(entry, "flash_white_line_width_percent", 100.0) or 0.0)))
    white_peak_pct = max(0.0, min(200.0, float(getattr(entry, "flash_white_line_peak_width_pct", 100.0) or 0.0)))
    width_mm = max(0.0, float(line_w_mm)) * white_width_pct * white_peak_pct / 10000.0
    if width_mm <= 1.0e-6:
        return 0
    return max(1, int(round(mm_to_px(width_mm, dpi) * 2.0)))


def _entry_fill_rgb255(entry):
    return _ep()._rgb255(getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0)), alpha=_fill_opacity(entry))


def _loop_cumulative_px(pts) -> tuple[list[tuple[float, float]], list[float]]:
    loop = [(float(x), float(y)) for x, y in pts]
    if len(loop) >= 2 and math.hypot(loop[0][0] - loop[-1][0], loop[0][1] - loop[-1][1]) > 1.0e-6:
        loop.append(loop[0])
    cum = [0.0]
    for index in range(1, len(loop)):
        cum.append(cum[-1] + math.hypot(loop[index][0] - loop[index - 1][0], loop[index][1] - loop[index - 1][1]))
    return loop, cum


def _point_on_loop_px(loop, cum, target: float) -> tuple[float, float] | None:
    if len(loop) < 2 or len(cum) != len(loop) or cum[-1] <= 1.0e-6:
        return None
    target = max(0.0, min(float(target), float(cum[-1])))
    for index in range(len(loop) - 1):
        start = float(cum[index])
        end = float(cum[index + 1])
        if target > end and index < len(loop) - 2:
            continue
        seg_len = end - start
        if seg_len <= 1.0e-6:
            continue
        p0 = loop[index]
        p1 = loop[index + 1]
        t = (target - start) / seg_len
        return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
    return loop[-1]


def _loop_subset_px(loop, cum, start_len: float, end_len: float) -> list[tuple[float, float]]:
    if len(loop) < 2 or len(cum) != len(loop):
        return []
    total = float(cum[-1])
    if total <= 1.0e-6:
        return []
    start_len = max(0.0, float(start_len))
    end_len = min(total, max(start_len, float(end_len)))
    out: list[tuple[float, float]] = []
    for index in range(len(loop) - 1):
        seg_start = float(cum[index])
        seg_end = float(cum[index + 1])
        if seg_end < start_len or seg_start > end_len:
            continue
        seg_len = seg_end - seg_start
        if seg_len <= 1.0e-6:
            continue
        p0 = loop[index]
        p1 = loop[index + 1]
        t0 = (max(seg_start, start_len) - seg_start) / seg_len
        t1 = (min(seg_end, end_len) - seg_start) / seg_len
        x0 = p0[0] + (p1[0] - p0[0]) * t0
        y0 = p0[1] + (p1[1] - p0[1]) * t0
        x1 = p0[0] + (p1[0] - p0[0]) * t1
        y1 = p0[1] + (p1[1] - p0[1]) * t1
        if not out or math.hypot(out[-1][0] - x0, out[-1][1] - y0) > 1.0e-6:
            out.append((x0, y0))
        if math.hypot(out[-1][0] - x1, out[-1][1] - y1) > 1.0e-6:
            out.append((x1, y1))
    return out


def _draw_pattern_loop(draw, pts, entry, color, width_px: int, dpi: int, style: str) -> None:
    loop, cum = _loop_cumulative_px(pts)
    if len(loop) < 2 or cum[-1] <= 1.0e-6:
        return
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.3))
    if style == "dotted":
        diameter_px = max(1.0, float(width_px))
        gap_px = max(0.0, float(mm_to_px(line_pattern.dotted_gap_mm(entry, line_width_mm), dpi)))
        spacing_px = max(diameter_px + gap_px, diameter_px * 1.05, 1.0)
        count = max(1, int(round(cum[-1] / spacing_px)))
        spacing_px = cum[-1] / count
        radius = diameter_px * 0.5
        for index in range(count):
            center = _point_on_loop_px(loop, cum, index * spacing_px)
            if center is None:
                continue
            x, y = center
            draw.ellipse(
                (
                    int(round(x - radius)),
                    int(round(y - radius)),
                    int(round(x + radius)),
                    int(round(y + radius)),
                ),
                fill=color,
            )
        return

    dash_px = max(1.0, float(mm_to_px(line_pattern.dashed_segment_mm(entry, line_width_mm), dpi)))
    gap_px = max(0.0, float(mm_to_px(line_pattern.dashed_gap_mm(entry, line_width_mm), dpi)))
    period_px = max(dash_px + gap_px, dash_px, 1.0)
    start = 0.0
    while start < cum[-1] - 1.0e-6:
        sub = _loop_subset_px(loop, cum, start, min(cum[-1], start + dash_px))
        if len(sub) >= 2:
            draw.line([(int(round(x)), int(round(y))) for x, y in sub], fill=color, width=width_px)
        start += period_px


def _draw_balloon_line_loop(draw, pts, entry, color, width_px: int, dpi: int) -> None:
    if width_px <= 0 or len(pts) < 2:
        return
    style = str(getattr(entry, "line_style", "solid") or "solid")
    if style in {"dashed", "dotted"}:
        _draw_pattern_loop(draw, pts, entry, color, width_px, dpi, style)
        return
    if style != "double":
        _ep()._draw_styled_loop(draw, pts, color, width_px, style)
        return

    count = max(1, int(getattr(entry, "multi_line_count", 3) or 3))
    spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
    line_width_mm = max(0.0, float(getattr(entry, "multi_line_width_mm", 0.3) or 0.0))
    scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    fill_color = _entry_fill_rgb255(entry)
    rings: list[tuple[int, int]] = []
    inner_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.3)) * 0.5 + spacing_mm
    for index in range(1, min(12, count)):
        extra_width_mm = line_width_mm * (scale ** (index - 1))
        if extra_width_mm <= 0.0:
            continue
        outer_mm = inner_mm + extra_width_mm
        rings.append((max(1, int(round(mm_to_px(outer_mm * 2.0, dpi)))),
                      max(1, int(round(mm_to_px(inner_mm * 2.0, dpi))))))
        inner_mm = outer_mm + spacing_mm
    for outer_width_px, inner_width_px in reversed(rings):
        _ep()._draw_styled_loop(draw, pts, color, outer_width_px, "solid")
        _ep()._draw_styled_loop(draw, pts, fill_color, inner_width_px, "solid")
    _ep()._draw_styled_loop(draw, pts, color, width_px, "solid")


def render_balloon_layer(entry, canvas_height_px: int, dpi: int):
    if getattr(entry, "shape", "rect") == "none":
        return None
    ep = _ep()
    rect = Rect(float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm))
    flip_h = bool(getattr(entry, "flip_h", False))
    flip_v = bool(getattr(entry, "flip_v", False))
    rotation_deg = float(getattr(entry, "rotation_deg", 0.0))
    outline = _balloon_outline_mm(entry, rect)
    fill_outline = _balloon_fill_outline_mm(entry, rect)
    outline = _apply_balloon_transforms(outline, rect, flip_h, flip_v, rotation_deg)
    fill_outline = _apply_balloon_transforms(fill_outline, rect, flip_h, flip_v, rotation_deg)
    all_pts = list(outline)
    all_pts.extend(fill_outline)
    for tail in entry.tails:
        all_pts.extend(_balloon_tail_polygon(rect, tail))
    bbox = ep._points_bbox(all_pts)
    if bbox is None:
        return None
    line_style = getattr(entry, "line_style", "solid")
    line_w_mm = 0.0 if str(line_style or "") == "none" else float(getattr(entry, "line_width_mm", 0.3) or 0.3)
    outer_w_mm = float(getattr(entry, "outer_white_margin_width_mm", 0.0) or 0.0) if bool(getattr(entry, "outer_white_margin_enabled", False)) else 0.0
    blur = max(0.0, min(1.0, float(getattr(entry, "fill_blur_amount", 0.0) or 0.0)))
    blur_pad = line_w_mm * (0.65 + 3.35 * blur) if blur > 0.0 else 0.0
    pad_mm = max(2.0, line_w_mm * 4.0 + outer_w_mm * 2.0 + blur_pad)
    canvas = ep._canvas_for_bbox(bbox, canvas_height_px, dpi, pad_mm=pad_mm)
    if canvas is None:
        return None
    line_color = ep._rgb255(entry.line_color, alpha=_entry_opacity(entry))
    outer_color = ep._rgb255(getattr(entry, "outer_white_margin_color", (1.0, 1.0, 1.0, 1.0)), alpha=_entry_opacity(entry))
    inner_color = ep._rgb255(getattr(entry, "inner_white_margin_color", (1.0, 1.0, 1.0, 1.0)), alpha=_entry_opacity(entry))
    line_width_px = max(0, int(round(mm_to_px(line_w_mm, dpi))))
    outer_width_px = int(round(mm_to_px(float(getattr(entry, "outer_white_margin_width_mm", 0.0)), dpi)))
    inner_width_px = int(round(mm_to_px(float(getattr(entry, "inner_white_margin_width_mm", 0.0)), dpi)))
    draw = ep.ImageDraw.Draw(canvas.image)
    outline_px = canvas.points_px(outline)
    fill_outline_px = canvas.points_px(fill_outline)
    fill_clip_mask = None
    if len(fill_outline_px) >= 3:
        fill_polygons = [fill_outline_px]
        fill_polygons.extend(canvas.points_px(_balloon_tail_polygon(rect, tail)) for tail in entry.tails)
        fill_clip_mask = _draw_fill_layer(canvas, entry, [pts for pts in fill_polygons if len(pts) >= 3], dpi)
    line_clip_mask = fill_clip_mask
    draw_line = str(line_style or "") != "none" and line_width_px > 0
    flash_white_width_px = _flash_white_line_width_px(entry, line_w_mm, dpi) if draw_line else 0
    flash_white_color = ep._rgb255((1.0, 1.0, 1.0, 1.0), alpha=_entry_opacity(entry))
    if flash_white_width_px > 0:
        _draw_inner_white_loop(canvas, line_clip_mask, outline_px, flash_white_color, flash_white_width_px, "solid")
    if draw_line and bool(getattr(entry, "outer_white_margin_enabled", False)):
        _draw_white_loop(draw, outline_px, outer_color, line_width_px + outer_width_px * 2, "solid")
    if draw_line and bool(getattr(entry, "inner_white_margin_enabled", False)):
        _draw_inner_white_loop(canvas, line_clip_mask, outline_px, inner_color, max(1, inner_width_px * 2), "solid")
    if draw_line:
        _draw_balloon_line_loop(draw, outline_px, entry, line_color, line_width_px, dpi)
    for tail in entry.tails:
        tail_px = canvas.points_px(_balloon_tail_polygon(rect, tail))
        if len(tail_px) >= 3:
            if flash_white_width_px > 0:
                _draw_inner_white_loop(canvas, line_clip_mask, tail_px, flash_white_color, flash_white_width_px, "solid")
            if draw_line and bool(getattr(entry, "outer_white_margin_enabled", False)):
                _draw_white_loop(draw, tail_px, outer_color, line_width_px + outer_width_px * 2, "solid")
            if draw_line and bool(getattr(entry, "inner_white_margin_enabled", False)):
                _draw_inner_white_loop(canvas, line_clip_mask, tail_px, inner_color, max(1, inner_width_px * 2), "solid")
            if draw_line:
                _draw_balloon_line_loop(draw, tail_px, entry, line_color, line_width_px, dpi)
    return ep.ExportLayer(
        str(getattr(entry, "id", "") or "balloon"),
        canvas.image,
        canvas.left,
        canvas.top,
        blend_mode="normal",
        group_path=(
            "balloons",
            str(getattr(entry, "merge_group_id", "") or ""),
        )
        if getattr(entry, "merge_group_id", "")
        else ("balloons",),
    )
