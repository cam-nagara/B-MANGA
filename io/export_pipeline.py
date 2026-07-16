"""書き出しパイプライン.

Pillow ベースで各要素を個別ラスタ化し、通常画像では合成、PSD では
レイヤー構造を保持して書き出す。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Any, Sequence

from . import export_group_masks, export_psd, export_raster, export_soft_mask, export_stack_order
from ..ui import overlay_shared
from ..utils import (
    border_geom,
    color_space,
    log,
    coma_content_mask,
    coma_preview,
    page_grid,
    percentage,
    spread_merge_geometry,
    text_style,
)
from ..utils.geom import Rect, m_to_mm, mm_to_px, q_to_mm

_logger = log.get_logger(__name__)

# FONT オブジェクト側の作品情報表示と同じ見た目に寄せるための補正。
# 日本語フォントでは Q 数をそのまま mm 換算すると字面が小さく出る。
_WORK_INFO_Q_VISIBLE_HEIGHT_COMPENSATION = 1.78

try:
    from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageEnhance, ImageFilter, ImageFont  # type: ignore

    _HAS_PIL = True
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageChops = None  # type: ignore
    ImageCms = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageEnhance = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageFont = None  # type: ignore
    _HAS_PIL = False

try:
    import pypdf  # type: ignore

    _HAS_PYPDF = True
except ImportError:  # pragma: no cover
    pypdf = None  # type: ignore
    _HAS_PYPDF = False

def has_pillow() -> bool:
    return _HAS_PIL


def has_pypdf() -> bool:
    return _HAS_PYPDF


def has_psd_tools() -> bool:
    return export_psd.has_psd_tools()


def can_write_layered_psd() -> bool:
    return export_psd.can_write_layered_psd()


@dataclass(frozen=True)
class ExportOptions:
    color_mode: str = "rgb"  # "rgb" | "monochrome" | "grayscale" | "cmyk"
    format: str = "png"  # "png" | "jpeg" | "tiff" | "pdf" | "psd"
    area: str = "withBleed"  # "finish" | "withBleed" | "innerFrame" | "canvas"
    dpi_override: int = 0
    include_border: bool = True
    include_white_margin: bool = True
    include_nombre: bool = True
    include_work_info: bool = True
    include_tombo: bool = False
    include_paper_color: bool = True
    include_coma_backgrounds: bool = True
    include_coma_previews: bool = True
    coma_preview_side: str = "all"  # "all" | "front" | "back"
    include_page_overlay_fills: bool = False
    icc_profile_path: str = ""


@dataclass(frozen=True)
class ExportLayer:
    name: str
    image: Any
    left: int
    top: int
    group_path: tuple[str, ...] = ()
    visible: bool = True
    opacity: int = 255
    blend_mode: str = "normal"
    stack_uid: str = ""
    stack_parent_key: str = ""

    @property
    def right(self) -> int:
        return self.left + self.image.width

    @property
    def bottom(self) -> int:
        return self.top + self.image.height


@dataclass(frozen=True)
class ExportMask:
    image: Any
    left: int
    top: int
    name: str = ""

    @property
    def right(self) -> int:
        return self.left + self.image.width

    @property
    def bottom(self) -> int:
        return self.top + self.image.height


@dataclass(frozen=True)
class _LayerCanvas:
    image: Any
    left: int
    top: int
    canvas_height_px: int
    dpi: int

    def point_px(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        x_px = int(round(mm_to_px(x_mm, self.dpi))) - self.left
        y_px = self.canvas_height_px - int(round(mm_to_px(y_mm, self.dpi))) - self.top
        return (x_px, y_px)

    def points_px(self, pts: Sequence[tuple[float, float]]) -> list[tuple[int, int]]:
        return [self.point_px(x, y) for x, y in pts]


def _dpi(paper, options: ExportOptions) -> int:
    return options.dpi_override if options.dpi_override > 0 else int(paper.dpi)


def _canvas_size_px(paper, options: ExportOptions) -> tuple[int, int]:
    dpi = _dpi(paper, options)
    w = int(round(mm_to_px(paper.canvas_width_mm, dpi)))
    h = int(round(mm_to_px(paper.canvas_height_mm, dpi)))
    return (w, h)


def _page_canvas_size_px(work, page, options: ExportOptions) -> tuple[int, int]:
    w, h = _canvas_size_px(work.paper, options)
    if bool(getattr(page, "spread", False)):
        dpi = _dpi(work.paper, options)
        width_mm = page_grid.spread_content_width_mm(
            page,
            float(getattr(work.paper, "canvas_width_mm", 0.0) or 0.0),
            float(getattr(work.paper, "finish_width_mm", 0.0) or 0.0),
        )
        return (int(round(mm_to_px(width_mm, dpi))), h)
    return (w, h)


def _area_rect_px(paper, options: ExportOptions, *, is_left_half: bool = False) -> tuple[int, int, int, int]:
    dpi = _dpi(paper, options)
    rects = overlay_shared.compute_paper_rects(paper, is_left_half=is_left_half)
    w_px, h_px = _canvas_size_px(paper, options)
    if options.area == "canvas":
        return (0, 0, w_px, h_px)
    if options.area == "withBleed":
        r = rects.bleed
    elif options.area == "finish":
        r = rects.finish
    elif options.area == "innerFrame":
        r = rects.inner_frame
    else:
        return (0, 0, w_px, h_px)
    left = int(round(mm_to_px(r.x, dpi)))
    top = h_px - int(round(mm_to_px(r.y2, dpi)))
    right = int(round(mm_to_px(r.x2, dpi)))
    bottom = h_px - int(round(mm_to_px(r.y, dpi)))
    return (left, top, right, bottom)


def _resolve_page_index(work, page) -> int:
    page_id = str(getattr(page, "id", "") or "")
    for index, candidate in enumerate(work.pages):
        if candidate == page:
            return index
        if page_id and str(getattr(candidate, "id", "") or "") == page_id:
            return index
    return max(0, int(getattr(work, "active_page_index", 0)))


def _resolve_page_number(work, page) -> int:
    try:
        start = int(work.nombre.start_number)
    except Exception:  # noqa: BLE001
        start = 1
    return start + _resolve_page_index(work, page)


def _is_active_page(work, page) -> bool:
    try:
        active_index = int(getattr(work, "active_page_index", -1))
    except Exception:  # noqa: BLE001
        return False
    if active_index < 0:
        return False
    return _resolve_page_index(work, page) == active_index


def _resolve_page_offset_mm(work, page) -> tuple[float, float]:
    try:
        import bpy

        from ..utils.page_grid import page_grid_offset_mm, page_manual_offset_mm
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return (0.0, 0.0)
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return (0.0, 0.0)
    cols = max(1, int(getattr(scene, "bmanga_overview_cols", 4)))
    from ..utils.page_grid import resolve_gap_mm
    gap_x, gap_y = resolve_gap_mm(scene)
    index = _resolve_page_index(work, page)
    paper = work.paper
    ox, oy = page_grid_offset_mm(
        index,
        cols,
        gap_x,
        float(paper.canvas_width_mm),
        float(paper.canvas_height_mm),
        getattr(paper, "start_side", "right"),
        getattr(paper, "read_direction", "left"),
        work=work,
        gap_y_mm=gap_y,
    )
    add_x, add_y = page_manual_offset_mm(page)
    return ox + add_x, oy + add_y


def _is_left_half_page(work, page) -> bool:
    try:
        from ..utils.page_grid import is_left_half_page
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return False
    index = _resolve_page_index(work, page)
    return is_left_half_page(
        index,
        getattr(work.paper, "start_side", "right"),
        getattr(work.paper, "read_direction", "left"),
        work=work,
    )


def _empty_rgba(size: tuple[int, int]) -> Any:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _rgb255(vec: Sequence[float], alpha: float | None = None) -> tuple[int, int, int, int]:
    a = float(vec[3]) if len(vec) > 3 else 1.0
    if alpha is not None:
        a *= alpha
    r, g, b = color_space.linear_to_srgb_rgb((float(vec[0]), float(vec[1]), float(vec[2])))
    return (
        int(round(max(0.0, min(1.0, r)) * 255)),
        int(round(max(0.0, min(1.0, g)) * 255)),
        int(round(max(0.0, min(1.0, b)) * 255)),
        int(round(max(0.0, min(1.0, a)) * 255)),
    )


def _scale_alpha(img, opacity: int) -> Any:
    if opacity >= 255:
        return img
    out = img.copy()
    alpha = out.getchannel("A").point([int(round(i * (opacity / 255.0))) for i in range(256)])
    out.putalpha(alpha)
    return out


def _normalize_opacity(value: Any) -> int:
    try:
        f = float(value)
    except Exception:  # noqa: BLE001
        return 255
    if f <= 0.0:
        return 0
    if f <= 1.0:
        return int(round(f * 255))
    if f <= 100.0:
        return int(round((f / 100.0) * 255))
    return int(round(max(0.0, min(255.0, f))))


def _percent_opacity_to_alpha(value: Any, default: float = 100.0) -> int:
    return int(round(percentage.percent_to_factor(value, default) * 255))


def _blend_mode_name(value: Any) -> str:
    text = str(value or "normal").strip().lower()
    if "." in text:
        text = text.split(".")[-1]
    mapping = {
        "regular": "normal",
        "normal": "normal",
        "multiply": "multiply",
        "screen": "screen",
        "overlay": "overlay",
        "hardlight": "overlay",
        "softlight": "overlay",
        "add": "add",
        "linear_dodge": "add",
    }
    return mapping.get(text, "normal")


def _abspath_maybe(path_str: str) -> str:
    if not path_str:
        return ""
    try:
        import bpy

        return bpy.path.abspath(path_str)
    except Exception:  # noqa: BLE001
        return path_str


def _font_candidates() -> list[str]:
    # utils.text_style と挙動を揃えるための委譲 (プリファレンスの標準フォント
    # にも対応させるため、実体は text_style 側に一本化している)。
    return text_style.font_candidates()


def _resolve_font_path(preferred: str = "") -> str:
    return text_style.resolve_font_path(preferred)


def _load_font(font_path: str, size_px: int):
    if not font_path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(font_path, max(1, int(size_px)))
    except (OSError, IOError):
        return ImageFont.load_default()


def _text_bbox(text: str, font, stroke_width_px: int = 0) -> tuple[int, int]:
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
    try:
        box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_width_px)
        return (max(1, int(math.ceil(box[2] - box[0]))), max(1, int(math.ceil(box[3] - box[1]))))
    except Exception:  # noqa: BLE001
        try:
            return probe.textsize(text, font=font)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return (max(1, len(text) * max(1, font.size // 2)), max(1, getattr(font, "size", 14)))


def _canvas_for_bbox(
    bbox_mm: tuple[float, float, float, float],
    canvas_height_px: int,
    dpi: int,
    *,
    pad_mm: float = 0.0,
) -> _LayerCanvas | None:
    left_mm = float(bbox_mm[0]) - pad_mm
    bottom_mm = float(bbox_mm[1]) - pad_mm
    right_mm = float(bbox_mm[2]) + pad_mm
    top_mm = float(bbox_mm[3]) + pad_mm
    if right_mm <= left_mm or top_mm <= bottom_mm:
        return None
    left_px = int(math.floor(mm_to_px(left_mm, dpi)))
    right_px = int(math.ceil(mm_to_px(right_mm, dpi)))
    top_px = canvas_height_px - int(math.ceil(mm_to_px(top_mm, dpi)))
    bottom_px = canvas_height_px - int(math.floor(mm_to_px(bottom_mm, dpi)))
    width_px = max(1, right_px - left_px)
    height_px = max(1, bottom_px - top_px)
    return _LayerCanvas(_empty_rgba((width_px, height_px)), left_px, top_px, canvas_height_px, dpi)


def _points_bbox(pts: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _intersects_mm(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _coma_base_polygon_mm(entry) -> list[tuple[float, float]]:
    if entry.shape_type == "rect":
        return [
            (float(entry.rect_x_mm), float(entry.rect_y_mm)),
            (float(entry.rect_x_mm + entry.rect_width_mm), float(entry.rect_y_mm)),
            (float(entry.rect_x_mm + entry.rect_width_mm), float(entry.rect_y_mm + entry.rect_height_mm)),
            (float(entry.rect_x_mm), float(entry.rect_y_mm + entry.rect_height_mm)),
        ]
    if entry.shape_type == "polygon" and len(entry.vertices) >= 3:
        return [(float(v.x_mm), float(v.y_mm)) for v in entry.vertices]
    return []


def _coma_polygon_mm(entry) -> list[tuple[float, float]]:
    pts = _coma_base_polygon_mm(entry)
    if len(pts) < 3:
        return pts
    border = getattr(entry, "border", None)
    corner_type = str(getattr(border, "corner_type", "square") or "square")
    radius_mm = float(getattr(border, "corner_radius_mm", 0.0) or 0.0)
    if corner_type == "square" or radius_mm <= 0.0:
        return pts
    try:
        styled = border_geom.styled_closed_path_mm(pts, corner_type, radius_mm)
    except Exception:  # noqa: BLE001
        _logger.exception("export: styled coma polygon failed")
        return pts
    return styled if len(styled) >= 3 else pts


def _coma_group_name(entry) -> str:
    return str(getattr(entry, "coma_id", "") or getattr(entry, "id", "") or "coma")


def _coma_root_group_path(entry) -> tuple[str, ...]:
    return ("comas", _coma_group_name(entry))


def _coma_content_group_path(entry) -> tuple[str, ...]:
    return (*_coma_root_group_path(entry), "content")


def _group_path_for_parent(
    page,
    parent_kind: str,
    parent_key: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    return export_group_masks.group_path_for_parent(
        page,
        parent_kind,
        parent_key,
        fallback,
        coma_content_group_path=_coma_content_group_path,
    )


def _coma_preview_source(work_dir: Path, page_id: str, entry) -> Path | None:
    return coma_preview.coma_preview_source_path(work_dir, page_id, entry)


# 同一画像 (コマ画像等) の繰り返し読み込みを避ける小さなキャッシュ。
# キーは (パス, 更新時刻) なので、画像が更新されれば自動で読み直す。
_IMAGE_FILE_CACHE: dict[tuple[str, float], Any] = {}
_IMAGE_FILE_CACHE_MAX = 64


def _safe_load_image(path: Path) -> Any | None:
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        mtime = -1.0
    key = (str(path), mtime)
    cached = _IMAGE_FILE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with Image.open(str(path)) as opened:
            image = opened.convert("RGBA")
    except (OSError, ValueError) as exc:
        _logger.warning("failed to open image %s: %s", path, exc)
        return None
    if len(_IMAGE_FILE_CACHE) >= _IMAGE_FILE_CACHE_MAX:
        _IMAGE_FILE_CACHE.clear()
    _IMAGE_FILE_CACHE[key] = image
    return image


def _line_segments_for_style(
    p0: tuple[int, int],
    p1: tuple[int, int],
    dash: float,
    gap: float,
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    x0, y0 = p0
    x1, y1 = p1
    dx = float(x1 - x0)
    dy = float(y1 - y0)
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return []
    ux = dx / length
    uy = dy / length
    pos = 0.0
    out: list[tuple[tuple[int, int], tuple[int, int]]] = []
    while pos < length:
        end = min(length, pos + dash)
        sx = int(round(x0 + ux * pos))
        sy = int(round(y0 + uy * pos))
        ex = int(round(x0 + ux * end))
        ey = int(round(y0 + uy * end))
        out.append(((sx, sy), (ex, ey)))
        pos += dash + gap
    return out


def _draw_styled_segment(
    draw,
    p0: tuple[int, int],
    p1: tuple[int, int],
    color: tuple[int, int, int, int],
    width_px: int,
    style: str = "solid",
) -> None:
    width_px = max(1, int(width_px))
    if style == "solid":
        draw.line((p0, p1), fill=color, width=width_px)
        return
    if style == "dashed":
        dash = max(width_px * 4.0, 8.0)
        gap = max(width_px * 2.5, 5.0)
        for seg in _line_segments_for_style(p0, p1, dash, gap):
            draw.line(seg, fill=color, width=width_px)
        return
    if style == "dotted":
        x0, y0 = p0
        x1, y1 = p1
        dx = float(x1 - x0)
        dy = float(y1 - y0)
        length = math.hypot(dx, dy)
        if length <= 0.0:
            return
        ux = dx / length
        uy = dy / length
        spacing = max(width_px * 2.2, 6.0)
        radius = max(1.0, width_px * 0.55)
        pos = 0.0
        while pos <= length:
            cx = x0 + ux * pos
            cy = y0 + uy * pos
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=color,
                outline=color,
            )
            pos += spacing
        return
    if style == "double":
        dx = float(p1[0] - p0[0])
        dy = float(p1[1] - p0[1])
        length = math.hypot(dx, dy)
        if length <= 0.0:
            return
        nx = -dy / length
        ny = dx / length
        offset = max(2.0, width_px * 1.2)
        inner_width = max(1, int(round(width_px * 0.55)))
        for sign in (-0.5, 0.5):
            ox = nx * offset * sign
            oy = ny * offset * sign
            q0 = (int(round(p0[0] + ox)), int(round(p0[1] + oy)))
            q1 = (int(round(p1[0] + ox)), int(round(p1[1] + oy)))
            draw.line((q0, q1), fill=color, width=inner_width)
        return
    draw.line((p0, p1), fill=color, width=width_px)


def _draw_styled_loop(
    draw,
    pts: Sequence[tuple[int, int]],
    color: tuple[int, int, int, int],
    width_px: int,
    style: str = "solid",
) -> None:
    if len(pts) < 2:
        return
    for i in range(len(pts)):
        _draw_styled_segment(draw, pts[i], pts[(i + 1) % len(pts)], color, width_px, style)


def _spread_basic_frame_info_for_export(work, page, entry):
    try:
        return spread_merge_geometry.basic_frame_info(work, page, entry)
    except Exception:  # noqa: BLE001
        return "", None


def _draw_spread_basic_frame_border(
    draw,
    canvas,
    entry,
    side: str,
    combined_rect,
    color: tuple[int, int, int, int],
    width_px: int,
    style_name: str,
) -> ExportLayer | None:
    if side == "right" or combined_rect is None:
        return None
    poly_mm = [
        (float(combined_rect.x), float(combined_rect.y)),
        (float(combined_rect.x2), float(combined_rect.y)),
        (float(combined_rect.x2), float(combined_rect.y2)),
        (float(combined_rect.x), float(combined_rect.y2)),
    ]
    if len(poly_mm) < 4:
        return None
    poly_px = canvas.points_px(poly_mm)
    draw_style = "solid" if style_name == "brush" else style_name
    for i in range(len(poly_px)):
        _draw_styled_segment(
            draw,
            poly_px[i],
            poly_px[(i + 1) % len(poly_px)],
            color,
            width_px,
            draw_style,
        )
    return ExportLayer("border", canvas.image, canvas.left, canvas.top)


def _draw_coma_border_layer(entry, canvas_height_px: int, dpi: int, *, work=None, page=None) -> ExportLayer | None:
    spread_basic_side, spread_basic_rect = _spread_basic_frame_info_for_export(work, page, entry)
    if not spread_basic_side and export_soft_mask.brush_edge_enabled(entry):
        return None
    poly_mm = _coma_polygon_mm(entry)
    if spread_basic_side == "left" and spread_basic_rect is not None:
        poly_mm = [
            (float(spread_basic_rect.x), float(spread_basic_rect.y)),
            (float(spread_basic_rect.x2), float(spread_basic_rect.y)),
            (float(spread_basic_rect.x2), float(spread_basic_rect.y2)),
            (float(spread_basic_rect.x), float(spread_basic_rect.y2)),
        ]
    bbox = _points_bbox(poly_mm)
    if bbox is None:
        return None
    border = entry.border
    canvas = _canvas_for_bbox(bbox, canvas_height_px, dpi, pad_mm=max(float(border.width_mm) * 3.0, 1.0))
    if canvas is None:
        return None
    draw = ImageDraw.Draw(canvas.image)
    base_color = _rgb255(border.color)
    base_width = max(1, int(round(mm_to_px(float(border.width_mm), dpi))))
    style_name = getattr(border, "style", "solid")
    if spread_basic_side:
        return _draw_spread_basic_frame_border(
            draw,
            canvas,
            entry,
            spread_basic_side,
            spread_basic_rect,
            base_color,
            base_width,
            style_name,
        )
    if (
        style_name == "solid"
    ):
        path_mm = poly_mm
        loops = border_geom.stroke_loops_mm(path_mm, float(border.width_mm))
        if loops is not None:
            outer_px = canvas.points_px(loops[0])
            inner_px = canvas.points_px(loops[1])
            for i in range(len(outer_px)):
                j = (i + 1) % len(outer_px)
                draw.polygon(
                    [outer_px[i], outer_px[j], inner_px[j], inner_px[i]],
                    fill=base_color,
                )
            return ExportLayer("border", canvas.image, canvas.left, canvas.top)

    poly_px = canvas.points_px(poly_mm)
    for i in range(len(poly_px)):
        color = base_color
        width = base_width
        _draw_styled_segment(
            draw,
            poly_px[i],
            poly_px[(i + 1) % len(poly_px)],
            color,
            width,
            style_name,
        )
    return ExportLayer("border", canvas.image, canvas.left, canvas.top)


def _draw_coma_white_margin_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    poly_mm = _coma_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None:
        return None
    wm = entry.white_margin
    if getattr(entry.border, "style", "solid") == "brush":
        return None
    base_width = max(0.0, float(getattr(wm, "width_mm", 0.0) or 0.0))
    if not bool(getattr(wm, "enabled", False)) or base_width <= 0.0:
        return None

    border = getattr(entry, "border", None)
    border_half = 0.0
    if (
        border is not None
        and bool(getattr(border, "visible", True))
        and str(getattr(border, "style", "solid") or "solid") != "brush"
    ):
        border_half = max(0.0, float(getattr(border, "width_mm", 0.0) or 0.0)) * 0.5

    def offset_loop(offset_mm: float):
        offset = float(offset_mm)
        if abs(offset) <= 1.0e-6:
            return border_geom._dedupe_closed(poly_mm)
        loops = border_geom.stroke_loops_mm(poly_mm, abs(offset) * 2.0)
        if loops is None:
            return None
        return loops[0] if offset > 0.0 else loops[1]

    placement = str(getattr(wm, "placement", "outside") or "outside")
    if placement not in {"outside", "inside", "both"}:
        placement = "outside"
    edge_outer = offset_loop(border_half)
    edge_inner = offset_loop(-border_half)
    far_outer = offset_loop(border_half + base_width)
    far_inner = offset_loop(-(border_half + base_width))
    bands = []
    if placement in {"outside", "both"} and edge_outer and far_outer:
        bands.append((edge_outer, far_outer))
    if placement in {"inside", "both"} and far_inner and edge_inner:
        bands.append((far_inner, edge_inner))
    if not bands:
        return None
    ring_bbox = _points_bbox([point for band in bands for loop in band for point in loop])
    if ring_bbox is None:
        return None
    canvas = _canvas_for_bbox(ring_bbox, canvas_height_px, dpi)
    if canvas is None:
        return None
    color = _rgb255(wm.color)
    draw = ImageDraw.Draw(canvas.image)
    for inner_mm, outer_mm in bands:
        inner_px = canvas.points_px(inner_mm)
        outer_px = canvas.points_px(outer_mm)
        for i in range(len(outer_px)):
            j = (i + 1) % len(outer_px)
            draw.polygon([inner_px[i], inner_px[j], outer_px[j], outer_px[i]], fill=color)
    return ExportLayer("white_margin", canvas.image, canvas.left, canvas.top)


def _draw_coma_background_layer(
    entry,
    canvas_height_px: int,
    dpi: int,
    *,
    include_brush_edge: bool = True,
) -> ExportLayer | None:
    if not bool(getattr(entry, "paper_visible", True)):
        return None
    color_src = getattr(entry, "background_color", (1.0, 1.0, 1.0, 0.0))
    color = _rgb255(color_src)
    if color[3] <= 0:
        return None
    poly_mm = _coma_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None or len(poly_mm) < 3:
        return None
    mask_bbox = bbox
    if include_brush_edge and export_soft_mask.brush_edge_enabled(entry):
        mask_bbox = export_soft_mask.expand_bbox(bbox, export_soft_mask.brush_soft_width_mm(entry))
    canvas = _canvas_for_bbox(mask_bbox, canvas_height_px, dpi)
    if canvas is None:
        return None
    if include_brush_edge and export_soft_mask.brush_edge_enabled(entry):
        mask = export_soft_mask.coma_soft_edge_mask(
            Image,
            ImageChops,
            ImageDraw,
            ImageFilter,
            entry,
            poly_mm,
            mask_bbox,
            canvas.image.size,
            dpi,
        )
        image = export_soft_mask.apply_mask_alpha(Image, Image.new("RGBA", canvas.image.size, color), mask, color[3])
        return ExportLayer("background", image, canvas.left, canvas.top)
    else:
        ImageDraw.Draw(canvas.image).polygon(canvas.points_px(poly_mm), fill=color)
    return ExportLayer("background", canvas.image, canvas.left, canvas.top)


def _render_coma_preview_layer(
    work,
    page,
    entry,
    canvas_size: tuple[int, int],
    dpi: int,
    *,
    include_brush_edge: bool = True,
) -> ExportLayer | None:
    if not bool(getattr(entry, "paper_visible", True)):
        return None
    poly_mm = _coma_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None or not work.work_dir:
        return None
    source_path = _coma_preview_source(Path(work.work_dir), page.id, entry)
    if source_path is None:
        return None
    source = _safe_load_image(source_path)
    if source is None:
        return None
    mask_bbox = bbox
    if include_brush_edge and export_soft_mask.brush_edge_enabled(entry):
        mask_bbox = export_soft_mask.expand_bbox(bbox, export_soft_mask.brush_soft_width_mm(entry))
    canvas = _canvas_for_bbox(mask_bbox, canvas_size[1], dpi)
    if canvas is None:
        return None
    if include_brush_edge and export_soft_mask.brush_edge_enabled(entry):
        left, top, right, bottom = export_soft_mask.local_box_px(bbox, mask_bbox, canvas.image.size)
        target_size = (max(1, right - left), max(1, bottom - top))
        source = source.resize(target_size, Image.LANCZOS)
        content = _empty_rgba(canvas.image.size)
        content.alpha_composite(source, dest=(left, top))
        source_alpha = Image.new("L", canvas.image.size, 0)
        source_alpha.paste(source.getchannel("A"), (left, top))
        mask = export_soft_mask.coma_soft_edge_mask(
            Image,
            ImageChops,
            ImageDraw,
            ImageFilter,
            entry,
            poly_mm,
            mask_bbox,
            canvas.image.size,
            dpi,
        )
        try:
            mask = ImageChops.multiply(mask, source_alpha)
        except Exception:  # noqa: BLE001
            pass
        canvas.image.paste(content, (0, 0), mask)
    elif len(poly_mm) >= 3:
        source = source.resize(canvas.image.size, Image.LANCZOS)
        mask = export_soft_mask.coma_shape_mask(Image, ImageDraw, poly_mm, bbox, canvas.image.size)
        try:
            mask = ImageChops.multiply(mask, source.getchannel("A"))
        except Exception:  # noqa: BLE001
            pass
        canvas.image.paste(source, (0, 0), mask)
    return ExportLayer("render", canvas.image, canvas.left, canvas.top)


def _render_coma_mask(work, page, entry, canvas_height_px: int, dpi: int) -> ExportMask | None:
    poly_mm = coma_content_mask.coma_polygon_mm(entry)
    bbox = coma_content_mask.mask_bbox_mm(entry)
    if bbox is None or len(poly_mm) < 3:
        return None
    canvas = _canvas_for_bbox(bbox, canvas_height_px, dpi)
    if canvas is None:
        return None
    mask = export_soft_mask.coma_soft_edge_mask(
        Image,
        ImageChops,
        ImageDraw,
        ImageFilter,
        entry,
        poly_mm,
        bbox,
        canvas.image.size,
        dpi,
    )
    return ExportMask(
        mask,
        canvas.left,
        canvas.top,
        coma_content_mask.mask_image_name(work, page, entry, dpi),
    )


def _apply_image_adjustments(img, entry) -> Any:
    out = img.convert("RGBA")
    if getattr(entry, "flip_x", False):
        out = out.transpose(Image.FLIP_LEFT_RIGHT)
    if getattr(entry, "flip_y", False):
        out = out.transpose(Image.FLIP_TOP_BOTTOM)
    brightness = float(getattr(entry, "brightness", 0.0))
    if abs(brightness) > 1e-6:
        out = ImageEnhance.Brightness(out).enhance(max(0.0, 1.0 + brightness))
    contrast = float(getattr(entry, "contrast", 0.0))
    if abs(contrast) > 1e-6:
        out = ImageEnhance.Contrast(out).enhance(max(0.0, 1.0 + contrast))
    if getattr(entry, "binarize_enabled", False):
        threshold = int(round(max(0.0, min(1.0, float(entry.binarize_threshold))) * 255))
        alpha = out.getchannel("A")
        mono = out.convert("L").point(lambda px: 255 if px >= threshold else 0)
        out = Image.merge("RGBA", (mono, mono, mono, alpha))
    tint = getattr(entry, "tint_color", None)
    if tint is not None:
        tinted = []
        for band, factor in zip(out.split(), tint):
            tinted.append(band.point(lambda px, k=float(factor): int(round(px * max(0.0, min(1.0, k))))))
        if len(tinted) == 4:
            out = Image.merge("RGBA", tuple(tinted))
    opacity = _percent_opacity_to_alpha(getattr(entry, "opacity", 100.0), 100.0)
    if opacity < 255:
        out = _scale_alpha(out, opacity)
    rotation = float(getattr(entry, "rotation_deg", 0.0))
    if abs(rotation) > 1e-6:
        out = out.rotate(-rotation, expand=True, resample=Image.BICUBIC)
    return out


def _render_image_layer(
    entry,
    canvas_size: tuple[int, int],
    dpi: int,
    *,
    group_path: tuple[str, ...] = ("image_layers",),
) -> ExportLayer | None:
    path = Path(_abspath_maybe(getattr(entry, "filepath", "")))
    if not path.is_file():
        return None
    source = _safe_load_image(path)
    if source is None:
        return None
    width_px = max(1, int(round(mm_to_px(float(entry.width_mm), dpi))))
    height_px = max(1, int(round(mm_to_px(float(entry.height_mm), dpi))))
    source = source.resize((width_px, height_px), Image.LANCZOS)
    source = _apply_image_adjustments(source, entry)
    center_x = int(round(mm_to_px(float(entry.x_mm + entry.width_mm * 0.5), dpi)))
    center_y = canvas_size[1] - int(round(mm_to_px(float(entry.y_mm + entry.height_mm * 0.5), dpi)))
    left = center_x - source.width // 2
    top = center_y - source.height // 2
    return ExportLayer(
        str(getattr(entry, "title", "") or path.stem),
        source,
        left,
        top,
        group_path=group_path,
        visible=bool(getattr(entry, "visible", True)),
        opacity=255,
        blend_mode=_blend_mode_name(getattr(entry, "blend_mode", "normal")),
        stack_parent_key=str(getattr(entry, "folder_key", "") or getattr(entry, "parent_key", "") or ""),
    )


def _render_fill_layer(
    entry,
    canvas_size: tuple[int, int],
    *,
    group_path: tuple[str, ...] = ("fill_layers",),
) -> ExportLayer | None:
    fill_type = str(getattr(entry, "fill_type", "solid") or "solid")
    opacity_pct = float(getattr(entry, "opacity", 100.0) or 100.0)
    alpha_byte = max(0, min(255, int(round(opacity_pct * 2.55))))

    try:
        from ..utils import color_space
    except Exception:  # noqa: BLE001
        color_space = None

    def _to_srgb_byte(linear_rgba):
        r, g, b = float(linear_rgba[0]), float(linear_rgba[1]), float(linear_rgba[2])
        a = float(linear_rgba[3]) if len(linear_rgba) > 3 else 1.0
        if color_space is not None:
            r, g, b = color_space.linear_to_srgb_rgb((r, g, b))
        return (
            max(0, min(255, int(round(r * 255)))),
            max(0, min(255, int(round(g * 255)))),
            max(0, min(255, int(round(b * 255)))),
            max(0, min(255, int(round(a * 255)))),
        )

    w, h = canvas_size
    if fill_type == "solid":
        c = _to_srgb_byte(tuple(entry.color))
        img = Image.new("RGBA", (w, h), c)
    else:
        grad_type = str(getattr(entry, "gradient_type", "linear") or "linear")
        c1 = _to_srgb_byte(tuple(entry.color))
        c2 = _to_srgb_byte(tuple(entry.color2))
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        import math

        if grad_type == "radial":
            cx, cy = w / 2.0, h / 2.0
            max_r = math.hypot(cx, cy)
            for y in range(h):
                for x in range(w):
                    d = math.hypot(x - cx, y - cy) / max_r if max_r > 0 else 0.0
                    t = max(0.0, min(1.0, d))
                    r = int(c1[0] + (c2[0] - c1[0]) * t)
                    g = int(c1[1] + (c2[1] - c1[1]) * t)
                    b = int(c1[2] + (c2[2] - c1[2]) * t)
                    a = int(c1[3] + (c2[3] - c1[3]) * t)
                    img.putpixel((x, y), (r, g, b, a))
        else:
            angle = float(getattr(entry, "gradient_angle", 0.0) or 0.0)
            dx = math.cos(angle)
            dy = -math.sin(angle)
            for y in range(h):
                for x in range(w):
                    nx = (x / w - 0.5) * dx + (y / h - 0.5) * dy + 0.5
                    t = max(0.0, min(1.0, nx))
                    r = int(c1[0] + (c2[0] - c1[0]) * t)
                    g = int(c1[1] + (c2[1] - c1[1]) * t)
                    b = int(c1[2] + (c2[2] - c1[2]) * t)
                    a = int(c1[3] + (c2[3] - c1[3]) * t)
                    img.putpixel((x, y), (r, g, b, a))

    return ExportLayer(
        str(getattr(entry, "title", "") or entry.id),
        img,
        0,
        0,
        group_path=group_path,
        visible=bool(getattr(entry, "visible", True)),
        opacity=alpha_byte,
        blend_mode="normal",
        stack_parent_key=str(getattr(entry, "folder_key", "") or getattr(entry, "parent_key", "") or ""),
    )


def _render_balloon_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    from . import export_balloon

    return export_balloon.render_balloon_layer(entry, canvas_height_px, dpi)


def _render_text_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    body = (getattr(entry, "body", "") or "").strip()
    if not body:
        return None
    from ..typography import ruby as text_ruby

    pad_mm = text_ruby.render_pad_mm_for_entry(entry, minimum=1.5)
    canvas = _canvas_for_bbox(
        (
            float(entry.x_mm),
            float(entry.y_mm),
            float(entry.x_mm + entry.width_mm),
            float(entry.y_mm + entry.height_mm),
        ),
        canvas_height_px,
        dpi,
        pad_mm=pad_mm,
    )
    if canvas is None:
        return None
    from ..typography import export_renderer, layout as text_layout
    from ..utils import text_layout_bounds

    font_path = _resolve_font_path(str(getattr(entry, "font", "")))
    # ビューポート実体 (utils/text_real_object.py) と同じ内側余白を使う。
    # 書き出し/ページプレビューだけ旧来の枠全体で組版すると、文字の位置が
    # 実体表示に対して斜めへずれる。
    inner = text_layout_bounds.text_inner_rect(
        Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    )
    result = text_layout.typeset(
        entry,
        pad_mm + inner.x,
        pad_mm + inner.y,
        inner.width,
        inner.height,
    )
    ruby_placements = text_ruby.compute_for_entry(result.placements, entry)
    stroke_width_px = 0
    stroke_color = (255, 255, 255, 255)
    if getattr(entry, "stroke_enabled", False):
        stroke_width_px = max(1, int(round(mm_to_px(float(getattr(entry, "stroke_width_mm", 0.2)), dpi))))
        stroke_color = _rgb255(getattr(entry, "stroke_color", (1.0, 1.0, 1.0, 1.0)))
    export_renderer.render_to_image(
        result,
        canvas.image,
        font_path=font_path,
        font_path_for_index=lambda index: _resolve_font_path(text_style.font_for_index(entry, index)),
        color_for_index=lambda index: _rgb255(text_style.color_for_index(entry, index)),
        bold_for_index=lambda index: text_style.bold_for_index(entry, index),
        italic_for_index=lambda index: text_style.italic_for_index(entry, index),
        px_per_mm=mm_to_px(1.0, dpi),
        color=_rgb255(getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))),
        stroke_width_px=stroke_width_px,
        stroke_color=stroke_color,
        ruby_placements=ruby_placements,
        writing_mode=str(getattr(entry, "writing_mode", "horizontal") or "horizontal"),
    )
    image, left_px, top_px = canvas.image, canvas.left, canvas.top
    rotation_deg = float(getattr(entry, "rotation_deg", 0.0) or 0.0)
    if abs(rotation_deg) > 1e-6:
        # canvas は矩形中心を基準に pad_mm を等分に取っているため、画像自身の
        # 中心 = entry 矩形の中心。image レイヤー (_apply_image_adjustments) と
        # 同じ考え方で、矩形中心を軸に回転して中心位置を維持する。
        image = image.rotate(-rotation_deg, expand=True, resample=Image.BICUBIC)
        center_x_mm = float(entry.x_mm) + float(entry.width_mm) * 0.5
        center_y_mm = float(entry.y_mm) + float(entry.height_mm) * 0.5
        center_x_px = int(round(mm_to_px(center_x_mm, dpi)))
        center_y_px = canvas_height_px - int(round(mm_to_px(center_y_mm, dpi)))
        left_px = center_x_px - image.width // 2
        top_px = center_y_px - image.height // 2
    return ExportLayer(
        str(getattr(entry, "id", "") or "text"),
        image,
        left_px,
        top_px,
        group_path=("texts",),
    )


def _render_simple_text_layer(
    text: str,
    *,
    left_mm: float,
    baseline_top_mm: float,
    font_path: str,
    font_size_mm: float,
    color: tuple[int, int, int, int],
    dpi: int,
    canvas_height_px: int,
    group_path: tuple[str, ...],
    name: str,
    anchor_x: str = "left",
    anchor_y: str = "top",
    stroke_width_mm: float = 0.0,
    stroke_color: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> ExportLayer | None:
    if not text:
        return None
    font = _load_font(font_path, max(1, int(round(mm_to_px(font_size_mm, dpi)))))
    stroke_width_px = max(0, int(round(mm_to_px(stroke_width_mm, dpi))))
    text_w, text_h = _text_bbox(text, font, stroke_width_px=stroke_width_px)
    pad_px = max(2, stroke_width_px + 2)
    image = _empty_rgba((text_w + pad_px * 2, text_h + pad_px * 2))
    draw = ImageDraw.Draw(image)
    draw.text((pad_px, pad_px), text, font=font, fill=color, stroke_width=stroke_width_px, stroke_fill=stroke_color)
    x_px = int(round(mm_to_px(left_mm, dpi)))
    y_px = canvas_height_px - int(round(mm_to_px(baseline_top_mm, dpi)))
    if anchor_x == "center":
        x_px -= image.width // 2
    elif anchor_x == "right":
        x_px -= image.width
    if anchor_y == "middle":
        y_px -= image.height // 2
    elif anchor_y == "bottom":
        y_px -= image.height
    return ExportLayer(name, image, x_px, y_px, group_path=group_path)


def _work_info_layers(work, page, canvas_size: tuple[int, int], dpi: int) -> list[ExportLayer]:
    info = getattr(work, "work_info", None)
    if info is None:
        return []
    if not bool(getattr(info, "display_visible", True)):
        return []
    layers: list[ExportLayer] = []
    rects = overlay_shared.compute_paper_rects(work.paper, is_left_half=_is_left_half_page(work, page))
    anchor_rect = rects.bleed
    page_text = _work_info_page_label(work, page, info)
    items = [
        (info.display_work_name, info.work_name, "work_name"),
        (info.display_episode, f"第{info.episode_number}話" if info.episode_number else "", "episode"),
        (info.display_subtitle, info.subtitle, "subtitle"),
        (info.display_author, info.author, "author"),
        (info.display_page_number, page_text, "page_number"),
    ]
    pad_mm = 2.0
    work_info_font = str(getattr(info, "font", "") or "")
    font_path = _resolve_font_path(work_info_font)
    for item, text, name in items:
        if item is None or not getattr(item, "enabled", False) or not text:
            continue
        pos = getattr(item, "position", "bottom-left")
        if pos.endswith("left"):
            x_mm = anchor_rect.x
            anchor_x = "left"
        elif pos.endswith("right"):
            x_mm = anchor_rect.x2
            anchor_x = "right"
        else:
            x_mm = anchor_rect.x + anchor_rect.width * 0.5
            anchor_x = "center"
        if pos.startswith("top"):
            y_mm = anchor_rect.y2 + pad_mm
            anchor_y = "top"
        else:
            y_mm = anchor_rect.y - pad_mm
            anchor_y = "bottom"
        font_size_mm = (
            q_to_mm(float(getattr(item, "font_size_q", 20.0)))
            * _WORK_INFO_Q_VISIBLE_HEIGHT_COMPENSATION
        )
        layer = _render_simple_text_layer(
            text,
            left_mm=x_mm,
            baseline_top_mm=y_mm,
            font_path=font_path,
            font_size_mm=font_size_mm,
            color=_rgb255(item.color),
            dpi=dpi,
            canvas_height_px=canvas_size[1],
            group_path=("work_info",),
            name=name,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
        )
        if layer is not None:
            layers.append(layer)
    return layers


def _work_info_page_label(work, page, info) -> str:
    paper = getattr(work, "paper", None)
    try:
        if paper is not None and page is not None:
            from ..core.paper import format_page_entry_display_label

            return format_page_entry_display_label(paper, page)
        page_number = int(getattr(info, "page_number_start", 1) or 1) + _resolve_page_index(work, page)
        if paper is not None:
            from ..core.paper import format_page_display_label

            return format_page_display_label(paper, page_number)
        return f"ページ{page_number:04d}"
    except Exception:  # noqa: BLE001
        return ""


def _nombre_layer(work, page, canvas_size: tuple[int, int], dpi: int) -> ExportLayer | None:
    nombre = getattr(work, "nombre", None)
    if nombre is None or not getattr(nombre, "enabled", False):
        return None
    page_number = _resolve_page_number(work, page)
    text = overlay_shared.format_nombre_text(nombre, page_number)
    x_mm, y_mm = overlay_shared.nombre_anchor(work.paper, nombre, is_left_half=_is_left_half_page(work, page))
    nombre_font = str(getattr(nombre, "font", "") or "")
    if not nombre_font:
        _wi = getattr(work, "work_info", None)
        if _wi is not None:
            nombre_font = str(getattr(_wi, "font", "") or "")
    font_path = _resolve_font_path(nombre_font)
    anchor_x = "center"
    pos = getattr(nombre, "position", "bottom-center")
    if pos.endswith("left"):
        anchor_x = "left"
    elif pos.endswith("right"):
        anchor_x = "right"
    anchor_y = "bottom" if pos.startswith("bottom") else "top"
    stroke_width_mm = float(getattr(nombre, "border_width_mm", 0.0)) if getattr(nombre, "border_enabled", False) else 0.0
    stroke_color = _rgb255(getattr(nombre, "border_color", (1.0, 1.0, 1.0, 1.0)))
    return _render_simple_text_layer(
        text,
        left_mm=float(x_mm),
        baseline_top_mm=float(y_mm),
        font_path=font_path,
        font_size_mm=float(getattr(nombre, "font_size_pt", 9.0)) * 25.4 / 72.0,
        color=_rgb255(nombre.color),
        dpi=dpi,
        canvas_height_px=canvas_size[1],
        group_path=("nombre",),
        name="nombre",
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        stroke_width_mm=stroke_width_mm,
        stroke_color=stroke_color,
    )


def _trim_mark_segments(rects) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    fr = rects.finish
    br = rects.bleed
    arm = 10.0
    gap = 5.0
    size = 10.0
    half = size * 0.5
    cx_mid = (fr.x + fr.x2) * 0.5
    cy_mid = (fr.y + fr.y2) * 0.5
    segs = [
        ((br.x - arm, fr.y), (br.x, fr.y)),
        ((fr.x, br.y - arm), (fr.x, br.y)),
        ((br.x - arm, br.y), (br.x, br.y)),
        ((br.x, br.y - arm), (br.x, br.y)),
        ((br.x2, fr.y), (br.x2 + arm, fr.y)),
        ((fr.x2, br.y - arm), (fr.x2, br.y)),
        ((br.x2, br.y), (br.x2 + arm, br.y)),
        ((br.x2, br.y - arm), (br.x2, br.y)),
        ((br.x - arm, fr.y2), (br.x, fr.y2)),
        ((fr.x, br.y2), (fr.x, br.y2 + arm)),
        ((br.x - arm, br.y2), (br.x, br.y2)),
        ((br.x, br.y2), (br.x, br.y2 + arm)),
        ((br.x2, fr.y2), (br.x2 + arm, fr.y2)),
        ((fr.x2, br.y2), (fr.x2, br.y2 + arm)),
        ((br.x2, br.y2), (br.x2 + arm, br.y2)),
        ((br.x2, br.y2), (br.x2, br.y2 + arm)),
    ]
    cy_top = br.y2 + gap + half
    cy_bottom = br.y - gap - half
    cx_left = br.x - gap - half
    cx_right = br.x2 + gap + half
    segs.extend(
        [
            ((cx_mid, cy_top - half), (cx_mid, cy_top + half)),
            ((cx_mid - half, cy_top), (cx_mid + half, cy_top)),
            ((cx_mid, cy_bottom - half), (cx_mid, cy_bottom + half)),
            ((cx_mid - half, cy_bottom), (cx_mid + half, cy_bottom)),
            ((cx_left, cy_mid - half), (cx_left, cy_mid + half)),
            ((cx_left - half, cy_mid), (cx_left + half, cy_mid)),
            ((cx_right, cy_mid - half), (cx_right, cy_mid + half)),
            ((cx_right - half, cy_mid), (cx_right + half, cy_mid)),
        ]
    )
    return segs


def _tombo_layer(work, page, canvas_size: tuple[int, int], dpi: int) -> ExportLayer | None:
    rects = overlay_shared.compute_paper_rects(work.paper, is_left_half=_is_left_half_page(work, page))
    segs = _trim_mark_segments(rects)
    xs = [p[0] for seg in segs for p in seg]
    ys = [p[1] for seg in segs for p in seg]
    canvas = _canvas_for_bbox((min(xs), min(ys), max(xs), max(ys)), canvas_size[1], dpi, pad_mm=1.0)
    if canvas is None:
        return None
    draw = ImageDraw.Draw(canvas.image)
    color = (13, 13, 13, 242)
    width = max(1, int(round(mm_to_px(0.40, dpi))))
    for p0, p1 in segs:
        draw.line((canvas.point_px(*p0), canvas.point_px(*p1)), fill=color, width=width)
    return ExportLayer("tombo", canvas.image, canvas.left, canvas.top, group_path=("tombo",))


def _overlay_fill_color255(
    work,
    color_attr: str,
    opacity_attr: str,
    default_opacity: float,
) -> tuple[int, int, int, int]:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None:
        return (0, 0, 0, 0)
    color = getattr(overlay, color_attr, (0.0, 0.0, 0.0))
    try:
        rgb = color_space.linear_to_srgb_rgb(tuple(float(color[i]) for i in range(3)))
    except Exception:  # noqa: BLE001
        rgb = (0.0, 0.0, 0.0)
    alpha = int(round(
        percentage.percent_to_factor(
            getattr(overlay, opacity_attr, default_opacity),
            default_opacity,
        ) * 255
    ))
    return (
        max(0, min(255, int(round(rgb[0] * 255.0)))),
        max(0, min(255, int(round(rgb[1] * 255.0)))),
        max(0, min(255, int(round(rgb[2] * 255.0)))),
        max(0, min(255, alpha)),
    )


def _draw_outside_rect_px(
    draw,
    *,
    page_left: int,
    page_width: int,
    page_height: int,
    rect: Rect,
    paper_height_mm: float,
    dpi: int,
    fill: tuple[int, int, int, int],
) -> bool:
    page_right = page_left + page_width
    left = page_left + int(round(mm_to_px(rect.x, dpi)))
    right = page_left + int(round(mm_to_px(rect.x2, dpi)))
    top = int(round(mm_to_px(paper_height_mm - rect.y2, dpi)))
    bottom = int(round(mm_to_px(paper_height_mm - rect.y, dpi)))
    left = max(page_left, min(page_right, left))
    right = max(page_left, min(page_right, right))
    top = max(0, min(page_height, top))
    bottom = max(0, min(page_height, bottom))
    changed = False
    if top > 0:
        draw.rectangle([page_left, 0, page_right - 1, top - 1], fill=fill)
        changed = True
    if bottom < page_height:
        draw.rectangle([page_left, bottom, page_right - 1, page_height - 1], fill=fill)
        changed = True
    if left > page_left and bottom > top:
        draw.rectangle([page_left, top, left - 1, bottom - 1], fill=fill)
        changed = True
    if right < page_right and bottom > top:
        draw.rectangle([right, top, page_right - 1, bottom - 1], fill=fill)
        changed = True
    return changed


def _draw_rect_ring_px(
    draw,
    *,
    page_left: int,
    page_width: int,
    page_height: int,
    outer: Rect,
    inner: Rect,
    paper_height_mm: float,
    dpi: int,
    fill: tuple[int, int, int, int],
) -> bool:
    page_right = page_left + page_width

    def bounds_px(rect: Rect) -> tuple[int, int, int, int]:
        left = page_left + int(round(mm_to_px(rect.x, dpi)))
        right = page_left + int(round(mm_to_px(rect.x2, dpi)))
        top = int(round(mm_to_px(paper_height_mm - rect.y2, dpi)))
        bottom = int(round(mm_to_px(paper_height_mm - rect.y, dpi)))
        return (
            max(page_left, min(page_right, left)),
            max(0, min(page_height, top)),
            max(page_left, min(page_right, right)),
            max(0, min(page_height, bottom)),
        )

    outer_left, outer_top, outer_right, outer_bottom = bounds_px(outer)
    inner_left, inner_top, inner_right, inner_bottom = bounds_px(inner)
    inner_left = max(outer_left, min(outer_right, inner_left))
    inner_right = max(outer_left, min(outer_right, inner_right))
    inner_top = max(outer_top, min(outer_bottom, inner_top))
    inner_bottom = max(outer_top, min(outer_bottom, inner_bottom))
    if outer_right <= outer_left or outer_bottom <= outer_top:
        return False

    changed = False
    if inner_top > outer_top:
        draw.rectangle([outer_left, outer_top, outer_right - 1, inner_top - 1], fill=fill)
        changed = True
    if inner_bottom < outer_bottom:
        draw.rectangle([outer_left, inner_bottom, outer_right - 1, outer_bottom - 1], fill=fill)
        changed = True
    if inner_left > outer_left and inner_bottom > inner_top:
        draw.rectangle([outer_left, inner_top, inner_left - 1, inner_bottom - 1], fill=fill)
        changed = True
    if inner_right < outer_right and inner_bottom > inner_top:
        draw.rectangle([inner_right, inner_top, outer_right - 1, inner_bottom - 1], fill=fill)
        changed = True
    return changed


def _overlay_fill_visible(
    work,
    *,
    enabled_attr: str,
    opacity_attr: str,
    default_opacity: float,
) -> bool:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None or not bool(getattr(overlay, enabled_attr, True)):
        return False
    return _percent_opacity_to_alpha(getattr(overlay, opacity_attr, default_opacity), default_opacity) > 0


def _page_overlay_fill_layer(
    work,
    page,
    options: ExportOptions,
    canvas_size: tuple[int, int],
    *,
    name: str,
    enabled_attr: str,
    color_attr: str,
    opacity_attr: str,
    default_opacity: float,
    rect_attr: str,
    outer_rect_attr: str = "canvas",
) -> ExportLayer | None:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None or not bool(getattr(overlay, enabled_attr, True)):
        return None
    fill = _overlay_fill_color255(work, color_attr, opacity_attr, default_opacity)
    if fill[3] <= 0:
        return None
    image = _empty_rgba(canvas_size)
    draw = ImageDraw.Draw(image)
    paper = work.paper
    page_width, page_height = _canvas_size_px(paper, options)
    if bool(getattr(page, "spread", False)):
        try:
            combined_rects = spread_merge_geometry.combined_spread_rects(paper, page)
            changed = _draw_rect_ring_px(
                draw,
                page_left=0,
                page_width=canvas_size[0],
                page_height=page_height,
                outer=getattr(combined_rects, outer_rect_attr),
                inner=getattr(combined_rects, rect_attr),
                paper_height_mm=float(paper.canvas_height_mm),
                dpi=_dpi(paper, options),
                fill=fill,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("spread overlay fill layer failed")
            changed = False
    else:
        is_left_half = _is_left_half_page(work, page)
        rects = overlay_shared.compute_paper_rects(paper, is_left_half=is_left_half)
        changed = _draw_rect_ring_px(
            draw,
            page_left=0,
            page_width=page_width,
            page_height=page_height,
            outer=getattr(rects, outer_rect_attr),
            inner=getattr(rects, rect_attr),
            paper_height_mm=float(paper.canvas_height_mm),
            dpi=_dpi(paper, options),
            fill=fill,
        )
    if not changed:
        return None
    return ExportLayer(name, image, 0, 0)


def _page_overlay_fill_layers(
    work,
    page,
    options: ExportOptions,
    canvas_size: tuple[int, int],
) -> list[ExportLayer]:
    if (
        str(getattr(options, "format", "") or "").lower() != "psd"
        and not bool(getattr(options, "include_page_overlay_fills", False))
    ):
        return []
    layers: list[ExportLayer] = []
    safe_outer_rect_attr = (
        "bleed"
        if _overlay_fill_visible(
            work,
            enabled_attr="bleed_outer_enabled",
            opacity_attr="bleed_outer_opacity",
            default_opacity=100.0,
        )
        else "canvas"
    )
    safe = _page_overlay_fill_layer(
        work,
        page,
        options,
        canvas_size,
        name="セーフライン外の塗り",
        enabled_attr="enabled",
        color_attr="color",
        opacity_attr="opacity",
        default_opacity=30.0,
        rect_attr="safe",
        outer_rect_attr=safe_outer_rect_attr,
    )
    if safe is not None:
        layers.append(safe)
    bleed = _page_overlay_fill_layer(
        work,
        page,
        options,
        canvas_size,
        name="裁ち落とし枠外の塗り",
        enabled_attr="bleed_outer_enabled",
        color_attr="bleed_outer_color",
        opacity_attr="bleed_outer_opacity",
        default_opacity=100.0,
        rect_attr="bleed",
    )
    if bleed is not None:
        layers.append(bleed)
    return layers


def _gp_material_info(obj, stroke) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], bool]:
    color = (0, 0, 0, 255)
    fill = (0, 0, 0, 255)
    show_fill = False
    idx = int(getattr(stroke, "material_index", 0))
    mats = getattr(getattr(obj, "data", None), "materials", None)
    if mats is None or idx < 0 or idx >= len(mats):
        return (color, fill, show_fill)
    mat = mats[idx]
    style = getattr(mat, "grease_pencil", None)
    if style is None:
        return (color, fill, show_fill)
    if hasattr(style, "color"):
        color = _rgb255(style.color)
    if hasattr(style, "fill_color"):
        fill = _rgb255(style.fill_color)
    show_fill = bool(getattr(style, "show_fill", False))
    return (color, fill, show_fill)


def _gp_layer_frame(layer):
    frames = list(getattr(layer, "frames", []))
    if not frames:
        return None
    try:
        import bpy

        current = int(bpy.context.scene.frame_current)
    except Exception:  # noqa: BLE001
        current = None
    if current is None:
        return frames[0]
    exact = [frame for frame in frames if int(getattr(frame, "frame_number", -1)) == current]
    if exact:
        return exact[0]
    earlier = [frame for frame in frames if int(getattr(frame, "frame_number", -1)) <= current]
    if earlier:
        earlier.sort(key=lambda frame: int(getattr(frame, "frame_number", 0)), reverse=True)
        return earlier[0]
    return frames[0]


def _gp_stroke_points_mm(obj, stroke, page_offset_mm: tuple[float, float]) -> tuple[list[tuple[float, float]], float, list[float]]:
    obj_loc = getattr(obj, "location", None)
    obj_x = float(getattr(obj_loc, "x", 0.0))
    obj_y = float(getattr(obj_loc, "y", 0.0))
    pts: list[tuple[float, float]] = []
    radii: list[float] = []
    opacities: list[float] = []
    for point in getattr(stroke, "points", []):
        pos = getattr(point, "position", None)
        if pos is None:
            continue
        x_mm = m_to_mm(obj_x + float(pos[0])) - page_offset_mm[0]
        y_mm = m_to_mm(obj_y + float(pos[1])) - page_offset_mm[1]
        pts.append((x_mm, y_mm))
        try:
            radii.append(m_to_mm(float(getattr(point, "radius", 0.0002))) * 2.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            opacities.append(max(0.0, min(1.0, float(getattr(point, "opacity", 1.0)))))
        except Exception:  # noqa: BLE001
            opacities.append(1.0)
    width_mm = max(radii) if radii else 0.4
    return (pts, max(0.05, width_mm), opacities)


def _rgba_with_alpha_scale(color: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    return (
        color[0],
        color[1],
        color[2],
        max(0, min(255, int(round(color[3] * max(0.0, min(1.0, float(scale))))))),
    )


def _draw_round_cap(draw, point, color, width_px: int) -> None:
    radius = max(0.5, float(width_px) * 0.5)
    x, y = point
    draw.ellipse(
        (
            int(round(x - radius)),
            int(round(y - radius)),
            int(round(x + radius)),
            int(round(y + radius)),
        ),
        fill=color,
    )


def _draw_gp_line_with_point_opacity(draw, pts_px, color, width_px: int, opacities: list[float]) -> None:
    if len(pts_px) < 2:
        return
    if len(opacities) != len(pts_px) or all(abs(op - 1.0) < 1.0e-6 for op in opacities):
        # PIL の line は端が四角・折れ目が欠けるため、画面 (Grease Pencil の
        # 丸い線) に合わせて折れ目を丸め、端に丸キャップを足す
        draw.line(pts_px, fill=color, width=width_px, joint="curve")
        _draw_round_cap(draw, pts_px[0], color, width_px)
        _draw_round_cap(draw, pts_px[-1], color, width_px)
        return
    for i in range(len(pts_px) - 1):
        p0 = pts_px[i]
        p1 = pts_px[i + 1]
        o0 = opacities[i]
        o1 = opacities[i + 1]
        # PIL の line は頂点ごとの alpha を持てないため、区間を細分して近似する。
        segments = 16 if abs(o1 - o0) > 1.0e-4 else 1
        prev = p0
        for j in range(1, segments + 1):
            t0 = (j - 1) / segments
            t1 = j / segments
            cur = (
                p0[0] + (p1[0] - p0[0]) * t1,
                p0[1] + (p1[1] - p0[1]) * t1,
            )
            alpha = o0 + (o1 - o0) * ((t0 + t1) * 0.5)
            draw.line([prev, cur], fill=_rgba_with_alpha_scale(color, alpha), width=width_px)
            prev = cur
    _draw_round_cap(draw, pts_px[0], _rgba_with_alpha_scale(color, opacities[0]), width_px)
    _draw_round_cap(draw, pts_px[-1], _rgba_with_alpha_scale(color, opacities[-1]), width_px)


def _render_gp_object_layers(
    obj,
    work,
    page,
    canvas_size: tuple[int, int],
    dpi: int,
    *,
    group_root: str,
    page_offset_mm: tuple[float, float],
) -> list[ExportLayer]:
    canvas_width_mm = page_grid.spread_content_width_mm(
        page,
        float(work.paper.canvas_width_mm),
        float(work.paper.finish_width_mm),
    )
    canvas_bbox = (0.0, 0.0, canvas_width_mm, float(work.paper.canvas_height_mm))
    out: list[ExportLayer] = []
    data = getattr(obj, "data", None)
    layers = getattr(data, "layers", None)
    if layers is None:
        return out
    object_parent_key = str(getattr(obj, "get", lambda *_: "")("bmanga_parent_key", "") or "")
    try:
        from ..utils import gpencil as gp_utils
        from ..utils import gp_layer_parenting as gp_parent
        from ..utils.layer_hierarchy import page_stack_key, split_child_key
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        gp_utils = None
        gp_parent = None
        page_stack_key = None
        split_child_key = None
    current_page_key = page_stack_key(page) if page_stack_key is not None else ""
    for layer in layers:
        if str(getattr(layer, "name", "") or "") == "__bmanga_mask":
            continue
        parent_key = object_parent_key
        if gp_parent is not None:
            parent_key = gp_parent.parent_key(layer) or parent_key
            if parent_key:
                layer_page_key, _child_key = split_child_key(parent_key)
                if layer_page_key != current_page_key:
                    continue
        try:
            hidden = (
                gp_utils.layer_effectively_hidden(layer)
                if gp_utils is not None
                else bool(getattr(layer, "hide", False))
            )
            if hidden:
                continue
        except Exception:  # noqa: BLE001
            if bool(getattr(layer, "hide", False)):
                continue
        frame = _gp_layer_frame(layer)
        drawing = getattr(frame, "drawing", None) if frame is not None else None
        strokes = list(getattr(drawing, "strokes", [])) if drawing is not None else []
        if not strokes:
            continue
        stroke_payloads: list[
            tuple[
                list[tuple[float, float]],
                float,
                tuple[int, int, int, int],
                tuple[int, int, int, int],
                bool,
                bool,
                list[float],
            ]
        ] = []
        bbox_pts: list[tuple[float, float]] = []
        for stroke in strokes:
            pts_mm, width_mm, point_opacities = _gp_stroke_points_mm(obj, stroke, page_offset_mm)
            if len(pts_mm) < 2:
                continue
            bbox = _points_bbox(pts_mm)
            if bbox is None:
                continue
            expanded = (bbox[0] - width_mm, bbox[1] - width_mm, bbox[2] + width_mm, bbox[3] + width_mm)
            if not _intersects_mm(expanded, canvas_bbox):
                continue
            stroke_color, fill_color, show_fill = _gp_material_info(obj, stroke)
            cyclic = bool(getattr(stroke, "cyclic", False))
            stroke_payloads.append((pts_mm, width_mm, stroke_color, fill_color, show_fill, cyclic, point_opacities))
            bbox_pts.extend(pts_mm)
        if not stroke_payloads:
            continue
        bbox = _points_bbox(bbox_pts)
        if bbox is None:
            continue
        max_width = max(payload[1] for payload in stroke_payloads)
        canvas = _canvas_for_bbox(bbox, canvas_size[1], dpi, pad_mm=max_width * 2.0)
        if canvas is None:
            continue
        draw = ImageDraw.Draw(canvas.image)
        for pts_mm, width_mm, stroke_color, fill_color, show_fill, cyclic, point_opacities in stroke_payloads:
            pts_px = canvas.points_px(pts_mm)
            width_px = max(1, int(round(mm_to_px(width_mm, dpi))))
            if cyclic and show_fill and len(pts_px) >= 3:
                draw.polygon(pts_px, fill=fill_color)
            _draw_gp_line_with_point_opacity(draw, pts_px, stroke_color, width_px, point_opacities)
            if cyclic and len(pts_px) >= 3:
                _draw_gp_line_with_point_opacity(
                    draw,
                    [*pts_px, pts_px[0]],
                    stroke_color,
                    width_px,
                    [*point_opacities, point_opacities[0] if point_opacities else 1.0],
                )
        out.append(
            ExportLayer(
                str(obj.get("bmanga_title", "") or getattr(layer, "name", "レイヤー")),
                canvas.image,
                canvas.left,
                canvas.top,
                group_path=_group_path_for_parent(
                    page,
                    "coma" if ":" in parent_key else "page",
                    parent_key,
                    ("gp", group_root),
                ),
                visible=True,
                opacity=_normalize_opacity(getattr(layer, "opacity", 1.0)),
                blend_mode=_blend_mode_name(getattr(layer, "blend_mode", "normal")),
                stack_uid=export_stack_order.stack_uid_for_object(obj),
                stack_parent_key=str(obj.get("bmanga_folder_id", "") or parent_key),
            )
        )
    return out


def _gp_layers(work, page, canvas_size: tuple[int, int], dpi: int) -> list[ExportLayer]:
    try:
        from ..utils import layer_object_model
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return []
    out: list[ExportLayer] = []
    page_offset_mm = _resolve_page_offset_mm(work, page)
    page_id = str(getattr(page, "id", "") or "")
    for obj in layer_object_model.iter_layer_objects():
        parent_key = layer_object_model.parent_key(obj)
        obj_page_id = parent_key.split(":", 1)[0] if parent_key else ""
        if obj_page_id != page_id:
            continue
        group_root = "effects" if layer_object_model.layer_kind(obj) == "effect" else "gp"
        out.extend(
            _render_gp_object_layers(
                obj,
                work,
                page,
                canvas_size,
                dpi,
                group_root=group_root,
                page_offset_mm=page_offset_mm,
            )
        )
    return out


def build_page_layers(work, page, options: ExportOptions) -> list[ExportLayer]:
    if not _HAS_PIL:
        return []
    # 作品ファイルなど、ページ詳細 (コマ・フキダシ・テキスト) を常駐させない
    # ファイルから出力するときは、ここで page.json から読み込む
    try:
        from ..utils import page_detail

        page_detail.ensure_page_detail(work, page)
    except Exception:  # noqa: BLE001
        _logger.exception("export: page detail on-demand load failed")
    from ..utils import layer_stack as layer_stack_utils
    from ..utils.layer_hierarchy import coma_stack_key
    paper = work.paper
    dpi = _dpi(paper, options)
    canvas_size = _page_canvas_size_px(work, page, options)
    layers: list[ExportLayer] = []
    if options.include_paper_color:
        layers.append(
            ExportLayer(
                "paper",
                Image.new("RGBA", canvas_size, _rgb255(paper.paper_color)),
                0,
                0,
                group_path=("paper",),
            )
        )
    else:
        layers.append(ExportLayer("paper", _empty_rgba(canvas_size), 0, 0, group_path=("paper",)))

    try:
        import bpy

        image_layers = getattr(bpy.context.scene, "bmanga_image_layers", None)
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        image_layers = None
    page_id_for_filter = str(getattr(page, "id", "") or "")
    _image_layers_by_coma: dict[str, list[ExportLayer]] = {}
    if image_layers is not None:
        for entry in image_layers:
            if not getattr(entry, "visible", True):
                continue
            entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
            entry_parent_key = str(getattr(entry, "parent_key", "") or "")
            if entry_parent_kind in {"none", "outside"}:
                continue
            if entry_parent_kind in {"page", "coma"} and entry_parent_key:
                entry_page_id = entry_parent_key.split(":", 1)[0]
                if entry_page_id and page_id_for_filter and entry_page_id != page_id_for_filter:
                    continue
            layer = _render_image_layer(
                entry,
                canvas_size,
                dpi,
                group_path=_group_path_for_parent(
                    page,
                    str(getattr(entry, "parent_kind", "") or "page"),
                    str(getattr(entry, "parent_key", "") or ""),
                    ("image_layers",),
                ),
            )
            if layer is not None:
                layer = replace(
                    layer,
                    stack_uid=layer_stack_utils.target_uid("image", str(getattr(entry, "id", "") or "")),
                )
                if entry_parent_kind == "coma" and ":" in entry_parent_key:
                    coma_id = entry_parent_key.split(":", 1)[1]
                    _image_layers_by_coma.setdefault(coma_id, []).append(layer)
                else:
                    layers.append(layer)

    try:
        fill_layers_coll = getattr(bpy.context.scene, "bmanga_fill_layers", None)
    except Exception:  # pragma: no cover
        fill_layers_coll = None
    _fill_layers_by_coma: dict[str, list[ExportLayer]] = {}
    if fill_layers_coll is not None:
        for entry in fill_layers_coll:
            if not getattr(entry, "visible", True):
                continue
            entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
            entry_parent_key = str(getattr(entry, "parent_key", "") or "")
            if entry_parent_kind in {"none", "outside"}:
                continue
            if entry_parent_kind in {"page", "coma"} and entry_parent_key:
                entry_page_id = entry_parent_key.split(":", 1)[0]
                if entry_page_id and page_id_for_filter and entry_page_id != page_id_for_filter:
                    continue
            layer = _render_fill_layer(
                entry,
                canvas_size,
                group_path=_group_path_for_parent(
                    page,
                    entry_parent_kind,
                    entry_parent_key,
                    ("fill_layers",),
                ),
            )
            if layer is not None:
                layer = replace(
                    layer,
                    stack_uid=layer_stack_utils.target_uid("fill", str(getattr(entry, "id", "") or "")),
                )
                if entry_parent_kind == "coma" and ":" in entry_parent_key:
                    coma_id = entry_parent_key.split(":", 1)[1]
                    _fill_layers_by_coma.setdefault(coma_id, []).append(layer)
                else:
                    layers.append(layer)

    try:
        raster_layers = export_raster.page_raster_layers(
            bpy.context.scene,
            work,
            page,
            canvas_size,
            dpi,
            ExportLayer,
            Image,
            group_path_for_parent=lambda entry, fallback: _group_path_for_parent(
                page,
                str(getattr(entry, "parent_kind", "") or "page"),
                str(getattr(entry, "parent_key", "") or ""),
                fallback,
            ),
            stack_uid_for_entry=lambda entry: layer_stack_utils.target_uid(
                "raster", str(getattr(entry, "id", "") or "")
            ),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("raster layer export failed")
        raster_layers = []
    _raster_layers_by_coma: dict[str, list[ExportLayer]] = {}
    for rl in raster_layers:
        gp = getattr(rl, "group_path", ()) or ()
        if len(gp) >= 2 and gp[0] == "comas":
            _raster_layers_by_coma.setdefault(gp[1], []).append(rl)
        else:
            layers.append(rl)

    for panel in sorted(page.comas, key=lambda candidate: int(getattr(candidate, "z_order", 0))):
        coma_group = _coma_root_group_path(panel)
        content_group = _coma_content_group_path(panel)
        coma_id = str(getattr(panel, "id", "") or "")
        if options.include_white_margin and getattr(panel.white_margin, "enabled", False):
            wm_layer = _draw_coma_white_margin_layer(panel, canvas_size[1], dpi)
            if wm_layer is not None:
                layers.append(replace(wm_layer, group_path=coma_group))
        if options.include_coma_backgrounds:
            bg_layer = _draw_coma_background_layer(
                panel,
                canvas_size[1],
                dpi,
                include_brush_edge=bool(options.include_border),
            )
            if bg_layer is not None:
                layers.append(replace(bg_layer, group_path=content_group))
        if options.include_coma_previews:
            render_layer = _render_coma_preview_layer(
                work,
                page,
                panel,
                canvas_size,
                dpi,
                include_brush_edge=bool(options.include_border),
            )
            if render_layer is not None:
                preview_uid = layer_stack_utils.target_uid(
                    layer_stack_utils.COMA_PREVIEW_KIND,
                    layer_stack_utils.coma_preview_key(coma_stack_key(page, panel)),
                )
                layers.append(
                    replace(
                        render_layer,
                        group_path=content_group,
                        stack_uid=preview_uid,
                        stack_parent_key=coma_stack_key(page, panel),
                    )
                )
        coma_gn = _coma_group_name(panel)
        for il in _image_layers_by_coma.get(coma_id, []):
            layers.append(il)
        for fl in _fill_layers_by_coma.get(coma_id, []):
            layers.append(fl)
        for rl in _raster_layers_by_coma.get(coma_gn, []):
            layers.append(rl)
        if options.include_border and getattr(panel.border, "visible", False):
            border_layer = _draw_coma_border_layer(panel, canvas_size[1], dpi, work=work, page=page)
            if border_layer is not None:
                layers.append(replace(border_layer, group_path=coma_group))

    layers.extend(_gp_layers(work, page, canvas_size, dpi))

    for balloon in getattr(page, "balloons", []):
        layer = _render_balloon_layer(balloon, canvas_size[1], dpi)
        if layer is not None:
            layers.append(
                replace(
                    layer,
                    # 非表示フキダシは PSD では非表示レイヤーとして残し、
                    # PNG / プレビューの合成では描かない
                    visible=bool(getattr(balloon, "visible", True)),
                    stack_uid=export_stack_order.entry_stack_uid("balloon", page, balloon),
                    stack_parent_key=export_stack_order.entry_stack_parent_key("balloon", page, balloon),
                    group_path=_group_path_for_parent(
                        page,
                        str(getattr(balloon, "parent_kind", "") or "page"),
                        str(getattr(balloon, "parent_key", "") or ""),
                        layer.group_path,
                    ),
                )
            )

    for text in getattr(page, "texts", []):
        layer = _render_text_layer(text, canvas_size[1], dpi)
        if layer is not None:
            layers.append(
                replace(
                    layer,
                    visible=bool(getattr(text, "visible", True)),
                    stack_uid=export_stack_order.entry_stack_uid("text", page, text),
                    stack_parent_key=str(getattr(text, "folder_key", "") or getattr(text, "parent_key", "") or ""),
                    group_path=_group_path_for_parent(
                        page,
                        str(getattr(text, "parent_kind", "") or "page"),
                        str(getattr(text, "parent_key", "") or ""),
                        layer.group_path,
                    ),
                )
            )

    layers = export_stack_order.apply_coma_preview_order(
        work,
        page,
        layers,
        side=str(getattr(options, "coma_preview_side", "all") or "all"),
    )

    if options.include_tombo:
        tombo = _tombo_layer(work, page, canvas_size, dpi)
        if tombo is not None:
            layers.append(tombo)

    if options.include_work_info:
        layers.extend(_work_info_layers(work, page, canvas_size, dpi))

    if options.include_nombre:
        nombre = _nombre_layer(work, page, canvas_size, dpi)
        if nombre is not None:
            layers.append(nombre)
    layers.extend(_page_overlay_fill_layers(work, page, options, canvas_size))
    return layers


def _crop_layers(
    layers: Sequence[ExportLayer],
    crop_box: tuple[int, int, int, int],
) -> tuple[list[ExportLayer], tuple[int, int]]:
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    out: list[ExportLayer] = []
    for layer in layers:
        inter_left = max(layer.left, crop_left)
        inter_top = max(layer.top, crop_top)
        inter_right = min(layer.right, crop_right)
        inter_bottom = min(layer.bottom, crop_bottom)
        if inter_right <= inter_left or inter_bottom <= inter_top:
            continue
        src_box = (
            inter_left - layer.left,
            inter_top - layer.top,
            inter_right - layer.left,
            inter_bottom - layer.top,
        )
        out.append(
            replace(
                layer,
                image=layer.image.crop(src_box),
                left=inter_left - crop_left,
                top=inter_top - crop_top,
            )
        )
    return (out, (crop_right - crop_left, crop_bottom - crop_top))


def _coma_group_masks(work, page, options: ExportOptions) -> dict[tuple[str, ...], ExportMask]:
    dpi = _dpi(work.paper, options)
    canvas_size = _page_canvas_size_px(work, page, options)
    masks: dict[tuple[str, ...], ExportMask] = {}
    for panel in sorted(page.comas, key=lambda candidate: int(getattr(candidate, "z_order", 0))):
        mask = _render_coma_mask(work, page, panel, canvas_size[1], dpi)
        if mask is not None:
            masks[_coma_content_group_path(panel)] = mask
    return masks


def _convert_flatten_mode(img, options: ExportOptions):
    if options.color_mode == "monochrome":
        return img.convert("L").convert("1", dither=Image.FLOYDSTEINBERG)
    if options.color_mode == "grayscale":
        return img.convert("L")
    if options.color_mode == "cmyk":
        converted = convert_to_cmyk(img, options.icc_profile_path)
        return converted if converted is not None else img
    return img.convert("RGBA")


def _convert_layer_mode_rgba(layer: ExportLayer, color_mode: str) -> ExportLayer:
    if color_mode == "rgb":
        return layer
    out = layer.image.convert("RGBA")
    alpha = out.getchannel("A")
    if color_mode == "grayscale":
        gray = out.convert("L")
        out = Image.merge("RGBA", (gray, gray, gray, alpha))
    elif color_mode == "monochrome":
        mono = out.convert("L").point(lambda px: 255 if px >= 128 else 0)
        out = Image.merge("RGBA", (mono, mono, mono, alpha))
    return replace(layer, image=out)


def _blend_rgb(base_rgb, src_rgb, mode: str):
    mode = (mode or "normal").lower()
    if mode == "multiply":
        return ImageChops.multiply(base_rgb, src_rgb)
    if mode == "screen":
        return ImageChops.screen(base_rgb, src_rgb)
    if mode == "lighten":
        return ImageChops.lighter(base_rgb, src_rgb)
    if mode == "overlay" and hasattr(ImageChops, "overlay"):
        return ImageChops.overlay(base_rgb, src_rgb)
    if mode in {"add", "linear_dodge"}:
        return ImageChops.add(base_rgb, src_rgb, scale=1.0)
    return src_rgb


def _composite_layer(canvas, layer: ExportLayer) -> None:
    if not layer.visible or layer.opacity <= 0:
        return
    src = layer.image.convert("RGBA")
    if layer.opacity < 255:
        src = _scale_alpha(src, layer.opacity)
    left = max(0, layer.left)
    top = max(0, layer.top)
    right = min(canvas.width, layer.right)
    bottom = min(canvas.height, layer.bottom)
    if right <= left or bottom <= top:
        return
    src_crop = src.crop((left - layer.left, top - layer.top, right - layer.left, bottom - layer.top))
    if layer.blend_mode in ("normal", "", None):
        canvas.alpha_composite(src_crop, dest=(left, top))
        return
    base_region = canvas.crop((left, top, right, bottom))
    base_rgb = base_region.convert("RGB")
    src_rgb = src_crop.convert("RGB")
    blended_rgb = _blend_rgb(base_rgb, src_rgb, layer.blend_mode)
    mask = src_crop.getchannel("A")
    mixed_rgb = Image.composite(blended_rgb, base_rgb, mask)
    alpha_region = base_region.copy()
    alpha_region.alpha_composite(src_crop)
    composed = Image.merge("RGBA", (*mixed_rgb.split(), alpha_region.getchannel("A")))
    canvas.paste(composed, (left, top))


def _flatten_layers(layers: Sequence[ExportLayer], size: tuple[int, int]) -> Any:
    canvas = _empty_rgba(size)
    for layer in layers:
        _composite_layer(canvas, layer)
    return canvas


def render_page(work, page, options: ExportOptions) -> Any:
    if not _HAS_PIL:
        _logger.warning("render_page called without Pillow")
        return None
    layers = build_page_layers(work, page, options)
    group_masks = _coma_group_masks(work, page, options)
    crop_box = _area_rect_px(work.paper, options, is_left_half=_is_left_half_page(work, page))
    if options.area != "canvas":
        layers, size = _crop_layers(layers, crop_box)
        group_masks = export_group_masks.crop_group_masks(group_masks, crop_box, ExportMask)
    else:
        size = _page_canvas_size_px(work, page, options)
    layers = export_group_masks.apply_group_masks_to_layers(layers, group_masks, Image, ImageChops)
    image = _flatten_layers(layers, size)
    return _convert_flatten_mode(image, options)


def merge_pdf(page_image_paths: list[Path], out_path: Path) -> bool:
    if not _HAS_PIL or not page_image_paths:
        return False
    try:
        Image.init()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("pdf: failed to initialize Pillow image plugins: %s", exc)
    images = []
    for path in page_image_paths:
        try:
            img = Image.open(str(path))
            if img.mode not in ("RGB", "L", "CMYK"):
                img = img.convert("RGB")
            images.append(img)
        except (OSError, ValueError) as exc:
            _logger.warning("pdf: failed to open %s: %s", path, exc)
    if not images:
        return False
    try:
        first, rest = images[0], images[1:]
        first.save(str(out_path), save_all=True, append_images=rest)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("merge_pdf failed: %s", exc)
        return False


def save_page_as_psd(work, page, options: ExportOptions, out_path: Path) -> bool:
    if not _HAS_PIL:
        raise RuntimeError("Pillow が利用できません")
    if not export_psd.can_write_layered_psd():
        raise RuntimeError("PSD レイヤー出力を利用できません")
    if options.color_mode == "cmyk":
        raise RuntimeError("PSD レイヤー出力での CMYK は未対応です")
    layers = build_page_layers(work, page, options)
    group_masks = _coma_group_masks(work, page, options)
    layers = [_convert_layer_mode_rgba(layer, options.color_mode) for layer in layers]
    crop_box = _area_rect_px(work.paper, options, is_left_half=_is_left_half_page(work, page))
    if options.area != "canvas":
        layers, size = _crop_layers(layers, crop_box)
        group_masks = export_group_masks.crop_group_masks(group_masks, crop_box, ExportMask)
    else:
        size = _page_canvas_size_px(work, page, options)
    if not layers:
        layers = [ExportLayer("empty", _empty_rgba(size), 0, 0)]
    ok = export_psd.save_layers_as_psd(layers, size, out_path, group_masks=group_masks)
    if not ok:
        raise RuntimeError("PSD 保存に失敗しました")
    return True


def save_as_psd(img, out_path: Path) -> bool:
    if not _HAS_PIL:
        return False
    return export_psd.save_flat_image_as_psd(img, out_path)


def convert_to_cmyk(img, icc_profile_path: str = "") -> "Image.Image | None":
    if not _HAS_PIL:
        return None
    if img.mode == "CMYK":
        return img
    if icc_profile_path and ImageCms is not None:
        try:
            srgb = ImageCms.createProfile("sRGB")
            cmyk = ImageCms.ImageCmsProfile(icc_profile_path)
            transform = ImageCms.buildTransform(srgb, cmyk, "RGB", "CMYK")
            return ImageCms.applyTransform(img.convert("RGB"), transform)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("ImageCms transform failed, fallback: %s", exc)
    return img.convert("CMYK")
