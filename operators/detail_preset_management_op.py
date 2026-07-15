"""実レイヤー詳細から固定したプリセットを管理する独立即時操作。"""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from ..core.work import get_work
from ..utils import log


_logger = log.get_logger(__name__)


_EXPECTED_KIND = {
    "border": "coma",
    "balloon": "balloon",
    "text": "text",
    "effect_line": "effect",
    "fill": "fill",
    "gradient": "fill",
    "image_path": "image_path",
}


def _work_dir(context) -> Path | None:
    work = get_work(context)
    value = str(getattr(work, "work_dir", "") or "") if work is not None else ""
    return Path(value) if value else None


def _preset_module(preset_type: str):
    from ..io import (
        balloon_presets,
        border_presets,
        effect_line_presets,
        fill_presets,
        gradient_presets,
        image_path_presets,
        text_presets,
    )

    return {
        "border": border_presets,
        "balloon": balloon_presets,
        "text": text_presets,
        "effect_line": effect_line_presets,
        "fill": fill_presets,
        "gradient": gradient_presets,
        "image_path": image_path_presets,
    }.get(preset_type)


def _required(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}がありません")
    return text


def _fixed_identity(operator) -> tuple[str, str, str]:
    """管理Operatorと親詳細のプリセット編集Operatorを同じ固定IDへ揃える。"""

    session_token = str(getattr(operator, "session_token", "") or "")
    target_kind = str(getattr(operator, "target_kind", "") or "")
    target_id = str(getattr(operator, "target_id", "") or "")
    return (
        session_token or str(getattr(operator, "parent_session_token", "") or ""),
        target_kind or str(getattr(operator, "parent_target_kind", "") or ""),
        target_id or str(getattr(operator, "parent_target_id", "") or ""),
    )


def _require_session(self) -> tuple[str, object, object]:
    from . import detail_dialog_runtime

    preset_type = _required(self.preset_type, "プリセット種別")
    session_token, target_kind, target_id = _fixed_identity(self)
    expected_kind = _EXPECTED_KIND.get(preset_type)
    if expected_kind is None or target_kind != expected_kind:
        raise ValueError("プリセットと詳細設定の対象種別が一致しません")
    if not detail_dialog_runtime.detail_action_is_allowed(
        session_token,
        self.bl_idname,
        expected_kind,
        target_id,
    ):
        raise ValueError("詳細設定を開いた対象が変更されています")
    module = _preset_module(preset_type)
    if module is None:
        raise ValueError("このプリセット種別は管理できません")
    target = detail_dialog_runtime.detail_action_target(
        session_token,
        expected_kind,
        target_id,
    )
    if target is None:
        raise ValueError("詳細設定を開いた対象が変更されています")
    if expected_kind == "fill":
        live_type = str(getattr(target.data, "fill_type", "solid") or "solid")
        live_preset_type = "gradient" if live_type == "gradient" else "fill"
        if preset_type != live_preset_type:
            raise ValueError("現在表示している塗りタイプとプリセット種別が一致しません")
    return preset_type, module, target


def _call_with_optional_work_dir(context, preset_type: str, callback_name: str, *args):
    module = _preset_module(preset_type)
    callback = getattr(module, callback_name)
    if preset_type == "border":
        work_dir = _work_dir(context)
        if work_dir is None:
            raise ValueError("作品フォルダーがありません")
        return callback(work_dir, *args)
    if preset_type in {"effect_line", "image_path"}:
        return callback(_work_dir(context), *args)
    return callback(*args)


def _unique_name(context, preset_type: str, base: str) -> str:
    return str(_call_with_optional_work_dir(context, preset_type, "unique_preset_name", base))


def _record_result(self, selected_name: str | None) -> None:
    from . import detail_dialog_runtime

    session_token, target_kind, target_id = _fixed_identity(self)
    detail_dialog_runtime.record_preset_selection_for_identity(
        session_token,
        target_kind,
        target_id,
        selected_name,
        preset_type=self.preset_type,
    )
    detail_dialog_runtime.record_detail_action(
        session_token,
        self.bl_idname,
        target_kind,
        target_id,
        selected_name,
    )


def _mark_current_settings_saved(context, operator, target, preset_type: str) -> None:
    """追加／上書き後は、現在値を切替確認の新しい基準にする。"""

    from . import detail_dialog_runtime

    session_token, _target_kind, _target_id = _fixed_identity(operator)
    try:
        detail_dialog_runtime.mark_preset_settings_saved(
            context,
            session_token,
            target,
            preset_type,
        )
    except Exception:  # 保存自体は成功済みなので、データを巻き戻さない
        _logger.exception("failed to update saved preset baseline")
        operator.report(
            {"WARNING"},
            "プリセットは保存しましたが、変更検知を更新できませんでした",
        )


def _reconcile_saved_reference(
    operator,
    old_name: str,
    new_name: str | None,
    *,
    balloon_outline_json: str = "",
) -> None:
    """JSON管理の確定結果を、固定対象と親ダイアログの取消基準へ反映する。"""

    from . import detail_dialog_runtime

    session_token, target_kind, target_id = _fixed_identity(operator)
    detail_dialog_runtime.reconcile_preset_reference_after_management(
        session_token,
        target_kind,
        target_id,
        operator.preset_type,
        old_name,
        new_name,
        balloon_outline_json=balloon_outline_json,
    )


def _balloon_outline_json_before_delete(context, preset_type: str, name: str) -> str:
    """削除後も適用済みフキダシの実形状を再生成できる輪郭を退避する。"""

    if preset_type != "balloon":
        return ""
    preset = _load_named_preset(context, preset_type, name)
    data = getattr(preset, "data", None)
    vertices = data.get("vertices", ()) if isinstance(data, dict) else ()
    outline = []
    for point in vertices:
        try:
            outline.append([float(point[0]), float(point[1])])
        except (IndexError, TypeError, ValueError):
            continue
    return json.dumps(outline, separators=(",", ":")) if len(outline) >= 3 else ""


def _default_new_name(preset_type: str) -> str:
    return {
        "border": "新規枠線プリセット",
        "balloon": "新規フキダシプリセット",
        "text": "新規テキストプリセット",
        "effect_line": "新規効果線プリセット",
        "fill": "新規囲い塗りプリセット",
        "gradient": "新規グラデーション",
        "image_path": "新規パターンカーブプリセット",
    }[preset_type]


def _balloon_outline(entry) -> list[tuple[float, float]]:
    from ..utils import balloon_multiline_curve

    points, _corners = balloon_multiline_curve.body_outline_for_entry(entry)
    outline = [(float(x), float(y)) for x, y in points]
    if len(outline) < 3:
        raise ValueError("フキダシの輪郭を取得できません")
    return outline


def _save_new_preset(context, preset_type: str, target, name: str, description: str):
    module = _preset_module(preset_type)
    work_dir = _work_dir(context)
    if preset_type == "border":
        if work_dir is None:
            raise ValueError("作品フォルダーがありません")
        return module.save_local_preset(work_dir, target.data, name, description)
    if preset_type == "effect_line":
        return module.save_local_preset(work_dir, target.params, name, description)
    if preset_type == "image_path":
        return module.save_local_preset(work_dir, target.data, name, description)
    if preset_type == "text":
        return module.save_local_preset(
            work_dir, name, description, module.snapshot_from_entry(target.data)
        )
    if preset_type in {"fill", "gradient"}:
        return module.save_local_preset(
            name, description, module.snapshot_from_entry(target.data)
        )
    if preset_type == "balloon":
        return module.save_local_preset(
            work_dir, name, description, _balloon_outline(target.data), False
        )
    raise ValueError("このプリセット種別は追加できません")


def _load_named_preset(context, preset_type: str, name: str):
    module = _preset_module(preset_type)
    work_dir = _work_dir(context)
    if preset_type == "border":
        if work_dir is None:
            raise ValueError("作品フォルダーがありません")
        return module.load_preset_by_name(name, work_dir)
    if preset_type in {"effect_line", "image_path"}:
        return module.load_preset_by_name(name, work_dir)
    return module.load_preset_by_name(name)


def overwrite_selected_preset(context, operator) -> str:
    """親詳細の現在値で選択プリセットを上書きする独立即時操作。"""

    preset_type, _module, target = _require_session(operator)
    name = _required(operator.preset_name, "プリセット名")
    preset = _load_named_preset(context, preset_type, name)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {name}")
    data = getattr(preset, "data", {})
    description = str(data.get("description", "") or "") if isinstance(data, dict) else ""
    _save_new_preset(context, preset_type, target, name, description)
    _record_result(operator, name)
    _mark_current_settings_saved(context, operator, target, preset_type)
    return name


class BMangaDetailPresetDraft(PropertyGroup):
    """親詳細内で入力する名前。セッション単位に分離して保持する。"""

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    selected_name: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    add_name: StringProperty(name="追加する名前", default="")  # type: ignore[valid-type]
    add_description: StringProperty(name="説明", default="")  # type: ignore[valid-type]
    rename_name: StringProperty(name="変更後の名前", default="")  # type: ignore[valid-type]
    duplicate_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]


def detail_preset_draft(context, session, preset_type: str, selected_name: str):
    """同じ親画面の再描画では入力値を保ち、選択変更時だけ初期化する。"""

    wm = getattr(context, "window_manager", None)
    drafts = getattr(wm, "bmanga_detail_preset_drafts", None) if wm is not None else None
    if drafts is None:
        return None
    token = str(getattr(session, "token", "") or "")
    selected = str(selected_name or "")
    draft = None
    for index in range(len(drafts) - 1, -1, -1):
        candidate = drafts[index]
        if candidate.session_token != token:
            continue
        if candidate.preset_type == preset_type:
            draft = candidate
        else:
            drafts.remove(index)
    if draft is None:
        draft = drafts.add()
        draft.session_token = token
        draft.preset_type = preset_type
        draft.add_name = _default_new_name(preset_type)
    if draft.selected_name != selected:
        draft.selected_name = selected
        draft.rename_name = selected
        draft.duplicate_name = f"{selected} コピー" if selected else ""
    return draft


class BMANGA_OT_detail_preset_add(Operator):
    bl_idname = "bmanga.detail_preset_add"
    bl_label = "プリセットを追加"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_kind: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        try:
            preset_type, _module, _target = _require_session(self)
            base = self.preset_name or _default_new_name(preset_type)
            self.preset_name = _unique_name(context, preset_type, base)
        except (LookupError, OSError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        try:
            preset_type, _module, target = _require_session(self)
            name = _unique_name(
                context,
                preset_type,
                _required(self.preset_name, "プリセット名"),
            )
            _save_new_preset(context, preset_type, target, name, self.description)
            _record_result(self, name)
            _mark_current_settings_saved(context, self, target, preset_type)
        except (LookupError, OSError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"プリセット「{name}」を追加しました")
        return {"FINISHED"}
class BMANGA_OT_detail_preset_rename(Operator):
    bl_idname = "bmanga.detail_preset_rename"
    bl_label = "プリセット名を変更"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_kind: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(  # type: ignore[valid-type]
        name="現在の名前",
        default="",
        options={"HIDDEN"},
    )
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        if not self.new_name:
            self.new_name = str(self.preset_name or "")
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        try:
            preset_type, _module, _target = _require_session(self)
            old_name = _required(self.preset_name, "現在のプリセット名")
            new_name = _required(self.new_name, "新しいプリセット名")
            result = _call_with_optional_work_dir(
                context, preset_type, "rename_preset", old_name, new_name
            )
            selected = str(getattr(result, "name", "") or new_name)
            _reconcile_saved_reference(self, old_name, selected)
            _record_result(self, selected)
        except (LookupError, OSError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"プリセット名を「{selected}」へ変更しました")
        return {"FINISHED"}


class BMANGA_OT_detail_preset_duplicate(Operator):
    bl_idname = "bmanga.detail_preset_duplicate"
    bl_label = "プリセットを複製"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_kind: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(  # type: ignore[valid-type]
        name="複製元",
        default="",
        options={"HIDDEN"},
    )
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    def invoke(self, context, _event):
        try:
            preset_type, _module, _target = _require_session(self)
            self.new_name = _unique_name(
                context,
                preset_type,
                self.new_name or f"{self.preset_name} コピー",
            )
        except (LookupError, OSError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        try:
            preset_type, _module, _target = _require_session(self)
            source_name = _required(self.preset_name, "複製元プリセット名")
            new_name = _unique_name(
                context,
                preset_type,
                _required(self.new_name, "複製後のプリセット名"),
            )
            result = _call_with_optional_work_dir(
                context, preset_type, "duplicate_preset", source_name, new_name
            )
            selected = str(getattr(result, "name", "") or new_name)
            _record_result(self, selected)
        except (LookupError, OSError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"プリセットを「{selected}」として複製しました")
        return {"FINISHED"}


class BMANGA_OT_detail_preset_delete(Operator):
    bl_idname = "bmanga.detail_preset_delete"
    bl_label = "プリセットを削除"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_kind: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(  # type: ignore[valid-type]
        name="プリセット名",
        default="",
        options={"HIDDEN"},
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            preset_type, _module, _target = _require_session(self)
            name = _required(self.preset_name, "プリセット名")
            balloon_outline_json = _balloon_outline_json_before_delete(
                context,
                preset_type,
                name,
            )
            _call_with_optional_work_dir(context, preset_type, "delete_preset", name)
            _reconcile_saved_reference(
                self,
                name,
                None,
                balloon_outline_json=balloon_outline_json,
            )
            _record_result(self, None)
        except (LookupError, OSError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"プリセット「{name}」を削除しました")
        return {"FINISHED"}


class BMANGA_OT_detail_preset_move(Operator):
    bl_idname = "bmanga.detail_preset_move"
    bl_label = "プリセットを並べ替え"
    bl_options = {"INTERNAL"}

    session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_kind: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    direction: StringProperty(default="UP", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        try:
            preset_type, _module, _target = _require_session(self)
            name = _required(self.preset_name, "プリセット名")
            direction = str(self.direction or "").upper()
            if direction not in {"UP", "DOWN"}:
                raise ValueError("並べ替え方向が不正です")
            _call_with_optional_work_dir(
                context,
                preset_type,
                "move_preset",
                name,
                direction,
            )
            _record_result(self, name)
        except (LookupError, OSError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (
    BMangaDetailPresetDraft,
    BMANGA_OT_detail_preset_add,
    BMANGA_OT_detail_preset_rename,
    BMANGA_OT_detail_preset_duplicate,
    BMANGA_OT_detail_preset_delete,
    BMANGA_OT_detail_preset_move,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bmanga_detail_preset_drafts = CollectionProperty(
        type=BMangaDetailPresetDraft
    )


def unregister() -> None:
    try:
        del bpy.types.WindowManager.bmanga_detail_preset_drafts
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


__all__ = [
    "BMANGA_OT_detail_preset_add",
    "BMANGA_OT_detail_preset_delete",
    "BMANGA_OT_detail_preset_duplicate",
    "BMANGA_OT_detail_preset_rename",
    "BMANGA_OT_detail_preset_move",
    "BMangaDetailPresetDraft",
    "detail_preset_draft",
    "overwrite_selected_preset",
    "register",
    "unregister",
]
