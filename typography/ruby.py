"""ルビ配置 (モノルビ/グループルビ/熟語ルビ).

計画書 3.1.5 参照。親文字配置 (layout.typeset の結果) に対して、ルビ
スパン (TextEntry.ruby_spans) を元にルビ文字の座標を計算する。
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import GlyphPlacement
from . import ruby_presentation


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
    if isinstance(span, dict):
        return span.get(name, default)
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
    return ruby_presentation.gap_mm_from_entry(entry)


def ruby_letter_spacing_from_entry(entry) -> float:
    return _entry_float(entry, "ruby_letter_spacing", 0.0)


def ruby_font_path_from_entry(entry) -> str:
    return ruby_presentation.resolve_font_path(entry)


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
        writing_mode=str(getattr(entry, "writing_mode", "horizontal") or "horizontal"),
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


def _ruby_extent(ruby_em: float, count: int, letter_spacing: float) -> float:
    if count <= 1:
        return ruby_em
    pitch = ruby_em * max(0.1, 1.0 + float(letter_spacing))
    return ruby_em + pitch * (count - 1)


def _ls_for_target_extent(target: float, ruby_em: float, count: int) -> float:
    if count <= 1 or ruby_em < 1e-9:
        return 0.0
    pitch = (target - ruby_em) / (count - 1)
    return max(0.0, pitch / ruby_em - 1.0)


def _ruby_rp_range(info: dict) -> tuple[float, float]:
    actual = max(info['parent_span'], info['ext'])
    align = info.get('align', 'center')
    if align == "start":
        return info['rp_lo'], info['rp_lo'] + actual
    c = info['rp_center']
    return c - actual * 0.5, c + actual * 0.5


def _resolve_ruby_overlaps(infos: list[dict]) -> None:
    for i in range(len(infos) - 1):
        a, b = infos[i], infos[i + 1]
        if a.get('style') == 'jukugo' and a.get('group_id') == b.get('group_id'):
            continue
        _, a_hi = _ruby_rp_range(a)
        b_lo, _ = _ruby_rp_range(b)
        if a_hi <= b_lo + 1e-6:
            continue
        overlap = a_hi - b_lo
        if a.get('align') == "start":
            max_ext = b['rp_lo'] - a['rp_lo']
            a['ext'] = max(a['min_ext'], min(a['ext'], max_ext))
            a['eff_ls'] = _ls_for_target_extent(a['ext'], a['ruby_em'], a['count'])
            continue
        needed = 2.0 * overlap
        a_of = max(0.0, a['ext'] - a['parent_span'])
        b_of = max(0.0, b['ext'] - b['parent_span'])
        total_of = a_of + b_of
        if total_of < 1e-9:
            continue
        a_share = needed * a_of / total_of
        b_share = needed * b_of / total_of
        a_room = max(0.0, a['ext'] - a['min_ext'])
        b_room = max(0.0, b['ext'] - b['min_ext'])
        a_cut = min(a_share, a_room)
        b_cut = min(b_share, b_room)
        left = needed - a_cut - b_cut
        if left > 1e-6:
            extra = min(left, a_room - a_cut)
            a_cut += extra
            left -= extra
        if left > 1e-6:
            b_cut += min(left, b_room - b_cut)
        a['ext'] -= a_cut
        b['ext'] -= b_cut
        a['eff_ls'] = _ls_for_target_extent(a['ext'], a['ruby_em'], a['count'])
        b['eff_ls'] = _ls_for_target_extent(b['ext'], b['ruby_em'], b['count'])


def _expanded_spans(ruby_spans) -> list[dict]:
    """v2 segmentを組版単位へ展開する。曖昧な旧データは従来の全体割付を保つ。"""
    expanded: list[dict] = []
    for group_id, span in enumerate(ruby_spans):
        base = {
            "start": int(_span_value(span, "start", 0)),
            "length": max(1, int(_span_value(span, "length", 1))),
            "ruby_text": str(_span_value(span, "ruby_text", "") or ""),
            "style": str(_span_value(span, "style", "group") or "group"),
            "group_id": group_id,
        }
        segments = list(_span_value(span, "segments", []) or [])
        if base["style"] not in {"mono", "jukugo"} or not segments:
            expanded.append(base)
            continue
        for segment in segments:
            rel_start = int(_span_value(segment, "start", 0))
            length = max(1, int(_span_value(segment, "length", 1)))
            if rel_start < 0 or rel_start + length > base["length"]:
                continue
            text = str(_span_value(segment, "ruby_text", "") or "")
            if not text:
                continue
            expanded.append({
                "start": base["start"] + rel_start,
                "length": length,
                "ruby_text": text,
                "style": base["style"],
                "group_id": group_id,
            })
    return expanded


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

    隣接スパンのルビが重なる場合は字間を自動圧縮して回避する。
    """
    is_horiz = str(writing_mode or "horizontal") == "horizontal"

    # ── Phase 1: スパン情報を収集 ──
    infos: list[dict] = []
    for span in _expanded_spans(ruby_spans):
        start_idx = int(_span_value(span, "start", 0))
        length = max(1, int(_span_value(span, "length", 1)))
        end_idx = start_idx + length
        covered = [
            g for g in parent_glyphs
            if start_idx <= int(getattr(g, "index", -1)) < end_idx
        ]
        if not covered:
            continue
        ruby_text = str(_span_value(span, "ruby_text", "") or "")
        if not ruby_text:
            continue
        ruby_text = _normalize_small_kana(ruby_text, ruby_small_kana)
        ruby_size = covered[0].size_pt * max(0.05, float(ruby_size_ratio))
        ruby_em = ruby_size * 25.4 / 72.0
        count = len(ruby_text)
        if count <= 0:
            continue
        if is_horiz:
            rp_lo = min(g.x_mm for g in covered)
            rp_hi = max(g.x_mm + _glyph_em_mm(g) for g in covered)
        else:
            rp_lo = -max(g.y_mm + _glyph_em_mm(g) for g in covered)
            rp_hi = -min(g.y_mm for g in covered)
        style = str(_span_value(span, "style", "group") or "group")
        effective_align = "center" if style == "mono" else ruby_align
        infos.append({
            'covered': covered, 'ruby_text': ruby_text,
            'ruby_size': ruby_size, 'ruby_em': ruby_em, 'count': count,
            'rp_lo': rp_lo, 'rp_hi': rp_hi,
            'rp_center': (rp_lo + rp_hi) * 0.5,
            'parent_span': rp_hi - rp_lo,
            'ext': _ruby_extent(ruby_em, count, ruby_letter_spacing),
            'min_ext': _ruby_extent(ruby_em, count, 0.0),
            'eff_ls': float(ruby_letter_spacing),
            'style': style, 'group_id': _span_value(span, "group_id", -1),
            'align': effective_align,
        })

    if not infos:
        return []

    # ── Phase 2: 隣接ルビの重なりを字間圧縮で解消 ──
    if len(infos) >= 2:
        infos.sort(key=lambda s: s['rp_lo'])
        _resolve_ruby_overlaps(infos)

    # ── Phase 3: 配置を生成 ──
    out: list[RubyPlacement] = []
    font = str(ruby_font_path or "")
    for info in infos:
        ls = info['eff_ls']
        em = info['ruby_em']
        cnt = info['count']
        txt = info['ruby_text']
        sz = info['ruby_size']
        align = info['align']
        if is_horiz:
            left_x = min(g.x_mm for g in info['covered'])
            right_x = max(g.x_mm + _glyph_em_mm(g) for g in info['covered'])
            top_y = max(g.y_mm + _glyph_em_mm(g) for g in info['covered'])
            starts = _distributed_starts(
                parent_start=left_x, parent_end=right_x,
                ruby_em=em, count=cnt,
                letter_spacing=ls, align=align,
            )
            ry = top_y + ruby_offset_mm
            for rch, rx in zip(txt, starts):
                out.append(RubyPlacement(
                    ch=rch, x_mm=rx, y_mm=ry, size_pt=sz, font_path=font,
                ))
        else:
            top_y = max(g.y_mm + _glyph_em_mm(g) for g in info['covered'])
            bot_y = min(g.y_mm for g in info['covered'])
            p_right = max(g.x_mm + _glyph_em_mm(g) for g in info['covered'])
            rx = p_right + ruby_offset_mm
            starts = _distributed_starts(
                parent_start=bot_y, parent_end=top_y,
                ruby_em=em, count=cnt,
                letter_spacing=ls, align=align,
            )
            if align == "start" and starts:
                shift = (top_y - em) - starts[-1]
                if abs(shift) > 1e-6:
                    starts = [s + shift for s in starts]
            for rch, ry in zip(txt, reversed(starts)):
                out.append(RubyPlacement(
                    ch=rch, x_mm=rx, y_mm=ry, size_pt=sz, font_path=font,
                ))
    return out
