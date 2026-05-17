"""B-Name ショートカットの有効範囲判定."""

from __future__ import annotations

import time

import bpy

BNAME_PANEL_CATEGORY = "B-Name"
_PANEL_DRAW_GRACE_SEC = 0.75
_last_bname_panel_draw = 0.0


def mark_bname_panel_drawn() -> None:
    """B-Name タブのパネルが実際に描画された時刻を記録する."""
    global _last_bname_panel_draw
    _last_bname_panel_draw = time.monotonic()


def _recent_bname_panel_drawn() -> bool:
    if _last_bname_panel_draw <= 0.0:
        return False
    return (time.monotonic() - _last_bname_panel_draw) <= _PANEL_DRAW_GRACE_SEC


def bname_panel_visible(context=None) -> bool:
    """3Dビューのサイドバーで B-Name タブが表示されているか返す."""
    if bool(getattr(bpy.app, "background", False)):
        return True
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return False
    category_available = False
    for window in getattr(wm, "windows", []) or []:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []) or []:
            if getattr(area, "type", "") != "VIEW_3D":
                continue
            space = getattr(area.spaces, "active", None)
            if space is None or not bool(getattr(space, "show_region_ui", False)):
                continue
            for region in getattr(area, "regions", []) or []:
                if getattr(region, "type", "") != "UI":
                    continue
                if getattr(region, "width", 0) <= 1 or getattr(region, "height", 0) <= 1:
                    continue
                category = getattr(region, "active_panel_category", None)
                if category is not None:
                    category_available = True
                if str(category or "") == BNAME_PANEL_CATEGORY:
                    return True
    if not category_available:
        return _recent_bname_panel_drawn()
    return False


def shortcuts_allowed(context=None) -> bool:
    """B-Name のキーボード操作を現在の画面状態で実行してよいか返す."""
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        if prefs is not None and not bool(getattr(prefs, "keymap_enabled", True)):
            return False
    except Exception:  # noqa: BLE001
        pass
    return bname_panel_visible(context)
