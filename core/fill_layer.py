"""フィルレイヤー (ベタ塗り / グラデーション) の PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)

FILL_TYPE_ITEMS = (
    ("solid", "ベタ塗り", ""),
    ("gradient", "グラデーション", ""),
)

GRADIENT_TYPE_ITEMS = (
    ("linear", "線形", ""),
    ("radial", "円形", ""),
)


def _on_fill_layer_changed(_self, context) -> None:
    try:
        from ..utils import fill_real_object

        fill_real_object.on_fill_entry_changed(_self)
    except Exception:  # noqa: BLE001
        pass
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _on_fill_title_changed(_self, context) -> None:
    _on_fill_layer_changed(_self, context)
    if not str(getattr(_self, "id", "") or "").strip():
        return
    try:
        from ..utils import layer_stack as layer_stack_utils

        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        pass


class BNameFillLayer(bpy.types.PropertyGroup):
    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", default="", update=_on_fill_title_changed)  # type: ignore[valid-type]

    fill_type: EnumProperty(  # type: ignore[valid-type]
        name="塗りタイプ",
        items=FILL_TYPE_ITEMS,
        default="solid",
        update=_on_fill_layer_changed,
    )

    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_fill_layer_changed,
    )
    color2: FloatVectorProperty(  # type: ignore[valid-type]
        name="色2",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_fill_layer_changed,
    )

    gradient_type: EnumProperty(  # type: ignore[valid-type]
        name="グラデーション種別",
        items=GRADIENT_TYPE_ITEMS,
        default="linear",
        update=_on_fill_layer_changed,
    )
    gradient_angle: FloatProperty(  # type: ignore[valid-type]
        name="角度",
        default=0.0,
        soft_min=-180.0,
        soft_max=180.0,
        subtype="ANGLE",
        update=_on_fill_layer_changed,
    )

    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_fill_layer_changed,
    )
    visible: BoolProperty(name="表示", default=True, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    selected: BoolProperty(name="マルチ選択", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]
    locked: BoolProperty(name="ロック", default=False)  # type: ignore[valid-type]

    parent_kind: StringProperty(name="親種別", default="page", update=_on_fill_layer_changed)  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="", update=_on_fill_layer_changed)  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", default="", update=_on_fill_layer_changed)  # type: ignore[valid-type]

    use_region: BoolProperty(name="領域指定", default=False, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    region_x_mm: FloatProperty(name="X (mm)", default=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    region_y_mm: FloatProperty(name="Y (mm)", default=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    region_width_mm: FloatProperty(name="幅 (mm)", default=0.0, min=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    region_height_mm: FloatProperty(name="高さ (mm)", default=0.0, min=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]

    lasso_points_json: StringProperty(name="投げ縄頂点", default="", update=_on_fill_layer_changed)  # type: ignore[valid-type]

    use_gradient_endpoints: BoolProperty(name="端点指定", default=False, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    gradient_start_x_mm: FloatProperty(name="開始X", default=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    gradient_start_y_mm: FloatProperty(name="開始Y", default=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    gradient_end_x_mm: FloatProperty(name="終了X", default=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]
    gradient_end_y_mm: FloatProperty(name="終了Y", default=0.0, update=_on_fill_layer_changed)  # type: ignore[valid-type]


_CLASSES = (BNameFillLayer,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_fill_layers = bpy.props.CollectionProperty(type=BNameFillLayer)
    bpy.types.Scene.bname_active_fill_layer_index = bpy.props.IntProperty(default=-1, min=-1)
    _logger.debug("fill_layer registered")


def unregister() -> None:
    for attr in (
        "bname_active_fill_layer_index",
        "bname_fill_layers",
    ):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
