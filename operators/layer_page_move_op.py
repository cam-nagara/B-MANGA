"""Move selected B-MANGA layers to another page."""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from ..core.work import get_work
from ..utils import layer_reparent
from ..utils import layer_stack as layer_stack_utils


_PAGE_ENUM_CACHE: list[tuple[str, str, str]] = []


def _target_page_items(_self, context):
    work = get_work(context)
    cache: list[tuple[str, str, str]] = []
    if work is not None:
        active_idx = int(getattr(work, "active_page_index", -1))
        for index, page in enumerate(getattr(work, "pages", []) or []):
            if index == active_idx:
                continue
            label = _page_label(work, page, index)
            cache.append((str(getattr(page, "id", "") or ""), label, ""))
    if not cache:
        cache.append(("", "(他のページなし)", ""))
    _PAGE_ENUM_CACHE[:] = cache
    return _PAGE_ENUM_CACHE


def _page_label(work, page, index: int) -> str:
    try:
        start = int(getattr(getattr(work, "work_info", None), "page_number_start", 1) or 1)
    except Exception:  # noqa: BLE001
        start = 1
    number = start + index
    title = str(getattr(page, "title", "") or "").strip()
    base = f"{number}ページ"
    return f"{base} {title}" if title else base


def _has_movable_selection(context) -> bool:
    stack = getattr(getattr(context, "scene", None), "bmanga_layer_stack", None)
    if stack is None:
        return False
    active = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
    if 0 <= active < len(stack) and str(getattr(stack[active], "kind", "") or "") != "page":
        return True
    return any(
        layer_stack_utils.is_item_selected(context, item)
        and str(getattr(item, "kind", "") or "") != "page"
        for item in stack
    )


class BMANGA_OT_layer_move_to_page(Operator):
    bl_idname = "bmanga.layer_move_to_page"
    bl_label = "別ページへ移動"
    bl_options = {"REGISTER", "UNDO"}

    target_page_id: EnumProperty(  # type: ignore[valid-type]
        name="移動先ページ",
        items=_target_page_items,
    )

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and bool(getattr(work, "loaded", False))
            and len(getattr(work, "pages", []) or []) >= 2
            and _has_movable_selection(context)
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        work = get_work(context)
        if work is None or not self.target_page_id:
            self.report({"ERROR"}, "移動先ページを選択してください")
            return {"CANCELLED"}
        target_page = None
        target_index = -1
        for index, page in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(page, "id", "") or "") == self.target_page_id:
                target_page = page
                target_index = index
                break
        if target_page is None:
            self.report({"ERROR"}, "移動先ページが見つかりません")
            return {"CANCELLED"}
        target = layer_reparent.ClickTarget(
            "page",
            target_page,
            None,
            target_index,
            None,
            None,
        )
        changed = layer_reparent.reparent_selected(context, target)
        if changed <= 0:
            self.report({"INFO"}, "移動できるレイヤーがありません")
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, f"{changed} 件を別ページへ移動しました")
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_layer_move_to_page,)


def _draw_layer_move_menu(self, context) -> None:
    if not BMANGA_OT_layer_move_to_page.poll(context):
        return
    layout = self.layout
    layout.separator()
    layout.operator(BMANGA_OT_layer_move_to_page.bl_idname, text="別ページへ移動", icon="FILE_PARENT")


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    try:
        bpy.types.UI_MT_list_item_context_menu.append(_draw_layer_move_menu)
    except Exception:  # noqa: BLE001
        pass


def unregister() -> None:
    try:
        bpy.types.UI_MT_list_item_context_menu.remove(_draw_layer_move_menu)
    except Exception:  # noqa: BLE001
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
