"""コマファイルのカメラ下絵に合わせたページ番号・作品情報表示。"""

from __future__ import annotations

import blf

from ..utils import page_preview_object
from . import overlay_visibility
from . import overlay_work_info

_PAGE_HEADER_GAP_MM = 6.0
_PAGE_HEADER_FONT_SIZE_PX = 34
_PAGE_HEADER_COLOR = (0.0, 0.0, 0.0, 0.95)
_PAGE_HEADER_OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.9)


def draw(context, work, paper, scene, region, rv3d) -> None:
    frame_rect = _camera_frame_pixel_rect(scene, region, rv3d)
    if frame_rect is None:
        return
    camera = getattr(scene, "camera", None)
    cam_data = getattr(camera, "data", None)
    if cam_data is None:
        return
    rects = page_preview_object.preview_rects_mm(scene, work)
    if not rects:
        return
    pages_by_id = {
        str(getattr(page, "id", "") or ""): (idx, page)
        for idx, page in enumerate(getattr(work, "pages", []) or [])
    }
    page_height_mm = max(1.0, float(getattr(paper, "canvas_height_mm", 1.0) or 1.0))
    for bg in getattr(cam_data, "background_images", []) or []:
        if not bool(getattr(bg, "show_background_image", True)):
            continue
        image = getattr(bg, "image", None)
        if image is None:
            continue
        try:
            kind = str(image.get("bmanga_kind", "") or "")
            page_id = str(image.get("bmanga_page_id", "") or "")
        except Exception:  # noqa: BLE001
            continue
        if kind not in {"name", "own_page"} or not page_id:
            continue
        page_info = pages_by_id.get(page_id)
        rect_info = rects.get(page_id)
        if page_info is None or rect_info is None:
            continue
        page_index, page = page_info
        if not overlay_visibility.page_visible(page):
            continue
        screen_rect = _background_screen_rect(frame_rect, bg)
        if screen_rect is None:
            continue
        _idx, x0, _y0, x1, _y1 = rect_info
        page_width_mm = max(1.0, float(x1 - x0))
        _draw_page_header_number(context, work, page_index, screen_rect, page_height_mm, region)
        overlay_work_info.draw_for_page_screen_rect(
            context,
            work,
            paper,
            page,
            page_index,
            screen_rect,
            page_width_mm,
            page_height_mm,
        )


def _camera_frame_pixel_rect(scene, region, rv3d) -> tuple[float, float, float, float] | None:
    if scene is None or region is None or rv3d is None:
        return None
    if getattr(rv3d, "view_perspective", "") != "CAMERA":
        return None
    camera = getattr(scene, "camera", None)
    cam_data = getattr(camera, "data", None)
    if camera is None or cam_data is None:
        return None
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    coords = []
    try:
        frame = cam_data.view_frame(scene=scene)
        matrix = camera.matrix_world
    except Exception:  # noqa: BLE001
        return None
    for corner in frame:
        try:
            coord = location_3d_to_region_2d(region, rv3d, matrix @ corner)
        except Exception:  # noqa: BLE001
            coord = None
        if coord is None:
            return None
        coords.append(coord)
    xs = [float(c.x) for c in coords]
    ys = [float(c.y) for c in coords]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _background_screen_rect(
    frame_rect: tuple[float, float, float, float],
    bg,
) -> tuple[float, float, float, float] | None:
    image = getattr(bg, "image", None)
    if image is None:
        return None
    try:
        img_w = max(1.0, float(image.size[0]))
        img_h = max(1.0, float(image.size[1]))
    except Exception:  # noqa: BLE001
        return None
    fx0, fy0, fx1, fy1 = frame_rect
    frame_w = max(1.0, fx1 - fx0)
    frame_h = max(1.0, fy1 - fy0)
    image_aspect = img_w / img_h
    frame_aspect = frame_w / frame_h
    if image_aspect >= frame_aspect:
        base_w = frame_w
        base_h = frame_w / image_aspect
    else:
        base_h = frame_h
        base_w = frame_h * image_aspect
    try:
        scale = max(0.0001, float(getattr(bg, "scale", 1.0) or 1.0))
        offset = getattr(bg, "offset", (0.0, 0.0))
        off_x = float(offset[0])
        off_y = float(offset[1])
    except Exception:  # noqa: BLE001
        scale = 1.0
        off_x = 0.0
        off_y = 0.0
    cx = (fx0 + fx1) * 0.5 + off_x * base_w
    cy = (fy0 + fy1) * 0.5 + off_y * base_h
    width = base_w * scale
    height = base_h * scale
    return cx - width * 0.5, cy - height * 0.5, cx + width * 0.5, cy + height * 0.5


def _draw_page_header_number(context, work, page_index, page_rect_px, page_height_mm, region) -> None:
    x0, y0, x1, y1 = page_rect_px
    if not (-300.0 < x1 and x0 < float(region.width) + 300.0):
        return
    if not (-300.0 < y1 and y0 < float(region.height) + 300.0):
        return
    text = _format_page_header_number(page_index, work)
    try:
        blf.size(0, _PAGE_HEADER_FONT_SIZE_PX)
        tw, th = blf.dimensions(0, text)
    except Exception:  # noqa: BLE001
        tw, th = 0.0, float(_PAGE_HEADER_FONT_SIZE_PX)
    gap_px = _PAGE_HEADER_GAP_MM * max(1.0, y1 - y0) / max(1.0, float(page_height_mm))
    sx = (x0 + x1) * 0.5 - tw * 0.5
    sy = y1 + gap_px - th * 0.5
    _draw_bold_text(text, sx, sy)


def _draw_bold_text(text: str, x_px: float, y_px: float) -> None:
    outline_offsets = (
        (-2.0, -2.0), (-2.0, 0.0), (-2.0, 2.0),
        (0.0, -2.0), (0.0, 2.0),
        (2.0, -2.0), (2.0, 0.0), (2.0, 2.0),
    )
    try:
        blf.color(0, *_PAGE_HEADER_OUTLINE_COLOR)
    except Exception:  # noqa: BLE001
        pass
    for dx, dy in outline_offsets:
        blf.position(0, x_px + dx, y_px + dy, 0.0)
        blf.draw(0, text)
    try:
        blf.color(0, *_PAGE_HEADER_COLOR)
    except Exception:  # noqa: BLE001
        pass
    for dx, dy in ((0.0, 0.0), (0.9, 0.0), (0.0, 0.9), (0.9, 0.9)):
        blf.position(0, x_px + dx, y_px + dy, 0.0)
        blf.draw(0, text)


def _format_page_header_number(page_index: int, work=None) -> str:
    try:
        start = int(getattr(getattr(work, "work_info", None), "page_number_start", 1))
    except Exception:  # noqa: BLE001
        start = 1
    page_number = max(0, start + int(page_index))
    try:
        paper = getattr(work, "paper", None) if work is not None else None
        pages = getattr(work, "pages", None) if work is not None else None
        if paper is not None and pages is not None and 0 <= int(page_index) < len(pages):
            from ..core.paper import format_page_entry_display_label
            return format_page_entry_display_label(paper, pages[int(page_index)])
        if paper is not None:
            from ..core.paper import format_page_display_label
            return format_page_display_label(paper, page_number)
    except Exception:  # noqa: BLE001
        pass
    return f"{page_number:04d}"
