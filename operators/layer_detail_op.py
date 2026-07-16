"""3Dビュー／アウトライナーから固定対象の詳細設定を開く。"""

from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..utils import detail_popup, log
from .detail_preset_apply_op import (
    BMANGA_DetailPresetListItem,
    on_detail_linked_balloon_index_changed,
    on_detail_preset_index_changed,
)


_logger = log.get_logger(__name__)


class BMANGA_OT_layer_detail_open(Operator):
    """選択したB-MANGAレイヤーだけを編集する詳細設定。"""

    bl_idname = "bmanga.layer_detail_open"
    bl_label = "詳細設定"
    bl_description = "3Dビューまたはアウトライナーで選択したレイヤーの詳細設定を開きます"
    bl_options = {"REGISTER", "UNDO"}

    bmanga_id: StringProperty(name="bmanga_id", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    kind: StringProperty(name="kind", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    detail_preset_items: CollectionProperty(  # type: ignore[valid-type]
        type=BMANGA_DetailPresetListItem,
        options={"HIDDEN"},
    )
    detail_preset_index: IntProperty(  # type: ignore[valid-type]
        default=-1,
        options={"HIDDEN"},
        update=on_detail_preset_index_changed,
    )
    detail_linked_balloon_items: CollectionProperty(  # type: ignore[valid-type]
        type=BMANGA_DetailPresetListItem,
        options={"HIDDEN"},
    )
    detail_linked_balloon_index: IntProperty(  # type: ignore[valid-type]
        default=-1,
        options={"HIDDEN"},
        update=on_detail_linked_balloon_index_changed,
    )

    @classmethod
    def poll(cls, context):
        try:
            from ..utils import detail_target_resolver

            detail_target_resolver.resolve_target_from_selected_object(context)
            return True
        except Exception:
            return False

    def invoke(self, context, event):
        self._detail_session = None
        self._text_dialog_cursor_override = False
        try:
            target = self._resolve_fixed_target(context)
            from . import detail_dialog_runtime

            self._detail_session = detail_dialog_runtime.begin_actual_session(context, target)
            self.bmanga_id = target.stable_id
            self.kind = target.kind
            self._set_text_dialog_cursor_override(context, True)
            result = detail_popup.invoke_props_dialog(
                context,
                event,
                self,
                width=self._detail_session.layout.dialog_width,
            )
        except Exception as exc:  # noqa: BLE001
            rollback_error = self._abort_opening_session(context)
            if rollback_error is not None:
                self.report({"ERROR"}, f"開始失敗後も元に戻せませんでした: {rollback_error}")
            else:
                self.report({"WARNING"}, f"詳細設定を開けません: {exc}")
            return {"CANCELLED"}
        if "CANCELLED" in result:
            rollback_error = self._abort_opening_session(context)
            if rollback_error is not None:
                self.report({"ERROR"}, f"開始中止後も元に戻せませんでした: {rollback_error}")
        return result

    def _abort_opening_session(self, context):
        self._set_text_dialog_cursor_override(context, False)
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

    def _set_text_dialog_cursor_override(self, context, active: bool) -> None:
        """右クリック詳細画面の間もテキストツールのカーソルを可視化する。"""
        active = bool(active)
        current = bool(getattr(self, "_text_dialog_cursor_override", False))
        if current == active:
            return
        from . import text_edit_runtime

        text_edit_runtime.set_dialog_cursor_override(context, active)
        self._text_dialog_cursor_override = active

    def _resolve_fixed_target(self, context):
        from ..utils import detail_dialog, detail_target_resolver

        if self.kind and self.bmanga_id:
            selected = detail_target_resolver.resolve_target_from_object(
                context,
                self.bmanga_id,
                self.kind,
            )
        else:
            selected = detail_target_resolver.resolve_target_from_selected_object(context)
        return detail_dialog.resolve_detail_target_from_object(
            selected.stable_id,
            lambda stable_id: selected if stable_id == selected.stable_id else None,
        )

    def draw(self, context):
        session = getattr(self, "_detail_session", None)
        if session is None:
            self.layout.label(text="詳細設定の対象がありません", icon="ERROR")
            return
        try:
            from . import detail_dialog_runtime

            detail_dialog_runtime.draw_actual_session(
                self.layout,
                context,
                session,
                preset_list_owner=self,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("right-click detail draw failed")
            self.layout.label(text="詳細設定を表示できません", icon="ERROR")
            self.layout.label(text=str(exc)[:80])

    def check(self, context):
        session = getattr(self, "_detail_session", None)
        if session is None:
            return False
        try:
            from . import detail_dialog_runtime

            detail_dialog_runtime.sync_actual_session(context, session)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"詳細設定の反映を中止しました: {exc}")
            return False
        return True

    def execute(self, context):
        self._set_text_dialog_cursor_override(context, False)
        session = getattr(self, "_detail_session", None)
        if session is None:
            return {"CANCELLED"}
        try:
            from . import detail_dialog_runtime

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

    def cancel(self, context):
        self._set_text_dialog_cursor_override(context, False)
        session = getattr(self, "_detail_session", None)
        if session is None:
            return
        try:
            from . import detail_dialog_runtime

            detail_dialog_runtime.cancel_actual_session(context, session)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"変更を元に戻せませんでした: {exc}")


_CLASSES = (BMANGA_OT_layer_detail_open,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
