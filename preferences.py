"""B-MANGA AddonPreferences.

Phase 0 時点では以下を提供:
- ログレベル
- Meldex 受信サーバーのポート（Phase 5 で利用、UI は先に用意）
- B-MANGA 専用キーマップのトグル
- 右クリック=スポイト モードのスイッチ
- Spaceバー既定挙動の退避情報（デバッグ表示）
- アセットライブラリ登録ガイド

``__package__`` が ``b_manga`` のようなアドオン ID 名を指す前提。
Blender 4.3+ / 5.x の Extensions Platform 配下では ``bl_idname`` に
``__package__`` を使う。
"""

from __future__ import annotations

import secrets

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
from bpy.types import Operator, PropertyGroup

from .utils import log

ADDON_ID = __package__ or "b_manga"

_LOG_LEVEL_ITEMS = (
    ("DEBUG", "Debug", "詳細ログ"),
    ("INFO", "Info", "標準ログ (既定)"),
    ("WARNING", "Warning", "警告以上のみ"),
    ("ERROR", "Error", "エラーのみ"),
)

_SPACEBAR_PRESET_ITEMS = (
    ("AUTO", "Auto", "現在の Blender 設定を検出して自動選択"),
    ("TOOL", "Tool", "Space = ツール切替 (Blender 既定)"),
    ("SEARCH", "Search", "Space = 検索メニュー"),
    ("PLAY", "Playback", "Space = 再生"),
)

_USERPREF_SAVE_PENDING = False
_USERPREF_SAVE_SUSPENDED = 0
_MELDEX_UPDATE_DEPTH = 0


def _save_user_preferences_timer():
    """変更後のプリファレンスを Blender のユーザー設定へ保存する."""
    global _USERPREF_SAVE_PENDING
    _USERPREF_SAVE_PENDING = False
    try:
        bpy.ops.wm.save_userpref()
    except Exception:  # noqa: BLE001
        log.get_logger(__name__).exception("B-MANGA preferences auto-save failed")
    return None


def request_user_preferences_save() -> None:
    """プリファレンス保存を短く遅延してまとめる."""
    global _USERPREF_SAVE_PENDING
    if _USERPREF_SAVE_SUSPENDED > 0:
        return
    if _USERPREF_SAVE_PENDING:
        return
    _USERPREF_SAVE_PENDING = True
    try:
        bpy.app.timers.register(_save_user_preferences_timer, first_interval=0.8)
    except Exception:  # noqa: BLE001
        _save_user_preferences_timer()


def _on_preferences_changed(_self, _context) -> None:  # noqa: ANN001 - Blender callback
    request_user_preferences_save()


def _on_meldex_settings_changed(self, context) -> None:  # noqa: ANN001
    global _MELDEX_UPDATE_DEPTH
    if _MELDEX_UPDATE_DEPTH:
        return
    _MELDEX_UPDATE_DEPTH += 1
    try:
        if bool(getattr(self, "meldex_enabled", False)) and not str(getattr(self, "meldex_token", "") or ""):
            self.meldex_token = secrets.token_hex(32)
        from .io import meldex_receiver

        if not meldex_receiver.restart_from_preferences(context):
            self.meldex_enabled = False
    finally:
        _MELDEX_UPDATE_DEPTH = max(0, _MELDEX_UPDATE_DEPTH - 1)
        request_user_preferences_save()


def _on_log_level_changed(self, _context) -> None:  # noqa: ANN001 - Blender callback
    log.set_level(self.log_level)
    request_user_preferences_save()


def _on_gpencil_follow_changed(self, _context=None) -> None:  # noqa: ANN001 - Blender callback
    """preferences.gpencil_follow_cursor 変更で watcher を即時起動/停止.

    Blender 拡張環境 (特に 5.1.2 以降) では ``update=lambda ...`` のラムダから
    モジュール関数を参照すると globals 解決に失敗し
    ``NameError: name '...' is not defined`` になることがある。そのため
    ``update=`` には名前付き関数を直接渡し、この関数内では ``self`` と関数内
    import だけで完結させる (他のモジュール globals を参照しない)。

    アドオン register/unregister の過渡状態 (operators モジュールがまだ
    完全に初期化されていない / 既に unregister 済) でも安全に no-op
    できるよう、全例外を握り潰す。
    """
    try:
        from .operators import gpencil_op

        if bool(getattr(self, "gpencil_follow_cursor", False)):
            gpencil_op._follow_start()
        else:
            gpencil_op._follow_stop()
    except Exception:  # noqa: BLE001
        pass
    finally:
        request_user_preferences_save()


def _on_keymap_settings_changed(self, _context) -> None:
    """preferences のキーマップ設定が変わったら addon kc を再構築する.

    register/unregister 中に呼ばれた場合は keymap モジュールが未初期化の
    可能性があるので例外を握り潰す。
    """
    try:
        from .keymap import keymap as _kmap

        _kmap.rebuild_keymap_from_prefs()
    except Exception:  # noqa: BLE001
        pass
    finally:
        request_user_preferences_save()


def _on_page_preview_resolution_changed(self, context) -> None:  # noqa: ANN001
    try:
        from .utils import page_preview_object, view_settings

        scene = getattr(context or bpy.context, "scene", None)
        if scene is None:
            return
        work = getattr(scene, "bmanga_work", None)
        value = view_settings.default_page_preview_resolution_percentage(context)
        if work is not None and hasattr(work, "view_page_preview_resolution_percentage"):
            work.view_page_preview_resolution_percentage = value
        if hasattr(scene, "bmanga_page_preview_resolution_percentage"):
            scene.bmanga_page_preview_resolution_percentage = value
        if work is not None and hasattr(work, "page_preview_scale_percentage"):
            work.page_preview_scale_percentage = value
        if hasattr(scene, "bmanga_coma_camera_preview_scale_percentage"):
            scene.bmanga_coma_camera_preview_scale_percentage = value
        if work is not None and getattr(work, "loaded", False):
            page_preview_object.sync_page_previews(context, work)
    except Exception:  # noqa: BLE001
        pass
    finally:
        request_user_preferences_save()


def _on_coma_thumb_scale_changed(self, context) -> None:  # noqa: ANN001
    try:
        scene = getattr(context or bpy.context, "scene", None)
        if scene is None:
            return
        work = getattr(scene, "bmanga_work", None)
        value = float(getattr(self, "coma_thumb_scale_percentage", 12.5) or 12.5)
        if work is not None and hasattr(work, "page_preview_scale_percentage"):
            work.page_preview_scale_percentage = value
        if hasattr(scene, "bmanga_coma_camera_preview_scale_percentage"):
            scene.bmanga_coma_camera_preview_scale_percentage = value
    except Exception:  # noqa: BLE001
        pass
    finally:
        request_user_preferences_save()


class BMangaRubyDictEntry(PropertyGroup):
    """自動ルビ用の辞書ファイルエントリ."""

    path: StringProperty(  # type: ignore[valid-type]
        name="辞書ファイル",
        description="IME / Google日本語入力の辞書テキストファイル (.txt)",
        subtype="FILE_PATH",
        update=_on_preferences_changed,
    )
    enabled: BoolProperty(  # type: ignore[valid-type]
        name="有効",
        description="この辞書を自動ルビ変換に使用するか",
        default=True,
        update=_on_preferences_changed,
    )


class BMangaPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    log_level: EnumProperty(  # type: ignore[valid-type]
        name="ログレベル",
        description="B-MANGA アドオンのログレベル",
        items=_LOG_LEVEL_ITEMS,
        default="INFO",
        update=_on_log_level_changed,
    )

    meldex_enabled: BoolProperty(  # type: ignore[valid-type]
        name="Meldexからの受信を有効にする",
        description="このPCのMeldexからシナリオを受け取ります（既定はオフ）",
        default=False,
        update=_on_meldex_settings_changed,
    )
    meldex_port: IntProperty(  # type: ignore[valid-type]
        name="Meldex 受信ポート",
        description="Meldex からのシナリオ受信に使う localhost ポート (Phase 5)",
        default=47817,
        min=1024,
        max=65535,
        update=_on_meldex_settings_changed,
    )
    meldex_token: StringProperty(  # type: ignore[valid-type]
        name="接続トークン",
        description="Meldexの送信画面へ入力する、このPC専用の接続トークン",
        default="",
        options={"HIDDEN"},
    )

    keymap_enabled: BoolProperty(  # type: ignore[valid-type]
        name="B-MANGA 専用キーマップを有効化",
        description="CLIP STUDIO PAINT 準拠のビューポート操作ショートカットを有効にする",
        default=True,
        update=_on_keymap_settings_changed,
    )

    right_click_eyedropper: BoolProperty(  # type: ignore[valid-type]
        name="右クリックをスポイトに割り当てる",
        description="B-MANGA モード中のみ、右クリックの既定動作をスポイトに切り替える",
        default=False,
        update=_on_keymap_settings_changed,
    )

    spacebar_preset: EnumProperty(  # type: ignore[valid-type]
        name="Spaceバー挙動 (検出用)",
        description="既定キーマップの Space キー挙動。AUTO 以外は退避処理のベースとして使用",
        items=_SPACEBAR_PRESET_ITEMS,
        default="AUTO",
        update=_on_keymap_settings_changed,
    )

    global_asset_library: StringProperty(  # type: ignore[valid-type]
        name="グローバルアセットライブラリ パス",
        description="全作品共通で参照するアセットの格納先 (Blender 設定でアセットライブラリとして登録)",
        default=r"D:\Develop\Blender\B-MANGA-Assets",
        subtype="DIR_PATH",
        update=_on_preferences_changed,
    )

    coma_blend_template_path: StringProperty(  # type: ignore[valid-type]
        name="コマ用blendファイル (共通)",
        description=(
            "全作品共通で使うコマ用blendファイル (.blend)。"
            "作品情報パネル側のコマ用blendファイルが空の場合に、こちらが使われる。"
        ),
        default="",
        subtype="FILE_PATH",
        update=_on_preferences_changed,
    )

    snap_gutter_to_finish: BoolProperty(  # type: ignore[valid-type]
        name="ノド側は仕上がり枠にもスナップ",
        description="コマ枠の三角ハンドルでノド側へ広げる時、仕上がり枠も候補にします",
        default=True,
        update=_on_preferences_changed,
    )

    ruby_dictionaries: CollectionProperty(  # type: ignore[valid-type]
        name="自動ルビ辞書",
        description="登録されている自動ルビ辞書の一覧",
        type=BMangaRubyDictEntry,
    )
    ruby_dict_active_index: IntProperty(  # type: ignore[valid-type]
        name="選択中の辞書",
        description="自動ルビ辞書一覧で選択中の項目",
        default=0,
        update=_on_preferences_changed,
    )

    gpencil_follow_cursor: BoolProperty(  # type: ignore[valid-type]
        name="カーソル追従でアクティブページ切替",
        description=(
            "overview モード中、マウス位置のページを自動で active_page_index に "
            "設定する (master GP 統一後は GP 切替ではなくページ index 追従のみ)"
        ),
        default=True,
        update=_on_gpencil_follow_changed,
    )

    text_selection_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="テキスト選択ハイライト色",
        description="テキスト編集中の選択範囲の色",
        subtype="COLOR_GAMMA",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.0, 0.7, 1.0, 0.45),
        update=_on_preferences_changed,
    )

    default_base_font_path: StringProperty(  # type: ignore[valid-type]
        name="標準フォント",
        description=(
            "テキストやルビにフォントが設定されていない時に使うフォントファイル。"
            "空欄ならOS標準の日本語フォントを自動選択"
        ),
        default="",
        subtype="FILE_PATH",
        update=_on_preferences_changed,
    )

    last_balloon_tool_preset: StringProperty(  # type: ignore[valid-type]
        name="前回のフキダシ形状",
        default="DEFAULT",
        options={"HIDDEN"},
    )
    last_tail_preset: StringProperty(  # type: ignore[valid-type]
        name="前回のしっぽプリセット",
        default="",
        options={"HIDDEN"},
    )
    last_text_tool_preset: StringProperty(  # type: ignore[valid-type]
        name="前回のテキストプリセット",
        default="",
        options={"HIDDEN"},
    )
    last_fill_tool_preset: StringProperty(  # type: ignore[valid-type]
        name="前回の囲い塗りプリセット",
        default="black",
        options={"HIDDEN"},
    )
    last_gradient_tool_preset: StringProperty(  # type: ignore[valid-type]
        name="前回のグラデーションプリセット",
        default="bw_linear",
        options={"HIDDEN"},
    )
    last_image_path_tool_preset: StringProperty(  # type: ignore[valid-type]
        name="前回のパターンカーブプリセット",
        default="標準スタンプ",
        options={"HIDDEN"},
    )
    last_effect_line_tool_preset: StringProperty(  # type: ignore[valid-type]
        name="前回の効果線プリセット",
        default="集中線",
        options={"HIDDEN"},
    )

    page_preview_resolution_percentage: FloatProperty(  # type: ignore[valid-type]
        name="プレビュー画像縮小率",
        description="ページ一覧プレビューとコマ画像の縮小率",
        default=25.0,
        min=1.0,
        soft_max=100.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_page_preview_resolution_changed,
    )

    coma_thumb_scale_percentage: FloatProperty(  # type: ignore[valid-type]
        name="コマ画像縮小率",
        description="ページ一覧に表示するコマ画像PNGの縮小率（後方互換用）",
        default=12.5,
        min=1.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_coma_thumb_scale_changed,
    )

    # ---------- ショートカットキーのカスタマイズ ----------
    # 各機能ごとに「キー文字列 + Shift/Ctrl/Alt 修飾」を保持する。
    # キー文字列は Blender の Event.type 名 (例: "SPACE", "O", "P",
    # "COMMA", "PERIOD", "LEFTMOUSE", "WHEELUPMOUSE")。
    # 値が変わると _on_keymap_settings_changed が addon kc を作り直す。

    key_navigate: StringProperty(  # type: ignore[valid-type]
        name="ナビゲート (パン/回転/ズーム統合)",
        description="このキー押下中の LMB ドラッグでパン/回転/ズーム",
        default="SPACE",
        update=_on_keymap_settings_changed,
    )

    key_set_mode_object: StringProperty(  # type: ignore[valid-type]
        name="オブジェクトツール切替",
        description="オブジェクトツールに切り替えるキー",
        default="O",
        update=_on_keymap_settings_changed,
    )
    mod_set_mode_object_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", description="オブジェクトツール切替キーにShiftを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_object_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", description="オブジェクトツール切替キーにCtrlを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_object_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", description="オブジェクトツール切替キーにAltを追加", default=False, update=_on_keymap_settings_changed
    )

    key_set_mode_draw: StringProperty(  # type: ignore[valid-type]
        name="描画ツール切替",
        description="描画ツールに切り替えるキー",
        default="P",
        update=_on_keymap_settings_changed,
    )
    mod_set_mode_draw_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", description="描画ツール切替キーにShiftを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_draw_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", description="描画ツール切替キーにCtrlを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_draw_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", description="描画ツール切替キーにAltを追加", default=False, update=_on_keymap_settings_changed
    )

    key_page_next: StringProperty(  # type: ignore[valid-type]
        name="次のページ",
        description="次のページに移動するキー",
        default="COMMA",
        update=_on_keymap_settings_changed,
    )
    mod_page_next_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", description="次のページキーにShiftを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_page_next_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", description="次のページキーにCtrlを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_page_next_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", description="次のページキーにAltを追加", default=False, update=_on_keymap_settings_changed
    )

    key_page_prev: StringProperty(  # type: ignore[valid-type]
        name="前のページ",
        description="前のページに移動するキー",
        default="PERIOD",
        update=_on_keymap_settings_changed,
    )
    mod_page_prev_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", description="前のページキーにShiftを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_page_prev_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", description="前のページキーにCtrlを追加", default=False, update=_on_keymap_settings_changed
    )
    mod_page_prev_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", description="前のページキーにAltを追加", default=False, update=_on_keymap_settings_changed
    )

    def draw(self, context) -> None:  # noqa: D401, ANN001
        layout = self.layout

        box = layout.box()
        box.label(text="ログ / デバッグ")
        box.prop(self, "log_level")

        box = layout.box()
        box.label(text="Meldex 連携")
        box.prop(self, "meldex_enabled")
        column = box.column(align=True)
        column.enabled = self.meldex_enabled
        column.prop(self, "meldex_port")
        row = column.row(align=True)
        row.prop(self, "meldex_token", text="接続トークン")
        row.operator("bmanga.meldex_token_regenerate", text="再生成", icon="FILE_REFRESH")
        if self.meldex_enabled:
            box.label(text="ポートを使用できない場合は自動的にオフになります", icon="INFO")

        box = layout.box()
        box.label(text="キーマップ")
        box.prop(self, "keymap_enabled")
        sub = box.column()
        sub.enabled = self.keymap_enabled
        sub.prop(self, "right_click_eyedropper")
        sub.prop(self, "spacebar_preset")

        box = layout.box()
        box.label(text="テキスト編集")
        box.prop(self, "text_selection_color", text="選択ハイライト色")
        box.prop(self, "default_base_font_path", text="標準フォント")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="テキストやルビにフォントが設定されていない時に使うフォント", icon="INFO")
        col.label(text="空欄ならOS標準の日本語フォントを自動選択")

        box = layout.box()
        box.label(text="コマ枠編集")
        box.prop(self, "snap_gutter_to_finish")

        # ショートカットキー カスタマイズ
        kbox = layout.box()
        kbox.label(text="ショートカットキー (変更後は自動反映)")
        kbox.enabled = self.keymap_enabled

        row = kbox.row(align=True)
        row.label(text="ナビゲート (パン/回転/ズーム)", icon="ORIENTATION_VIEW")
        row.prop(self, "key_navigate", text="")

        for label, key_attr, mod_prefix, icon in (
            ("オブジェクトツール", "key_set_mode_object", "mod_set_mode_object", "OBJECT_DATAMODE"),
            ("描画ツール", "key_set_mode_draw", "mod_set_mode_draw", "GREASEPENCIL"),
            ("次のページ", "key_page_next", "mod_page_next", "TRIA_RIGHT"),
            ("前のページ", "key_page_prev", "mod_page_prev", "TRIA_LEFT"),
        ):
            row = kbox.row(align=True)
            row.label(text=label, icon=icon)
            row.prop(self, f"{mod_prefix}_shift", toggle=True)
            row.prop(self, f"{mod_prefix}_ctrl", toggle=True)
            row.prop(self, f"{mod_prefix}_alt", toggle=True)
            row.prop(self, key_attr, text="")

        kbox.separator()
        info = kbox.column(align=True)
        info.scale_y = 0.85
        info.label(text="キー名は Blender のイベント名 (例: SPACE, O, P, COMMA, PERIOD, F1〜F12)", icon="INFO")
        info.label(text="ナビゲートのモード切替はキー押下中の Shift=回転 / Ctrl=ズーム (固定)")
        info.label(text="B-MANGA使用中は Z=Undo / X=Redo (固定)")
        info.label(text="ズーム中の LMB クリック=40%イン / Alt+LMB クリック=40%アウト (固定)")
        info.label(text="描画ツール中: Space=ナビゲート / C=ブラシシェルフ表示切替 (Blender既定の入れ替え)")

        box = layout.box()
        box.label(text="アセットライブラリ登録ガイド")
        box.prop(self, "global_asset_library")
        col = box.column(align=True)
        col.label(text="1. 上のパスを Blender 本体の Preferences > File Paths > Asset Libraries に追加")
        col.label(text="2. 作品固有アセットは MyWork.bmanga/assets/ 配下 (B-MANGA が自動管理)")
        col.label(text="3. コマ編集モード中にアセットブラウザからドラッグ&ドロップでリンク参照")

        box = layout.box()
        box.label(text="コマ用blendファイル (全作品共通)", icon="FILE_BLEND")
        box.prop(self, "coma_blend_template_path", text="")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="新しいコマ (cNN.blend) を作成するときの初期テンプレートとして全作品で使い回す", icon="INFO")
        col.label(text="作品情報パネル側のコマ用blendファイルが設定されていれば、そちらが優先される")

        box = layout.box()
        box.label(text="設定の移行", icon="FILE_REFRESH")
        row = box.row(align=True)
        row.operator("bmanga.preferences_export", text="設定を書き出す", icon="EXPORT")
        row.operator("bmanga.preferences_import", text="設定を読み込む", icon="IMPORT")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="プリファレンスと共通プリセットをZIPで移行します", icon="INFO")

        box = layout.box()
        box.label(text="自動ルビ辞書", icon="FONT_DATA")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="Google日本語入力 / MS-IME / ATOK のエクスポート辞書ファイル (TSV) を登録", icon="INFO")
        col.label(text="形式: 読み<TAB>表記<TAB>品詞 (1行1語)")
        row = box.row()
        row.template_list(
            "BMANGA_UL_ruby_dict_list", "",
            self, "ruby_dictionaries",
            self, "ruby_dict_active_index",
            rows=3,
        )
        side = row.column(align=True)
        side.operator("bmanga.ruby_dict_add", icon="ADD", text="")
        side.operator("bmanga.ruby_dict_remove", icon="REMOVE", text="")

        box = layout.box()
        box.label(text="Grease Pencil (overview)")
        box.prop(self, "gpencil_follow_cursor")


def get_preferences(context=None) -> "BMangaPreferences | None":
    ctx = context or bpy.context
    prefs = ctx.preferences.addons.get(ADDON_ID)
    return prefs.preferences if prefs else None


class BMANGA_OT_meldex_token_regenerate(Operator):
    bl_idname = "bmanga.meldex_token_regenerate"
    bl_label = "接続トークンを再生成"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None:
            return {"CANCELLED"}
        prefs.meldex_token = secrets.token_hex(32)
        if prefs.meldex_enabled:
            from .io import meldex_receiver

            if not meldex_receiver.restart_from_preferences(context):
                prefs.meldex_enabled = False
                self.report({"ERROR"}, "Meldex受信を開始できませんでした")
                return {"CANCELLED"}
        request_user_preferences_save()
        self.report({"INFO"}, "接続トークンを再生成しました")
        return {"FINISHED"}


_CLASSES = (BMangaRubyDictEntry, BMANGA_OT_meldex_token_regenerate, BMangaPreferences)


def register() -> None:
    global _USERPREF_SAVE_SUSPENDED
    logger = log.get_logger(__name__)
    generated_meldex_token = False
    _USERPREF_SAVE_SUSPENDED += 1
    try:
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
        prefs = get_preferences()
        if prefs is not None:
            if not str(getattr(prefs, "meldex_token", "") or ""):
                prefs.meldex_token = secrets.token_hex(32)
                generated_meldex_token = True
            log.set_level(prefs.log_level)
    finally:
        _USERPREF_SAVE_SUSPENDED = max(0, _USERPREF_SAVE_SUSPENDED - 1)
    if generated_meldex_token:
        request_user_preferences_save()
    logger.debug("preferences registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            # 既に解除されている場合は黙殺
            pass
