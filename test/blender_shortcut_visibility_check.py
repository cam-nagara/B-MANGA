"""Blender 実機(背景)用: B-MANGA ショートカットの有効条件確認."""

from __future__ import annotations

import importlib.util
import sys
import time
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


class _RecordingLayout:
    def __init__(self, records: list[tuple[str, str, str]]) -> None:
        self.records = records

    def operator(self, op_id: str, text: str = "", **_kwargs):
        self.records.append(("operator", op_id, text))
        return SimpleNamespace()

    def prop(self, _data, prop_name: str, text: str = "", **_kwargs) -> None:
        self.records.append(("prop", prop_name, text))

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def label(self, text: str = "", **_kwargs) -> None:
        self.records.append(("label", "", text))

    def separator(self, **_kwargs) -> None:
        self.records.append(("separator", "", ""))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_shortcut_visibility", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_shortcut_visibility"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _active_bmanga_items(keymap_mod) -> int:
    state = keymap_mod.get_state()
    if state is None:
        return 0
    return sum(1 for item in state.bmanga_items if bool(getattr(item, "active", False)))


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    from bmanga_dev_shortcut_visibility.keymap import keymap as keymap_mod
    from bmanga_dev_shortcut_visibility.keymap import viewport_ops
    from bmanga_dev_shortcut_visibility.operators import coma_modal_state
    from bmanga_dev_shortcut_visibility.panels import work_panel
    from bmanga_dev_shortcut_visibility.utils import page_browser, runtime_activity, shortcut_visibility

    work = bpy.context.scene.bmanga_work
    work.loaded = True
    work.work_dir = str(ROOT / "_shortcut_visibility_test.bmanga")
    bpy.context.scene.bmanga_mode = "PAGE"
    assert runtime_activity.interval_for_loaded_work(bpy.context, active=0.5, idle=2.0) == 0.5, (
        "作品読込中の監視間隔がアクティブ扱いになりません"
    )
    work.loaded = False
    assert runtime_activity.interval_for_loaded_work(bpy.context, active=0.5, idle=2.0) == 2.0, (
        "作品未読込時の監視間隔が低頻度扱いになりません"
    )
    work.loaded = True

    original_panel_visible = shortcut_visibility.bmanga_panel_visible
    original_any_panel_visible = shortcut_visibility.any_bmanga_panel_visible
    original_allowed = shortcut_visibility.shortcuts_allowed
    conflict_km = None
    try:
        fake_bmanga_area = SimpleNamespace(
            type="VIEW_3D",
            spaces=SimpleNamespace(active=SimpleNamespace(show_region_ui=True)),
            regions=[
                SimpleNamespace(type="UI", width=260, height=900, active_panel_category="B-MANGA")
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
        assert shortcut_visibility._area_has_bmanga_panel_category(fake_bmanga_area), (
            "B-MANGAタブのエリア判定が有効になりません"
        )
        assert not shortcut_visibility._area_has_bmanga_panel_category(fake_other_area), (
            "別タブのエリア判定でB-MANGAショートカットが有効になります"
        )
        shortcut_visibility._last_bmanga_panel_draw = 0.0
        assert not shortcut_visibility._area_has_bmanga_panel_category(fake_unknown_area), (
            "タブ情報が取れないだけでB-MANGAショートカットが有効になります"
        )
        shortcut_visibility.mark_bmanga_panel_drawn(
            SimpleNamespace(area=fake_unknown_area, screen=fake_unknown_screen)
        )
        assert shortcut_visibility._area_has_bmanga_panel_category(fake_unknown_area), (
            "B-MANGAパネル描画後の代替判定が有効になりません"
        )
        assert not shortcut_visibility._area_has_bmanga_panel_category(fake_other_area), (
            "明示的に別タブと分かる場合にB-MANGAショートカットが有効になります"
        )
        assert shortcut_visibility._area_bmanga_status(fake_other_area) == "other", (
            "別タブが明示されている状態を判定できません"
        )
        page_browser_space = SimpleNamespace(
            type="VIEW_3D",
            show_region_toolbar=True,
            show_region_ui=True,
            show_gizmo=True,
            overlay=SimpleNamespace(show_overlays=True),
            region_3d=SimpleNamespace(view_perspective="PERSP"),
            shading=SimpleNamespace(type="MATERIAL", light="STUDIO", background_type="THEME"),
        )
        page_browser.apply_page_browser_view_settings(
            SimpleNamespace(type="VIEW_3D", spaces=[page_browser_space])
        )
        assert page_browser_space.show_region_ui is True, (
            "ページ一覧ビュー設定でサイドバー開閉状態を上書きしています"
        )
        assert page_browser_space.region_3d.view_perspective == "PERSP", (
            "ページ一覧ビュー設定でビュー回転状態を上書きしています"
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
        shortcut_visibility.mark_bmanga_panel_drawn(
            SimpleNamespace(area=fake_reported_other_area, screen=fake_reported_other_screen)
        )
        assert shortcut_visibility._area_has_bmanga_panel_category(fake_reported_other_area), (
            "パネル再描画直後の短い猶予中にB-MANGA操作が切れています"
        )
        time.sleep(shortcut_visibility.PANEL_DRAW_GRACE_SECONDS + 0.05)
        assert not shortcut_visibility._area_has_bmanga_panel_category(fake_reported_other_area), (
            "再描画猶予後もB-MANGA以外のタブでショートカットが有効です"
        )

        kc = bpy.context.window_manager.keyconfigs.addon
        conflict_km = kc.keymaps.new(name="B-MANGA Test Object Conflict", space_type="EMPTY", region_type="WINDOW")
        conflict_kmi = conflict_km.keymap_items.new("wm.call_menu", "F", "PRESS")
        assert bool(conflict_kmi.active), "競合確認用キーが作成直後に無効です"

        shortcut_visibility.bmanga_panel_visible = lambda _context=None: False
        shortcut_visibility.any_bmanga_panel_visible = lambda _context=None: False
        keymap_mod._watch_bmanga_tab()
        keymap_mod._watch_bmanga_tab()
        assert _active_bmanga_items(keymap_mod) == 0, "B-MANGAタブ非表示扱いでショートカットが有効です"
        assert bool(conflict_kmi.active), "B-MANGAタブ非表示扱いで他のショートカットが無効化されています"

        shortcut_visibility.bmanga_panel_visible = lambda _context=None: True
        shortcut_visibility.any_bmanga_panel_visible = lambda _context=None: True
        keymap_mod._watch_bmanga_tab()
        assert _active_bmanga_items(keymap_mod) > 0, "B-MANGAタブ表示扱いでショートカットが有効になりません"
        state = keymap_mod.get_state()
        has_context_menu_key = any(
            str(getattr(kmi, "idname", "") or "") == "bmanga.view_context_menu"
            and str(getattr(kmi, "type", "") or "") == "RIGHTMOUSE"
            for kmi in getattr(state, "bmanga_items", []) or []
        )
        assert has_context_menu_key, "B-MANGA右クリックメニューのキーマップがありません"
        assert not bool(conflict_kmi.active), "B-MANGAタブ表示中に競合ショートカットが退避されません"

        class _DummyModal:
            def __init__(self):
                self.finished = []

            def finish_from_external(self, context, *, keep_selection: bool) -> None:
                _ = context
                self.finished.append(bool(keep_selection))
                self._externally_finished = True

        dummy = _DummyModal()
        coma_modal_state.set_active("object_tool", dummy, bpy.context)
        shortcut_visibility.bmanga_panel_visible = lambda _context=None: False
        shortcut_visibility.any_bmanga_panel_visible = lambda _context=None: False
        # An active tool is stopped only after a confirmed second off tick.
        keymap_mod._watch_bmanga_tab()
        keymap_mod._watch_bmanga_tab()
        assert dummy.finished == [True], "B-MANGAタブ非表示時に起動済み操作が終了しません"
        assert not coma_modal_state.is_active("object_tool"), "B-MANGAタブ非表示後も起動済み操作が残っています"

        dummy_create = _DummyModal()
        coma_modal_state.set_active("coma_create", dummy_create, bpy.context)
        assert coma_modal_state.finish_all(bpy.context), "コマ作成中の操作をまとめて終了できません"
        assert dummy_create.finished == [True], "コマ作成中の操作がまとめて終了対象に含まれていません"

        shortcut_visibility.bmanga_panel_visible = lambda _context=None: True
        shortcut_visibility.any_bmanga_panel_visible = lambda _context=None: True
        keymap_mod.suspend_visibility_updates(seconds=1.0, reason="test blend switch")
        assert _active_bmanga_items(keymap_mod) == 0, "blend切替待機中にB-MANGAキーが有効です"
        assert bool(conflict_kmi.active), "blend切替待機中に他のショートカットが退避されています"
        keymap_mod._SUSPEND_UNTIL = 0.0
        keymap_mod._watch_bmanga_tab()
        assert _active_bmanga_items(keymap_mod) > 0, "blend切替待機後にB-MANGAキーが有効になりません"
        assert not bool(conflict_kmi.active), "blend切替待機後に競合ショートカットが退避されません"

        bpy.context.scene.bmanga_mode = "COMA"
        keymap_mod._watch_bmanga_tab()
        assert _active_bmanga_items(keymap_mod) == 0, "コマ用blendファイル扱いでB-MANGAキーが有効です"
        assert bool(conflict_kmi.active), "コマ用blendファイル扱いで他のショートカットが退避されています"
        assert not shortcut_visibility.shortcuts_allowed(bpy.context), (
            "コマ用blendファイル扱いでB-MANGAショートカットの実行判定が有効です"
        )
        records: list[tuple[str, str, str]] = []
        work_panel.BMANGA_PT_coma_return.draw(
            SimpleNamespace(layout=_RecordingLayout(records)),
            bpy.context,
        )
        assert ("prop", "bmanga_interaction_enabled", "B-MANGAショートカットキー") not in records, (
            "コマ用blendファイルのパネルにB-MANGAショートカットキーチェックボックスが残っています"
        )

        bpy.context.scene.bmanga_mode = "PAGE"
        keymap_mod._watch_bmanga_tab()
        assert _active_bmanga_items(keymap_mod) > 0, "ページ一覧ファイル扱いへ戻してもB-MANGAキーが有効になりません"
        assert not bool(conflict_kmi.active), "ページ一覧ファイル扱いへ戻しても競合ショートカットが退避されません"

        dummy_mode = _DummyModal()
        coma_modal_state.set_active("balloon_tool", dummy_mode, bpy.context)
        bpy.context.scene.bmanga_interaction_enabled = False
        assert dummy_mode.finished == [True], "B-MANGAショートカットキーOFFで起動済み操作が終了しません"
        assert not coma_modal_state.is_active("balloon_tool"), "B-MANGAショートカットキーOFF後も起動済み操作が残っています"
        keymap_mod._watch_bmanga_tab()
        assert _active_bmanga_items(keymap_mod) == 0, "B-MANGAショートカットキーOFFでショートカットが有効です"
        assert not shortcut_visibility.any_shortcuts_allowed(bpy.context), "B-MANGAショートカットキーOFFで実行判定が有効です"
        bpy.context.scene.bmanga_interaction_enabled = True

        shortcut_visibility.bmanga_panel_visible = lambda _context=None: False
        shortcut_visibility.any_bmanga_panel_visible = lambda _context=None: False
        keymap_mod._watch_bmanga_tab()
        assert _active_bmanga_items(keymap_mod) == 0, "B-MANGAタブ非表示へ戻した後もショートカットが有効です"
        assert bool(conflict_kmi.active), "B-MANGAタブ非表示へ戻した後も他のショートカットが退避されたままです"

        keymap_mod.rebuild_keymap_from_prefs()
        assert _active_bmanga_items(keymap_mod) == 0, "キーマップ再構築後にB-MANGAタブ非表示扱いが崩れています"

        shortcut_visibility.shortcuts_allowed = lambda _context=None: False
        assert not viewport_ops._shortcuts_allowed(bpy.context), "B-MANGAタブ非表示扱いでナビゲート判定が有効です"

        shortcut_visibility.shortcuts_allowed = lambda _context=None: True
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = None
        result = bpy.ops.bmanga.set_mode_object("EXEC_DEFAULT")
        assert "FINISHED" in result, "オブジェクトが未選択の状態でオブジェクトツールへ切り替わりません"

        shortcut_visibility._last_bmanga_panel_draw = 0.0
        shortcut_visibility.mark_bmanga_panel_drawn(
            SimpleNamespace(area=fake_unknown_area, screen=fake_unknown_screen)
        )
        shortcut_visibility._last_bmanga_panel_draw -= (
            shortcut_visibility.PANEL_DRAW_GRACE_SECONDS + 0.1
        )
        assert not shortcut_visibility._area_has_bmanga_panel_category(fake_unknown_area), (
            "タブ名が取得できない同一エリアで、B-MANGAパネル表示判定が時間切れ後も残ります"
        )
    finally:
        try:
            bpy.context.scene.bmanga_interaction_enabled = True
        except Exception:
            pass
        shortcut_visibility.bmanga_panel_visible = original_panel_visible
        shortcut_visibility.any_bmanga_panel_visible = original_any_panel_visible
        shortcut_visibility.shortcuts_allowed = original_allowed
        mod.unregister()
        if conflict_km is not None:
            try:
                bpy.context.window_manager.keyconfigs.addon.keymaps.remove(conflict_km)
            except Exception:
                pass

    print("BMANGA_SHORTCUT_VISIBILITY_CHECK_OK")


main()
