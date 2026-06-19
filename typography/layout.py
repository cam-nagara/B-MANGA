"""縦書き・横書きレイアウトエンジン.

計画書 3.1.5 参照。テキスト文字列と矩形領域 (mm) を受け取り、各文字の
配置座標 (mm) と行分割情報を計算する。描画はしない。

ビューポート用 (blf) / 書き出し用 (Pillow) で共通で呼ばれるよう、純粋
データ返却のみに徹する。
"""

from __future__ import annotations

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
) -> TypesetResult:
    """縦書きで文字を配置.

    - 右→左の行進行
    - 文字は上→下
    - 句読点・括弧の簡易約物処理 (将来拡張)
    - 禁則処理 (行頭/行末) は簡易版
    """
    placements: list[GlyphPlacement] = []
    base_em_mm = _mm_per_em_at(font_size_pt)
    ruby_line_height = float(ruby_line_height if ruby_line_height is not None else line_height)
    ruby_flags = _logical_line_ruby_flags(text, _ruby_parent_indices(ruby_spans))

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

    for text_index, ch in enumerate(text):
        if ch == "\n":
            advance_column(new_logical_line=True)
            continue
        glyph_size_pt = (
            float(font_size_pt_for_index(text_index))
            if font_size_pt_for_index is not None
            else font_size_pt
        )
        em_mm = _mm_per_em_at(glyph_size_pt)
        char_pitch_mm = em_mm * (1.0 + letter_spacing)
        # 現在の列 X 座標 (右端から左へ)
        x = region_x_mm + region_width_mm - em_mm / 2.0 - col_offset_mm
        # 現在の行 Y 座標 (上端から下へ)
        y = y_cursor - em_mm

        if x < region_x_mm:
            overflow = True
            break
        if y < region_y_mm:
            advance_column(new_logical_line=False)
            x = region_x_mm + region_width_mm - em_mm / 2.0 - col_offset_mm
            y = y_cursor - em_mm
            if x < region_x_mm:
                overflow = True
                break

        # 禁則処理: 行頭に禁則文字が来たら前の行末にぶら下げて追加 (簡易版)。
        # 新しい行の 1 文字目になるのを避け、前行の最終文字のさらに 1 段下に置く。
        if y_cursor == region_y_mm + region_height_mm and metrics.is_kinsoku_start(ch) and placements:
            prev = placements[-1]
            placements.append(
                GlyphPlacement(
                    ch=ch,
                    x_mm=prev.x_mm,
                    y_mm=prev.y_mm - char_pitch_mm,
                    size_pt=glyph_size_pt,
                    rotation_deg=0.0,
                    index=text_index,
                )
            )
            # y_cursor はそのまま (次の文字も新行の先頭扱い)
            continue

        rot = -90.0 if metrics.needs_vertical_rotation(ch) else 0.0
        placements.append(
            GlyphPlacement(
                ch=ch,
                x_mm=x,
                y_mm=y,
                size_pt=glyph_size_pt,
                rotation_deg=rot,
                index=text_index,
            )
        )
        y_cursor -= char_pitch_mm

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
    )
