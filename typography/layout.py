"""縦書き・横書きレイアウトエンジン.

計画書 3.1.5 参照。テキスト文字列と矩形領域 (mm) を受け取り、各文字の
配置座標 (mm) と行分割情報を計算する。描画はしない。

ビューポート用 (blf) / 書き出し用 (Pillow) で共通で呼ばれるよう、純粋
データ返却のみに徹する。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..utils import text_style
from ..utils.geom import q_to_pt
from . import metrics


@dataclass(frozen=True)
class GlyphPlacement:
    """1 文字の配置."""

    ch: str
    x_mm: float
    y_mm: float
    size_pt: float  # この文字の描画サイズ (縦中横などで変わる)
    rotation_deg: float  # 0=通常、-90=縦中横の横向き等
    index: int = -1  # 元本文内の文字インデックス
    # 字面の描画時ずらし (縦書きの句読点・小書き仮名など)。セル座標
    # (x_mm/y_mm) には含めないため、カーソル・ルビ・当たり判定は不変。
    offset_x_mm: float = 0.0  # 右+
    offset_y_mm: float = 0.0  # 上+


@dataclass(frozen=True)
class TypesetResult:
    """テキスト組版結果."""

    placements: list[GlyphPlacement]
    overflow: bool  # 収まり切らずに切れたか


def _mm_per_em_at(size_pt: float) -> float:
    """フォントサイズ (pt) 1em あたりの mm."""
    # 1 pt = 1/72 inch = 25.4/72 mm
    return size_pt * 25.4 / 72.0


def _ruby_parent_indices(ruby_spans) -> set[int]:
    indices: set[int] = set()
    for span in ruby_spans or ():
        try:
            start = int(getattr(span, "start", 0))
            length = max(1, int(getattr(span, "length", 1)))
        except Exception:  # noqa: BLE001
            continue
        indices.update(range(start, start + length))
    return indices


def _logical_line_ruby_flags(text: str, ruby_indices: set[int]) -> list[bool]:
    flags = [False]
    line_index = 0
    for text_index, ch in enumerate(text or ""):
        if ch == "\n":
            line_index += 1
            flags.append(False)
            continue
        if text_index in ruby_indices:
            flags[line_index] = True
    return flags


def _line_advance_mm(base_em_mm: float, line_height: float, ruby_line_height: float, has_ruby: bool) -> float:
    height = ruby_line_height if has_ruby else line_height
    return base_em_mm * max(0.1, float(height))


def _line_flag(flags: list[bool], logical_line_index: int) -> bool:
    if 0 <= logical_line_index < len(flags):
        return bool(flags[logical_line_index])
    return False


def _normalize_tatechuyoko_starts(
    text: str, ranges: Sequence[tuple[int, int]] | None
) -> dict[int, int]:
    r"""縦中横範囲をソート・重複除去し、範囲開始インデックス→長さの辞書にする.

    範囲外へはみ出す指定は本文長へ切り詰め、改行 (\n) を含む範囲は無効として
    通常処理 (縦積み) に落とす。重なる範囲は開始位置が早いものを優先する。
    """
    if not ranges:
        return {}
    text_len = len(text)
    cleaned: list[tuple[int, int]] = []
    for item in ranges:
        try:
            start = int(item[0])
            length = int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if length <= 0:
            continue
        start = max(0, start)
        end = min(text_len, start + length)
        if start >= end:
            continue
        if "\n" in text[start:end]:
            continue
        cleaned.append((start, end - start))
    cleaned.sort(key=lambda r: (r[0], r[1]))
    starts: dict[int, int] = {}
    occupied_until = -1
    for start, length in cleaned:
        if start < occupied_until:
            continue
        starts[start] = length
        occupied_until = start + length
    return starts


def _place_tatechuyoko_cell(
    text: str,
    start: int,
    count: int,
    region_x_mm: float,
    region_y_mm: float,
    region_width_mm: float,
    col_offset_mm: float,
    y_cursor: float,
    letter_spacing: float,
    font_size_pt: float,
    font_size_pt_for_index,
) -> tuple[list[GlyphPlacement], float] | None:
    """縦中横セル (1 文字分の領域に count 文字を横並び) の配置を計算する.

    列に収まらない場合は None を返す (呼出側が列送りしてから再試行する)。
    """
    base_size_pt = (
        float(font_size_pt_for_index(start)) if font_size_pt_for_index is not None else font_size_pt
    )
    em_mm = _mm_per_em_at(base_size_pt)
    char_pitch_mm = em_mm * (1.0 + letter_spacing)
    x = region_x_mm + region_width_mm - em_mm - col_offset_mm
    y = y_cursor - em_mm
    if x < region_x_mm or y < region_y_mm:
        return None

    # 半角文字の自然幅は 0.5em。count 文字を 1em 領域へ収めるための縮小率。
    scale = min(1.0, 2.0 / count) if count > 0 else 1.0
    glyph_size_pt = base_size_pt * scale
    total_em = 0.5 * count * scale
    margin_mm = max(0.0, 1.0 - total_em) * 0.5 * em_mm
    left_x = x + margin_mm

    cell_glyphs: list[GlyphPlacement] = []
    for i in range(count):
        offset_x = 0.5 * scale * em_mm * i
        cell_glyphs.append(
            GlyphPlacement(
                ch=text[start + i],
                x_mm=left_x + offset_x,
                y_mm=y,
                size_pt=glyph_size_pt,
                rotation_deg=0.0,
                index=start + i,
                offset_x_mm=0.0,
                offset_y_mm=0.0,
            )
        )
    return cell_glyphs, char_pitch_mm


def typeset_vertical(
    text: str,
    region_x_mm: float,
    region_y_mm: float,
    region_width_mm: float,
    region_height_mm: float,
    font_size_pt: float = 9.0,
    line_height: float = 1.4,
    letter_spacing: float = 0.0,
    ruby_line_height: float | None = None,
    ruby_spans=None,
    font_size_pt_for_index=None,
    tatechuyoko_ranges: Sequence[tuple[int, int]] | None = None,
) -> TypesetResult:
    """縦書きで文字を配置.

    - 右→左の行進行
    - 文字は上→下
    - 句読点・括弧の簡易約物処理 (将来拡張)
    - 禁則処理 (行頭/行末) は簡易版
    - ``tatechuyoko_ranges``: 縦中横 (半角文字を横並びの 1 文字セルへ圧縮)
      で処理する (start, length) の一覧。テキストインデックス基準。
    """
    placements: list[GlyphPlacement] = []
    base_em_mm = _mm_per_em_at(font_size_pt)
    ruby_line_height = float(ruby_line_height if ruby_line_height is not None else line_height)
    ruby_flags = _logical_line_ruby_flags(text, _ruby_parent_indices(ruby_spans))
    tatechuyoko_starts = _normalize_tatechuyoko_starts(text, tatechuyoko_ranges)

    # 右上から始まる: 1 行目 = 右端列
    col_offset_mm = 0.0
    logical_line_index = 0
    y_cursor = region_y_mm + region_height_mm
    overflow = False

    def advance_column(*, new_logical_line: bool) -> None:
        nonlocal col_offset_mm, logical_line_index, y_cursor
        if new_logical_line:
            logical_line_index += 1
        col_offset_mm += _line_advance_mm(
            base_em_mm,
            line_height,
            ruby_line_height,
            _line_flag(ruby_flags, logical_line_index),
        )
        y_cursor = region_y_mm + region_height_mm

    after_explicit_break = False
    text_len = len(text)
    text_index = 0
    while text_index < text_len:
        ch = text[text_index]
        if ch == "\n":
            advance_column(new_logical_line=True)
            after_explicit_break = True
            text_index += 1
            continue

        cell_length = tatechuyoko_starts.get(text_index)
        if cell_length is not None:
            cell = _place_tatechuyoko_cell(
                text,
                text_index,
                cell_length,
                region_x_mm,
                region_y_mm,
                region_width_mm,
                col_offset_mm,
                y_cursor,
                letter_spacing,
                font_size_pt,
                font_size_pt_for_index,
            )
            if cell is None:
                # 現在の列に収まらない → 範囲全体を次の列の先頭へ折り返す
                advance_column(new_logical_line=False)
                cell = _place_tatechuyoko_cell(
                    text,
                    text_index,
                    cell_length,
                    region_x_mm,
                    region_y_mm,
                    region_width_mm,
                    col_offset_mm,
                    y_cursor,
                    letter_spacing,
                    font_size_pt,
                    font_size_pt_for_index,
                )
                if cell is None:
                    overflow = True
                    break
            cell_glyphs, consumed_pitch_mm = cell
            placements.extend(cell_glyphs)
            y_cursor -= consumed_pitch_mm
            after_explicit_break = False
            text_index += cell_length
            continue

        glyph_size_pt = (
            float(font_size_pt_for_index(text_index))
            if font_size_pt_for_index is not None
            else font_size_pt
        )
        em_mm = _mm_per_em_at(glyph_size_pt)
        char_pitch_mm = em_mm * (1.0 + letter_spacing)
        # 縦書きの約物処理: 回転 (括弧・ダッシュ類) と字面ずらし (句読点・小書き仮名)
        rot = -90.0 if metrics.needs_vertical_rotation(ch) else 0.0
        offset_x_em, offset_y_em = metrics.vertical_draw_offset_em(ch)
        offset_x_mm = offset_x_em * em_mm
        offset_y_mm = offset_y_em * em_mm
        # 現在の列 X 座標 (右端から左へ) — 文字セルの左端
        x = region_x_mm + region_width_mm - em_mm - col_offset_mm
        # 現在の行 Y 座標 (上端から下へ)
        y = y_cursor - em_mm

        if x < region_x_mm:
            overflow = True
            break
        if y < region_y_mm:
            # 禁則処理: 自動折返しで行頭に禁則文字が来る場合、列は進めずに
            # 前の行末へぶら下げる (簡易版)。列を進めてからぶら下げると、
            # 直後の明示改行 (\n) でさらに列が進んで空列ができ、後続の行が
            # 領域外へ消える。明示改行直後の行頭 (作者がその文字で行を始めた
            # 場合) はぶら下げ対象にしない。
            if not after_explicit_break and metrics.is_kinsoku_start(ch) and placements:
                prev = placements[-1]
                placements.append(
                    GlyphPlacement(
                        ch=ch,
                        x_mm=prev.x_mm,
                        y_mm=prev.y_mm - char_pitch_mm,
                        size_pt=glyph_size_pt,
                        rotation_deg=rot,
                        index=text_index,
                        offset_x_mm=offset_x_mm,
                        offset_y_mm=offset_y_mm,
                    )
                )
                # y_cursor はそのまま (次の文字が改めて折返しを判定する)
                text_index += 1
                continue
            advance_column(new_logical_line=False)
            x = region_x_mm + region_width_mm - em_mm - col_offset_mm
            y = y_cursor - em_mm
            if x < region_x_mm:
                overflow = True
                break

        placements.append(
            GlyphPlacement(
                ch=ch,
                x_mm=x,
                y_mm=y,
                size_pt=glyph_size_pt,
                rotation_deg=rot,
                index=text_index,
                offset_x_mm=offset_x_mm,
                offset_y_mm=offset_y_mm,
            )
        )
        y_cursor -= char_pitch_mm
        after_explicit_break = False
        text_index += 1

    return TypesetResult(placements=placements, overflow=overflow)


def typeset_horizontal(
    text: str,
    region_x_mm: float,
    region_y_mm: float,
    region_width_mm: float,
    region_height_mm: float,
    font_size_pt: float = 9.0,
    line_height: float = 1.4,
    letter_spacing: float = 0.0,
    ruby_line_height: float | None = None,
    ruby_spans=None,
    font_size_pt_for_index=None,
) -> TypesetResult:
    """横書きで文字を配置 (左→右、上→下)."""
    placements: list[GlyphPlacement] = []
    base_em_mm = _mm_per_em_at(font_size_pt)
    ruby_line_height = float(ruby_line_height if ruby_line_height is not None else line_height)
    ruby_flags = _logical_line_ruby_flags(text, _ruby_parent_indices(ruby_spans))

    row_offset_mm = 0.0
    logical_line_index = 0
    x_cursor = region_x_mm
    overflow = False

    def advance_row(*, new_logical_line: bool) -> None:
        nonlocal row_offset_mm, logical_line_index, x_cursor
        if new_logical_line:
            logical_line_index += 1
        row_offset_mm += _line_advance_mm(
            base_em_mm,
            line_height,
            ruby_line_height,
            _line_flag(ruby_flags, logical_line_index),
        )
        x_cursor = region_x_mm

    for text_index, ch in enumerate(text):
        if ch == "\n":
            advance_row(new_logical_line=True)
            continue
        glyph_size_pt = (
            float(font_size_pt_for_index(text_index))
            if font_size_pt_for_index is not None
            else font_size_pt
        )
        em_mm = _mm_per_em_at(glyph_size_pt)
        char_pitch_mm = em_mm * (1.0 + letter_spacing)
        x = x_cursor
        y = region_y_mm + region_height_mm - em_mm - row_offset_mm
        if y < region_y_mm:
            overflow = True
            break
        if x + char_pitch_mm > region_x_mm + region_width_mm:
            advance_row(new_logical_line=False)
            x = x_cursor
            y = region_y_mm + region_height_mm - em_mm - row_offset_mm
            if y < region_y_mm:
                overflow = True
                break
        placements.append(
            GlyphPlacement(
                ch=ch,
                x_mm=x,
                y_mm=y,
                size_pt=glyph_size_pt,
                rotation_deg=0.0,
                index=text_index,
            )
        )
        x_cursor += char_pitch_mm
    return TypesetResult(placements=placements, overflow=overflow)


def _manual_tatechuyoko_ranges(text_entry) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for span in getattr(text_entry, "tatechuyoko_ranges", None) or ():
        try:
            start = int(getattr(span, "start", 0))
            length = max(1, int(getattr(span, "length", 1)))
        except Exception:  # noqa: BLE001
            continue
        ranges.append((start, length))
    return ranges


def _ranges_overlap(a_start: int, a_length: int, b_start: int, b_length: int) -> bool:
    return a_start < b_start + b_length and b_start < a_start + a_length


def tatechuyoko_ranges_for_entry(text_entry, body: str) -> list[tuple[int, int]]:
    """テキストエントリから縦中横の最終範囲一覧 (手動 + 自動) を組み立てる.

    手動範囲 (``entry.tatechuyoko_ranges``) を優先し、自動検出
    (``entry.tatechuyoko_auto``) の範囲が手動範囲と重なる場合は自動側を
    捨てる。横書きでは呼び出し側 (``typeset``) がそもそも呼ばない。
    """
    manual = _manual_tatechuyoko_ranges(text_entry)
    ranges = list(manual)
    if bool(getattr(text_entry, "tatechuyoko_auto", True)):
        for start, length in metrics.auto_tatechuyoko_ranges(body or ""):
            if any(_ranges_overlap(start, length, m_start, m_length) for m_start, m_length in manual):
                continue
            ranges.append((start, length))
    return ranges


def typeset(
    text_entry,
    region_x_mm: float,
    region_y_mm: float,
    region_width_mm: float,
    region_height_mm: float,
) -> TypesetResult:
    """PropertyGroup TextEntry からレイアウトを実行."""
    font_size_pt = float(
        q_to_pt(float(getattr(text_entry, "font_size_q", 20.0)))
        if hasattr(text_entry, "font_size_q")
        else getattr(text_entry, "font_size_pt", 9.0)
    )
    if text_entry.writing_mode == "horizontal":
        return typeset_horizontal(
            text_entry.body,
            region_x_mm,
            region_y_mm,
            region_width_mm,
            region_height_mm,
            font_size_pt=font_size_pt,
            line_height=text_entry.line_height,
            letter_spacing=text_entry.letter_spacing,
            ruby_line_height=getattr(text_entry, "ruby_line_height", text_entry.line_height),
            ruby_spans=getattr(text_entry, "ruby_spans", []) or [],
            font_size_pt_for_index=lambda index: q_to_pt(text_style.font_size_q_for_index(text_entry, index)),
        )
    return typeset_vertical(
        text_entry.body,
        region_x_mm,
        region_y_mm,
        region_width_mm,
        region_height_mm,
        font_size_pt=font_size_pt,
        line_height=text_entry.line_height,
        letter_spacing=text_entry.letter_spacing,
        ruby_line_height=getattr(text_entry, "ruby_line_height", text_entry.line_height),
        ruby_spans=getattr(text_entry, "ruby_spans", []) or [],
        font_size_pt_for_index=lambda index: q_to_pt(text_style.font_size_q_for_index(text_entry, index)),
        tatechuyoko_ranges=tatechuyoko_ranges_for_entry(text_entry, text_entry.body),
    )
