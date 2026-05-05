"""B-Name-Render data model."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


COMMAND_TYPE_ITEMS = (
    ("STATE_BEGIN", "出力状態を退避して初期化", ""),
    ("STATE_END", "出力状態を復元", ""),
    ("SET_VIEW_LAYER", "ビューレイヤー", ""),
    ("SET_COLLECTION_EXCLUDE", "コレクション除外", ""),
    ("SET_NODE_MUTE", "ノードミュート", ""),
    ("SET_OUTPUT_GROUP", "ファイル出力切替", ""),
    ("SET_AOV_INPUT", "AOV入力", ""),
    ("SET_OUTPUT_NAME", "出力画像名", ""),
    ("SET_OUTPUT_FOLDER", "出力フォルダ", ""),
    ("RELOAD_IMAGES", "画像ノード再読み込み", ""),
    ("RENDER", "レンダー", ""),
    ("RENDER_LAYER", "フレーム出力レンダー", ""),
    ("EEVR_SETUP", "eeVR設定", ""),
    ("EEVR_RENDER_IMAGE", "eeVR魚眼レンダー", ""),
    ("EEVR_RENDER_FACES", "eeVR方向画像レンダー", ""),
    ("EEVR_ASSEMBLE", "eeVRパノラマ合成", ""),
    ("OPERATOR", "Blenderオペレータ", ""),
)

ENGINE_ITEMS = (
    ("CYCLES", "Cycles", ""),
    ("BLENDER_EEVEE_NEXT", "EEVEE Next", ""),
    ("BLENDER_WORKBENCH", "Workbench", ""),
)


class BNameRenderCommand(bpy.types.PropertyGroup):
    name: StringProperty(name="カード名", default="レンダー")  # type: ignore[valid-type]
    command_type: EnumProperty(name="種類", items=COMMAND_TYPE_ITEMS, default="RENDER")  # type: ignore[valid-type]
    enabled: BoolProperty(name="有効", default=True)  # type: ignore[valid-type]

    view_layer_name: StringProperty(name="ビューレイヤー", default="")  # type: ignore[valid-type]
    view_layer_enabled: BoolProperty(name="有効化", default=True)  # type: ignore[valid-type]

    collection_name: StringProperty(name="コレクション", default="")  # type: ignore[valid-type]
    exclude_collection: BoolProperty(name="除外", default=True)  # type: ignore[valid-type]

    node_name: StringProperty(name="ノード名", default="")  # type: ignore[valid-type]
    node_group_name: StringProperty(name="対象", default="")  # type: ignore[valid-type]
    label_contains: StringProperty(name="フレーム名", default="")  # type: ignore[valid-type]
    mute: BoolProperty(name="ミュート", default=False)  # type: ignore[valid-type]

    input_name: StringProperty(name="入力名", default="")  # type: ignore[valid-type]
    float_value: FloatProperty(name="値", default=0.0)  # type: ignore[valid-type]
    text_value: StringProperty(name="文字列", default="")  # type: ignore[valid-type]
    folder_path: StringProperty(name="フォルダ", subtype="DIR_PATH", default="//passes/")  # type: ignore[valid-type]

    sample_count: IntProperty(name="サンプル数", default=1, min=1, soft_max=1024)  # type: ignore[valid-type]
    engine: EnumProperty(name="レンダーエンジン", items=ENGINE_ITEMS, default="CYCLES")  # type: ignore[valid-type]
    operator_idname: StringProperty(name="オペレータ", default="")  # type: ignore[valid-type]


class BNameRenderPreset(bpy.types.PropertyGroup):
    name: StringProperty(name="プリセット名", default="新規プリセット")  # type: ignore[valid-type]
    commands: CollectionProperty(type=BNameRenderCommand)  # type: ignore[valid-type]
    active_command_index: IntProperty(name="カード", default=0, min=0)  # type: ignore[valid-type]


class BNameRenderState(bpy.types.PropertyGroup):
    presets: CollectionProperty(type=BNameRenderPreset)  # type: ignore[valid-type]
    active_preset_index: IntProperty(name="プリセット", default=0, min=0)  # type: ignore[valid-type]
    last_card_click_index: IntProperty(name="前回カード", default=-1)  # type: ignore[valid-type]
    last_card_click_time: FloatProperty(name="前回クリック時刻", default=0.0)  # type: ignore[valid-type]
    sound_enabled: BoolProperty(name="出力完了時アラーム再生", default=False)  # type: ignore[valid-type]


_CLASSES = (
    BNameRenderCommand,
    BNameRenderPreset,
    BNameRenderState,
)


def get_state(context) -> BNameRenderState | None:
    scene = getattr(context, "scene", None)
    return getattr(scene, "bname_render_state", None) if scene is not None else None


def active_preset(context) -> BNameRenderPreset | None:
    state = get_state(context)
    if state is None or not state.presets:
        return None
    idx = max(0, min(int(state.active_preset_index), len(state.presets) - 1))
    state.active_preset_index = idx
    return state.presets[idx]


def active_command(context) -> BNameRenderCommand | None:
    preset = active_preset(context)
    if preset is None or not preset.commands:
        return None
    idx = max(0, min(int(preset.active_command_index), len(preset.commands) - 1))
    preset.active_command_index = idx
    return preset.commands[idx]


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_render_state = PointerProperty(type=BNameRenderState)


def unregister() -> None:
    if hasattr(bpy.types.Scene, "bname_render_state"):
        del bpy.types.Scene.bname_render_state
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
