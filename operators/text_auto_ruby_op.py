"""自動ルビ — IME辞書から読みを一括設定する Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator, UIList

from ..core.work import get_active_page, get_work
from ..preferences import get_preferences
from ..utils import auto_ruby, log, page_file_scene

_logger = log.get_logger(__name__)


class BNAME_UL_ruby_dict_list(UIList):
    """プリファレンス内の自動ルビ辞書リスト."""

    bl_idname = "BNAME_UL_ruby_dict_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.prop(item, "path", text="")


class BNAME_OT_ruby_dict_add(Operator):
    """自動ルビ辞書を追加."""

    bl_idname = "bname.ruby_dict_add"
    bl_label = "辞書を追加"

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None:
            return {"CANCELLED"}
        entry = prefs.ruby_dictionaries.add()
        entry.enabled = True
        prefs.ruby_dict_active_index = len(prefs.ruby_dictionaries) - 1
        return {"FINISHED"}


class BNAME_OT_ruby_dict_remove(Operator):
    """選択中の辞書を削除."""

    bl_idname = "bname.ruby_dict_remove"
    bl_label = "辞書を削除"

    @classmethod
    def poll(cls, context):
        prefs = get_preferences(context)
        if prefs is None:
            return False
        return 0 <= prefs.ruby_dict_active_index < len(prefs.ruby_dictionaries)

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None:
            return {"CANCELLED"}
        idx = prefs.ruby_dict_active_index
        prefs.ruby_dictionaries.remove(idx)
        prefs.ruby_dict_active_index = min(idx, len(prefs.ruby_dictionaries) - 1)
        return {"FINISHED"}


class BNAME_OT_auto_ruby_apply(Operator):
    """IME辞書を使って全テキストに自動ルビを設定."""

    bl_idname = "bname.auto_ruby_apply"
    bl_label = "自動ルビ"
    bl_description = "登録済みの辞書から漢字の読みを自動で設定します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        if not page_file_scene.is_page_edit_scene(context.scene):
            return False
        prefs = get_preferences(context)
        if prefs is None:
            return False
        return any(
            e.enabled and str(e.path or "").strip()
            for e in prefs.ruby_dictionaries
        )

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None:
            self.report({"ERROR"}, "設定が読み込めません")
            return {"CANCELLED"}

        dict_paths = [
            Path(str(e.path).strip())
            for e in prefs.ruby_dictionaries
            if e.enabled and str(e.path or "").strip()
        ]
        if not dict_paths:
            self.report({"WARNING"}, "有効な辞書が登録されていません")
            return {"CANCELLED"}

        dictionary = auto_ruby.load_dictionaries(dict_paths)
        if not dictionary:
            self.report({"WARNING"}, "辞書から読み込める単語がありませんでした")
            return {"CANCELLED"}

        work = get_work(context)
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが見つかりません")
            return {"CANCELLED"}

        total = 0
        for entry in getattr(page, "texts", []) or []:
            total += auto_ruby.apply_auto_ruby(entry, dictionary)

        if total == 0:
            self.report({"INFO"}, "ルビを設定する漢字が見つかりませんでした")
        else:
            self.report({"INFO"}, f"{total} 箇所にルビを設定しました")

        try:
            from ..io import page_io
            work_dir = Path(str(getattr(work, "work_dir", "") or ""))
            page_io.save_page_json(work_dir, page)
        except Exception:  # noqa: BLE001
            _logger.exception("auto_ruby: save failed")

        return {"FINISHED"}


_CLASSES = (
    BNAME_UL_ruby_dict_list,
    BNAME_OT_ruby_dict_add,
    BNAME_OT_ruby_dict_remove,
    BNAME_OT_auto_ruby_apply,
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
