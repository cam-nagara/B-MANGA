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
    font_path: str = ""


def _glyph_em_mm(glyph: GlyphPlacement) -> float:
    return float(glyph.size_pt) * 25.4 / 72.0


def _span_value(span, name: str, default):
    try:
        return getattr(span, name)
    except Exception:  # noqa: BLE001
        return default


def _entry_float(entry, name: str, default: float, *, min_value: float | None = None) -> float:
    try:
        value = float(getattr(entry, name, default))
    except Exception:  # noqa: BLE001
        value = float(default)
    if min_value is not None:
        value = max(float(min_value), value)
    return value


def ruby_size_ratio_from_entry(entry) -> float:
    return _entry_float(entry, "ruby_size_percent", 50.0, min_value=5.0) / 100.0


def ruby_gap_mm_from_entry(entry) -> float:
    return _entry_float(entry, "ruby_gap_mm", 0.0, min_value=0.0)


def ruby_letter_spacing_from_entry(entry) -> float:
    return _entry_float(entry, "ruby_letter_spacing", 0.0)


def ruby_font_path_from_entry(entry) -> str:
    return str(getattr(entry, "ruby_font", "") or "")


def ruby_align_from_entry(entry) -> str:
    return str(getattr(entry, "ruby_align", "center") or "center")


def ruby_small_kana_from_entry(entry) -> str:
    return str(getattr(entry, "ruby_small_kana", "keep") or "keep")


_SMALL_TO_FULL_KANA: dict[str, str] = {
    "ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お",
    "っ": "つ", "ゃ": "や", "ゅ": "ゆ", "ょ": "よ", "ゎ": "わ",
    "ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ",
    "ッ": "ツ", "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ヮ": "ワ",
    "ゕ": "か", "ゖ": "け", "ヵ": "カ", "ヶ": "ケ",
}


def _normalize_small_kana(text: str, mode: str) -> str:
    if mode != "fullsize":
        return text
    return "".join(_SMALL_TO_FULL_KANA.get(ch, ch) for ch in text)


def render_pad_mm_for_entry(entry, minimum: float = 1.5) -> float:
    pad = max(0.0, float(minimum))
    spans = getattr(entry, "ruby_spans", []) or []
    if not spans:
        return pad
    try:
        from ..utils.geom import q_to_mm

        base_em = q_to_mm(float(getattr(entry, "font_size_q", 20.0)))
    except Exception:  # noqa: BLE001
        base_em = 5.0
    ruby_em = base_em * ruby_size_ratio_from_entry(entry)
    gap = ruby_gap_mm_from_entry(entry)
    spacing = max(0.1, 1.0 + ruby_letter_spacing_from_entry(entry))
    max_count = 0
    for span in spans:
        max_count = max(max_count, len(str(_span_value(span, "ruby_text", "") or "")))
    natural_span = ruby_em if max_count <= 1 else ruby_em + ruby_em * spacing * (max_count - 1)
    return max(pad, gap + ruby_em + 1.0, natural_span * 0.5 + 1.0)


def compute_for_entry(parent_glyphs: list[GlyphPlacement], entry) -> list[RubyPlacement]:
    return compute_ruby_placements(
        parent_glyphs,
        getattr(entry, "ruby_spans", []) or [],
        ruby_size_ratio=ruby_size_ratio_from_entry(entry),
        ruby_offset_mm=ruby_gap_mm_from_entry(entry),
        ruby_letter_spacing=ruby_letter_spacing_from_entry(entry),
        ruby_font_path=ruby_font_path_from_entry(entry),
        writing_mode=str(getattr(entry, "writing_mode", "vertical") or "vertical"),
        ruby_align=ruby_align_from_entry(entry),
        ruby_small_kana=ruby_small_kana_from_entry(entry),
    )


def _distributed_starts(
    *,
    parent_start: float,
    parent_end: float,
    ruby_em: float,
    count: int,
    letter_spacing: float,
    align: str = "center",
) -> list[float]:
    count = int(count)
    if count <= 0:
        return []
    parent_span = max(0.0, float(parent_end) - float(parent_start))
    parent_center = (float(parent_start) + float(parent_end)) * 0.5
    if count == 1:
        if align == "start":
            return [float(parent_start)]
        return [parent_center - ruby_em * 0.5]
    natural_pitch = ruby_em * max(0.1, 1.0 + float(letter_spacing))
    natural_span = ruby_em + natural_pitch * (count - 1)
    target_span = max(parent_span, natural_span)
    step = 0.0 if count <= 1 else (target_span - ruby_em) / (count - 1)
    if align == "start":
        first = float(parent_start)
    else:
        first = parent_center - target_span * 0.5
    return [first + step * i for i in range(count)]


def compute_ruby_placements(
    parent_glyphs: list[GlyphPlacement],
    ruby_spans,
    ruby_size_ratio: float = 0.5,
    ruby_offset_mm: float = 0.0,
    ruby_letter_spacing: float = 0.0,
    ruby_font_path: str = "",
    writing_mode: str = "vertical",
    ruby_align: str = "center",
    ruby_small_kana: str = "keep",
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
        ruby_text = _normalize_small_kana(ruby_text, ruby_small_kana)
        parent_size = covered[0].size_pt
        ruby_size = parent_size * max(0.05, float(ruby_size_ratio))
        ruby_em = ruby_size * 25.4 / 72.0
        count = len(ruby_text)
        if count == 0:
            continue
        if str(writing_mode or "vertical") == "horizontal":
            left_x = min(g.x_mm for g in covered)
            right_x = max(g.x_mm + _glyph_em_mm(g) for g in covered)
            top_y = max(g.y_mm + _glyph_em_mm(g) for g in covered)
            starts = _distributed_starts(
                parent_start=left_x,
                parent_end=right_x,
                ruby_em=ruby_em,
                count=count,
                letter_spacing=ruby_letter_spacing,
                align=ruby_align,
            )
            ruby_y = top_y + ruby_offset_mm
            for rch, rx in zip(ruby_text, starts):
                out.append(
                    RubyPlacement(
                        ch=rch,
                        x_mm=rx,
                        y_mm=ruby_y,
                        size_pt=ruby_size,
                        font_path=str(ruby_font_path or ""),
                    )
                )
        else:
            top_y = max(g.y_mm + _glyph_em_mm(g) for g in covered)
            bottom_y = min(g.y_mm for g in covered)
            parent_right = max(g.x_mm + _glyph_em_mm(g) for g in covered)
            ruby_x = parent_right + ruby_offset_mm
            starts = _distributed_starts(
                parent_start=bottom_y,
                parent_end=top_y,
                ruby_em=ruby_em,
                count=count,
                letter_spacing=ruby_letter_spacing,
                align=ruby_align,
            )
            if ruby_align == "start" and starts:
                shift = (top_y - ruby_em) - starts[-1]
                if abs(shift) > 1e-6:
                    starts = [s + shift for s in starts]
            for rch, ry in zip(ruby_text, reversed(starts)):
                out.append(
                    RubyPlacement(
                        ch=rch,
                        x_mm=ruby_x,
                        y_mm=ry,
                        size_pt=ruby_size,
                        font_path=str(ruby_font_path or ""),
                    )
                )
    return out
