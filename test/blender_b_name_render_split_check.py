"""Blender 実機用: B-Name-Render 分離の登録確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_package(package_name: str, package_root: Path):
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bname = None
    render = None
    try:
        bname = _load_package("bname_dev", ROOT)
        assert getattr(bpy.types, "BNAME_PT_export", None) is not None
        assert getattr(bpy.types, "BNAME_PT_export").bl_label == "ページ出力"
        assert getattr(bpy.types, "BNAME_OT_export_page", None) is not None
        assert getattr(bpy.types, "BNAME_OT_export_all_pages", None) is not None
        assert getattr(bpy.types, "BNAME_OT_export_pdf", None) is not None
        assert "bname_dev.operators.io_op" in sys.modules
        assert "bname_dev.panels.export_panel" in sys.modules

        render = _load_package("bname_render_dev", ROOT / "addons" / "b_name_render")
        assert getattr(bpy.types, "BNAME_RENDER_PT_main", None) is not None
        assert getattr(bpy.types.Scene, "bname_render_state", None) is not None
        assert getattr(bpy.types.Scene, "fisheye_layout_mode", None) is not None
        assert getattr(bpy.types.Scene, "my_tool", None) is not None

        result = bpy.ops.bname_render.load_builtin_presets(reset=True)
        assert result == {"FINISHED"}, result
        state = bpy.context.scene.bname_render_state
        preset_names = {item.name for item in state.presets}
        for required in ("すべて", "効果", "ページ", "キャラ", "背景", "背景pen合成", "画像ノード再読み込み"):
            assert required in preset_names, required
        for preset in state.presets:
            for command in preset.commands:
                assert not command.name.startswith("未設定"), (preset.name, command.name)

        state.active_preset_index = 0
        preset = state.presets[0]
        before = len(preset.commands)
        result = bpy.ops.bname_render.command_add(command_type="RENDER", card_name="テストカード")
        assert result == {"FINISHED"}, result
        assert len(preset.commands) == before + 1
        assert preset.commands[preset.active_command_index].name == "テストカード"

        empty = state.presets.add()
        empty.name = "空プリセット"
        state.active_preset_index = len(state.presets) - 1
        result = bpy.ops.bname_render.command_card_click(index=0)
        assert result == {"CANCELLED"}, result

        restore_preset = state.presets.add()
        restore_preset.name = "状態復元"
        state.active_preset_index = len(state.presets) - 1
        restore_preset.commands.add().command_type = "STATE_BEGIN"
        restore_preset.commands.add().command_type = "STATE_BEGIN"
        restore_preset.commands.add().command_type = "STATE_END"
        bpy.context.scene.render.film_transparent = False
        result = bpy.ops.bname_render.preset_run()
        assert result == {"FINISHED"}, result
        assert bpy.context.scene.render.film_transparent is False

        print("BNAME_RENDER_SPLIT_OK")
    finally:
        if render is not None:
            try:
                render.unregister()
            except Exception:
                pass
        if bname is not None:
            try:
                bname.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
