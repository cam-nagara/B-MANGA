"""レイヤー一覧のリンク操作."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import layer_links
from ..utils import layer_stack as layer_stack_utils


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
        _group_id, count = layer_links.link_selected(context, stack=stack)
        if count < 2:
            self.report({"WARNING"}, "リンクするレイヤーを2つ以上選択してください")
            return {"CANCELLED"}
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
