"""Blender 実機(背景)用: B-Name ショートカットの有効条件確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy

ROOT = Path(__file__).resolve().parents[1]


class _PtrNamespace(SimpleNamespace):
    def __init__(self, ptr: int, **kwargs):
        super().__init__(**kwargs)
        self._ptr = ptr

    def as_pointer(self):
        return self._ptr


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

    work = bpy.context.scene.bname_work
    work.loaded = True
    work.work_dir = str(ROOT / "_shortcut_visibility_test.bname")
    bpy.context.scene.bname_mode = "PAGE"

    original_panel_visible = shortcut_visibility.bname_panel_visible
    original_any_panel_visible = shortcut_visibility.any_bname_panel_visible
    original_allowed = shortcut_visibility.shortcuts_allowed
    conflict_km = None
    try:
        fake_bname_area = SimpleNamespace(
            type="VIEW_3D",
            spaces=SimpleNamespace(active=SimpleNamespace(show_region_ui=True)),
            regions=[
                SimpleNamespace(type="UI", width=260, height=900, active_panel_category="B-Name")
            ],
        )
        fake_other_area = SimpleNamespace(
            type="VIEW_3D",
            spaces=SimpleNamespace(active=SimpleNamespace(show_region_ui=True)),
            regions=[
                SimpleNamespace(type="UI", width=260, height=900, active_panel_category="Tool")
            ],
        )
        fake_unknown_area = _PtrNamespace(
            1001,
            type="VIEW_3D",
            spaces=SimpleNamespace(active=SimpleNamespace(show_region_ui=True)),
            regions=[
                SimpleNamespace(type="UI", width=260, height=900)
            ],
        )
        fake_unknown_screen = _PtrNamespace(2001, areas=[fake_unknown_area])
        assert shortcut_visibility._area_has_bname_panel_category(fake_bname_area), (
            "B-Nameタブのエリア判定が有効になりません"
        )
        assert not shortcut_visibility._area_has_bname_panel_category(fake_other_area), (
            "別タブのエリア判定でB-Nameショートカットが有効になります"
        )
        shortcut_visibility._last_bname_panel_draw = 0.0
        assert not shortcut_visibility._area_has_bname_panel_category(fake_unknown_area), (
            "タブ情報が取れないだけでB-Nameショートカットが有効になります"
        )
        shortcut_visibility.mark_bname_panel_drawn(
            SimpleNamespace(area=fake_unknown_area, screen=fake_unknown_screen)
        )
        assert shortcut_visibility._area_has_bname_panel_category(fake_unknown_area), (
            "B-Nameパネル描画後の代替判定が有効になりません"
        )
        assert not shortcut_visibility._area_has_bname_panel_category(fake_other_area), (
            "明示的に別タブと分かる場合にB-Nameショートカットが有効になります"
        )
        assert shortcut_visibility._area_bname_status(fake_other_area) == "other", (
            "別タブが明示されている状態を判定できません"
        )
        fake_reported_other_area = _PtrNamespace(
            1002,
            type="VIEW_3D",
            spaces=SimpleNamespace(active=SimpleNamespace(show_region_ui=True)),
            regions=[
                SimpleNamespace(type="UI", width=260, height=900, active_panel_category="Tool")
            ],
        )
        fake_reported_other_screen = _PtrNamespace(2002, areas=[fake_reported_other_area])
        shortcut_visibility.mark_bname_panel_drawn(
            SimpleNamespace(area=fake_reported_other_area, screen=fake_reported_other_screen)
        )
        assert shortcut_visibility._area_has_bname_panel_category(fake_reported_other_area), (
            "B-Nameパネル描画直後の補助判定が有効になりません"
        )
        shortcut_visibility._last_bname_panel_draw -= (
            shortcut_visibility.PANEL_DRAW_GRACE_SECONDS + 0.1
        )
        assert not shortcut_visibility._area_has_bname_panel_category(fake_reported_other_area), (
            "B-Nameパネル描画の補助判定が時間切れ後も有効です"
        )

        kc = bpy.context.window_manager.keyconfigs.addon
        conflict_km = kc.keymaps.new(name="B-Name Test Object Conflict", space_type="EMPTY", region_type="WINDOW")
        conflict_kmi = conflict_km.keymap_items.new("wm.call_menu", "F", "PRESS")
        assert bool(conflict_kmi.active), "競合確認用キーが作成直後に無効です"

        shortcut_visibility.bname_panel_visible = lambda _context=None: False
        shortcut_visibility.any_bname_panel_visible = lambda _context=None: False
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) == 0, "B-Nameタブ非表示扱いでショートカットが有効です"
        assert bool(conflict_kmi.active), "B-Nameタブ非表示扱いで他のショートカットが無効化されています"

        shortcut_visibility.bname_panel_visible = lambda _context=None: True
        shortcut_visibility.any_bname_panel_visible = lambda _context=None: True
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) > 0, "B-Nameタブ表示扱いでショートカットが有効になりません"
        assert not bool(conflict_kmi.active), "B-Nameタブ表示中に競合ショートカットが退避されません"

        keymap_mod.suspend_visibility_updates(seconds=1.0, reason="test blend switch")
        assert _active_bname_items(keymap_mod) == 0, "blend切替待機中にB-Nameキーが有効です"
        assert bool(conflict_kmi.active), "blend切替待機中に他のショートカットが退避されています"
        keymap_mod._SUSPEND_UNTIL = 0.0
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) > 0, "blend切替待機後にB-Nameキーが有効になりません"
        assert not bool(conflict_kmi.active), "blend切替待機後に競合ショートカットが退避されません"

        bpy.context.scene.bname_mode = "COMA"
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) == 0, "コマ用blendファイル扱いでB-Nameキーが有効です"
        assert bool(conflict_kmi.active), "コマ用blendファイル扱いで他のショートカットが退避されています"
        assert not shortcut_visibility.shortcuts_allowed(bpy.context), (
            "コマ用blendファイル扱いでB-Nameショートカットの実行判定が有効です"
        )

        bpy.context.scene.bname_mode = "PAGE"
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) > 0, "ページ一覧ファイル扱いへ戻してもB-Nameキーが有効になりません"
        assert not bool(conflict_kmi.active), "ページ一覧ファイル扱いへ戻しても競合ショートカットが退避されません"

        shortcut_visibility.bname_panel_visible = lambda _context=None: False
        shortcut_visibility.any_bname_panel_visible = lambda _context=None: False
        keymap_mod._watch_bname_tab()
        assert _active_bname_items(keymap_mod) == 0, "B-Nameタブ非表示へ戻した後もショートカットが有効です"
        assert bool(conflict_kmi.active), "B-Nameタブ非表示へ戻した後も他のショートカットが退避されたままです"

        keymap_mod.rebuild_keymap_from_prefs()
        assert _active_bname_items(keymap_mod) == 0, "キーマップ再構築後にB-Nameタブ非表示扱いが崩れています"

        shortcut_visibility.shortcuts_allowed = lambda _context=None: False
        assert not viewport_ops._shortcuts_allowed(bpy.context), "B-Nameタブ非表示扱いでナビゲート判定が有効です"

        shortcut_visibility.shortcuts_allowed = lambda _context=None: True
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = None
        result = bpy.ops.bname.set_mode_object("EXEC_DEFAULT")
        assert "FINISHED" in result, "オブジェクトが未選択の状態でオブジェクトツールへ切り替わりません"

        shortcut_visibility._last_bname_panel_draw = 0.0
        shortcut_visibility.mark_bname_panel_drawn(
            SimpleNamespace(area=fake_unknown_area, screen=fake_unknown_screen)
        )
        shortcut_visibility._last_bname_panel_draw -= (
            shortcut_visibility.PANEL_DRAW_GRACE_SECONDS + 0.1
        )
        assert shortcut_visibility._area_has_bname_panel_category(fake_unknown_area), (
            "タブ名が取得できない同一エリアで、B-Nameパネル表示判定が時間切れになります"
        )
    finally:
        shortcut_visibility.bname_panel_visible = original_panel_visible
        shortcut_visibility.any_bname_panel_visible = original_any_panel_visible
        shortcut_visibility.shortcuts_allowed = original_allowed
        mod.unregister()
        if conflict_km is not None:
            try:
                bpy.context.window_manager.keyconfigs.addon.keymaps.remove(conflict_km)
            except Exception:
                pass

    print("BNAME_SHORTCUT_VISIBILITY_CHECK_OK")


main()
