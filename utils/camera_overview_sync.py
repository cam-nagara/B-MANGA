"""カメラビュー切替時に「全ページを一覧」状態へ自動で合わせる.

ユーザーが 3D ビューポートをカメラビュー (Numpad0 等) に切り替えると、
B-Name 作品 (ページモード) では全ページ一覧モードを ON にして全ページを
ビューポート枠にフィット表示する。カメラビュー以外の通常視点へ手動で
切り替えたら、自動 ON する前の一覧モード状態へ戻す。

msgbus の notify 内で直接ビュー操作はできないため、bpy.app.timers で
1 回だけ遅延実行する。
"""

from __future__ import annotations

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


def _view3d_items():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return []
    items = []
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
                        items.append((win, area, region, rv3d))
    return items


def _apply() -> None:
    global _PREV_OVERVIEW, _PENDING
    _PENDING = False
    ctx = bpy.context
    work = get_work(ctx)
    if work is None or not getattr(work, "loaded", False):
        return
    if get_mode(ctx) != MODE_PAGE:
        return
    view_items = _view3d_items()
    if not view_items:
        return
    scene = ctx.scene
    if scene is None:
        return
    camera_items = [
        (win, area, region, rv3d)
        for win, area, region, rv3d in view_items
        if str(getattr(rv3d, "view_perspective", "")) == "CAMERA"
    ]
    cur_overview = bool(getattr(scene, "bname_overview_mode", False))
    if camera_items:
        if _PREV_OVERVIEW is None:
            _PREV_OVERVIEW = cur_overview
        try:
            from . import overview_camera

            camera = overview_camera.ensure_overview_camera(scene, work)
            if camera is not None:
                scene.camera = camera
            for win, area, region, rv3d in camera_items:
                try:
                    rv3d.view_camera_offset = (0.0, 0.0)
                    rv3d.view_perspective = "CAMERA"
                except Exception:  # noqa: BLE001
                    pass
                try:
                    with ctx.temp_override(window=win, screen=getattr(win, "screen", None), area=area, region=region):
                        bpy.ops.view3d.view_center_camera()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    # view_center_camera は Blender 側の判断で view_camera_zoom を
                    # 拡大側へ戻すことがあるため、最後にカメラ枠全体が見える倍率へ
                    # 固定する。
                    rv3d.view_camera_zoom = -30
                    rv3d.view_camera_offset = (0.0, 0.0)
                except Exception:  # noqa: BLE001
                    pass
                area.tag_redraw()
            scene.bname_overview_mode = True
        except Exception:  # noqa: BLE001
            _logger.exception("camera-overview: overview camera update failed")
        return
    # カメラビュー以外へ切替: 自動介入していたら元の状態へ戻す
    if _PREV_OVERVIEW is not None:
        try:
            scene.bname_overview_mode = bool(_PREV_OVERVIEW)
        except Exception:  # noqa: BLE001
            pass
        _PREV_OVERVIEW = None
        for _win, area, _region, _rv3d in view_items:
            area.tag_redraw()


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
