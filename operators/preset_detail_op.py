"""共通描画APIを使うプリセット詳細設定ダイアログ。

流れ:
  invoke  — プリセット JSON を読み込み、WindowManager 上のスクラッチ
            PropertyGroup (実コマ/実テキスト等とは無関係な入れ物) へ
            io の apply 関数で流し込む。
  draw    — 説明編集欄 + 保存可能項目だけを共通の順序で描画。
  execute — スクラッチ PropertyGroup から io の snapshot/save 関数で
            同じプリセット名へ上書き保存する。

スクラッチ PropertyGroup は WindowManager 上に持たせるため Blender セッション
中ずっと生き続ける。update= コールバックの安全性 (実シーンの実データを書き
換えないこと) は各コールバックがポインタ同一性 or entry.id で実体を解決する
既存の仕組みに依存しており、スクラッチ側の id は常に空文字のままにする
(実体側の id が空になることはない前提。border は id を持たない専用の
入れ物クラスを新設し、そもそも対象を持たない)。唯一 id に依存しない
core/effect_line.py の ``_on_params_changed`` だけは、対象が
``scene.bmanga_effect_line_params`` と同一インスタンスかを明示的に確認する
ガードを追加している。
"""

from __future__ import annotations

from typing import Any, Callable

import bpy
from bpy.props import FloatVectorProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from ..core.balloon import BMangaBalloonTail
from ..core.coma_border import BMangaComaBorder, BMangaComaWhiteMargin
from ..core.effect_line import BMangaEffectLineParams
from ..core.fill_layer import BMangaFillLayer
from ..core.image_path_layer import BMangaImagePathLayer
from ..core.text_entry import BMangaTextEntry
from ..utils import log
from ..utils.detail_dialog import (
    DetailContractError,
    DetailMode,
    current_column_count_for_target,
    resolve_detail_layout,
    resolve_preset_detail_target,
)
from ..utils.detail_dialog_state import (
    begin_detail_session,
    cancel_detail_session,
    commit_detail_session,
)
from ..utils.detail_state_adapters import ACTUAL_DETAIL_STATE_REGISTRY
from ..panels.detail_drawers import draw_detail_dialog

_logger = log.get_logger(__name__)


# ────────────────────────────────────────────────────────────────
# スクラッチ PropertyGroup
# ────────────────────────────────────────────────────────────────


class _BMangaPresetScratchComa(PropertyGroup):
    """枠線プリセット詳細編集用のスクラッチ入れ物 (実コマとは無関係).

    io/border_presets.py の ``apply_preset_to_coma`` / ``save_local_preset``
    は ``coma.border`` / ``coma.white_margin`` に加え ``coma.paper_visible`` /
    ``coma.background_color`` も参照するため、それらも保持する。実 Coma の
    それらのプロパティと違い update= コールバックは付けない (実シーンへは
    一切同期しないダミー値のため付ける必要が無い)。
    """

    border: PointerProperty(type=BMangaComaBorder)  # type: ignore[valid-type]
    white_margin: PointerProperty(type=BMangaComaWhiteMargin)  # type: ignore[valid-type]
    paper_visible: bpy.props.BoolProperty(default=True)  # type: ignore[valid-type]
    background_color: FloatVectorProperty(  # type: ignore[valid-type]
        subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0
    )


# WindowManager 側スクラッチ属性名 → 保持する PropertyGroup 型。
# fill と gradient は同じ BMangaFillLayer だが、プリセットの実体
# (JSON 保存先ディレクトリ) が別なので取り違え防止のため別インスタンスにする。
_SCRATCH_WM_PROPS: tuple[tuple[str, type], ...] = (
    ("bmanga_preset_scratch_border", _BMangaPresetScratchComa),
    ("bmanga_preset_scratch_text", BMangaTextEntry),
    ("bmanga_preset_scratch_effect_line", BMangaEffectLineParams),
    ("bmanga_preset_scratch_fill", BMangaFillLayer),
    ("bmanga_preset_scratch_gradient", BMangaFillLayer),
    ("bmanga_preset_scratch_image_path", BMangaImagePathLayer),
    ("bmanga_preset_scratch_tail", BMangaBalloonTail),
)


def _reset_props(instance) -> None:
    """PropertyGroup インスタンスの全プロパティを型のデフォルト値へ戻す.

    WindowManager 上のスクラッチ入れ物は Blender セッション中ずっと生き
    続ける。一方、io 側の apply 関数の多くは「JSON に無いキーはスキップ」
    する部分適用のため、先に別のプリセットを編集した際の値が残ったまま
    だと、今回読み込むプリセットに存在しない項目が古い値を引きずって
    しまう。プリセットを読み込む直前に必ず呼び、型のデフォルトへ戻して
    から適用する。COLLECTION / POINTER (ネストした PropertyGroup) は
    対象外 — 呼び出し側が個別に扱う (例: border の ``.border``/
    ``.white_margin``、tail の ``.points``)。
    """
    props = getattr(getattr(instance, "bl_rna", None), "properties", None)
    if props is None:
        return
    for prop in props:
        identifier = prop.identifier
        if identifier == "rna_type":
            continue
        if getattr(prop, "is_readonly", False):
            continue
        if str(getattr(prop, "type", "")) in {"COLLECTION", "POINTER"}:
            continue
        try:
            instance.property_unset(identifier)
        except Exception:  # noqa: BLE001
            pass


# ────────────────────────────────────────────────────────────────
# タイプ別 load / draw / save
# ────────────────────────────────────────────────────────────────


def _load_border(context, preset_name: str) -> str | None:
    from ..io import border_presets

    preset = border_presets.load_preset_by_name(preset_name, None)
    if preset is None:
        return None
    scratch = context.window_manager.bmanga_preset_scratch_border
    _reset_props(scratch)
    _reset_props(scratch.border)
    _reset_props(scratch.white_margin)
    border_presets.apply_preset_to_coma(preset, scratch)
    return str(preset.data.get("description", "") or "")


def _save_border(context, preset_name: str, description: str) -> None:
    from ..io import border_presets

    scratch = context.window_manager.bmanga_preset_scratch_border
    original = border_presets.load_preset_by_name(preset_name, None)
    original_data = original.data if original is not None else {}
    new_data = border_presets.preset_dict_from_coma(scratch, preset_name, description)
    for key in ("paperVisible", "backgroundColor", "backgroundColorAlpha"):
        if key in original_data:
            new_data[key] = original_data[key]
        else:
            new_data.pop(key, None)
    border_presets._write_local_preset_data(None, new_data, preset_name, description=description)


def _load_text(context, preset_name: str) -> str | None:
    from ..io import text_presets

    preset = text_presets.load_preset_by_name(preset_name)
    if preset is None:
        return None
    scratch = context.window_manager.bmanga_preset_scratch_text
    _reset_props(scratch)
    text_presets.apply_to_entry(scratch, preset.data)
    return str(preset.data.get("description", "") or "")


def _save_text(context, preset_name: str, description: str) -> None:
    from ..io import text_presets

    scratch = context.window_manager.bmanga_preset_scratch_text
    data = text_presets.snapshot_from_entry(scratch)
    text_presets.save_local_preset(None, preset_name, description, data)


def _load_effect_line(context, preset_name: str) -> str | None:
    from ..io import effect_line_presets

    preset = effect_line_presets.load_preset_by_name(preset_name, None)
    if preset is None:
        return None
    scratch = context.window_manager.bmanga_preset_scratch_effect_line
    _reset_props(scratch)
    effect_line_presets.apply_preset_to_params(preset, scratch)
    return str(preset.data.get("description", "") or "")


def _save_effect_line(context, preset_name: str, description: str) -> None:
    from ..io import effect_line_presets

    scratch = context.window_manager.bmanga_preset_scratch_effect_line
    effect_line_presets.save_local_preset(None, scratch, preset_name, description)


def _load_fill(context, preset_name: str) -> str | None:
    from ..io import fill_presets

    preset = fill_presets.load_preset_by_name(preset_name, None)
    if preset is None:
        return None
    scratch = context.window_manager.bmanga_preset_scratch_fill
    _reset_props(scratch)
    scratch.fill_type = "solid"
    fill_presets.apply_to_entry(scratch, preset.data)
    return str(preset.data.get("description", "") or "")


def _save_fill(context, preset_name: str, description: str) -> None:
    from ..io import fill_presets

    scratch = context.window_manager.bmanga_preset_scratch_fill
    data = fill_presets.snapshot_from_entry(scratch)
    fill_presets.save_local_preset(preset_name, description, data)


def _load_gradient(context, preset_name: str) -> str | None:
    from ..io import gradient_presets

    preset = gradient_presets.load_preset_by_name(preset_name, None)
    if preset is None:
        return None
    scratch = context.window_manager.bmanga_preset_scratch_gradient
    _reset_props(scratch)
    scratch.fill_type = "gradient"
    gradient_presets.apply_to_entry(scratch, preset.data)
    return str(preset.data.get("description", "") or "")


def _save_gradient(context, preset_name: str, description: str) -> None:
    from ..io import gradient_presets

    scratch = context.window_manager.bmanga_preset_scratch_gradient
    data = gradient_presets.snapshot_from_entry(scratch)
    gradient_presets.save_local_preset(preset_name, description, data)


def _load_image_path(context, preset_name: str) -> str | None:
    from ..io import image_path_presets

    preset = image_path_presets.load_preset_by_name(preset_name, None)
    if preset is None:
        return None
    scratch = context.window_manager.bmanga_preset_scratch_image_path
    _reset_props(scratch)
    image_path_presets.apply_preset_to_entry(preset, scratch)
    return str(preset.data.get("description", "") or "")


def _save_image_path(context, preset_name: str, description: str) -> None:
    from ..io import image_path_presets

    scratch = context.window_manager.bmanga_preset_scratch_image_path
    image_path_presets.save_local_preset(None, scratch, preset_name, description)


def _load_tail(context, preset_name: str) -> str | None:
    from ..io import tail_presets

    preset = tail_presets.load_preset_by_name(preset_name, None)
    if preset is None:
        return None
    scratch = context.window_manager.bmanga_preset_scratch_tail
    _reset_props(scratch)
    scratch.points.clear()
    tail_presets.apply_preset_to_tail(preset, scratch)
    return str(preset.data.get("description", "") or "")


def _save_tail(context, preset_name: str, description: str) -> None:
    from ..io import tail_presets

    scratch = context.window_manager.bmanga_preset_scratch_tail
    tail_presets.save_local_preset(None, scratch, preset_name, description)


# balloon は対象外 (プリセットの実体が頂点座標列で、ツール詳細ダイアログが
# 存在しない)。説明編集のみ現行どおり残す。


def _load_balloon(preset_name: str) -> dict[str, Any] | None:
    from ..io import balloon_presets

    preset = balloon_presets.load_preset_by_name(preset_name)
    if preset is None:
        return None
    return dict(preset.data)


def _save_balloon(preset_name: str, description: str, data: dict[str, Any]) -> None:
    from ..io import balloon_presets

    payload = dict(data)
    payload["description"] = description
    balloon_presets._write_local_preset_data(payload, preset_name, description=description)


_LoaderFn = Callable[[Any, str], "str | None"]
_SaverFn = Callable[[Any, str, str], None]

_LOADERS: dict[str, _LoaderFn] = {
    "border": _load_border,
    "text": _load_text,
    "effect_line": _load_effect_line,
    "fill": _load_fill,
    "gradient": _load_gradient,
    "image_path": _load_image_path,
    "tail": _load_tail,
}
_SAVERS: dict[str, _SaverFn] = {
    "border": _save_border,
    "text": _save_text,
    "effect_line": _save_effect_line,
    "fill": _save_fill,
    "gradient": _save_gradient,
    "image_path": _save_image_path,
    "tail": _save_tail,
}


_PRESET_SCRATCH_ATTRS = {
    "border": "bmanga_preset_scratch_border",
    "text": "bmanga_preset_scratch_text",
    "effect_line": "bmanga_preset_scratch_effect_line",
    "fill": "bmanga_preset_scratch_fill",
    "gradient": "bmanga_preset_scratch_gradient",
    "image_path": "bmanga_preset_scratch_image_path",
    "tail": "bmanga_preset_scratch_tail",
}


def _available_dialog_width(context) -> int | None:
    try:
        width = int(context.window.width)
    except (AttributeError, TypeError, ValueError):
        width = 0
    return width if width > 0 else None


def _preset_target_data(context, preset_type: str, balloon_data: dict[str, Any]):
    if preset_type == "balloon":
        return balloon_data
    attr = _PRESET_SCRATCH_ATTRS.get(preset_type)
    if attr is None:
        return None
    return getattr(context.window_manager, attr, None)


def _preset_target_is_alive(stable_id: str):
    """プリセット編集対象は固定IDを持つ一時データとして生存判定する。"""

    return lambda identity: identity.stable_id == stable_id


def _prepare_preset_curve_ui(preset_type: str, target) -> None:
    if preset_type == "effect_line":
        from ..utils import effect_inout_curve

        effect_inout_curve.restore_ui_nodes_from_params(target.params)
    elif preset_type == "border":
        from ..utils import coma_blur_curve

        coma_blur_curve.restore_ui_curve_from_border(getattr(target.data, "border", None))


def _sync_preset_curve_ui(preset_type: str, target) -> bool:
    if preset_type == "effect_line":
        from . import detail_dialog_runtime

        return bool(detail_dialog_runtime._sync_curve_nodes(target.params))
    if preset_type == "border":
        from ..utils import coma_blur_curve

        return bool(coma_blur_curve.sync_ui_curve_to_border(getattr(target.data, "border", None)))
    return False


class BMANGA_OT_preset_detail_edit(Operator):
    bl_idname = "bmanga.preset_detail_edit"
    bl_label = "プリセット詳細設定"
    bl_options = {"REGISTER", "INTERNAL"}

    preset_type: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]
    preset_name: StringProperty(options={"HIDDEN"})  # type: ignore[valid-type]
    parent_session_token: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    parent_target_kind: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    parent_target_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    description_text: StringProperty(name="説明")  # type: ignore[valid-type]

    _balloon_data: dict[str, Any] = {}
    _detail_session: Any = None

    def _open_detail_session(self, context):
        data = _preset_target_data(context, self.preset_type, self._balloon_data)
        if data is None:
            raise RuntimeError(f"プリセット一時設定を取得できません: {self.preset_type}")
        params = data if self.preset_type == "effect_line" else None
        target = resolve_preset_detail_target(
            self.preset_type,
            self.preset_name,
            data,
            params=params,
        )
        _prepare_preset_curve_ui(self.preset_type, target)
        available_width = _available_dialog_width(context)
        fixed_layout = resolve_detail_layout(
            target,
            DetailMode.PRESET,
            available_width=available_width,
        )
        self._detail_session = begin_detail_session(
            target,
            DetailMode.PRESET,
            registry=ACTUAL_DETAIL_STATE_REGISTRY,
            target_validator=_preset_target_is_alive(target.stable_id),
            available_width=fixed_layout.available_width,
            current_columns=fixed_layout.column_count,
            section_columns=fixed_layout.section_columns,
        )
        from . import detail_dialog_runtime

        try:
            detail_dialog_runtime.register_preset_session(self._detail_session)
        except Exception:
            cancel_detail_session(self._detail_session)
            self._detail_session = None
            raise
        return self._invoke_detail_dialog(context)

    def _invoke_detail_dialog(self, context):
        try:
            result = context.window_manager.invoke_props_dialog(
                self,
                width=self._detail_session.layout.dialog_width,
            )
        except Exception as opening_error:  # noqa: BLE001
            try:
                self._restore_open_session()
            except Exception as restore_error:  # noqa: BLE001
                raise RuntimeError(
                    f"開始失敗後に一時設定を復元できませんでした: {restore_error}"
                ) from opening_error
            raise
        if "CANCELLED" in result:
            self._restore_open_session()
        return result

    def invoke(self, context, event):
        self._detail_session = None
        if not self._parent_session_is_valid():
            self.report({"WARNING"}, "元の詳細設定を開き直してください")
            return {"CANCELLED"}
        pt = self.preset_type
        if str(self.parent_session_token or ""):
            # Blenderは親のinvoke_props_dialog中に別のprops dialogを安全に
            # 開けない。親画面では、既に表示中の現在設定を明示的に上書きする。
            return self._overwrite_from_parent(context)
        from . import detail_dialog_runtime

        try:
            # 同じ種別はWindowManager上の同じscratchを共有する。先に拒否し、
            # 2画面目のloaderが1画面目の編集中値を上書きしないようにする。
            detail_dialog_runtime.ensure_preset_type_available(pt)
        except DetailContractError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        if pt == "balloon":
            data = _load_balloon(self.preset_name)
            if data is None:
                self.report({"WARNING"}, f"プリセットが見つかりません: {self.preset_name}")
                return {"CANCELLED"}
            self._balloon_data = data
            self.description_text = str(data.get("description", "") or "")
            try:
                return self._open_detail_session(context)
            except Exception:  # noqa: BLE001
                _logger.exception("failed to open preset detail %s/%s", pt, self.preset_name)
                self.report({"WARNING"}, "プリセット詳細設定を開けませんでした")
                return {"CANCELLED"}

        loader = _LOADERS.get(pt)
        if loader is None:
            self.report({"WARNING"}, f"未対応のプリセットタイプです: {pt}")
            return {"CANCELLED"}
        try:
            description = loader(context, self.preset_name)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to load preset %s/%s", pt, self.preset_name)
            description = None
        if description is None:
            self.report({"WARNING"}, f"プリセットが見つかりません: {self.preset_name}")
            return {"CANCELLED"}
        self.description_text = description
        try:
            return self._open_detail_session(context)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to open preset detail %s/%s", pt, self.preset_name)
            self.report({"WARNING"}, "プリセット詳細設定を開けませんでした")
            return {"CANCELLED"}

    def draw(self, context):
        layout = self.layout
        session = self._detail_session
        if session is None:
            layout.prop(self, "description_text")
            layout.separator()
            layout.label(text="このプリセットタイプは詳細編集未対応です")
            return
        draw_detail_dialog(
            layout,
            context,
            session,
            DetailMode.PRESET,
            description_owner=self,
        )

    def check(self, context):
        session = self._detail_session
        if session is None:
            return False
        changed = _sync_preset_curve_ui(self.preset_type, session.target)
        column_count = current_column_count_for_target(session.target, DetailMode.PRESET)
        if column_count == session.layout.column_count:
            return changed
        session.set_current_columns(column_count)
        return True

    def execute(self, context):
        pt = self.preset_type
        from ..panels import preset_list_ui

        if not self._parent_session_is_valid():
            self.report({"WARNING"}, "元の詳細設定を開き直してください")
            self._release_failed_session()
            return {"CANCELLED"}
        # 親props dialog内ではBlenderがinvokeを省略してexecuteへ直接入る場合も
        # あるため、こちらにも同じ独立即時経路を置く。
        if str(self.parent_session_token or ""):
            return self._overwrite_from_parent(context)
        if self._detail_session is None:
            self.report({"WARNING"}, "プリセット詳細設定を開き直してください")
            return {"CANCELLED"}
        _sync_preset_curve_ui(pt, self._detail_session.target)

        if pt == "balloon":
            try:
                _save_balloon(self.preset_name, self.description_text, self._balloon_data)
            except Exception:  # noqa: BLE001
                _logger.exception("failed to save balloon preset description %s", self.preset_name)
                self.report({"WARNING"}, f"プリセット「{self.preset_name}」の保存に失敗しました")
                self._release_failed_session()
                return {"CANCELLED"}
            self._commit_open_session()
            self._record_parent_action()
            preset_list_ui.refresh_preset_list(context, pt)
            self.report({"INFO"}, f"プリセット「{self.preset_name}」を保存しました")
            return {"FINISHED"}

        saver = _SAVERS.get(pt)
        if saver is None:
            self.report({"WARNING"}, f"未対応のプリセットタイプです: {pt}")
            self._release_failed_session()
            return {"CANCELLED"}
        try:
            saver(context, self.preset_name, self.description_text)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to save preset %s/%s", pt, self.preset_name)
            self.report({"WARNING"}, f"プリセット「{self.preset_name}」の保存に失敗しました")
            self._release_failed_session()
            return {"CANCELLED"}
        self._commit_open_session()
        self._record_parent_action()
        preset_list_ui.refresh_preset_list(context, pt)
        self.report({"INFO"}, f"プリセット「{self.preset_name}」を保存しました")
        return {"FINISHED"}

    def _overwrite_from_parent(self, context):
        try:
            from . import detail_preset_management_op
            from ..panels import preset_list_ui

            name = detail_preset_management_op.overwrite_selected_preset(context, self)
            preset_list_ui.refresh_preset_list(context, self.preset_type)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("failed to overwrite preset from parent detail")
            self.report({"WARNING"}, str(exc) or "プリセットを保存できませんでした")
            return {"CANCELLED"}
        self.report({"INFO"}, f"プリセット「{name}」を現在の設定で保存しました")
        return {"FINISHED"}

    def _parent_session_is_valid(self) -> bool:
        if not str(self.parent_session_token or ""):
            return True
        from . import detail_dialog_runtime

        return detail_dialog_runtime.detail_action_is_allowed(
            self.parent_session_token,
            self.bl_idname,
            self.parent_target_kind,
            self.parent_target_id,
        )

    def _record_parent_action(self) -> None:
        if not str(self.parent_session_token or ""):
            return
        from . import detail_dialog_runtime

        detail_dialog_runtime.record_detail_action(
            self.parent_session_token,
            self.bl_idname,
            self.parent_target_kind,
            self.parent_target_id,
            self.preset_name,
        )

    def cancel(self, context):
        try:
            self._restore_open_session()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "failed to restore preset detail %s/%s",
                self.preset_type,
                self.preset_name,
            )
            self.report({"ERROR"}, "一時設定を元に戻せませんでした")

    def _commit_open_session(self) -> None:
        from . import detail_dialog_runtime

        session = self._detail_session
        if session is None:
            raise RuntimeError("プリセット詳細設定を開き直してください")
        self._detail_session = None
        try:
            commit_detail_session(session)
        finally:
            detail_dialog_runtime.unregister_preset_session(session)

    def _release_failed_session(self) -> None:
        try:
            self._restore_open_session()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "failed to restore preset after operation failure %s/%s",
                self.preset_type,
                self.preset_name,
            )

    def _restore_open_session(self) -> None:
        session = self._detail_session
        self._detail_session = None
        if session is None:
            return
        from . import detail_dialog_runtime
        failure = None
        try:
            for _attempt in range(2):
                try:
                    cancel_detail_session(session)
                    failure = None
                    break
                except Exception as exc:  # noqa: BLE001
                    failure = exc
            if failure is not None:
                raise failure
            _prepare_preset_curve_ui(self.preset_type, session.target)
        finally:
            detail_dialog_runtime.unregister_preset_session(session)


_CLASSES = (_BMangaPresetScratchComa, BMANGA_OT_preset_detail_edit)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for attr, prop_type in _SCRATCH_WM_PROPS:
        setattr(bpy.types.WindowManager, attr, PointerProperty(type=prop_type))


def unregister():
    for attr, _prop_type in _SCRATCH_WM_PROPS:
        try:
            delattr(bpy.types.WindowManager, attr)
        except AttributeError:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
