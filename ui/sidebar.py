"""VIEW_3D サイドバーの B-MANGA タブ表示補助."""

from __future__ import annotations

import bpy

from ..utils import page_browser

B_NAME_CATEGORY = "B-MANGA"


def open_bmanga_sidebar(context=None, *, select_category: bool = True) -> int:
    """全 VIEW_3D でサイドバーを開き、可能なら B-MANGA タブを選択する."""
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return 0
    changed = 0
    for window in getattr(wm, "windows", []):
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []):
            if area.type != "VIEW_3D":
                continue
            if page_browser.is_page_browser_area_for_window(window, area):
                page_browser.apply_page_browser_view_settings(area)
                continue
            for space in getattr(area, "spaces", []):
                if space.type != "VIEW_3D":
                    continue
                try:
                    if not bool(getattr(space, "show_region_ui", False)):
                        space.show_region_ui = True
                        changed += 1
                except Exception:  # noqa: BLE001
                    pass
            if select_category:
                _select_bmanga_category(area)
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass
    return changed


def schedule_open_bmanga_sidebar(retries: int = 8, interval: float = 0.15) -> None:
    """ファイルロード後に UI area が再構築されるまで複数回サイドバーを開く."""
    state = {"left": max(1, int(retries))}

    def _tick():
        try:
            open_bmanga_sidebar(bpy.context)
        except Exception:  # noqa: BLE001
            pass
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        pass


def _bmanga_tab_name() -> str:
    """登録済みパネルの実際のbl_categoryを返す（SIMPLE TABS対応）."""
    cls = getattr(bpy.types, "BMANGA_PT_work", None) or getattr(bpy.types, "BMANGA_PT_view", None)
    if cls is not None:
        cat = getattr(cls, "bl_category", None)
        if cat:
            return cat
    return B_NAME_CATEGORY


def _select_bmanga_category(area) -> None:
    cat = _bmanga_tab_name()
    for region in getattr(area, "regions", []):
        if region.type != "UI":
            continue
        try:
            region.active_panel_category = cat
        except Exception:  # noqa: BLE001
            pass
