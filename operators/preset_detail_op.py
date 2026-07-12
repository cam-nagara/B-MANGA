"""プリセット詳細設定ダイアログ.

各プリセットタイプ用の専用ダイアログを持つのをやめ、各ツールが普段使っている
詳細設定描画関数 (panels/coma_detail_panel.py・operators/layer_detail_op.py・
panels/effect_line_panel.py・panels/layer_stack_detail_ui.py・
operators/balloon_tail_detail_op.py) をそのまま共用する。

流れ:
  invoke  — プリセット JSON を読み込み、WindowManager 上のスクラッチ
            PropertyGroup (実コマ/実テキスト等とは無関係な入れ物) へ
            io の apply 関数で流し込む。
  draw    — 説明編集欄 + 各ツールの詳細設定描画関数 (``preset_mode=True``)。
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


def _draw_border(layout, context) -> None:
    from ..panels import coma_detail_panel

    scratch = context.window_manager.bmanga_preset_scratch_border
    border_box = layout.box()
    border_box.label(text="枠線")
    coma_detail_panel.draw_coma_border_settings(border_box, context, scratch, preset_mode=True)
    white_box = layout.box()
    white_box.label(text="フチ")
    coma_detail_panel.draw_coma_white_margin_settings(white_box, scratch)


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


def _draw_text(layout, context) -> None:
    from . import layer_detail_op

    scratch = context.window_manager.bmanga_preset_scratch_text
    layer_detail_op._draw_text_detail(layout, context, scratch, preset_mode=True)


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


def _draw_effect_line(layout, context) -> None:
    from . import layer_detail_op
    from ..panels import effect_line_panel

    scratch = context.window_manager.bmanga_preset_scratch_effect_line
    cols = layer_detail_op._equal_columns(layout, 2)
    effect_line_panel.draw_effect_params(
        cols[0], scratch, with_generate_button=False, columns=cols, preset_mode=True
    )


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


def _draw_fill(layout, context) -> None:
    from ..panels import layer_stack_detail_ui

    scratch = context.window_manager.bmanga_preset_scratch_fill
    layer_stack_detail_ui._draw_fill_selected_settings(layout, context, scratch, preset_mode=True)


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


def _draw_gradient(layout, context) -> None:
    from ..panels import layer_stack_detail_ui

    scratch = context.window_manager.bmanga_preset_scratch_gradient
    layer_stack_detail_ui._draw_fill_selected_settings(layout, context, scratch, preset_mode=True)


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


def _draw_image_path(layout, context) -> None:
    from . import layer_detail_op

    scratch = context.window_manager.bmanga_preset_scratch_image_path
    layer_detail_op._draw_image_path_detail(layout, context, scratch, preset_mode=True)


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


def _draw_tail(layout, context) -> None:
    from . import balloon_tail_detail_op

    scratch = context.window_manager.bmanga_preset_scratch_tail
    balloon_tail_detail_op._draw_tail_box(layout, context, None, None, scratch, 0, preset_mode=True)


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
_DrawerFn = Callable[[Any, Any], None]
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
_DRAWERS: dict[str, _DrawerFn] = {
    "border": _draw_border,
    "text": _draw_text,
    "effect_line": _draw_effect_line,
    "fill": _draw_fill,
    "gradient": _draw_gradient,
    "image_path": _draw_image_path,
    "tail": _draw_tail,
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

_DIALOG_WIDTH: dict[str, int] = {
    "border": 320,
    "balloon": 320,
    "text": 340,
    "effect_line": 560,
    "fill": 300,
    "gradient": 320,
    "image_path": 340,
    "tail": 300,
}


class BMANGA_OT_preset_detail_edit(Operator):
    bl_idname = "bmanga.preset_detail_edit"
    bl_label = "プリセット詳細設定"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    preset_type: StringProperty()  # type: ignore[valid-type]
    preset_name: StringProperty()  # type: ignore[valid-type]

    description_text: StringProperty(name="説明")  # type: ignore[valid-type]

    _balloon_data: dict[str, Any] = {}

    def invoke(self, context, event):
        pt = self.preset_type
        if pt == "balloon":
            data = _load_balloon(self.preset_name)
            if data is None:
                self.report({"WARNING"}, f"プリセットが見つかりません: {self.preset_name}")
                return {"CANCELLED"}
            self._balloon_data = data
            self.description_text = str(data.get("description", "") or "")
            return context.window_manager.invoke_props_dialog(self, width=_DIALOG_WIDTH.get(pt, 320))

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
        return context.window_manager.invoke_props_dialog(self, width=_DIALOG_WIDTH.get(pt, 320))

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "description_text")
        layout.separator()
        pt = self.preset_type
        drawer = _DRAWERS.get(pt)
        if drawer is None:
            layout.label(text="このプリセットタイプは詳細編集未対応です")
            return
        drawer(layout, context)

    def execute(self, context):
        pt = self.preset_type
        from ..panels import preset_list_ui

        if pt == "balloon":
            try:
                _save_balloon(self.preset_name, self.description_text, self._balloon_data)
            except Exception:  # noqa: BLE001
                _logger.exception("failed to save balloon preset description %s", self.preset_name)
                self.report({"WARNING"}, f"プリセット「{self.preset_name}」の保存に失敗しました")
                return {"CANCELLED"}
            preset_list_ui.refresh_preset_list(context, pt)
            self.report({"INFO"}, f"プリセット「{self.preset_name}」を保存しました")
            return {"FINISHED"}

        saver = _SAVERS.get(pt)
        if saver is None:
            self.report({"WARNING"}, f"未対応のプリセットタイプです: {pt}")
            return {"CANCELLED"}
        try:
            saver(context, self.preset_name, self.description_text)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to save preset %s/%s", pt, self.preset_name)
            self.report({"WARNING"}, f"プリセット「{self.preset_name}」の保存に失敗しました")
            return {"CANCELLED"}
        preset_list_ui.refresh_preset_list(context, pt)
        self.report({"INFO"}, f"プリセット「{self.preset_name}」を保存しました")
        return {"FINISHED"}


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
