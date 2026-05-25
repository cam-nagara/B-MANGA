"""Non-destructive coma content opacity mask helpers."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Sequence

import bpy

from . import border_geom, log, object_naming as on
from .geom import mm_to_m, mm_to_px

_logger = log.get_logger(__name__)

MASK_IMAGE_PREFIX = "コマ内容マスク"
MASK_SPACE_PREFIX = "coma_content_mask_space_"
PROP_MASK_KIND = "bname_content_mask_kind"
PROP_MASK_PAGE_ID = "bname_content_mask_page_id"
PROP_MASK_COMA_ID = "bname_content_mask_coma_id"
PROP_MASK_PAGE_NUMBER = "bname_content_mask_page_number"
PROP_MASK_DPI = "bname_content_mask_dpi"
PROP_MASK_SIGNATURE = "bname_content_mask_signature"
PROP_MASK_BBOX_MM = "bname_content_mask_bbox_mm"

VIEWPORT_MASK_MAX_SIZE = 2048


@dataclass(frozen=True)
class ComaContentMask:
    image: bpy.types.Image
    space_object: bpy.types.Object
    name: str
    bbox_mm: tuple[float, float, float, float]
    dpi: int


def _token(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9A-Za-z_\-\u3040-\u30ff\u3400-\u9fff]+", "_", text)
    return text.strip("_") or "none"


def page_index(work, page) -> int:
    page_id = str(getattr(page, "id", "") or "")
    for index, candidate in enumerate(getattr(work, "pages", []) or []):
        if candidate == page:
            return index
        if page_id and str(getattr(candidate, "id", "") or "") == page_id:
            return index
    return 0


def page_number(work, page) -> int:
    try:
        start = int(getattr(getattr(work, "nombre", None), "start_number", 1) or 1)
    except Exception:  # noqa: BLE001
        start = 1
    return start + page_index(work, page)


def mask_image_name(work, page, coma, dpi: int | None = None) -> str:
    dpi_value = int(dpi or getattr(getattr(work, "paper", None), "dpi", 600) or 600)
    page_id = _token(getattr(page, "id", "page"))
    coma_id = _token(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "coma")
    return f"{MASK_IMAGE_PREFIX}_{page_id}_{page_number(work, page):04d}_{coma_id}_{dpi_value}dpi"


def _space_object_name(page, coma) -> str:
    return f"{MASK_SPACE_PREFIX}{_token(getattr(page, 'id', 'page'))}_{_token(getattr(coma, 'id', 'coma'))}"


def _base_coma_polygon_mm(coma) -> list[tuple[float, float]]:
    shape = str(getattr(coma, "shape_type", "rect") or "rect")
    if shape == "rect":
        x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
        w = max(0.0, float(getattr(coma, "rect_width_mm", 0.0) or 0.0))
        h = max(0.0, float(getattr(coma, "rect_height_mm", 0.0) or 0.0))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    verts = getattr(coma, "vertices", None)
    if shape == "polygon" and verts is not None and len(verts) >= 3:
        return [(float(v.x_mm), float(v.y_mm)) for v in verts]
    return []


def coma_polygon_mm(coma) -> list[tuple[float, float]]:
    pts = _base_coma_polygon_mm(coma)
    if len(pts) < 3:
        return pts
    border = getattr(coma, "border", None)
    corner_type = str(getattr(border, "corner_type", "square") or "square")
    radius_mm = float(getattr(border, "corner_radius_mm", 0.0) or 0.0)
    if corner_type == "square" or radius_mm <= 0.0:
        return pts
    try:
        styled = border_geom.styled_closed_path_mm(pts, corner_type, radius_mm)
        return styled if len(styled) >= 3 else pts
    except Exception:  # noqa: BLE001
        _logger.exception("coma content mask: styled polygon failed")
        return pts


def points_bbox_mm(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [float(x) for x, _y in points]
    ys = [float(y) for _x, y in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _soft_width_mm(coma) -> float:
    border = getattr(coma, "border", None)
    if border is None:
        return 0.0
    if not bool(getattr(coma, "paper_visible", True)):
        return 0.0
    if str(getattr(border, "style", "solid") or "solid") != "brush":
        return 0.0
    line_w = max(0.0, float(getattr(border, "width_mm", 0.0) or 0.0))
    blur = max(0.0, min(1.0, float(getattr(border, "blur_amount", 0.0) or 0.0)))
    return line_w * 0.5 * blur


def mask_bbox_mm(coma) -> tuple[float, float, float, float] | None:
    bbox = points_bbox_mm(coma_polygon_mm(coma))
    if bbox is None:
        return None
    pad = _soft_width_mm(coma)
    return (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)


def _page_by_id(work, page_id: str):
    for page in getattr(work, "pages", []) or []:
        if str(getattr(page, "id", "") or "") == page_id:
            return page
    return None


def _coma_by_id(page, coma_id: str):
    for coma in getattr(page, "comas", []) or []:
        if str(getattr(coma, "id", "") or "") == coma_id:
            return coma
    return None


def resolve_page_coma(work, parent_key: str, page_hint=None):
    key = str(parent_key or "")
    if ":" not in key:
        return None, None
    page_id, coma_id = key.split(":", 1)
    page = page_hint if str(getattr(page_hint, "id", "") or "") == page_id else _page_by_id(work, page_id)
    coma = _coma_by_id(page, coma_id) if page is not None else None
    return page, coma


def resolve_entry_page_coma(work, page_hint, entry):
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if parent_kind != "coma" and ":" not in parent_key:
        return None, None
    return resolve_page_coma(work, parent_key, page_hint)


def _page_offset_mm(work, scene, page) -> tuple[float, float]:
    try:
        from . import page_grid

        return page_grid.page_total_offset_mm(work, scene, page_index(work, page))
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def _image_signature(coma, bbox: tuple[float, float, float, float], size: tuple[int, int], dpi: int) -> str:
    border = getattr(coma, "border", None)
    payload = {
        "bbox": tuple(round(v, 5) for v in bbox),
        "poly": tuple((round(x, 5), round(y, 5)) for x, y in coma_polygon_mm(coma)),
        "size": size,
        "dpi": int(dpi),
        "paper": bool(getattr(coma, "paper_visible", True)),
        "style": str(getattr(border, "style", "") or ""),
        "width": round(float(getattr(border, "width_mm", 0.0) or 0.0), 5),
        "blur": round(float(getattr(border, "blur_amount", 0.0) or 0.0), 5),
        "curve": str(getattr(border, "blur_curve_points", "") or ""),
        "dither": bool(getattr(border, "blur_dither", False)),
    }
    return hashlib.sha1(repr(payload).encode("utf-8")).hexdigest()


def _mask_size(bbox: tuple[float, float, float, float], dpi: int) -> tuple[int, int, float]:
    width_px = max(1, int(round(mm_to_px(max(0.01, bbox[2] - bbox[0]), dpi))))
    height_px = max(1, int(round(mm_to_px(max(0.01, bbox[3] - bbox[1]), dpi))))
    scale = min(1.0, VIEWPORT_MASK_MAX_SIZE / max(width_px, height_px))
    if scale < 1.0:
        width_px = max(1, int(round(width_px * scale)))
        height_px = max(1, int(round(height_px * scale)))
    return width_px, height_px, scale


def _render_mask_pixels(coma, bbox: tuple[float, float, float, float], size: tuple[int, int], effective_dpi: float):
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Pillow が利用できません") from exc

    from ..io import export_soft_mask

    poly = coma_polygon_mm(coma)
    mask = export_soft_mask.coma_soft_edge_mask(
        Image,
        ImageChops,
        ImageDraw,
        ImageFilter,
        coma,
        poly,
        bbox,
        size,
        int(max(1, round(effective_dpi))),
    )
    return mask.convert("L")


def _ensure_image(name: str, width: int, height: int) -> bpy.types.Image:
    image = bpy.data.images.get(name)
    if image is not None and (image.size[0] != width or image.size[1] != height):
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass
        image = None
    if image is None:
        image = bpy.data.images.new(name, width=width, height=height, alpha=True, float_buffer=False)
    return image


def _set_image_from_mask(image: bpy.types.Image, mask) -> None:
    width, height = mask.size
    rows = []
    px = mask.load()
    for y in range(height - 1, -1, -1):
        for x in range(width):
            alpha = float(px[x, y]) / 255.0
            rows.extend((1.0, 1.0, 1.0, alpha))
    image.pixels.foreach_set(rows)
    image.update()


def _ensure_space_object(scene, work, page, coma, bbox: tuple[float, float, float, float]) -> bpy.types.Object:
    name = _space_object_name(page, coma)
    obj = bpy.data.objects.get(name)
    if obj is None:
        mesh = bpy.data.meshes.new(f"{name}_mesh")
        mesh.from_pydata(
            [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
            [],
            [(0, 1, 2, 3)],
        )
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
    ox_mm, oy_mm = _page_offset_mm(work, scene, page)
    cx = (bbox[0] + bbox[2]) * 0.5 + ox_mm
    cy = (bbox[1] + bbox[3]) * 0.5 + oy_mm
    obj.location = (mm_to_m(cx), mm_to_m(cy), 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.scale = (max(mm_to_m(bbox[2] - bbox[0]), 1.0e-6), max(mm_to_m(bbox[3] - bbox[1]), 1.0e-6), 1.0)
    obj[PROP_MASK_KIND] = "coma_content_space"
    obj[PROP_MASK_PAGE_ID] = str(getattr(page, "id", "") or "")
    obj[PROP_MASK_COMA_ID] = str(getattr(coma, "id", "") or "")
    obj[on.PROP_MANAGED] = False
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True
    try:
        obj.hide_set(True)
        obj.display_type = "WIRE"
    except Exception:  # noqa: BLE001
        pass
    target_colls = []
    try:
        from . import outliner_model

        coll = outliner_model.ensure_page_collection(
            scene,
            str(getattr(page, "id", "") or ""),
            str(getattr(page, "title", "") or getattr(page, "id", "") or ""),
        )
        if coll is not None:
            target_colls.append(coll)
    except Exception:  # noqa: BLE001
        pass
    if not target_colls:
        target_colls = [scene.collection]
    for coll in target_colls:
        if obj.name not in coll.objects.keys():
            try:
                coll.objects.link(obj)
            except Exception:  # noqa: BLE001
                pass
    for coll in tuple(getattr(obj, "users_collection", []) or []):
        if coll in target_colls:
            continue
        try:
            coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    return obj


def ensure_viewport_mask(
    scene: bpy.types.Scene,
    work,
    page,
    coma,
    *,
    dpi: int | None = None,
) -> ComaContentMask | None:
    if scene is None or work is None or page is None or coma is None:
        return None
    dpi_value = int(dpi or getattr(getattr(work, "paper", None), "dpi", 600) or 600)
    bbox = mask_bbox_mm(coma)
    if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    width, height, scale = _mask_size(bbox, dpi_value)
    name = mask_image_name(work, page, coma, dpi_value)
    signature = _image_signature(coma, bbox, (width, height), dpi_value)
    image = _ensure_image(name, width, height)
    if str(image.get(PROP_MASK_SIGNATURE, "") or "") != signature:
        mask = _render_mask_pixels(coma, bbox, (width, height), dpi_value * scale)
        _set_image_from_mask(image, mask)
        image[PROP_MASK_SIGNATURE] = signature
    image[PROP_MASK_KIND] = "coma_content"
    image[PROP_MASK_PAGE_ID] = str(getattr(page, "id", "") or "")
    image[PROP_MASK_COMA_ID] = str(getattr(coma, "id", "") or "")
    image[PROP_MASK_PAGE_NUMBER] = int(page_number(work, page))
    image[PROP_MASK_DPI] = int(dpi_value)
    image[PROP_MASK_BBOX_MM] = ",".join(f"{v:.6f}" for v in bbox)
    space = _ensure_space_object(scene, work, page, coma, bbox)
    space[PROP_MASK_SIGNATURE] = signature
    return ComaContentMask(image=image, space_object=space, name=name, bbox_mm=bbox, dpi=dpi_value)


def ensure_viewport_mask_for_parent(
    scene: bpy.types.Scene,
    work,
    parent_key: str,
    *,
    page_hint=None,
) -> ComaContentMask | None:
    page, coma = resolve_page_coma(work, parent_key, page_hint)
    if page is None or coma is None:
        return None
    return ensure_viewport_mask(scene, work, page, coma)


def ensure_viewport_mask_for_entry(
    scene: bpy.types.Scene,
    work,
    page_hint,
    entry,
) -> ComaContentMask | None:
    page, coma = resolve_entry_page_coma(work, page_hint, entry)
    if page is None or coma is None:
        return None
    return ensure_viewport_mask(scene, work, page, coma)
