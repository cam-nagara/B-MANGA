"""Pillow decoration helpers for page preview images."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..ui import overlay_shared
from . import page_grid, percentage, spread_merge_geometry
from .geom import Rect

_GUIDE_COLORS = {
    "dim": (150, 150, 150, 170),
    "light": (188, 188, 188, 190),
    "inner": (145, 205, 215, 220),
    "safe": (95, 215, 235, 235),
}


def preview_detail_variant(scene=None) -> bool:
    """ページ/コマ用 blend のページ一覧プレビューかどうかを返す."""
    if scene is None:
        return False
    try:
        from . import page_file_scene

        role, _page_id, _coma_id = page_file_scene.current_role(__import__("bpy").context)
        return role in {page_file_scene.ROLE_PAGE, page_file_scene.ROLE_COMA}
    except Exception:  # noqa: BLE001
        return False


def page_guides_visible(work, scene=None) -> bool:
    paper = getattr(work, "paper", None) if work is not None else None
    if paper is None:
        return False
    try:
        if not bool(getattr(paper, "show_guides", True)):
            return False
    except Exception:  # noqa: BLE001
        return False
    if preview_detail_variant(scene):
        return bool(getattr(scene, "bmanga_page_guides_visible", True))
    return True


def page_work_info_visible(work, scene=None) -> bool:
    info = getattr(work, "work_info", None) if work is not None else None
    if info is not None and not bool(getattr(info, "display_visible", True)):
        return False
    if preview_detail_variant(scene):
        return bool(getattr(scene, "bmanga_page_work_info_visible", True))
    return True


def draw_preview_decoration(
    image: Image.Image,
    work,
    page,
    *,
    scene=None,
    include_fills: bool,
    include_guides: bool = True,
) -> None:
    """ページ一覧プレビュー画像へ用紙ガイド/塗りを焼き込む."""
    if image is None or work is None or page is None:
        return
    if not page_guides_visible(work, scene):
        return
    paper = getattr(work, "paper", None)
    if paper is None:
        return
    geometry = _page_guide_geometry(work, paper, page)
    if include_fills:
        safe_pairs, bleed_pairs = _fill_rect_pairs(work, page, geometry["fill_rects"])
        _draw_bleed_outer_fills(image, work, bleed_pairs, geometry["content_width"], geometry["canvas_height"])
        _draw_safe_fills(image, work, safe_pairs, geometry["content_width"], geometry["canvas_height"])
    if include_guides:
        _draw_guides(
            image,
            geometry["guide_sets"],
            geometry["content_width"],
            geometry["canvas_height"],
        )


def _page_guide_geometry(work, paper, page) -> dict:
    canvas_width = max(1.0, float(getattr(paper, "canvas_width_mm", 1.0) or 1.0))
    canvas_height = max(1.0, float(getattr(paper, "canvas_height_mm", 1.0) or 1.0))
    finish_width = max(1.0, float(getattr(paper, "finish_width_mm", 1.0) or 1.0))
    content_width = page_grid.spread_content_width_mm(page, canvas_width, finish_width)
    if bool(getattr(page, "spread", False)):
        guide_sets, fill_rects = _spread_page_guide_geometry(work, paper, page)
    else:
        is_left_half = False
        try:
            pages = list(getattr(work, "pages", []) or [])
            page_index = pages.index(page)
            is_left_half = page_grid.is_left_half_page(work, page_index)
        except Exception:  # noqa: BLE001
            is_left_half = False
        rects = overlay_shared.compute_paper_rects(paper, is_left_half=is_left_half)
        guide_sets = _paper_guide_geometry_sets(paper, rects)
        fill_rects = rects
    return {
        "guide_sets": guide_sets,
        "fill_rects": fill_rects,
        "content_width": content_width,
        "canvas_height": canvas_height,
    }


def _rect_loop(rect: Rect) -> list[tuple[float, float]]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _trim_segments(
    finish: Rect,
    bleed: Rect,
    *,
    corner_arm_mm: float = 10.0,
    center_size_mm: float = 10.0,
    center_gap_mm: float = 5.0,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    fr, br = finish, bleed
    arm = corner_arm_mm
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    segs.extend(
        [
            ((br.x - arm, fr.y), (br.x, fr.y)),
            ((fr.x, br.y - arm), (fr.x, br.y)),
            ((br.x - arm, br.y), (br.x, br.y)),
            ((br.x, br.y - arm), (br.x, br.y)),
            ((br.x2, fr.y), (br.x2 + arm, fr.y)),
            ((fr.x2, br.y - arm), (fr.x2, br.y)),
            ((br.x2, br.y), (br.x2 + arm, br.y)),
            ((br.x2, br.y - arm), (br.x2, br.y)),
            ((br.x - arm, fr.y2), (br.x, fr.y2)),
            ((fr.x, br.y2), (fr.x, br.y2 + arm)),
            ((br.x - arm, br.y2), (br.x, br.y2)),
            ((br.x, br.y2), (br.x, br.y2 + arm)),
            ((br.x2, fr.y2), (br.x2 + arm, fr.y2)),
            ((fr.x2, br.y2), (fr.x2, br.y2 + arm)),
            ((br.x2, br.y2), (br.x2 + arm, br.y2)),
            ((br.x2, br.y2), (br.x2, br.y2 + arm)),
        ]
    )
    cx_mid = (fr.x + fr.x2) * 0.5
    cy_mid = (fr.y + fr.y2) * 0.5
    half = center_size_mm * 0.5
    gap = center_gap_mm
    cy_top = br.y2 + gap + half
    cy_bot = br.y - gap - half
    cx_left = br.x - gap - half
    cx_right = br.x2 + gap + half
    segs.extend(
        [
            ((cx_mid, cy_top - half), (cx_mid, cy_top + half)),
            ((cx_mid - half, cy_top), (cx_mid + half, cy_top)),
            ((cx_mid, cy_bot - half), (cx_mid, cy_bot + half)),
            ((cx_mid - half, cy_bot), (cx_mid + half, cy_bot)),
            ((cx_left, cy_mid - half), (cx_left, cy_mid + half)),
            ((cx_left - half, cy_mid), (cx_left + half, cy_mid)),
            ((cx_right, cy_mid - half), (cx_right, cy_mid + half)),
            ((cx_right - half, cy_mid), (cx_right + half, cy_mid)),
        ]
    )
    return segs


def _paper_guide_geometry_sets(paper, rects) -> list[tuple[str, list, list]]:
    guides_visible = bool(getattr(paper, "show_guides", True))
    bleed_enabled = float(getattr(paper, "bleed_mm", 0.0) or 0.0) > 0.0
    return [
        (
            "dim",
            [
                _rect_loop(rects.canvas) if guides_visible and getattr(paper, "show_canvas_frame", True) else [],
                _rect_loop(rects.bleed)
                if guides_visible and bleed_enabled and getattr(paper, "show_bleed_frame", True)
                else [],
            ],
            [],
        ),
        (
            "light",
            [_rect_loop(rects.finish) if guides_visible and getattr(paper, "show_finish_frame", True) else []],
            _trim_segments(rects.finish, rects.bleed)
            if guides_visible and bleed_enabled and getattr(paper, "show_trim_marks", True)
            else [],
        ),
        (
            "inner",
            [_rect_loop(rects.inner_frame) if guides_visible and getattr(paper, "show_inner_frame", True) else []],
            [],
        ),
        ("safe", [_rect_loop(rects.safe) if guides_visible and getattr(paper, "show_safe_line", True) else []], []),
    ]


def _shift_loop(loop, dx_mm: float) -> list[tuple[float, float]]:
    return [(float(x) + dx_mm, float(y)) for x, y in loop]


def _shift_segments(segments, dx_mm: float):
    return [((float(a[0]) + dx_mm, float(a[1])), (float(b[0]) + dx_mm, float(b[1]))) for a, b in segments]


def _merge_geometry_sets(*sets):
    merged: dict[str, list] = {}
    order: list[str] = []
    for geom_sets in sets:
        for label, loops, segments in geom_sets:
            if label not in merged:
                merged[label] = [[], []]
                order.append(label)
            merged[label][0].extend(loops)
            merged[label][1].extend(segments)
    return [(label, merged[label][0], merged[label][1]) for label in order]


def _spread_page_guide_geometry(work, paper, page):
    canvas_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    finish_width = float(getattr(paper, "finish_width_mm", 0.0) or 0.0)
    right_offset = page_grid.spread_right_page_offset_mm(page, canvas_width, finish_width)
    left_rects = overlay_shared.compute_paper_rects(paper, is_left_half=True)
    right_rects = overlay_shared.compute_paper_rects(paper, is_left_half=False)
    left_geom = _paper_guide_geometry_sets(paper, left_rects)
    right_geom = _paper_guide_geometry_sets(paper, right_rects)
    right_shifted = [
        (label, [_shift_loop(loop, right_offset) for loop in loops], _shift_segments(segs, right_offset))
        for label, loops, segs in right_geom
    ]
    page_pair = _merge_geometry_sets(left_geom, right_shifted)
    combined_rects = spread_merge_geometry.combined_spread_rects(paper, page)
    combined_geom = _paper_guide_geometry_sets(paper, combined_rects)
    combined_by_label = {label: (label, loops, segs) for label, loops, segs in combined_geom}
    guide_sets = []
    for label, loops, segs in page_pair:
        guide_sets.append((label, loops, segs) if label in {"dim", "light"} else combined_by_label.get(label, (label, loops, segs)))
    return guide_sets, combined_rects


def _srgb(value: float) -> float:
    v = max(0.0, min(1.0, float(value)))
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def _rgba255(rgba, fallback=(0, 0, 0, 0)) -> tuple[int, int, int, int]:
    try:
        r, g, b, a = rgba[:4]
        return (
            int(round(_srgb(r) * 255.0)),
            int(round(_srgb(g) * 255.0)),
            int(round(_srgb(b) * 255.0)),
            int(round(max(0.0, min(1.0, float(a))) * 255.0)),
        )
    except Exception:  # noqa: BLE001
        return fallback


def _safe_fill_color(work) -> tuple[int, int, int, int]:
    overlay = getattr(work, "safe_area_overlay", None)
    color = getattr(overlay, "color", (0.0, 0.0, 0.0)) if overlay else (0.0, 0.0, 0.0)
    opacity = percentage.percent_to_factor(getattr(overlay, "opacity", 30.0) if overlay else 30.0, 30.0)
    return _rgba255((*color[:3], opacity))


def _bleed_outer_fill_color(work) -> tuple[int, int, int, int]:
    overlay = getattr(work, "safe_area_overlay", None)
    color = getattr(overlay, "bleed_outer_color", (0.0, 0.0, 0.0)) if overlay else (0.0, 0.0, 0.0)
    opacity = percentage.percent_to_factor(
        getattr(overlay, "bleed_outer_opacity", 100.0) if overlay else 100.0,
        100.0,
    )
    return _rgba255((*color[:3], opacity))


def _bleed_outer_fill_is_visible(work) -> bool:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None or not bool(getattr(overlay, "bleed_outer_enabled", True)):
        return False
    return _bleed_outer_fill_color(work)[3] > 0


def _fill_rect_pairs(work, page, rects):
    if not bool(getattr(page, "spread", False)):
        safe_outer = rects.bleed if _bleed_outer_fill_is_visible(work) else rects.canvas
        return [(safe_outer, rects.safe)], [(rects.canvas, rects.bleed)]
    try:
        combined = spread_merge_geometry.combined_spread_rects(getattr(work, "paper", None), page)
    except Exception:  # noqa: BLE001
        safe_outer = rects.bleed if _bleed_outer_fill_is_visible(work) else rects.canvas
        return [(safe_outer, rects.safe)], [(rects.canvas, rects.bleed)]
    safe_outer = combined.bleed if _bleed_outer_fill_is_visible(work) else combined.canvas
    return [(safe_outer, combined.safe)], [(combined.canvas, combined.bleed)]


def _draw_safe_fills(image, work, rect_pairs, content_width: float, canvas_height: float) -> None:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None or not bool(getattr(overlay, "enabled", True)):
        return
    color = _safe_fill_color(work)
    if color[3] <= 0:
        return
    for outer, inner in rect_pairs:
        _draw_frame_with_hole(image, outer, inner, color, content_width, canvas_height)


def _draw_bleed_outer_fills(image, work, rect_pairs, content_width: float, canvas_height: float) -> None:
    if not _bleed_outer_fill_is_visible(work):
        return
    color = _bleed_outer_fill_color(work)
    if color[3] <= 0:
        return
    for outer, inner in rect_pairs:
        _draw_frame_with_hole(image, outer, inner, color, content_width, canvas_height)


def _to_px(x_mm: float, y_mm: float, width: int, height: int, content_width: float, canvas_height: float):
    return (
        x_mm / max(1.0, content_width) * width,
        height - (y_mm / max(1.0, canvas_height) * height),
    )


def _box(rect: Rect, width: int, height: int, content_width: float, canvas_height: float):
    x0, y0 = _to_px(rect.x, rect.y2, width, height, content_width, canvas_height)
    x1, y1 = _to_px(rect.x2, rect.y, width, height, content_width, canvas_height)
    left = max(0, min(width, int(round(min(x0, x1)))))
    right = max(0, min(width, int(round(max(x0, x1)))))
    top = max(0, min(height, int(round(min(y0, y1)))))
    bottom = max(0, min(height, int(round(max(y0, y1)))))
    return left, top, right, bottom


def _draw_frame_with_hole(image, outer: Rect, inner: Rect, color, content_width: float, canvas_height: float) -> None:
    width, height = image.size
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    ox0, oy0, ox1, oy1 = _box(outer, width, height, content_width, canvas_height)
    ix0, iy0, ix1, iy1 = _box(inner, width, height, content_width, canvas_height)
    bands = [
        (ox0, oy0, ox1, iy0),
        (ox0, iy1, ox1, oy1),
        (ox0, iy0, ix0, iy1),
        (ix1, iy0, ox1, iy1),
    ]
    for x0, y0, x1, y1 in bands:
        if x1 > x0 and y1 > y0:
            draw.rectangle((x0, y0, x1, y1), fill=color)
    image.alpha_composite(layer)


def _draw_guides(image, guide_sets, content_width: float, canvas_height: float) -> None:
    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    line_width = max(1, int(round(max(width / max(content_width, 1.0), height / max(canvas_height, 1.0)) * 0.2)))
    line_width = max(1, min(4, line_width))
    for label, loops, segments in guide_sets:
        color = _GUIDE_COLORS.get(label, _GUIDE_COLORS["inner"])
        for loop in loops:
            if not loop:
                continue
            pts = [_to_px(float(x), float(y), width, height, content_width, canvas_height) for x, y in loop]
            draw.line(pts + [pts[0]], fill=color, width=line_width, joint="curve")
        for a, b in segments:
            pa = _to_px(float(a[0]), float(a[1]), width, height, content_width, canvas_height)
            pb = _to_px(float(b[0]), float(b[1]), width, height, content_width, canvas_height)
            draw.line([pa, pb], fill=color, width=line_width)
