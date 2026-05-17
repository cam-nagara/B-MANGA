"""Blender 実機(背景)用: B-Name ショートカットの有効条件確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_shortcut_visibility", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_shortcut_visibility"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _active_bname_items(keymap_mod) -> int:
    state = keymap_mod.get_state()
    if state is None:
        return 0
    return sum(1 for item in state.bname_items if bool(getattr(item, "active", False)))


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    from bname_dev_shortcut_visibility.keymap import keymap as keymap_mod
    from bname_dev_shortcut_visibility.keymap import viewport_ops
    from bname_dev_shortcut_visibility.utils import shortcut_visibility

    original_panel_visible = shortcut_visibility.bname_panel_visible
    original_allowed = shortcut_visibility.shortcuts_allowed
    conflict_km = None
    try:
        kc = bpy.context.window_manager.keyconfigs.addon
        conflict_km = kc.keymaps.new(name="B-Name Test Conflict", space_type="EMPTY", region_type="WINDOW")
        conflict_kmi = conflict_km.keymap_items.new("wm.call_menu", "F", "PRESS")
        assert bool(conflict_kmi.active), "競合確認用キーが作成直後に無効です"

        shortcut_visibility.bname_panel_visible = lambda _context=None: False
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) == 0, "B-Nameタブ非表示扱いでショートカットが有効です"
        assert bool(conflict_kmi.active), "B-Nameタブ非表示扱いで他のショートカットが無効化されています"

        shortcut_visibility.bname_panel_visible = lambda _context=None: True
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) > 0, "B-Nameタブ表示扱いでショートカットが有効になりません"
        assert not bool(conflict_kmi.active), "B-Nameタブ表示中に競合ショートカットが退避されません"

        shortcut_visibility.bname_panel_visible = lambda _context=None: False
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) == 0, "B-Nameタブ非表示へ戻した後もショートカットが有効です"
        assert bool(conflict_kmi.active), "B-Nameタブ非表示へ戻した後も他のショートカットが退避されたままです"

        keymap_mod.rebuild_keymap_from_prefs()
        assert _active_bname_items(keymap_mod) == 0, "キーマップ再構築後にB-Nameタブ非表示扱いが崩れています"

        shortcut_visibility.shortcuts_allowed = lambda _context=None: False
        assert not viewport_ops._shortcuts_allowed(bpy.context), "B-Nameタブ非表示扱いでナビゲート判定が有効です"
    finally:
        shortcut_visibility.bname_panel_visible = original_panel_visible
        shortcut_visibility.shortcuts_allowed = original_allowed
        mod.unregister()
        if conflict_km is not None:
            try:
                bpy.context.window_manager.keyconfigs.addon.keymaps.remove(conflict_km)
            except Exception:
                pass

    print("BNAME_SHORTCUT_VISIBILITY_CHECK_OK")


main()
