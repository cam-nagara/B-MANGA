"""Blender 実機用: コマファイルの B-MANGA 右クリックメニュー確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_context_menu",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_context_menu"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _FakeLayout:
    def __init__(self):
        self.operator_context = "EXEC_DEFAULT"
        self.enabled = True
        self.ops: list[tuple[str, dict]] = []
        self.menus: list[tuple[str, dict]] = []

    def row(self, align=False):
        _ = align
        return self

    def separator(self):
        pass

    def menu(self, menu_idname, **kwargs):
        self.menus.append((menu_idname, kwargs))

    def operator(self, op_id, **kwargs):
        self.ops.append((op_id, kwargs))
        return type("_OpProps", (), {})()


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        from bmanga_dev_coma_context_menu.ui import context_menu
        from bmanga_dev_coma_context_menu.utils import page_file_scene, shortcut_visibility

        original_current_role = page_file_scene.current_role
        original_current_blend_is_coma = shortcut_visibility.current_blend_is_coma_blend
        original_panel_visible = shortcut_visibility.bmanga_panel_visible
        try:
            page_file_scene.current_role = lambda _context=None: (
                page_file_scene.ROLE_COMA,
                "p0001",
                "c01",
            )
            shortcut_visibility.current_blend_is_coma_blend = lambda: True
            shortcut_visibility.bmanga_panel_visible = lambda _context=None: True

            layout = _FakeLayout()
            context_menu.BMANGA_MT_object_context.draw(SimpleNamespace(layout=layout), bpy.context)
            assert [op_id for op_id, _kwargs in layout.ops] == [
                "bmanga.open_link_source",
                "bmanga.record_asset_link",
            ], layout.ops
            assert [kwargs.get("text") for _op_id, kwargs in layout.ops] == [
                "リンク元ファイルを開く",
                "このリンクを記録",
            ], layout.ops

            assert context_menu._OUTLINER_APPEND_MENUS == ("OUTLINER_MT_object",)
            assert "OUTLINER_MT_context_menu" in context_menu._OUTLINER_CLEANUP_MENUS
            assert "OUTLINER_MT_collection" in context_menu._OUTLINER_CLEANUP_MENUS

            bpy.ops.mesh.primitive_cube_add(size=1.0)
            with_panel = SimpleNamespace(layout=_FakeLayout())
            context_menu._draw_in_object_context(with_panel, bpy.context)
            assert [menu_id for menu_id, _kwargs in with_panel.layout.menus] == [
                context_menu.BMANGA_MT_object_context.bl_idname
            ], with_panel.layout.menus

            shortcut_visibility.bmanga_panel_visible = lambda _context=None: False
            without_panel = SimpleNamespace(layout=_FakeLayout())
            context_menu._draw_in_object_context(without_panel, bpy.context)
            assert without_panel.layout.menus == [], without_panel.layout.menus
        finally:
            page_file_scene.current_role = original_current_role
            shortcut_visibility.current_blend_is_coma_blend = original_current_blend_is_coma
            shortcut_visibility.bmanga_panel_visible = original_panel_visible
        print("BMANGA_COMA_CONTEXT_MENU_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
