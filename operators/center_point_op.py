"""選択中要素の中心点操作."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import layer_stack as layer_stack_utils


def _active_stack_kind(context) -> str:
    item = layer_stack_utils.active_stack_item(context)
    return str(getattr(item, "kind", "") or "")


class BMANGA_OT_reset_center_point(Operator):
    bl_idname = "bmanga.reset_center_point"
    bl_label = "中心点を中心へ戻す"
    bl_description = "選択中のフキダシまたは効果線の中心点を枠の中心に戻します"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        kind = _active_stack_kind(context)
        return kind in {"balloon", "effect", "effect_legacy"}

    def execute(self, context):
        kind = _active_stack_kind(context)
        if kind in {"effect", "effect_legacy"}:
            from . import effect_line_op

            if effect_line_op.reset_effect_center_to_bounds(context):
                return {"FINISHED"}
        if kind == "balloon":
            item = layer_stack_utils.active_stack_item(context)
            resolved = layer_stack_utils.resolve_stack_item(context, item) if item is not None else None
            entry = resolved.get("target") if resolved else None
            if entry is not None:
                if hasattr(entry, "center_offset_x_mm"):
                    entry.center_offset_x_mm = 0.0
                if hasattr(entry, "center_offset_y_mm"):
                    entry.center_offset_y_mm = 0.0
                try:
                    from . import layer_link_duplicate_op

                    layer_link_duplicate_op.propagate_linked_balloon_center_free(context, resolved.get("page"), entry)
                except Exception:  # noqa: BLE001
                    pass
                layer_stack_utils.sync_layer_stack_after_data_change(context)
                return {"FINISHED"}
        self.report({"WARNING"}, "中心点を戻す対象が見つかりません")
        return {"CANCELLED"}


_CLASSES = (BMANGA_OT_reset_center_point,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
