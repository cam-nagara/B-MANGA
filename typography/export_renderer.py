"""書き出し用テキストレンダリング (Pillow).

計画書 3.1.5 / 3.8.4 参照。layout.py の計算結果を受け取り、Pillow 画像
に焼き込む。書き出しパイプライン (Phase 6) から呼ばれる。

Pillow が同梱されていない環境 (Phase 3 時点) では使えないため、遅延
インポートにして fallback を設ける。
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable, Sequence

from ..utils import python_deps
from ..utils import log
from .layout import TypesetResult

python_deps.ensure_bundled_wheels_on_path()

_logger = log.get_logger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    _HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow is bundled later
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    _HAS_PIL = False


def has_pillow() -> bool:
    return _HAS_PIL


def _layout_bottom_to_pillow_top(image_height: int, y_px: float, size_px: int) -> float:
    return float(image_height) - (float(y_px) + float(size_px))


def _draw_rotated_char(
    image: Any,
    draw: Any,
    ch: str,
    font: Any,
    x: float,
    y: float,
    size_px: int,
    rotation_deg: float,
    is_bold: bool = False,
    is_italic: bool = False,
    **kwargs,
) -> None:
    """1文字を回転して image に合成（太字・斜体にも対応）."""
    margin = size_px
    tmp_size = size_px + margin * 2
    tmp = Image.new("RGBA", (tmp_size, tmp_size), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    bold_kwargs = dict(kwargs)
    if is_bold:
        bold_kwargs["stroke_width"] = max(
            int(kwargs.get("stroke_width", 0) or 0),
            max(1, size_px // 16),
        )
        bold_kwargs.setdefault("stroke_fill", kwargs.get("fill"))
    tmp_draw.text((margin, margin), ch, font=font, **bold_kwargs)
    if is_italic:
        tmp = _apply_shear(tmp)
    rotated = tmp.rotate(-rotation_deg, resample=Image.BICUBIC, expand=False)
    paste_x = int(x - tmp_size / 2 + size_px * 0.5)
    paste_y = int(y - tmp_size / 2 + size_px * 0.5)
    image.alpha_composite(rotated, (paste_x, paste_y))


def _draw_italic_char(
    image: Any,
    draw: Any,
    ch: str,
    font: Any,
    x: float,
    y: float,
    size_px: int,
    is_bold: bool = False,
    **kwargs,
) -> None:
    """1文字を斜体（シア変換）で image に合成."""
    margin = size_px
    tmp_size = size_px + margin * 2
    tmp = Image.new("RGBA", (tmp_size, tmp_size), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    bold_kwargs = dict(kwargs)
    if is_bold:
        bold_kwargs["stroke_width"] = max(
            int(kwargs.get("stroke_width", 0) or 0),
            max(1, size_px // 16),
        )
        bold_kwargs.setdefault("stroke_fill", kwargs.get("fill"))
    tmp_draw.text((margin, margin), ch, font=font, **bold_kwargs)
    tmp = _apply_shear(tmp)
    paste_x = int(x - margin)
    paste_y = int(y - margin)
    image.alpha_composite(tmp, (paste_x, paste_y))


def _apply_shear(img: Any, angle_deg: float = 12.0) -> Any:
    """画像にシア（斜体）変換を適用する."""
    import math

    shear = math.tan(math.radians(angle_deg))
    w, h = img.size
    new_w = int(w + abs(shear) * h)
    coeffs = (1, -shear, shear * h if shear > 0 else 0, 0, 1, 0)
    result = img.transform((new_w, h), Image.AFFINE, coeffs, resample=Image.BICUBIC)
    dx = (new_w - w) // 2
    return result.crop((dx, 0, dx + w, h))


def render_to_image(
    result: TypesetResult,
    image: Any,
    *,
    font_path: str,
    font_path_for_index: Callable[[int], str] | None = None,
    color_for_index: Callable[[int], tuple[int, int, int, int]] | None = None,
    bold_for_index: Callable[[int], bool] | None = None,
    italic_for_index: Callable[[int], bool] | None = None,
    px_per_mm: float,
    origin_xy_px: tuple[float, float] = (0.0, 0.0),
    color: tuple[int, int, int, int] = (0, 0, 0, 255),
    stroke_width_px: int = 0,
    stroke_color: tuple[int, int, int, int] = (255, 255, 255, 255),
    ruby_placements: Sequence[Any] | None = None,
) -> None:
    """Pillow Image に組版結果を描画."""
    if not _HAS_PIL:
        _logger.warning("Pillow not bundled; export_renderer disabled")
        return
    draw = ImageDraw.Draw(image)
    font_cache: dict[tuple[str, int], Any] = {}

    def font_for(path: str, size_px: int):
        cache_key = (path or "", int(size_px))
        font = font_cache.get(cache_key)
        if font is not None:
            return font
        try:
            font = ImageFont.truetype(path, size_px)
        except (OSError, IOError):
            font = ImageFont.load_default()
        font_cache[cache_key] = font
        return font

    for g in result.placements:
        size_px = max(1, int(g.size_pt * px_per_mm * 25.4 / 72.0))
        glyph_font_path = font_path_for_index(g.index) if font_path_for_index is not None else font_path
        font = font_for(glyph_font_path, size_px)
        x = origin_xy_px[0] + g.x_mm * px_per_mm
        y = origin_xy_px[1] + g.y_mm * px_per_mm
        # layout.py の y は文字の下端、Pillow は左上原点の上端指定。
        y_px = _layout_bottom_to_pillow_top(image.height, y, size_px)
        glyph_color = color_for_index(g.index) if color_for_index is not None else color
        kwargs: dict = {"fill": glyph_color, "stroke_width": 1, "stroke_fill": glyph_color}
        if stroke_width_px > 0:
            kwargs["stroke_width"] = stroke_width_px
            kwargs["stroke_fill"] = stroke_color
        is_bold = bold_for_index is not None and bold_for_index(g.index)
        is_italic = italic_for_index is not None and italic_for_index(g.index)
        if g.rotation_deg != 0.0:
            _draw_rotated_char(
                image, draw, g.ch, font, x, y_px, size_px, g.rotation_deg,
                is_bold=is_bold, is_italic=is_italic, **kwargs,
            )
        elif is_italic:
            _draw_italic_char(image, draw, g.ch, font, x, y_px, size_px, is_bold=is_bold, **kwargs)
        else:
            glyph_kwargs = dict(kwargs)
            if is_bold:
                glyph_kwargs["stroke_width"] = max(
                    int(kwargs.get("stroke_width", 0) or 0),
                    max(1, size_px // 16),
                )
                glyph_kwargs.setdefault("stroke_fill", kwargs.get("fill"))
            draw.text((x, y_px), g.ch, font=font, **glyph_kwargs)
    for r in ruby_placements or ():
        size_pt = float(getattr(r, "size_pt", 0.0) or 0.0)
        if size_pt <= 0.0:
            continue
        size_px = max(1, int(size_pt * px_per_mm * 25.4 / 72.0))
        ruby_font_path = str(getattr(r, "font_path", "") or font_path)
        font = font_for(ruby_font_path, size_px)
        x = origin_xy_px[0] + float(getattr(r, "x_mm", 0.0)) * px_per_mm
        y = origin_xy_px[1] + float(getattr(r, "y_mm", 0.0)) * px_per_mm
        y_px = _layout_bottom_to_pillow_top(image.height, y, size_px)
        kwargs: dict = {"fill": color, "stroke_width": 1, "stroke_fill": color}
        if stroke_width_px > 0:
            kwargs["stroke_width"] = max(1, stroke_width_px // 2)
            kwargs["stroke_fill"] = stroke_color
        draw.text((x, y_px), str(getattr(r, "ch", "") or ""), font=font, **kwargs)
