"""B-Name ショートカットの有効範囲判定."""

from __future__ import annotations

import bpy

BNAME_PANEL_CATEGORY = "B-Name"
_last_bname_panel_draw = 0.0


def mark_bname_panel_drawn() -> None:
    """B-Name タブのパネルが実際に描画された時刻を記録する."""
    global _last_bname_panel_draw
    # 旧版はこの時刻をサイドバー状態取得に失敗した時の代替判定に使っていた。
    # Blender 側の通常操作を奪う原因になるため、現在はデバッグ用の記録だけにする。
    import time

    _last_bname_panel_draw = time.monotonic()


def _recent_bname_panel_drawn() -> bool:
    return False


def _area_has_bname_panel_category(area) -> bool:
    if area is None or getattr(area, "type", "") != "VIEW_3D":
        return False
    space = getattr(getattr(area, "spaces", None), "active", None)
    if space is None or not bool(getattr(space, "show_region_ui", False)):
        return False
    for region in getattr(area, "regions", []) or []:
        if getattr(region, "type", "") != "UI":
            continue
        if getattr(region, "width", 0) <= 1 or getattr(region, "height", 0) <= 1:
            continue
        if str(getattr(region, "active_panel_category", "") or "") == BNAME_PANEL_CATEGORY:
            return True
    return False


def any_bname_panel_visible(context=None) -> bool:
    """開いている 3D ビューのどこかで B-Name タブが表示されているか返す."""
    if bool(getattr(bpy.app, "background", False)):
        return True
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return False
    for window in getattr(wm, "windows", []) or []:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []) or []:
            if _area_has_bname_panel_category(area):
                return True
    return False


def bname_panel_visible(context=None) -> bool:
    """現在操作中の 3D ビューで B-Name タブが表示されているか返す."""
    if bool(getattr(bpy.app, "background", False)):
        return True
    ctx = context or bpy.context
    area = getattr(ctx, "area", None)
    if area is not None:
        return _area_has_bname_panel_category(area)
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
