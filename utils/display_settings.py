"""Scene display/color-management defaults for B-Name files."""

from __future__ import annotations

import bpy


def apply_standard_color_management(scene=None) -> None:
    """Set the active scene color-management view transform to Standard."""
    target = scene or bpy.context.scene
    if target is None:
        return
    view_settings = getattr(target, "view_settings", None)
    if view_settings is None:
        return
    try:
        view_settings.view_transform = "Standard"
    except Exception:  # noqa: BLE001
        pass
    try:
        view_settings.look = "None"
    except Exception:  # noqa: BLE001
        pass
    try:
        view_settings.exposure = 0.0
    except Exception:  # noqa: BLE001
        pass
    try:
        view_settings.gamma = 1.0
    except Exception:  # noqa: BLE001
        pass


def apply_grayscale_view(scene, enabled: bool) -> None:
    """コマ用blendの「グレースケール表示」トグルに応じて色管理を切替える.

    - ON: ビュー変換 = AgX, 露出 = 1.0
    - OFF: ビュー変換 = 標準 (Standard), 露出 = 0.0
    - 「表示」(display device) はどちらも sRGB

    ユーザー操作 (トグル) でのみ呼ぶ。 開閉では呼ばない。
    """
    if scene is None:
        return
    display = getattr(scene, "display_settings", None)
    if display is not None:
        # view_transform の候補は display_device に依存するため先に設定する。
        try:
            display.display_device = "sRGB"
        except Exception:  # noqa: BLE001
            pass
    view_settings = getattr(scene, "view_settings", None)
    if view_settings is None:
        return
    if enabled:
        try:
            view_settings.view_transform = "AgX"
        except Exception:  # noqa: BLE001
            pass
        try:
            view_settings.exposure = 1.0
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            view_settings.view_transform = "Standard"
        except Exception:  # noqa: BLE001
            pass
        try:
            view_settings.exposure = 0.0
        except Exception:  # noqa: BLE001
            pass
