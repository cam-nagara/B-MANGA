"""統合レイヤー一覧から開く詳細設定ダイアログ。"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..utils import layer_stack as layer_stack_utils
from ..utils.layer_hierarchy import COMA_KIND
from .detail_preset_apply_op import BMANGA_DetailPresetListItem


def _resolve_detail_stack_index(stack, requested_index: int, requested_uid: str) -> int:
    """再同期後の一覧でUIDを最優先し、別行へのすり替わりを防ぐ。"""

    uid = str(requested_uid or "").strip()
    if stack is None:
        return -1
    if uid:
        for index, item in enumerate(stack):
            if layer_stack_utils.stack_item_uid(item) == uid:
                return index
        return -1
    index = int(requested_index)
    return index if 0 <= index < len(stack) else -1


class BMANGA_OT_layer_stack_detail(Operator):
    bl_idname = "bmanga.layer_stack_detail"
    bl_label = "詳細設定"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    uid: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preserve_edge_selection: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]
    offset_from_selection: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]
    detail_preset_items: CollectionProperty(  # type: ignore[valid-type]
        type=BMANGA_DetailPresetListItem,
        options={"HIDDEN"},
    )
    detail_preset_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    detail_linked_balloon_items: CollectionProperty(  # type: ignore[valid-type]
        type=BMANGA_DetailPresetListItem,
        options={"HIDDEN"},
    )
    detail_linked_balloon_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def invoke(self, context, event):
        self._detail_session = None
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        index = _resolve_detail_stack_index(stack, self.index, self.uid)
        if stack is None or not (0 <= index < len(stack)):
            self.report({"ERROR"}, "詳細設定を開くレイヤーが見つかりません")
            return {"CANCELLED"}
        edge_state = self._capture_edge_selection(context)
        self.index = index
        self.uid = layer_stack_utils.stack_item_uid(stack[index])
        layer_stack_utils.select_stack_index(context, index)
        self._restore_edge_selection_if_needed(context, stack[index], edge_state)
        try:
            self._detail_session = self._begin_detail_session(context)
            layer_stack_utils.tag_view3d_redraw(context)
            self._offset_cursor_for_selection_popup(context, event)
            result = context.window_manager.invoke_props_dialog(
                self,
                width=self._detail_session.layout.dialog_width,
            )
        except Exception as exc:  # noqa: BLE001
            rollback_error = self._abort_opening_session(context)
            if rollback_error is not None:
                self.report({"ERROR"}, f"開始失敗後も元に戻せませんでした: {rollback_error}")
            else:
                self.report({"ERROR"}, f"詳細設定を開けません: {exc}")
            return {"CANCELLED"}
        if "CANCELLED" in result:
            rollback_error = self._abort_opening_session(context)
            if rollback_error is not None:
                self.report({"ERROR"}, f"開始中止後も元に戻せませんでした: {rollback_error}")
        return result

    def _begin_detail_session(self, context):
        from ..utils import detail_dialog, detail_target_resolver
        from . import detail_dialog_runtime

        target = detail_dialog.resolve_detail_target_from_stack(
            self.uid,
            lambda uid: detail_target_resolver.resolve_target_from_stack(context, uid),
        )
        expected = detail_dialog.resolve_detail_layout(
            target,
            detail_dialog.DetailMode.ACTUAL,
            available_width=detail_dialog_runtime.available_dialog_width(context),
        )
        session = detail_dialog_runtime.begin_actual_session(context, target)
        self._detail_session = session
        if session.layout.dialog_width != expected.dialog_width:
            raise RuntimeError("詳細設定の幅を固定できませんでした")
        return session

    def _abort_opening_session(self, context):
        session = getattr(self, "_detail_session", None)
        self._detail_session = None
        if session is None:
            return None
        try:
            from . import detail_dialog_runtime

            detail_dialog_runtime.abort_opening_actual_session(context, session)
        except Exception as exc:  # noqa: BLE001
            return exc
        return None

    def execute(self, context):
        from . import detail_dialog_runtime

        session = getattr(self, "_detail_session", None)
        if session is None:
            return {"CANCELLED"}
        try:
            detail_dialog_runtime.commit_actual_session(context, session)
        except Exception as exc:  # noqa: BLE001
            try:
                detail_dialog_runtime.rollback_failed_actual_session(context, session)
            except Exception as rollback_exc:  # noqa: BLE001
                self.report(
                    {"ERROR"},
                    f"確定失敗後も元に戻せませんでした: {rollback_exc}",
                )
            else:
                self.report({"ERROR"}, f"確定できなかったため変更を元に戻しました: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}

    def check(self, context):
        from . import detail_dialog_runtime

        session = getattr(self, "_detail_session", None)
        if session is None:
            return False
        try:
            detail_dialog_runtime.sync_actual_session(context, session)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"詳細設定の反映を中止しました: {exc}")
            return False
        return True

    def cancel(self, context):
        from . import detail_dialog_runtime

        session = getattr(self, "_detail_session", None)
        if session is None:
            return
        try:
            detail_dialog_runtime.cancel_actual_session(context, session)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"変更を元に戻せませんでした: {exc}")

    def draw(self, context):
        from ..panels.detail_drawers import draw_detail_dialog
        from ..utils.detail_dialog import DetailMode

        session = getattr(self, "_detail_session", None)
        if session is None:
            self.layout.label(text="詳細設定の対象がありません", icon="ERROR")
            return
        try:
            draw_detail_dialog(
                self.layout,
                context,
                session,
                DetailMode.ACTUAL,
                preset_list_owner=self,
            )
        except Exception as exc:  # noqa: BLE001
            self.layout.label(text="詳細設定を表示できません", icon="ERROR")
            self.layout.label(text=str(exc)[:80])
        layer_stack_utils.tag_view3d_redraw(context)

    def _offset_cursor_for_selection_popup(self, context, event) -> None:
        if not bool(getattr(self, "offset_from_selection", False)):
            return
        from ..utils import detail_popup

        detail_popup.position_dialog_cursor(context, event, key="layer_detail", offset_x=360)

    def _capture_edge_selection(self, context) -> tuple[str, int, int, int, int]:
        wm = getattr(context, "window_manager", None)
        if wm is None:
            return ("none", -1, -1, -1, -1)
        return (
            str(getattr(wm, "bmanga_edge_select_kind", "none") or "none"),
            int(getattr(wm, "bmanga_edge_select_page", -1)),
            int(getattr(wm, "bmanga_edge_select_coma", -1)),
            int(getattr(wm, "bmanga_edge_select_edge", -1)),
            int(getattr(wm, "bmanga_edge_select_vertex", -1)),
        )

    def _restore_edge_selection_if_needed(self, context, item, edge_state) -> None:
        if not bool(getattr(self, "preserve_edge_selection", False)):
            return
        if item.kind != COMA_KIND:
            return
        kind, page_index, coma_index, edge_index, vertex_index = edge_state
        if kind not in {"edge", "vertex", "border"}:
            return
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        if resolved is None:
            return
        if (
            int(resolved.get("page_index", -2)) != page_index
            or int(resolved.get("index", -2)) != coma_index
        ):
            return
        from ..utils import edge_selection

        edge_selection.set_selection(
            context,
            kind,
            page_index=page_index,
            coma_index=coma_index,
            edge_index=edge_index,
            vertex_index=vertex_index,
        )


_CLASSES = (BMANGA_OT_layer_stack_detail,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
