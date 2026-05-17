"""カメラビュー切替時に「全ページを一覧」状態へ自動で合わせる.

ユーザーが 3D ビューポートをカメラビュー (Numpad0 等) に切り替えると、
B-Name 作品 (ページモード) では全ページ一覧モードを ON にして全ページを
ビューポート枠にフィット表示する。カメラビュー以外の通常視点へ手動で
切り替えたら、自動 ON する前の一覧モード状態へ戻す。

msgbus の notify 内で直接ビュー操作はできないため、bpy.app.timers で
1 回だけ遅延実行する。
"""

from __future__ import annotations

import time

import bpy
from bpy.app.handlers import persistent

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_work
from . import log

_logger = log.get_logger(__name__)

_OWNER = object()
# 自動で一覧 ON にした際の「元の overview 状態」。None=自動介入していない
_PREV_OVERVIEW: bool | None = None
_PENDING = False
# 一覧フィットは内部的に ORTHO へ切り替わり view_perspective が再度変化する。
# その自己誘発イベントで即「元に戻す」が走らないよう一定時間無視する。
_SUPPRESS_UNTIL = 0.0
_SUPPRESS_SEC = 0.7


def _active_view3d():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return None
    for win in wm.windows:
        scr = win.screen
        if scr is None:
            continue
        for area in scr.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    space = area.spaces.active
                    rv3d = getattr(space, "region_3d", None)
                    if rv3d is not None:
                        return win, area, region, rv3d
    return None


def _apply() -> None:
    global _PREV_OVERVIEW, _PENDING, _SUPPRESS_UNTIL
    _PENDING = False
    if time.monotonic() < _SUPPRESS_UNTIL:
        return
    ctx = bpy.context
    work = get_work(ctx)
    if work is None or not getattr(work, "loaded", False):
        return
    if get_mode(ctx) != MODE_PAGE:
        return
    found = _active_view3d()
    if found is None:
        return
    win, area, region, rv3d = found
    scene = ctx.scene
    if scene is None:
        return
    is_camera = str(getattr(rv3d, "view_perspective", "")) == "CAMERA"
    cur_overview = bool(getattr(scene, "bname_overview_mode", False))
    if is_camera:
        if _PREV_OVERVIEW is None:
            _PREV_OVERVIEW = cur_overview
        # フィットは ORTHO へ切り替わり再度 msgbus が走るため、その間の
        # 自己誘発イベントを無視する抑制ウィンドウを張る。
        _SUPPRESS_UNTIL = time.monotonic() + _SUPPRESS_SEC
        try:
            with ctx.temp_override(window=win, area=area, region=region):
                bpy.ops.bname.view_fit_all()
        except Exception:  # noqa: BLE001
            _logger.exception("camera-overview: view_fit_all failed")
        return
    # カメラビュー以外へ切替: 自動介入していたら元の状態へ戻す
    if _PREV_OVERVIEW is not None:
        try:
            scene.bname_overview_mode = bool(_PREV_OVERVIEW)
        except Exception:  # noqa: BLE001
            pass
        _PREV_OVERVIEW = None
        for a in (getattr(getattr(win, "screen", None), "areas", []) or []):
            if a.type == "VIEW_3D":
                a.tag_redraw()


def _schedule() -> None:
    global _PENDING
    if _PENDING:
        return
    _PENDING = True
    try:
        bpy.app.timers.register(_timer, first_interval=0.0)
    except Exception:  # noqa: BLE001
        _apply()


def _timer():
    try:
        _apply()
    except Exception:  # noqa: BLE001
        _logger.exception("camera-overview timer failed")
    return None


def _msgbus_callback() -> None:
    _schedule()


def _resubscribe() -> None:
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except Exception:  # noqa: BLE001
        pass
    try:
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.RegionView3D, "view_perspective"),
            owner=_OWNER,
            args=(),
            notify=_msgbus_callback,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("camera-overview msgbus subscribe skipped: %s", exc)


@persistent
def _on_load_post(_filepath: str) -> None:
    global _PREV_OVERVIEW
    _PREV_OVERVIEW = None
    _resubscribe()


def register() -> None:
    _resubscribe()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister() -> None:
    global _PREV_OVERVIEW
    _PREV_OVERVIEW = None
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except Exception:  # noqa: BLE001
        pass
    try:
        bpy.app.handlers.load_post.remove(_on_load_post)
    except (ValueError, Exception):  # noqa: BLE001
        pass