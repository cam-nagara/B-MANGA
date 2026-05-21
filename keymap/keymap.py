"""B-Name 専用キーマップ.

Phase 0 ではキーマップの **基盤** のみ用意する。実際のオペレータ呼び出し
割り当て（パン/回転/ズーム/スポイト等）は Phase 1 以降の viewport_ops.py
実装と同時に追加する。

ここで提供する機能:
- B-Name 専用 KeyMap の作成 / 破棄
- 既定キーマップ (Blender 標準) のうち B-Name と衝突し得るアイテムを
  ``KeyMapItem.active = False`` に切り替え、退避情報を保持
- unregister 時 (またはキーマップ無効化時) に元の active 状態へ完全に復元
- 現在の Blender キーマップ Preset 名を検出するフォールバック

設計メモ:
- ``bpy.context.window_manager.keyconfigs.addon`` はアドオンごとの KeyMap
  登録先として Blender 公式が用意している層。unregister 時に自作 KeyMap
  を空にすれば残留しない。
- 退避対象の既定キーマップ項目は ``keyconfigs.default`` 配下で検索する。
  Preset によって map の命名は概ね同じだが、キー割当や存在有無は違うので、
  衝突候補は「見つかったものだけ」退避する。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import bpy

from ..utils import log, runtime_activity

_logger = log.get_logger(__name__)

# B-Name 専用キーは Blender 標準の "3D View" キーマップ (addon 層) に登録する.
# 独自名 ("B-Name Viewport" 等) で keymaps.new すると addon kc には残るが、
# Blender の active keyconfig マージ評価に乗らずキーが一切発火しない (確認済).
# unregister では kc.keymaps.remove(km) は呼ばず、keymap_items の削除のみ行う
# (標準の "3D View" キーマップを丸ごと消すと Blender 既定操作が壊れる)。
BNAME_KEYMAP_NAME = "3D View"
BNAME_SPACE_TYPE = "VIEW_3D"
BNAME_REGION_TYPE = "WINDOW"

# B-Name が独占使用するキー組み合わせ.
# (type, shift, ctrl, alt) のタプル列挙。Blender のプリセット (Blender /
# Industry Compatible / Blender 27x 等) や idname は多岐にわたるため、
# idname ではなく「キー組み合わせ全部」で一括退避する。
_BNAME_EXCLUSIVE_COMBOS: tuple = (
    ("SPACE", False, False, False),  # Space
    ("SPACE", True, False, False),   # Shift + Space
    ("SPACE", False, True, False),   # Ctrl + Space
    ("WHEELUPMOUSE", False, True, False),    # Ctrl + Wheel Up
    ("WHEELDOWNMOUSE", False, True, False),  # Ctrl + Wheel Down
    ("LEFTMOUSE", True, True, False),        # Ctrl + Shift + LMB
)


@dataclass
class _SavedItem:
    keyconfig_name: str
    keymap_name: str
    idname: str
    key_type: str
    shift: bool
    ctrl: bool
    alt: bool
    oskey: bool
    prev_active: bool
    item_ref: object = field(repr=False)  # bpy_struct (KeyMapItem) 参照


class KeymapState:
    """退避情報と B-Name 専用 KeyMap を保持する状態オブジェクト."""

    def __init__(self) -> None:
        self.saved: List[_SavedItem] = []
        self.saved_conflicts: List[_SavedItem] = []
        self.bname_keymaps: List[object] = []
        self.bname_items: List[object] = []
        self.enabled: bool = False

    # ---------- B-Name 専用 KeyMap ----------

    def create_bname_keymap(self) -> Optional[object]:
        wm = bpy.context.window_manager
        if wm is None:
            print("[B-Name][KEYMAP] create_bname_keymap: window_manager is None")
            return None
        kc = wm.keyconfigs.addon
        if kc is None:
            print("[B-Name][KEYMAP] create_bname_keymap: keyconfigs.addon is None")
            _logger.warning("addon keyconfig unavailable; skip bname keymap")
            return None
        # 旧バージョンが残した独自名キーマップ ("B-Name Viewport") を addon kc
        # から掃除する。残ったまま新しい "3D View" 経由で kmi を増やすと、
        # 無効化時に二重 unregister で C レベルクラッシュする可能性がある。
        for legacy_name in ("B-Name Viewport",):
            legacy = kc.keymaps.get(legacy_name)
            if legacy is not None:
                try:
                    kc.keymaps.remove(legacy)
                    print(f"[B-Name][KEYMAP] removed legacy keymap: {legacy_name!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[B-Name][KEYMAP] legacy keymap removal failed: {exc!r}")
        try:
            km = kc.keymaps.new(
                name=BNAME_KEYMAP_NAME,
                space_type=BNAME_SPACE_TYPE,
                region_type=BNAME_REGION_TYPE,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] keymaps.new failed: {exc!r}")
            _logger.exception("keymaps.new failed")
            return None
        self.bname_keymaps.append(km)
        try:
            self._populate_keymap_items(km)
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] _populate_keymap_items failed: {exc!r}")
            _logger.exception("_populate_keymap_items failed")
            return None
        # Object Mode keymap にも Alt+LEFTMOUSE / Alt+Shift+LEFTMOUSE を追加.
        # これは "3D View" space keymap より高優先度で評価されるため、ツール
        # keymap (例: Select Box の Alt+LEFTMOUSE → paint.face_select_loop) が
        # poll 失敗で fall-through した時に確実に発火する.
        try:
            self._populate_object_mode_overrides(kc)
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] _populate_object_mode_overrides failed: {exc!r}")
        # Window キーマップにも Shift+Space / Ctrl+Space を登録して
        # screen.screen_full_area (Shift+Space) 等の標準ショートカットを
        # 先取りする。3D View 外で押された場合は invoke が PASS_THROUGH を
        # 返すので、Outliner 等での標準動作には影響しない。
        try:
            km_window = kc.keymaps.new(
                name="Window", space_type="EMPTY", region_type="WINDOW"
            )
            self.bname_keymaps.append(km_window)
            self._populate_window_overrides(km_window)
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] Window keymap setup failed: {exc!r}")
            _logger.exception("Window keymap setup failed")

        # Grease Pencil Paint / Draw モードキーマップに Space と C を登録。
        # キーマップ名は Blender バージョン (GP legacy / GP v3) や Locale で
        # 揺れるため、default kc を走査して名前+SPACE/C 既定割当を全部 dump し、
        # "rease" を含む keymap には漏らさず先取り登録する。
        gp_keymap_targets: list[tuple[str, str, str]] = []
        try:
            default_kc = wm.keyconfigs.default
            if default_kc is not None:
                # 全キーマップ名 dump (デバッグ用、"rease"/"Paint"/"Draw"/"Asset"/"Brush" を含むもの)
                print("[B-Name][KEYMAP] -- default kc keymap survey --")
                for km in default_kc.keymaps:
                    nm = km.name
                    if any(s in nm for s in ("rease", "Paint", "Draw", "Asset", "Brush")):
                        # SPACE / C 既定割当を確認
                        space_kmis = []
                        c_kmis = []
                        try:
                            for kmi in km.keymap_items:
                                if kmi.type == "SPACE" and not (
                                    kmi.shift or kmi.ctrl or kmi.alt
                                ):
                                    space_kmis.append(kmi.idname)
                                if kmi.type == "C" and not (
                                    kmi.shift or kmi.ctrl or kmi.alt
                                ):
                                    c_kmis.append(kmi.idname)
                        except Exception:  # noqa: BLE001
                            pass
                        print(
                            f"  km={nm!r} space_type={km.space_type}"
                            f" region_type={km.region_type}"
                            f" SPACE={space_kmis} C={c_kmis}"
                        )
                # GP Paint/Draw/Edit モード系を全部ターゲットに含める
                # (L=投げ縄 / Ctrl+X / Ctrl+V を Edit モードでも先取り)
                for km in default_kc.keymaps:
                    if "rease Pencil" in km.name and (
                        "Paint" in km.name
                        or "Draw" in km.name
                        or "Edit" in km.name
                    ):
                        gp_keymap_targets.append(
                            (km.name, km.space_type, km.region_type)
                        )
            print(f"[B-Name][KEYMAP] GP Paint/Draw/Edit targets: {gp_keymap_targets}")
            for name, st, rt in gp_keymap_targets:
                try:
                    km_gp = kc.keymaps.new(name=name, space_type=st, region_type=rt)
                    self.bname_keymaps.append(km_gp)
                    self._populate_gp_paint_overrides(km_gp, name)
                except Exception as exc:  # noqa: BLE001
                    print(f"[B-Name][KEYMAP] GP keymap setup failed ({name}): {exc!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] GP keymap discovery failed: {exc!r}")

        # Image Paint (= 3D View TEXTURE_PAINT mode) keymap への先取り。
        # 既定では SPACE が wm.call_asset_shelf_popover (Brush Asset Shelf) に
        # 割当てられているため、ラスター描画中に SPACE を押すとブラシシェルフ
        # が出てしまい view 操作ができない。B-Name の view_navigate を SPACE
        # に登録して先取りする。
        try:
            km_paint = kc.keymaps.new(
                name="Image Paint", space_type="EMPTY", region_type="WINDOW"
            )
            self.bname_keymaps.append(km_paint)
            self._populate_image_paint_overrides(km_paint)
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] Image Paint keymap setup failed: {exc!r}")
        print(
            f"[B-Name][KEYMAP] bname keymap created: name={BNAME_KEYMAP_NAME}"
            f" items={len(self.bname_items)} kc_name={kc.name!r}"
        )
        _logger.info("bname keymap created (items=%d)", len(self.bname_items))
        return km

    def _populate_gp_paint_overrides(self, km, km_name: str) -> None:
        """Grease Pencil Paint / Edit モードキーマップに先取り登録.

        - Space → bname.view_navigate (ブラシ Asset Shelf の先取り)
        - C     → wm.call_asset_shelf_popover (元の機能を C 側に移設)
        - E     → bname.toggle_eraser_brush (Eraser Hard / Stroke 切替)
        - K     → bname.layer_move_tool (レイヤー移動ツール)
        - Ctrl+Alt+LMB → bname.brush_size_drag (ブラシサイズ調整)
        - L     → bname.toggle_lasso_tool (投げ縄 ⇔ Box トグル)
        - Ctrl+X → bname.gp_cut_to_new_layer (Paste で新レイヤー化フラグを立てる)
        - Ctrl+V → bname.gp_paste_to_new_layer (フラグありなら新レイヤーへ paste)
        """
        try:
            from ..preferences import get_preferences
            prefs = get_preferences()
            nav_key = getattr(prefs, "key_navigate", "SPACE") if prefs else "SPACE"
            if not nav_key:
                nav_key = "SPACE"
        except Exception:  # noqa: BLE001
            nav_key = "SPACE"

        def _add(idname, key, **mods):
            try:
                kmi = km.keymap_items.new(idname, key, "PRESS", **mods)
                self.bname_items.append(kmi)
                print(
                    f"[B-Name][KEYMAP] + {idname} ({km_name}) {key}"
                    f" shift={kmi.shift} ctrl={kmi.ctrl} alt={kmi.alt}"
                )
                return kmi
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] {km_name} {key} override failed: {exc!r}")
                return None

        _add("bname.view_navigate", nav_key)
        shelf_name = None
        if "Weight" in km_name:
            shelf_name = "VIEW3D_AST_brush_gpencil_weight"
        elif "Vertex" in km_name:
            shelf_name = "VIEW3D_AST_brush_gpencil_vertex"
        elif "Draw" in km_name or "Paint" in km_name:
            shelf_name = "VIEW3D_AST_brush_gpencil_paint"
        if shelf_name is not None:
            kmi = _add("wm.call_asset_shelf_popover", "C")
            if kmi is not None:
                try:
                    kmi.properties.name = shelf_name
                except Exception as exc:  # noqa: BLE001
                    print(f"[B-Name][KEYMAP] set asset shelf name failed: {exc!r}")
        else:
            _add("bname.toggle_asset_shelf", "C")
        _add("bname.toggle_eraser_brush", "E")
        _add("bname.layer_move_tool", "K")
        _add("bname.brush_size_drag", "LEFTMOUSE", ctrl=True, alt=True)
        _add("bname.toggle_lasso_tool", "L")
        _add("bname.gp_cut_to_new_layer", "X", ctrl=True)
        _add("bname.gp_paste_to_new_layer", "V", ctrl=True)
        # F/T → B-Name ツール (GP モードでも先取り)
        _add("bname.coma_knife_cut", "F")
        _add("bname.text_tool", "T")

    def _populate_image_paint_overrides(self, km) -> None:
        """Image Paint (TEXTURE_PAINT) モードキーマップに先取り登録.

        Blender 5.x の TEXTURE_PAINT モードでは既定で SPACE が
        ``wm.call_asset_shelf_popover`` (Brush Asset Shelf) に割当てられて
        おり、ラスター描画中に SPACE を押すとブラシシェルフが開いて
        view 操作 (パン/回転/ズーム) ができなくなる。GP Paint モードと
        同様に B-Name の view_navigate を SPACE で先取りし、ブラシ
        切替を ``C`` キーへ移設する。

        Ctrl+Alt+ドラッグは GP 描画と同じブラシサイズ調整に割り当てる。
        他のショートカット (E=消しゴム, X=Undo, V=Redo 等) は Blender
        既定がラスター描画上で重要な役割を持つため、Texture Paint では
        SPACE / C / Ctrl+Alt+ドラッグのみを上書きする。
        """
        try:
            from ..preferences import get_preferences
            prefs = get_preferences()
            nav_key = getattr(prefs, "key_navigate", "SPACE") if prefs else "SPACE"
            if not nav_key:
                nav_key = "SPACE"
        except Exception:  # noqa: BLE001
            nav_key = "SPACE"

        def _add(idname, key, **mods):
            try:
                kmi = km.keymap_items.new(idname, key, "PRESS", **mods)
                self.bname_items.append(kmi)
                print(
                    f"[B-Name][KEYMAP] + {idname} (Image Paint) {key}"
                    f" shift={kmi.shift} ctrl={kmi.ctrl} alt={kmi.alt}"
                )
                return kmi
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] Image Paint {key} override failed: {exc!r}")
                return None

        _add("bname.view_navigate", nav_key)
        # SPACE がブラシシェルフを開いていた機能を C に移設
        kmi = _add("wm.call_asset_shelf_popover", "C")
        if kmi is not None:
            try:
                kmi.properties.name = "VIEW3D_AST_brush_texture_paint"
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] set asset shelf name failed: {exc!r}")
        _add("bname.brush_size_drag", "LEFTMOUSE", ctrl=True, alt=True)

    def _populate_object_mode_overrides(self, kc) -> None:
        """Object Mode (mode keymap) にも Alt+drag / Alt+Shift+click を登録.

        Blender のキーマップは Tool > Mode > Space > Window の優先度で評価される。
        "3D View" は Space 層なので、Tool keymap (例: Select Box の Alt+LEFTMOUSE
        → paint.face_select_loop) が先に消費する場合がある。Object Mode (Mode 層)
        に登録すると Tool 層の poll 失敗時の fall-through で確実に発火し、また
        どのツールが active でも Alt+drag が動くようになる.
        """
        target_keymaps = (
            "Object Mode",
            "Grease Pencil Edit Mode",
            "Grease Pencil Draw Mode",
            "Grease Pencil Sculpt Mode",
            "Grease Pencil Weight Paint",
            "Grease Pencil Vertex Paint",
        )
        for km_name in target_keymaps:
            km = kc.keymaps.get(km_name)
            if km is None:
                # 一部のキーマップは初回ロードまで存在しない場合があるので
                # mode 名を指定して新規取得 (なければ作る)
                try:
                    km = kc.keymaps.new(name=km_name, space_type="EMPTY", region_type="WINDOW")
                except Exception:  # noqa: BLE001
                    continue
            self.bname_keymaps.append(km)
            try:
                kmi = km.keymap_items.new(
                    "bname.alt_reparent_drag",
                    "LEFTMOUSE",
                    "PRESS",
                    alt=True,
                    head=True,
                )
                self.bname_items.append(kmi)
                print(f"[B-Name][KEYMAP] + bname.alt_reparent_drag ({km_name}) LEFTMOUSE alt=1")
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] {km_name} alt_reparent_drag failed: {exc!r}")
            try:
                kmi = km.keymap_items.new(
                    "bname.alt_reparent_out",
                    "LEFTMOUSE",
                    "PRESS",
                    alt=True,
                    shift=True,
                    head=True,
                )
                self.bname_items.append(kmi)
                print(f"[B-Name][KEYMAP] + bname.alt_reparent_out ({km_name}) LEFTMOUSE alt=1 shift=1")
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] {km_name} alt_reparent_out failed: {exc!r}")

    def _populate_window_overrides(self, km) -> None:
        """Window キーマップに修飾キー操作と主要ツールキーを先取り登録.

        Blender のキーマップ評価は Window kc (空間非依存) が area kc より先に
        走るため、ここに登録すると他のアドオンが area kc に登録した同キーを
        先取りできる。枠線ツール側は invoke 時に利用可能な 3D View を探索する。
        """
        # preferences 取得 (失敗時は SPACE 既定)
        try:
            from ..preferences import get_preferences
            prefs = get_preferences()
            nav_key = getattr(prefs, "key_navigate", "SPACE") if prefs else "SPACE"
            if not nav_key:
                nav_key = "SPACE"
        except Exception:  # noqa: BLE001
            prefs = None
            nav_key = "SPACE"

        def _key(attr, default):
            if prefs is None:
                return default
            value = getattr(prefs, attr, default)
            return value if value else default

        def _mods(prefix):
            if prefs is None:
                return False, False, False
            return (
                bool(getattr(prefs, f"{prefix}_shift", False)),
                bool(getattr(prefs, f"{prefix}_ctrl", False)),
                bool(getattr(prefs, f"{prefix}_alt", False)),
            )

        def _add_window(op_id, key, **mods):
            try:
                kmi = km.keymap_items.new(op_id, key, "PRESS", **mods)
                self.bname_items.append(kmi)
                print(
                    f"[B-Name][KEYMAP] + {op_id} (Window) {key}"
                    f" shift={kmi.shift} ctrl={kmi.ctrl} alt={kmi.alt}"
                    f" active={kmi.active}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] window override {key} failed: {exc!r}")

        for shift, ctrl, label in ((True, False, "shift"), (False, True, "ctrl")):
            _add_window("bname.view_navigate", nav_key, shift=shift, ctrl=ctrl)

        # O → オブジェクトツール、F/K/T → B-Name ツール、Z/X/E → 補助操作。
        # Window kc に登録し、サイドバーの B-Name パネル上でも先取りさせる。
        # 注: exit_coma_mode の Esc は 3D View kc のみに限定し、Window kc には
        # 登録しない。Window kc に Esc を載せると Outliner / Image Editor 等の
        # area で MODE_COMA 中に Esc を押した際、本来期待される area 固有の
        # cancel 動作 (検索キャンセル等) を奪ってしまうため。
        s, c, a = _mods("mod_set_mode_object")
        _add_window("bname.set_mode_object", _key("key_set_mode_object", "O"), shift=s, ctrl=c, alt=a)

        for op_id, key in (
            ("bname.undo", "Z"),
            ("bname.redo", "X"),
            ("bname.toggle_eraser_brush", "E"),
            ("bname.coma_knife_cut", "F"),
            ("bname.layer_move_tool", "K"),
            ("bname.text_tool", "T"),
        ):
            _add_window(op_id, key)

    def _populate_keymap_items(self, km) -> None:
        """B-Name 専用のキーマップエントリを追加.

        preferences 値 (key_navigate / key_set_mode_object / key_set_mode_draw
        / key_page_next / key_page_prev とそれぞれの mod_*) を読み込んで
        keymap items を構築する。preferences 取得失敗時は既定値を用いる。

        ナビゲート (パン/回転/ズーム) は Space 1キーに統合し、modal 内で
        Shift/Ctrl 状態を見て動的切替する。Shift+Space を addon kc に直接
        登録すると Blender 標準 (screen.screen_full_area) と衝突するため、
        修飾組み合わせは Window キーマップ側で別途先取りする。
        """
        def _add(idname, key, value="PRESS", **mods):
            try:
                kmi = km.keymap_items.new(idname, key, value, **mods)
                self.bname_items.append(kmi)
                print(
                    f"[B-Name][KEYMAP] + {idname} key={key} value={value}"
                    f" shift={kmi.shift} ctrl={kmi.ctrl} alt={kmi.alt}"
                    f" active={kmi.active}"
                )
                return kmi
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] FAILED to add {idname} {key} {mods}: {exc!r}")
                return None

        # preferences を取得 (失敗時は既定値で動く)
        try:
            from ..preferences import get_preferences
            prefs = get_preferences()
        except Exception:  # noqa: BLE001
            prefs = None

        def _key(attr, default):
            if prefs is None:
                return default
            v = getattr(prefs, attr, default)
            return v if v else default

        def _mods(prefix):
            if prefs is None:
                return False, False, False
            return (
                bool(getattr(prefs, f"{prefix}_shift", False)),
                bool(getattr(prefs, f"{prefix}_ctrl", False)),
                bool(getattr(prefs, f"{prefix}_alt", False)),
            )

        # 統合ナビゲートモーダル (キー単独、修飾は modal 内で動的判定)
        _add("bname.view_navigate", _key("key_navigate", "SPACE"))

        # F → 枠線カットツール (CSP 互換)
        _add("bname.coma_knife_cut", "F")
        # K → レイヤー移動ツール
        _add("bname.layer_move_tool", "K")
        # T → テキストツール
        _add("bname.text_tool", "T")
        # Z / X → Undo / Redo (B-Name work が開かれている時だけ実行)
        _add("bname.undo", "Z")
        _add("bname.redo", "X")
        # Ctrl+C / Ctrl+V → B-Name レイヤーのコピー / 貼り付け
        _add("bname.layer_clipboard_copy", "C", ctrl=True)
        _add("bname.layer_clipboard_paste", "V", ctrl=True)
        # Ctrl+Shift+C / Ctrl+Shift+V → フキダシしっぽのコピー / 貼り付け
        _add("bname.balloon_tail_clipboard_copy", "C", ctrl=True, shift=True)
        _add("bname.balloon_tail_clipboard_paste", "V", ctrl=True, shift=True)
        # E → Eraser Hard / Eraser Stroke 切替 (GP描画中のみ実行)
        _add("bname.toggle_eraser_brush", "E")
        # 右クリック → B-Name 選択メニュー。ツールの modal が動いていない
        # 通常選択状態でも同じメニューを出せるよう keymap 側でも拾う。
        _add("bname.view_context_menu", "RIGHTMOUSE")
        # Esc → コマ編集モードを抜けて全ページ一覧 (work.blend) に戻る
        # poll が MODE_COMA または「現在の .blend が cNN.blend」を要求する
        # (堅牢版: load_post 失敗等で bname_mode が同期されていなくても帰れる)。
        # 紙面編集モード中は両方とも False になり Blender 既定の Esc 動作が走る。
        _add("bname.exit_coma_mode_safe", "ESC")

        # Ctrl + ホイール → 1 ステップズーム (固定)
        kmi = _add("bname.view_zoom_step", "WHEELUPMOUSE", ctrl=True)
        if kmi is not None:
            try:
                kmi.properties.direction = "IN"
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] set direction IN failed: {exc!r}")
        kmi = _add("bname.view_zoom_step", "WHEELDOWNMOUSE", ctrl=True)
        if kmi is not None:
            try:
                kmi.properties.direction = "OUT"
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] set direction OUT failed: {exc!r}")

        # Ctrl+Shift+クリック → レイヤー選択 (固定)
        _add("bname.view_layer_pick", "LEFTMOUSE", ctrl=True, shift=True)
        # Ctrl+Alt+ドラッグ → ブラシサイズ変更 (固定)
        _add("bname.brush_size_drag", "LEFTMOUSE", ctrl=True, alt=True)
        # オブジェクトモードのクリック → ページをアクティブ化。
        # operator 側で PASS_THROUGH し、Blender 標準のオブジェクト選択は妨げない。
        _add("bname.page_pick_viewport", "LEFTMOUSE")
        # Ctrl+クリック / Shift+クリック → ページ/コマのマルチセレクト
        # operator 内で event の修飾キーを参照し、object_selection に toggle/add する.
        _add("bname.page_pick_viewport", "LEFTMOUSE", ctrl=True)
        _add("bname.page_pick_viewport", "LEFTMOUSE", shift=True)
        # Alt+ドラッグ → 選択中レイヤーをドロップ先のコマ/ページへ reparent + 位置追従
        _add("bname.alt_reparent_drag", "LEFTMOUSE", alt=True, head=True)
        # Alt+Shift+クリック → 選択中レイヤーを 1 段浅い親へ (位置維持)
        # operator 内でドラッグ判定をしないため、PRESS 即発火扱い.
        _add("bname.alt_reparent_out", "LEFTMOUSE", alt=True, shift=True, head=True)
        # ダブルクリック → コマ編集モードへ (固定)。ファイル選択用の
        # プロパティを持つ本体 operator を keymap から直接呼ぶと、
        # mainfile 切替後の keymap 再構築で Blender 本体が落ちる場合が
        # あるため、keymap にはプロパティを持たない中継 operator を載せる。
        _add("bname.enter_coma_mode_from_viewport", "LEFTMOUSE", value="DOUBLE_CLICK")

        # preferences 設定可能なショートカット
        s, c, a = _mods("mod_set_mode_object")
        _add("bname.set_mode_object", _key("key_set_mode_object", "O"),
             shift=s, ctrl=c, alt=a)
        s, c, a = _mods("mod_set_mode_draw")
        _add("bname.set_mode_draw", _key("key_set_mode_draw", "P"),
             shift=s, ctrl=c, alt=a)
        s, c, a = _mods("mod_page_next")
        _add("bname.page_next", _key("key_page_next", "COMMA"),
             shift=s, ctrl=c, alt=a)
        s, c, a = _mods("mod_page_prev")
        _add("bname.page_prev", _key("key_page_prev", "PERIOD"),
             shift=s, ctrl=c, alt=a)

        _logger.debug("bname keymap items: %d", len(self.bname_items))

    def set_bname_items_active(self, active: bool) -> int:
        """B-Name 自身のキーマップアイテムを一括で active/inactive に切替.

        addon keyconfig 層のアイテムは default 層より優先されるため、
        タブ非アクティブ時には False にしておかないと Blender 既定ショート
        カットに戻らない。
        """
        changed = 0
        for kmi in self.bname_items:
            try:
                if bool(kmi.active) != bool(active):
                    kmi.active = bool(active)
                    changed += 1
            except (ReferenceError, AttributeError):
                pass
        return changed

    # ---------- 衝突キー無効化 (他アドオン対策) ----------

    # B-Name が単独修飾なしで予約するキー (他アドオンや標準機能に奪われる対象)
    _BNAME_RESERVED_SINGLE_KEYS: tuple[str, ...] = ("O", "F", "K", "T")
    _BNAME_EXCLUSIVE_IDNAMES: tuple[str, ...] = (
        "bname.set_mode_object",
        "bname.coma_knife_cut",
        "bname.layer_move_tool",
        "bname.text_tool",
    )

    @staticmethod
    def _ptr(value) -> int | None:
        try:
            return int(value.as_pointer())
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _combo_for_kmi(kmi) -> tuple[str, bool, bool, bool, bool]:
        return (
            str(getattr(kmi, "type", "")),
            bool(getattr(kmi, "shift", False)),
            bool(getattr(kmi, "ctrl", False)),
            bool(getattr(kmi, "alt", False)),
            bool(getattr(kmi, "oskey", False)),
        )

    @staticmethod
    def _keymap_can_steal_view3d_shortcut(km) -> bool:
        """B-Name の 3Dビュー操作を奪い得るキーマップだけを対象にする."""
        name = str(getattr(km, "name", "") or "")
        space_type = str(getattr(km, "space_type", "") or "")
        if space_type == "VIEW_3D":
            return True
        if name in {
            "Window",
            "3D View",
            "Object Mode",
            "Mesh",
            "Grease Pencil",
            "Grease Pencil Paint Mode",
            "Grease Pencil Edit Mode",
            "Image Paint",
        }:
            return True
        return any(token in name for token in ("3D View", "Object", "Mesh", "Grease Pencil", "Image Paint"))

    def _exclusive_conflict_combos(self) -> set[tuple[str, bool, bool, bool, bool]]:
        """現在の B-Name キー設定から退避すべきキー組み合わせを作る."""
        target_idnames = set(self._BNAME_EXCLUSIVE_IDNAMES)
        combos: set[tuple[str, bool, bool, bool, bool]] = set()
        for kmi in list(self.bname_items):
            try:
                if str(kmi.idname) not in target_idnames:
                    continue
                if str(getattr(kmi, "value", "PRESS")) != "PRESS":
                    continue
                combos.add(self._combo_for_kmi(kmi))
            except (ReferenceError, AttributeError):
                continue
        for key in self._BNAME_RESERVED_SINGLE_KEYS:
            combos.add((key, False, False, False, False))
        return combos

    def disable_conflicting_keys(self) -> int:
        """B-Name パネル表示中に同じキーを奪う kmi を一時的に無効化.

        標準のプロポーショナル変形や Fluent のパイメニューなど、B-Name の
        "O" / "F" より先に評価される同一キーを退避する。対象は 3Dビュー操作を
        奪い得るキーマップに限定し、B-Name パネルを閉じたら元の active 状態へ戻す。
        """
        wm = bpy.context.window_manager
        if wm is None:
            return 0
        keyconfigs = []
        seen_keyconfigs: set[int] = set()
        # 標準操作は active keyconfig 経由で見える。default keyconfig を直接
        # 書き換える方式は Blender 終了時に不安定化した過去があるため触らない。
        for attr in ("addon", "user", "active"):
            kc = getattr(wm.keyconfigs, attr, None)
            if kc is None:
                continue
            ptr = self._ptr(kc)
            if ptr is not None and ptr in seen_keyconfigs:
                continue
            if ptr is not None:
                seen_keyconfigs.add(ptr)
            keyconfigs.append((attr, kc))
        if not keyconfigs:
            return 0
        target_combos = self._exclusive_conflict_combos()
        bname_ptrs = {ptr for ptr in (self._ptr(kmi) for kmi in self.bname_items) if ptr is not None}
        saved_ptrs = {
            ptr for ptr in (self._ptr(s.item_ref) for s in self.saved_conflicts) if ptr is not None
        }
        disabled = 0
        for kc_name, kc in keyconfigs:
            # iterator 内で modify すると C ref が破綻するため、まず list 化
            try:
                keymaps = list(kc.keymaps)
            except Exception:  # noqa: BLE001
                continue
            for km in keymaps:
                if not self._keymap_can_steal_view3d_shortcut(km):
                    continue
                try:
                    kmis = list(km.keymap_items)
                except Exception:  # noqa: BLE001
                    continue
                for kmi in kmis:
                    try:
                        idname = str(getattr(kmi, "idname", "") or "")
                        if idname.startswith("bname."):
                            continue
                        item_ptr = self._ptr(kmi)
                        if item_ptr is not None and item_ptr in bname_ptrs:
                            continue
                        if item_ptr is not None and item_ptr in saved_ptrs:
                            if bool(getattr(kmi, "active", False)):
                                kmi.active = False
                            continue
                        if str(getattr(kmi, "value", "PRESS")) != "PRESS":
                            continue
                        if self._combo_for_kmi(kmi) not in target_combos:
                            continue
                        if not bool(getattr(kmi, "active", False)):
                            continue
                        # 退避してから無効化 (独立リスト saved_conflicts を使用、
                        # restore_defaults による saved クリアの影響を受けない)
                        self.saved_conflicts.append(_SavedItem(
                            keyconfig_name=kc_name,
                            keymap_name=km.name,
                            idname=idname,
                            key_type=kmi.type,
                            shift=bool(kmi.shift),
                            ctrl=bool(kmi.ctrl),
                            alt=bool(kmi.alt),
                            oskey=bool(getattr(kmi, "oskey", False)),
                            prev_active=True,
                            item_ref=kmi,
                        ))
                        if item_ptr is not None:
                            saved_ptrs.add(item_ptr)
                        kmi.active = False
                        disabled += 1
                        print(
                            f"[B-Name][KEYMAP] disabled conflict: "
                            f"kc={kc_name!r} km={km.name!r} idname={idname!r} key={kmi.type}"
                        )
                    except (ReferenceError, AttributeError):
                        continue
        if disabled > 0:
            _logger.info("disabled %d conflicting kmis", disabled)
        return disabled

    def restore_conflicting_keys(self) -> None:
        """無効化した kmi を元の active 状態に復元."""
        for s in list(self.saved_conflicts):
            try:
                if s.item_ref is not None:
                    s.item_ref.active = bool(s.prev_active)
            except (ReferenceError, AttributeError):
                pass
        self.saved_conflicts.clear()

    def remove_bname_keymaps(self) -> None:
        """B-Name が追加した keymap_items だけを削除し、標準キーマップは残す.

        ``BNAME_KEYMAP_NAME = "3D View"`` (Blender 標準キーマップ名) に
        相乗りしているため、kc.keymaps.remove(km) を呼ぶと標準操作が
        全部消える。ここでは bname_items のみ remove する。
        """
        wm = bpy.context.window_manager
        if wm is None:
            self.bname_keymaps.clear()
            self.bname_items.clear()
            return
        # km / kmi の C 参照が既に無効化されている可能性があるため、
        # 個別 try で防御し、最後にリストを必ずクリアする。
        for km in self.bname_keymaps:
            for kmi in list(self.bname_items):
                try:
                    km.keymap_items.remove(kmi)
                except Exception:  # noqa: BLE001
                    pass
        self.bname_keymaps.clear()
        self.bname_items.clear()
        _logger.debug("bname keymap items removed (standard '3D View' keymap kept)")

    # ---------- 既定キーマップ退避/復元 ----------
    # NOTE (deprecated): override_defaults / restore_defaults は廃止された。
    # B-Name は addon kc の "3D View" キーマップに kmi を追加するだけで、
    # Blender のキーマップ評価優先順 (addon > user > default) によって
    # 自動的に既定操作より優先される。default kc の active プロパティを
    # 書き換える方式は、アドオン無効化中に Blender 内部のキーマップ
    # 再構築とレースして C レベル segfault を起こすため除去した。
    # これらのメソッドは互換維持のため残してあるが no-op 化している。

    def override_defaults(
        self, combos: Iterable = _BNAME_EXCLUSIVE_COMBOS
    ) -> int:
        """[NO-OP] 既定キーマップ退避は廃止された.

        addon kc に "3D View" 同名キーマップで kmi を追加すれば
        Blender のキーマップ評価が自動的に addon kc を優先するため、
        default kc 側を書き換える必要がない。書き換える方式はアドオン
        無効化中に Blender 内部のキーマップ再構築とレースして
        EXCEPTION_ACCESS_VIOLATION を起こすため除去した。
        """
        # enabled フラグだけ立てておく (watcher の再呼び出しを抑制)
        self.enabled = True
        return 0

    def restore_defaults(self) -> None:
        """[NO-OP] 既定キーマップ復元は廃止された (override_defaults 参照)."""
        self.saved.clear()
        self.enabled = False

    # ---------- Preset 検出 ----------

    @staticmethod
    def detect_preset_name() -> str:
        """現在の Blender キーマップ Preset 名を検出.

        Blender の Preset は ``WindowManager.keyconfigs.active.name`` に
        入っている (例: "Blender", "Industry Compatible" 等)。取得できない
        場合は空文字を返す。
        """
        wm = bpy.context.window_manager
        if wm is None:
            return ""
        kc = wm.keyconfigs.active
        return kc.name if kc is not None else ""


def _match_filter(kmi: object, filt: dict) -> bool:
    for key, expected in filt.items():
        if getattr(kmi, key, None) != expected:
            return False
    return True


# ---------- モジュール公開 API ----------

_state: Optional[KeymapState] = None

# タイマー監視間隔 (秒)
_WATCH_INTERVAL = runtime_activity.KEYMAP_WATCH_INTERVAL
# B-Name タブの bl_category 名
_BNAME_TAB_CATEGORY = "B-Name"
_SUSPEND_UNTIL = 0.0
_SUSPEND_REASON = ""


def get_state() -> Optional[KeymapState]:
    return _state


def suspend_visibility_updates(
    seconds: float = 3.0,
    *,
    reason: str = "",
    disable_now: bool = True,
) -> None:
    """mainfile 切替直後の不安定なタイミングでは keymap を触らない."""
    global _SUSPEND_UNTIL, _SUSPEND_REASON
    try:
        seconds = max(0.0, float(seconds))
    except (TypeError, ValueError):
        seconds = 3.0
    _SUSPEND_UNTIL = max(_SUSPEND_UNTIL, time.monotonic() + seconds)
    _SUSPEND_REASON = str(reason or "")
    if disable_now:
        force_shortcuts_disabled()


def force_shortcuts_disabled() -> None:
    """現在登録済みの B-Name キーを即時に無効化する."""
    state = _state
    if state is None:
        return
    try:
        state.restore_conflicting_keys()
    except Exception:  # noqa: BLE001
        _logger.exception("restore_conflicting_keys failed")
    if state.enabled:
        state.restore_defaults()
    state.set_bname_items_active(False)


def is_visibility_update_suspended() -> bool:
    return time.monotonic() < _SUSPEND_UNTIL


def _any_bname_tab_active() -> bool:
    """いずれかの VIEW_3D の N パネルで B-Name タブがアクティブか判定.

    ショートカットをページ一覧ファイルかつ B-Name タブ表示中に限定するための
    薄い wrapper。
    """
    from ..utils import shortcut_visibility

    return shortcut_visibility.any_shortcuts_allowed(bpy.context)


def _watch_bname_tab() -> Optional[float]:
    """タイマー: B-Name タブ表示中だけ B-Name キーマップを有効化する.

    override_defaults / restore_defaults / set_bname_items_active は
    冪等で、毎ティック呼んでも追加コストは微小 (状態の早期 return あり)。

    register 時に ``window_manager`` / ``keyconfigs.addon`` がまだ整っておら
    ず ``create_bname_keymap`` が失敗した場合に備え、毎 tick ``bname_items``
    が空なら作成をリトライする。これがないと「ショートカットが一つも効か
    ない」状態が永久に続く。
    """
    state = _state
    if state is None:
        return None  # タイマー停止
    if is_visibility_update_suspended():
        return _WATCH_INTERVAL
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        keymap_pref_enabled = True if prefs is None else bool(prefs.keymap_enabled)
        enabled = bool(keymap_pref_enabled and _any_bname_tab_active())

        # キーマップ未作成 (register 時に wm/addon keyconfig が None だった等) なら再試行
        if not state.bname_items:
            km = state.create_bname_keymap()
            if km is not None:
                _logger.info(
                    "bname keymap recreated by watcher (items=%d)",
                    len(state.bname_items),
                )

        _apply_visibility_state(state, enabled)
    except Exception:  # noqa: BLE001
        _logger.exception("watch_bname_tab failed")
    return _WATCH_INTERVAL


def _bname_tab_is_active() -> bool:
    """N パネル sidebar で B-Name タブが現在アクティブな area が 1 つでもあるか.

    ``Region.active_panel_category`` を使う (Blender 5.x では実装済)。
    """
    return _any_bname_tab_active()


def _apply_visibility_state(state: KeymapState, enabled: bool) -> None:
    """B-Name タブ表示状態に合わせて自前キーと競合退避を切り替える."""
    if enabled:
        state.set_bname_items_active(True)
        if not state.enabled and state.bname_items:
            state.override_defaults()
        try:
            state.disable_conflicting_keys()
        except Exception:  # noqa: BLE001
            _logger.exception("disable_conflicting_keys failed")
        return
    try:
        state.restore_conflicting_keys()
    except Exception:  # noqa: BLE001
        _logger.exception("restore_conflicting_keys failed")
    if state.enabled:
        state.restore_defaults()
    state.set_bname_items_active(False)


def _register_watcher() -> None:
    if bpy.app.timers.is_registered(_watch_bname_tab):
        return
    bpy.app.timers.register(
        _watch_bname_tab,
        first_interval=_WATCH_INTERVAL,
        persistent=True,
    )


def _unregister_watcher() -> None:
    if bpy.app.timers.is_registered(_watch_bname_tab):
        try:
            bpy.app.timers.unregister(_watch_bname_tab)
        except ValueError:
            pass


def register() -> None:
    global _state
    print("[B-Name][KEYMAP] register() called")
    _state = KeymapState()
    preset = KeymapState.detect_preset_name()
    print(f"[B-Name][KEYMAP] detected preset: {preset or '(unknown)'}")
    _logger.info("detected keymap preset: %s", preset or "(unknown)")

    # Preferences に従い、B-Name キーマップを有効化するかを決める
    from ..preferences import get_preferences

    prefs = get_preferences()
    keymap_enabled = True if prefs is None else bool(prefs.keymap_enabled)

    km = _state.create_bname_keymap()
    if km is None or not _state.bname_items:
        # wm / keyconfigs.addon がまだ整っていない (Blender 起動直後のアドオン
        # 自動有効化等)。watcher が後で再試行するので fatal ではない。
        print(
            "[B-Name][KEYMAP] register: keymap NOT created at register-time;"
            f" watcher will retry every {_WATCH_INTERVAL:.1f}s"
        )
        _logger.warning(
            "bname keymap not created at register-time (wm/addon keyconfig unavailable);"
            " watcher will retry every %.1fs",
            _WATCH_INTERVAL,
        )
    # register 時点でもサイドバー状態を見て active を合わせる。
    # B-Name タブが表示されていない間は Blender 標準キーへ戻す。
    _apply_visibility_state(_state, bool(keymap_enabled and _any_bname_tab_active()))
    # watcher は preferences.keymap_enabled の動的トグルへの追従専用
    _register_watcher()
    _logger.info(
        "keymap registered (enabled=%s, items=%d, overrides=%d, watcher=%s)",
        keymap_enabled,
        len(_state.bname_items),
        len(_state.saved),
        bpy.app.timers.is_registered(_watch_bname_tab),
    )


def unregister() -> None:
    global _state
    if _state is None:
        return
    _unregister_watcher()
    try:
        # 衝突キー (F/G) の無効化を元に戻す
        _state.restore_conflicting_keys()
    except Exception:  # noqa: BLE001
        _logger.exception("restore_conflicting_keys failed")
    try:
        _state.restore_defaults()
    finally:
        _state.remove_bname_keymaps()
    _state = None
    _logger.debug("keymap unregistered")


def rebuild_keymap_from_prefs() -> None:
    """preferences のショートカット設定が変わった時に呼ぶ.

    既存の bname_items を全て remove → preferences を再読込で keymap を作り直す。
    アドオン無効化中などで _state が None なら何もしない。
    """
    state = _state
    if state is None:
        return
    if is_visibility_update_suspended():
        _logger.info("rebuild_keymap_from_prefs deferred during keymap suspend: %s", _SUSPEND_REASON)
        return
    print("[B-Name][KEYMAP] rebuild_keymap_from_prefs() triggered")
    try:
        # 衝突キーの退避情報を一旦復元してリフレッシュ
        state.restore_conflicting_keys()
    except Exception:  # noqa: BLE001
        _logger.exception("rebuild: restore_conflicting_keys failed")
    try:
        # 既存のアイテムを掃除 (既存 keymap オブジェクト自体は標準 "3D View" /
        # "Window" を参照しているため remove しない)
        state.remove_bname_keymaps()
    except Exception:  # noqa: BLE001
        _logger.exception("rebuild: remove_bname_keymaps failed")
    try:
        # B-Name タブ状態に合わせて自前キーだけを切り替える
        state.create_bname_keymap()
        from ..preferences import get_preferences
        prefs = get_preferences()
        keymap_enabled = True if prefs is None else bool(prefs.keymap_enabled)
        _apply_visibility_state(state, bool(keymap_enabled and _any_bname_tab_active()))
    except Exception:  # noqa: BLE001
        _logger.exception("rebuild: create_bname_keymap failed")
