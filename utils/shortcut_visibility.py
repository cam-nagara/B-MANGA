"""B-Name ショートカットの有効範囲判定."""

from __future__ import annotations

import time

import bpy

BNAME_PANEL_CATEGORY = "B-Name"
PANEL_DRAW_GRACE_SECONDS = 2.0
_last_bname_panel_draw = 0.0
_last_bname_panel_area_ptr: int | None = None
_last_bname_panel_screen_ptr: int | None = None


def mark_bname_panel_drawn(context=None) -> None:
    """B-Name タブのパネルが実際に描画された時刻を記録する."""
    global _last_bname_panel_draw, _last_bname_panel_area_ptr, _last_bname_panel_screen_ptr
    _last_bname_panel_draw = time.monotonic()
    ctx = context or bpy.context
    area = getattr(ctx, "area", None)
    screen = getattr(ctx, "screen", None)
    _last_bname_panel_area_ptr = _as_pointer(area)
    _last_bname_panel_screen_ptr = _as_pointer(screen)


def _as_pointer(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value.as_pointer())
    except Exception:  # noqa: BLE001
        return None


def _recent_bname_panel_drawn(area=None, screen=None) -> bool:
    if _last_bname_panel_draw <= 0.0:
        return False
    if time.monotonic() - _last_bname_panel_draw > PANEL_DRAW_GRACE_SECONDS:
        return False
    area_ptr = _as_pointer(area)
    screen_ptr = _as_pointer(screen)
    if area_ptr is None and _last_bname_panel_area_ptr is not None:
        return False
    if area_ptr is not None and _last_bname_panel_area_ptr not in {None, area_ptr}:
        return False
    if screen_ptr is not None and _last_bname_panel_screen_ptr not in {None, screen_ptr}:
        return False
    return True


def _known_bname_panel_area(area=None, screen=None) -> bool:
    """タブ名を取得できない環境でも、同じUI領域なら表示中として扱う."""
    if _last_bname_panel_draw <= 0.0:
        return False
    area_ptr = _as_pointer(area)
    screen_ptr = _as_pointer(screen)
    if area_ptr is None or _last_bname_panel_area_ptr is None:
        return False
    if area_ptr != _last_bname_panel_area_ptr:
        return False
    if screen_ptr is not None and _last_bname_panel_screen_ptr not in {None, screen_ptr}:
        return False
    return True


def _visible_ui_regions(area):
    if area is None or getattr(area, "type", "") != "VIEW_3D":
        return []
    space = getattr(getattr(area, "spaces", None), "active", None)
    if space is None or not bool(getattr(space, "show_region_ui", False)):
        return []
    regions = []
    for region in getattr(area, "regions", []) or []:
        if getattr(region, "type", "") != "UI":
            continue
        if getattr(region, "width", 0) <= 1 or getattr(region, "height", 0) <= 1:
            continue
        regions.append(region)
    return regions


def _category_for_region(region) -> tuple[bool, str]:
    sentinel = object()
    value = getattr(region, "active_panel_category", sentinel)
    if value is sentinel:
        return False, ""
    return True, str(value or "")


def _area_bname_status(area) -> str:
    """VIEW_3D サイドバーの B-Name タブ状態を返す.

    戻り値は ``bname`` / ``other`` / ``unknown`` / ``hidden``。
    ``unknown`` は Blender 側から現在タブ名を取得できないが、UI 領域は
    表示されている状態を表す。
    """
    regions = _visible_ui_regions(area)
    if not regions:
        return "hidden"
    for region in regions:
        _has_category, category = _category_for_region(region)
        if category == BNAME_PANEL_CATEGORY:
            return "bname"
        if category:
            return "other"
    return "unknown"


def _area_has_bname_panel_category(area, screen=None) -> bool:
    status = _area_bname_status(area)
    if status == "bname":
        return True
    if status == "unknown":
        return _recent_bname_panel_drawn(area, screen) or _known_bname_panel_area(area, screen)
    if status == "other":
        return _recent_bname_panel_drawn(area, screen)
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
            if _area_has_bname_panel_category(area, screen):
                return True
    return False


def bname_panel_visible(context=None) -> bool:
    """現在操作中の 3D ビューで B-Name タブが表示されているか返す."""
    if bool(getattr(bpy.app, "background", False)):
        return True
    ctx = context or bpy.context
    area = getattr(ctx, "area", None)
    if area is not None:
        return _area_has_bname_panel_category(area, getattr(ctx, "screen", None))
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
