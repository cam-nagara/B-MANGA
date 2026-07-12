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


# ───────────────────────────���───────────────────────────��────────
# 統一プリセットリスト描画
# ────────────────────────────────────────────────────────────────

_PRESET_CONFIGS = {
    "border": {
        "label": "枠線プリセット",
        "icon": "MESH_PLANE",
        "selector_attr": "bmanga_border_preset_selector",
        "op_prefix": "bmanga.border_preset",
        "has_move": True,
        "has_save": False,
    },
    "balloon": {
        "label": "フキダシプリセット",
        "icon": "MESH_CIRCLE",
        "selector_attr": "bmanga_balloon_tool_preset_selector",
        "op_prefix": "bmanga.balloon_preset",
        "add_op": "bmanga.balloon_save_preset",
        "has_move": True,
        "has_save": False,
    },
    "text": {
        "label": "テキストプリセット",
        "icon": "FONT_DATA",
        "selector_attr": "bmanga_text_tool_preset_selector",
        "op_prefix": "bmanga.text_preset",
        "has_move": True,
        "has_save": True,
        "save_op": "bmanga.text_preset_save",
        "save_label": "上書き保存",
    },
    "effect_line": {
        "label": "効果線プリセット",
        "icon": "FORCE_FORCE",
        "selector_attr": "bmanga_effect_line_tool_preset_selector",
        "op_prefix": "bmanga.effect_line_preset",
        "has_move": False,
        "has_save": False,
    },
    "fill": {
        "label": "囲い塗りプリセット",
        "icon": "SNAP_FACE",
        "selector_attr": "bmanga_fill_tool_preset_selector",
        "op_prefix": "bmanga.fill_preset",
        "has_move": True,
        "has_save": True,
        "save_op": "bmanga.fill_preset_save",
        "save_label": "上書き保存",
    },
    "gradient": {
        "label": "グラデーションプリセット",
        "icon": "NODE_TEXTURE",
        "selector_attr": "bmanga_gradient_tool_preset_selector",
        "op_prefix": "bmanga.gradient_preset",
        "has_move": True,
        "has_save": True,
        "save_op": "bmanga.gradient_preset_save",
        "save_label": "上書き保存",
    },
    "image_path": {
        "label": "パターンカーブプリセット",
        "icon": "CURVE_BEZCURVE",
        "selector_attr": "bmanga_image_path_tool_preset_selector",
        "op_prefix": "bmanga.image_path_preset",
        "has_move": False,
        "has_save": False,
    },
    "tail": {
        "label": "しっぽプリセット",
        "icon": "SHARPCURVE",
        "selector_attr": "bmanga_tail_preset_selector",
        "op_prefix": None,
        "has_move": False,
        "has_save": False,
    },
}


def _get_selected_name(context, preset_type: str) -> str:
    """プリセットセレクタの選択中プリセット名を取得する."""
    cfg = _PRESET_CONFIGS.get(preset_type)
    if cfg is None:
        return ""
    value = _selected(context, cfg["selector_attr"])
    if preset_type == "balloon" and value.startswith("custom:"):
        return value.split(":", 1)[1]
    if preset_type == "balloon" and not value.startswith("custom:"):
        return ""
    if value == "NONE":
        return ""
    return value


def draw_preset_list(layout, context, preset_type: str, *, compact: bool = False) -> None:
    """統一プリセットリストを描画する.

    Parameters
    ----------
    layout : UILayout
    context : bpy.types.Context
    preset_type : str
        "border", "balloon", "text", "effect_line", "fill", "gradient", "image_path", "tail"
    compact : bool
        True ならボックスなしでコンパクトに描画（ツールパネル用）
    """
    cfg = _PRESET_CONFIGS.get(preset_type)
    if cfg is None:
        return
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, cfg["selector_attr"]):
        return

    if compact:
        container = layout
    else:
        container = layout.box()
        container.label(text=cfg["label"], icon=cfg["icon"])

    container.prop(wm, cfg["selector_attr"], text="")

    if cfg["op_prefix"] is None:
        return

    selected = _get_selected_name(context, preset_type)
    op_prefix = cfg["op_prefix"]

    # CRUD ボタン行
    row = container.row(align=True)
    add_op = cfg.get("add_op") or f"{op_prefix}_add_local"
    row.operator(add_op, text="", icon="ADD")
    if selected:
        op = row.operator(f"{op_prefix}_rename", text="", icon="GREASEPENCIL")
        op.preset_name = selected
        op.new_name = selected
        op = row.operator(f"{op_prefix}_duplicate", text="", icon="DUPLICATE")
        op.preset_name = selected
        op.new_name = f"{selected} コピー"
        op = row.operator(f"{op_prefix}_delete", text="", icon="TRASH")
        op.preset_name = selected
    else:
        row.label(text="")
        row.label(text="")
        row.label(text="")

    # 上書き保存ボタン
    if cfg.get("has_save") and cfg.get("save_op"):
        container.operator(cfg["save_op"], text=cfg.get("save_label", "上書き保存"), icon="FILE_TICK")

    # 並べ替えボタン
    if cfg.get("has_move") and selected:
        move_row = container.row(align=True)
        op = move_row.operator(f"{op_prefix}_move", text="", icon="TRIA_UP")
        op.preset_name = selected
        op.direction = "UP"
        op = move_row.operator(f"{op_prefix}_move", text="", icon="TRIA_DOWN")
        op.preset_name = selected
        op.direction = "DOWN"


# ────────────────────────────────────────────────────────────────
# 旧API互換（既存呼び出し元がまだ使う可能性）
# ────────────────────────────────────────────────────────────────

def draw_image_path_preset_management(layout, context) -> None:
    draw_preset_list(layout, context, "image_path")


def draw_effect_line_preset_management(layout, context) -> None:
    draw_preset_list(layout, context, "effect_line")


def draw_balloon_preset_management(layout, context) -> None:
    draw_preset_list(layout, context, "balloon")


def draw_text_preset_selection(layout, context) -> None:
    draw_preset_list(layout, context, "text")


def draw_fill_preset_selection(layout, context, *, gradient: bool = False) -> None:
    draw_preset_list(layout, context, "gradient" if gradient else "fill")
