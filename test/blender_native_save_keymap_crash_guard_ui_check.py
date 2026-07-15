"""Blender UI実機: 保存復旧mainfile再読込とキーマップ更新の競合を反復検証."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from bpy.app.handlers import persistent


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_native_save_keymap_crash_guard_ui"
ITERATIONS = 20
_state = {
    "addon": None,
    "handlers": None,
    "keymap": None,
    "paths": (),
    "expected": None,
    "completed": 0,
    "temp": None,
    "crashes_before": set(),
}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _crash_logs() -> set[tuple[str, int]]:
    temp = Path(tempfile.gettempdir())
    result = set()
    for path in temp.glob("*.crash.txt"):
        try:
            result.add((str(path.resolve()), path.stat().st_mtime_ns))
        except OSError:
            pass
    return result


def _save_probe(path: Path, marker: str) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene["bmanga_keymap_crash_probe"] = marker
    result = bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    assert "FINISHED" in result


def _active_bmanga_items() -> int:
    keymap = _state["keymap"]
    state = keymap.get_state()
    return sum(1 for item in state.bmanga_items if bool(getattr(item, "active", False)))


def _fail(exc: BaseException) -> None:
    print("BMANGA_NATIVE_SAVE_KEYMAP_CRASH_GUARD_ERROR", flush=True)
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    os._exit(1)


def _finish() -> None:
    try:
        new_crashes = _crash_logs() - _state["crashes_before"]
        if new_crashes:
            raise AssertionError(f"新しいBlenderクラッシュログがあります: {new_crashes}")
        addon = _state["addon"]
        if addon is not None:
            addon.unregister()
        print(
            "BMANGA_NATIVE_SAVE_KEYMAP_CRASH_GUARD_OK",
            f"iterations={_state['completed']}",
            flush=True,
        )
        os._exit(0)
    except BaseException as exc:
        _fail(exc)


def _exercise_after_reload() -> None:
    try:
        expected = Path(_state["expected"])
        if Path(bpy.data.filepath).resolve() != expected.resolve():
            raise AssertionError(f"再読込先が不正です: {bpy.data.filepath} != {expected}")
        keymap = _state["keymap"]
        keymap._SUSPEND_UNTIL = 0.0
        keymap._watch_bmanga_tab()
        bpy.context.window_manager.keyconfigs.update()
        # Empty factory scenes have no active B-MANGA page/layer target.  The
        # operators still need to be resolved here because that allocates and
        # frees their RNA properties around the keyconfig update -- the crash
        # path under test -- but a context poll failure is expected.
        try:
            bpy.ops.bmanga.view_fit_page()
        except RuntimeError:
            pass
        try:
            bpy.ops.bmanga.layer_stack_multi_select(index=0, anchor_index=-1, mode="SET")
        except RuntimeError:
            pass
        bpy.context.window_manager.keyconfigs.update()
        _state["completed"] += 1
        if _state["completed"] >= ITERATIONS:
            _finish()
            return
        _schedule_next_reload()
    except BaseException as exc:
        _fail(exc)


@persistent
def _after_load(_dummy=None) -> None:
    bpy.app.timers.register(_exercise_after_reload, first_interval=0.2)


def _schedule_next_reload() -> None:
    try:
        paths = _state["paths"]
        target = paths[_state["completed"] % len(paths)]
        _state["expected"] = target
        handlers = _state["handlers"]
        handlers._schedule_native_save_reload(target, notice=False)
        keymap = _state["keymap"]
        if not keymap.is_visibility_update_suspended():
            raise AssertionError("再読込予約時にキーマップ更新が停止されていません")
        if _active_bmanga_items() != 0:
            raise AssertionError("再読込予約時にB-MANGAキーマップが無効化されていません")
    except BaseException as exc:
        _fail(exc)


def _run() -> None:
    try:
        if bpy.app.background:
            raise RuntimeError("このチェックは --background なしで実行してください")
        temp_root = Path(tempfile.mkdtemp(prefix="bmanga_keymap_crash_guard_"))
        path_a = temp_root / "probe_a.blend"
        path_b = temp_root / "probe_b.blend"
        _save_probe(path_a, "a")
        _save_probe(path_b, "b")
        bpy.ops.wm.open_mainfile(filepath=str(path_a), load_ui=False)
        addon = _load_addon()
        _state.update(
            {
                "addon": addon,
                "handlers": importlib.import_module(f"{MOD_NAME}.utils.handlers"),
                "keymap": importlib.import_module(f"{MOD_NAME}.keymap.keymap"),
                "paths": (path_a, path_b),
                "temp": temp_root,
                "crashes_before": _crash_logs(),
            }
        )
        bpy.app.handlers.load_post.append(_after_load)
        _schedule_next_reload()
    except BaseException as exc:
        _fail(exc)


bpy.app.timers.register(_run, first_interval=0.5)
