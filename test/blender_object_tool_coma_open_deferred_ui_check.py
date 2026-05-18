"""Blender実機(UI)用: オブジェクトツールのコマダブルクリック遷移確認."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import MethodType, SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_deferred_coma_open_ui",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_deferred_coma_open_ui"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _start_check(temp_root: Path) -> Path:
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "DeferredOpen.bname"))
    assert result == {"FINISHED"}, result

    from bname_dev_deferred_coma_open_ui.operators import object_tool_op
    from bname_dev_deferred_coma_open_ui.utils import object_selection

    work = bpy.context.scene.bname_work
    page = work.pages[0]
    for _ in range(3):
        result = bpy.ops.bname.coma_add()
        assert result == {"FINISHED"}, result
    coma_index = next(
        index
        for index, coma in enumerate(page.comas)
        if str(getattr(coma, "coma_id", "") or getattr(coma, "id", "")) == "c04"
    )
    coma = page.comas[coma_index]
    work.active_page_index = 0
    page.active_coma_index = coma_index
    work_path = Path(bpy.data.filepath).resolve()

    fake_tool = SimpleNamespace(finished=False, keep_selection=False)
    fake_tool._try_enter_coma_from_hit = MethodType(
        object_tool_op.BNAME_OT_object_tool._try_enter_coma_from_hit,
        fake_tool,
    )

    def _finish_from_external(_context, *, keep_selection: bool) -> None:
        fake_tool.finished = True
        fake_tool.keep_selection = bool(keep_selection)

    fake_tool.finish_from_external = _finish_from_external
    hit = {
        "kind": "coma",
        "page": 0,
        "coma": coma_index,
        "part": "body",
        "key": object_selection.coma_key(page, coma),
    }
    assert fake_tool._try_enter_coma_from_hit(bpy.context, hit)
    assert fake_tool.finished and fake_tool.keep_selection
    assert Path(bpy.data.filepath).resolve() == work_path, "イベント中にコマファイルを開いています"
    return temp_root / "DeferredOpen.bname" / "p0001" / "c04" / "c04.blend"


def _assert_opened(expected: Path) -> None:
    assert Path(bpy.data.filepath).resolve() == expected.resolve(), bpy.data.filepath
    assert bpy.context.scene.bname_current_coma_id == "c04"


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_deferred_coma_open_"))
    attempts = {"count": 0}

    def _timer():
        attempts["count"] += 1
        if bpy.context.window is None and attempts["count"] < 30:
            return 0.1
        try:
            expected = _start_check(temp_root)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            os._exit(1)

        open_attempts = {"count": 0}

        def _assert_timer():
            open_attempts["count"] += 1
            try:
                if (
                    Path(bpy.data.filepath).resolve() != expected.resolve()
                    and open_attempts["count"] < 30
                ):
                    return 0.1
                _assert_opened(expected)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                os._exit(1)

            stable_attempts = {"count": 0}

            def _stability_timer():
                stable_attempts["count"] += 1
                try:
                    _assert_opened(expected)
                    for window in getattr(bpy.context.window_manager, "windows", []):
                        screen = getattr(window, "screen", None)
                        if screen is None:
                            continue
                        for area in getattr(screen, "areas", []):
                            area.tag_redraw()
                    if stable_attempts["count"] < 20:
                        return 0.1
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    os._exit(1)
                print("BNAME_OBJECT_TOOL_COMA_OPEN_DEFERRED_UI_CHECK_OK")
                sys.stdout.flush()
                os._exit(0)
                return None

            bpy.app.timers.register(_stability_timer, first_interval=0.1, persistent=True)
            return None

        bpy.app.timers.register(_assert_timer, first_interval=0.1, persistent=True)
        return None

    bpy.app.timers.register(_timer, first_interval=0.1)


if __name__ == "__main__":
    main()
