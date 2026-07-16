"""詳細設定で固定したラスターだけを操作する独立即時オペレーター。"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator


def _fixed_entry(operator, context):
    from . import detail_dialog_runtime, raster_layer_op

    target_id = str(operator.target_id or "")
    if not target_id or not detail_dialog_runtime.detail_action_is_allowed(
        operator.session_token,
        operator.bl_idname,
        "raster",
        target_id,
    ):
        operator.report({"WARNING"}, "詳細設定を開いたラスターが変更されています")
        return None
    entry, index = raster_layer_op.find_raster_entry(context.scene, target_id)
    if entry is None or index < 0 or str(getattr(entry, "id", "") or "") != target_id:
        operator.report({"WARNING"}, "詳細設定を開いたラスターが見つかりません")
        return None
    return entry


def _run_independent(operator, action):
    from . import detail_dialog_runtime

    return detail_dialog_runtime.execute_independent_detail_action(
        operator.session_token,
        operator.bl_idname,
        "raster",
        operator.target_id,
        action,
    )


class BMANGA_OT_detail_raster_paint_enter(Operator):
    bl_idname = "bmanga.detail_raster_paint_enter"
    bl_label = "テクスチャペイントを開始"
    bl_description = "詳細設定をOKまたはキャンセルで閉じてから、このラスターのテクスチャペイントを開始します"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        if _fixed_entry(self, context) is None:
            return {"CANCELLED"}
        try:
            _run_independent(
                self,
                lambda identity: bpy.ops.bmanga.raster_layer_paint_enter(
                    "EXEC_DEFAULT",
                    raster_id=identity.stable_id,
                ),
            )
        except Exception:
            self.report(
                {"WARNING"},
                "詳細設定をOKまたはキャンセルで閉じてから、テクスチャペイントを開始してください",
            )
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_detail_raster_save_png(Operator):
    bl_idname = "bmanga.detail_raster_save_png"
    bl_label = "ラスターPNGを保存"
    bl_description = "現在のラスターをPNGへ即時保存します。詳細設定のキャンセルでは戻りません"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    force: BoolProperty(default=True, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        if _fixed_entry(self, context) is None:
            return {"CANCELLED"}
        def save_png(identity):
            result = bpy.ops.bmanga.raster_layer_save_png(
                "EXEC_DEFAULT",
                raster_id=identity.stable_id,
                force=bool(self.force),
            )
            if "FINISHED" not in result:
                raise RuntimeError("ラスターをPNGへ保存できませんでした")
            return result

        try:
            _run_independent(self, save_png)
        except Exception as exc:
            self.report({"ERROR"}, "ラスターをPNGへ保存できませんでした")
            if str(exc):
                self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "ラスターをPNGへ保存しました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_detail_raster_paint_enter,
    BMANGA_OT_detail_raster_save_png,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "BMANGA_OT_detail_raster_paint_enter",
    "BMANGA_OT_detail_raster_save_png",
    "register",
    "unregister",
]
