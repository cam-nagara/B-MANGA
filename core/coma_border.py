"""コマ枠線・白フチの PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, StringProperty

from ..utils import log

_logger = log.get_logger(__name__)


_LINE_STYLE_ITEMS = (
    ("solid", "実線", ""),
    ("dashed", "破線", ""),
    ("dotted", "点線", ""),
    ("double", "二重線", ""),
    ("brush", "輪郭ぼかし", "輪郭をぼかした枠線"),
)

_CORNER_ITEMS = (
    ("square", "直角", ""),
    ("rounded", "丸角", ""),
    ("bevel", "面取り", ""),
)


def _on_border_changed(self, _context) -> None:
    try:
        from ..utils import coma_border_object

        coma_border_object.on_coma_border_changed(self)
    except Exception:  # noqa: BLE001
        pass


class BNameComaBorder(bpy.types.PropertyGroup):
    """コマ枠線スタイル."""

    style: EnumProperty(  # type: ignore[valid-type]
        name="線種",
        items=_LINE_STYLE_ITEMS,
        default="solid",
        update=_on_border_changed,
    )
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="線幅 (mm)",
        default=0.5,
        min=0.0,
        soft_max=10.0,
        update=_on_border_changed,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="線色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_border_changed,
    )
    corner_type: EnumProperty(  # type: ignore[valid-type]
        name="角処理",
        items=_CORNER_ITEMS,
        default="square",
        update=_on_border_changed,
    )
    corner_radius_mm: FloatProperty(  # type: ignore[valid-type]
        name="角半径 (mm)",
        default=0.0,
        min=0.0,
        soft_max=20.0,
        update=_on_border_changed,
    )
    blur_amount: FloatProperty(  # type: ignore[valid-type]
        name="ボカシ量",
        description="輪郭ぼかし線種のときの輪郭のボケ具合",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_on_border_changed,
    )
    blur_curve_points: StringProperty(  # type: ignore[valid-type]
        name="ぼかしカーブ",
        default="0.0000,0.0000;0.2500,0.0950;0.5000,0.5000;0.7500,0.9050;1.0000,1.0000",
        options={"HIDDEN"},
        update=_on_border_changed,
    )
    blur_dither: BoolProperty(  # type: ignore[valid-type]
        name="ディザ化",
        description="輪郭ぼかしのボケをディザで表現する",
        default=False,
        update=_on_border_changed,
    )
    visible: BoolProperty(  # type: ignore[valid-type]
        name="枠線を表示",
        default=True,
        update=_on_border_changed,
    )
    preset_name: StringProperty(  # type: ignore[valid-type]
        name="適用中プリセット",
        description="最後に適用した枠線プリセット名 (セレクタ表示の追従用)",
        default="",
        options={"HIDDEN"},
    )


class BNameComaWhiteMargin(bpy.types.PropertyGroup):
    """コマの白フチ."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="白フチ",
        default=True,
        update=_on_border_changed,
    )
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅 (mm)",
        default=0.5,
        min=0.0,
        soft_max=5.0,
        update=_on_border_changed,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_border_changed,
    )


_CLASSES = (
    BNameComaBorder,
    BNameComaWhiteMargin,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("coma_border registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
