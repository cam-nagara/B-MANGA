"""N-Panel の B-MANGA タブ: ビュー操作."""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import page_file_scene


B_NAME_CATEGORY = "B-MANGA"
PAGE_PREVIEW_RANGE_ALL = "ALL"
PAGE_PREVIEW_RANGE_NEAR = "NEAR"


def _active_page_number_get(scene) -> int:
    work = getattr(scene, "bmanga_work", None)
    info = getattr(work, "work_info", None) if work is not None else None
    start = int(getattr(info, "page_number_start", 1) or 1) if info is not None else 1
    idx = int(getattr(work, "active_page_index", 0) or 0) if work is not None else 0
    if work is None or not getattr(work, "loaded", False) or len(getattr(work, "pages", [])) == 0:
        return start
    idx = max(0, min(len(work.pages) - 1, idx))
    return start + idx


def _active_page_number_set(scene, value: int) -> None:
    work = getattr(scene, "bmanga_work", None)
    if work is None or not getattr(work, "loaded", False) or len(getattr(work, "pages", [])) == 0:
        return
    if page_file_scene.is_page_edit_scene(scene):
        return
    info = getattr(work, "work_info", None)
    start = int(getattr(info, "page_number_start", 1) or 1) if info is not None else 1
    idx = int(value) - start
    idx = max(0, min(len(work.pages) - 1, idx))
    try:
        from ..utils import page_range

        if not page_range.page_in_range(work.pages[idx]):
            return
    except Exception:  # noqa: BLE001
        pass
    work.active_page_index = idx
    scene.bmanga_overview_mode = True
    scene.bmanga_current_coma_id = ""
    scene.bmanga_current_coma_page_id = ""
    if hasattr(scene, "bmanga_active_layer_kind"):
        scene.bmanga_active_layer_kind = "page"
    try:
        from ..utils import edge_selection, layer_stack

        edge_selection.clear_selection(bpy.context)
        layer_stack.sync_layer_stack_after_data_change(bpy.context)
    except Exception:  # noqa: BLE001
        pass


def _current_page_id_for_update(scene, context) -> str:
    page_id = page_file_scene.current_page_id(scene) or ""
    if not page_id:
        from ..core.mode import MODE_COMA, get_mode
        if get_mode(context) == MODE_COMA:
            page_id = str(getattr(scene, "bmanga_current_coma_page_id", "") or "")
    return page_id


def _refresh_page_preview_content(scene, context, *, force: bool = True) -> None:
    try:
        from ..utils import page_preview_object

        page_preview_object.sync_page_previews(context, getattr(scene, "bmanga_work", None), force=force)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..core.mode import MODE_COMA, get_mode

        if get_mode(context) == MODE_COMA:
            from ..utils import coma_camera

            if bool(getattr(scene, "bmanga_page_preview_enabled", True)):
                coma_camera.refresh_coma_page_overview(context)
            else:
                coma_camera._remove_page_overview_backgrounds(scene)
    except Exception:  # noqa: BLE001
        pass
    try:
        for area in (context.screen.areas if context.screen else ()):
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def _page_work_info_visible_update(scene, context) -> None:
    _refresh_page_preview_content(scene, context, force=True)


def _page_guides_visible_update(scene, context) -> None:
    _refresh_page_preview_content(scene, context, force=True)


def _coma_content_visible_update(scene, context) -> None:
    visible = bool(scene.bmanga_coma_content_visible)
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    if settings is not None:
        settings.koma_visible = visible
    try:
        from ..utils import coma_camera

        coma_camera.set_background_kind_visibility(context, "koma", visible)
    except Exception:  # noqa: BLE001
        pass


def _page_preview_enabled_update(scene, context) -> None:
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    if settings is not None:
        try:
            settings.name_visible = bool(getattr(scene, "bmanga_page_preview_enabled", True))
        except Exception:  # noqa: BLE001
            pass
    try:
        from ..utils import view_settings

        view_settings.copy_scene_to_work(scene, getattr(scene, "bmanga_work", None))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..utils import page_preview_object

        page_preview_object.sync_page_previews(context, getattr(scene, "bmanga_work", None), force=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..core.mode import MODE_COMA, get_mode

        if get_mode(context) == MODE_COMA:
            from ..utils import coma_camera

            if bool(getattr(scene, "bmanga_page_preview_enabled", True)):
                coma_camera.refresh_coma_page_overview(context)
            else:
                coma_camera._remove_page_overview_backgrounds(scene)
    except Exception:  # noqa: BLE001
        pass


def _draw_page_preview_range_buttons(layout, scene, *, respect_enabled: bool = True) -> None:
    current = str(getattr(scene, "bmanga_page_preview_range_mode", PAGE_PREVIEW_RANGE_ALL) or PAGE_PREVIEW_RANGE_ALL)
    row = layout.row(align=True)
    if respect_enabled:
        row.enabled = bool(getattr(scene, "bmanga_page_preview_enabled", True))
    op = row.operator(
        "bmanga.page_preview_range_mode_set",
        text="全ページ",
        depress=current == PAGE_PREVIEW_RANGE_ALL,
    )
    op.mode = PAGE_PREVIEW_RANGE_ALL
    op = row.operator(
        "bmanga.page_preview_range_mode_set",
        text="前後ページ",
        depress=current == PAGE_PREVIEW_RANGE_NEAR,
    )
    op.mode = PAGE_PREVIEW_RANGE_NEAR


def _draw_page_image_row(layout, settings) -> None:
    if settings is None:
        return
    row = layout.row(align=True)
    own_vis = bool(getattr(settings, "own_page_visible", True))
    row.prop(
        settings,
        "own_page_visible",
        text="ページ画像",
        icon="HIDE_OFF" if own_vis else "HIDE_ON",
        toggle=True,
    )
    row.prop(settings, "own_page_opacity", text="")


def _draw_coma_content_row(layout, scene, settings) -> None:
    row = layout.row(align=True)
    content_vis = bool(getattr(scene, "bmanga_coma_content_visible", True))
    row.prop(
        scene,
        "bmanga_coma_content_visible",
        text="コマ内レイヤー",
        icon="HIDE_OFF" if content_vis else "HIDE_ON",
        toggle=True,
    )
    if settings is not None:
        row.prop(settings, "koma_bg_images_opacity", text="")


def _draw_page_list_section(layout, scene, settings=None) -> None:
    row = layout.row(align=True)
    preview_vis = bool(getattr(scene, "bmanga_page_preview_enabled", True))
    row.prop(
        scene,
        "bmanga_page_preview_enabled",
        text="ページ一覧",
        icon="HIDE_OFF" if preview_vis else "HIDE_ON",
        toggle=True,
    )
    if settings is not None:
        row.prop(settings, "name_bg_images_opacity", text="")
    else:
        row.prop(scene, "bmanga_page_preview_opacity", text="")

    col = layout.column(align=True)
    col.enabled = preview_vis
    _draw_page_preview_range_buttons(col, scene, respect_enabled=False)
    row = col.row(align=True)
    row.prop(scene, "bmanga_overview_cols", text="列数")
    row = col.row(align=True)
    row.prop(scene, "bmanga_overview_gap_x_mm", text="横間隔mm")
    row.prop(scene, "bmanga_overview_gap_y_mm", text="縦間隔mm")
    if settings is not None:
        col.prop(settings, "bg_images_scale", text="ページ画像のスケール")


def _draw_coma_display_controls(layout, scene, settings) -> None:
    if settings is None:
        return
    box = layout.box()
    box.prop(scene, "bmanga_coma_grayscale_view", text="グレースケール表示")
    box.prop(scene, "bmanga_coma_white_background", text="背景を白にする")
    box.prop(settings, "white_background", text="背景を透過")
    box.prop(settings, "world_background_camera_only", text="ワールド背景色を被写体に影響させない")
    row = box.row(align=True)
    row.prop(settings, "use_solid_background_color", text="ソリッド背景色")
    sub = row.row(align=True)
    sub.enabled = bool(settings.use_solid_background_color)
    sub.prop(settings, "solid_background_color", text="")
    box.prop(settings, "subsurf_realtime", text="サブディビジョンサーフェス")
    box.prop(settings, "koma_depth", text="コマを後ろにする")
    box.prop(settings, "hatching_visible", text="ハッチング間隔を表示")
    row = box.row()
    row.enabled = bool(settings.hatching_visible)
    row.prop(settings, "hatching_rotation", text="ハッチング回転")
    box.operator("bmanga.coma_camera_update_view", text="ビューを更新")



class BMANGA_OT_page_preview_range_mode_set(Operator):
    bl_idname = "bmanga.page_preview_range_mode_set"
    bl_label = "ページ一覧の表示範囲を切り替え"
    bl_description = "ページ一覧を全ページ分表示するか、現在ページの前後1ページだけ表示するかを切り替えます"

    mode: StringProperty(default=PAGE_PREVIEW_RANGE_ALL)  # type: ignore[valid-type]

    def execute(self, context):
        scene = getattr(context, "scene", None)
        if scene is None:
            return {"CANCELLED"}
        mode = str(self.mode or PAGE_PREVIEW_RANGE_ALL).upper()
        if mode not in {PAGE_PREVIEW_RANGE_ALL, PAGE_PREVIEW_RANGE_NEAR}:
            mode = PAGE_PREVIEW_RANGE_ALL
        scene.bmanga_page_preview_range_mode = mode
        return {"FINISHED"}


class BMANGA_PT_view(Panel):
    bl_idname = "BMANGA_PT_view"
    bl_label = "ビュー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 14

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def draw(self, context):
        layout = self.layout
        mode = get_mode(context)
        is_coma_mode = mode == MODE_COMA
        scene = context.scene

        enabled = bool(getattr(scene, "bmanga_overlay_enabled", True))
        row = layout.row(align=True)
        row.operator(
            "bmanga.overlay_toggle",
            text="オーバーレイ表示 ON" if enabled else "オーバーレイ表示 OFF",
            icon="HIDE_OFF" if enabled else "HIDE_ON",
            depress=enabled,
        )

        layout.operator("bmanga.view_fit_page", text="ページに合わせる", icon="ZOOM_SELECTED")

        work = get_work(context)
        in_sub_file = page_file_scene.is_page_edit_scene(scene) or is_coma_mode
        if work is not None and work.loaded:
            row = layout.row(align=True)
            if in_sub_file:
                wi_vis = bool(getattr(scene, "bmanga_page_work_info_visible", True))
                row.prop(
                    scene,
                    "bmanga_page_work_info_visible",
                    text="作品情報",
                    icon="HIDE_OFF" if wi_vis else "HIDE_ON",
                    toggle=True,
                )
                gd_vis = bool(getattr(scene, "bmanga_page_guides_visible", True))
                row.prop(
                    scene,
                    "bmanga_page_guides_visible",
                    text="用紙ガイド",
                    icon="HIDE_OFF" if gd_vis else "HIDE_ON",
                    toggle=True,
                )
            else:
                info = work.work_info
                info_vis = bool(getattr(info, "display_visible", True))
                row.prop(
                    info,
                    "display_visible",
                    text="作品情報",
                    icon="HIDE_OFF" if info_vis else "HIDE_ON",
                    toggle=True,
                )
                guides_vis = bool(getattr(work.paper, "show_guides", True))
                row.prop(
                    work.paper,
                    "show_guides",
                    text="用紙ガイド",
                    icon="HIDE_OFF" if guides_vis else "HIDE_ON",
                    toggle=True,
                )

        if is_coma_mode:
            settings = getattr(scene, "bmanga_coma_camera_settings", None)
            _draw_page_image_row(layout, settings)
            _draw_coma_content_row(layout, scene, settings)
            _draw_page_list_section(layout, scene, settings)
            _draw_coma_display_controls(layout, scene, settings)

        if not is_coma_mode:
            in_page_file = page_file_scene.is_page_edit_scene(scene)
            if in_page_file:
                _draw_page_list_section(layout, scene)
            else:
                col = layout.column(align=True)
                row = col.row(align=True)
                row.operator("bmanga.view_fit_all", text="全ページを一覧", icon="IMGDISPLAY")
                row = col.row(align=True)
                row.prop(scene, "bmanga_overview_cols", text="列数")
                row = col.row(align=True)
                row.prop(scene, "bmanga_overview_gap_x_mm", text="横間隔mm")
                row.prop(scene, "bmanga_overview_gap_y_mm", text="縦間隔mm")
                row = col.row(align=True)
                row.prop(scene, "bmanga_active_page_number", text="選択ページ")


_CLASSES = (
    BMANGA_OT_page_preview_range_mode_set,
    BMANGA_PT_view,
)


def register() -> None:
    bpy.types.Scene.bmanga_page_work_info_visible = bpy.props.BoolProperty(
        name="作品情報表示",
        description="このページの作品情報 (作品名・話数・作者名・ノンブルなど) の表示を切り替えます",
        default=True,
        update=_page_work_info_visible_update,
    )
    bpy.types.Scene.bmanga_page_guides_visible = bpy.props.BoolProperty(
        name="用紙ガイド表示",
        description="このページの用紙ガイド (セーフライン・裁ち落とし枠・基本枠など) の表示を切り替えます",
        default=True,
        update=_page_guides_visible_update,
    )
    bpy.types.Scene.bmanga_coma_content_visible = bpy.props.BoolProperty(
        name="コマ内レイヤー表示",
        description="コマ内のフキダシ・フィル・テキストなどのレイヤーの表示を切り替えます",
        default=True,
        update=_coma_content_visible_update,
    )
    bpy.types.Scene.bmanga_active_page_number = bpy.props.IntProperty(
        name="選択ページ",
        min=1,
        get=_active_page_number_get,
        set=_active_page_number_set,
    )
    bpy.types.Scene.bmanga_page_preview_enabled = bpy.props.BoolProperty(
        name="ページ一覧表示",
        description="ページ編集中に、他のページを軽い縮小画像で表示します",
        default=True,
        update=_page_preview_enabled_update,
    )
    bpy.types.Scene.bmanga_page_preview_page_radius = bpy.props.IntProperty(
        name="旧ページ一覧半径",
        description="旧バージョンの保存データを読み込むための互換用設定です",
        default=3,
        min=0,
        soft_max=20,
        options={"HIDDEN"},
        update=_page_preview_enabled_update,
    )
    bpy.types.Scene.bmanga_page_preview_range_mode = bpy.props.EnumProperty(
        name="ページ一覧表示範囲",
        description="ページ一覧を全ページ分表示するか、現在ページの前後1ページだけ表示するかを選びます",
        items=(
            (PAGE_PREVIEW_RANGE_ALL, "全ページ", "全ページ分を表示します"),
            (PAGE_PREVIEW_RANGE_NEAR, "前後ページ", "現在ページの前後1ページだけ表示します"),
        ),
        default=PAGE_PREVIEW_RANGE_ALL,
        update=_page_preview_enabled_update,
    )
    bpy.types.Scene.bmanga_page_preview_opacity = bpy.props.FloatProperty(
        name="ページ一覧不透明度",
        description="ページファイルでのページ一覧プレビュー画像の不透明度です",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_page_preview_enabled_update,
    )
    bpy.types.Scene.bmanga_page_preview_resolution_percentage = bpy.props.FloatProperty(
        name="画像解像度%",
        description="ページプレビュー画像の細かさ。ページ実解像度 (用紙サイズ×DPI) に対する割合で指定します (長辺1536pxが上限)",
        default=25.0,
        min=5.0,
        soft_max=100.0,
        max=200.0,
        subtype="PERCENTAGE",
        update=_page_preview_enabled_update,
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.Scene.bmanga_active_page_number
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_page_preview_enabled
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_page_preview_page_radius
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_page_preview_range_mode
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_page_preview_opacity
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_page_preview_resolution_percentage
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_coma_content_visible
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_page_work_info_visible
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bmanga_page_guides_visible
    except AttributeError:
        pass
