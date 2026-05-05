"""セーフライン外側オーバーレイの PropertyGroup.

描画 (draw_handler_add + gpu) は ui/overlay.py に実装。
ここではデータモデルと既定値のみ保持する。

仕様:
- セーフライン外の塗りはテキストより下、フキダシ / 効果線 / コマより上に描画する
- GPU の乗算合成が安定しないため、黒固定 + 不透明度で暗くする
- 初期不透明度は 30%
- 表示専用 — 書き出しには含めない
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty

from ..utils import log

_logger = log.get_logger(__name__)

_DEFAULT_OPACITY = 0.30


class BNameSafeAreaOverlay(bpy.types.PropertyGroup):
    """セーフライン外側を黒固定の不透明度で暗くするビューポート専用オーバーレイ."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="セーフライン",
        description="セーフライン外を暗く表示 (書き出しには含まれない)",
        default=True,
    )
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        description="セーフライン外の黒塗り不透明度",
        default=_DEFAULT_OPACITY,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="旧塗り色",
        description="旧バージョン互換用。現在の表示は黒固定 + 不透明度で行う",
        subtype="COLOR",
        size=3,
        default=(0.0, 0.0, 0.0),
        min=0.0,
        max=1.0,
        options={"HIDDEN"},
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
