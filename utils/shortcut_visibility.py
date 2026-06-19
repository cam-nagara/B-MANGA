"""B-MANGA ショートカットの有効範囲判定."""

from __future__ import annotations

from pathlib import Path
import time

import bpy

from . import paths

BMANGA_PANEL_CATEGORY = "B-MANGA"
PANEL_DRAW_GRACE_SECONDS = 0.35

_panel_categories_cache: frozenset[str] | None = None
_panel_categories_time: float = 0.0
_PANEL_CATEGORIES_TTL = 5.0


def _bmanga_panel_categories() -> frozenset[str]:
    """登録済み BMANGA パネルの実際の bl_category を収集する.

    SIMPLE TABS 等でタブ名が変更されていても追従する。
    """
    global _panel_categories_cache, _panel_categories_time
    now = time.monotonic()
    if _panel_categories_cache is not None and now - _panel_categories_time < _PANEL_CATEGORIES_TTL:
        return _panel_categories_cache
    cats: set[str] = {BMANGA_PANEL_CATEGORY}
    for name in dir(bpy.types):
        if not name.startswith("BMANGA_PT_"):
            continue
        cls = getattr(bpy.types, name, None)
        if cls is not None:
            cat = getattr(cls, "bl_category", None)
            if cat:
                cats.add(cat)
    _panel_categories_cache = frozenset(cats)
    _panel_categories_time = now
    return _panel_categories_cache
_last_bmanga_panel_draw = 0.0
_last_bmanga_panel_area_ptr: int | None = None
_last_bmanga_panel_screen_ptr: int | None = None


def mark_bmanga_panel_drawn(context=None) -> None:
    """B-MANGA タブのパネルが実際に描画された時刻を記録する."""
    global _last_bmanga_panel_draw, _last_bmanga_panel_area_ptr, _last_bmanga_panel_screen_ptr
    _last_bmanga_panel_draw = time.monotonic()
    ctx = context or bpy.context
    area = getattr(ctx, "area", None)
    screen = getattr(ctx, "screen", None)
    _last_bmanga_panel_area_ptr = _as_pointer(area)
    _last_bmanga_panel_screen_ptr = _as_pointer(screen)


def _as_pointer(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value.as_pointer())
    except Exception:  # noqa: BLE001
        return None


def _recent_bmanga_panel_drawn(area=None, screen=None) -> bool:
    if _last_bmanga_panel_draw <= 0.0:
        return False
    if time.monotonic() - _last_bmanga_panel_draw > PANEL_DRAW_GRACE_SECONDS:
        return False
    area_ptr = _as_pointer(area)
    screen_ptr = _as_pointer(screen)
    if area_ptr is None and _last_bmanga_panel_area_ptr is not None:
        return False
    if area_ptr is not None and _last_bmanga_panel_area_ptr not in {None, area_ptr}:
        return False
    if screen_ptr is not None and _last_bmanga_panel_screen_ptr not in {None, screen_ptr}:
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


def _area_bmanga_status(area) -> str:
    """VIEW_3D サイドバーの B-MANGA タブ状態を返す.

    戻り値は ``bmanga`` / ``other`` / ``unknown`` / ``hidden``。
    ``unknown`` は Blender 側から現在タブ名を取得できないが、UI 領域は
    表示されている状態を表す。
    """
    regions = _visible_ui_regions(area)
    if not regions:
        return "hidden"
    for region in regions:
        _has_category, category = _category_for_region(region)
        if category in _bmanga_panel_categories():
            return "bmanga"
        if category:
            return "other"
    return "unknown"


def _area_has_bmanga_panel_category(area, screen=None) -> bool:
    status = _area_bmanga_status(area)
    if status == "bmanga":
        return True
    if status in ("unknown", "other"):
        return _recent_bmanga_panel_drawn(area, screen)
    return False


def any_bmanga_panel_visible(context=None) -> bool:
    """開いている 3D ビューのどこかで B-MANGA タブが表示されているか返す."""
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
            if _area_has_bmanga_panel_category(area, screen):
                return True
    return False


def any_bmanga_panel_status(context=None) -> str:
    """全 3D ビューを総合した B-MANGA タブ状態を返す.

    戻り値:
        ``"bmanga"``     — どこかのビューで B-MANGA タブがアクティブ
        ``"ambiguous"`` — タブ名を読めないビューがある (一時的な判定不能)
        ``"off"``       — すべて非表示 or 他タブ (確定的に B-MANGA 外)

    タブ名は再描画タイミングによって一瞬読めなくなることがあり、その瞬間を
    「タブが閉じた」と誤認すると常駐ツールが不意に終了する。呼び出し側が
    「確定 off」と「判定不能」を区別できるようにする。
    """
    if bool(getattr(bpy.app, "background", False)):
        return "bmanga"
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return "off"
    ambiguous = False
    for window in getattr(wm, "windows", []) or []:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []) or []:
            if getattr(area, "type", "") != "VIEW_3D":
                continue
            status = _area_bmanga_status(area)
            if status == "bmanga":
                return "bmanga"
            if status == "unknown":
                if _recent_bmanga_panel_drawn(area, screen):
                    return "bmanga"
                ambiguous = True
    return "ambiguous" if ambiguous else "off"


def current_blend_is_coma_blend() -> bool:
    """現在の .blend が B-MANGA のコマ用blendファイルなら True."""
    filepath = str(getattr(bpy.data, "filepath", "") or "")
    if not filepath:
        return False
    try:
        path = Path(filepath).resolve()
    except OSError:
        return False
    parts = path.parts
    if len(parts) < 3:
        return False
    page_id, coma_id, filename = parts[-3], parts[-2], parts[-1]
    return (
        paths.is_valid_page_id(page_id)
        and paths.is_valid_coma_id(coma_id)
        and filename == f"{coma_id}.blend"
    )


def shortcut_file_scope_allowed(context=None) -> bool:
    """ショートカットを実行してよい B-MANGA ファイル状態か返す."""
    try:
        from ..core.mode import MODE_PAGE, get_mode
        from ..core.work import get_work

        if current_blend_is_coma_blend():
            return False
        ctx = context or bpy.context
        work = get_work(ctx)
        if work is None or not bool(getattr(work, "loaded", False)):
            return False
        return get_mode(ctx) == MODE_PAGE
    except Exception:  # noqa: BLE001
        return False


def interaction_enabled(context=None) -> bool:
    """ユーザーが B-MANGA のビューポート操作を有効にしているか返す."""
    try:
        ctx = context or bpy.context
        scene = getattr(ctx, "scene", None)
        if scene is None:
            return True
        return bool(getattr(scene, "bmanga_interaction_enabled", True))
    except Exception:  # noqa: BLE001
        return True


def bmanga_panel_visible(context=None) -> bool:
    """現在操作中の 3D ビューで B-MANGA タブが表示されているか返す."""
    if bool(getattr(bpy.app, "background", False)):
        return True
    ctx = context or bpy.context
    area = getattr(ctx, "area", None)
    if area is not None:
        return _area_has_bmanga_panel_category(area, getattr(ctx, "screen", None))
    return False


def any_shortcuts_allowed(context=None) -> bool:
    """いずれかの 3D ビューで B-MANGA ショートカットを有効化してよいか返す."""
    return (
        interaction_enabled(context)
        and shortcut_file_scope_allowed(context)
        and any_bmanga_panel_visible(context)
    )


def shortcuts_allowed(context=None) -> bool:
    """B-MANGA のキーボード操作を現在の画面状態で実行してよいか返す."""
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        if prefs is not None and not bool(getattr(prefs, "keymap_enabled", True)):
            return False
    except Exception:  # noqa: BLE001
        pass
    return (
        interaction_enabled(context)
        and shortcut_file_scope_allowed(context)
        and bmanga_panel_visible(context)
    )
