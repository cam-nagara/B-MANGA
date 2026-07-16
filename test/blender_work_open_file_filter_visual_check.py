"""作品「開く」のファイルブラウザーでフィルターOFFを通常画面確認する。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-17_work_open_filter"
SCREENSHOT = OUT_DIR / "file_browser_filter_off.png"
MODULE_NAME = "bmanga_dev_work_open_filter_visual"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


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
            "EXEC_DEFAULT",
            filepath=str(filepath),
            check_existing=False,
        )
    if result != {"FINISHED"} or not filepath.is_file():
        raise AssertionError(f"スクリーンショットを保存できません: {result}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if SCREENSHOT.exists():
        SCREENSHOT.unlink()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_work_open_filter_"))
    work_path = temp_root / "FilterVisible.bmanga"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    assert bpy.ops.bmanga.work_new(filepath=str(work_path)) == {"FINISHED"}
    work_blend = work_path / "work.blend"
    assert work_blend.is_file(), work_blend
    state = {"phase": "invoke"}

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
            if phase == "invoke":
                result = bpy.ops.bmanga.work_open("INVOKE_DEFAULT")
                if result != {"RUNNING_MODAL"}:
                    raise AssertionError(f"作品ファイルブラウザーを開けません: {result}")
                state["phase"] = "browser_open"
                return 0.5

            window, area = _file_browser_area()
            if window is None or area is None:
                raise AssertionError("作品『開く』のファイルブラウザーが見つかりません")
            params = area.spaces.active.params
            if params is None:
                return 0.1

            if phase == "browser_open":
                assert params.use_filter is False, "拡張子フィルターが初期オンです"
                assert params.filter_glob == "", params.filter_glob
                assert params.filter_search == "", params.filter_search
                assert Path(os.fsdecode(params.directory)).resolve() == work_path.resolve()
                assert work_blend.is_file()
                state["phase"] = "browser_loaded"
                return 2.0

            if phase == "browser_loaded":
                assert params.use_filter is False, "待機後にフィルターがオンへ戻りました"
                assert Path(os.fsdecode(params.directory)).resolve() == work_path.resolve()
                assert work_blend.is_file()
                _capture_screenshot(window, SCREENSHOT)
                print(
                    "BMANGA_WORK_OPEN_FILTER_VISUAL_OK "
                    f"{SCREENSHOT}",
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
