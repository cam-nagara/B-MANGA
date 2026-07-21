"""blf 描画パラメータの安全ガード.

ビュー依存の文字サイズ計算 (ページ上の mm 寸法→スクリーン px 換算) は、
透視投影がページ平面すれすれのグレージング角になると発散し、数十万 px
単位の巨大値になることがある。その値をそのまま ``blf.size()`` →
``blf.draw()`` へ渡すと、Blender 本体のグリフラスタライズ
(``blf_glyph_draw``) がグリフ画像の確保でバッファ外へ書き込み、
EXCEPTION_ACCESS_VIOLATION で Blender ごとクラッシュする
(2026-07-21 実発生、CHANGELOG v0.6.568 参照)。

ビュー依存で計算した文字サイズを blf.size へ渡す前に、必ず
``safe_text_px_size()`` を通すこと。固定定数サイズ (ページ識別番号の
34px 等) はガード不要。
"""

from __future__ import annotations

import math

# これを超える文字は 4K 画面でも縦を覆い尽くし視認目的で意味がなく、
# blf のグリフ確保も危険域に入る。超過は投影の発散とみなし、クランプ
# 描画 (壊れた見た目が残る) ではなく描画スキップにする。
MAX_TEXT_PX_SIZE = 1024.0


def safe_text_px_size(px_size) -> float | None:
    """blf.size に渡してよいサイズなら float を、skip すべきなら None を返す."""
    try:
        value = float(px_size)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0.0:
        return None
    if value > MAX_TEXT_PX_SIZE:
        return None
    return value


def finite_xy(x, y) -> bool:
    """スクリーン座標が有限か (inf/nan を blf.position へ渡さないため)."""
    try:
        return math.isfinite(float(x)) and math.isfinite(float(y))
    except (TypeError, ValueError):
        return False
