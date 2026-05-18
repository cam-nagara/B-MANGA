"""フキダシしっぽ編集 Operator."""

from __future__ import annotations

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Menu, Operator

from ..core.work import get_active_page, get_work
from ..utils import balloon_tail_geom, layer_stack as layer_stack_utils

_TAIL_POINT_CONTEXT = {
    "page_id": "",
    "balloon_id": "",
    "tail_index": -1,
    "point_index": -1,
}


def _find_balloon(context, page_id: str, balloon_id: str):
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None, None
    page_id = str(page_id or "")
    balloon_id = str(balloon_id or "")
    if page_id:
        for page in getattr(work, "pages", []):
            if str(getattr(page, "id", "") or "") != page_id:
                continue
            for entry in getattr(page, "balloons", []):
                if str(getattr(entry, "id", "") or "") == balloon_id:
                    return page, entry
            return None, None
    if balloon_id:
        for page in getattr(work, "pages", []):
            for entry in getattr(page, "balloons", []):
                if str(getattr(entry, "id", "") or "") == balloon_id:
                    return page, entry
        for entry in getattr(work, "shared_balloons", []):
            if str(getattr(entry, "id", "") or "") == balloon_id:
                return None, entry
    page = get_active_page(context)
    if page is not None:
        idx = int(getattr(page, "active_balloon_index", -1))
        if 0 <= idx < len(page.balloons):
            return page, page.balloons[idx]
    return None, None


def _sync_after_tail_change(context, page, entry) -> None:
    try:
        from ..utils import balloon_curve_object

        if page is not None:
            balloon_curve_object.ensure_balloon_curve_object(
                scene=context.scene,
                entry=entry,
                page=page,
            )
    except Exception:  # noqa: BLE001
        pass
    layer_stack_utils.sync_layer_stack_after_data_change(context)


def set_tail_point_context(page_id: str, balloon_id: str, tail_index: int, point_index: int) -> None:
    _TAIL_POINT_CONTEXT["page_id"] = str(page_id or "")
    _TAIL_POINT_CONTEXT["balloon_id"] = str(balloon_id or "")
    _TAIL_POINT_CONTEXT["tail_index"] = int(tail_index)
    _TAIL_POINT_CONTEXT["point_index"] = int(point_index)


def open_tail_point_context_menu(context, page_id: str, balloon_id: str, tail_index: int, point_index: int) -> bool:
    set_tail_point_context(page_id, balloon_id, tail_index, point_index)
    try:
        bpy.ops.wm.call_menu(name=BNAME_MT_balloon_tail_point_context.bl_idname)
        return True
    except Exception:  # noqa: BLE001
        return False


def _context_values(self) -> tuple[str, str, int, int]:
    page_id = str(getattr(self, "page_id", "") or _TAIL_POINT_CONTEXT["page_id"])
    balloon_id = str(getattr(self, "balloon_id", "") or _TAIL_POINT_CONTEXT["balloon_id"])
    tail_index = int(getattr(self, "tail_index", -1))
    point_index = int(getattr(self, "point_index", -1))
    if tail_index < 0:
        tail_index = int(_TAIL_POINT_CONTEXT["tail_index"])
    if point_index < 0:
        point_index = int(_TAIL_POINT_CONTEXT["point_index"])
    return page_id, balloon_id, tail_index, point_index


class BNAME_OT_balloon_tail_add_target(Operator):
    bl_idname = "bname.balloon_tail_add_target"
    bl_label = "しっぽを追加"
    bl_options = {"REGISTER", "UNDO"}

    page_id: StringProperty(name="ページID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(name="フキダシID", default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if entry is None:
            self.report({"WARNING"}, "フキダシが見つかりません")
            return {"CANCELLED"}
        tail = entry.tails.add()
        tail.type = "straight"
        tail.direction_deg = 270.0
        tail.length_mm = 6.0
        tail.root_width_mm = 3.0
        tail.tip_width_mm = 0.0
        tail.curve_bend = 0.0
        _sync_after_tail_change(context, page, entry)
        return {"FINISHED"}


class BNAME_OT_balloon_tail_remove(Operator):
    bl_idname = "bname.balloon_tail_remove"
    bl_label = "しっぽを削除"
    bl_options = {"REGISTER", "UNDO"}

    page_id: StringProperty(name="ページID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(name="フキダシID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    tail_index: IntProperty(name="しっぽ番号", default=-1, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if entry is None:
            self.report({"WARNING"}, "フキダシが見つかりません")
            return {"CANCELLED"}
        idx = int(self.tail_index)
        if not (0 <= idx < len(entry.tails)):
            self.report({"WARNING"}, "しっぽが見つかりません")
            return {"CANCELLED"}
        entry.tails.remove(idx)
        _sync_after_tail_change(context, page, entry)
        return {"FINISHED"}


class BNAME_OT_balloon_tail_point_delete(Operator):
    bl_idname = "bname.balloon_tail_point_delete"
    bl_label = "制御点を削除"
    bl_options = {"REGISTER", "UNDO"}

    page_id: StringProperty(name="ページID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(name="フキダシID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    tail_index: IntProperty(name="しっぽ番号", default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    point_index: IntProperty(name="制御点番号", default=-1, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        page_id, balloon_id, tail_index, point_index = _context_values(self)
        page, entry = _find_balloon(context, page_id, balloon_id)
        if entry is None or not (0 <= tail_index < len(entry.tails)):
            self.report({"WARNING"}, "しっぽが見つかりません")
            return {"CANCELLED"}
        tail = entry.tails[tail_index]
        if len(tail.points) <= 2 or point_index <= 0 or point_index >= len(tail.points) - 1:
            self.report({"WARNING"}, "始点と終点は削除できません")
            return {"CANCELLED"}
        tail.points.remove(point_index)
        balloon_tail_geom.sync_legacy_axis_fields(tail)
        _sync_after_tail_change(context, page, entry)
        return {"FINISHED"}


class BNAME_OT_balloon_tail_point_toggle_corner(Operator):
    bl_idname = "bname.balloon_tail_point_toggle_corner"
    bl_label = "角のタイプを切り替え"
    bl_options = {"REGISTER", "UNDO"}

    page_id: StringProperty(name="ページID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: StringProperty(name="フキダシID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    tail_index: IntProperty(name="しっぽ番号", default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    point_index: IntProperty(name="制御点番号", default=-1, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        page_id, balloon_id, tail_index, point_index = _context_values(self)
        page, entry = _find_balloon(context, page_id, balloon_id)
        if entry is None or not (0 <= tail_index < len(entry.tails)):
            self.report({"WARNING"}, "しっぽが見つかりません")
            return {"CANCELLED"}
        tail = entry.tails[tail_index]
        if not (0 <= point_index < len(tail.points)):
            self.report({"WARNING"}, "制御点が見つかりません")
            return {"CANCELLED"}
        point = tail.points[point_index]
        point.corner_type = "curve" if str(getattr(point, "corner_type", "line") or "line") == "line" else "line"
        _sync_after_tail_change(context, page, entry)
        return {"FINISHED"}


class BNAME_MT_balloon_tail_point_context(Menu):
    bl_idname = "BNAME_MT_balloon_tail_point_context"
    bl_label = "しっぽ制御点"

    def draw(self, context):
        layout = self.layout
        page_id = str(_TAIL_POINT_CONTEXT["page_id"])
        balloon_id = str(_TAIL_POINT_CONTEXT["balloon_id"])
        tail_index = int(_TAIL_POINT_CONTEXT["tail_index"])
        point_index = int(_TAIL_POINT_CONTEXT["point_index"])
        can_delete = False
        try:
            _page, entry = _find_balloon(context, page_id, balloon_id)
            if entry is not None and 0 <= tail_index < len(entry.tails):
                can_delete = 0 < point_index < len(entry.tails[tail_index].points) - 1
        except Exception:  # noqa: BLE001
            can_delete = False
        row = layout.row()
        row.enabled = can_delete
        op = row.operator(BNAME_OT_balloon_tail_point_delete.bl_idname, text="制御点を削除", icon="X")
        op.page_id = page_id
        op.balloon_id = balloon_id
        op.tail_index = tail_index
        op.point_index = point_index
        op = layout.operator(BNAME_OT_balloon_tail_point_toggle_corner.bl_idname, text="角のタイプを切り替え", icon="MOD_CURVE")
        op.page_id = page_id
        op.balloon_id = balloon_id
        op.tail_index = tail_index
        op.point_index = point_index


_CLASSES = (
    BNAME_OT_balloon_tail_add_target,
    BNAME_OT_balloon_tail_remove,
    BNAME_OT_balloon_tail_point_delete,
    BNAME_OT_balloon_tail_point_toggle_corner,
    BNAME_MT_balloon_tail_point_context,
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
