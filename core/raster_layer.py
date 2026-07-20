"""ラスター描画レイヤーの PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)

BIT_DEPTH_ITEMS = (
    ("gray8", "グレー 8bit", "256階調のグレースケールで保存します"),
    ("gray1", "1bit", "白黒2階調で保存します"),
)

SCOPE_ITEMS = (
    ("page", "ページ", "このページだけに配置します"),
    ("master", "マスター", "全ページ共通のマスターに配置します"),
)

PARENT_KIND_ITEMS = (
    ("none", "なし", "親を持ちません"),
    ("page", "ページ", "ページを親にします"),
    ("coma", "コマ", "コマを親にします"),
)


def _on_raster_runtime_display_changed(self, context) -> None:
    try:
        from ..operators import raster_layer_op

        raster_layer_op.sync_raster_runtime_display(context, self)
    except Exception:  # noqa: BLE001
        _logger.exception("raster runtime display update failed")
        screen = getattr(context, "screen", None) if context is not None else None
        if screen is not None:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def _on_raster_title_changed(_self, context) -> None:
    if not str(getattr(_self, "id", "") or "").strip():
        return
    try:
        from ..utils import layer_stack as layer_stack_utils

        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        pass


class BMangaRasterLayer(bpy.types.PropertyGroup):
    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", description="レイヤー一覧に表示する名前です", default="", update=_on_raster_title_changed)  # type: ignore[valid-type]
    image_name: StringProperty(name="Image名", description="このレイヤーが参照するBlender画像データの名前です", default="")  # type: ignore[valid-type]
    filepath_rel: StringProperty(name="PNG相対パス", description="書き出し先PNGファイルの相対パスです", default="")  # type: ignore[valid-type]
    dpi: IntProperty(  # type: ignore[valid-type]
        name="DPI",
        description="ラスター画像の解像度です (dpi)",
        default=300,
        min=30,
        soft_max=1200,
    )
    bit_depth: EnumProperty(  # type: ignore[valid-type]
        name="階調",
        description="保存する画像の階調を選択します",
        items=BIT_DEPTH_ITEMS,
        default="gray8",
    )
    line_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="カラー",
        description="グレースケール画像の濃い側 (黒) に適用する色です",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_raster_runtime_display_changed,
    )
    fill_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="セカンダリカラー",
        description="グレースケール画像の薄い側 (白) に適用する色です。既定の白のままなら見た目は変わりません",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_raster_runtime_display_changed,
    )
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        description="レイヤー全体の不透明度です (%)",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_raster_runtime_display_changed,
    )
    visible: BoolProperty(name="表示", description="このレイヤーを表示します", default=True, update=_on_raster_runtime_display_changed)  # type: ignore[valid-type]
    selected: BoolProperty(name="マルチ選択", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]
    locked: BoolProperty(name="ロック", description="このレイヤーの編集をロックします", default=False)  # type: ignore[valid-type]
    scope: EnumProperty(  # type: ignore[valid-type]
        name="所属",
        description="レイヤーの所属先を選択します (ページ固有かマスターか)",
        items=SCOPE_ITEMS,
        default="page",
    )
    parent_kind: EnumProperty(  # type: ignore[valid-type]
        name="親",
        items=PARENT_KIND_ITEMS,
        default="page",
    )
    parent_key: StringProperty(name="親キー", default="")  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", default="")  # type: ignore[valid-type]


_CLASSES = (BMangaRasterLayer,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bmanga_raster_layers = CollectionProperty(type=BMangaRasterLayer)
    bpy.types.Scene.bmanga_active_raster_layer_index = IntProperty(default=-1, min=-1)
    _logger.debug("raster_layer registered")


def unregister() -> None:
    for attr in (
        "bmanga_active_raster_layer_index",
        "bmanga_raster_layers",
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
