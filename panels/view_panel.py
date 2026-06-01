"""N-Panel の B-Name タブ: ビュー操作."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import page_browser, page_file_scene

B_NAME_CATEGORY = "B-Name"


def _active_page_number_get(scene) -> int:
    work = getattr(scene, "bname_work", None)
    info = getattr(work, "work_info", None) if work is not None else None
    start = int(getattr(info, "page_number_start", 1) or 1) if info is not None else 1
    idx = int(getattr(work, "active_page_index", 0) or 0) if work is not None else 0
    if work is None or not getattr(work, "loaded", False) or len(getattr(work, "pages", [])) == 0:
        return start
    idx = max(0, min(len(work.pages) - 1, idx))
    return start + idx


def _active_page_number_set(scene, value: int) -> None:
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False) or len(getattr(work, "pages", [])) == 0:
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
    scene.bname_overview_mode = True
    scene.bname_current_coma_id = ""
    scene.bname_current_coma_page_id = ""
    if hasattr(scene, "bname_active_layer_kind"):
        scene.bname_active_layer_kind = "page"
    try:
        from ..utils import edge_selection, layer_stack

        edge_selection.clear_selection(bpy.context)
        layer_stack.sync_layer_stack_after_data_change(bpy.context)
    except Exception:  # noqa: BLE001
        pass


def _page_preview_enabled_update(scene, context) -> None:
    try:
        from ..utils import page_preview_object

        page_preview_object.sync_page_previews(context, getattr(scene, "bname_work", None))
    except Exception:  # noqa: BLE001
        pass


class BNAME_PT_view(Panel):
    bl_idname = "BNAME_PT_view"
    bl_label = "ビュー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 4

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and get_mode(context) != MODE_COMA)

    def draw(self, context):
        layout = self.layout
        mode = get_mode(context)
        is_coma_mode = mode == MODE_COMA
        scene = context.scene

        enabled = bool(getattr(scene, "bname_overlay_enabled", True))
        row = layout.row(align=True)
        row.operator(
            "bname.overlay_toggle",
            text="オーバーレイ表示 ON" if enabled else "オーバーレイ表示 OFF",
            icon="HIDE_OFF" if enabled else "HIDE_ON",
            depress=enabled,
        )

        col = layout.column(align=True)
        col.enabled = not is_coma_mode
        row = col.row(align=True)
        row.operator("bname.view_fit_page", text="ページに合わせる", icon="ZOOM_SELECTED")
        row.operator("bname.view_fit_all", text="全ページを一覧", icon="IMGDISPLAY")
        row = col.row(align=True)
        row.prop(scene, "bname_overview_cols", text="列数")
        row.prop(scene, "bname_overview_gap_mm", text="間隔mm")
        row = col.row(align=True)
        row.prop(scene, "bname_active_page_number", text="選択ページ")
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        if role == page_file_scene.ROLE_PAGE:
            row = col.row(align=True)
            row.prop(scene, "bname_page_preview_enabled", text="ページ一覧表示")

        if mode != MODE_PAGE:
            layout.separator()
            box = layout.box()
            box.label(text="ページ一覧ビュー", icon="WINDOW")
            box.prop(scene, "bname_page_browser_position", text="位置")
            box.prop(scene, "bname_page_browser_size", text="サイズ")
            box.prop(scene, "bname_page_browser_fit", text="フィット")
            row = box.row(align=True)
            row.operator("bname.page_browser_workspace", text="専用ワークスペース", icon="WINDOW")
            row.operator("bname.page_browser_mark_area", text="", icon="IMGDISPLAY")
            if page_browser.is_page_browser_area(context):
                box.label(text="この3Dビューはページ一覧です", icon="CHECKMARK")


_CLASSES = (
    BNAME_PT_view,
)


def register() -> None:
    bpy.types.Scene.bname_active_page_number = bpy.props.IntProperty(
        name="選択ページ",
        min=1,
        get=_active_page_number_get,
        set=_active_page_number_set,
    )
    bpy.types.Scene.bname_page_preview_enabled = bpy.props.BoolProperty(
        name="ページ一覧表示",
        description="ページ編集中に、他のページを軽い縮小画像で表示します",
        default=True,
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
        del bpy.types.Scene.bname_active_page_number
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bname_page_preview_enabled
    except AttributeError:
        pass
