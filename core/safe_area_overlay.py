"""セーフライン外側オーバーレイの PropertyGroup.

描画 (draw_handler_add + gpu) は ui/overlay.py に実装。
ここではデータモデルと既定値のみ保持する。

仕様:
- 黒固定 + 不透明度スライダで暗さ調整 (既定 0.3)
- ALPHA blend で「黒 30%」相当を描く (Blender GPU 乗算は EEVEE Next で
  期待通り動かないため ALPHA で代替)
- 表示専用 — 書き出しには含めない
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty

from ..utils import log

_logger = log.get_logger(__name__)


class BNameSafeAreaOverlay(bpy.types.PropertyGroup):
    """セーフライン外側を黒で暗くするビューポート専用オーバーレイ."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="セーフライン",
        description="セーフライン外を黒で暗く表示 (書き出しには含まれない)",
        default=True,
    )
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        description="セーフライン外側の暗さ (0=透明, 1=完全黒)",
        default=0.3,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


_CLASSES = (BNameSafeAreaOverlay,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("safe_area_overlay registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
