"""Blender実機用: ページファイルのビュー／ツール初期配置順を確認する。"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_page_panel_order"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        from bmanga_dev_page_panel_order.panels.tool_panel import BMANGA_PT_tools
        from bmanga_dev_page_panel_order.panels.view_panel import BMANGA_PT_view

        assert BMANGA_PT_view.bl_order < BMANGA_PT_tools.bl_order, (
            f"ビュー({BMANGA_PT_view.bl_order})がツール({BMANGA_PT_tools.bl_order})より下です"
        )
        assert BMANGA_PT_view.bl_order == 13, BMANGA_PT_view.bl_order
        assert BMANGA_PT_tools.bl_order == 14, BMANGA_PT_tools.bl_order
        print("BMANGA_PAGE_PANEL_INITIAL_ORDER_OK", flush=True)
    finally:
        mod.unregister()


if __name__ == "__main__":
    try:
        main()
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
