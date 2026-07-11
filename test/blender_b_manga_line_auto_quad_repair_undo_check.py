"""Blender 5.1 UI実機: 自動修復・四角面化のUndo / Redo."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

from blender_b_manga_line_auto_quad_repair_check import (  # noqa: E402
    _clear_scene,
    _select_only,
    _triangulated_open_cylinder,
)
from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402


_PRESET_STORE = None
_SOURCE_FACES = 0
_STAGE = ""
_DEADLINE = 0.0
_TEST_KEYMAP = None
STATUS_PATH = ROOT / "_verify" / "2026-07-11_bml_auto_quad_repair_undo_status.txt"


def _install_test_shortcut() -> None:
    global _TEST_KEYMAP
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    keymap = keyconfig.keymaps.new(name="Window", space_type="EMPTY")
    item = keymap.keymap_items.new(
        "bmanga_line.auto_repair_quad_mesh",
        "Q",
        "PRESS",
        ctrl=True,
        alt=True,
    )
    _TEST_KEYMAP = (keymap, item)


def _fail() -> None:
    traceback.print_exc()
    os._exit(1)


def _monitor_external_undo():
    global _STAGE
    try:
        import b_manga_line as addon

        obj = bpy.data.objects.get("UndoQuad")
        if obj is not None and _STAGE == "WAIT_OPERATION":
            completed = (
                obj.data.polygons
                and all(len(polygon.vertices) == 4 for polygon in obj.data.polygons)
                and obj.get(addon.mesh_optimizer.OPTIMIZED_PROP) is True
                and not obj.bmanga_line_settings.auto_subdivision_for_midpoint
            )
            if completed:
                _STAGE = "WAIT_UNDO"
                STATUS_PATH.write_text("READY_UNDO", encoding="utf-8")
        elif obj is not None and _STAGE == "WAIT_UNDO":
            restored = (
                len(obj.data.polygons) == _SOURCE_FACES
                and all(len(polygon.vertices) == 3 for polygon in obj.data.polygons)
                and obj.get(addon.mesh_optimizer.OPTIMIZED_PROP) is None
                and obj.bmanga_line_settings.auto_subdivision_for_midpoint
            )
            if restored:
                _STAGE = "WAIT_REDO"
                STATUS_PATH.write_text("READY_REDO", encoding="utf-8")
        elif obj is not None and _STAGE == "WAIT_REDO":
            redone = (
                obj.data.polygons
                and all(len(polygon.vertices) == 4 for polygon in obj.data.polygons)
                and obj.get(addon.mesh_optimizer.OPTIMIZED_PROP) is True
                and not obj.bmanga_line_settings.auto_subdivision_for_midpoint
            )
            if redone:
                STATUS_PATH.write_text("PASS", encoding="utf-8")
                print("B-MANGA Liner auto quad repair undo check: PASS", flush=True)
                os._exit(0)
        if time.monotonic() > _DEADLINE:
            STATUS_PATH.write_text(f"FAIL:{_STAGE}", encoding="utf-8")
            raise AssertionError(f"外部キー入力待ちがタイムアウトしました: {_STAGE}")
    except Exception:  # noqa: BLE001
        _fail()
    return 0.1


def _after_load() -> None:
    global _STAGE, _DEADLINE
    try:
        source = bpy.data.objects["UndoQuad"]
        _select_only(source)
        _install_test_shortcut()
        _STAGE = "WAIT_OPERATION"
        _DEADLINE = time.monotonic() + 600.0
        STATUS_PATH.write_text("READY_OPERATION", encoding="utf-8")
        bpy.app.timers.register(
            _monitor_external_undo,
            first_interval=0.1,
            persistent=True,
        )
    except Exception:  # noqa: BLE001
        _fail()


def main() -> None:
    global _PRESET_STORE, _SOURCE_FACES
    import b_manga_line as addon

    _PRESET_STORE = temporary_line_preset_store()
    _PRESET_STORE.__enter__()
    addon.register()
    _clear_scene()
    obj = _triangulated_open_cylinder("UndoQuad")
    obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
    _SOURCE_FACES = len(obj.data.polygons)
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text("STARTING", encoding="utf-8")
    temp_dir = tempfile.mkdtemp(prefix="bml_quad_undo_")
    path = str(Path(temp_dir) / "undo_source.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.app.timers.register(_after_load, first_interval=0.1, persistent=True)
    bpy.ops.wm.open_mainfile(filepath=path)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        os._exit(1)
