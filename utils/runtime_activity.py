"""Runtime activity policy for B-Name background work."""

from __future__ import annotations

import bpy

KEYMAP_WATCH_INTERVAL = 0.5
SETTINGS_SYNC_INTERVAL = 1.0
LOADED_WORK_IDLE_INTERVAL = 2.0
GUIDE_ACTIVE_INTERVAL = 0.12
GUIDE_IDLE_INTERVAL = 1.0


def is_background() -> bool:
    return bool(getattr(bpy.app, "background", False))


def work_loaded(context=None) -> bool:
    try:
        from ..core.work import get_work

        ctx = context or bpy.context
        work = get_work(ctx)
        return work is not None and bool(getattr(work, "loaded", False))
    except Exception:  # noqa: BLE001
        return False


def bname_panel_visible(context=None) -> bool:
    if is_background():
        return True
    try:
        from . import shortcut_visibility

        return shortcut_visibility.any_bname_panel_visible(context or bpy.context)
    except Exception:  # noqa: BLE001
        return False


def live_view_updates_allowed(context=None) -> bool:
    return bname_panel_visible(context)


def interval_for_loaded_work(
    context=None,
    *,
    active: float = KEYMAP_WATCH_INTERVAL,
    idle: float = LOADED_WORK_IDLE_INTERVAL,
) -> float:
    return float(active) if work_loaded(context) else float(idle)
