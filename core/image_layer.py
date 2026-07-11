"""画像レイヤー (ビットマップ) の PropertyGroup.

計画書 3.1.1 参照。スキャンラフ取り込み・写真参照・実写背景用途。
画像そのものは透明テクスチャ付き平面として同期し、書き出し時は
io/export_pipeline.py が Pillow で合成する。
"""

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

_BLEND_MODE_ITEMS = (
    ("normal", "通常", "そのまま重ねます"),
    ("multiply", "乗算", "下のレイヤーと乗算して重ねます (暗くなります)"),
    ("screen", "スクリーン", "下のレイヤーとスクリーン合成します (明るくなります)"),
    ("overlay", "オーバーレイ", "明暗を保ったまま重ねます"),
    ("add", "加算", "色を加算して重ねます (明るくなります)"),
)


def _on_image_layer_changed(_self, context) -> None:
    try:
        from ..utils import image_real_object

        image_real_object.on_image_entry_changed(_self)
    except Exception:  # noqa: BLE001
        pass
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _on_image_title_changed(_self, context) -> None:
    _on_image_layer_changed(_self, context)
    if not str(getattr(_self, "id", "") or "").strip():
        return
    try:
        from ..utils import layer_stack as layer_stack_utils

        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        pass


class BMangaImageLayer(bpy.types.PropertyGroup):
    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", description="レイヤー一覧に表示する名前です", default="", update=_on_image_title_changed)  # type: ignore[valid-type]
    filepath: StringProperty(  # type: ignore[valid-type]
        name="画像パス",
        description="PNG/JPG/TIFF/PSD",
        subtype="FILE_PATH",
        default="",
        update=_on_image_layer_changed,
    )
    # 配置 (mm)
    x_mm: FloatProperty(name="X", description="画像の配置X座標です (mm)", default=0.0, update=_on_image_layer_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", description="画像の配置Y座標です (mm)", default=0.0, update=_on_image_layer_changed)  # type: ignore[valid-type]
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="画像の表示幅です (mm)",
        default=100.0,
        min=0.1,
        update=_on_image_layer_changed,
    )
    height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="画像の表示高さです (mm)",
        default=100.0,
        min=0.1,
        update=_on_image_layer_changed,
    )
    rotation_deg: FloatProperty(  # type: ignore[valid-type]
        name="回転",
        description="画像の回転角度です (度)",
        default=0.0,
        update=_on_image_layer_changed,
    )
    flip_x: BoolProperty(  # type: ignore[valid-type]
        name="左右反転",
        description="画像を左右反転して表示します",
        default=False,
        update=_on_image_layer_changed,
    )
    flip_y: BoolProperty(  # type: ignore[valid-type]
        name="上下反転",
        description="画像を上下反転して表示します",
        default=False,
        update=_on_image_layer_changed,
    )

    # 表示属性
    visible: BoolProperty(name="表示", description="このレイヤーを表示します", default=True, update=_on_image_layer_changed)  # type: ignore[valid-type]
    selected: BoolProperty(name="マルチ選択", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]
    locked: BoolProperty(name="ロック", description="このレイヤーの編集をロックします", default=False)  # type: ignore[valid-type]
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        description="レイヤー全体の不透明度です (%)",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_image_layer_changed,
    )
    blend_mode: EnumProperty(  # type: ignore[valid-type]
        name="ブレンド",
        description="下のレイヤーとの合成方法を選択します",
        items=_BLEND_MODE_ITEMS,
        default="normal",
        update=_on_image_layer_changed,
    )

    # 簡易レベル補正 (下書き取込用途、計画書 3.1.1)
    brightness: FloatProperty(  # type: ignore[valid-type]
        name="明度",
        description="画像の明るさを補正します",
        default=0.0,
        soft_min=-1.0,
        soft_max=1.0,
        update=_on_image_layer_changed,
    )
    contrast: FloatProperty(  # type: ignore[valid-type]
        name="コントラスト",
        description="画像のコントラストを補正します",
        default=0.0,
        soft_min=-1.0,
        soft_max=1.0,
        update=_on_image_layer_changed,
    )
    binarize_enabled: BoolProperty(  # type: ignore[valid-type]
        name="2値化",
        description="画像を白黒2値に変換します",
        default=False,
        update=_on_image_layer_changed,
    )
    binarize_threshold: FloatProperty(  # type: ignore[valid-type]
        name="2値化しきい値",
        description="2値化する明るさのしきい値です",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_on_image_layer_changed,
    )

    tint_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色合い",
        description="画像に重ねる色合いです",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_image_layer_changed,
    )
    parent_kind: StringProperty(name="親種別", default="none", update=_on_image_layer_changed)  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="", update=_on_image_layer_changed)  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", default="", update=_on_image_layer_changed)  # type: ignore[valid-type]


_CLASSES = (BMangaImageLayer,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("image_layer registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
