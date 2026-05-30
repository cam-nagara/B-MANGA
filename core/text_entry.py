"""テキストエントリ PropertyGroup (フキダシ内テキスト/擬音/ナレーション共通).

計画書 3.1.4.4 / 3.1.5 参照。縦書き・ルビ・縦中横・白フチ・行間/字間を
保持する。実際の組版レンダリングは typography/ が担当する。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from ..utils import log
from ..utils.geom import pt_to_q, q_to_pt

_logger = log.get_logger(__name__)


_WRITING_MODE_ITEMS = (
    ("vertical", "縦書き", ""),
    ("horizontal", "横書き", ""),
)

_SPEAKER_TYPE_ITEMS = (
    ("normal", "通常セリフ", ""),
    ("thought", "思考", ""),
    ("shout", "叫び", ""),
    ("narration", "ナレーション", ""),
    ("monologue", "モノローグ", ""),
    ("sfx", "擬音", ""),
    ("custom", "カスタム", ""),
)

_FONT_SIZE_UNIT_ITEMS = (
    ("q", "Q", "Q 数"),
    ("pt", "pt", "ポイント"),
)

_font_size_sync_depth = 0


class BNameRubySpan(bpy.types.PropertyGroup):
    """親文字範囲とルビ (フリガナ) を対応付ける."""

    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1)  # type: ignore[valid-type]
    ruby_text: StringProperty(name="ルビ", default="")  # type: ignore[valid-type]
    # ルビスタイル: monoRuby (1文字1ルビ), groupRuby (親語全体に), jukugoRuby (熟語ルビ)
    style: EnumProperty(  # type: ignore[valid-type]
        name="スタイル",
        items=(
            ("mono", "モノルビ", ""),
            ("group", "グループルビ", ""),
            ("jukugo", "熟語ルビ", ""),
        ),
        default="mono",
    )


class BNameTextFontSpan(bpy.types.PropertyGroup):
    """本文内の一部範囲に適用するフォント指定."""

    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1)  # type: ignore[valid-type]
    font: StringProperty(name="フォント", default="", subtype="FILE_PATH")  # type: ignore[valid-type]


class BNameTextStyleSpan(bpy.types.PropertyGroup):
    """本文内の一部範囲に適用する文字スタイル."""

    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1)  # type: ignore[valid-type]
    font: StringProperty(name="フォント", default="", subtype="FILE_PATH")  # type: ignore[valid-type]
    font_size_q: FloatProperty(name="サイズ (Q)", default=20.0, min=1.0, soft_max=200.0)  # type: ignore[valid-type]
    color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]
    font_bold: BoolProperty(name="太字", default=False)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False)  # type: ignore[valid-type]


def _on_text_visible_changed(_self, context) -> None:
    _on_text_entry_changed(_self, context)


def _on_text_entry_changed(self, context) -> None:
    try:
        from ..utils import text_real_object

        text_real_object.on_text_entry_changed(self)
    except Exception:  # noqa: BLE001
        pass
    try:
        screen = getattr(context, "screen", None) if context is not None else None
        if screen is not None:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def _on_text_free_transform_changed(self, context) -> None:
    try:
        from ..utils import text_real_object

        text_real_object.on_text_free_transform_changed(self)
    except Exception:  # noqa: BLE001
        pass
    try:
        screen = getattr(context, "screen", None) if context is not None else None
        if screen is not None:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def _on_text_font_size_q_changed(self, context) -> None:
    global _font_size_sync_depth
    if _font_size_sync_depth > 0:
        return
    _font_size_sync_depth += 1
    try:
        self.font_size_pt = max(0.1, float(q_to_pt(float(getattr(self, "font_size_q", 20.0) or 20.0))))
    finally:
        _font_size_sync_depth -= 1
    _on_text_entry_changed(self, context)


def _on_text_font_size_pt_changed(self, context) -> None:
    global _font_size_sync_depth
    if _font_size_sync_depth > 0:
        return
    _font_size_sync_depth += 1
    try:
        self.font_size_q = max(0.1, float(pt_to_q(float(getattr(self, "font_size_pt", 9.0) or 9.0))))
    finally:
        _font_size_sync_depth -= 1
    _on_text_entry_changed(self, context)


def _get_text_font_size_value(self) -> float:
    if str(getattr(self, "font_size_unit", "q") or "q") == "pt":
        return float(getattr(self, "font_size_pt", q_to_pt(float(getattr(self, "font_size_q", 20.0)))) or 0.0)
    return float(getattr(self, "font_size_q", 20.0) or 0.0)


def _set_text_font_size_value(self, value: float) -> None:
    size = max(0.1, float(value or 0.0))
    if str(getattr(self, "font_size_unit", "q") or "q") == "pt":
        self.font_size_pt = size
    else:
        self.font_size_q = size


class BNameTextEntry(bpy.types.PropertyGroup):
    """1 つのテキストオブジェクト.

    Phase 3 以降、テキストはページ単位 (``BNamePageEntry.texts``) で保持し、
    ``parent_balloon_id`` 経由でフキダシと親子連動する (フキダシ移動で子
    テキストも同じ delta で動く)。
    """

    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        default=True,
        update=_on_text_visible_changed,
    )
    selected: BoolProperty(  # type: ignore[valid-type]
        name="マルチ選択",
        default=False,
        options={"SKIP_SAVE"},
    )
    body: StringProperty(name="本文", default="", options={"TEXTEDIT_UPDATE"}, update=_on_text_entry_changed)  # type: ignore[valid-type]

    # ページローカル座標 (mm). overlay 描画時にページ grid offset を加算する。
    x_mm: FloatProperty(name="X", default=0.0, update=_on_text_entry_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0, update=_on_text_entry_changed)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅", default=30.0, min=0.1, update=_on_text_entry_changed)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ", default=15.0, min=0.1, update=_on_text_entry_changed)  # type: ignore[valid-type]
    free_transform_enabled: BoolProperty(name="自由変形", default=False, options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_bottom_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_bottom_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_top_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_top_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]

    # 親フキダシ (同一ページの BNameBalloonEntry.id を参照). 空文字なら独立テキスト。
    parent_balloon_id: StringProperty(  # type: ignore[valid-type]
        name="親フキダシ ID",
        description="同じページの BNameBalloonEntry.id を参照。空で独立テキスト。",
        default="",
        update=_on_text_entry_changed,
    )
    # レイヤーリスト上のページ/コマ親子付け。空の旧データは位置から親を推定する。
    parent_kind: StringProperty(name="親種別", default="page", update=_on_text_entry_changed)  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="", update=_on_text_entry_changed)  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", default="", update=_on_text_entry_changed)  # type: ignore[valid-type]
    speaker_type: EnumProperty(  # type: ignore[valid-type]
        name="セリフ種別",
        items=_SPEAKER_TYPE_ITEMS,
        default="normal",
    )
    speaker_name: StringProperty(name="話者", default="")  # type: ignore[valid-type]

    font: StringProperty(name="基本フォント", default="", subtype="FILE_PATH", update=_on_text_entry_changed)  # type: ignore[valid-type]
    font_size_q: FloatProperty(  # type: ignore[valid-type]
        name="サイズ (Q)",
        description="文字サイズを Q 数 (1 Q = 0.25 mm) で指定",
        default=20.0,
        min=1.0,
        soft_max=200.0,
        update=_on_text_font_size_q_changed,
    )
    font_size_pt: FloatProperty(  # type: ignore[valid-type]
        name="サイズ (pt)",
        description="文字サイズを pt で指定",
        default=q_to_pt(20.0),
        min=0.1,
        soft_max=200.0,
        update=_on_text_font_size_pt_changed,
    )
    font_size_unit: EnumProperty(  # type: ignore[valid-type]
        name="サイズ単位",
        description="テキストサイズの表示・入力単位",
        items=_FONT_SIZE_UNIT_ITEMS,
        default="q",
    )
    font_size_value: FloatProperty(  # type: ignore[valid-type]
        name="サイズ",
        description="現在のサイズ単位で表示・入力する文字サイズ",
        default=20.0,
        min=0.1,
        soft_max=200.0,
        precision=3,
        get=_get_text_font_size_value,
        set=_set_text_font_size_value,
    )
    # Meldex の fontBold / fontItalic 相当
    font_bold: BoolProperty(name="太字", default=False, update=_on_text_entry_changed)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False, update=_on_text_entry_changed)  # type: ignore[valid-type]
    color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_text_entry_changed)  # type: ignore[valid-type]
    writing_mode: EnumProperty(items=_WRITING_MODE_ITEMS, default="vertical", update=_on_text_entry_changed)  # type: ignore[valid-type]
    line_height: FloatProperty(name="行間", default=1.4, min=0.5, soft_max=3.0, update=_on_text_entry_changed)  # type: ignore[valid-type]
    letter_spacing: FloatProperty(name="字間", default=0.0, soft_min=-1.0, soft_max=1.0, update=_on_text_entry_changed)  # type: ignore[valid-type]

    # 白フチ (計画書 3.1.4.4)
    stroke_enabled: BoolProperty(name="白フチ", default=False, update=_on_text_entry_changed)  # type: ignore[valid-type]
    stroke_width_mm: FloatProperty(name="フチ幅", default=0.2, min=0.0, soft_max=5.0, update=_on_text_entry_changed)  # type: ignore[valid-type]
    stroke_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, update=_on_text_entry_changed)  # type: ignore[valid-type]

    # ルビ (複数スパン)
    ruby_spans: CollectionProperty(type=BNameRubySpan)  # type: ignore[valid-type]

    # 部分フォント。font が空の範囲は基本フォントに戻す扱い。
    font_spans: CollectionProperty(type=BNameTextFontSpan)  # type: ignore[valid-type]

    # 部分スタイル。font が空の範囲は基本フォントに戻す扱い。
    style_spans: CollectionProperty(type=BNameTextStyleSpan)  # type: ignore[valid-type]

    # 縦中横 (horizontal-in-vertical): 指定した範囲を縦書き内で横向きに
    tatechuyoko_ranges: CollectionProperty(type=BNameRubySpan)  # type: ignore[valid-type]


_CLASSES = (
    BNameRubySpan,
    BNameTextFontSpan,
    BNameTextStyleSpan,
    BNameTextEntry,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("text_entry registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
