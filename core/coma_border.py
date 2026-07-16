"""コマ枠線・フチの PropertyGroup."""

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

_WHITE_MARGIN_PLACEMENT_ITEMS = (
    ("outside", "外側", ""),
    ("inside", "内側", ""),
    ("both", "両側", ""),
)


def _on_border_changed(self, _context) -> None:
    try:
        from ..utils import coma_border_object

        coma_border_object.on_coma_border_changed(self)
    except Exception:  # noqa: BLE001
        pass


class BMangaComaBorder(bpy.types.PropertyGroup):
    """コマ枠線スタイル."""

    style: EnumProperty(  # type: ignore[valid-type]
        name="線種",
        description="コマ枠線の線種 (実線・破線・点線・二重線・輪郭ぼかし)",
        items=_LINE_STYLE_ITEMS,
        default="solid",
        update=_on_border_changed,
    )
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="線幅 (mm)",
        description="コマ枠線の太さ",
        default=0.5,
        min=0.0,
        soft_max=10.0,
        update=_on_border_changed,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="線色",
        description="コマ枠線の色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_border_changed,
    )
    corner_type: EnumProperty(  # type: ignore[valid-type]
        name="角処理",
        description="コマ枠の角の処理方法 (直角・丸角・面取り)",
        items=_CORNER_ITEMS,
        default="square",
        update=_on_border_changed,
    )
    corner_radius_mm: FloatProperty(  # type: ignore[valid-type]
        name="角半径 (mm)",
        description="丸角・面取り時の角の半径",
        default=0.0,
        min=0.0,
        soft_max=20.0,
        update=_on_border_changed,
    )
    blur_amount: FloatProperty(  # type: ignore[valid-type]
        name="ボカシ量",
        description="輪郭ぼかし線種のときの輪郭のボケ具合。ぼかしカーブは表示更新後に編集できます",
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
        description="コマ枠線を表示する",
        default=True,
        update=_on_border_changed,
    )
    preset_name: StringProperty(  # type: ignore[valid-type]
        name="適用中プリセット",
        description="最後に適用した枠線プリセット名 (セレクタ表示の追従用)",
        default="",
        options={"HIDDEN"},
    )


class BMangaComaWhiteMargin(bpy.types.PropertyGroup):
    """コマ枠のフチ."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="フチ",
        description="コマ枠の外側または内側にフチ (縁取り) を表示する",
        default=True,
        update=_on_border_changed,
    )
    placement: EnumProperty(  # type: ignore[valid-type]
        name="位置",
        description="フチを配置する位置 (外側・内側・両側)",
        items=_WHITE_MARGIN_PLACEMENT_ITEMS,
        default="outside",
        update=_on_border_changed,
    )
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅 (mm)",
        description="フチの幅",
        default=0.5,
        min=0.0,
        soft_max=5.0,
        update=_on_border_changed,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        description="外側色・内側色が未設定の場合に使うフォールバック色 (通常は使用されません)",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_border_changed,
    )
    outer_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="外側色",
        description="フチの外側部分の色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_border_changed,
    )
    inner_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="内側色",
        description="フチの内側部分の色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_border_changed,
    )


_CLASSES = (
    BMangaComaBorder,
    BMangaComaWhiteMargin,
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
