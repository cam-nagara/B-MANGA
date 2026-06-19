"""B-MANGA 設定のエクスポート / インポート Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..io import settings_bundle
from ..utils import log

_logger = log.get_logger(__name__)


class BMANGA_OT_preferences_export(Operator):
    bl_idname = "bmanga.preferences_export"
    bl_label = "B-MANGA設定をエクスポート"
    bl_description = "B-MANGAのプリファレンスと共通プリセットをZIPに書き出します"
    bl_options = {"REGISTER"}

    filepath: StringProperty(  # type: ignore[valid-type]
        name="保存先",
        default="B-MANGA_settings.zip",
        subtype="FILE_PATH",
    )
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})  # type: ignore[valid-type]

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            out = settings_bundle.export_bundle(context, self.filepath)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("B-MANGA settings export failed")
            self.report({"ERROR"}, f"エクスポート失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"B-MANGA設定を書き出しました: {Path(out).name}")
        return {"FINISHED"}


class BMANGA_OT_preferences_import(Operator):
    bl_idname = "bmanga.preferences_import"
    bl_label = "B-MANGA設定をインポート"
    bl_description = "ZIPからB-MANGAのプリファレンスと共通プリセットを読み込みます"
    bl_options = {"REGISTER"}

    filepath: StringProperty(  # type: ignore[valid-type]
        name="読み込み元",
        default="",
        subtype="FILE_PATH",
    )
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})  # type: ignore[valid-type]

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            result = settings_bundle.import_bundle(context, self.filepath)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("B-MANGA settings import failed")
            self.report({"ERROR"}, f"インポート失敗: {exc}")
            return {"CANCELLED"}
        try:
            from . import balloon_tail_detail_op, preset_op

            preset_op.restore_tool_preset_selectors(context)
            balloon_tail_detail_op.restore_tail_preset_selector(context)
        except Exception:  # noqa: BLE001
            _logger.warning("preset selector restore after import failed", exc_info=True)
        self.report(
            {"INFO"},
            f"B-MANGA設定を読み込みました: プリセット{int(result.get('preset_files', 0))}件",
        )
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_preferences_export,
    BMANGA_OT_preferences_import,
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
