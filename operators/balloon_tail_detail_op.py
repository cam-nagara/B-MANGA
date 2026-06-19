"""しっぽの詳細設定ダイアログとしっぽプリセット管理.

フキダシの詳細設定から独立したダイアログで、しっぽの形状・線種・太さ等を
編集し、プリセットの適用・保存・削除を行う。
しっぽの線の色・太さ・塗り (下地) は親フキダシの設定に従う。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import tail_presets
from ..utils import balloon_tail_geom, log
from .balloon_tail_op import _find_balloon, _sync_after_tail_change

_logger = log.get_logger(__name__)

# 動的 EnumProperty の項目文字列は参照を保持しないと文字化けするため、
# 直近の項目リストをモジュール側で保持する (Blender の既知の挙動への対策)。
_ENUM_ITEMS_CACHE: dict[str, list] = {}
_SUPPRESS_TAIL_PRESET_REMEMBER = False


def _work_dir(context) -> Path | None:
    work = get_work(context)
    raw = str(getattr(work, "work_dir", "") or "") if work is not None else ""
    return Path(raw) if raw else None


def _tail_preset_enum_items(_self, context):
    items = []
    try:
        for preset in tail_presets.list_all_presets(_work_dir(context)):
            label = preset.name if preset.source == "user" else f"{preset.name} (同梱)"
            items.append((preset.name, label, preset.description or ""))
    except Exception:  # noqa: BLE001
        _logger.exception("tail preset enum build failed")
    if not items:
        items.append(("NONE", "—", ""))
    _ENUM_ITEMS_CACHE["all"] = items
    return items


def _remember_tail_preset(context, value: str) -> None:
    if _SUPPRESS_TAIL_PRESET_REMEMBER:
        return
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
        if prefs is None:
            return
        prefs.last_tail_preset = str(value or "")
        addon_preferences.request_user_preferences_save()
    except Exception:  # noqa: BLE001
        _logger.debug("tail preset selection remember failed", exc_info=True)


def _on_tail_preset_selector_change(self, context):
    value = str(getattr(self, "bmanga_tail_preset_selector", "") or "")
    _remember_tail_preset(context, value)


def restore_tail_preset_selector(context) -> None:
    """前回選んだしっぽプリセットを選択欄へ戻す."""
    global _SUPPRESS_TAIL_PRESET_REMEMBER
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_tail_preset_selector"):
        return
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
    except Exception:  # noqa: BLE001
        prefs = None
    value = str(getattr(prefs, "last_tail_preset", "") or "") if prefs is not None else ""
    if not value:
        return
    try:
        valid = {str(item[0]) for item in _tail_preset_enum_items(None, context)}
    except Exception:  # noqa: BLE001
        _logger.debug("tail preset restore items failed", exc_info=True)
        return
    if value not in valid or str(getattr(wm, "bmanga_tail_preset_selector", "") or "") == value:
        return
    _SUPPRESS_TAIL_PRESET_REMEMBER = True
    try:
        wm.bmanga_tail_preset_selector = value
    finally:
        _SUPPRESS_TAIL_PRESET_REMEMBER = False


def _local_tail_preset_enum_items(_self, context):
    items = []
    try:
        work_dir = _work_dir(context)
        if work_dir is not None:
            for preset in tail_presets.list_local_presets(work_dir):
                items.append((preset.name, preset.name, preset.description or ""))
    except Exception:  # noqa: BLE001
        _logger.exception("local tail preset enum build failed")
    items = items or [("NONE", "(削除できるプリセットなし)", "")]
    _ENUM_ITEMS_CACHE["local"] = items
    return items


class BMANGA_OT_balloon_tail_preset_apply(Operator):
    bl_idname = "bmanga.balloon_tail_preset_apply"
    bl_label = "しっぽプリセットを適用"
    bl_description = "選んだプリセットの設定をこのしっぽへ適用します (位置とポイントは保持)"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "preset_name"

    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    tail_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: bpy.props.EnumProperty(name="プリセット", items=_tail_preset_enum_items)  # type: ignore[valid-type]

    def execute(self, context):
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if entry is None or not (0 <= self.tail_index < len(entry.tails)):
            self.report({"WARNING"}, "しっぽが見つかりません")
            return {"CANCELLED"}
        if str(self.preset_name or "NONE") == "NONE":
            return {"CANCELLED"}
        preset = tail_presets.load_preset_by_name(str(self.preset_name), _work_dir(context))
        if preset is None:
            self.report({"WARNING"}, f"プリセットが見つかりません: {self.preset_name}")
            return {"CANCELLED"}
        tail_presets.apply_preset_to_tail(preset, entry.tails[self.tail_index])
        _sync_after_tail_change(context, page, entry)
        self.report({"INFO"}, f"しっぽプリセットを適用しました: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_balloon_tail_preset_save(Operator):
    bl_idname = "bmanga.balloon_tail_preset_save"
    bl_label = "しっぽプリセットとして保存"
    bl_description = "このしっぽの設定一式を、全作品共通のしっぽプリセットとして保存します"
    bl_options = {"REGISTER"}

    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    tail_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        work_dir = _work_dir(context)
        if work_dir is None:
            self.report({"WARNING"}, "作品が開かれていません")
            return {"CANCELLED"}
        if not self.preset_name:
            self.preset_name = tail_presets.unique_preset_name(work_dir, "新規しっぽプリセット")
        return context.window_manager.invoke_props_dialog(self, width=280)

    def execute(self, context):
        work_dir = _work_dir(context)
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if work_dir is None or entry is None or not (0 <= self.tail_index < len(entry.tails)):
            self.report({"WARNING"}, "しっぽが見つかりません")
            return {"CANCELLED"}
        name = str(self.preset_name or "").strip()
        if not name:
            self.report({"WARNING"}, "プリセット名を入力してください")
            return {"CANCELLED"}
        del page
        tail_presets.save_local_preset(work_dir, entry.tails[self.tail_index], name, str(self.description or ""))
        self.report({"INFO"}, f"しっぽプリセットを保存しました: {name}")
        return {"FINISHED"}


class BMANGA_OT_balloon_tail_preset_delete(Operator):
    bl_idname = "bmanga.balloon_tail_preset_delete"
    bl_label = "しっぽプリセットを削除"
    bl_description = "共通保存したしっぽプリセットを削除します (同梱プリセットは削除できません)"
    bl_options = {"REGISTER"}
    bl_property = "preset_name"

    preset_name: bpy.props.EnumProperty(name="プリセット", items=_local_tail_preset_enum_items)  # type: ignore[valid-type]

    def execute(self, context):
        work_dir = _work_dir(context)
        name = str(self.preset_name or "")
        if work_dir is None or name in {"", "NONE"}:
            return {"CANCELLED"}
        if tail_presets.delete_local_preset(work_dir, name):
            self.report({"INFO"}, f"しっぽプリセットを削除しました: {name}")
            return {"FINISHED"}
        self.report({"WARNING"}, f"プリセットを削除できません: {name}")
        return {"CANCELLED"}


def _draw_tail_box(layout, context, page, entry, tail, tail_index: int) -> None:
    page_id = str(getattr(page, "id", "") or "")
    balloon_id = str(getattr(entry, "id", "") or "")
    box = layout.box()
    header = box.row(align=True)
    header.label(text=f"しっぽ {tail_index + 1}", icon="SHARPCURVE")
    remove = header.operator("bmanga.balloon_tail_remove", text="", icon="X")
    remove.page_id = page_id
    remove.balloon_id = balloon_id
    remove.tail_index = tail_index

    has_points = len(balloon_tail_geom.tail_local_points(tail)) >= 2
    row = box.row(align=True)
    row.prop(tail, "line_type", expand=True)
    if balloon_tail_geom.is_ellipse_chain(tail):
        row = box.row(align=True)
        row.prop(tail, "ellipse_gap_mm", text="間隔")
        row.prop(tail, "ellipse_angle_deg", text="角度")
        sub = box.column(align=True)
        sub.label(text="楕円の向き")
        sub.row(align=True).prop(tail, "ellipse_orient", expand=True)
    elif balloon_tail_geom.is_line_stroke(tail):
        row = box.row(align=True)
        row.prop(tail, "taper_in_percent", text="入り")
        row.prop(tail, "taper_out_percent", text="抜き")
    else:
        box.prop(tail, "sharp_corners")
    if has_points and len(tail.points) >= 3:
        row = box.row(align=True)
        row.prop(tail, "curve_mode", expand=True)
    if not has_points:
        box.prop(tail, "type")
        row = box.row(align=True)
        row.prop(tail, "direction_deg")
        row.prop(tail, "length_mm")
        if str(getattr(tail, "type", "") or "") == "curve":
            box.prop(tail, "curve_bend", slider=True)
    row = box.row(align=True)
    row.prop(tail, "root_width_mm")
    row.prop(tail, "tip_width_mm")

    preset_row = box.row(align=True)
    apply_op = preset_row.operator_menu_enum(
        BMANGA_OT_balloon_tail_preset_apply.bl_idname,
        "preset_name",
        text="プリセットを適用",
        icon="PRESET",
    )
    apply_op.page_id = page_id
    apply_op.balloon_id = balloon_id
    apply_op.tail_index = tail_index
    save_op = preset_row.operator(
        BMANGA_OT_balloon_tail_preset_save.bl_idname,
        text="",
        icon="FILE_TICK",
    )
    save_op.page_id = page_id
    save_op.balloon_id = balloon_id
    save_op.tail_index = tail_index


class BMANGA_OT_balloon_tail_detail_open(Operator):
    bl_idname = "bmanga.balloon_tail_detail_open"
    bl_label = "しっぽの詳細設定"
    bl_description = "しっぽの形状・線種・プリセットを編集します"
    bl_options = {"REGISTER"}

    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def invoke(self, context, _event):
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if entry is None:
            self.report({"WARNING"}, "フキダシが見つかりません")
            return {"CANCELLED"}
        del page
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if entry is None:
            layout.label(text="フキダシが見つかりません", icon="ERROR")
            return
        title = str(getattr(entry, "title", "") or getattr(entry, "id", "") or "フキダシ")
        layout.label(text=f"{title} のしっぽ", icon="MOD_FLUID")
        add = layout.operator("bmanga.balloon_tail_add_target", text="しっぽを追加", icon="ADD")
        add.page_id = str(getattr(page, "id", "") or "")
        add.balloon_id = str(getattr(entry, "id", "") or "")
        tails = list(getattr(entry, "tails", []) or [])
        if not tails:
            layout.label(text="しっぽがありません。しっぽツールでも作成できます", icon="INFO")
        for i, tail in enumerate(tails):
            _draw_tail_box(layout, context, page, entry, tail, i)
        note = layout.box()
        note.label(text="線の色・太さ・下地はフキダシの設定に従います", icon="INFO")
        layout.operator_menu_enum(
            BMANGA_OT_balloon_tail_preset_delete.bl_idname,
            "preset_name",
            text="プリセットを削除",
            icon="TRASH",
        )

    def execute(self, _context):
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_balloon_tail_preset_apply,
    BMANGA_OT_balloon_tail_preset_save,
    BMANGA_OT_balloon_tail_preset_delete,
    BMANGA_OT_balloon_tail_detail_open,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bmanga_tail_preset_selector = bpy.props.EnumProperty(
        name="しっぽプリセット",
        description="しっぽツールで新しく作るしっぽに適用するプリセット",
        items=_tail_preset_enum_items,
        update=_on_tail_preset_selector_change,
    )
    try:
        restore_tail_preset_selector(bpy.context)
    except Exception:  # noqa: BLE001
        _logger.exception("tail preset selector restore failed")


def unregister() -> None:
    try:
        del bpy.types.WindowManager.bmanga_tail_preset_selector
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
