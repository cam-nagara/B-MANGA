"""N-Panel の B-Name タブ: 用紙設定・セーフラインオーバーレイ."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


def _paper_unit_label(paper) -> str:
    unit = str(getattr(paper, "unit", "mm") or "mm")
    if unit == "px":
        return "px"
    if unit == "inch":
        return "inch"
    return "mm"


class BNAME_PT_paper(Panel):
    bl_idname = "BNAME_PT_paper"
    bl_label = "用紙"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and get_mode(context) != MODE_COMA)

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
        row.prop(wm, "bname_paper_preset_selector", text="")
        row.operator("bname.paper_preset_save_local", text="", icon="FILE_TICK")

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

        box = layout.box()
        box.label(text="基本枠")
        row = box.row(align=True)
        row.prop(p, "inner_frame_width_value", text=f"幅 ({unit_label})")
        row.prop(p, "inner_frame_height_value", text=f"高さ ({unit_label})")
        row = box.row(align=True)
        row.prop(p, "inner_frame_offset_x_value", text=f"横オフセット ({unit_label})")
        row.prop(p, "inner_frame_offset_y_value", text=f"縦オフセット ({unit_label})")

        box = layout.box()
        box.label(text="セーフライン")
        row = box.row(align=True)
        row.prop(p, "safe_top_value", text=f"天 ({unit_label})")
        row.prop(p, "safe_bottom_value", text=f"地 ({unit_label})")
        row = box.row(align=True)
        row.prop(p, "safe_gutter_value", text=f"ノド ({unit_label})")
        row.prop(p, "safe_fore_edge_value", text=f"小口 ({unit_label})")
        # セーフライン外塗り (旧「セーフライン外オーバーレイ」パネル) を統合
        sa = work.safe_area_overlay
        row = box.row(align=True)
        row.prop(sa, "enabled", text="セーフライン外を塗る")
        sub = box.row(align=True)
        sub.enabled = sa.enabled
        sub.prop(sa, "color", text="")
        sub.prop(sa, "opacity", text="不透明度", slider=True)

        box = layout.box()
        box.label(text="色")
        box.prop(p, "paper_color", text="用紙色")

        box = layout.box()
        box.label(text="用紙要素の表示")
        box.prop(p, "show_guides")
        guide_box = box.column()
        guide_box.enabled = bool(getattr(p, "show_guides", True))
        row = guide_box.row(align=True)
        row.prop(p, "show_canvas_frame")
        row.prop(p, "show_bleed_frame")
        row = guide_box.row(align=True)
        row.prop(p, "show_finish_frame")
        row.prop(p, "show_inner_frame")
        row = guide_box.row(align=True)
        row.prop(p, "show_safe_line")
        row.prop(p, "show_trim_marks")

        # 綴じ / 読む方向
        box = layout.box()
        box.label(text="綴じ / 読む方向")
        box.prop(p, "start_side")
        box.prop(p, "read_direction")

        box = layout.box()
        box.label(text="原稿上の表示")
        box.operator("bname.work_meta_dialog", text="メタ情報を編集", icon="INFO")
        info = work.work_info
        _draw_display_item(box, "作品名", info.display_work_name)
        _draw_display_item(box, "話数", info.display_episode)
        _draw_display_item(box, "サブタイトル", info.display_subtitle)
        _draw_display_item(box, "作者名", info.display_author)
        _draw_display_item(box, "ページ番号", info.display_page_number)

        box = layout.box()
        box.label(text="コマ間隔")
        g = work.coma_gap
        row = box.row(align=True)
        row.prop(g, "vertical_mm")
        row.prop(g, "horizontal_mm")


def _draw_display_item(layout, label: str, item) -> None:
    row = layout.row(align=True)
    row.prop(item, "enabled", text=label)
    sub = layout.row(align=True)
    sub.enabled = item.enabled
    sub.prop(item, "position", text="")
    sub.prop(item, "font_size_unit", text="")
    sub.prop(item, "font_size_value", text="サイズ")


_CLASSES = (
    BNAME_PT_paper,
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
