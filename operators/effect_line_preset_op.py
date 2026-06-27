"""効果線プリセットの選択・管理."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import effect_line_presets
from ..utils import log

_logger = log.get_logger(__name__)

_EFFECT_LINE_TOOL_ENUM_CACHE: list[tuple[str, str, str]] | None = None
_SUPPRESS_EFFECT_LINE_PRESET_REMEMBER = False


def _effect_line_preset_work_dir(context) -> Path | None:
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False) or not getattr(work, "work_dir", ""):
        return None
    return Path(work.work_dir)


def _active_effect_params(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "bmanga_effect_line_params", None) if scene is not None else None


def _selected_effect_line_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_effect_line_tool_preset_selector"):
        return ""
    return str(getattr(wm, "bmanga_effect_line_tool_preset_selector", "") or "")


def _effect_line_tool_preset_enum_items(_self, context):
    global _EFFECT_LINE_TOOL_ENUM_CACHE
    work_dir = _effect_line_preset_work_dir(context)
    presets = effect_line_presets.list_all_presets(work_dir)
    cache = [
        (p.name, p.name if p.source == "global" else f"{p.name} (共通)", p.description)
        for p in presets
    ]
    if not cache:
        cache.append(("", "(プリセットなし)", ""))
    _EFFECT_LINE_TOOL_ENUM_CACHE = cache
    return _EFFECT_LINE_TOOL_ENUM_CACHE


def _remember_effect_line_preset(context, value: str) -> None:
    global _SUPPRESS_EFFECT_LINE_PRESET_REMEMBER
    if _SUPPRESS_EFFECT_LINE_PRESET_REMEMBER:
        return
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
        if prefs is not None and hasattr(prefs, "last_effect_line_tool_preset"):
            prefs.last_effect_line_tool_preset = value
            addon_preferences.request_user_preferences_save()
    except Exception:  # noqa: BLE001
        _logger.exception("effect line preset remember failed")


def apply_selected_effect_line_preset(context, params=None) -> bool:
    params = params or _active_effect_params(context)
    if params is None:
        return False
    work_dir = _effect_line_preset_work_dir(context)
    name = _selected_effect_line_preset_name(context)
    preset = effect_line_presets.load_preset_by_name(name, work_dir) if name else None
    if preset is None:
        presets = effect_line_presets.list_all_presets(work_dir)
        preset = presets[0] if presets else None
    if preset is None:
        return False
    effect_line_presets.apply_preset_to_params(preset, params)
    return True


def _on_effect_line_preset_selector_change(self, context):
    value = str(getattr(self, "bmanga_effect_line_tool_preset_selector", "") or "")
    _remember_effect_line_preset(context, value)
    try:
        apply_selected_effect_line_preset(context)
    except Exception:  # noqa: BLE001
        _logger.exception("effect line preset apply failed")


def _set_effect_line_preset_selector(context, name: str) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_effect_line_tool_preset_selector") or not name:
        return
    valid = {item[0] for item in _effect_line_tool_preset_enum_items(None, context)}
    if name in valid:
        setattr(wm, "bmanga_effect_line_tool_preset_selector", name)


def _restore_selector_if_valid(context, value: str) -> None:
    global _SUPPRESS_EFFECT_LINE_PRESET_REMEMBER
    if not value:
        return
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_effect_line_tool_preset_selector"):
        return
    valid = {item[0] for item in _effect_line_tool_preset_enum_items(None, context)}
    if value not in valid:
        return
    _SUPPRESS_EFFECT_LINE_PRESET_REMEMBER = True
    try:
        setattr(wm, "bmanga_effect_line_tool_preset_selector", value)
    finally:
        _SUPPRESS_EFFECT_LINE_PRESET_REMEMBER = False


def restore_effect_line_preset_selector(context) -> None:
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
    except Exception:  # noqa: BLE001
        prefs = None
    if prefs is None:
        return
    _restore_selector_if_valid(context, str(getattr(prefs, "last_effect_line_tool_preset", "") or ""))


class BMANGA_OT_effect_line_preset_add_local(Operator):
    bl_idname = "bmanga.effect_line_preset_add_local"
    bl_label = "効果線プリセットを追加"
    bl_description = "現在の効果線設定を、新しい共通プリセットとして追加します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="新規効果線プリセット")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _active_effect_params(context) is not None

    def invoke(self, context, _event):
        work_dir = _effect_line_preset_work_dir(context)
        self.preset_name = effect_line_presets.unique_preset_name(work_dir, "新規効果線プリセット")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        params = _active_effect_params(context)
        if params is None:
            self.report({"ERROR"}, "効果線設定がありません")
            return {"CANCELLED"}
        work_dir = _effect_line_preset_work_dir(context)
        name = effect_line_presets.unique_preset_name(work_dir, self.preset_name.strip())
        try:
            effect_line_presets.save_local_preset(
                work_dir,
                params,
                name,
                self.description,
                insert_after=_selected_effect_line_preset_name(context),
            )
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"追加失敗: {exc}")
            return {"CANCELLED"}
        _set_effect_line_preset_selector(context, name)
        self.report({"INFO"}, f"効果線プリセット追加: {name}")
        return {"FINISHED"}


class BMANGA_OT_effect_line_preset_rename(Operator):
    bl_idname = "bmanga.effect_line_preset_rename"
    bl_label = "効果線プリセットを改名"
    bl_description = "選択中の効果線プリセットを改名します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="現在の名前", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_effect_line_preset_name(context))

    def invoke(self, context, _event):
        selected = _selected_effect_line_preset_name(context)
        self.preset_name = selected
        self.new_name = selected
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work_dir = _effect_line_preset_work_dir(context)
        old_name = self.preset_name.strip() or _selected_effect_line_preset_name(context)
        try:
            preset = effect_line_presets.rename_preset(work_dir, old_name, self.new_name.strip())
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"改名失敗: {exc}")
            return {"CANCELLED"}
        _set_effect_line_preset_selector(context, preset.name)
        self.report({"INFO"}, f"効果線プリセット改名: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_effect_line_preset_duplicate(Operator):
    bl_idname = "bmanga.effect_line_preset_duplicate"
    bl_label = "効果線プリセットを複製"
    bl_description = "選択中の効果線プリセットを共通プリセットとして複製します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="複製元", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_effect_line_preset_name(context))

    def invoke(self, context, _event):
        work_dir = _effect_line_preset_work_dir(context)
        selected = _selected_effect_line_preset_name(context)
        self.preset_name = selected
        self.new_name = effect_line_presets.unique_preset_name(work_dir, f"{selected} コピー")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work_dir = _effect_line_preset_work_dir(context)
        source_name = self.preset_name.strip() or _selected_effect_line_preset_name(context)
        try:
            preset = effect_line_presets.duplicate_preset(work_dir, source_name, self.new_name.strip())
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"複製失敗: {exc}")
            return {"CANCELLED"}
        _set_effect_line_preset_selector(context, preset.name)
        self.report({"INFO"}, f"効果線プリセット複製: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_effect_line_preset_delete(Operator):
    bl_idname = "bmanga.effect_line_preset_delete"
    bl_label = "効果線プリセットを削除"
    bl_description = "選択中の効果線プリセットを共通一覧から削除します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_effect_line_preset_name(context))

    def invoke(self, context, event):
        self.preset_name = self.preset_name or _selected_effect_line_preset_name(context)
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work_dir = _effect_line_preset_work_dir(context)
        name = self.preset_name.strip() or _selected_effect_line_preset_name(context)
        names_before = [preset.name for preset in effect_line_presets.list_all_presets(work_dir)]
        fallback = ""
        if name in names_before and len(names_before) > 1:
            index = names_before.index(name)
            fallback = names_before[index + 1] if index + 1 < len(names_before) else names_before[index - 1]
        try:
            effect_line_presets.delete_preset(work_dir, name)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"削除失敗: {exc}")
            return {"CANCELLED"}
        presets_after = effect_line_presets.list_all_presets(work_dir)
        after_names = {preset.name for preset in presets_after}
        target = fallback if fallback in after_names else (presets_after[0].name if presets_after else "")
        if target:
            _set_effect_line_preset_selector(context, target)
        self.report({"INFO"}, f"効果線プリセット削除: {name}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_effect_line_preset_add_local,
    BMANGA_OT_effect_line_preset_rename,
    BMANGA_OT_effect_line_preset_duplicate,
    BMANGA_OT_effect_line_preset_delete,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bmanga_effect_line_tool_preset_selector = EnumProperty(
        name="効果線プリセット",
        description="効果線ツールで新しく作る効果線の設定",
        items=_effect_line_tool_preset_enum_items,
        update=_on_effect_line_preset_selector_change,
    )
    try:
        restore_effect_line_preset_selector(bpy.context)
    except Exception:  # noqa: BLE001
        _logger.exception("effect line preset selector restore failed")


def unregister() -> None:
    try:
        del bpy.types.WindowManager.bmanga_effect_line_tool_preset_selector
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
