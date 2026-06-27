"""Blender実機用: フキダシツールとNURBSフキダシの入口統合を確認。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_balloon_tool_unification",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_tool_unification"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_tool_unification_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonTool.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file()
        assert "FINISHED" in result, result

        from bmanga_dev_balloon_tool_unification.operators import preset_op

        wm = bpy.context.window_manager
        items = preset_op._balloon_tool_preset_enum_items(None, bpy.context)
        ids = {item[0] for item in items}
        assert preset_op.BALLOON_TOOL_NURBS_PRESET in ids, "NURBS作成がフキダシプリセットにありません"
        assert "shape:ellipse" in ids, "通常フキダシ形状がプリセットにありません"

        wm.bmanga_balloon_tool_preset_selector = preset_op.BALLOON_TOOL_NURBS_PRESET
        assert preset_op.selected_balloon_tool_creation_mode(bpy.context) == "nurbs"
        assert preset_op.selected_balloon_tool_shape(bpy.context) == ("", "")

        wm.bmanga_balloon_tool_preset_selector = "shape:ellipse"
        assert preset_op.selected_balloon_tool_creation_mode(bpy.context) == "drag"
        assert preset_op.selected_balloon_tool_shape(bpy.context) == ("ellipse", "")

        assert bpy.ops.bmanga.balloon_tool.poll(), "フキダシツールが起動可能ではありません"
        assert bpy.ops.bmanga.balloon_nurbs_tool.poll(), "互換用NURBSフキダシ操作が残っていません"

        tool_panel_source = (ROOT / "panels" / "tool_panel.py").read_text(encoding="utf-8")
        assert '"bmanga.balloon_nurbs_tool"' not in tool_panel_source, "ツール欄にNURBS専用ボタンが残っています"

        print("BMANGA_BALLOON_TOOL_UNIFICATION_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
