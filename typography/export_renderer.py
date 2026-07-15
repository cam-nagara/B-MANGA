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


# CJK フォントはベースラインを em ボディ下端から 0.12em 上に置く設計慣習。
# ボディ中心を回転軸に取るときのアセンダ上端→ボディ上端の補正に使う。
_CJK_BODY_ASCENT_RATIO = 0.88


def _draw_rotated_char(
    image: Any,
    draw: Any,
    ch: str,
    font: Any,
    x: float,
    y: float,
    size_px: int,
    rotation_deg: float,
    **kwargs,
) -> None:
    """1文字を回転して image に合成 (全角ボディ中心を軸に回転).

    rotation_deg は blf と同じ符号 (負=時計回り)。Pillow の ``Image.rotate``
    は正=反時計回りなので、そのまま渡すと同じ見た目になる。
    """
    margin = size_px
    tmp_size = size_px * 3
    tmp = Image.new("RGBA", (tmp_size, tmp_size), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    tmp_draw.text((margin, margin), ch, font=font, **kwargs)
    # 既定アンカー "la" ではアセンダ上端が y に来るため、em ボディ上端は
    # ascent - 0.88em だけ下がる。ボディ中心を回転軸に補正する。
    try:
        ascent_px = float(font.getmetrics()[0])
    except Exception:  # noqa: BLE001 - ビットマップ代替フォント等は補正なし
        ascent_px = size_px * _CJK_BODY_ASCENT_RATIO
    body_top = margin + ascent_px - size_px * _CJK_BODY_ASCENT_RATIO
    center = (margin + size_px * 0.5, body_top + size_px * 0.5)
    rotated = tmp.rotate(rotation_deg, resample=Image.BICUBIC, expand=False, center=center)
    # tmp の (margin, margin) が非回転描画の (x, y) と一致するように合成する
    image.alpha_composite(rotated, (int(x - margin), int(y - margin)))


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
        x = origin_xy_px[0] + (g.x_mm + g.offset_x_mm) * px_per_mm
        y = origin_xy_px[1] + (g.y_mm + g.offset_y_mm) * px_per_mm
        # layout.py の y は文字の下端、Pillow は左上原点の上端指定。
        y_px = _layout_bottom_to_pillow_top(image.height, y, size_px)
        glyph_color = color_for_index(g.index) if color_for_index is not None else color
        kwargs: dict = {"fill": glyph_color, "stroke_width": 1, "stroke_fill": glyph_color}
        if stroke_width_px > 0:
            kwargs["stroke_width"] = stroke_width_px
            kwargs["stroke_fill"] = stroke_color
        if g.rotation_deg != 0.0:
            _draw_rotated_char(image, draw, g.ch, font, x, y_px, size_px, g.rotation_deg, **kwargs)
        else:
            draw.text((x, y_px), g.ch, font=font, **kwargs)
            if bold_for_index is not None and bold_for_index(g.index):
                draw.text((x + max(1, size_px // 28), y_px), g.ch, font=font, **kwargs)
            if italic_for_index is not None and italic_for_index(g.index):
                draw.text(
                    (x + max(1, int(round(size_px * 0.055))), y_px - max(1, int(round(size_px * 0.025)))),
                    g.ch,
                    font=font,
                    **kwargs,
                )
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
