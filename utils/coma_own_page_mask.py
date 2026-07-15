"""Current-page underlay cutout for coma blend files."""

from __future__ import annotations

from ..io import export_pipeline, export_soft_mask
from . import coma_content_mask, log

_logger = log.get_logger(__name__)


def apply_current_coma_cutout(src, coma, canvas_w_mm: float, canvas_h_mm: float):
    """Return ``src`` with the current coma area cut out.

    The cutout uses the same soft edge mask as page export so a coma whose
    border is set to "輪郭ぼかし" keeps its blurred transition in the page image.
    """
    split = split_current_coma_layers(src, coma, canvas_w_mm, canvas_h_mm)
    return split[0] if split is not None else None


def extract_current_coma_content(src, coma, canvas_w_mm: float, canvas_h_mm: float):
    """Return only the part of ``src`` inside the current coma."""
    split = split_current_coma_layers(src, coma, canvas_w_mm, canvas_h_mm)
    return split[1] if split is not None else None


def split_current_coma_layers(src, coma, canvas_w_mm: float, canvas_h_mm: float):
    """Split a page preview into outside-page and current-coma layers."""
    mask = _current_coma_mask(src, coma, canvas_w_mm, canvas_h_mm)
    if mask is None:
        return None
    inverse = mask.point(lambda px: 255 - int(px))
    return _apply_alpha_mask(src, inverse), _apply_alpha_mask(src, mask)


def _current_coma_mask(src, coma, canvas_w_mm: float, canvas_h_mm: float):
    Image = export_pipeline.Image
    ImageChops = export_pipeline.ImageChops
    ImageDraw = export_pipeline.ImageDraw
    ImageFilter = export_pipeline.ImageFilter
    if Image is None or ImageDraw is None:
        return None
    points_mm = coma_content_mask.coma_polygon_mm(coma)
    if len(points_mm) < 3:
        return None
    width_px, height_px = src.size
    if width_px <= 0 or height_px <= 0:
        return None
    bbox_mm = (
        0.0,
        0.0,
        max(1.0e-6, float(canvas_w_mm)),
        max(1.0e-6, float(canvas_h_mm)),
    )
    try:
        return export_soft_mask.coma_soft_edge_mask(
            Image,
            ImageChops,
            ImageDraw,
            ImageFilter,
            coma,
            points_mm,
            bbox_mm,
            (width_px, height_px),
            _effective_image_dpi(width_px, height_px, bbox_mm[2], bbox_mm[3]),
        ).convert("L")
    except Exception:  # noqa: BLE001
        _logger.exception("own page coma cutout mask failed")
        return None


def _apply_alpha_mask(src, mask):
    Image = export_pipeline.Image
    ImageChops = export_pipeline.ImageChops
    if Image is None:
        return None
    out = src.convert("RGBA").copy()
    alpha = out.getchannel("A")
    if ImageChops is not None:
        alpha = ImageChops.multiply(alpha, mask)
    else:
        clipped = Image.new("RGBA", out.size, (0, 0, 0, 0))
        clipped.paste(out, mask=mask)
        return clipped
    out.putalpha(alpha)
    return out


def _effective_image_dpi(width_px: int, height_px: int, width_mm: float, height_mm: float) -> int:
    dpi_x = float(width_px) / max(1.0e-6, float(width_mm)) * 25.4
    dpi_y = float(height_px) / max(1.0e-6, float(height_mm)) * 25.4
    return max(1, int(round((dpi_x + dpi_y) * 0.5)))
