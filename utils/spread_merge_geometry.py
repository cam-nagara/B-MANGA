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


def _close(a: float, b: float, eps: float = 0.05) -> bool:
    return abs(float(a) - float(b)) <= eps


def coma_matches_rect(coma, rect: Rect, *, eps: float = 0.05) -> bool:
    if str(getattr(coma, "shape_type", "rect") or "rect") != "rect":
        return False
    return (
        _close(getattr(coma, "rect_x_mm", 0.0), rect.x, eps)
        and _close(getattr(coma, "rect_y_mm", 0.0), rect.y, eps)
        and _close(getattr(coma, "rect_width_mm", 0.0), rect.width, eps)
        and _close(getattr(coma, "rect_height_mm", 0.0), rect.height, eps)
    )


def basic_frame_info(work, page, coma) -> tuple[str, Rect | None]:
    """見開きの基本枠コマなら、描画担当側と合体後の矩形を返す."""
    if work is None or page is None or coma is None or not bool(getattr(page, "spread", False)):
        return "", None
    paper = getattr(work, "paper", None)
    if paper is None:
        return "", None
    canvas_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    finish_width = float(getattr(paper, "finish_width_mm", 0.0) or 0.0)
    right_offset = page_grid.spread_right_page_offset_mm(page, canvas_width, finish_width)
    if right_offset > canvas_width + 0.05:
        return "", None

    left_rect = overlay_shared.compute_paper_rects(paper, is_left_half=True).inner_frame
    legacy_left_rect = overlay_shared.compute_paper_rects(paper, is_left_half=False).inner_frame
    right_rect = shift_rect(legacy_left_rect, right_offset)
    left_rects = [left_rect]
    if not (
        _close(left_rect.x, legacy_left_rect.x)
        and _close(left_rect.y, legacy_left_rect.y)
        and _close(left_rect.width, legacy_left_rect.width)
        and _close(left_rect.height, legacy_left_rect.height)
    ):
        left_rects.append(legacy_left_rect)

    has_left = False
    has_right = False
    current_left = False
    current_right = False
    matched_left_rect = None
    current_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
    for candidate in getattr(page, "comas", []) or []:
        candidate_id = str(getattr(candidate, "id", "") or getattr(candidate, "coma_id", "") or "")
        is_current = bool(current_id and candidate_id == current_id) or candidate is coma
        candidate_left_rect = next((rect for rect in left_rects if coma_matches_rect(candidate, rect)), None)
        if candidate_left_rect is not None:
            has_left = True
            if matched_left_rect is None:
                matched_left_rect = candidate_left_rect
            if is_current:
                current_left = True
        if coma_matches_rect(candidate, right_rect):
            has_right = True
            if is_current:
                current_right = True
    if not (has_left and has_right):
        return "", None
    combined_rect = union_rects(matched_left_rect, right_rect)
    if current_left:
        return "left", combined_rect
    if current_right:
        return "right", combined_rect
    return "", None
