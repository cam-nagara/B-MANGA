"""見開き中央の合体表示に使う矩形計算ヘルパ."""

from __future__ import annotations

from ..ui import overlay_shared
from . import page_grid
from .geom import Rect


def shift_rect(rect: Rect, dx_mm: float) -> Rect:
    return Rect(float(rect.x) + float(dx_mm), float(rect.y), float(rect.width), float(rect.height))


def union_rects(*rects: Rect) -> Rect:
    valid = [rect for rect in rects if rect is not None]
    if not valid:
        return Rect(0.0, 0.0, 0.0, 0.0)
    x1 = min(float(rect.x) for rect in valid)
    y1 = min(float(rect.y) for rect in valid)
    x2 = max(float(rect.x2) for rect in valid)
    y2 = max(float(rect.y2) for rect in valid)
    return Rect(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))


def combined_spread_rects(paper, page):
    """見開きページを左右合体済みの 1 枚の矩形群として返す."""
    left_rects = overlay_shared.compute_paper_rects(paper, is_left_half=True)
    right_rects = overlay_shared.compute_paper_rects(paper, is_left_half=False)
    right_offset = page_grid.spread_right_page_offset_mm(
        page,
        float(getattr(paper, "canvas_width_mm", 0.0) or 0.0),
        float(getattr(paper, "finish_width_mm", 0.0) or 0.0),
    )
    shifted_right = overlay_shared.PaperRects(
        canvas=shift_rect(right_rects.canvas, right_offset),
        bleed=shift_rect(right_rects.bleed, right_offset),
        finish=shift_rect(right_rects.finish, right_offset),
        inner_frame=shift_rect(right_rects.inner_frame, right_offset),
        safe=shift_rect(right_rects.safe, right_offset),
    )
    return overlay_shared.PaperRects(
        canvas=union_rects(left_rects.canvas, shifted_right.canvas),
        bleed=union_rects(left_rects.bleed, shifted_right.bleed),
        finish=union_rects(left_rects.finish, shifted_right.finish),
        inner_frame=union_rects(left_rects.inner_frame, shifted_right.inner_frame),
        safe=union_rects(left_rects.safe, shifted_right.safe),
    )
