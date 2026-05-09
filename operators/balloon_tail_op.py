"""フキダシしっぽ編集 Operator."""

from __future__ import annotations

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..utils import layer_stack as layer_stack_utils


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


_CLASSES = (
    BNAME_OT_balloon_tail_add_target,
    BNAME_OT_balloon_tail_remove,
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
