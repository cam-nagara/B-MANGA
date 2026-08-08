"""統合レイヤーを10行分まとめて前面／背面へ移動するOperator。"""

from __future__ import annotations

from bpy.props import EnumProperty
from bpy.types import Operator

from ..utils import layer_stack as layer_stack_utils

_MOVE_STEPS = 10


def _stack_index_for_uid(stack, uid: str) -> int:
    for index, item in enumerate(stack or []):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index
    return -1


class BMANGA_OT_layer_stack_move_ten(Operator):
    bl_idname = "bmanga.layer_stack_move_ten"
    bl_label = "レイヤーを10段移動"
    bl_description = "選択中のレイヤーを10レイヤー分まとめて移動します"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(  # type: ignore[valid-type]
        items=(
            ("UP", "10レイヤー前面へ", "10レイヤー分、前面へ移動します"),
            ("DOWN", "10レイヤー背面へ", "10レイヤー分、背面へ移動します"),
        ),
        default="UP",
    )

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        return stack is not None and len(stack) > 0

    @classmethod
    def description(cls, _context, properties):
        if str(getattr(properties, "direction", "") or "") == "DOWN":
            return "選択中のレイヤーを10レイヤー分、背面へ移動します"
        return "選択中のレイヤーを10レイヤー分、前面へ移動します"

    def execute(self, context):
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        active_index = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        if stack is None or not (0 <= active_index < len(stack)):
            return {"CANCELLED"}
        moved_uid = layer_stack_utils.stack_item_uid(stack[active_index])
        moved_count = 0
        for _step in range(_MOVE_STEPS):
            stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
            current_index = _stack_index_for_uid(stack, moved_uid)
            if current_index < 0:
                break
            if not layer_stack_utils.move_stack_item(
                context,
                current_index,
                direction=self.direction,
                commit=False,
            ):
                break
            moved_count += 1
        if not moved_count:
            return {"CANCELLED"}
        return (
            {"FINISHED"}
            if layer_stack_utils.commit_stack_order(context)
            else {"CANCELLED"}
        )


_CLASSES = (BMANGA_OT_layer_stack_move_ten,)


def register() -> None:
    import bpy

    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    import bpy

    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
