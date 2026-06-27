"""パターンカーブレイヤーの PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from ..utils import line_effect_schema, log

_logger = log.get_logger(__name__)

IMAGE_PATH_DRAW_MODE_ITEMS = line_effect_schema.PATH_IMAGE_DRAW_MODE_ITEMS
IMAGE_PATH_SOURCE_ITEMS = line_effect_schema.PATH_CONTENT_SOURCE_ITEMS
IMAGE_PATH_SHAPE_ITEMS = line_effect_schema.PATH_GENERATED_SHAPE_ITEMS
IMAGE_PATH_STAMP_ANGLE_MODE_ITEMS = line_effect_schema.PATH_IMAGE_STAMP_ANGLE_MODE_ITEMS
IMAGE_PATH_RIBBON_REPEAT_MODE_ITEMS = line_effect_schema.PATH_IMAGE_RIBBON_REPEAT_MODE_ITEMS


def _on_image_path_changed(_self, context) -> None:
    try:
        from ..utils import image_path_object

        image_path_object.on_image_path_entry_changed(_self)
    except Exception:  # noqa: BLE001
        pass
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _on_image_path_title_changed(_self, context) -> None:
    _on_image_path_changed(_self, context)
    if not str(getattr(_self, "id", "") or "").strip():
        return
    try:
        from ..utils import layer_stack as layer_stack_utils

        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        pass


class BMangaImagePathLayer(bpy.types.PropertyGroup):
    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", default="", update=_on_image_path_title_changed)  # type: ignore[valid-type]
    filepath: StringProperty(  # type: ignore[valid-type]
        name="画像",
        default="",
        subtype="FILE_PATH",
        update=_on_image_path_changed,
    )
    path_points_json: StringProperty(name="パス頂点", default="", update=_on_image_path_changed)  # type: ignore[valid-type]
    content_source: EnumProperty(  # type: ignore[valid-type]
        name="内容",
        items=IMAGE_PATH_SOURCE_ITEMS,
        default="image",
        update=_on_image_path_changed,
    )
    shape_kind: EnumProperty(  # type: ignore[valid-type]
        name="生成形状",
        items=IMAGE_PATH_SHAPE_ITEMS,
        default="circle",
        update=_on_image_path_changed,
    )
    shape_sides: IntProperty(  # type: ignore[valid-type]
        name="角数",
        default=6,
        min=3,
        max=16,
        update=_on_image_path_changed,
    )

    draw_mode: EnumProperty(  # type: ignore[valid-type]
        name="表示方法",
        items=IMAGE_PATH_DRAW_MODE_ITEMS,
        default="stamp",
        update=_on_image_path_changed,
    )
    brush_size_mm: FloatProperty(  # type: ignore[valid-type]
        name="ブラシサイズ",
        default=10.0,
        min=0.1,
        soft_max=100.0,
        unit="LENGTH",
        update=_on_image_path_changed,
    )
    aspect_ratio: FloatProperty(  # type: ignore[valid-type]
        name="縦横比",
        default=1.0,
        min=0.01,
        soft_min=0.1,
        soft_max=10.0,
        update=_on_image_path_changed,
    )
    image_angle_deg: FloatProperty(  # type: ignore[valid-type]
        name="画像の角度",
        default=0.0,
        soft_min=-180.0,
        soft_max=180.0,
        update=_on_image_path_changed,
    )
    spacing_percent: FloatProperty(  # type: ignore[valid-type]
        name="間隔",
        default=100.0,
        min=1.0,
        soft_max=400.0,
        subtype="PERCENTAGE",
        update=_on_image_path_changed,
    )
    stamp_angle_mode: EnumProperty(  # type: ignore[valid-type]
        name="角度",
        items=IMAGE_PATH_STAMP_ANGLE_MODE_ITEMS,
        default="line",
        update=_on_image_path_changed,
    )
    stamp_angle_object_name: StringProperty(  # type: ignore[valid-type]
        name="方向オブジェクト",
        default="",
        update=_on_image_path_changed,
    )
    ribbon_repeat_mode: EnumProperty(  # type: ignore[valid-type]
        name="リボン",
        items=IMAGE_PATH_RIBBON_REPEAT_MODE_ITEMS,
        default="repeat",
        update=_on_image_path_changed,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_image_path_changed,
    )
    inout_size_enabled: BoolProperty(name="サイズ", default=False, update=_on_image_path_changed)  # type: ignore[valid-type]
    inout_opacity_enabled: BoolProperty(name="不透明度", default=False, update=_on_image_path_changed)  # type: ignore[valid-type]
    inout_color_enabled: BoolProperty(name="色", default=False, update=_on_image_path_changed)  # type: ignore[valid-type]
    in_percent: FloatProperty(name="入り (%)", default=100.0, min=0.0, max=100.0, update=_on_image_path_changed)  # type: ignore[valid-type]
    out_percent: FloatProperty(name="抜き (%)", default=100.0, min=0.0, max=100.0, update=_on_image_path_changed)  # type: ignore[valid-type]
    in_start_percent: FloatProperty(name="入り始点 (%)", default=0.0, min=0.0, max=100.0, update=_on_image_path_changed)  # type: ignore[valid-type]
    out_start_percent: FloatProperty(name="抜き始点 (%)", default=0.0, min=0.0, max=100.0, update=_on_image_path_changed)  # type: ignore[valid-type]
    in_easing_curve: StringProperty(name="入りカーブ", default="0.0000,0.0000;1.0000,1.0000", update=_on_image_path_changed)  # type: ignore[valid-type]
    out_easing_curve: StringProperty(name="抜きカーブ", default="0.0000,0.0000;1.0000,1.0000", update=_on_image_path_changed)  # type: ignore[valid-type]
    inout_start_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="入り色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_image_path_changed,
    )
    inout_end_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="抜き色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_image_path_changed,
    )

    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_image_path_changed,
    )
    visible: BoolProperty(name="表示", default=True, update=_on_image_path_changed)  # type: ignore[valid-type]
    selected: BoolProperty(name="マルチ選択", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]
    locked: BoolProperty(name="ロック", default=False)  # type: ignore[valid-type]

    parent_kind: StringProperty(name="親種別", default="page", update=_on_image_path_changed)  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="", update=_on_image_path_changed)  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", default="", update=_on_image_path_changed)  # type: ignore[valid-type]


_CLASSES = (BMangaImagePathLayer,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bmanga_image_path_layers = bpy.props.CollectionProperty(type=BMangaImagePathLayer)
    bpy.types.Scene.bmanga_active_image_path_layer_index = bpy.props.IntProperty(default=-1, min=-1)
    _logger.debug("image_path_layer registered")


def unregister() -> None:
    for attr in (
        "bmanga_active_image_path_layer_index",
        "bmanga_image_path_layers",
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
