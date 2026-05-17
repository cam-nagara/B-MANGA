"""ページ一覧用のカメラ同期."""

from __future__ import annotations

import bpy

from . import log, object_naming as on, outliner_model as om, page_range
from .geom import mm_to_m

_logger = log.get_logger(__name__)

OVERVIEW_CAMERA_NAME = "B-Name ページ一覧カメラ"
PROP_OVERVIEW_CAMERA = "bname_overview_camera"
_CAMERA_FIT_MARGIN = 2.50


def _visible_pages_bbox_mm(work, scene) -> tuple[float, float, float, float] | None:
    if work is None or scene is None:
        return None
    from . import page_grid

    paper = getattr(work, "paper", None)
    if paper is None:
        return None
    cw = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    ch = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)
    if cw <= 0.0 or ch <= 0.0:
        return None
    min_x = min_y = max_x = max_y = None
    for index, page in enumerate(getattr(work, "pages", []) or []):
        if not page_range.page_in_range(page):
            continue
        ox, oy = page_grid.page_total_offset_mm(work, scene, index)
        x0, y0 = ox, oy
        x1, y1 = ox + cw, oy + ch
        min_x = x0 if min_x is None else min(min_x, x0)
        min_y = y0 if min_y is None else min(min_y, y0)
        max_x = x1 if max_x is None else max(max_x, x1)
        max_y = y1 if max_y is None else max(max_y, y1)
    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return min_x, min_y, max_x - min_x, max_y - min_y


def _camera_aspect(scene) -> float:
    render = getattr(scene, "render", None)
    if render is None:
        return 16.0 / 9.0
    res_x = float(getattr(render, "resolution_x", 1920) or 1920)
    res_y = float(getattr(render, "resolution_y", 1080) or 1080)
    pixel_x = float(getattr(render, "pixel_aspect_x", 1.0) or 1.0)
    pixel_y = float(getattr(render, "pixel_aspect_y", 1.0) or 1.0)
    return max(0.01, (res_x * pixel_x) / max(1.0, res_y * pixel_y))


def _ensure_camera_object(scene) -> bpy.types.Object:
    obj = bpy.data.objects.get(OVERVIEW_CAMERA_NAME)
    if obj is not None and obj.type != "CAMERA":
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        obj = None
    if obj is None:
        cam_data = bpy.data.cameras.new(OVERVIEW_CAMERA_NAME)
        obj = bpy.data.objects.new(OVERVIEW_CAMERA_NAME, cam_data)
    obj[PROP_OVERVIEW_CAMERA] = True
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    root = om.ensure_root_collection(scene)
    if root is not None and not any(existing is obj for existing in root.objects):
        try:
            root.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("overview camera link failed")
    for coll in tuple(obj.users_collection):
        if coll is root:
            continue
        try:
            coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    return obj


def ensure_overview_camera(scene, work) -> bpy.types.Object | None:
    """全ページ一覧を収める正投影カメラを作成・更新する."""
    if scene is None or work is None or not bool(getattr(work, "loaded", False)):
        return None
    bbox = _visible_pages_bbox_mm(work, scene)
    if bbox is None:
        return None
    x, y, w, h = bbox
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0) or 30.0)
    pad = max(4.0, gap * 0.18)
    aspect = _camera_aspect(scene)
    target_w = max(1.0, w + pad * 2.0)
    target_h = max(1.0, h + pad * 2.0)
    # 「全ページを一覧表示」のビューポートフィットは、選択フィット由来の
    # 余白を残す。カメラビューだけキャンバス外周ぴったりにするとトンボや
    # ページ配置の見え方が一致しないため、同程度の余白込みで撮影する。
    ortho_h_mm = max(target_h, target_w / aspect) * _CAMERA_FIT_MARGIN
    cx = x + w * 0.5
    cy = y + h * 0.5

    obj = _ensure_camera_object(scene)
    current_roll = float(getattr(obj.rotation_euler, "z", 0.0) or 0.0)
    cam = obj.data
    cam.type = "ORTHO"
    cam.ortho_scale = mm_to_m(ortho_h_mm)
    obj.location = (mm_to_m(cx), mm_to_m(cy), 10.0)
    obj.rotation_euler = (0.0, 0.0, current_roll)
    scene.camera = obj
    obj.hide_viewport = False
    obj.hide_render = False
    return obj
