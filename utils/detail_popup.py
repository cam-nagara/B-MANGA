"""選択レイヤーの詳細設定ダイアログを開く補助."""

from __future__ import annotations

import bpy

from . import layer_stack as layer_stack_utils
from . import log

_logger = log.get_logger(__name__)

_POS_PREFIX = "bmanga_detail_popup_pos"


def _pos_key(key: str, suffix: str) -> str:
    safe_key = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(key or "default"))
    return f"{_POS_PREFIX}_{safe_key}_{suffix}"


def _stored_position(window_manager, key: str) -> tuple[int, int] | None:
    try:
        if not bool(window_manager.get(_pos_key(key, "valid"), False)):
            return None
        return int(window_manager[_pos_key(key, "x")]), int(window_manager[_pos_key(key, "y")])
    except Exception:  # noqa: BLE001
        return None


def _clamp_to_window(window, x: int, y: int) -> tuple[int, int]:
    width = int(getattr(window, "width", 0) or 0)
    height = int(getattr(window, "height", 0) or 0)
    if width > 0:
        x = min(max(20, int(x)), max(20, width - 20))
    if height > 0:
        y = min(max(20, int(y)), max(20, height - 20))
    return int(x), int(y)


def _call_blender_menu(menu_idname: str) -> None:
    bpy.ops.wm.call_menu(name=menu_idname)


def position_dialog_cursor(context, event, *, key: str = "layer_detail", offset_x: int = 0) -> bool:
    """詳細設定ダイアログ用の位置候補を記録する。カーソル自体は動かさない。"""
    window = getattr(context, "window", None)
    window_manager = getattr(context, "window_manager", None)
    if window is None or window_manager is None or event is None:
        return False
    try:
        original_x = int(getattr(event, "mouse_x", 0))
        original_y = int(getattr(event, "mouse_y", 0))
        if original_x <= 0 and original_y <= 0:
            return False
        stored = _stored_position(window_manager, key)
        if stored is None:
            target_x, target_y = original_x + int(offset_x), original_y
        else:
            target_x, target_y = stored
        target_x, target_y = _clamp_to_window(window, target_x, target_y)
        window_manager[_pos_key(key, "x")] = int(target_x)
        window_manager[_pos_key(key, "y")] = int(target_y)
        window_manager[_pos_key(key, "valid")] = True
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("detail popup: failed to position dialog cursor")
        return False


def call_menu_right_of_cursor(context, event, menu_idname: str, *, half_width_px: int = 130) -> bool:
    """ポップアップメニューを現在のカーソル位置で開く.

    以前はメニューを右側へ寄せるためにカーソルを一時移動していたが、
    その移動が画面上で見えてしまうため、右クリックメニューではマウス位置を
    変更しない。
    """
    _ = context, event, half_width_px
    try:
        _call_blender_menu(menu_idname)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("context menu: call_menu failed: %s", menu_idname)
        return False


def _active_detail_index(context) -> int:
    scene = getattr(context, "scene", None)
    if scene is None:
        return -1
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return -1
    index = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    if 0 <= index < len(stack):
        return index
    return -1


def open_active_detail(context) -> bool:
    """現在選択中のレイヤー詳細を、既存の詳細設定ダイアログで開く."""
    index = _active_detail_index(context)
    if index < 0:
        return False
    try:
        result = bpy.ops.bmanga.layer_stack_detail(
            "INVOKE_DEFAULT",
            index=index,
            preserve_edge_selection=True,
            offset_from_selection=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("detail popup: failed to open active layer detail")
        return False
    return "FINISHED" in result or "RUNNING_MODAL" in result


def open_active_detail_deferred(context, *, delay: float = 0.01) -> bool:
    """modal のイベント処理が抜けた直後に詳細設定を開く."""
    return open_active_detail_deferred_if(context, lambda: True, delay=delay)


def open_active_detail_deferred_if(context, predicate, *, delay: float = 0.01) -> bool:
    """predicate が真のままなら、少し後で詳細設定を開く."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    scene_name = str(getattr(scene, "name", "") or "")

    def _open():
        try:
            if not bool(predicate()):
                return None
        except Exception:  # noqa: BLE001
            _logger.exception("detail popup: predicate failed")
            return None
        current_scene = bpy.data.scenes.get(scene_name)
        if current_scene is None:
            return None
        ctx = bpy.context
        if getattr(ctx, "window", None) is None or getattr(ctx, "scene", None) is None:
            return None
        open_active_detail(ctx)
        return None

    try:
        bpy.app.timers.register(_open, first_interval=max(0.0, float(delay)))
    except Exception:  # noqa: BLE001
        _logger.exception("detail popup: failed to schedule active layer detail")
        return False
    return True
