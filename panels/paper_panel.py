"""N-Panel の B-MANGA タブ: 用紙設定・セーフラインオーバーレイ."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import page_file_scene
from ..utils import shortcut_visibility

B_NAME_CATEGORY = "B-MANGA"


def _role(context) -> str:
    try:
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        return role
    except Exception:  # noqa: BLE001
        return page_file_scene.ROLE_UNKNOWN


def _is_work_file_context(context) -> bool:
    role = _role(context)
    if role == page_file_scene.ROLE_WORK:
        return True
    if role in {page_file_scene.ROLE_PAGE, page_file_scene.ROLE_COMA}:
        return False
    return page_file_scene.is_work_list_scene(getattr(context, "scene", None))


def _is_page_file_context(context) -> bool:
    role = _role(context)
    if role == page_file_scene.ROLE_PAGE:
        return True
    if role in {page_file_scene.ROLE_WORK, page_file_scene.ROLE_COMA}:
        return False
    return page_file_scene.is_page_edit_scene(getattr(context, "scene", None))


def _is_coma_file_context(context) -> bool:
    role = _role(context)
    if role == page_file_scene.ROLE_COMA:
        return True
    if role in {page_file_scene.ROLE_WORK, page_file_scene.ROLE_PAGE}:
        return False
    return get_mode(context) == MODE_COMA or shortcut_visibility.current_blend_is_coma_blend()


def _paper_unit_label(paper) -> str:
    unit = str(getattr(paper, "unit", "mm") or "mm")
    if unit == "px":
        return "px"
    if unit == "inch":
        return "inch"
    return "mm"


class BMANGA_PT_paper(Panel):
    bl_idname = "BMANGA_PT_paper"
    bl_label = "用紙"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 12
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and _is_work_file_context(context))

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None:
            return
        p = work.paper
        unit_label = _paper_unit_label(p)

        # プリセット操作 (ドロップダウンから選択 → 即時適用)
        row = layout.row(align=True)
        row.label(text="プリセット", icon="PRESET")
        wm = context.window_manager
        row.prop(wm, "bmanga_paper_preset_selector", text="")
        row.operator("bmanga.paper_preset_save_local", text="", icon="FILE_TICK")

        box = layout.box()
        box.label(text="キャンバス")
        row = box.row(align=True)
        row.prop(p, "canvas_width_value", text=f"幅 ({unit_label})")
        row.prop(p, "canvas_height_value", text=f"高さ ({unit_label})")
        row = box.row(align=True)
        row.prop(p, "dpi")
        row.prop(p, "unit", text="")

        box = layout.box()
        box.label(text="仕上がり / 裁ち落とし")
        row = box.row(align=True)
        row.prop(p, "finish_width_value", text=f"幅 ({unit_label})")
        row.prop(p, "finish_height_value", text=f"高さ ({unit_label})")
        box.prop(p, "bleed_value", text=f"裁ち落とし幅 ({unit_label})")
        sa = work.safe_area_overlay
        row = box.row(align=True)
        row.prop(sa, "bleed_outer_enabled", text="裁ち落とし枠外を塗る")
        sub = box.row(align=True)
        sub.enabled = sa.bleed_outer_enabled
        sub.prop(sa, "bleed_outer_color", text="")
        sub.prop(sa, "bleed_outer_opacity", text="不透明度", slider=True)

        box = layout.box()
        box.label(text="基本枠")
        row = box.row(align=True)
        row.prop(p, "inner_frame_width_value", text=f"幅 ({unit_label})")
        row.prop(p, "inner_frame_height_value", text=f"高さ ({unit_label})")
        row = box.row(align=True)
        row.prop(p, "inner_frame_offset_x_value", text=f"横オフセット ({unit_label})")
        row.prop(p, "inner_frame_offset_y_value", text=f"縦オフセット ({unit_label})")
        box.prop(p, "coma_border_width_mm")

        box = layout.box()
        box.label(text="セーフライン")
        row = box.row(align=True)
        row.prop(p, "safe_top_value", text=f"天 ({unit_label})")
        row.prop(p, "safe_bottom_value", text=f"地 ({unit_label})")
        row = box.row(align=True)
        row.prop(p, "safe_gutter_value", text=f"ノド ({unit_label})")
        row.prop(p, "safe_fore_edge_value", text=f"小口 ({unit_label})")
        # セーフライン外塗り (旧「セーフライン外オーバーレイ」パネル) を統合
        row = box.row(align=True)
        row.prop(sa, "enabled", text="セーフライン外を塗る")
        sub = box.row(align=True)
        sub.enabled = sa.enabled
        sub.prop(sa, "color", text="")
        sub.prop(sa, "opacity", text="不透明度", slider=True)

        box = layout.box()
        box.label(text="色")
        box.prop(p, "paper_color", text="用紙色")

        # 綴じ / 読む方向
        box = layout.box()
        box.label(text="綴じ / 読む方向")
        box.prop(p, "start_side")
        box.prop(p, "read_direction")

        box = layout.box()
        box.label(text="コマ間隔")
        g = work.coma_gap
        row = box.row(align=True)
        row.prop(g, "vertical_mm")
        row.prop(g, "horizontal_mm")


def draw_paper_visibility_controls(layout, paper) -> None:
    layout.prop(paper, "show_guides")
    guide_box = layout.column()
    guide_box.enabled = bool(getattr(paper, "show_guides", True))
    row = guide_box.row(align=True)
    row.prop(paper, "show_canvas_frame")
    row.prop(paper, "show_bleed_frame")
    row = guide_box.row(align=True)
    row.prop(paper, "show_finish_frame")
    row.prop(paper, "show_inner_frame")
    row = guide_box.row(align=True)
    row.prop(paper, "show_safe_line")
    row.prop(paper, "show_trim_marks")


class BMANGA_PT_work_paper_visibility(Panel):
    """作品ファイル上の用紙要素表示を用紙セクション外に出す."""

    bl_idname = "BMANGA_PT_work_paper_visibility"
    bl_label = "用紙要素の表示"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 13

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and _is_work_file_context(context))

    def draw(self, context):
        work = get_work(context)
        if work is None:
            return
        draw_paper_visibility_controls(self.layout, work.paper)


class BMANGA_PT_page_paper_visibility(Panel):
    """ページファイル上でも用紙要素の表示を切り替えられるようにする."""

    bl_idname = "BMANGA_PT_page_paper_visibility"
    bl_label = "用紙要素の表示"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 13

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and _is_page_file_context(context))

    def draw(self, context):
        work = get_work(context)
        if work is None:
            return
        draw_paper_visibility_controls(self.layout, work.paper)


class BMANGA_PT_coma_paper_visibility(Panel):
    """コマファイル上でも用紙要素の表示を切り替えられるようにする."""

    bl_idname = "BMANGA_PT_coma_paper_visibility"
    bl_label = "用紙要素の表示"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 15

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and _is_coma_file_context(context))

    def draw(self, context):
        work = get_work(context)
        if work is None:
            return
        draw_paper_visibility_controls(self.layout, work.paper)


_CLASSES = (
    BMANGA_PT_paper,
    BMANGA_PT_work_paper_visibility,
    BMANGA_PT_page_paper_visibility,
    BMANGA_PT_coma_paper_visibility,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
