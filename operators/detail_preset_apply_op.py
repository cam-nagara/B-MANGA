"""詳細設定から、開始時に固定した実対象へプリセットを適用する。

一覧のアクティブ行、選択オブジェクト、各種 ``active_*_index`` は参照しない。
ダイアログから渡された種別と永続 ID だけで対象を再解決し、同種の別レイヤーへ
誤適用されることを防ぐ。しっぽは個別の明示入口を使うため本モジュールの対象外。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList

from ..utils import log


_logger = log.get_logger(__name__)
_EMPTY_PRESET = "__BMANGA_NO_PRESET__"
_ENUM_CACHE: dict[str, list[tuple[str, str, str]]] = {}

_EXPECTED_TARGET_KIND = {
    "border": "coma",
    "text": "text",
    "effect_line": "effect",
    "fill": "fill",
    "gradient": "fill",
    "image_path": "image_path",
    "balloon": "balloon",
}


class BMANGA_DetailPresetListItem(PropertyGroup):
    """詳細設定専用UIListの1行。通常ツール側の選択状態とは共有しない。"""

    identifier: StringProperty(name="プリセット名")  # type: ignore[valid-type]
    description: StringProperty(name="説明")  # type: ignore[valid-type]
    preset_type: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]
    target_kind: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]
    target_id: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]
    stack_uid: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]
    session_token: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]
    is_selected: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]


class BMANGA_UL_detail_presets(UIList):
    """固定済み詳細対象へだけ適用するプリセット一覧。"""

    bl_idname = "BMANGA_UL_detail_presets"

    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index,
    ):
        if self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="PRESET")
            return
        layout.label(text=str(item.name or item.identifier))


class BMANGA_UL_detail_linked_balloon_presets(UIList):
    """テキスト詳細のリンクフキダシプリセット一覧。"""

    bl_idname = "BMANGA_UL_detail_linked_balloon_presets"

    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index,
    ):
        if self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="LINKED")
            return
        layout.label(text=str(item.name or item.identifier))


def _invoke_detail_preset_apply(item):
    return bpy.ops.bmanga.detail_preset_apply(
        "INVOKE_DEFAULT",
        preset_type=str(item.preset_type),
        preset_name=str(item.identifier),
        preset_label=str(item.name or item.identifier),
        target_kind=str(item.target_kind),
        target_id=str(item.target_id),
        stable_id=str(item.target_id),
        stack_uid=str(item.stack_uid),
        session_token=str(item.session_token),
        confirm_unsaved_changes=True,
    )


def on_detail_preset_index_changed(owner, context) -> None:
    """標準UIListの選択変更を、固定済み詳細対象への即時適用へ変換する。"""

    if bool(getattr(owner, "_detail_preset_list_syncing", False)):
        return
    collection = getattr(owner, "detail_preset_items", None)
    index = int(getattr(owner, "detail_preset_index", -1))
    if collection is None or not (0 <= index < len(collection)):
        return
    try:
        result = _invoke_detail_preset_apply(collection[index])
        if "CANCELLED" in result:
            owner._detail_preset_list_signature = None
    except Exception:  # UIListの再描画は維持し、適用Operator側の報告を優先する
        owner._detail_preset_list_signature = None
        _logger.exception("detail preset list selection failed")


def _invoke_detail_linked_balloon_set(item):
    return bpy.ops.bmanga.detail_text_linked_balloon_set(
        "EXEC_DEFAULT",
        session_token=str(item.session_token),
        target_id=str(item.target_id),
        preset_name=str(item.identifier),
    )


def on_detail_linked_balloon_index_changed(owner, context) -> None:
    """リンクフキダシも標準UIListの選択変更だけで固定対象へ適用する。"""

    if bool(getattr(owner, "_detail_linked_balloon_list_syncing", False)):
        return
    collection = getattr(owner, "detail_linked_balloon_items", None)
    index = int(getattr(owner, "detail_linked_balloon_index", -1))
    if collection is None or not (0 <= index < len(collection)):
        return
    try:
        result = _invoke_detail_linked_balloon_set(collection[index])
        if "CANCELLED" in result:
            owner._detail_linked_balloon_list_signature = None
    except Exception:
        owner._detail_linked_balloon_list_signature = None
        _logger.exception("linked balloon preset list selection failed")


def _work_dir(context) -> Path | None:
    scene = getattr(context, "scene", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    value = str(getattr(work, "work_dir", "") or "").strip() if work is not None else ""
    return Path(value) if value else None


def _preset_type(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {"effect": "effect_line", "solid_fill": "fill", "pattern_curve": "image_path"}
    return aliases.get(text, text)


def _list_presets(context, preset_type: str):
    from ..io import (
        balloon_presets,
        border_presets,
        effect_line_presets,
        fill_presets,
        gradient_presets,
        image_path_presets,
        text_presets,
    )

    work_dir = _work_dir(context)
    callbacks = {
        "border": lambda: border_presets.list_all_presets(work_dir),
        "text": lambda: text_presets.list_all_presets(work_dir),
        "effect_line": lambda: effect_line_presets.list_all_presets(work_dir),
        "fill": lambda: fill_presets.list_all_presets(work_dir),
        "gradient": lambda: gradient_presets.list_all_presets(work_dir),
        "image_path": lambda: image_path_presets.list_all_presets(work_dir),
        "balloon": lambda: balloon_presets.list_all_presets(work_dir),
    }
    callback = callbacks.get(preset_type)
    return list(callback() if callback is not None else ())


def _detail_preset_entries(context, preset_type: str) -> list[tuple[str, str, str]]:
    """実レイヤーへ適用できる識別子・表示名・説明を一覧化する。"""

    preset_type = _preset_type(preset_type)
    if preset_type != "balloon":
        return [
            (
                str(getattr(preset, "name", "") or "").strip(),
                str(getattr(preset, "name", "") or "").strip(),
                str(getattr(preset, "description", "") or ""),
            )
            for preset in _list_presets(context, preset_type)
            if str(getattr(preset, "name", "") or "").strip()
        ]

    from ..core.balloon import _SHAPE_ITEMS

    entries = [
        (f"shape:{shape_id}", str(label), str(description or ""))
        for shape_id, label, description, _icon, _number in _SHAPE_ITEMS
        if shape_id not in {"custom", "none"}
    ]
    entries.extend(
        (
            str(preset.name),
            f"{preset.name} (カスタム)",
            str(getattr(preset, "description", "") or ""),
        )
        for preset in _list_presets(context, preset_type)
        if str(getattr(preset, "name", "") or "").strip()
    )
    return entries


def sync_detail_preset_list(owner, context, session, preset_type: str) -> int:
    """ダイアログ所有Operatorの独立UIListを現在の全プリセットと同期する。"""

    collection = getattr(owner, "detail_preset_items", None)
    if collection is None or not hasattr(owner, "detail_preset_index"):
        return -1
    preset_type = _preset_type(preset_type)
    entries = tuple(_detail_preset_entries(context, preset_type))
    selected = str(getattr(session, "preset_selection", "") or "")
    target = session.target
    selected_identifier = selected
    if preset_type == "balloon":
        shape = str(getattr(target.data, "shape", "") or "")
        selected_identifier = (
            selected if shape == "custom" and selected else f"shape:{shape}"
        )
        if shape != "custom":
            # フキダシの「プリセット保存対象値」は形状そのものなので、
            # 「形状」フィールドの直接編集は組み込み形状プリセットを選ぶの
            # と同義であり、「プリセット未保存の変更」ではない。基準値を
            # 現在値へ追従させ、一覧が追従選択した際にプリセット切り替え
            # 確認ダイアログが誤発動 (連続表示含む) しないようにする。
            # カスタム形状の輪郭編集だけは従来どおり確認の対象に残す。
            from ..utils import detail_preset_change_guard

            current = detail_preset_change_guard.capture_preset_settings(
                target, "balloon"
            )
            if getattr(session, "preset_baseline", None) not in (None, current):
                session.set_preset_baseline(current)
    signature = (
        str(session.token),
        preset_type,
        str(target.kind),
        str(target.stable_id),
        str(target.stack_uid or ""),
        selected,
        selected_identifier,
        entries,
    )
    if getattr(owner, "_detail_preset_list_signature", None) == signature:
        _restore_applied_preset_index(owner)
        return len(collection)

    collection.clear()
    selected_index = -1
    for index, (identifier, label, description) in enumerate(entries):
        item = collection.add()
        item.name = label
        item.identifier = identifier
        item.description = description
        item.preset_type = preset_type
        item.target_kind = target.kind
        item.target_id = target.stable_id
        item.stack_uid = target.stack_uid or ""
        item.session_token = session.token
        item.is_selected = identifier == selected_identifier
        if item.is_selected:
            selected_index = index
    owner._detail_preset_list_syncing = True
    try:
        owner.detail_preset_index = (
            selected_index if selected_index >= 0 else (0 if entries else -1)
        )
    finally:
        owner._detail_preset_list_syncing = False
    owner._detail_preset_list_signature = signature
    return len(entries)


def _restore_applied_preset_index(owner) -> None:
    """確認待ち／取消中は、UIListの選択表示を適用中の行へ戻す。"""

    collection = getattr(owner, "detail_preset_items", None)
    if collection is None:
        return
    selected_index = next(
        (index for index, item in enumerate(collection) if bool(item.is_selected)),
        -1,
    )
    if int(getattr(owner, "detail_preset_index", -1)) == selected_index:
        return
    owner._detail_preset_list_syncing = True
    try:
        owner.detail_preset_index = selected_index
    finally:
        owner._detail_preset_list_syncing = False


def sync_detail_linked_balloon_preset_list(owner, context, session) -> int:
    """リンクフキダシ用の「なし」を含む全一覧を独立UIListへ同期する。"""

    collection = getattr(owner, "detail_linked_balloon_items", None)
    if collection is None or not hasattr(owner, "detail_linked_balloon_index"):
        return -1
    from . import detail_transaction_action_op

    entries = tuple(detail_transaction_action_op._linked_balloon_preset_items(None, context))
    selected_name = str(getattr(session.target.data, "linked_balloon_preset", "") or "")
    selected_identifier = (
        f"{detail_transaction_action_op._LINKED_BALLOON_PRESET_PREFIX}{selected_name}"
        if selected_name
        else detail_transaction_action_op._NO_LINKED_BALLOON_PRESET
    )
    signature = (
        str(session.token),
        str(session.target.stable_id),
        selected_identifier,
        entries,
    )
    if getattr(owner, "_detail_linked_balloon_list_signature", None) == signature:
        return len(collection)

    collection.clear()
    selected_index = 0 if entries else -1
    for index, (identifier, label, description) in enumerate(entries):
        item = collection.add()
        item.name = str(label)
        item.identifier = str(identifier)
        item.description = str(description or "")
        item.target_id = session.target.stable_id
        item.session_token = session.token
        item.is_selected = item.identifier == selected_identifier
        if item.is_selected:
            selected_index = index
    owner._detail_linked_balloon_list_syncing = True
    try:
        owner.detail_linked_balloon_index = selected_index
    finally:
        owner._detail_linked_balloon_list_syncing = False
    owner._detail_linked_balloon_list_signature = signature
    return len(entries)


def _preset_enum_items(self, context):
    preset_type = _preset_type(getattr(self, "preset_type", ""))
    try:
        items = _detail_preset_entries(context, preset_type)
    except Exception:  # Blender のメニュー描画を壊さず実行時に改めて報告する
        _logger.exception("detail preset enum build failed: %s", preset_type)
        items = []
    if not items:
        items = [(_EMPTY_PRESET, "（プリセットなし）", "利用できるプリセットがありません")]
    _ENUM_CACHE[preset_type] = items
    return _ENUM_CACHE[preset_type]


def _fixed_stable_id(target_id: str, stable_id: str) -> str:
    first = str(target_id or "").strip()
    second = str(stable_id or "").strip()
    if first and second and first != second:
        raise ValueError("詳細設定の対象IDが一致しません")
    value = first or second
    if not value:
        raise ValueError("詳細設定の対象IDがありません")
    return value


def _resolve_fixed_target(context, *, preset_type: str, target_kind: str, target_id: str,
                          stable_id: str, stack_uid: str):
    from ..utils import detail_target_resolver

    expected_kind = _EXPECTED_TARGET_KIND.get(preset_type)
    actual_kind = str(target_kind or "").strip()
    if expected_kind is None:
        raise ValueError("このプリセット種別は詳細設定から適用できません")
    if actual_kind != expected_kind:
        raise ValueError("プリセットと詳細設定の対象種別が一致しません")
    fixed_id = _fixed_stable_id(target_id, stable_id)
    if detail_target_resolver.is_pointer_derived_uid(stack_uid):
        raise ValueError("旧形式の対象識別子は使用できません")
    target = detail_target_resolver.resolve_target_from_object(context, fixed_id, actual_kind)
    _verify_stack_identity(context, target, stack_uid)
    return target


def _verify_stack_identity(context, target, stack_uid: str) -> None:
    uid = str(stack_uid or "").strip()
    if not uid:
        return
    from ..utils import detail_target_resolver
    from ..utils.detail_dialog import DetailTargetNotFoundError

    try:
        stack_target = detail_target_resolver.resolve_target_from_stack(context, uid)
    except DetailTargetNotFoundError:
        return  # 一覧再構築後も永続IDで同じ対象を安全に解決できている
    if stack_target.kind != target.kind or stack_target.stable_id != target.stable_id:
        raise ValueError("詳細設定を開いた対象が変更されています")


def _require_preset(preset, preset_name: str):
    if preset is None:
        raise LookupError(f"プリセットが見つかりません: {preset_name}")
    return preset


def _apply_border(context, target, name: str) -> str:
    from ..io import border_presets
    from ..utils import coma_blur_curve, coma_border_object

    preset = _require_preset(border_presets.load_preset_by_name(name, _work_dir(context)), name)
    border_presets.apply_preset_to_coma(preset, target.data)
    border = getattr(target.data, "border", None)
    if border is not None:
        coma_border_object.on_coma_border_changed(border)
        if str(getattr(border, "style", "") or "") == "brush":
            coma_blur_curve.ensure_ui_curve_node(border)
    return str(preset.name)


def _apply_text(_context, target, name: str) -> str:
    from ..io import text_presets
    from ..utils import text_real_object

    preset = _require_preset(text_presets.load_preset_by_name(name), name)
    with text_real_object.suspend_auto_sync():
        text_presets.apply_to_entry(target.data, preset.data)
    text_real_object.on_text_entry_changed(target.data)
    return str(preset.name)


def _refresh_effect_curve_nodes(params) -> None:
    from ..utils import effect_inout_curve

    effect_inout_curve.ensure_ui_nodes(params)
    effect_inout_curve.ensure_profile_node(params)
    for fields, node_name, source_prop, label in (
        (effect_inout_curve.WHITE_PROFILE_FIELDS, effect_inout_curve.WHITE_PROFILE_NODE_NAME,
         effect_inout_curve.WHITE_PROFILE_SOURCE_PROP, "白線の線幅グラフ"),
        (effect_inout_curve.BLACK_PROFILE_FIELDS, effect_inout_curve.BLACK_PROFILE_NODE_NAME,
         effect_inout_curve.BLACK_PROFILE_SOURCE_PROP, "黒線の線幅グラフ"),
    ):
        if all(hasattr(params, attr) for attr in fields.values()):
            effect_inout_curve.ensure_profile_node(
                params, fields=fields, node_name=node_name, source_prop=source_prop, label=label
            )


def _apply_effect(context, target, name: str) -> str:
    from ..io import effect_line_presets
    from . import effect_line_op

    preset = _require_preset(
        effect_line_presets.load_preset_by_name(name, _work_dir(context)), name
    )
    effect_line_op._set_scene_params_syncing(context.scene, True)
    try:
        effect_line_presets.apply_preset_to_params(preset, target.params)
    finally:
        effect_line_op._set_scene_params_syncing(context.scene, False)
    _refresh_effect_curve_nodes(target.params)
    bounds = effect_line_op.effect_layer_bounds(target.object_ref, target.data)
    if bounds is None:
        raise RuntimeError("効果線の描画範囲を取得できません")
    effect_line_op._write_effect_strokes(
        context,
        target.object_ref,
        target.data,
        bounds,
        params_override=target.params,
        propagate_link=False,
    )
    return str(preset.name)


def _validate_fill_namespace(target, preset_type: str) -> None:
    is_gradient = str(getattr(target.data, "fill_type", "solid") or "solid") == "gradient"
    if preset_type == "gradient" and not is_gradient:
        raise ValueError("ベタ塗りにはグラデーションプリセットを適用できません")
    if preset_type == "fill" and is_gradient:
        raise ValueError("グラデーションにはベタ塗りプリセットを適用できません")


def _apply_fill(_context, target, name: str, preset_type: str) -> str:
    from ..io import fill_presets, gradient_presets
    from ..utils import fill_real_object

    _validate_fill_namespace(target, preset_type)
    module = gradient_presets if preset_type == "gradient" else fill_presets
    preset = _require_preset(module.load_preset_by_name(name), name)
    with fill_real_object.suspend_auto_sync():
        module.apply_to_entry(target.data, preset.data)
    fill_real_object.on_fill_entry_changed(target.data)
    return str(preset.name)


def _apply_image_path(context, target, name: str) -> str:
    from ..io import image_path_presets
    from ..utils import image_path_object

    preset = _require_preset(
        image_path_presets.load_preset_by_name(name, _work_dir(context)), name
    )
    with image_path_object.suspend_auto_sync():
        image_path_presets.apply_preset_to_entry(preset, target.data)
    image_path_object.on_image_path_entry_changed(target.data)
    return str(preset.name)


def _apply_balloon(_context, target, name: str) -> str:
    from ..io import balloon_presets
    from ..utils import balloon_curve_object

    if name.startswith("shape:"):
        shape = name.split(":", 1)[1]
        from ..core.balloon import _SHAPE_ITEMS

        shape_labels = {
            str(item[0]): str(item[1])
            for item in _SHAPE_ITEMS
            if str(item[0]) not in {"custom", "none"}
        }
        if shape not in shape_labels:
            raise LookupError(f"フキダシ形状が見つかりません: {shape}")
        with balloon_curve_object.suspend_auto_sync():
            target.data.custom_preset_name = ""
            target.data.shape = shape
        balloon_curve_object.on_balloon_entry_changed(target.data)
        return shape_labels[shape]

    preset_name = name.split(":", 1)[1] if name.startswith("custom:") else name
    preset = _require_preset(balloon_presets.load_preset_by_name(preset_name), preset_name)
    with balloon_curve_object.suspend_auto_sync():
        balloon_presets.apply_linked_text_settings(target.data, preset.data)
        target.data.custom_preset_name = str(preset.name)
        target.data.shape = "custom"
    balloon_curve_object.on_balloon_entry_changed(target.data)
    return str(preset.name)


def apply_preset_to_target(context, target, preset_type: str, preset_name: str) -> str:
    """固定済み ``DetailTarget`` だけへプリセットを適用して名称を返す。"""

    preset_type = _preset_type(preset_type)
    callbacks = {
        "border": lambda: _apply_border(context, target, preset_name),
        "text": lambda: _apply_text(context, target, preset_name),
        "effect_line": lambda: _apply_effect(context, target, preset_name),
        "fill": lambda: _apply_fill(context, target, preset_name, preset_type),
        "gradient": lambda: _apply_fill(context, target, preset_name, preset_type),
        "image_path": lambda: _apply_image_path(context, target, preset_name),
        "balloon": lambda: _apply_balloon(context, target, preset_name),
    }
    callback = callbacks.get(preset_type)
    if callback is None:
        raise ValueError("このプリセット種別は詳細設定から適用できません")
    return callback()


def _refresh_after_apply(context) -> None:
    try:
        from ..utils import layer_stack

        layer_stack.tag_view3d_redraw(context)
    except Exception:  # 再描画失敗で適用済みデータを破棄しない
        _logger.exception("detail preset redraw failed")


class BMANGA_OT_detail_preset_apply(Operator):
    """詳細設定を開いた時に固定した対象へプリセットを即時適用する。"""

    bl_idname = "bmanga.detail_preset_apply"
    bl_label = "プリセットを適用"
    bl_description = "この詳細設定の対象だけへプリセットを適用します"
    bl_options = {"INTERNAL"}
    bl_property = "preset_name"

    preset_type: StringProperty(  # type: ignore[valid-type]
        name="プリセット種別",
        default="",
        options={"HIDDEN"},
    )
    preset_name: EnumProperty(name="プリセット", items=_preset_enum_items)  # type: ignore[valid-type]
    target_kind: StringProperty(  # type: ignore[valid-type]
        name="対象種別",
        default="",
        options={"HIDDEN"},
    )
    target_id: StringProperty(  # type: ignore[valid-type]
        name="対象ID",
        default="",
        options={"HIDDEN"},
    )
    stable_id: StringProperty(  # type: ignore[valid-type]
        name="安定ID",
        default="",
        options={"HIDDEN"},
    )
    stack_uid: StringProperty(  # type: ignore[valid-type]
        name="一覧UID",
        default="",
        options={"HIDDEN"},
    )
    session_token: StringProperty(name="詳細設定セッション", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preset_label: StringProperty(name="表示名", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    confirm_unsaved_changes: BoolProperty(  # type: ignore[valid-type]
        name="未保存設定を確認",
        default=False,
        options={"HIDDEN"},
    )

    def _fixed_target(self, context, preset_type: str):
        target = _resolve_fixed_target(
            context,
            preset_type=preset_type,
            target_kind=self.target_kind,
            target_id=self.target_id,
            stable_id=self.stable_id,
            stack_uid=self.stack_uid,
        )
        if self.session_token:
            from . import detail_dialog_runtime

            if not detail_dialog_runtime.preset_session_is_open(
                self.session_token, target
            ):
                raise ValueError("詳細設定を開いた対象が変更されています")
        return target

    def invoke(self, context, event):
        if not self.confirm_unsaved_changes or not self.session_token:
            return self.execute(context)
        try:
            preset_type = _preset_type(self.preset_type)
            target = self._fixed_target(context, preset_type)
            from . import detail_dialog_runtime

            if not detail_dialog_runtime.preset_switch_requires_confirmation(
                context,
                self.session_token,
                target,
                preset_type,
            ):
                return self.execute(context)
        except (LookupError, RuntimeError, ValueError, ReferenceError) as exc:
            self.report({"WARNING"}, str(exc) or "プリセットを切り替えられません")
            return {"CANCELLED"}
        label = str(self.preset_label or self.preset_name or "選択プリセット")
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="プリセットの切り替え確認",
            message=(
                "現在の設定はプリセットに保存されていません。"
                f"保存せずに「{label}」へ切り替えますか？"
            ),
            confirm_text="保存せずに切り替える",
            icon="QUESTION",
        )

    def execute(self, context):
        preset_type = _preset_type(self.preset_type)
        preset_name = str(self.preset_name or "").strip()
        if not preset_name or preset_name == _EMPTY_PRESET:
            self.report({"WARNING"}, "適用できるプリセットがありません")
            return {"CANCELLED"}
        try:
            target = self._fixed_target(context, preset_type)
            if self.session_token:
                from . import detail_dialog_runtime

                applied_name = detail_dialog_runtime.execute_transactional_detail_action(
                    context,
                    self.session_token,
                    self.bl_idname,
                    target.kind,
                    target.stable_id,
                    lambda fixed_target: apply_preset_to_target(
                        context,
                        fixed_target,
                        preset_type,
                        preset_name,
                    ),
                )
            else:
                applied_name = apply_preset_to_target(
                    context, target, preset_type, preset_name
                )
        except (LookupError, RuntimeError, ValueError, ReferenceError) as exc:
            self.report({"WARNING"}, str(exc) or "詳細設定の対象へ適用できませんでした")
            return {"CANCELLED"}
        except Exception as exc:  # 予期しない失敗も別対象へのフォールバックはしない
            _logger.exception("detail preset apply failed")
            self.report({"ERROR"}, f"プリセットを適用できませんでした: {exc}")
            return {"CANCELLED"}
        if self.session_token:
            detail_dialog_runtime.record_preset_selection(
                self.session_token,
                target,
                applied_name,
                preset_type=preset_type,
            )
            try:
                detail_dialog_runtime.mark_preset_settings_saved(
                    context,
                    self.session_token,
                    target,
                    preset_type,
                )
            except Exception:  # 適用済みデータは維持し、次回は安全側の再確認に倒す
                _logger.exception("failed to update preset switch baseline")
                self.report(
                    {"WARNING"},
                    "プリセットは適用しましたが、変更検知を更新できませんでした",
                )
        _refresh_after_apply(context)
        self.report({"INFO"}, f"プリセットを適用しました: {applied_name}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_DetailPresetListItem,
    BMANGA_UL_detail_presets,
    BMANGA_UL_detail_linked_balloon_presets,
    BMANGA_OT_detail_preset_apply,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "BMANGA_DetailPresetListItem",
    "BMANGA_UL_detail_linked_balloon_presets",
    "BMANGA_UL_detail_presets",
    "BMANGA_OT_detail_preset_apply",
    "apply_preset_to_target",
    "on_detail_linked_balloon_index_changed",
    "on_detail_preset_index_changed",
    "sync_detail_preset_list",
    "sync_detail_linked_balloon_preset_list",
    "register",
    "unregister",
]
