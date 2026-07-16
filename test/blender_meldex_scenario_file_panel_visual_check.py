"""Meldex読込導線とファイルブラウザーの初期フィルターOFFを通常画面で確認する."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-16_meldex_scenario_file_panel"
PANEL_SCREENSHOT = OUT_DIR / "work_panel.png"
FILE_BROWSER_SCREENSHOT = OUT_DIR / "file_browser_filter_off.png"
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


def _file_browser_area():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "FILE_BROWSER":
                return window, area
    return None, None


def _capture_screenshot(window, filepath: Path) -> None:
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT", filepath=str(filepath), check_existing=False
        )
    if result != {"FINISHED"} or not filepath.is_file():
        raise AssertionError(f"スクリーンショットを保存できません: {result}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for screenshot in (PANEL_SCREENSHOT, FILE_BROWSER_SCREENSHOT):
        if screenshot.exists():
            screenshot.unlink()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_meldex_panel_visual_"))
    work_path = temp_root / "PanelVisual.bmanga"
    scenario_path = work_path / "表示確認.scriptnote.json"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    assert bpy.ops.bmanga.work_new(filepath=str(work_path)) == {"FINISHED"}
    scenario_path.write_text(
        json.dumps(
            {
                "fileType": "meldex-scriptnote",
                "schema_version": 2,
                "version": 2,
                "title": "ファイルブラウザー表示確認",
                "layoutMode": "manga",
                "rows": [
                    {
                        "id": "filter-visible-row",
                        "role": "セリフ",
                        "text": "表示確認",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = {"phase": "panel"}

    def _finish(exit_code: int) -> None:
        try:
            addon.unregister()
        except Exception:
            pass
        shutil.rmtree(temp_root, ignore_errors=True)
        os._exit(exit_code)

    def _check_ui():
        try:
            phase = state["phase"]
            if phase == "panel":
                _show_bmanga_sidebar()
                window = bpy.context.window_manager.windows[0]
                _capture_screenshot(window, PANEL_SCREENSHOT)
                result = bpy.ops.bmanga.meldex_scenario_file_import("INVOKE_DEFAULT")
                if result != {"RUNNING_MODAL"}:
                    raise AssertionError(f"ファイルブラウザーを開けません: {result}")
                state["phase"] = "browser_open"
                return 0.5

            window, area = _file_browser_area()
            if window is None or area is None:
                raise AssertionError("Meldexシナリオのファイルブラウザーが見つかりません")
            space = area.spaces.active
            params = space.params
            if params is None:
                return 0.1

            if phase == "browser_open":
                assert params.use_filter is False, "拡張子フィルターが初期オンです"
                assert params.filter_glob == "", params.filter_glob
                assert params.filter_search == "", params.filter_search
                assert Path(os.fsdecode(params.directory)).resolve() == work_path.resolve()
                assert scenario_path.is_file()
                state["phase"] = "browser_loaded"
                return 2.0

            if phase == "browser_loaded":
                assert params.use_filter is False, "待機後に拡張子フィルターがオンへ戻りました"
                assert Path(os.fsdecode(params.directory)).resolve() == work_path.resolve()
                assert scenario_path.is_file()
                _capture_screenshot(window, FILE_BROWSER_SCREENSHOT)
                print(
                    "BMANGA_MELDEX_SCENARIO_FILE_PANEL_VISUAL_OK "
                    f"{PANEL_SCREENSHOT} {FILE_BROWSER_SCREENSHOT}",
                    flush=True,
                )
                _finish(0)
            raise AssertionError(f"不明なテスト段階です: {phase}")
        except Exception:
            import traceback

            traceback.print_exc()
            _finish(1)

    bpy.app.timers.register(_check_ui, first_interval=2.5)


if __name__ == "__main__":
    main()
