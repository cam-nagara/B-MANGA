"""用紙ガイド線・セーフ/断ち切り外塗りのGPUオーバーレイ描画 (POST_VIEW)."""

from __future__ import annotations

import gpu
from gpu_extras.batch import batch_for_shader

from . import overlay_shared
from ..utils import color_space, percentage, spread_merge_geometry, viewport_colors
from ..utils.geom import Rect, mm_to_m
from ..utils.paper_guide_object import (
    GUIDE_MAX_WIDTH_MM,
    GUIDE_MIN_WIDTH_MM,
    GUIDE_SCREEN_PX,
)

_GUIDE_COLORS = {
    "dim": viewport_colors.PAPER_GUIDE_DIM,
    "light": viewport_colors.PAPER_GUIDE_LIGHT,
    "inner": viewport_colors.PAPER_GUIDE,
    "safe": viewport_colors.SAFE_LINE,
}


def draw_for_page(
    work,
    paper,
    rects,
    page,
    page_index: int,
    ox_mm: float,
    oy_mm: float,
    is_left_half: bool,
    region,
    rv3d,
) -> None:
    import bpy as _bpy
    scene = getattr(_bpy.context, "scene", None)
    if scene is not None and not bool(getattr(scene, "bmanga_page_guides_visible", True)):
        return
    mpp = _meters_per_pixel(region, rv3d)
    if mpp is None or mpp <= 0.0:
        return

    is_spread = bool(getattr(page, "spread", False))

    if is_spread:
        guide_sets, fill_rects = _spread_page_guide_geometry(work, paper, page)
    else:
        page_rects = overlay_shared.compute_paper_rects(paper, is_left_half=is_left_half)
        guide_sets = _paper_guide_geometry_sets(paper, page_rects)
        fill_rects = page_rects

    safe_pairs, bleed_pairs = _fill_rect_pairs_for_page(work, page, fill_rects)

    prev_depth = gpu.state.depth_test_get()
    gpu.state.depth_test_set("NONE")
    try:
        _draw_bleed_outer_fills(work, bleed_pairs, ox_mm, oy_mm)
        _draw_safe_area_fills(work, safe_pairs, ox_mm, oy_mm)

        # 編集中ページの実体ガイド (paper_guide_object) と同じ太さ規則に揃える
        width_mm = _mm_width_for_screen_px(GUIDE_SCREEN_PX, mpp)
        width_mm = max(GUIDE_MIN_WIDTH_MM, min(GUIDE_MAX_WIDTH_MM, width_mm))
        for label, loops, segments in guide_sets:
            color = _GUIDE_COLORS.get(label, viewport_colors.PAPER_GUIDE)
            for loop in loops:
                if loop:
                    _draw_loop_outline(loop, color, width_mm, ox_mm, oy_mm)
            if segments:
                _draw_segments(segments, color, width_mm, ox_mm, oy_mm)
    finally:
        gpu.state.depth_test_set(prev_depth)


# -- 幾何計算 (paper_guide_object.py から移植) --


def _rect_loop(rect: Rect) -> list[tuple[float, float]]:
    return [
        (rect.x, rect.y),
        (rect.x2, rect.y),
        (rect.x2, rect.y2),
        (rect.x, rect.y2),
    ]


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
    segs.extend([
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
    ])
    cx_mid = (fr.x + fr.x2) * 0.5
    cy_mid = (fr.y + fr.y2) * 0.5
    half = center_size_mm * 0.5
    gap = center_gap_mm
    cy_top = br.y2 + gap + half
    cy_bot = br.y - gap - half
    cx_left = br.x - gap - half
    cx_right = br.x2 + gap + half
    segs.extend([
        ((cx_mid, cy_top - half), (cx_mid, cy_top + half)),
        ((cx_mid - half, cy_top), (cx_mid + half, cy_top)),
        ((cx_mid, cy_bot - half), (cx_mid, cy_bot + half)),
        ((cx_mid - half, cy_bot), (cx_mid + half, cy_bot)),
        ((cx_left, cy_mid - half), (cx_left, cy_mid + half)),
        ((cx_left - half, cy_mid), (cx_left + half, cy_mid)),
        ((cx_right, cy_mid - half), (cx_right, cy_mid + half)),
        ((cx_right - half, cy_mid), (cx_right + half, cy_mid)),
    ])
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
        (
            "safe",
            [_rect_loop(rects.safe) if guides_visible and getattr(paper, "show_safe_line", True) else []],
            [],
        ),
    ]


def _shift_loop(loop, dx_mm: float) -> list[tuple[float, float]]:
    return [(float(x) + dx_mm, float(y)) for x, y in loop]


def _shift_segments(segments, dx_mm: float):
    return [
        ((float(a[0]) + dx_mm, float(a[1])), (float(b[0]) + dx_mm, float(b[1])))
        for a, b in segments
    ]


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
    from ..utils import page_grid

    canvas_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    finish_width = float(getattr(paper, "finish_width_mm", 0.0) or 0.0)
    right_offset = page_grid.spread_right_page_offset_mm(page, canvas_width, finish_width)
    left_rects = overlay_shared.compute_paper_rects(paper, is_left_half=True)
    right_rects = overlay_shared.compute_paper_rects(paper, is_left_half=False)

    left_geom = _paper_guide_geometry_sets(paper, left_rects)
    right_geom = _paper_guide_geometry_sets(paper, right_rects)
    right_shifted = [
        (label, [_shift_loop(l, right_offset) for l in loops], _shift_segments(segs, right_offset))
        for label, loops, segs in right_geom
    ]
    page_pair = _merge_geometry_sets(left_geom, right_shifted)

    combined_rects = spread_merge_geometry.combined_spread_rects(paper, page)
    combined_geom = _paper_guide_geometry_sets(paper, combined_rects)
    combined_by_label = {label: (label, loops, segs) for label, loops, segs in combined_geom}

    page_pair_labels = {"dim", "light"}
    guide_sets = []
    for label, loops, segs in page_pair:
        if label in page_pair_labels:
            guide_sets.append((label, loops, segs))
        else:
            guide_sets.append(combined_by_label.get(label, (label, loops, segs)))

    return guide_sets, combined_rects


# -- 塗り --


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _display_rgb_from_linear(color) -> tuple[float, float, float]:
    try:
        return tuple(
            _clamp01(c)
            for c in color_space.linear_to_srgb_rgb(
                (float(color[0]), float(color[1]), float(color[2]))
            )
        )
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 0.0)


def _safe_fill_color(work) -> tuple[float, float, float, float]:
    overlay = getattr(work, "safe_area_overlay", None)
    color = getattr(overlay, "color", (0.0, 0.0, 0.0)) if overlay else (0.0, 0.0, 0.0)
    opacity = percentage.percent_to_factor(
        getattr(overlay, "opacity", 30.0) if overlay else 30.0, 30.0,
    )
    r, g, b = _display_rgb_from_linear(color)
    return (_clamp01(r), _clamp01(g), _clamp01(b), _clamp01(float(opacity or 0.0)))


def _bleed_outer_fill_color(work) -> tuple[float, float, float, float]:
    overlay = getattr(work, "safe_area_overlay", None)
    color = (
        getattr(overlay, "bleed_outer_color", viewport_colors.BLENDER_BACKGROUND_DEFAULT_LINEAR)
        if overlay
        else viewport_colors.BLENDER_BACKGROUND_DEFAULT_LINEAR
    )
    opacity = percentage.percent_to_factor(
        getattr(overlay, "bleed_outer_opacity", 100.0) if overlay else 100.0, 100.0,
    )
    r, g, b = _display_rgb_from_linear(color)
    return (_clamp01(r), _clamp01(g), _clamp01(b), _clamp01(float(opacity or 0.0)))


def _bleed_outer_fill_is_visible(work) -> bool:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None or not bool(getattr(overlay, "bleed_outer_enabled", True)):
        return False
    return _bleed_outer_fill_color(work)[3] > 0.0


def _fill_rect_pairs_for_page(work, page, rects):
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


def _draw_safe_area_fills(work, rect_pairs, ox_mm, oy_mm) -> None:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None or not bool(getattr(overlay, "enabled", True)):
        return
    color = _safe_fill_color(work)
    if color[3] <= 0.0:
        return
    for outer, inner in rect_pairs:
        _draw_frame_with_hole(
            Rect(outer.x + ox_mm, outer.y + oy_mm, outer.width, outer.height),
            Rect(inner.x + ox_mm, inner.y + oy_mm, inner.width, inner.height),
            color,
        )


def _draw_bleed_outer_fills(work, rect_pairs, ox_mm, oy_mm) -> None:
    if not _bleed_outer_fill_is_visible(work):
        return
    color = _bleed_outer_fill_color(work)
    if color[3] <= 0.0:
        return
    for outer, inner in rect_pairs:
        _draw_frame_with_hole(
            Rect(outer.x + ox_mm, outer.y + oy_mm, outer.width, outer.height),
            Rect(inner.x + ox_mm, inner.y + oy_mm, inner.width, inner.height),
            color,
        )


# -- GPU描画ヘルパ --


def _draw_frame_with_hole(outer: Rect, inner: Rect, color: tuple) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    top = Rect(outer.x, inner.y2, outer.width, outer.y2 - inner.y2)
    bottom = Rect(outer.x, outer.y, outer.width, inner.y - outer.y)
    left = Rect(outer.x, inner.y, inner.x - outer.x, inner.height)
    right = Rect(inner.x2, inner.y, outer.x2 - inner.x2, inner.height)
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for r in (top, bottom, left, right):
        if r.width <= 0 or r.height <= 0:
            continue
        base = len(verts)
        verts.extend([
            (mm_to_m(r.x), mm_to_m(r.y), 0.0),
            (mm_to_m(r.x2), mm_to_m(r.y), 0.0),
            (mm_to_m(r.x2), mm_to_m(r.y2), 0.0),
            (mm_to_m(r.x), mm_to_m(r.y2), 0.0),
        ])
        indices.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    if not verts:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)





def _draw_loop_outline(loop, color, width_mm, ox_mm, oy_mm) -> None:
    half = width_mm * 0.5
    n = len(loop)
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for i in range(n):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % n]
        x0 += ox_mm
        y0 += oy_mm
        x1 += ox_mm
        y1 += oy_mm
        dx = x1 - x0
        dy = y1 - y0
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-9:
            continue
        nx = -dy / length * half
        ny = dx / length * half
        base = len(verts)
        verts.extend([
            (mm_to_m(x0 - nx), mm_to_m(y0 - ny), 0.0),
            (mm_to_m(x1 - nx), mm_to_m(y1 - ny), 0.0),
            (mm_to_m(x1 + nx), mm_to_m(y1 + ny), 0.0),
            (mm_to_m(x0 + nx), mm_to_m(y0 + ny), 0.0),
        ])
        indices.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    if not verts:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_segments(segments, color, width_mm, ox_mm, oy_mm) -> None:
    half = width_mm * 0.5
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for (x0, y0), (x1, y1) in segments:
        x0 += ox_mm
        y0 += oy_mm
        x1 += ox_mm
        y1 += oy_mm
        dx = x1 - x0
        dy = y1 - y0
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-9:
            continue
        nx = -dy / length * half
        ny = dx / length * half
        base = len(verts)
        verts.extend([
            (mm_to_m(x0 - nx), mm_to_m(y0 - ny), 0.0),
            (mm_to_m(x1 - nx), mm_to_m(y1 - ny), 0.0),
            (mm_to_m(x1 + nx), mm_to_m(y1 + ny), 0.0),
            (mm_to_m(x0 + nx), mm_to_m(y0 + ny), 0.0),
        ])
        indices.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    if not verts:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


# -- view-constant thickness --


def _meters_per_pixel(region, rv3d):
    if region is None or rv3d is None:
        return None
    try:
        from bpy_extras import view3d_utils
    except Exception:  # noqa: BLE001
        return None
    sample_m = mm_to_m(10.0)
    try:
        p0 = view3d_utils.location_3d_to_region_2d(region, rv3d, (0.0, 0.0, 0.0))
        px = view3d_utils.location_3d_to_region_2d(region, rv3d, (sample_m, 0.0, 0.0))
        py = view3d_utils.location_3d_to_region_2d(region, rv3d, (0.0, sample_m, 0.0))
        distances = []
        if p0 is not None and px is not None:
            distances.append((px - p0).length)
        if p0 is not None and py is not None:
            distances.append((py - p0).length)
        valid = [d for d in distances if d > 1.0e-6]
        if valid:
            return sample_m / (sum(valid) / len(valid))
    except Exception:  # noqa: BLE001
        pass
    try:
        cx = region.width * 0.5
        cy = region.height * 0.5
        p0 = view3d_utils.region_2d_to_location_3d(region, rv3d, (cx, cy), (0.0, 0.0, 0.0))
        p1 = view3d_utils.region_2d_to_location_3d(region, rv3d, (cx + 1.0, cy), (0.0, 0.0, 0.0))
        if p0 is not None and p1 is not None:
            d = (p1 - p0).length
            return d if d > 1.0e-9 else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _mm_width_for_screen_px(px: float, mpp: float) -> float:
    return px * mpp * 1000.0
