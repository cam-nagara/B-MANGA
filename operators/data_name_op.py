"""実データ名整理オペレーター."""

from __future__ import annotations

import bpy
from bpy.types import Operator


class BNAME_OT_organize_data_names(Operator):
    """ページ/コマの実データ名を現在の並びへ揃える."""

    bl_idname = "bname.organize_data_names"
    bl_label = "実データ名を整理"
    bl_description = "ページとコマのフォルダ名・ファイル名を、現在のページ順と読む順番に揃えます"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        from ..core.mode import MODE_PAGE, get_mode
        from ..core.work import get_work
        from ..utils import page_file_scene

        work = get_work(context)
        return (
            work is not None
            and getattr(work, "loaded", False)
            and bool(getattr(work, "work_dir", "") or "")
            and get_mode(context) == MODE_PAGE
            and page_file_scene.is_work_list_scene(getattr(context, "scene", None))
        )

    def execute(self, context):
        from ..utils import data_name_organizer

        try:
            result = data_name_organizer.organize_data_names(context)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"実データ名の整理に失敗しました: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, result.summary)
        return {"FINISHED"}


_CLASSES = (BNAME_OT_organize_data_names,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
