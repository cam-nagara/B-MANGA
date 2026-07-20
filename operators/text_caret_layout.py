"""キャレット・選択矩形を実レイアウト (typography.layout.typeset) から導出する.

テキスト描画 (ui/overlay.py・utils/text_real_object.py・書き出し) と同じ
typeset の配置結果を基準にすることで、縦中横・行頭禁則ぶら下げ・文字サイズ
混在があってもキャレットと選択ハイライトが描画グリフと一致する。
旧実装 (基本文字サイズの等幅前提で文字数を数える方式) は、縦中横 1 セルに
複数文字が入ると以降のキャレットが文字数ぶん送り方向へズレていた。
"""

from __future__ import annotations

import math
from bisect import bisect_left

# 縦中横の正規化・ルビ行フラグは typeset 本体と同じ規則を使う必要があるため、
# typography.layout のモジュール私有関数をそのまま共有する (別実装を持つと
# 将来の組版仕様変更でキャレットだけまたズレるため)。
from ..typography import layout as typography_layout
from ..utils import text_layout_bounds, text_style
from ..utils.geom import Rect, q_to_mm

CARET_MIN_THICKNESS_MM = 0.18
_PT_TO_MM = 25.4 / 72.0


def _em_from_pt(size_pt: float) -> float:
    return max(0.25, float(size_pt) * _PT_TO_MM)


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(float(value), low), high)


def _thickness(em: float) -> float:
    return max(CARET_MIN_THICKNESS_MM, em * 0.08)


def _entry_layout_params(entry) -> tuple[float, float, float, float]:
    """entry から (基本em mm, 字送り倍率, 行間, ルビ行間) を安全に読む."""
    try:
        base_em = max(0.25, q_to_mm(float(getattr(entry, "font_size_q", 20.0))))
    except Exception:  # noqa: BLE001
        base_em = 5.0
    try:
        char_scale = 1.0 + float(getattr(entry, "letter_spacing", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        char_scale = 1.0
    try:
        line_height = max(0.1, float(getattr(entry, "line_height", 1.4) or 1.4))
    except Exception:  # noqa: BLE001
        line_height = 1.4
    try:
        ruby_line_height = max(
            0.1, float(getattr(entry, "ruby_line_height", line_height) or line_height)
        )
    except Exception:  # noqa: BLE001
        ruby_line_height = line_height
    return base_em, char_scale, line_height, ruby_line_height


def _build_group_map(entry, body: str, horizontal: bool) -> dict[int, tuple[int, int]]:
    """縦中横の文字インデックス → (範囲開始, 文字数) の対応表."""
    if horizontal:
        return {}
    try:
        starts = typography_layout._normalize_tatechuyoko_starts(
            body, typography_layout.tatechuyoko_ranges_for_entry(entry, body)
        )
    except Exception:  # noqa: BLE001
        return {}
    groups: dict[int, tuple[int, int]] = {}
    for start, length in starts.items():
        for index in range(start, start + length):
            groups[index] = (start, length)
    return groups


class _CaretLayout:
    """1 回の typeset 結果と、キャレット計算に使う組版パラメータの束."""

    def __init__(self, entry, rect: Rect) -> None:
        self.entry = entry
        self.region = text_layout_bounds.text_inner_rect(rect)
        self.body = str(getattr(entry, "body", "") or "")
        self.horizontal = getattr(entry, "writing_mode", "vertical") == "horizontal"
        (
            self.base_em,
            self.char_scale,
            self.line_height,
            self.ruby_line_height,
        ) = _entry_layout_params(entry)
        try:
            placements = typography_layout.typeset(
                entry, self.region.x, self.region.y, self.region.width, self.region.height
            ).placements
        except Exception:  # noqa: BLE001
            placements = []
        self.by_index: dict[int, object] = {}
        for placement in placements:
            self.by_index.setdefault(int(placement.index), placement)
        self.placed_indices = sorted(self.by_index)
        self.groups = _build_group_map(entry, self.body, self.horizontal)
        self.ruby_flags = typography_layout._logical_line_ruby_flags(
            self.body,
            typography_layout._ruby_parent_indices(getattr(entry, "ruby_spans", []) or []),
        )

    def glyph_em(self, index: int) -> float:
        try:
            return max(0.25, q_to_mm(float(text_style.font_size_q_for_index(self.entry, int(index)))))
        except Exception:  # noqa: BLE001
            return self.base_em

    def line_advance(self, logical_line_index: int) -> float:
        """明示改行 1 回ぶんの行送り (typeset の advance_column/row と同じ規則)."""
        has_ruby = (
            bool(self.ruby_flags[logical_line_index])
            if 0 <= logical_line_index < len(self.ruby_flags)
            else False
        )
        return self.base_em * (self.ruby_line_height if has_ruby else self.line_height)

    def prev_placed(self, index: int) -> int | None:
        pos = bisect_left(self.placed_indices, int(index))
        return self.placed_indices[pos - 1] if pos > 0 else None


def _vertical_cell(layout: _CaretLayout, index: int) -> tuple[float, float, float]:
    """縦書きセルの (中心x, セル上端y, セルem)。縦中横グループは 1 セル扱い."""
    placement = layout.by_index[index]
    group = layout.groups.get(index)
    if group is None:
        em = _em_from_pt(placement.size_pt)
        return placement.x_mm + em * 0.5, placement.y_mm + em, em
    start, count = group
    first = layout.by_index.get(start, placement)
    base_em = layout.glyph_em(start)
    scale = min(1.0, 2.0 / count) if count > 0 else 1.0
    # _place_tatechuyoko_cell と同じ余白計算でセル左端を復元する。
    margin = max(0.0, 1.0 - 0.5 * count * scale) * 0.5 * base_em
    return first.x_mm - margin + base_em * 0.5, first.y_mm + base_em, base_em


def _vertical_bar(region: Rect, center_x: float, top_y: float, cell_em: float) -> Rect:
    thickness = _thickness(cell_em)
    half = min(cell_em * 0.45, max(0.6, region.width * 0.5))
    x = _clamp(center_x, region.x, region.x2) - half
    y = _clamp(top_y, region.y, region.y2) - thickness * 0.5
    return Rect(x, y, half * 2.0, thickness)


def _horizontal_bar(region: Rect, x: float, top_y: float, em: float) -> Rect:
    thickness = _thickness(em)
    bar_x = _clamp(x, region.x, region.x2) - thickness * 0.5
    y = _clamp(top_y - em, region.y, max(region.y, region.y2 - em))
    return Rect(bar_x, y, thickness, min(em, region.height))


def _caret_rect_vertical(layout: _CaretLayout, index: int) -> Rect | None:
    region = layout.region
    placement = layout.by_index.get(index)
    if placement is not None:
        group = layout.groups.get(index)
        if group is not None and index != group[0]:
            # 縦中横セル内 (桁の間): 横書き相当の縦バーを桁の左端に立てる。
            member_em = _em_from_pt(placement.size_pt)
            thickness = _thickness(member_em)
            x = _clamp(placement.x_mm, region.x, region.x2) - thickness * 0.5
            y = _clamp(placement.y_mm, region.y, max(region.y, region.y2 - member_em))
            return Rect(x, y, thickness, member_em)
        center_x, top_y, cell_em = _vertical_cell(layout, index)
        return _vertical_bar(region, center_x, top_y, cell_em)
    prev_index = layout.prev_placed(index)
    if prev_index is None:
        center_x, top_y, cell_em = region.x2 - layout.base_em * 0.5, region.y2, layout.base_em
        newline_from = 0
    else:
        center_x, top_y, cell_em = _vertical_cell(layout, prev_index)
        top_y -= cell_em * layout.char_scale
        newline_from = layout.body.count("\n", 0, prev_index + 1)
    newline_to = layout.body.count("\n", 0, index)
    if newline_to > newline_from:
        for line_index in range(newline_from + 1, newline_to + 1):
            center_x -= layout.line_advance(line_index)
        top_y = region.y2
        cell_em = layout.base_em
    if center_x < region.x - layout.base_em:
        return None
    return _vertical_bar(region, center_x, top_y, cell_em)


def _caret_rect_horizontal(layout: _CaretLayout, index: int) -> Rect | None:
    region = layout.region
    placement = layout.by_index.get(index)
    if placement is not None:
        em = _em_from_pt(placement.size_pt)
        return _horizontal_bar(region, placement.x_mm, placement.y_mm + em, em)
    prev_index = layout.prev_placed(index)
    if prev_index is None:
        x, top_y, em = region.x, region.y2, layout.base_em
        newline_from = 0
    else:
        prev = layout.by_index[prev_index]
        em = _em_from_pt(prev.size_pt)
        x = prev.x_mm + em * layout.char_scale
        top_y = prev.y_mm + em
        newline_from = layout.body.count("\n", 0, prev_index + 1)
    newline_to = layout.body.count("\n", 0, index)
    if newline_to > newline_from:
        for line_index in range(newline_from + 1, newline_to + 1):
            top_y -= layout.line_advance(line_index)
        x = region.x
        em = layout.base_em
    if top_y < region.y:
        return None
    return _horizontal_bar(region, x, top_y, em)


def _caret_rect_in_layout(layout: _CaretLayout, cursor_index: int) -> Rect | None:
    index = max(0, min(len(layout.body), int(cursor_index)))
    if layout.horizontal:
        return _caret_rect_horizontal(layout, index)
    return _caret_rect_vertical(layout, index)


def caret_rect(entry, rect: Rect, cursor_index: int) -> Rect | None:
    """描画と同じ typeset 結果からキャレット矩形 (ページローカル mm) を返す."""
    return _caret_rect_in_layout(_CaretLayout(entry, rect), cursor_index)


def cursor_index_from_point(entry, rect: Rect, x_mm: float, y_mm: float) -> int:
    """クリック座標に最も近いキャレット位置 (本文インデックス) を返す."""
    layout = _CaretLayout(entry, rect)
    best_index = 0
    best_distance = math.inf
    for index in range(len(layout.body) + 1):
        caret = _caret_rect_in_layout(layout, index)
        if caret is None:
            continue
        cx, cy = caret.center
        distance = math.hypot(float(x_mm) - cx, float(y_mm) - cy)
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _selection_rect_for(layout: _CaretLayout, index: int, placement) -> Rect:
    region = layout.region
    if layout.horizontal:
        em = _em_from_pt(placement.size_pt)
        return Rect(placement.x_mm, placement.y_mm, em * layout.char_scale, min(em, region.height))
    group = layout.groups.get(index)
    if group is not None:
        # 縦中横セル内は桁単位の小さな矩形 (桁送りは 0.5em × 縮小率)。
        start, count = group
        base_em = layout.glyph_em(start)
        scale = min(1.0, 2.0 / count) if count > 0 else 1.0
        return Rect(placement.x_mm, placement.y_mm, max(0.1, 0.5 * scale * base_em), base_em)
    center_x, top_y, cell_em = _vertical_cell(layout, index)
    half = min(cell_em * 0.45, max(0.6, region.width * 0.5))
    x = _clamp(center_x, region.x, region.x2) - half
    pitch = cell_em * layout.char_scale
    return Rect(x, top_y - pitch, half * 2.0, pitch)


def selection_rects(entry, rect: Rect, start: int, end: int) -> list[Rect]:
    """選択範囲 [start, end) の文字ごとのハイライト矩形を返す."""
    layout = _CaretLayout(entry, rect)
    rects: list[Rect] = []
    end = min(int(end), len(layout.body))
    for index in range(max(0, int(start)), end):
        if layout.body[index] == "\n":
            continue
        placement = layout.by_index.get(index)
        if placement is None:
            continue
        rects.append(_selection_rect_for(layout, index, placement))
    return rects
