"""Runtime conversion for B-MANGA collection assets dropped in the viewport."""

from __future__ import annotations

import bpy

from . import asset_bundle, log

_logger = log.get_logger(__name__)
_TIMER_INTERVAL = 0.5
_registered = False


def _timer():
    try:
        asset_bundle.process_pending_dropped_assets(bpy.context)
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA asset drop processing failed")
    return _TIMER_INTERVAL if _registered else None


def register() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    try:
        bpy.app.timers.register(_timer, first_interval=_TIMER_INTERVAL, persistent=True)
    except Exception:  # noqa: BLE001
        _registered = False
        _logger.exception("B-MANGA asset drop timer register failed")


def unregister() -> None:
    global _registered
    _registered = False
    try:
        if bpy.app.timers.is_registered(_timer):
            bpy.app.timers.unregister(_timer)
    except Exception:  # noqa: BLE001
        pass
