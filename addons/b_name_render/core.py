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
    ("FISHEYE_RENDER_IMAGE_OR_LAYER", "魚眼/通常レンダー", ""),
    ("FISHEYE_RENDER_FACES_OR_LAYER", "魚眼方向/通常レンダー", ""),
    ("FISHEYE_ASSEMBLE_OR_LAYER", "魚眼合成/通常レンダー", ""),
    ("EEVR_SETUP", "魚眼設定", ""),
    ("EEVR_RENDER_IMAGE", "魚眼レンダー", ""),
    ("EEVR_RENDER_FACES", "方向画像レンダー", ""),
    ("EEVR_ASSEMBLE", "魚眼合成", ""),
    ("OPERATOR", "Blenderオペレータ", ""),
)

ENGINE_ITEMS = (
    ("CYCLES", "Cycles", ""),
    ("BLENDER_EEVEE_NEXT", "EEVEE Next", ""),
    ("BLENDER_WORKBENCH", "Workbench", ""),
)


class BNameRenderToolSettings(bpy.types.PropertyGroup):
    bg_images_scale: FloatProperty(name="ページ画像のスケール", default=1.0, min=0.1, max=10.0)  # type: ignore[valid-type]


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
    BNameRenderToolSettings,
    BNameRenderCommand,
    BNameRenderPreset,
    BNameRenderState,
)

_REGISTERED_SCENE_PROPS: list[str] = []


def _ensure_original_resolution(scene) -> tuple[int, int]:
    current_x = max(1, int(getattr(scene.render, "resolution_x", 1) or 1))
    current_y = max(1, int(getattr(scene.render, "resolution_y", 1) or 1))
    original_x = int(getattr(scene, "original_resolution_x", 0) or 0)
    original_y = int(getattr(scene, "original_resolution_y", 0) or 0)
    if original_x <= 0 or original_y <= 0:
        scene.original_resolution_x = current_x
        scene.original_resolution_y = current_y
        return current_x, current_y
    return original_x, original_y


def _set_camera_projection_for_fisheye(scene, enabled: bool) -> None:
    camera = getattr(scene, "camera", None)
    camera_data = getattr(camera, "data", None)
    if camera_data is None or not hasattr(camera_data, "type"):
        return
    try:
        camera_data.type = "PANO" if enabled else "PERSP"
        if enabled:
            if hasattr(camera_data, "panorama_type"):
                camera_data.panorama_type = "FISHEYE_EQUIDISTANT"
            if hasattr(camera_data, "fisheye_fov"):
                camera_data.fisheye_fov = float(getattr(scene, "fisheye_fov", 3.1415927) or 3.1415927)
    except Exception:  # noqa: BLE001
        pass


def _apply_output_resolution_mode(scene) -> None:
    if scene is None or getattr(scene, "render", None) is None:
        return
    original_x, original_y = _ensure_original_resolution(scene)
    scale = max(0.01, min(1.0, float(getattr(scene, "preview_scale_percentage", 100.0) or 100.0) / 100.0))
    fisheye = bool(getattr(scene, "fisheye_layout_mode", False))
    reduction = bool(getattr(scene, "reduction_mode", False))
    _set_camera_projection_for_fisheye(scene, fisheye)
    if fisheye:
        edge = max(original_x, original_y)
        if reduction:
            edge = max(1, int(round(edge * scale)))
        scene.render.resolution_x = edge
        scene.render.resolution_y = edge
        return
    if reduction:
        scene.render.resolution_x = max(1, int(round(original_x * scale)))
        scene.render.resolution_y = max(1, int(round(original_y * scale)))
        return
    scene.render.resolution_x = original_x
    scene.render.resolution_y = original_y


def _on_output_mode_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else None
    try:
        _apply_output_resolution_mode(scene)
    except Exception:  # noqa: BLE001
        pass


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
    _register_scene_props()


def unregister() -> None:
    _unregister_scene_props()
    if hasattr(bpy.types.Scene, "bname_render_state"):
        del bpy.types.Scene.bname_render_state
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def _register_scene_prop(name: str, prop) -> None:
    if hasattr(bpy.types.Scene, name):
        return
    setattr(bpy.types.Scene, name, prop)
    _REGISTERED_SCENE_PROPS.append(name)


def _register_scene_props() -> None:
    _register_scene_prop("my_tool", PointerProperty(type=BNameRenderToolSettings))
    _register_scene_prop("fisheye_layout_mode", BoolProperty(name="魚眼モード", default=False, update=_on_output_mode_changed))
    _register_scene_prop(
        "fisheye_fov",
        FloatProperty(
            name="魚眼FOV",
            description="魚眼モード時の視野角",
            default=3.1415927,
            min=1.7453293,
            max=6.2831855,
            subtype="ANGLE",
            update=_on_output_mode_changed,
        ),
    )
    _register_scene_prop("reduction_mode", BoolProperty(name="縮小モード", default=False, update=_on_output_mode_changed))
    _register_scene_prop("original_resolution_x", IntProperty(name="元解像度X", default=0, min=0))
    _register_scene_prop("original_resolution_y", IntProperty(name="元解像度Y", default=0, min=0))
    _register_scene_prop(
        "preview_scale_percentage",
        FloatProperty(name="縮小率", default=12.5, min=1.0, max=100.0, subtype="PERCENTAGE", update=_on_output_mode_changed),
    )
    _register_scene_prop("comic_frame_mode", BoolProperty(name="コマプレビューとして出力", default=False))


def _unregister_scene_props() -> None:
    while _REGISTERED_SCENE_PROPS:
        name = _REGISTERED_SCENE_PROPS.pop()
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
