"""縦組みで正立する細い記号の字面中央補正."""

from __future__ import annotations

from functools import lru_cache
from typing import Any


# 通常の仮名・漢字は字形固有の左右差を保つ。全角セル内で正立する細い記号だけ、
# フォントの実際の ink bbox を使って光学中心をセル中央へ合わせる。
OPTICAL_CENTER_CHARS = frozenset("・！‼⁉？⁇⁈")


def optical_center_offset_px(ch: str, font: Any, size_px: float) -> float:
    """Pillow フォントの字面中心を全角セル中心へ合わせる X 補正量."""
    if ch not in OPTICAL_CENTER_CHARS or font is None or size_px <= 0.0:
        return 0.0
    try:
        left, _top, right, _bottom = font.getbbox(ch)
        ink_width = float(right) - float(left)
        if ink_width <= 0.0:
            return 0.0
        return float(size_px) * 0.5 - (float(left) + float(right)) * 0.5
    except Exception:  # noqa: BLE001 - 代替ビットマップフォント等は補正なし
        return 0.0


@lru_cache(maxsize=128)
def _pillow_font(font_path: str, size_px: int):
    try:
        from PIL import ImageFont  # type: ignore

        return ImageFont.truetype(font_path, size_px)
    except Exception:  # noqa: BLE001 - Pillow未導入・無効パスは補正なし
        return None


def optical_center_offset_for_path_px(ch: str, font_path: str, size_px: float) -> float:
    """フォントパスから字面中央補正を得る（BLFオーバーレイ用）."""
    draw_size = max(1, int(size_px))
    return optical_center_offset_px(ch, _pillow_font(str(font_path or ""), draw_size), draw_size)
