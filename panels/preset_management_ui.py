"""プリセット管理 UI の共通描画ヘルパー."""

from __future__ import annotations

from pathlib import Path

from ..core.work import get_work
from ..io import effect_line_presets, image_path_presets


def _work_dir(context) -> Path | None:
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False) or not getattr(work, "work_dir", ""):
        return None
    return Path(work.work_dir)


def _selected(context, attr: str) -> str:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, attr):
        return ""
    return str(getattr(wm, attr, "") or "")


def draw_image_path_preset_management(layout, context) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_image_path_tool_preset_selector"):
        return
    selected = _selected(context, "bmanga_image_path_tool_preset_selector")
    work_dir = _work_dir(context)
    box = layout.box()
    box.label(text="パターンカーブプリセット", icon="PRESET")
    box.prop(wm, "bmanga_image_path_tool_preset_selector", text="")
    row = box.row(align=True)
    row.operator("bmanga.image_path_preset_add_local", text="", icon="ADD")
    op = row.operator("bmanga.image_path_preset_rename", text="", icon="GREASEPENCIL")
    op.preset_name = selected
    op.new_name = selected
    op = row.operator("bmanga.image_path_preset_duplicate", text="", icon="DUPLICATE")
    op.preset_name = selected
    op.new_name = image_path_presets.unique_preset_name(work_dir, f"{selected} コピー") if selected else ""
    op = row.operator("bmanga.image_path_preset_delete", text="", icon="TRASH")
    op.preset_name = selected


def draw_effect_line_preset_management(layout, context) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_effect_line_tool_preset_selector"):
        return
    selected = _selected(context, "bmanga_effect_line_tool_preset_selector")
    work_dir = _work_dir(context)
    box = layout.box()
    box.label(text="効果線プリセット", icon="PRESET")
    box.prop(wm, "bmanga_effect_line_tool_preset_selector", text="")
    row = box.row(align=True)
    row.operator("bmanga.effect_line_preset_add_local", text="", icon="ADD")
    op = row.operator("bmanga.effect_line_preset_rename", text="", icon="GREASEPENCIL")
    op.preset_name = selected
    op.new_name = selected
    op = row.operator("bmanga.effect_line_preset_duplicate", text="", icon="DUPLICATE")
    op.preset_name = selected
    op.new_name = effect_line_presets.unique_preset_name(work_dir, f"{selected} コピー") if selected else ""
    op = row.operator("bmanga.effect_line_preset_delete", text="", icon="TRASH")
    op.preset_name = selected


def draw_balloon_preset_management(layout, context) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_balloon_tool_preset_selector"):
        return
    box = layout.box()
    box.label(text="フキダシプリセット", icon="PRESET")
    box.prop(wm, "bmanga_balloon_tool_preset_selector", text="")
    box.operator("bmanga.balloon_save_preset", text="現在の形状を保存", icon="FILE_TICK")


def draw_text_preset_selection(layout, context) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_text_tool_preset_selector"):
        return
    box = layout.box()
    box.label(text="テキストプリセット", icon="PRESET")
    box.prop(wm, "bmanga_text_tool_preset_selector", text="")


def draw_fill_preset_selection(layout, context, *, gradient: bool = False) -> None:
    wm = getattr(context, "window_manager", None)
    attr = "bmanga_gradient_tool_preset_selector" if gradient else "bmanga_fill_tool_preset_selector"
    if wm is None or not hasattr(wm, attr):
        return
    box = layout.box()
    box.label(text="グラデーションプリセット" if gradient else "囲い塗りプリセット", icon="PRESET")
    box.prop(wm, attr, text="")
