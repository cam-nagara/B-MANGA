"""統合レイヤーリストのロック切替 Operator.

- ``BMANGA_OT_layer_stack_toggle_lock``: レイヤー一覧のカード上のロック
  アイコン用。単一行のロック状態を切り替える (表示切替ボタンと同じ
  index 方式)。
- ``BMANGA_OT_layer_stack_lock_selected``: レイヤー一覧右側のツール列
  ボタン用。選択中の全レイヤーのロック状態を一括切替する。
"""

from __future__ import annotations

import bpy
from bpy.props import IntProperty
from bpy.types import Operator

from ..utils import layer_lock
from ..utils import layer_stack as layer_stack_utils


class BMANGA_OT_layer_stack_toggle_lock(Operator):
    bl_idname = "bmanga.layer_stack_toggle_lock"
    bl_label = "レイヤーロックを切替"
    bl_description = "このレイヤーのロックを切り替えます"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        if stack is None or not (0 <= self.index < len(stack)):
            return {"CANCELLED"}
        item = stack[self.index]
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        if resolved is None or not layer_lock.is_lockable(item, resolved):
            return {"CANCELLED"}
        new_value = not layer_lock.get_locked(item, resolved)
        if not layer_lock.set_locked(item, resolved, new_value):
            return {"CANCELLED"}
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BMANGA_OT_layer_stack_lock_selected(Operator):
    bl_idname = "bmanga.layer_stack_lock_selected"
    bl_label = "選択レイヤーのロックを切替"
    bl_description = "選択中のレイヤーのロックを切り替え"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def _selected_lockable_targets(self, context, stack):
        targets = []
        for item in stack:
            if not layer_lock.is_lockable_kind(str(getattr(item, "kind", "") or "")):
                continue
            if not layer_stack_utils.is_item_selected(context, item):
                continue
            resolved = layer_stack_utils.resolve_stack_item(context, item)
            if resolved is None or not layer_lock.is_lockable(item, resolved):
                continue
            targets.append((item, resolved))
        return targets

    def execute(self, context):
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        if stack is None:
            return {"CANCELLED"}
        targets = self._selected_lockable_targets(context, stack)
        if not targets:
            self.report({"WARNING"}, "ロックを切り替えるレイヤーを選択してください")
            return {"CANCELLED"}
        all_locked = all(layer_lock.get_locked(item, resolved) for item, resolved in targets)
        new_value = not all_locked
        changed = 0
        for item, resolved in targets:
            if layer_lock.set_locked(item, resolved, new_value):
                changed += 1
        if not changed:
            return {"CANCELLED"}
        layer_stack_utils.tag_view3d_redraw(context)
        state = "ロック" if new_value else "ロック解除"
        self.report({"INFO"}, f"{changed}件のレイヤーを{state}しました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_layer_stack_toggle_lock,
    BMANGA_OT_layer_stack_lock_selected,
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
