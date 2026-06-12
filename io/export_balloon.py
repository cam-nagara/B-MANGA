"""フキダシのラスタ書き出しヘルパ."""

from __future__ import annotations

import math
from typing import Sequence

from ..utils import balloon_shapes, balloon_tail_geom, free_transform, line_pattern, percentage
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


def _apply_entry_free_transform(
    entry,
    pts: Sequence[tuple[float, float]],
    rect: Rect,
) -> list[tuple[float, float]]:
    if not free_transform.entry_enabled(entry):
        return list(pts)
    out = []
    for x, y in pts:
        local_x = float(x) - rect.x
        local_y = float(y) - rect.y
        tx, ty = free_transform.transform_entry_local_point(entry, local_x, local_y)
        out.append((rect.x + tx, rect.y + ty))
    return out


def _balloon_tail_polygon(rect: Rect, tail) -> list[tuple[float, float]]:
    return balloon_tail_geom.polygon_for_tail(rect, tail)


def _merged_outline_with_tails(
    outline: Sequence[tuple[float, float]],
    tail_outlines: Sequence[Sequence[tuple[float, float]]],
    union_only_outlines: Sequence[Sequence[tuple[float, float]]] = (),
) -> list[tuple[float, float]] | None:
    """本体としっぽの輪郭を、ビューポート描画と同じ結合方式で 1 つにする.

    外へ伸びるしっぽは本体へ結合し、内側へえぐるしっぽは本体から
    差し引く。連続楕円しっぽの「本体に重なる楕円」(union_only_outlines)
    は常に結合する。結合できない場合は None を返し、呼び出し側は従来の
    個別描画へフォールバックする。
    """
    tails = [list(pts) for pts in tail_outlines if len(pts) >= 3]
    union_only = [list(pts) for pts in union_only_outlines if len(pts) >= 3]
    if len(outline) < 3 or (not tails and not union_only):
        return None
    try:
        from ..utils import balloon_tail_boolean
    except Exception:  # noqa: BLE001
        return None
    # 結合判定のしきい値が m 基準のため mm -> m へ揃える
    scale = 0.001
    body_m = [(x * scale, y * scale) for x, y in outline]
    tails_m = [[(x * scale, y * scale) for x, y in pts] for pts in tails]
    union_only_m = [[(x * scale, y * scale) for x, y in pts] for pts in union_only]
    merged, changed = balloon_tail_boolean.combine_body_with_tail_polygons(
        body_m, tails_m, union_only_points_list=union_only_m
    )
    if merged is None or not changed:
        return None
    try:
        coords = list(merged.exterior.coords)
    except Exception:  # noqa: BLE001
        return None
    if len(coords) < 4:
        return None
    return [(float(x) / scale, float(y) / scale) for x, y in coords[:-1]]


def _split_ellipse_outlines_by_body(
    outline: Sequence[tuple[float, float]],
    ellipse_outlines: Sequence[Sequence[tuple[float, float]]],
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """楕円列を「本体に重なる (結合対象)」と「重ならない (個別描画)」に分ける."""
    polys = [list(pts) for pts in ellipse_outlines]
    if not polys or len(outline) < 3:
        return [], polys
    try:
        from ..utils import balloon_tail_boolean

        touching, separate = balloon_tail_boolean.split_indices_touching_body(list(outline), polys)
        return [polys[i] for i in touching], [polys[i] for i in separate]
    except Exception:  # noqa: BLE001
        return [], polys


def _draw_multi_ring_bands(canvas, outline, entry, color, *, sharp: bool) -> None:
    """多重線のリングを、画面のメッシュと同じオフセット帯で描く.

    本体の線の外側 (または内側) に「隙間 = 間隔」で帯を順に並べる。
    幅スケール・間隔スケール・方向 (外側/内側/両方向) に対応する。
    """
    line_w_mm = _scaled_width_mm(entry, "line_width_mm", 0.3)
    ring_w_base = _scaled_width_mm(entry, "multi_line_width_mm", 0.3)
    spacing_base = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
    count = max(1, min(12, int(getattr(entry, "multi_line_count", 3) or 3)))
    width_scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    spacing_scale = max(0.0, float(getattr(entry, "multi_line_spacing_scale_percent", 100.0) or 0.0)) / 100.0
    direction = str(getattr(entry, "multi_line_direction", "outside") or "outside")
    if direction == "both":
        sides = ("inside", "outside")
    elif direction == "inside":
        sides = ("inside",)
    else:
        sides = ("outside",)
    running_outside = line_w_mm
    running_inside = 0.0
    for ring_index in range(1, count + 1):
        ring_w = ring_w_base * (width_scale ** max(0, ring_index - 1))
        spacing = spacing_base * (spacing_scale ** max(0, ring_index - 1))
        if ring_w <= 1.0e-6:
            continue
        for side in sides:
            if side == "inside":
                inner = running_inside + spacing
                band = _mitre_band_polygons_mm(outline, -inner, -(inner + ring_w), sharp=sharp)
                running_inside = inner + ring_w
            else:
                inner = running_outside + spacing
                band = _mitre_band_polygons_mm(outline, inner + ring_w, inner, sharp=sharp)
                running_outside = inner + ring_w
            _composite_patches_px(canvas, band, color)


def _composite_patches_px(canvas, patches, color, clip_mask=None) -> None:
    """穴つき多角形パッチを指定色で合成する (穴は透過のまま残す)."""
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None or not patches:
        return
    temp = ep.Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
    temp_draw = ep.ImageDraw.Draw(temp)
    for outer, holes in patches:
        outer_px = canvas.points_px(outer)
        if len(outer_px) < 3:
            continue
        temp_draw.polygon(outer_px, fill=color)
        for hole in holes:
            hole_px = canvas.points_px(hole)
            if len(hole_px) >= 3:
                temp_draw.polygon(hole_px, fill=(0, 0, 0, 0))
    if clip_mask is not None and ep.ImageChops is not None:
        alpha = ep.ImageChops.multiply(temp.getchannel("A"), clip_mask)
        temp.putalpha(alpha)
    canvas.image.alpha_composite(temp)


def _mitre_band_polygons_mm(outline, outer_off_mm: float, inner_off_mm: float, *, sharp: bool = True):
    """輪郭のオフセット帯 (mm 座標) を返す。sharp=True で角が尖る."""
    if len(outline) < 3:
        return []
    try:
        from ..utils import balloon_tail_boolean

        return balloon_tail_boolean.mitre_band_polygons(
            list(outline), float(outer_off_mm), float(inner_off_mm), sharp=sharp
        )
    except Exception:  # noqa: BLE001
        return []


def _body_sharp_corners(entry) -> bool:
    """フキダシ本体の「角を尖らせる」(形状パラメータ) が ON か."""
    sp = getattr(entry, "shape_params", None)
    return bool(getattr(sp, "cloud_valley_sharp", False))


def _flash_strokes_page_mm(entry, rect: Rect, flip_h: bool, flip_v: bool, rotation_deg: float):
    """ウニフラ/白抜き線のストローク列をページ座標 mm で返す.

    ビューポートのメッシュ焼き込みと同じ生成器を使い、出力 (サムネイル/
    ページ出力/PSD) にも同じ放射線を描けるようにする。
    戻り値: [(role, pts_mm, radii_mm, opacities, side, cyclic), ...]
    """
    try:
        from ..utils import balloon_flash_effect_line_mesh as flash_mesh

        strokes = flash_mesh.generate_flash_strokes_rect_local(entry)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for stroke in strokes:
        raw = list(getattr(stroke, "points_xyz", None) or [])
        if len(raw) < 2:
            continue
        pts = [(rect.x + float(p[0]) * 1000.0, rect.y + float(p[1]) * 1000.0) for p in raw]
        pts = _apply_entry_free_transform(entry, pts, rect)
        pts = _apply_balloon_transforms(pts, rect, flip_h, flip_v, rotation_deg)
        radii = list(getattr(stroke, "radii", None) or [])
        base_r_mm = float(getattr(stroke, "radius", 0.0) or 0.0) * 1000.0
        radii_mm = [
            float(radii[i]) * 1000.0 if i < len(radii) else base_r_mm
            for i in range(len(raw))
        ]
        out.append((
            str(getattr(stroke, "role", "") or "line"),
            pts,
            radii_mm,
            list(getattr(stroke, "opacities", None) or []),
            float(getattr(stroke, "side", 0.0) or 0.0),
            bool(getattr(stroke, "cyclic", False)),
        ))
    return out


def _draw_flash_strokes(canvas, entry, flash_strokes, dpi: int) -> None:
    """ウニフラ/白抜き線のストローク列を可変幅の多角形として描く."""
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None or not flash_strokes:
        return
    style = str(getattr(entry, "line_style", "") or "")
    line_rgb = ep._rgb255(entry.line_color, alpha=1.0)[:3]
    white_rgb = (255, 255, 255)
    if style == "uni_flash":
        # スロット 1 (終点形状の塗り) は塗り色、下地はウニフラの下地色
        slot1_rgb = ep._rgb255(getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0)), alpha=1.0)[:3]
        underlay_rgb = ep._rgb255(getattr(entry, "white_underlay_color", (1.0, 1.0, 1.0, 1.0)), alpha=1.0)[:3]
    else:
        slot1_rgb = white_rgb
        underlay_rgb = white_rgb
    z_order = {"end_fill": 0, "underlay": 1, "white_outline_white": 2}
    temp = ep.Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
    draw = ep.ImageDraw.Draw(temp)
    for role, pts_mm, radii_mm, opacities, side, cyclic in sorted(
        flash_strokes, key=lambda item: z_order.get(item[0], 3)
    ):
        if role == "underlay":
            rgb = underlay_rgb
        elif role in {"end_fill", "white_outline_white"}:
            rgb = slot1_rgb
        else:
            rgb = line_rgb
        if (role == "end_fill" or (role == "white_outline_white" and cyclic)) and len(pts_mm) >= 3:
            poly_px = canvas.points_px(pts_mm)
            if len(poly_px) >= 3:
                draw.polygon(poly_px, fill=(*rgb, 255))
            continue
        n = len(pts_mm)
        seg_count = n if cyclic else n - 1
        for i in range(seg_count):
            j = (i + 1) % n
            x0, y0 = pts_mm[i]
            x1, y1 = pts_mm[j]
            dx = x1 - x0
            dy = y1 - y0
            seg = math.hypot(dx, dy)
            if seg <= 1.0e-9:
                continue
            r0 = radii_mm[i] if i < len(radii_mm) else 0.0
            r1 = radii_mm[j] if j < len(radii_mm) else 0.0
            if r0 <= 1.0e-9 and r1 <= 1.0e-9:
                continue
            a0 = float(opacities[i]) if i < len(opacities) else 1.0
            a1 = float(opacities[j]) if j < len(opacities) else 1.0
            alpha = int(round(255.0 * max(0.0, min(1.0, (a0 + a1) * 0.5))))
            if alpha <= 0:
                continue
            nx = -dy / seg
            ny = dx / seg
            if role == "underlay" and abs(side) > 1.0e-9:
                sign = 1.0 if side >= 0.0 else -1.0
                quad_mm = [
                    (x0, y0),
                    (x0 + nx * sign * r0, y0 + ny * sign * r0),
                    (x1 + nx * sign * r1, y1 + ny * sign * r1),
                    (x1, y1),
                ]
            else:
                quad_mm = [
                    (x0 + nx * r0, y0 + ny * r0),
                    (x0 - nx * r0, y0 - ny * r0),
                    (x1 - nx * r1, y1 - ny * r1),
                    (x1 + nx * r1, y1 + ny * r1),
                ]
            quad_px = canvas.points_px(quad_mm)
            if len(quad_px) >= 3:
                draw.polygon(quad_px, fill=(*rgb, alpha))
    opacity = _entry_opacity(entry)
    if opacity < 0.999:
        alpha_ch = temp.getchannel("A").point(lambda v: int(v * opacity))
        temp.putalpha(alpha_ch)
    canvas.image.alpha_composite(temp)


def _entry_opacity(entry) -> float:
    return percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)


def _line_width_scale(entry) -> float:
    try:
        return max(0.01, float(getattr(entry, "free_transform_line_width_scale", 1.0) or 1.0))
    except Exception:  # noqa: BLE001
        return 1.0


def _scaled_width_mm(entry, attr: str, default: float) -> float:
    try:
        value = float(getattr(entry, attr, default) or 0.0)
    except Exception:  # noqa: BLE001
        value = float(default)
    return max(0.0, value) * _line_width_scale(entry)


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
    angle_value = getattr(entry, "fill_gradient_angle_deg", None)
    # 0 度は有効値なので `or 90.0` で潰さない
    angle = math.radians(90.0 if angle_value is None else float(angle_value))
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
        line_w = max(0.3, _scaled_width_mm(entry, "line_width_mm", 0.3))
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
    line_width_mm = _scaled_width_mm(entry, "line_width_mm", 0.3)
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


def _draw_shape_line_loop(draw, pts, entry, color, width_px: int, dpi: int, center_px=None) -> None:
    """線種「図形」: 図形を輪郭に沿って連続配置して描く."""
    from ..utils import line_decor_geom

    polygons = line_decor_geom.decorations_along_loop(
        [(float(x), float(y)) for x, y in pts],
        kind=str(getattr(entry, "line_shape_kind", "circle") or "circle"),
        size=float(width_px),
        spacing=mm_to_px(max(0.0, float(getattr(entry, "line_shape_spacing_mm", 1.5) or 0.0)), dpi),
        angle_rad=math.radians(float(getattr(entry, "line_shape_angle_deg", 0.0) or 0.0)),
        jitter=float(getattr(entry, "line_shape_jitter", 0.0) or 0.0),
        seed=int(getattr(entry, "line_shape_seed", 0) or 0),
        flip_y=True,
        orient=str(getattr(entry, "line_shape_orient", "line") or "line"),
        center=center_px,
    )
    for poly in polygons:
        draw.polygon([(int(round(x)), int(round(y))) for x, y in poly], fill=color)


def _wrapped_strip(src, u0: float, width_ratio: float):
    """画像を横方向 u0..u0+width_ratio (画像幅 1.0 で折り返し) で切り出す."""
    ep = _ep()
    src_w, src_h = src.size
    x0 = int(round((u0 % 1.0) * src_w))
    take = max(1, int(round(width_ratio * src_w)))
    strip = ep.Image.new("RGBA", (take, src_h), (0, 0, 0, 0))
    copied = 0
    while copied < take:
        chunk = min(src_w - x0, take - copied)
        strip.paste(src.crop((x0, 0, x0 + chunk, src_h)), (copied, 0))
        copied += chunk
        x0 = 0
    return strip


def _draw_image_line_loop(canvas, pts, entry, width_px: int, dpi: int) -> None:
    """線種「画像」: 画像を輪郭に沿って引き延ばして描く (区間パッチ近似)."""
    from pathlib import Path as _Path

    from ..utils import line_decor_geom

    ep = _ep()
    raw = str(getattr(entry, "line_image_path", "") or "").strip()
    if not raw or width_px <= 0 or len(pts) < 3:
        return
    try:
        import bpy

        path = bpy.path.abspath(raw)
    except Exception:  # noqa: BLE001
        path = raw
    if not _Path(path).is_file():
        return
    try:
        src = ep.Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return
    angle_deg = float(getattr(entry, "line_image_angle_deg", 0.0) or 0.0)
    if abs(angle_deg) > 1.0e-3:
        src = src.rotate(-angle_deg, expand=True)
    # フキダシの不透明度を画像線にも反映する
    opacity = _entry_opacity(entry)
    if opacity < 0.999:
        alpha = src.getchannel("A").point(lambda v: int(v * opacity))
        src.putalpha(alpha)
    interval_px = max(2.0, mm_to_px(max(0.5, float(getattr(entry, "line_image_interval_mm", 20.0) or 20.0)), dpi))
    jitter = max(0.0, min(1.0, float(getattr(entry, "line_image_jitter", 0.0) or 0.0)))
    loop = line_decor_geom.resample_loop(
        [(float(x), float(y)) for x, y in pts],
        max(4.0, float(width_px) * 2.0),
    )
    if len(loop) < 3:
        return
    resampling = getattr(getattr(ep.Image, "Resampling", ep.Image), "BICUBIC", 3)
    arc = 0.0
    n = len(loop)
    for i in range(n):
        p0 = loop[i]
        p1 = loop[(i + 1) % n]
        seg_len = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if seg_len < 0.5:
            continue
        strip = _wrapped_strip(src, arc / interval_px, seg_len / interval_px)
        patch = strip.resize((max(1, int(round(seg_len + 1))), max(1, int(round(width_px)))), resampling)
        rotation = math.degrees(math.atan2(-(p1[1] - p0[1]), p1[0] - p0[0]))
        patch = patch.rotate(rotation, expand=True, resample=resampling)
        cx = (p0[0] + p1[0]) * 0.5
        cy = (p0[1] + p1[1]) * 0.5
        if jitter > 0.0:
            wobble = math.sin(arc / interval_px * math.tau) * width_px * 0.5 * jitter
            normal = math.atan2(p1[0] - p0[0], -(p1[1] - p0[1]))
            cx += math.cos(normal) * wobble
            cy += math.sin(normal) * wobble
        pos = (int(round(cx - patch.width * 0.5)), int(round(cy - patch.height * 0.5)))
        canvas.image.alpha_composite(patch, dest=pos)
        arc += seg_len


def _draw_balloon_line_loop(draw, pts, entry, color, width_px: int, dpi: int, shape_center_px=None) -> None:
    if width_px <= 0 or len(pts) < 2:
        return
    style = str(getattr(entry, "line_style", "solid") or "solid")
    if style in {"dashed", "dotted"}:
        _draw_pattern_loop(draw, pts, entry, color, width_px, dpi, style)
        return
    if style == "shape":
        _draw_shape_line_loop(draw, pts, entry, color, width_px, dpi, shape_center_px)
        return
    if style != "double":
        _ep()._draw_styled_loop(draw, pts, color, width_px, style)
        return

    count = max(1, int(getattr(entry, "multi_line_count", 3) or 3))
    spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
    line_width_mm = _scaled_width_mm(entry, "multi_line_width_mm", 0.3)
    scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    fill_color = _entry_fill_rgb255(entry)
    rings: list[tuple[int, int]] = []
    inner_mm = _scaled_width_mm(entry, "line_width_mm", 0.3) * 0.5 + spacing_mm
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
    outline = _apply_entry_free_transform(entry, outline, rect)
    fill_outline = _apply_entry_free_transform(entry, fill_outline, rect)
    outline = _apply_balloon_transforms(outline, rect, flip_h, flip_v, rotation_deg)
    fill_outline = _apply_balloon_transforms(fill_outline, rect, flip_h, flip_v, rotation_deg)
    tail_outlines = []
    sharp_tail_regions: list[list[tuple[float, float]]] = []
    sharp_tail_infos: list[tuple[list, list, list]] = []
    for tail in entry.tails:
        tail_outline = _apply_entry_free_transform(entry, _balloon_tail_polygon(rect, tail), rect)
        tail_outline = _apply_balloon_transforms(tail_outline, rect, flip_h, flip_v, rotation_deg)
        tail_outlines.append(tail_outline)
        if bool(getattr(tail, "sharp_corners", False)) and len(tail_outline) >= 3:
            sharp_tail_regions.append(tail_outline)
            # 先端を「抜き」のように絞るための中心線と半幅
            centerline_mm, halves_mm = balloon_tail_geom.centerline_with_halfwidths(rect, tail)
            if len(centerline_mm) >= 2:
                centerline_mm = _apply_entry_free_transform(entry, centerline_mm, rect)
                centerline_mm = _apply_balloon_transforms(centerline_mm, rect, flip_h, flip_v, rotation_deg)
                sharp_tail_infos.append((centerline_mm, list(halves_mm), tail_outline))
    # 線しっぽ (線種「線」): 1本のストローク線として線色で塗る
    line_stroke_outlines: list[list[tuple[float, float]]] = []
    for tail in entry.tails:
        if not balloon_tail_geom.is_line_stroke(tail):
            continue
        pts = balloon_tail_geom.line_stroke_polygon_for_tail(rect, tail)
        pts = _apply_entry_free_transform(entry, pts, rect)
        pts = _apply_balloon_transforms(pts, rect, flip_h, flip_v, rotation_deg)
        if len(pts) >= 3:
            line_stroke_outlines.append(pts)
    # 連続楕円しっぽ (線種「楕円」): 本体に重なる楕円は本体と結合し、
    # 重ならない楕円だけ独立した楕円列として描く
    ellipse_outlines: list[list[tuple[float, float]]] = []
    for tail in entry.tails:
        if not balloon_tail_geom.is_ellipse_chain(tail):
            continue
        for ellipse in balloon_tail_geom.ellipse_chain_for_tail(rect, tail):
            pts = balloon_tail_geom.ellipse_polygon(ellipse)
            pts = _apply_entry_free_transform(entry, pts, rect)
            pts = _apply_balloon_transforms(pts, rect, flip_h, flip_v, rotation_deg)
            if len(pts) >= 3:
                ellipse_outlines.append(pts)
    merged_ellipses, ellipse_outlines = _split_ellipse_outlines_by_body(outline, ellipse_outlines)
    # 「中心点」向き図形の基準: 本体 (しっぽ結合前) の輪郭の中心
    body_center_mm = None
    if outline:
        body_center_mm = (
            sum(x for x, _y in outline) / len(outline),
            sum(y for _x, y in outline) / len(outline),
        )
    # ビューポートと同じ結合 (外しっぽは結合 / 内しっぽはえぐり) を出力側にも適用
    merged_outline = _merged_outline_with_tails(outline, tail_outlines, merged_ellipses)
    if merged_outline is not None:
        outline = merged_outline
        fill_outline = list(merged_outline)
        tail_outlines = []
    all_pts = list(outline)
    all_pts.extend(fill_outline)
    for tail_outline in tail_outlines:
        all_pts.extend(tail_outline)
    for ellipse_outline in ellipse_outlines:
        all_pts.extend(ellipse_outline)
    for stroke_outline in line_stroke_outlines:
        all_pts.extend(stroke_outline)
    # ウニフラ/白抜き線: ビューポートと同じ生成器で放射線を計算し、
    # キャンバス範囲にも含める (線はフキダシの外へ大きく伸びるため)
    is_flash = balloon_shapes.is_flash_line_style(str(getattr(entry, "line_style", "") or ""))
    flash_strokes = (
        _flash_strokes_page_mm(entry, rect, flip_h, flip_v, rotation_deg) if is_flash else []
    )
    flash_pad_mm = 0.0
    for _role, flash_pts, flash_radii, _ops, _side, _cyc in flash_strokes:
        all_pts.extend(flash_pts)
        if flash_radii:
            flash_pad_mm = max(flash_pad_mm, max(flash_radii))
    bbox = ep._points_bbox(all_pts)
    if bbox is None:
        return None
    line_style = getattr(entry, "line_style", "solid")
    line_w_mm = 0.0 if str(line_style or "") == "none" else _scaled_width_mm(entry, "line_width_mm", 0.3)
    outer_w_mm = _scaled_width_mm(entry, "outer_white_margin_width_mm", 0.0) if bool(getattr(entry, "outer_white_margin_enabled", False)) else 0.0
    blur = max(0.0, min(1.0, float(getattr(entry, "fill_blur_amount", 0.0) or 0.0)))
    blur_pad = line_w_mm * (0.65 + 3.35 * blur) if blur > 0.0 else 0.0
    pad_mm = max(2.0, line_w_mm * 4.0 + outer_w_mm * 2.0 + blur_pad, flash_pad_mm + 1.0)
    canvas = ep._canvas_for_bbox(bbox, canvas_height_px, dpi, pad_mm=pad_mm)
    if canvas is None:
        return None
    line_color = ep._rgb255(entry.line_color, alpha=_entry_opacity(entry))
    outer_color = ep._rgb255(getattr(entry, "outer_white_margin_color", (1.0, 1.0, 1.0, 1.0)), alpha=_entry_opacity(entry))
    inner_color = ep._rgb255(getattr(entry, "inner_white_margin_color", (1.0, 1.0, 1.0, 1.0)), alpha=_entry_opacity(entry))
    line_width_px = max(0, int(round(mm_to_px(line_w_mm, dpi))))
    outer_width_px = int(round(mm_to_px(_scaled_width_mm(entry, "outer_white_margin_width_mm", 0.0), dpi)))
    inner_width_px = int(round(mm_to_px(_scaled_width_mm(entry, "inner_white_margin_width_mm", 0.0), dpi)))
    draw = ep.ImageDraw.Draw(canvas.image)
    outline_px = canvas.points_px(outline)
    fill_outline_px = canvas.points_px(fill_outline)
    body_center_px = None
    if body_center_mm is not None:
        center_pts = canvas.points_px([body_center_mm])
        if center_pts:
            body_center_px = (float(center_pts[0][0]), float(center_pts[0][1]))
    fill_clip_mask = None
    if len(fill_outline_px) >= 3:
        fill_polygons = [fill_outline_px]
        fill_polygons.extend(canvas.points_px(tail_outline) for tail_outline in tail_outlines)
        fill_clip_mask = _draw_fill_layer(canvas, entry, [pts for pts in fill_polygons if len(pts) >= 3], dpi)
    line_clip_mask = fill_clip_mask
    draw_line = str(line_style or "") != "none" and line_width_px > 0
    flash_white_width_px = _flash_white_line_width_px(entry, line_w_mm, dpi) if draw_line else 0
    flash_white_color = ep._rgb255((1.0, 1.0, 1.0, 1.0), alpha=_entry_opacity(entry))
    if flash_white_width_px > 0:
        _draw_inner_white_loop(canvas, line_clip_mask, outline_px, flash_white_color, flash_white_width_px, "solid")
    # 実線・多重線の主線とフチは、画面のメッシュと同じ「輪郭の外側に乗る」
    # オフセット帯で描く。「角を尖らせる」ON なら mitre join で角まで尖らせる。
    inner_w_mm = _scaled_width_mm(entry, "inner_white_margin_width_mm", 0.0)
    body_sharp = _body_sharp_corners(entry)
    band_line_styles = {"solid", "double"}
    if draw_line and not is_flash and bool(getattr(entry, "outer_white_margin_enabled", False)):
        # 外フチ: 線の外側にだけ付く帯 (画面のメッシュと同じ付き方)。
        # 「角を尖らせる」ON のときは mitre join で角まで尖らせる。
        _composite_patches_px(
            canvas,
            _mitre_band_polygons_mm(
                outline, line_w_mm + outer_w_mm, line_w_mm, sharp=body_sharp
            ),
            outer_color,
        )
    if draw_line and not is_flash and bool(getattr(entry, "inner_white_margin_enabled", False)):
        # 内フチ: 本体の内側に付く帯
        _composite_patches_px(
            canvas,
            _mitre_band_polygons_mm(outline, 0.0, -inner_w_mm, sharp=body_sharp),
            inner_color,
            clip_mask=line_clip_mask,
        )
    if draw_line:
        if is_flash:
            # ウニフラ/白抜き線: 本体輪郭の線は描かず、放射線群を描く
            # (ビューポートでも主線の帯は無く、放射線メッシュだけが見える)
            _draw_flash_strokes(canvas, entry, flash_strokes, dpi)
        elif str(line_style or "") == "image":
            _draw_image_line_loop(canvas, outline_px, entry, line_width_px, dpi)
        elif str(line_style or "") in band_line_styles:
            if str(line_style or "") == "double":
                _draw_multi_ring_bands(canvas, outline, entry, line_color, sharp=body_sharp)
            band_rings = _mitre_band_polygons_mm(outline, line_w_mm, 0.0, sharp=body_sharp)
            # 「角を尖らせる」しっぽ: 折れ角を尖らせ、先端をペンの抜きのように絞る
            if merged_outline is not None and sharp_tail_infos:
                try:
                    from ..utils import balloon_tail_boolean

                    band_rings = balloon_tail_boolean.apply_sharp_tail_tips(
                        band_rings,
                        list(outline),
                        line_w_mm,
                        sharp_tail_infos,
                        add_bend_mitre=not body_sharp,
                    )
                except Exception:  # noqa: BLE001
                    pass
            _composite_patches_px(canvas, band_rings, line_color)
        else:
            _draw_balloon_line_loop(draw, outline_px, entry, line_color, line_width_px, dpi, body_center_px)
    for tail_outline in tail_outlines:
        tail_px = canvas.points_px(tail_outline)
        if len(tail_px) >= 3:
            if flash_white_width_px > 0:
                _draw_inner_white_loop(canvas, line_clip_mask, tail_px, flash_white_color, flash_white_width_px, "solid")
            tail_sharp = body_sharp or tail_outline in sharp_tail_regions
            if draw_line and not is_flash and bool(getattr(entry, "outer_white_margin_enabled", False)):
                _composite_patches_px(
                    canvas,
                    _mitre_band_polygons_mm(
                        tail_outline, line_w_mm + outer_w_mm, line_w_mm, sharp=tail_sharp
                    ),
                    outer_color,
                )
            if draw_line and not is_flash and bool(getattr(entry, "inner_white_margin_enabled", False)):
                _composite_patches_px(
                    canvas,
                    _mitre_band_polygons_mm(tail_outline, 0.0, -inner_w_mm, sharp=tail_sharp),
                    inner_color,
                    clip_mask=line_clip_mask,
                )
            if draw_line and not is_flash:
                if str(line_style or "") == "image":
                    _draw_image_line_loop(canvas, tail_px, entry, line_width_px, dpi)
                elif str(line_style or "") in band_line_styles:
                    band_rings = _mitre_band_polygons_mm(tail_outline, line_w_mm, 0.0, sharp=tail_sharp)
                    tail_info = next(
                        (info for info in sharp_tail_infos if info[2] is tail_outline), None
                    )
                    if tail_info is not None:
                        try:
                            from ..utils import balloon_tail_boolean

                            band_rings = balloon_tail_boolean.apply_sharp_tail_tips(
                                band_rings,
                                list(tail_outline),
                                line_w_mm,
                                [tail_info],
                                add_bend_mitre=False,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    _composite_patches_px(canvas, band_rings, line_color)
                else:
                    _draw_balloon_line_loop(draw, tail_px, entry, line_color, line_width_px, dpi, body_center_px)
    # 線しっぽ (線種「線」): ストローク多角形を線色で塗る
    if line_stroke_outlines and draw_line:
        for stroke_outline in line_stroke_outlines:
            stroke_px = canvas.points_px(stroke_outline)
            if len(stroke_px) >= 3:
                draw.polygon(stroke_px, fill=line_color)
    # 連続楕円しっぽ: 親フキダシの塗り色・線色・線幅で各楕円を描く
    if ellipse_outlines:
        ellipse_fill = _entry_fill_rgb255(entry)
        for ellipse_outline in ellipse_outlines:
            ellipse_px = canvas.points_px(ellipse_outline)
            if len(ellipse_px) < 3:
                continue
            draw.polygon(ellipse_px, fill=ellipse_fill)
            if draw_line:
                ep._draw_styled_loop(draw, ellipse_px, line_color, line_width_px, "solid")
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
