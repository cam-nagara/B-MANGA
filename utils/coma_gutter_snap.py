"""Helpers for gutter-side frame snap candidates."""

from __future__ import annotations

from . import page_grid
from .geom import finish_rect


def is_left_half_page_for_work(work, page_index: int) -> bool:
    paper = getattr(work, "paper", None)
    if paper is None:
        return False
    try:
        grid_page_index = page_grid.original_page_index(work, int(page_index))
        if grid_page_index < 0:
            grid_page_index = int(page_index)
        return page_grid.is_left_half_page(
            grid_page_index,
            str(getattr(paper, "start_side", "right") or "right"),
            str(getattr(paper, "read_direction", "left") or "left"),
            work=work,
        )
    except Exception:  # noqa: BLE001
        return False


def finish_gutter_line_for_page(work, page_index: int) -> tuple[tuple[float, float], tuple[float, float]]:
    paper = getattr(work, "paper", None)
    rect = finish_rect(paper)
    x = rect.x2 if is_left_half_page_for_work(work, page_index) else rect.x
    return (x, rect.y), (x, rect.y2)
