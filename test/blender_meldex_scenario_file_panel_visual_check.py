"""Blender通常画面で作品ファイルのMeldexシナリオ読込導線を撮影する."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-16_meldex_scenario_file_panel"
SCREENSHOT = OUT_DIR / "work_panel.png"
MODULE_NAME = "bmanga_dev_meldex_file_panel_visual"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _show_bmanga_sidebar() -> None:
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            area.spaces.active.show_region_ui = True
            area.spaces.active.show_region_toolbar = False
            for region in area.regions:
                if region.type == "UI":
                    try:
                        region.active_panel_category = "B-MANGA"
                    except Exception:
                        pass
            area.tag_redraw()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if SCREENSHOT.exists():
        SCREENSHOT.unlink()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_meldex_panel_visual_"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    assert bpy.ops.bmanga.work_new(filepath=str(temp_root / "PanelVisual.bmanga")) == {"FINISHED"}
    state = {"done": False}

    def _capture():
        if state["done"]:
            return None
        state["done"] = True
        try:
            _show_bmanga_sidebar()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
            result = bpy.ops.screen.screenshot(
                "EXEC_DEFAULT", filepath=str(SCREENSHOT), check_existing=False
            )
            if result != {"FINISHED"} or not SCREENSHOT.is_file():
                raise AssertionError(f"スクリーンショットを保存できません: {result}")
            print(f"BMANGA_MELDEX_SCENARIO_FILE_PANEL_VISUAL_OK {SCREENSHOT}")
        except Exception:
            import traceback

            traceback.print_exc()
            os._exit(1)
        finally:
            try:
                addon.unregister()
            except Exception:
                pass
            shutil.rmtree(temp_root, ignore_errors=True)
        os._exit(0)
        return None

    bpy.app.timers.register(_capture, first_interval=2.5)


if __name__ == "__main__":
    main()
