"""Blender 実機用: B-Name パネル表示中の O/F キー競合確認."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import bpy
from bpy.types import Operator

ROOT = Path(__file__).resolve().parents[1]


class WM_OT_bname_test_fluent_pie(Operator):
    bl_idname = "wm.bname_test_fluent_pie"
    bl_label = "B-Name Test Fluent Pie"

    def execute(self, context):
        context.window_manager["bname_test_fluent_triggered"] = True
        return {"FINISHED"}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_shortcut_conflict",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_shortcut_conflict"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _active_bname_items(keymap_mod) -> int:
    state = keymap_mod.get_state()
    if state is None:
        return 0
    return sum(1 for item in state.bname_items if bool(getattr(item, "active", False)))


def _add_conflict_keymaps():
    wm = bpy.context.window_manager
    created = []
    kc_user = wm.keyconfigs.user
    kc_addon = wm.keyconfigs.addon
    km_o = kc_user.keymaps.new(
        name="B-Name Test 3D View Object Conflict",
        space_type="VIEW_3D",
        region_type="WINDOW",
    )
    created.append((kc_user, km_o))
    kmi_o = km_o.keymap_items.new("wm.context_toggle", "O", "PRESS")
    try:
        kmi_o.properties.data_path = "tool_settings.use_proportional_edit_objects"
    except Exception:
        pass
    km_f = kc_addon.keymaps.new(
        name="B-Name Test 3D View Fluent Conflict",
        space_type="VIEW_3D",
        region_type="WINDOW",
    )
    created.append((kc_addon, km_f))
    kmi_f = km_f.keymap_items.new("wm.bname_test_fluent_pie", "F", "PRESS")
    return created, kmi_o, kmi_f


def _view3d_event_position() -> tuple[int, int] | None:
    wm = bpy.context.window_manager
    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return int(region.x + region.width * 0.5), int(region.y + region.height * 0.5)
    return None


def _simulate_key(key: str) -> None:
    window = bpy.context.window
    simulate = getattr(window, "event_simulate", None)
    if simulate is None:
        raise RuntimeError("event_simulate が使えません")
    pos = _view3d_event_position()
    if pos is None:
        raise RuntimeError("3Dビューが見つかりません")
    x, y = pos
    simulate(type=key, value="PRESS", x=x, y=y)
    simulate(type=key, value="RELEASE", x=x, y=y)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)


def main() -> None:
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    bpy.utils.register_class(WM_OT_bname_test_fluent_pie)
    mod = _load_addon()
    from bname_dev_shortcut_conflict.keymap import keymap as keymap_mod
    from bname_dev_shortcut_conflict.utils import shortcut_visibility

    work = bpy.context.scene.bname_work
    work.loaded = True
    work.work_dir = str(ROOT / "_shortcut_conflict_test.bname")
    bpy.context.scene.bname_mode = "PAGE"

    original_panel_visible = shortcut_visibility.bname_panel_visible
    original_any_panel_visible = shortcut_visibility.any_bname_panel_visible
    created = []
    try:
        created, kmi_o, kmi_f = _add_conflict_keymaps()
        assert bool(kmi_o.active), "O競合キーが作成直後に無効です"
        assert bool(kmi_f.active), "F競合キーが作成直後に無効です"

        shortcut_visibility.bname_panel_visible = lambda _context=None: True
        shortcut_visibility.any_bname_panel_visible = lambda _context=None: True
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) > 0, "B-Nameパネル表示中のショートカットが有効になりません"
        assert not bool(kmi_o.active), "Oの標準系競合が退避されません"
        assert not bool(kmi_f.active), "Fの他アドオン系競合が退避されません"

        if not bool(getattr(bpy.app, "background", False)):
            bpy.context.scene.tool_settings.use_proportional_edit_objects = False
            bpy.context.window_manager["bname_test_fluent_triggered"] = False
            _simulate_key("O")
            assert not bool(bpy.context.scene.tool_settings.use_proportional_edit_objects), (
                "Oキーでプロポーショナル編集が反応しています"
            )
            _simulate_key("F")
            assert not bool(bpy.context.window_manager.get("bname_test_fluent_triggered", False)), (
                "Fキーで他アドオン相当のパイメニューが反応しています"
            )

        shortcut_visibility.bname_panel_visible = lambda _context=None: False
        shortcut_visibility.any_bname_panel_visible = lambda _context=None: False
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) == 0, "B-Nameパネル非表示後もB-Nameキーが有効です"
        assert bool(kmi_o.active), "B-Nameパネル非表示後にO競合キーが復元されません"
        assert bool(kmi_f.active), "B-Nameパネル非表示後にF競合キーが復元されません"
    finally:
        shortcut_visibility.bname_panel_visible = original_panel_visible
        shortcut_visibility.any_bname_panel_visible = original_any_panel_visible
        mod.unregister()
        for kc, km in reversed(created):
            try:
                kc.keymaps.remove(km)
            except Exception:
                pass
        try:
            bpy.utils.unregister_class(WM_OT_bname_test_fluent_pie)
        except RuntimeError:
            pass

    print("BNAME_SHORTCUT_KEY_CONFLICT_CHECK_OK")
    if not bool(getattr(bpy.app, "background", False)):
        sys.stdout.flush()
        os._exit(0)


main()
