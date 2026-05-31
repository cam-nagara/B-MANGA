"""Hit-test helpers for coma-masked content."""

from __future__ import annotations

from ..core.work import get_work
from . import layer_hierarchy, page_grid


def _point_segment_distance(point, start, end) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1.0e-12:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    nx = ax + dx * t
    ny = ay + dy * t
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5


def _point_in_coma_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if layer_hierarchy.point_in_polygon(point, polygon):
        return True
    return any(
        _point_segment_distance(point, start, polygon[(index + 1) % len(polygon)]) <= 0.25
        for index, start in enumerate(polygon)
    )


def _coma_id_matches(panel, coma_id: str) -> bool:
    return str(coma_id or "") in {
        str(getattr(panel, "id", "") or ""),
        str(getattr(panel, "coma_id", "") or ""),
    }


def local_point_visible_in_entry_parent(page, entry, x_mm: float, y_mm: float) -> bool:
    if str(getattr(entry, "parent_kind", "") or "") != "coma":
        return True
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if ":" not in parent_key:
        return True
    _page_id, coma_id = parent_key.split(":", 1)
    for panel in getattr(page, "comas", []) or []:
        if _coma_id_matches(panel, coma_id):
            polygon = layer_hierarchy.coma_polygon(panel)
            return len(polygon) < 3 or _point_in_coma_polygon((float(x_mm), float(y_mm)), polygon)
    return True


def world_point_visible_in_parent(context, parent_kind: str, parent_key: str, x_mm: float, y_mm: float) -> bool:
    parent_key = str(parent_key or "")
    if str(parent_kind or "") != "coma" and ":" not in parent_key:
        return True
    if ":" not in parent_key:
        return True
    page_id, coma_id = parent_key.split(":", 1)
    work = get_work(context)
    if work is None:
        return True
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") != page_id:
            continue
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
        local = (float(x_mm) - ox, float(y_mm) - oy)
        for panel in getattr(page, "comas", []) or []:
            if _coma_id_matches(panel, coma_id):
                polygon = layer_hierarchy.coma_polygon(panel)
                return len(polygon) < 3 or _point_in_coma_polygon(local, polygon)
    return True
