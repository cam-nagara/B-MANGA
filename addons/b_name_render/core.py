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
    ("RENDER_LAYER", "レンダー：ワード検出", ""),
    ("FISHEYE_RENDER_IMAGE_OR_LAYER", "魚眼/通常レンダー", ""),
    ("FISHEYE_RENDER_FACES_OR_LAYER", "魚眼方向/通常レンダー", ""),
    ("FISHEYE_ASSEMBLE_OR_LAYER", "魚眼合成/通常レンダー", ""),
    ("EEVR_SETUP", "魚眼設定", ""),
    ("EEVR_RENDER_IMAGE", "魚眼レンダー", ""),
    ("EEVR_RENDER_FACES", "方向画像レンダー", ""),
    ("EEVR_ASSEMBLE", "魚眼合成", ""),
    ("OPERATOR", "Blenderオペレータ", ""),
    ("RUN_PRESET", "プリセット実行", ""),
)

ENGINE_ITEMS = (
    ("CYCLES", "Cycles", ""),
    ("BLENDER_EEVEE_NEXT", "EEVEE Next", ""),
    ("BLENDER_WORKBENCH", "Workbench", ""),
)


class BNameRenderToolSettings(bpy.types.PropertyGroup):
    bg_images_scale: FloatProperty(name="ページ画像のスケール", default=1.0, min=0.1, max=10.0)  # type: ignore[valid-type]


class BNameRenderCommand(bpy.types.PropertyGroup):
    name: StringProperty(name="コマンド名", default="レンダー")  # type: ignore[valid-type]
    name_auto: BoolProperty(name="名前を自動生成", default=True)  # type: ignore[valid-type]
    command_type: EnumProperty(name="種類", items=COMMAND_TYPE_ITEMS, default="RENDER")  # type: ignore[valid-type]
    enabled: BoolProperty(name="有効", default=True)  # type: ignore[valid-type]
    collapsed: BoolProperty(name="折りたたみ", default=False)  # type: ignore[valid-type]

    view_layer_name: StringProperty(name="ビューレイヤー", default="")  # type: ignore[valid-type]
    view_layer_enabled: BoolProperty(name="有効化", default=True)  # type: ignore[valid-type]

    collection_name: StringProperty(name="コレクション", default="")  # type: ignore[valid-type]
    exclude_collection: BoolProperty(name="除外", default=True)  # type: ignore[valid-type]

    node_name: StringProperty(name="ノード名", default="")  # type: ignore[valid-type]
    node_group_name: StringProperty(name="対象", default="")  # type: ignore[valid-type]
    label_contains: StringProperty(name="検出ワード", default="")  # type: ignore[valid-type]
    mute: BoolProperty(name="ミュート", default=False)  # type: ignore[valid-type]

    input_name: StringProperty(name="入力名", default="")  # type: ignore[valid-type]
    float_value: FloatProperty(name="値", default=0.0)  # type: ignore[valid-type]
    text_value: StringProperty(name="文字列", default="")  # type: ignore[valid-type]
    folder_path: StringProperty(name="フォルダ", subtype="DIR_PATH", default="//passes/")  # type: ignore[valid-type]

    sample_count: IntProperty(name="サンプル数", default=1, min=1, soft_max=1024)  # type: ignore[valid-type]
    engine: EnumProperty(name="レンダーエンジン", items=ENGINE_ITEMS, default="CYCLES")  # type: ignore[valid-type]
    operator_idname: StringProperty(name="オペレータ", default="")  # type: ignore[valid-type]

    target_preset_name: StringProperty(name="実行するプリセット", default="")  # type: ignore[valid-type]


PRESET_CATEGORY_ITEMS = (
    ("ALL", "すべて", "すべてのプリセットを表示"),
    ("GROUP", "まとめ", "親プリセット (プリセット実行を含む) のみ表示"),
    ("CHARA", "キャラ", "キャラ系プリセットのみ表示"),
    ("BG", "背景", "背景系プリセットのみ表示"),
    ("LEGACY", "旧出力シーン互換", "旧出力シーン互換プリセットのみ表示"),
    ("OTHER", "その他", "キャラ・背景以外のプリセットを表示"),
)


def preset_category_of(name: str) -> str:
    n = str(name or "")
    if n.startswith("旧出力シーン互換"):
        return "LEGACY"
    if n.startswith("キャラ"):
        return "CHARA"
    if n.startswith("背景"):
        return "BG"
    return "OTHER"


def preset_is_group(preset) -> bool:
    """「プリセット実行」コマンドを含む = 親プリセット (まとめ) かを返す."""
    commands = getattr(preset, "commands", None)
    if not commands:
        return False
    return any(getattr(c, "command_type", "") == "RUN_PRESET" for c in commands)


def preset_matches_category(preset, category: str) -> bool:
    """プリセットが表示カテゴリに合致するか. GROUP は親プリセット判定。"""
    if category == "ALL":
        return True
    if category == "GROUP":
        return preset_is_group(preset)
    return preset_category_of(getattr(preset, "name", "")) == category


def _on_preset_category_update(self, context) -> None:
    """種類フィルタ変更時、選択中プリセットも表示中の種類へ追従させる.

    追従しないと、選択中 (= コマンド/「プリセットを実行」の対象) が一覧で
    非表示のまま残り、別種類の隠れたプリセットを誤実行しかねない。
    ``self`` は WindowManager (選択状態は WM 上)。プリセット本体は Scene 側。
    """
    scene = getattr(context, "scene", None) if context is not None else None
    state = getattr(scene, "bname_render_state", None) if scene is not None else None
    presets = getattr(state, "presets", None) if state is not None else None
    category = str(getattr(self, "bname_render_preset_category", "ALL") or "ALL")
    if category == "ALL" or not presets:
        return
    cur = max(0, min(int(getattr(self, "bname_render_active_preset_index", 0)), len(presets) - 1))
    if preset_matches_category(presets[cur], category):
        return
    for i, preset in enumerate(presets):
        if preset_matches_category(preset, category):
            self.bname_render_active_preset_index = i
            return


# 選択 index の実体は WindowManager (Scene プロパティをクリック毎に書き換える
# と重いシーンで依存グラフ再評価が走り遅いため)。UI/template_list は WM を直接
# 参照する。下記の get/set プロキシは、テストや外部から従来通り
# ``state.active_preset_index`` / ``preset.active_command_index`` でアクセス
# できるようにするための後方互換シム (実体は常に WM を読み書きする)。
def _api_wm():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is not None:
        return wm
    wms = getattr(bpy.data, "window_managers", None)
    return wms[0] if wms and len(wms) else None


def _api_get_preset_index(_self) -> int:
    wm = _api_wm()
    return int(getattr(wm, "bname_render_active_preset_index", 0) or 0) if wm is not None else 0


def _api_set_preset_index(_self, value: int) -> None:
    wm = _api_wm()
    if wm is not None:
        wm.bname_render_active_preset_index = max(0, int(value))


def _api_get_command_index(_self) -> int:
    wm = _api_wm()
    return int(getattr(wm, "bname_render_active_command_index", 0) or 0) if wm is not None else 0


def _api_set_command_index(_self, value: int) -> None:
    wm = _api_wm()
    if wm is not None:
        wm.bname_render_active_command_index = max(0, int(value))


class BNameRenderPreset(bpy.types.PropertyGroup):
    name: StringProperty(name="プリセット名", default="新規プリセット")  # type: ignore[valid-type]
    commands: CollectionProperty(type=BNameRenderCommand)  # type: ignore[valid-type]
    active_command_index: IntProperty(  # type: ignore[valid-type]
        name="コマンド", get=_api_get_command_index, set=_api_set_command_index
    )


class BNameRenderState(bpy.types.PropertyGroup):
    presets: CollectionProperty(type=BNameRenderPreset)  # type: ignore[valid-type]
    active_preset_index: IntProperty(  # type: ignore[valid-type]
        name="プリセット", get=_api_get_preset_index, set=_api_set_preset_index
    )
    # 表示カテゴリも WindowManager 側 (テスト等からの直接利用は無い)。
    last_card_click_index: IntProperty(name="前回コマンド", default=-1)  # type: ignore[valid-type]
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


def fisheye_enabled(scene) -> bool:
    if scene is None:
        return False
    return bool(
        getattr(scene, "fisheye_layout_mode", False)
        or getattr(scene, "bname_coma_camera_fisheye_layout_mode", False)
    )


def fisheye_fov(scene) -> float:
    if scene is None:
        return 3.1415927
    if bool(getattr(scene, "bname_coma_camera_fisheye_layout_mode", False)):
        return float(getattr(scene, "bname_coma_camera_fisheye_fov", 3.1415927) or 3.1415927)
    return float(getattr(scene, "fisheye_fov", 3.1415927) or 3.1415927)


def reduction_enabled(scene) -> bool:
    if scene is None:
        return False
    return bool(
        getattr(scene, "reduction_mode", False)
        or getattr(scene, "bname_coma_camera_reduction_mode", False)
    )


def preview_scale_percentage(scene) -> float:
    if scene is None:
        return 100.0
    if bool(getattr(scene, "bname_coma_camera_reduction_mode", False)):
        return float(getattr(scene, "bname_coma_camera_preview_scale_percentage", 100.0) or 100.0)
    return float(getattr(scene, "preview_scale_percentage", 100.0) or 100.0)


def original_resolution(scene) -> tuple[int, int]:
    if scene is None or getattr(scene, "render", None) is None:
        return 1, 1
    bname_x = int(getattr(scene, "bname_coma_camera_original_resolution_x", 0) or 0)
    bname_y = int(getattr(scene, "bname_coma_camera_original_resolution_y", 0) or 0)
    if bname_x > 0 and bname_y > 0:
        return bname_x, bname_y
    return _ensure_original_resolution(scene)


def _set_camera_projection_for_fisheye(scene, enabled: bool) -> None:
    camera = getattr(scene, "camera", None)
    camera_data = getattr(camera, "data", None)
    if camera_data is None or not hasattr(camera_data, "type"):
        return
    try:
        camera_data.type = "PANO" if enabled else "PERSP"
        if enabled:
            if hasattr(camera_data, "fisheye_fov"):
                camera_data.fisheye_fov = fisheye_fov(scene)
    except Exception:  # noqa: BLE001
        pass


def _apply_output_resolution_mode(scene) -> None:
    if scene is None or getattr(scene, "render", None) is None:
        return
    original_x, original_y = original_resolution(scene)
    scale = max(0.01, min(1.0, preview_scale_percentage(scene) / 100.0))
    fisheye = fisheye_enabled(scene)
    reduction = reduction_enabled(scene)
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


def _mirror_render_setting_to_bname(scene, render_name: str, bname_name: str) -> None:
    if hasattr(scene, render_name) and hasattr(scene, bname_name):
        try:
            setattr(scene, bname_name, getattr(scene, render_name))
        except Exception:  # noqa: BLE001
            pass


def _on_output_mode_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else None
    try:
        _apply_output_resolution_mode(scene)
    except Exception:  # noqa: BLE001
        pass


def _on_reduction_mode_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else None
    try:
        if scene is not None:
            _mirror_render_setting_to_bname(scene, "reduction_mode", "bname_coma_camera_reduction_mode")
        _apply_output_resolution_mode(scene)
    except Exception:  # noqa: BLE001
        pass


def _on_preview_scale_changed(_self, context) -> None:
    scene = getattr(context, "scene", None) if context is not None else None
    try:
        if scene is not None:
            _mirror_render_setting_to_bname(scene, "preview_scale_percentage", "bname_coma_camera_preview_scale_percentage")
        _apply_output_resolution_mode(scene)
    except Exception:  # noqa: BLE001
        pass


def get_state(context) -> BNameRenderState | None:
    scene = getattr(context, "scene", None)
    return getattr(scene, "bname_render_state", None) if scene is not None else None


# 選択状態 (プリセット/コマンドの index) は WindowManager に置く。
# Scene プロパティをクリックの度に書き換えると、重いシーンで Blender の
# 依存グラフ再評価 (COW) が毎回走り、一覧の選択切替が一拍遅れるため。
def get_active_preset_index(context) -> int:
    wm = getattr(context, "window_manager", None)
    return int(getattr(wm, "bname_render_active_preset_index", 0) or 0) if wm is not None else 0


def set_active_preset_index(context, value: int) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is not None:
        wm.bname_render_active_preset_index = max(0, int(value))


def get_active_command_index(context) -> int:
    wm = getattr(context, "window_manager", None)
    return int(getattr(wm, "bname_render_active_command_index", 0) or 0) if wm is not None else 0


def set_active_command_index(context, value: int) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is not None:
        wm.bname_render_active_command_index = max(0, int(value))


def active_preset(context) -> BNameRenderPreset | None:
    state = get_state(context)
    if state is None or not state.presets:
        return None
    # 読み取り専用アクセサ。index は WindowManager 側。クランプはローカル
    # 変数だけで行い、描画中に書き戻さない (ID 書き込み禁止)。
    idx = max(0, min(get_active_preset_index(context), len(state.presets) - 1))
    return state.presets[idx]


def active_command(context) -> BNameRenderCommand | None:
    preset = active_preset(context)
    if preset is None or not preset.commands:
        return None
    idx = max(0, min(get_active_command_index(context), len(preset.commands) - 1))
    return preset.commands[idx]


_WM_PROPS = (
    "bname_render_active_preset_index",
    "bname_render_active_command_index",
    "bname_render_preset_category",
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_render_state = PointerProperty(type=BNameRenderState)
    # 選択状態は WindowManager に置く (Scene だと重いシーンで依存グラフ
    # 再評価が走り選択切替が遅くなるため)。ファイル間で保存はされない。
    bpy.types.WindowManager.bname_render_active_preset_index = IntProperty(
        name="プリセット", default=0, min=0
    )
    bpy.types.WindowManager.bname_render_active_command_index = IntProperty(
        name="コマンド", default=0, min=0
    )
    bpy.types.WindowManager.bname_render_preset_category = EnumProperty(
        name="表示", items=PRESET_CATEGORY_ITEMS, default="ALL",
        update=_on_preset_category_update,
    )
    _register_scene_props()


def unregister() -> None:
    _unregister_scene_props()
    for name in _WM_PROPS:
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
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
    _register_scene_prop("reduction_mode", BoolProperty(name="縮小モード", default=False, update=_on_reduction_mode_changed))
    _register_scene_prop("original_resolution_x", IntProperty(name="元解像度X", default=0, min=0))
    _register_scene_prop("original_resolution_y", IntProperty(name="元解像度Y", default=0, min=0))
    _register_scene_prop(
        "preview_scale_percentage",
        FloatProperty(name="縮小率", default=12.5, min=1.0, max=100.0, subtype="PERCENTAGE", update=_on_preview_scale_changed),
    )


def _unregister_scene_props() -> None:
    while _REGISTERED_SCENE_PROPS:
        name = _REGISTERED_SCENE_PROPS.pop()
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
