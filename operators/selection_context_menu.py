"""選択中要素向け右クリックメニューの起動ヘルパ."""

from __future__ import annotations

import bpy

from ..utils import layer_stack as layer_stack_utils, object_selection


def _call_selection_menu(context) -> bool:
    if layer_stack_utils.active_stack_item(context) is None:
        return False
    try:
        bpy.ops.wm.call_menu(name="BNAME_MT_selection_context")
    except Exception:  # noqa: BLE001
        return False
    return True


def _right_click_selection_mode(context, hit: dict, event) -> str:
    if bool(getattr(event, "ctrl", False)):
        return "toggle"
    if bool(getattr(event, "shift", False)):
        return "add"
    key = str(hit.get("key", "") or "")
    selected = object_selection.get_keys(context)
    if key and key in selected and len(selected) >= 2:
        return "add"
    if _hit_is_selected_in_layer_stack(context, key):
        return "add"
    return "single"


def _hit_is_selected_in_layer_stack(context, key: str) -> bool:
    if not key:
        return False
    try:
        from ..utils import layer_links

        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        selected_uids = set(layer_links.selected_linkable_uids(context, stack=stack, sync=False))
        if len(selected_uids) < 2:
            return False
        for item in stack or ():
            if layer_stack_utils.stack_item_uid(item) not in selected_uids:
                continue
            if layer_links.object_key_for_item(context, item) == key:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def open_for_viewport_object(context, event) -> bool:
    from . import object_tool_op

    hit = object_tool_op.hit_object_at_event(context, event)
    if hit is None:
        return _call_selection_menu(context)
    object_tool_op.activate_hit(context, hit, mode=_right_click_selection_mode(context, hit, event))
    return _call_selection_menu(context)


def open_for_object_tool(op, context, event) -> bool:
    _ = op
    return open_for_viewport_object(context, event)


def open_for_balloon_tool(context, event) -> bool:
    return open_for_viewport_object(context, event)


def open_for_text_tool(context, event) -> bool:
    return open_for_viewport_object(context, event)


def open_for_effect_tool(context, event) -> bool:
    return open_for_viewport_object(context, event)


def open_for_coma_edge_tool(op, context, event) -> bool:
    from . import coma_edge_move_op

    mx, my = op._to_window(event)
    hit = coma_edge_move_op._pick_edge_or_vertex(
        op._work,
        op._region,
        op._rv3d,
        mx,
        my,
        context=context,
        area=getattr(op, "_area", None),
    )
    if hit is not None:
        op._selection = hit
        op._update_wm_selection(context)
        page = op._work.pages[int(hit["page"])]
        panel = page.comas[int(hit["coma"])]
        object_selection.select_key(
            context,
            object_selection.coma_key(page, panel),
            mode="single",
        )
        return _call_selection_menu(context)
    return open_for_viewport_object(context, event)
