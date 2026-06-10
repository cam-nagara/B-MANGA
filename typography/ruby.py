"""ルビ配置 (モノルビ/グループルビ/熟語ルビ).

計画書 3.1.5 参照。親文字配置 (layout.typeset の結果) に対して、ルビ
スパン (TextEntry.ruby_spans) を元にルビ文字の座標を計算する。
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import GlyphPlacement


@dataclass(frozen=True)
class RubyPlacement:
    ch: str
    x_mm: float
    y_mm: float
    size_pt: float


def _glyph_em_mm(glyph: GlyphPlacement) -> float:
    return float(glyph.size_pt) * 25.4 / 72.0


def _span_value(span, name: str, default):
    try:
        return getattr(span, name)
    except Exception:  # noqa: BLE001
        return default


def compute_ruby_placements(
    parent_glyphs: list[GlyphPlacement],
    ruby_spans,
    ruby_size_ratio: float = 0.5,
    ruby_offset_mm: float = 0.3,
    writing_mode: str = "vertical",
) -> list[RubyPlacement]:
    """親文字の配置とルビスパンからルビ座標を計算.

    縦書きでは親文字の右側、横書きでは親文字の上側へ小さく並べる。
    """
    out: list[RubyPlacement] = []
    for span in ruby_spans:
        start = int(_span_value(span, "start", 0))
        length = max(1, int(_span_value(span, "length", 1)))
        end = start + length
        covered = [
            glyph for glyph in parent_glyphs
            if start <= int(getattr(glyph, "index", -1)) < end
        ]
        if not covered:
            continue
        ruby_text = str(_span_value(span, "ruby_text", "") or "")
        if not ruby_text:
            continue
        parent_size = covered[0].size_pt
        ruby_size = parent_size * ruby_size_ratio
        ruby_em = ruby_size * 25.4 / 72.0
        count = len(ruby_text)
        if count == 0:
            continue
        if str(writing_mode or "vertical") == "horizontal":
            left_x = min(g.x_mm for g in covered)
            right_x = max(g.x_mm + _glyph_em_mm(g) for g in covered)
            top_y = max(g.y_mm + _glyph_em_mm(g) for g in covered)
            ruby_width = ruby_em * count
            start_x = (left_x + right_x - ruby_width) * 0.5
            ruby_y = top_y + ruby_offset_mm
            for i, rch in enumerate(ruby_text):
                rx = start_x + ruby_em * i
                out.append(RubyPlacement(ch=rch, x_mm=rx, y_mm=ruby_y, size_pt=ruby_size))
        else:
            top_y = max(g.y_mm + _glyph_em_mm(g) for g in covered)
            bottom_y = min(g.y_mm for g in covered)
            parent_x = max(g.x_mm for g in covered)
            ruby_x = parent_x + _glyph_em_mm(covered[0]) + ruby_offset_mm
            ruby_height = ruby_em * count
            start_y = (top_y + bottom_y + ruby_height) * 0.5 - ruby_em
            for i, rch in enumerate(ruby_text):
                ry = start_y - ruby_em * i
                out.append(RubyPlacement(ch=rch, x_mm=ruby_x, y_mm=ry, size_pt=ruby_size))
    return out
