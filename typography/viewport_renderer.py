"""ビューポート用テキストレンダリング (blf + gpu).

計画書 3.1.5 の「ビューポート表示」層。draw_handler から呼ばれ、
layout.py の計算結果を使って blf で文字を描画する。

Blender 4.0+ の blf API: ``blf.size(fontid, size)`` (dpi 引数は廃止済み)。
"""

from __future__ import annotations

import math

import blf

from ..utils import log
from ..utils.geom import mm_to_pt, pt_to_mm
from .layout import TypesetResult

_logger = log.get_logger(__name__)


def render_placements(
    result: TypesetResult,
    *,
    font_id: int = 0,
    color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    view_to_screen_px_per_mm: float = 1.0,
    origin_screen_xy: tuple[float, float] = (0.0, 0.0),
) -> None:
    """組版結果をスクリーン座標へ変換して blf で描画.

    viewport_renderer は POST_PIXEL で呼ばれることを前提とする
    (3D 座標で文字を描画すると視点依存でサイズが変わるため)。
    ``view_to_screen_px_per_mm`` は現在のビューの mm→pixel 換算係数。
    呼出側は region.width / world_span_mm から計算して渡す。
    """
    blf.color(font_id, color[0], color[1], color[2], color[3])
    rotated = False
    for g in result.placements:
        size_px = g.size_pt * view_to_screen_px_per_mm * 25.4 / 72.0
        if size_px <= 0:
            continue
        blf.size(font_id, max(1, int(size_px)))
        screen_x = origin_screen_xy[0] + (g.x_mm + g.offset_x_mm) * view_to_screen_px_per_mm
        screen_y = origin_screen_xy[1] + (g.y_mm + g.offset_y_mm) * view_to_screen_px_per_mm
        if g.rotation_deg != 0.0:
            blf.enable(font_id, blf.ROTATION)
            theta = math.radians(g.rotation_deg)
            blf.rotation(font_id, theta)
            # blf の回転はペン位置 (ベースライン左端) が軸。全角ボディ中心が
            # セル中心と一致するペン位置を逆算する (ui/overlay.py と同式)。
            half_em = size_px * 0.5
            m_x = half_em
            m_y = size_px * 0.38
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            blf.position(
                font_id,
                screen_x + half_em - (m_x * cos_t - m_y * sin_t),
                screen_y + half_em - (m_x * sin_t + m_y * cos_t),
                0.0,
            )
            rotated = True
        else:
            if rotated:
                blf.disable(font_id, blf.ROTATION)
                rotated = False
            blf.position(font_id, screen_x, screen_y, 0.0)
        blf.draw(font_id, g.ch)
    if rotated:
        blf.disable(font_id, blf.ROTATION)
