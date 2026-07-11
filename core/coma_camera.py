"""コマ編集モード用カメラ操作の PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)


def _update_all_bg_opacity(self, context) -> None:
    from ..utils import coma_camera
    from ..utils import percentage

    coma_camera.set_background_images_opacity(context, percentage.percent_to_factor(self.bg_images_opacity, 50.0))


def _update_all_bg_scale(self, context) -> None:
    from ..utils import coma_camera
    from ..utils import page_preview_object

    coma_camera.set_background_images_scale(context, float(self.bg_images_scale), kind_filter="name")
    coma_camera.refresh_coma_page_overview(context)
    coma_camera.update_render_border_from_current_coma(context)
    page_preview_object.set_preview_scale(context, float(self.bg_images_scale))


def _update_name_bg_opacity(self, context) -> None:
    from ..utils import coma_camera
    from ..utils import page_preview_object
    from ..utils import percentage

    coma_camera.set_background_images_properties(
        context, "ネーム", opacity=percentage.percent_to_factor(self.name_bg_images_opacity, 100.0), kind_filter="name"
    )
    page_preview_object.set_preview_opacity(
        context,
        percentage.percent_to_factor(self.name_bg_images_opacity, 100.0),
    )


def _update_own_page_visible(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.set_background_kind_visibility(context, "own_page", bool(self.own_page_visible))


def _update_own_page_opacity(self, context) -> None:
    from ..utils import coma_camera
    from ..utils import percentage

    coma_camera.set_background_images_properties(
        context, "", opacity=percentage.percent_to_factor(self.own_page_opacity, 100.0), kind_filter="own_page"
    )


def _update_koma_bg_opacity(self, context) -> None:
    from ..utils import coma_camera
    from ..utils import percentage

    coma_camera.set_background_images_properties(
        context, "コマ", opacity=percentage.percent_to_factor(self.koma_bg_images_opacity, 100.0), kind_filter="koma"
    )


def _update_name_show_all_pages(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.set_page_reference_visibility(
        context,
        show_all=bool(self.name_show_all_pages),
    )
    coma_camera.refresh_coma_page_overview(context)


def _update_name_visible(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.set_page_reference_visibility(
        context,
        show_all=bool(getattr(self, "name_show_all_pages", False)),
    )


def _update_koma_visible(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.set_background_kind_visibility(context, "koma", bool(self.koma_visible))


def _update_white_background(self, context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    if bool(getattr(scene, "bmanga_coma_white_background", False)):
        scene.render.film_transparent = False
        return
    scene.render.film_transparent = bool(self.white_background)


def _update_white_bg_toggle(self, context) -> None:
    scene = getattr(context, "scene", None) or self
    enabled = bool(getattr(scene, "bmanga_coma_white_background", False))
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    if settings is None:
        return
    from ..utils import coma_camera

    if enabled:
        scene.render.film_transparent = False
        coma_camera.apply_white_world_background(scene)
    else:
        coma_camera._restore_world_before_white(scene)
        scene.render.film_transparent = bool(getattr(settings, "white_background", True))
        coma_camera.sync_world_background_color(context)


def _update_grayscale_view(self, context) -> None:
    scene = getattr(context, "scene", None) or self
    from ..utils import display_settings

    display_settings.apply_grayscale_view(
        scene, bool(getattr(scene, "bmanga_coma_grayscale_view", False))
    )


def _update_subsurf_realtime(self, _context) -> None:
    for obj in bpy.data.objects:
        for mod in getattr(obj, "modifiers", []):
            if getattr(mod, "type", "") == "SUBSURF":
                mod.show_viewport = bool(self.subsurf_realtime)


def _update_hatching_visible(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.ensure_hatching_background(context)
    coma_camera.set_background_image_visibility(
        context, "ハッチング間隔.png", bool(self.hatching_visible)
    )


def _update_hatching_rotation(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.ensure_hatching_background(context)
    coma_camera.set_background_image_rotation(
        context, "ハッチング間隔.png", float(self.hatching_rotation)
    )


def _update_koma_depth(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.set_koma_background_depth(context, back=bool(self.koma_depth))


def _update_world_background_camera_only(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.sync_world_background_color(context)
    coma_camera.update_view(context)


def _update_solid_background(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.view_camera_in_viewports(context)


def _update_fisheye_mode(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.apply_fisheye_mode(context)


def _update_fisheye_fov(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.apply_fisheye_fov(context)


def _update_reduction_mode(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.apply_reduction_mode(context)


def _update_preview_scale(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.apply_reduction_mode(context)


def _update_resolution_index(self, context) -> None:
    from ..utils import coma_camera

    coma_camera.apply_selected_resolution_setting(context)


class BMangaComaCameraAngleItem(bpy.types.PropertyGroup):
    """カメラ位置・画角・下絵スケールを保存するアングルプリセット."""

    name: StringProperty(name="アングル名", description="このカメラアングルプリセットの名前", default="Angle")  # type: ignore[valid-type]
    location: FloatVectorProperty(name="位置", description="保存するカメラの位置", size=3, default=(0.0, -6.0, 0.0))  # type: ignore[valid-type]
    rotation: FloatVectorProperty(name="回転", description="保存するカメラの回転 (ラジアン)", size=3, default=(1.5707963, 0.0, 0.0))  # type: ignore[valid-type]
    lens: FloatProperty(name="焦点距離", description="保存するカメラの焦点距離 (mm)", default=35.0, min=1.0, max=1000.0)  # type: ignore[valid-type]
    shift_x: FloatProperty(name="シフトX", description="保存するカメラのレンズシフトX", default=0.0)  # type: ignore[valid-type]
    shift_y: FloatProperty(name="シフトY", description="保存するカメラのレンズシフトY", default=0.0)  # type: ignore[valid-type]
    fisheye_layout_mode: BoolProperty(name="魚眼モード", description="保存時に魚眼モードが有効だったか", default=False)  # type: ignore[valid-type]
    fisheye_fov: FloatProperty(name="魚眼FOV", description="保存する魚眼レンズの画角 (ラジアン)", default=3.1415927, min=1.7453293, max=6.2831855)  # type: ignore[valid-type]
    bg_images_scale: FloatProperty(name="ページ画像スケール", description="保存する下絵画像の表示スケール", default=1.0, min=0.1, max=10.0)  # type: ignore[valid-type]


class BMangaComaCameraResolutionSetting(bpy.types.PropertyGroup):
    """カメラ出力解像度プリセット."""

    name: StringProperty(name="名前", description="この原稿サイズプリセットの名前", default="新規原稿サイズ")  # type: ignore[valid-type]
    resolution_x: IntProperty(name="幅", description="出力解像度の幅 (px)", default=1920, min=1)  # type: ignore[valid-type]
    resolution_y: IntProperty(name="高さ", description="出力解像度の高さ (px)", default=1080, min=1)  # type: ignore[valid-type]


class BMangaComaCameraSettings(bpy.types.PropertyGroup):
    """参照スクリプトのカメラ操作パネル相当の Scene 設定."""

    camera_angles: CollectionProperty(type=BMangaComaCameraAngleItem)  # type: ignore[valid-type]
    camera_angles_index: IntProperty(name="アングルIndex", description="カメラプリセット一覧の選択中インデックス", default=0, min=0)  # type: ignore[valid-type]

    bg_images_opacity: FloatProperty(
        name="下絵の不透明度",
        description="コマ内の下絵画像の不透明度 (%)",
        min=0.0,
        max=100.0,
        default=50.0,
        subtype="PERCENTAGE",
        update=_update_all_bg_opacity,
    )  # type: ignore[valid-type]
    bg_images_scale: FloatProperty(
        name="ページ画像のスケール",
        description="コマ内に表示するページ画像 (下絵) の拡大率",
        min=0.1,
        max=10.0,
        default=1.0,
        update=_update_all_bg_scale,
    )  # type: ignore[valid-type]
    name_bg_images_opacity: FloatProperty(
        name="ページ一覧不透明度",
        description="ページ一覧 (ネーム) プレビュー画像の不透明度 (%)",
        min=0.0,
        max=100.0,
        default=100.0,
        subtype="PERCENTAGE",
        update=_update_name_bg_opacity,
    )  # type: ignore[valid-type]
    own_page_visible: BoolProperty(
        name="ページ画像表示",
        description="現在のページの原稿画像を表示する",
        default=True,
        update=_update_own_page_visible,
    )  # type: ignore[valid-type]
    own_page_opacity: FloatProperty(
        name="ページ画像不透明度",
        description="現在のページの原稿画像の不透明度 (%)",
        min=0.0,
        max=100.0,
        default=100.0,
        subtype="PERCENTAGE",
        update=_update_own_page_opacity,
    )  # type: ignore[valid-type]
    koma_bg_images_opacity: FloatProperty(
        name="コマ内レイヤーの不透明度",
        description="コマ内レイヤー画像の不透明度 (%)",
        min=0.0,
        max=100.0,
        default=100.0,
        subtype="PERCENTAGE",
        update=_update_koma_bg_opacity,
    )  # type: ignore[valid-type]
    name_visible: BoolProperty(
        name="ページ一覧表示",
        description="ページ一覧 (ネーム) プレビューを表示する",
        default=True,
        update=_update_name_visible,
    )  # type: ignore[valid-type]
    name_show_all_pages: BoolProperty(
        name="全ページのページ画像を表示",
        description="ページ一覧ですべてのページの原稿画像を表示する (オフで前後ページのみ)",
        default=False,
        update=_update_name_show_all_pages,
    )  # type: ignore[valid-type]
    koma_visible: BoolProperty(
        name="コマ内レイヤー表示",
        description="コマ内レイヤー画像を表示する",
        default=True,
        update=_update_koma_visible,
    )  # type: ignore[valid-type]
    white_background: BoolProperty(
        name="背景を透過",
        description="オンでレンダー背景を透過にする",
        default=True,
        update=_update_white_background,
    )  # type: ignore[valid-type]
    subsurf_realtime: BoolProperty(
        name="サブディビジョンサーフェス",
        description="ビューポート上でサブディビジョンサーフェスモディファイアを有効にする",
        default=False,
        update=_update_subsurf_realtime,
    )  # type: ignore[valid-type]
    hatching_visible: BoolProperty(
        name="ハッチング間隔を表示",
        description="コマ内にハッチング (トーン) 間隔の目安画像を表示する",
        default=False,
        update=_update_hatching_visible,
    )  # type: ignore[valid-type]
    hatching_rotation: FloatProperty(
        name="ハッチング回転",
        description="ハッチング間隔目安画像の回転角度 (ラジアン)",
        default=0.0,
        soft_min=-3.1415927,
        soft_max=3.1415927,
        update=_update_hatching_rotation,
    )  # type: ignore[valid-type]
    koma_depth: BoolProperty(
        name="コマを後ろにする",
        description="コマ内レイヤー画像を奥行き方向の奥に配置する",
        default=False,
        update=_update_koma_depth,
    )  # type: ignore[valid-type]
    world_background_camera_only: BoolProperty(
        name="ワールド背景色をカメラのみに反映",
        description="オンでワールド背景色をカメラ出力のみに反映し、オブジェクトの陰影に影響させない",
        default=False,
        update=_update_world_background_camera_only,
    )  # type: ignore[valid-type]
    use_solid_background_color: BoolProperty(
        name="ソリッド背景色を指定",
        description="オンで単色のソリッド背景色を使用する",
        default=False,
        update=_update_solid_background,
    )  # type: ignore[valid-type]
    solid_background_color: FloatVectorProperty(
        name="ソリッド背景色",
        description="ソリッド背景に使う色",
        subtype="COLOR",
        size=3,
        default=(0.05, 0.05, 0.05),
        min=0.0,
        max=1.0,
        update=_update_solid_background,
    )  # type: ignore[valid-type]
    prev_render_engine: StringProperty(name="前回レンダーエンジン", description="内部処理用に直前のレンダーエンジン名を保持します", default="")  # type: ignore[valid-type]


_CLASSES = (
    BMangaComaCameraAngleItem,
    BMangaComaCameraResolutionSetting,
    BMangaComaCameraSettings,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bmanga_coma_camera_settings = PointerProperty(type=BMangaComaCameraSettings)
    bpy.types.Scene.bmanga_coma_camera_resolution_settings = CollectionProperty(
        type=BMangaComaCameraResolutionSetting
    )
    bpy.types.Scene.bmanga_coma_camera_resolution_settings_index = IntProperty(
        name="Index",
        description="原稿サイズプリセット一覧の選択中インデックス",
        default=0,
        min=0,
        update=_update_resolution_index,
    )
    bpy.types.Scene.bmanga_coma_camera_fisheye_layout_mode = BoolProperty(
        name="魚眼モード",
        description="オンでカメラを魚眼 (パノラマ) レンズに切り替える",
        default=False,
        update=_update_fisheye_mode,
    )
    bpy.types.Scene.bmanga_coma_camera_reduction_mode = BoolProperty(
        name="縮小モード",
        description="オンで表示解像度を「縮小率」に従って縮小し、作業を軽量化する",
        default=False,
        update=_update_reduction_mode,
    )
    bpy.types.Scene.bmanga_coma_grayscale_view = BoolProperty(
        name="グレースケール表示",
        description="オンでビュー変換を AgX (露出1.0)、オフで標準にする (表示は常に sRGB)",
        default=False,
        update=_update_grayscale_view,
    )
    bpy.types.Scene.bmanga_coma_white_background = BoolProperty(
        name="背景を白にする",
        description="ソリッド背景色を白に設定し、背景を不透明にします",
        default=False,
        update=_update_white_bg_toggle,
    )
    bpy.types.Scene.bmanga_coma_camera_original_resolution_x = IntProperty(
        name="Original Resolution X",
        description="魚眼モード/縮小モード適用前の元の解像度X (内部保存用)",
        default=0,
        min=0,
    )
    bpy.types.Scene.bmanga_coma_camera_original_resolution_y = IntProperty(
        name="Original Resolution Y",
        description="魚眼モード/縮小モード適用前の元の解像度Y (内部保存用)",
        default=0,
        min=0,
    )
    bpy.types.Scene.bmanga_coma_camera_preview_scale_percentage = FloatProperty(
        name="縮小率",
        description="縮小モード時のプレビュー解像度 (%)",
        default=12.5,
        min=1.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_update_preview_scale,
    )
    bpy.types.Scene.bmanga_coma_camera_lens = FloatProperty(
        name="透視投影の焦点距離",
        description="透視投影時のカメラ焦点距離 (mm)",
        default=35.0,
        min=1.0,
        max=1000.0,
    )
    bpy.types.Scene.bmanga_coma_camera_fisheye_fov = FloatProperty(
        name="魚眼FOV",
        description="魚眼レンズの画角 (度)",
        default=3.1415927,
        min=1.7453293,
        max=6.2831855,
        subtype="ANGLE",
        update=_update_fisheye_fov,
    )
    _logger.debug("coma_camera registered")


def unregister() -> None:
    for attr in (
        "bmanga_coma_camera_fisheye_fov",
        "bmanga_coma_camera_lens",
        "bmanga_coma_camera_preview_scale_percentage",
        "bmanga_coma_camera_original_resolution_y",
        "bmanga_coma_camera_original_resolution_x",
        "bmanga_coma_white_background",
        "bmanga_coma_grayscale_view",
        "bmanga_coma_camera_reduction_mode",
        "bmanga_coma_camera_fisheye_layout_mode",
        "bmanga_coma_camera_resolution_settings_index",
        "bmanga_coma_camera_resolution_settings",
        "bmanga_coma_camera_settings",
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
