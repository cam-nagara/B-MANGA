"""Blender UI screenshot timeout diagnostic."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy


OUT_DIR = Path(__file__).resolve().parents[1] / "_verify" / "ui_screenshot_diagnostic"
STATE_PATH = OUT_DIR / "state.json"


def _write(**update) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.update(update)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _finish() -> None:
    _write(finish_called=True)
    os._exit(0)


def _after_timer() -> float | None:
    _write(timer_fired=True)
    screenshot_path = OUT_DIR / "diagnostic_screenshot.png"
    _write(before_screenshot=True, screenshot_path=str(screenshot_path))
    try:
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(screenshot_path),
            check_existing=False,
        )
        _write(after_screenshot=True, screenshot_result=sorted(result))
    except Exception as exc:  # noqa: BLE001
        _write(after_screenshot_exception=repr(exc))
    bpy.app.timers.register(_finish, first_interval=0.5)
    return None


def main() -> None:
    _write(started=True, background=bool(bpy.app.background))
    bpy.app.timers.register(_after_timer, first_interval=0.5)


main()
