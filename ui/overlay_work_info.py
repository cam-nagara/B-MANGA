"""作品情報テキストのGPUオーバーレイ描画 (POST_PIXEL, blf)."""

from __future__ import annotations

import blf

from . import overlay_shared
from ..utils import page_range, text_style
from ..utils.geom import mm_to_m, q_to_mm

_FONT_ID_CACHE: dict[str, int] = {}


def draw_for_page(
    context,
    work,
    paper,
    page,
    page_index: int,
    ox_mm: float,
    oy_mm: float,
    region,
    rv3d,
) -> None:
    if region is None or rv3d is None:
        return
    import bpy as _bpy
    scene = getattr(_bpy.context, "scene", None)
    if scene is not None and not bool(getattr(scene, "bmanga_page_work_info_visible", True)):
        return
    info = getattr(work, "work_info", None)
    if info is None:
        return
    if not bool(getattr(info, "display_visible", True)):
        return
    if not page_range.page_in_range(page):
        return

    bleed_rect = overlay_shared.compute_paper_rects(paper).bleed
    font_path = _work_info_font_path(work)
    font_id = _get_font_id(font_path)

    for item_key, item, text in _text_items(info, page_index, paper, page):
        if item is None or not bool(getattr(item, "enabled", False)) or not text:
            continue
        position = str(getattr(item, "position", "bottom-left") or "bottom-left")
        x_mm, y_mm, align_x, align_y = _anchor(bleed_rect, position)
        abs_x_mm = x_mm + ox_mm
        abs_y_mm = y_mm + oy_mm
        q_size = max(0.1, float(getattr(item, "font_size_q", 20.0) or 20.0))
        item_color = getattr(item, "color", (1.0, 1.0, 1.0, 1.0))
        _draw_text_item(
            font_id, text, abs_x_mm, abs_y_mm,
            q_size, item_color, align_x, align_y,
            region, rv3d,
        )


def draw_for_page_screen_rect(
    context,
    work,
    paper,
    page,
    page_index: int,
    page_rect_px: tuple[float, float, float, float],
    page_width_mm: float,
    page_height_mm: float,
) -> None:
    import bpy as _bpy
    scene = getattr(_bpy.context, "scene", None)
    if scene is not None and not bool(getattr(scene, "bmanga_page_work_info_visible", True)):
        return
    info = getattr(work, "work_info", None)
    if info is None:
        return
    if not bool(getattr(info, "display_visible", True)):
        return
    if not page_range.page_in_range(page):
        return

    x0, y0, x1, y1 = page_rect_px
    width_px = max(1.0, x1 - x0)
    height_px = max(1.0, y1 - y0)
    page_width_mm = max(1.0, float(page_width_mm))
    page_height_mm = max(1.0, float(page_height_mm))
    bleed_rect = overlay_shared.compute_paper_rects(paper).bleed
    font_id = _get_font_id(_work_info_font_path(work))

    for item_key, item, text in _text_items(info, page_index, paper, page):
        if item is None or not bool(getattr(item, "enabled", False)) or not text:
            continue
        position = str(getattr(item, "position", "bottom-left") or "bottom-left")
        x_mm, y_mm, align_x, align_y = _anchor(bleed_rect, position)
        px_x = x0 + (float(x_mm) / page_width_mm) * width_px
        px_y = y0 + (float(y_mm) / page_height_mm) * height_px
        q_size = max(0.1, float(getattr(item, "font_size_q", 20.0) or 20.0))
        px_size = max(1.0, q_to_mm(q_size) * width_px / page_width_mm)
        item_color = getattr(item, "color", (1.0, 1.0, 1.0, 1.0))
        _draw_text_item_pixel(font_id, text, px_x, px_y, px_size, item_color, align_x, align_y)


def _draw_text_item(
    font_id, text, x_mm, y_mm,
    font_size_q, color, align_x, align_y,
    region, rv3d,
) -> None:
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    pos = location_3d_to_region_2d(
        region, rv3d,
        Vector((mm_to_m(x_mm), mm_to_m(y_mm), 0.0)),
    )
    if pos is None:
        return
    px_x, px_y = float(pos.x), float(pos.y)

    mm_size = q_to_mm(font_size_q)
    p0 = location_3d_to_region_2d(region, rv3d, Vector((0.0, 0.0, 0.0)))
    p1 = location_3d_to_region_2d(region, rv3d, Vector((mm_to_m(mm_size), 0.0, 0.0)))
    if p0 is None or p1 is None:
        return
    px_size = max(1.0, abs(float(p1.x) - float(p0.x)))

    _draw_text_item_pixel(font_id, text, px_x, px_y, px_size, color, align_x, align_y)


def _draw_text_item_pixel(
    font_id, text, px_x, px_y,
    px_size, color, align_x, align_y,
) -> None:
    try:
        blf.size(font_id, px_size)
    except Exception:  # noqa: BLE001
        return

    tw, th = blf.dimensions(font_id, text)

    if align_x == "RIGHT":
        px_x -= tw
    elif align_x == "CENTER":
        px_x -= tw * 0.5

    if align_y == "TOP":
        px_y -= th
    elif align_y == "CENTER":
        px_y -= th * 0.5

    a = float(color[3]) if len(color) > 3 else 1.0
    try:
        blf.color(font_id, float(color[0]), float(color[1]), float(color[2]), a)
    except Exception:  # noqa: BLE001
        pass
    blf.position(font_id, px_x, px_y, 0.0)
    blf.draw(font_id, text)


# -- テキスト項目・アンカー計算 (work_info_text_object.py から移植) --


def _text_items(info, page_index, paper, page_entry) -> list[tuple[str, object, str]]:
    page_text = ""
    try:
        if paper is not None and page_entry is not None:
            from ..core.paper import format_page_entry_display_label
            page_text = format_page_entry_display_label(paper, page_entry)
        else:
            page_number = int(info.page_number_start) + int(page_index)
            if paper is not None:
                from ..core.paper import format_page_display_label
                page_text = format_page_display_label(paper, page_number)
            else:
                page_text = f"ページ{page_number:04d}"
    except Exception:  # noqa: BLE001
        page_text = ""
    return [
        ("work_name", info.display_work_name, str(getattr(info, "work_name", "") or "")),
        (
            "episode",
            info.display_episode,
            f"第{int(info.episode_number)}話" if int(getattr(info, "episode_number", 0) or 0) else "",
        ),
        ("subtitle", info.display_subtitle, str(getattr(info, "subtitle", "") or "")),
        ("author", info.display_author, str(getattr(info, "author", "") or "")),
        ("page_number", info.display_page_number, page_text),
    ]


def _anchor(anchor_rect, position: str) -> tuple[float, float, str, str]:
    pad = 2.0
    if position.endswith("right"):
        x_mm = anchor_rect.x2
        align_x = "RIGHT"
    elif position.endswith("center"):
        x_mm = (anchor_rect.x + anchor_rect.x2) * 0.5
        align_x = "CENTER"
    else:
        x_mm = anchor_rect.x
        align_x = "LEFT"
    if position.startswith("top"):
        y_mm = anchor_rect.y2 + pad
        align_y = "BOTTOM"
    else:
        y_mm = anchor_rect.y - pad
        align_y = "TOP"
    return x_mm, y_mm, align_x, align_y


def _work_info_font_path(work=None) -> str:
    preferred = ""
    if work is not None:
        info = getattr(work, "work_info", None)
        if info is not None:
            preferred = str(getattr(info, "font", "") or "")
    return text_style.resolve_font_path(preferred)


def _get_font_id(font_path: str) -> int:
    from . import overlay as _ov
    return _ov._get_font_id_for_path(font_path)
