"""現在開いているページを示す専用オーバーレイ枠。

ページファイルではページ実体の外周を POST_VIEW、コマファイルでは
カメラ下絵の外周を POST_PIXEL で描く。選択中ページとは別の概念なので、
``active_page_index`` ではなく現在の ``.blend`` パスに含まれる page_id を
唯一の正本とする。
"""

from __future__ import annotations

from typing import Optional

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work
from ..utils import log, page_file_scene, page_grid, paths, viewport_colors
from ..utils.geom import Rect, mm_to_m
from . import overlay_coma_page_labels, overlay_visibility

_logger = log.get_logger(__name__)

_handle_view: Optional[object] = None
_handle_pixel: Optional[object] = None

# 既存の選択枠はキャンバスから約 5.4 / 6.8 mm 外側にある。
# 現在ページ枠をその内側へ置き、両方が同時に見える間隔を保つ。
_WORLD_OUTER_OUTSET_MM = 3.0
_WORLD_OUTER_WIDTH_MM = 1.2
_WORLD_INNER_OUTSET_MM = 1.5
_WORLD_INNER_WIDTH_MM = 0.6

_PIXEL_OUTER_INSET = 3.0
_PIXEL_OUTER_WIDTH = 4.0
_PIXEL_INNER_INSET = 7.0
_PIXEL_INNER_WIDTH = 2.0

_PAGE_OVERVIEW_BG_PROP = "_bmanga_page_overview_bg"
_BACKGROUND_KIND_PRIORITY = {"own_page": 0, "name": 1, "koma": 2}


def _overlay_enabled(scene) -> bool:
    return bool(scene is not None and getattr(scene, "bmanga_overlay_enabled", True))


def _current_page_id_for_role(context, expected_role: str) -> str:
    """ファイルパスから現在ページIDを解決する。Sceneの選択状態は見ない。"""
    try:
        role, page_id, _coma_id = page_file_scene.current_role(context)
    except Exception:  # noqa: BLE001
        return ""
    if role != expected_role or not paths.is_valid_page_id(page_id):
        return ""
    return str(page_id)


def _current_page_entry(work, page_id: str):
    index = page_file_scene.find_page_index(work, page_id)
    if not (0 <= index < len(getattr(work, "pages", []) or [])):
        return -1, None
    return index, work.pages[index]


def _page_world_rect(work, scene, page_index: int) -> Rect | None:
    if work is None or scene is None or not (0 <= page_index < len(work.pages)):
        return None
    paper = getattr(work, "paper", None)
    if paper is None:
        return None
    canvas_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    canvas_height = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)
    if canvas_width <= 0.0 or canvas_height <= 0.0:
        return None
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, page_index)
    width_mm = page_grid.page_content_width_mm(work, page_index, canvas_width)
    if width_mm <= 0.0:
        return None
    return Rect(float(ox_mm), float(oy_mm), float(width_mm), canvas_height)


def _rect_band_quads(rect: Rect, width: float) -> list[Rect]:
    half = max(0.001, float(width)) * 0.5
    return [
        Rect(rect.x - half, rect.y2 - half, rect.width + half * 2.0, half * 2.0),
        Rect(rect.x - half, rect.y - half, rect.width + half * 2.0, half * 2.0),
        Rect(rect.x - half, rect.y - half, half * 2.0, rect.height + half * 2.0),
        Rect(rect.x2 - half, rect.y - half, half * 2.0, rect.height + half * 2.0),
    ]


def _draw_world_rect_outline(rect: Rect, color: tuple[float, ...], width_mm: float) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for band in _rect_band_quads(rect, width_mm):
        base = len(verts)
        verts.extend(
            [
                (mm_to_m(band.x), mm_to_m(band.y), 0.0),
                (mm_to_m(band.x2), mm_to_m(band.y), 0.0),
                (mm_to_m(band.x2), mm_to_m(band.y2), 0.0),
                (mm_to_m(band.x), mm_to_m(band.y2), 0.0),
            ]
        )
        indices.extend(((base, base + 1, base + 2), (base, base + 2, base + 3)))
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_page_file_outline() -> None:
    context = bpy.context
    scene = getattr(context, "scene", None)
    if not _overlay_enabled(scene):
        return
    page_id = _current_page_id_for_role(context, page_file_scene.ROLE_PAGE)
    if not page_id:
        return
    work = get_work(context)
    if work is None or not bool(getattr(work, "loaded", False)):
        return
    page_index, page = _current_page_entry(work, page_id)
    if page is None or not overlay_visibility.page_visible(page):
        return
    rect = _page_world_rect(work, scene, page_index)
    if rect is None:
        return
    region = getattr(context, "region", None)
    rv3d = getattr(context, "region_data", None)
    if not overlay_visibility.rect_may_be_visible_in_region(rect, region, rv3d):
        return

    previous_blend = None
    previous_depth = None
    try:
        previous_blend = gpu.state.blend_get()
        previous_depth = gpu.state.depth_test_get()
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        _draw_world_rect_outline(
            rect.inset(-_WORLD_OUTER_OUTSET_MM),
            viewport_colors.CURRENT_PAGE_STRONG,
            _WORLD_OUTER_WIDTH_MM,
        )
        _draw_world_rect_outline(
            rect.inset(-_WORLD_INNER_OUTSET_MM),
            viewport_colors.CURRENT_PAGE,
            _WORLD_INNER_WIDTH_MM,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("current page POST_VIEW outline failed")
    finally:
        try:
            gpu.state.depth_test_set(previous_depth or "LESS_EQUAL")
        except Exception:  # noqa: BLE001
            pass
        try:
            gpu.state.blend_set(previous_blend or "NONE")
        except Exception:  # noqa: BLE001
            pass


def _background_metadata(bg) -> tuple[int, str] | None:
    if not bool(getattr(bg, "show_background_image", True)):
        return None
    image = getattr(bg, "image", None)
    if image is None:
        return None
    try:
        if not bool(image.get(_PAGE_OVERVIEW_BG_PROP, False)):
            return None
        kind = str(image.get("bmanga_kind", "") or "")
        page_id = str(image.get("bmanga_page_id", "") or "")
    except Exception:  # noqa: BLE001
        return None
    priority = _BACKGROUND_KIND_PRIORITY.get(kind)
    if priority is None or not paths.is_valid_page_id(page_id):
        return None
    return priority, page_id


def _current_coma_background_rect(scene, region, rv3d, page_id: str):
    frame_rect = overlay_coma_page_labels._camera_frame_pixel_rect(scene, region, rv3d)
    if frame_rect is None:
        return None
    camera = getattr(scene, "camera", None)
    cam_data = getattr(camera, "data", None)
    if cam_data is None or not bool(getattr(cam_data, "show_background_images", True)):
        return None
    candidates = []
    for bg in getattr(cam_data, "background_images", []) or []:
        metadata = _background_metadata(bg)
        if metadata is None or metadata[1] != page_id:
            continue
        screen_rect = overlay_coma_page_labels._background_screen_rect(frame_rect, bg)
        if screen_rect is not None:
            candidates.append((metadata[0], screen_rect))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _inset_pixel_rect(rect, amount: float):
    x0, y0, x1, y1 = (float(value) for value in rect)
    inset = max(0.0, float(amount))
    if x1 - x0 <= inset * 2.0 or y1 - y0 <= inset * 2.0:
        return None
    return x0 + inset, y0 + inset, x1 - inset, y1 - inset


def _pixel_rect_band_quads(rect, width: float):
    x0, y0, x1, y1 = (float(value) for value in rect)
    half = max(0.5, float(width) * 0.5)
    return (
        (x0 - half, y1 - half, x1 + half, y1 + half),
        (x0 - half, y0 - half, x1 + half, y0 + half),
        (x0 - half, y0 - half, x0 + half, y1 + half),
        (x1 - half, y0 - half, x1 + half, y1 + half),
    )


def _draw_pixel_rect_outline(rect, color: tuple[float, ...], width_px: float) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts: list[tuple[float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for x0, y0, x1, y1 in _pixel_rect_band_quads(rect, width_px):
        base = len(verts)
        verts.extend(((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
        indices.extend(((base, base + 1, base + 2), (base, base + 2, base + 3)))
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_coma_file_outline() -> None:
    context = bpy.context
    scene = getattr(context, "scene", None)
    if not _overlay_enabled(scene):
        return
    page_id = _current_page_id_for_role(context, page_file_scene.ROLE_COMA)
    if not page_id:
        return
    work = get_work(context)
    if work is None or not bool(getattr(work, "loaded", False)):
        return
    region = getattr(context, "region", None)
    rv3d = getattr(context, "region_data", None)
    if region is None or rv3d is None:
        return
    rect = _current_coma_background_rect(scene, region, rv3d, page_id)
    if rect is None:
        return
    outer = _inset_pixel_rect(rect, _PIXEL_OUTER_INSET)
    inner = _inset_pixel_rect(rect, _PIXEL_INNER_INSET)
    if outer is None or inner is None:
        return

    previous_blend = None
    previous_depth = None
    try:
        previous_blend = gpu.state.blend_get()
        previous_depth = gpu.state.depth_test_get()
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        _draw_pixel_rect_outline(
            outer,
            viewport_colors.CURRENT_PAGE_STRONG,
            _PIXEL_OUTER_WIDTH,
        )
        _draw_pixel_rect_outline(
            inner,
            viewport_colors.CURRENT_PAGE,
            _PIXEL_INNER_WIDTH,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("current page POST_PIXEL outline failed")
    finally:
        try:
            gpu.state.depth_test_set(previous_depth or "NONE")
        except Exception:  # noqa: BLE001
            pass
        try:
            gpu.state.blend_set(previous_blend or "NONE")
        except Exception:  # noqa: BLE001
            pass


def register() -> None:
    global _handle_view, _handle_pixel
    if _handle_view is None:
        _handle_view = bpy.types.SpaceView3D.draw_handler_add(
            _draw_page_file_outline,
            (),
            "WINDOW",
            "POST_VIEW",
        )
    if _handle_pixel is None:
        _handle_pixel = bpy.types.SpaceView3D.draw_handler_add(
            _draw_coma_file_outline,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
    _logger.debug("current page outline handlers registered")


def unregister() -> None:
    global _handle_view, _handle_pixel
    if _handle_pixel is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle_pixel, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle_pixel = None
    if _handle_view is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle_view, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle_view = None
    _logger.debug("current page outline handlers removed")
