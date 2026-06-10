"""レイヤー一覧のリンク操作."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import layer_links
from ..utils import layer_stack as layer_stack_utils


def _selected_link_items(context, stack, uids: list[str]) -> dict[str, object]:
    selected = set(uids)
    return {
        layer_stack_utils.stack_item_uid(item): item
        for item in stack or []
        if layer_stack_utils.stack_item_uid(item) in selected
    }


def _ordered_items_for_kind(context, stack, uids: list[str], kind: str) -> list[object]:
    items_by_uid = _selected_link_items(context, stack, uids)
    active = layer_stack_utils.active_stack_item(context)
    active_uid = layer_stack_utils.stack_item_uid(active) if active is not None else ""
    ordered: list[object] = []
    if active_uid in items_by_uid and str(getattr(items_by_uid[active_uid], "kind", "") or "") == kind:
        ordered.append(items_by_uid[active_uid])
    for uid in uids:
        item = items_by_uid.get(uid)
        if item is None or item in ordered:
            continue
        if str(getattr(item, "kind", "") or "") == kind:
            ordered.append(item)
    return ordered


def _sync_linked_balloon_items(context, items: list[object]) -> int:
    if len(items) < 2:
        return 0
    from . import layer_link_duplicate_op

    resolved = layer_stack_utils.resolve_stack_item(context, items[0])
    source = resolved.get("target") if resolved is not None else None
    page = resolved.get("page") if resolved is not None else None
    if source is None:
        return 0
    changed = 0
    skip_uids: set[str] = set()
    for item in items[1:]:
        uid = layer_stack_utils.stack_item_uid(item)
        if uid:
            skip_uids.add(uid)
        target_resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = target_resolved.get("target") if target_resolved is not None else None
        target_page = target_resolved.get("page") if target_resolved is not None else None
        if layer_link_duplicate_op.sync_balloon_transform_to_target(
            context,
            page,
            source,
            target_page,
            target,
        ):
            changed += 1
    changed += layer_link_duplicate_op.propagate_linked_balloon_transform_absolute(
        context,
        page,
        source,
        skip_uids=skip_uids,
    )
    return changed


def _sync_linked_effect_items(context, items: list[object]) -> int:
    if len(items) < 2:
        return 0
    from . import effect_line_link_op

    effect_layers: list[tuple[object, object]] = []
    for item in items:
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        if resolved is None:
            continue
        effect_layers.append((resolved.get("object"), resolved.get("target")))
    return effect_line_link_op.link_existing_effect_layers(context, effect_layers)


def _sync_new_link_transforms(context, stack, uids: list[str]) -> None:
    for kind, sync in (
        ("balloon", _sync_linked_balloon_items),
        ("effect", _sync_linked_effect_items),
    ):
        items = _ordered_items_for_kind(context, stack, uids, kind)
        if len(items) >= 2:
            sync(context, items)


class BNAME_OT_layer_stack_link_selected(Operator):
    bl_idname = "bname.layer_stack_link_selected"
    bl_label = "選択レイヤーをリンク"
    bl_description = "選択中のレイヤー同士をリンクします"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return layer_links.selected_linkable_count(context) >= 2

    def execute(self, context):
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        uids = layer_links.selected_linkable_uids(context, stack=stack)
        _group_id, count = layer_links.link_uids(context, uids)
        if count < 2:
            self.report({"WARNING"}, "リンクするレイヤーを2つ以上選択してください")
            return {"CANCELLED"}
        _sync_new_link_transforms(context, stack, uids)
        layer_links.expand_linked_selection(context, stack=stack)
        layer_stack_utils.tag_view3d_redraw(context)
        self.report({"INFO"}, f"{count}件のレイヤーをリンクしました")
        return {"FINISHED"}


_CLASSES = (BNAME_OT_layer_stack_link_selected,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
