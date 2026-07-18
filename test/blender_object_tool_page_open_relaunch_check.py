"""Blender実機(UI)用: ページを開いた直後にオブジェクトツールが自動再開する確認.

2026-07-18 報告の不具合:
  ページを開く経路 (work_new / open_page_file / ダブルクリック遷移) は既存の
  オブジェクトツール常駐モーダルを終了させるが、再開経路が無かったため、
  ページを開いた直後はツールボタンがON表示のままドラッグ移動が効かず、
  ツールを選び直すまで直らなかった。

確認内容:
  1. 作品ファイル (work.blend) を開いた後、オブジェクトツールモーダルが自動再開する
  2. ページ用blend (page.blend) を開いた後も自動再開する

実行: --background なし (モーダル起動にウィンドウが必要)。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_page_open_relaunch"


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


def _object_tool_active() -> bool:
    module = sys.modules[MODULE_NAME]
    coma_modal_state = module.operators.coma_modal_state
    return coma_modal_state.get_active("object_tool") is not None


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_open_relaunch_"))
    state = {"phase": "wait_window", "ticks": 0}

    def _fail(message: str) -> None:
        print(f"RELAUNCH_CHECK_FAIL: {message}")
        sys.stdout.flush()
        os._exit(1)

    def _tick():
        state["ticks"] += 1
        if state["ticks"] > 400:
            _fail(f"timeout in phase {state['phase']}")
        try:
            if state["phase"] == "wait_window":
                if bpy.context.window is None:
                    return 0.1
                result = bpy.ops.bmanga.work_new(
                    filepath=str(temp_root / "RelaunchCheck.bmanga")
                )
                assert result == {"FINISHED"}, result
                state["phase"] = "wait_work_relaunch"
                state["ticks"] = 0
                return 0.1
            if state["phase"] == "wait_work_relaunch":
                if not _object_tool_active():
                    if state["ticks"] > 60:
                        _fail("作品ファイルを開いた後にオブジェクトツールが再開しない")
                    return 0.1
                print("work.blend relaunch OK")
                result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
                assert result == {"FINISHED"}, result
                state["phase"] = "wait_page_relaunch"
                state["ticks"] = 0
                return 0.1
            if state["phase"] == "wait_page_relaunch":
                if not Path(bpy.data.filepath).name == "page.blend":
                    return 0.1
                if not _object_tool_active():
                    if state["ticks"] > 60:
                        _fail("ページを開いた後にオブジェクトツールが再開しない")
                    return 0.1
                print("BMANGA_OBJECT_TOOL_PAGE_OPEN_RELAUNCH_OK")
                sys.stdout.flush()
                os._exit(0)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            os._exit(1)
        return 0.1

    # open_page_file が mainfile を差し替えても監視を続けるため persistent 必須
    bpy.app.timers.register(_tick, first_interval=0.2, persistent=True)


if __name__ == "__main__":
    main()
