"""Blender 実機用: 退避されたまま取り残された標準キーの自己修復確認.

disable_conflicting_keys で無効化された F/K/O/T が userpref.blend に
焼き付いた状況 (前セッションでタブを開いたまま Blender を終了した状況) を
再現し、repair_stale_disabled_shortcuts が次回読込時に復元することを確認する。

実行:
'/c/Program Files/Blender Foundation/Blender 5.1/blender.exe' --background \
    --factory-startup --python-exit-code 1 \
    --python test/blender_shortcut_conflict_repair_check.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_shortcut_repair",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_shortcut_repair"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _get_or_create_keymap(kc, name: str):
    km = kc.keymaps.get(name)
    if km is None:
        km = kc.keymaps.new(name=name, space_type="EMPTY", region_type="WINDOW")
    return km


def main() -> None:
    mod = _load_addon()
    from bmanga_dev_shortcut_repair.keymap import keymap as keymap_mod
    from bmanga_dev_shortcut_repair.utils import shortcut_visibility

    wm = bpy.context.window_manager
    kc_user = wm.keyconfigs.user
    kc_default = wm.keyconfigs.default

    km_user = _get_or_create_keymap(kc_user, "Mesh")
    km_default = _get_or_create_keymap(kc_default, "Mesh")

    created_user = []
    created_default = []
    original_panel_visible = shortcut_visibility.bmanga_panel_visible
    original_any_panel_visible = shortcut_visibility.any_bmanga_panel_visible
    try:
        # 1) default で active な標準キー (F) が user 側で inactive のまま
        #    → 前セッションの退避取り残し。修復対象。
        kmi_default_f = km_default.keymap_items.new("wm.context_toggle", "F", "PRESS")
        created_default.append(kmi_default_f)
        kmi_user_f = km_user.keymap_items.new("wm.context_toggle", "F", "PRESS")
        created_user.append(kmi_user_f)
        kmi_user_f.active = False

        # 2) default 側に同一 idname の対応が無い項目 → 修復してはいけない
        kmi_user_orphan = km_user.keymap_items.new("wm.window_new", "F", "PRESS")
        created_user.append(kmi_user_orphan)
        kmi_user_orphan.active = False

        # 3) B-MANGA 予約コンボ外のキー (E) → 修復してはいけない
        kmi_default_e = km_default.keymap_items.new("wm.context_toggle", "E", "PRESS")
        created_default.append(kmi_default_e)
        kmi_user_e = km_user.keymap_items.new("wm.context_toggle", "E", "PRESS")
        created_user.append(kmi_user_e)
        kmi_user_e.active = False

        repaired = keymap_mod.repair_stale_disabled_shortcuts()
        assert repaired >= 1, f"自己修復の件数が0です: {repaired}"
        assert bool(kmi_user_f.active), "取り残されたFキーが自己修復されません"
        assert not bool(kmi_user_orphan.active), (
            "default対応の無い項目まで有効化されました"
        )
        assert not bool(kmi_user_e.active), "予約コンボ外の項目まで有効化されました"

        # ---- 今セッションで意図的に退避中の項目は修復しない ----
        work = bpy.context.scene.bmanga_work
        work.loaded = True
        work.work_dir = str(ROOT / "_shortcut_repair_test.bmanga")
        bpy.context.scene.bmanga_mode = "PAGE"
        shortcut_visibility.bmanga_panel_visible = lambda _context=None: True
        shortcut_visibility.any_bmanga_panel_visible = lambda _context=None: True
        keymap_mod._watch_bmanga_tab()
        assert not bool(kmi_user_f.active), "タブ表示中のF退避が行われていません"

        keymap_mod.repair_stale_disabled_shortcuts()
        assert not bool(kmi_user_f.active), (
            "退避中の項目まで自己修復され、タブ表示中のキー排他が壊れました"
        )

        # タブ非表示 (確定 off は連続 2 tick) → 通常経路の復元が働く
        shortcut_visibility.bmanga_panel_visible = lambda _context=None: False
        shortcut_visibility.any_bmanga_panel_visible = lambda _context=None: False
        keymap_mod._watch_bmanga_tab()
        keymap_mod._watch_bmanga_tab()
        assert bool(kmi_user_f.active), "タブ非表示後にF退避が復元されません"
    finally:
        shortcut_visibility.bmanga_panel_visible = original_panel_visible
        shortcut_visibility.any_bmanga_panel_visible = original_any_panel_visible
        mod.unregister()
        for kmi in created_user:
            try:
                km_user.keymap_items.remove(kmi)
            except Exception:  # noqa: BLE001
                pass
        for kmi in created_default:
            try:
                km_default.keymap_items.remove(kmi)
            except Exception:  # noqa: BLE001
                pass

    print("BMANGA_SHORTCUT_CONFLICT_REPAIR_CHECK_OK")


main()
