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
    ("vertical", "縦書き", "文字を上から下へ縦に並べます"),
    ("horizontal", "横書き", "文字を左から右へ横に並べます"),
)

_RUBY_ALIGN_ITEMS = (
    ("center", "中付き", "ルビと親文字の中心を揃える（JIS標準）"),
    ("start", "肩付き", "ルビと親文字の先頭を揃える"),
)

_RUBY_SMALL_KANA_ITEMS = (
    ("keep", "小書きのまま", "ゃゅょっ等をそのまま表示"),
    ("fullsize", "直音に変換", "ゃ→や、ゅ→ゆ 等に変換して表示"),
)

_FONT_SIZE_UNIT_ITEMS = (
    ("q", "Q", "Q 数"),
    ("pt", "pt", "ポイント"),
)

_font_size_sync_depth = 0


class BMangaRubySpan(bpy.types.PropertyGroup):
    """親文字範囲とルビ (フリガナ) を対応付ける."""

    start: IntProperty(name="開始", default=0, min=0, description="ルビを付ける親文字の開始位置（文字インデックス）")  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1, description="ルビを付ける親文字の文字数")  # type: ignore[valid-type]
    ruby_text: StringProperty(name="ルビ", default="", description="親文字に付けるふりがな")  # type: ignore[valid-type]
    # ルビスタイル: monoRuby (1文字1ルビ), groupRuby (親語全体に), jukugoRuby (熟語ルビ)
    style: EnumProperty(  # type: ignore[valid-type]
        name="スタイル",
        description="ルビの割り付け方式",
        items=(
            ("mono", "モノルビ", "1文字ずつにルビを付けます"),
            ("group", "グループルビ", "親文字列全体にまとめてルビを付けます"),
            ("jukugo", "熟語ルビ", "熟語全体に対してルビを配置します"),
        ),
        default="mono",
    )


class BMangaTextFontSpan(bpy.types.PropertyGroup):
    """本文内の一部範囲に適用するフォント指定."""

    start: IntProperty(name="開始", default=0, min=0, description="フォントを適用する開始位置（文字インデックス）")  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1, description="フォントを適用する文字数")  # type: ignore[valid-type]
    font: StringProperty(name="フォント", default="", subtype="FILE_PATH", description="この範囲に適用するフォント")  # type: ignore[valid-type]


class BMangaTextStyleSpan(bpy.types.PropertyGroup):
    """本文内の一部範囲に適用する文字スタイル."""

    start: IntProperty(name="開始", default=0, min=0, description="スタイルを適用する開始位置（文字インデックス）")  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1, description="スタイルを適用する文字数")  # type: ignore[valid-type]
    font: StringProperty(name="フォント", default="", subtype="FILE_PATH", description="この範囲に適用するフォント")  # type: ignore[valid-type]
    font_size_q: FloatProperty(name="サイズ (Q)", default=20.0, min=1.0, soft_max=200.0, description="この範囲の文字サイズを Q 数 (1 Q = 0.25 mm) で指定")  # type: ignore[valid-type]
    color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, description="この範囲の文字色")  # type: ignore[valid-type]
    font_bold: BoolProperty(name="太字", default=False, description="この範囲の文字を太字にします")  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False, description="この範囲の文字を斜体にします")  # type: ignore[valid-type]


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


def _sync_layer_stack_title(context, entry=None) -> None:
    if entry is not None and not str(getattr(entry, "id", "") or "").strip():
        return
    try:
        from ..utils import layer_stack as layer_stack_utils

        layer_stack_utils.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001
        pass


def _on_text_title_changed(self, context) -> None:
    _on_text_entry_changed(self, context)
    _sync_layer_stack_title(context, self)


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


class BMangaTextEntry(bpy.types.PropertyGroup):
    """1 つのテキストオブジェクト.

    Phase 3 以降、テキストはページ単位 (``BMangaPageEntry.texts``) で保持し、
    ``parent_balloon_id`` 経由でフキダシと親子連動する (フキダシ移動で子
    テキストも同じ delta で動く)。
    """

    id: StringProperty(name="ID", default="", description="このテキストを識別するID")  # type: ignore[valid-type]
    meldex_source_document_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    meldex_source_row_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    meldex_type: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    title: StringProperty(name="名前", default="", description="レイヤー一覧に表示するこのテキストの名前", update=_on_text_title_changed)  # type: ignore[valid-type]
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        description="このテキストを表示 / 非表示にします",
        default=True,
        update=_on_text_visible_changed,
    )
    selected: BoolProperty(  # type: ignore[valid-type]
        name="マルチ選択",
        default=False,
        options={"SKIP_SAVE"},
    )
    body: StringProperty(name="本文", default="", description="表示する本文（セリフ本文）", options={"TEXTEDIT_UPDATE"}, update=_on_text_entry_changed)  # type: ignore[valid-type]

    # ページローカル座標 (mm). overlay 描画時にページ grid offset を加算する。
    x_mm: FloatProperty(name="X", default=0.0, description="ページ上でのX座標（mm）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0, description="ページ上でのY座標（mm）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅", default=30.0, min=0.1, description="テキスト枠の幅（mm）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ", default=15.0, min=0.1, description="テキスト枠の高さ（mm）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="回転", description="テキストの回転角度（選択枠の中心を軸に回転します）", default=0.0, update=_on_text_entry_changed)  # type: ignore[valid-type]
    free_transform_enabled: BoolProperty(name="自由変形", default=False, options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_bottom_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_bottom_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_top_left: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]
    free_transform_top_right: FloatVectorProperty(size=2, default=(0.0, 0.0), options={"HIDDEN"}, update=_on_text_free_transform_changed)  # type: ignore[valid-type]

    # 親フキダシ (同一ページの BMangaBalloonEntry.id を参照). 空文字なら独立テキスト。
    parent_balloon_id: StringProperty(  # type: ignore[valid-type]
        name="親フキダシ ID",
        description="同じページの BMangaBalloonEntry.id を参照。空で独立テキスト。",
        default="",
        update=_on_text_entry_changed,
    )
    # レイヤーリスト上のページ/コマ親子付け。空の旧データは位置から親を推定する。
    parent_kind: StringProperty(name="親種別", default="page", update=_on_text_entry_changed)  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="", update=_on_text_entry_changed)  # type: ignore[valid-type]
    folder_key: StringProperty(name="レイヤーフォルダ", default="", update=_on_text_entry_changed)  # type: ignore[valid-type]
    speaker_name: StringProperty(name="話者", default="", description="このセリフを話すキャラクター名")  # type: ignore[valid-type]

    font: StringProperty(name="基本フォント", default="", description="本文全体に使う基本フォント（部分フォントで範囲ごとに上書き可能）", subtype="FILE_PATH", update=_on_text_entry_changed)  # type: ignore[valid-type]
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
    font_bold: BoolProperty(name="太字", default=False, description="本文全体を太字にします", update=_on_text_entry_changed)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False, description="本文全体を斜体にします", update=_on_text_entry_changed)  # type: ignore[valid-type]
    color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, description="本文の文字色", update=_on_text_entry_changed)  # type: ignore[valid-type]
    writing_mode: EnumProperty(items=_WRITING_MODE_ITEMS, default="vertical", description="テキストの書字方向を選択します", update=_on_text_entry_changed)  # type: ignore[valid-type]
    line_height: FloatProperty(name="行間", default=1.4, min=0.5, soft_max=3.0, description="行と行の間隔（文字サイズに対する倍率）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    letter_spacing: FloatProperty(name="字間", default=0.0, soft_min=-1.0, soft_max=1.0, description="文字と文字の間隔（文字サイズに対する倍率）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    ruby_line_height: FloatProperty(name="ルビ行の行間", default=1.8, min=0.5, soft_max=4.0, description="ルビを含む行の行間（文字サイズに対する倍率）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    ruby_gap_mm: FloatProperty(name="親文字との間隔", default=0.0, min=0.0, soft_max=5.0, description="親文字とルビの間隔（mm）", unit="LENGTH", update=_on_text_entry_changed)  # type: ignore[valid-type]
    ruby_letter_spacing: FloatProperty(name="ルビの字間", default=0.0, soft_min=-0.9, soft_max=3.0, description="ルビ文字同士の間隔（文字サイズに対する倍率）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    ruby_size_percent: FloatProperty(name="サイズ（親文字比%）", default=50.0, min=5.0, soft_max=200.0, description="ルビの文字サイズを親文字に対する割合(%)で指定", subtype="PERCENTAGE", update=_on_text_entry_changed)  # type: ignore[valid-type]
    ruby_font: StringProperty(name="ルビ用フォント", default="", description="ルビに使うフォント（空欄なら基本フォントを使用）", subtype="FILE_PATH", update=_on_text_entry_changed)  # type: ignore[valid-type]
    ruby_align: EnumProperty(name="配置方法", description="ルビと親文字の位置揃え方を選択します", items=_RUBY_ALIGN_ITEMS, default="center", update=_on_text_entry_changed)  # type: ignore[valid-type]
    ruby_small_kana: EnumProperty(name="小書き仮名", description="小書き仮名（ゃゅょっ等）の表示方法を選択します", items=_RUBY_SMALL_KANA_ITEMS, default="keep", update=_on_text_entry_changed)  # type: ignore[valid-type]

    # 白フチ (計画書 3.1.4.4)
    stroke_enabled: BoolProperty(name="白フチ", default=False, description="文字の周囲に白フチを付けます", update=_on_text_entry_changed)  # type: ignore[valid-type]
    stroke_width_mm: FloatProperty(name="フチ幅", default=0.2, min=0.0, soft_max=5.0, description="白フチの太さ（mm）", update=_on_text_entry_changed)  # type: ignore[valid-type]
    stroke_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0, description="白フチの色", update=_on_text_entry_changed)  # type: ignore[valid-type]

    # リンクフキダシプリセット
    linked_balloon_preset: StringProperty(  # type: ignore[valid-type]
        name="リンクフキダシプリセット",
        description="このテキストに連動するフキダシプリセット名（空で連動なし）",
        default="",
        update=_on_text_entry_changed,
    )

    # ルビ (複数スパン)
    ruby_spans: CollectionProperty(type=BMangaRubySpan, description="本文内のルビ設定の一覧")  # type: ignore[valid-type]

    # 部分フォント。font が空の範囲は基本フォントに戻す扱い。
    font_spans: CollectionProperty(type=BMangaTextFontSpan, description="本文内の部分フォント指定の一覧")  # type: ignore[valid-type]

    # 部分スタイル。font が空の範囲は基本フォントに戻す扱い。
    style_spans: CollectionProperty(type=BMangaTextStyleSpan, description="本文内の部分スタイル指定の一覧")  # type: ignore[valid-type]

    # 縦中横 (horizontal-in-vertical): 指定した範囲を縦書き内で横向きに
    tatechuyoko_ranges: CollectionProperty(type=BMangaRubySpan, description="縦中横（縦書き内で横向きに並べる範囲）の一覧")  # type: ignore[valid-type]


_CLASSES = (
    BMangaRubySpan,
    BMangaTextFontSpan,
    BMangaTextStyleSpan,
    BMangaTextEntry,
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
