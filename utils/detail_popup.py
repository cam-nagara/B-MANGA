"""対象を隠さないダイアログ／右クリックメニュー配置補助。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import bpy

from . import log


_logger = log.get_logger(__name__)
_POS_PREFIX = "bmanga_detail_popup_pos"
_PLACEMENT_PREFIX = "bmanga_popup_placement"


@dataclass(frozen=True)
class PopupRect:
    """Blenderウィンドウ左下を原点とするピクセル矩形。"""

    left: float
    bottom: float
    right: float
    top: float

    @property
    def center_y(self) -> float:
        return (self.bottom + self.top) * 0.5


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


def _ui_scale(context) -> float:
    try:
        scale = float(context.preferences.system.ui_scale)
        return scale if scale > 0.0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def _view3d_regions(context):
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) if window is not None else None
    if screen is None:
        return
    preferred_area = getattr(context, "area", None)
    areas = list(screen.areas)
    if preferred_area in areas:
        areas.remove(preferred_area)
        areas.insert(0, preferred_area)
    for area in areas:
        if getattr(area, "type", "") != "VIEW_3D":
            continue
        space = getattr(getattr(area, "spaces", None), "active", None)
        rv3d = getattr(space, "region_3d", None) if space is not None else None
        if rv3d is None:
            continue
        for region in area.regions:
            if getattr(region, "type", "") == "WINDOW":
                yield region, rv3d


def _project_world_points(context, points) -> PopupRect | None:
    try:
        from bpy_extras.view3d_utils import location_3d_to_region_2d
    except Exception:  # noqa: BLE001
        return None
    fallback = None
    for region, rv3d in _view3d_regions(context) or ():
        projected = []
        for point in points:
            pos = location_3d_to_region_2d(region, rv3d, point)
            if pos is None:
                projected = []
                break
            projected.append((float(region.x) + float(pos.x), float(region.y) + float(pos.y)))
        if not projected:
            continue
        rect = PopupRect(
            min(point[0] for point in projected),
            min(point[1] for point in projected),
            max(point[0] for point in projected),
            max(point[1] for point in projected),
        )
        if fallback is None:
            fallback = rect
        region_left = float(region.x)
        region_bottom = float(region.y)
        region_right = region_left + float(region.width)
        region_top = region_bottom + float(region.height)
        if rect.right >= region_left and rect.left <= region_right and rect.top >= region_bottom and rect.bottom <= region_top:
            return rect
    return fallback


def _text_edit_target_rect(context) -> PopupRect | None:
    """編集中は選択文字だけでなく、テキスト編集領域全体を避ける。"""

    try:
        from ..core.work import get_work
        from ..operators import coma_modal_state, text_edit_runtime
        from . import geom, page_grid

        op = coma_modal_state.get_active("text_tool")
        if op is None or not bool(getattr(op, "_editing", False)):
            return None
        page, entry, _index = op._current_text_entry(context)
        if entry is None:
            return None
        rect = text_edit_runtime.text_rect(entry)
        cx, cy = rect.center
        angle = math.radians(float(getattr(entry, "rotation_deg", 0.0) or 0.0))
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        offset_x = 0.0
        offset_y = 0.0
        work = get_work(context)
        if work is not None and page is not None:
            page_id = str(getattr(page, "id", "") or "")
            page_index = next(
                (
                    index
                    for index, candidate in enumerate(work.pages)
                    if str(getattr(candidate, "id", "") or "") == page_id
                ),
                -1,
            )
            if page_index >= 0:
                offset_x, offset_y = page_grid.page_total_offset_mm(work, context.scene, page_index)
        points = []
        for x_mm, y_mm in (
            (rect.x, rect.y),
            (rect.x2, rect.y),
            (rect.x2, rect.y2),
            (rect.x, rect.y2),
        ):
            dx = x_mm - cx
            dy = y_mm - cy
            world_x = cx + dx * cos_a - dy * sin_a + offset_x
            world_y = cy + dx * sin_a + dy * cos_a + offset_y
            points.append((geom.mm_to_m(world_x), geom.mm_to_m(world_y), 0.0))
        return _project_world_points(context, points)
    except Exception:  # noqa: BLE001
        _logger.exception("popup: failed to resolve active text edit target")
        return None


def _selection_target_rect(context) -> PopupRect | None:
    try:
        from ..operators import object_tool_selection
        from . import geom

        key = object_tool_selection.active_selection_key(context)
        rect = object_tool_selection.selection_bounds_for_key(context, key) if key else None
        if rect is None:
            return None
        points = (
            (geom.mm_to_m(rect.x), geom.mm_to_m(rect.y), 0.0),
            (geom.mm_to_m(rect.x2), geom.mm_to_m(rect.y), 0.0),
            (geom.mm_to_m(rect.x2), geom.mm_to_m(rect.y2), 0.0),
            (geom.mm_to_m(rect.x), geom.mm_to_m(rect.y2), 0.0),
        )
        return _project_world_points(context, points)
    except Exception:  # noqa: BLE001
        _logger.exception("popup: failed to resolve B-MANGA selection target")
        return None


def _active_object_target_rect(context) -> PopupRect | None:
    obj = getattr(context, "active_object", None)
    corners = getattr(obj, "bound_box", None) if obj is not None else None
    matrix = getattr(obj, "matrix_world", None) if obj is not None else None
    if corners is None or matrix is None:
        return None
    try:
        from mathutils import Vector

        return _project_world_points(context, tuple(matrix @ Vector(corner) for corner in corners))
    except Exception:  # noqa: BLE001
        return None


def target_screen_rect(context, event=None) -> PopupRect | None:
    """編集中テキスト、B-MANGA選択、実オブジェクトの順で対象矩形を返す。"""

    rect = _text_edit_target_rect(context)
    if rect is not None:
        return rect
    rect = _selection_target_rect(context)
    if rect is not None:
        return rect
    rect = _active_object_target_rect(context)
    if rect is not None:
        return rect
    if event is None:
        return None
    try:
        x = float(event.mouse_x)
        y = float(event.mouse_y)
        return PopupRect(x, y, x, y)
    except Exception:  # noqa: BLE001
        return None


def _popup_anchor(context, target: PopupRect, popup_width: int) -> tuple[int, int, str]:
    window = getattr(context, "window", None)
    width = int(getattr(window, "width", 0) or 0)
    scale = _ui_scale(context)
    half_width = max(1.0, float(popup_width) * scale * 0.5)
    gap = 16.0 * scale
    margin = 12.0 * scale
    right_x = target.right + gap + half_width
    left_x = target.left - gap - half_width
    if width <= 0 or right_x + half_width + margin <= float(width):
        x = right_x
        side = "right"
    elif left_x - half_width - margin >= 0.0:
        x = left_x
        side = "left"
    else:
        max_x = max(half_width + margin, float(width) - half_width - margin) if width > 0 else right_x
        x = min(max(half_width + margin, right_x), max_x)
        side = "right_clamped"
    x, y = _clamp_to_window(window, int(round(x)), int(round(target.center_y)))
    return x, y, side


def _record_placement(context, target: PopupRect, anchor: tuple[int, int], side: str) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    values = {
        "target_left": target.left,
        "target_bottom": target.bottom,
        "target_right": target.right,
        "target_top": target.top,
        "anchor_x": anchor[0],
        "anchor_y": anchor[1],
        "side": side,
    }
    for suffix, value in values.items():
        try:
            wm[f"{_PLACEMENT_PREFIX}_{suffix}"] = value
        except Exception:  # noqa: BLE001
            return


def _call_at_anchor(context, event, popup_width: int, callback: Callable[[], object]):
    window = getattr(context, "window", None)
    target = target_screen_rect(context, event)
    if window is None or event is None or target is None:
        return callback()
    try:
        original = (int(event.mouse_x), int(event.mouse_y))
        anchor_x, anchor_y, side = _popup_anchor(context, target, popup_width)
        _record_placement(context, target, (anchor_x, anchor_y), side)
        window.cursor_warp(anchor_x, anchor_y)
    except Exception:  # noqa: BLE001
        _logger.exception("popup: target-aware placement failed")
        return callback()
    try:
        return callback()
    finally:
        # Blenderは呼出時点の座標をポップアップへコピーするため、同じイベント
        # 処理内ですぐ戻しても位置は維持され、ポインター移動は画面に現れない。
        try:
            window.cursor_warp(*original)
        except Exception:  # noqa: BLE001
            _logger.exception("popup: failed to restore pointer position")


def invoke_props_dialog(context, event, operator, *, width: int | None = None):
    """対象の右側（画面端だけ左側）へprops dialogを開く。"""

    popup_width = int(width or 300)

    def _invoke():
        if width is None:
            return context.window_manager.invoke_props_dialog(operator)
        return context.window_manager.invoke_props_dialog(operator, width=width)

    return _call_at_anchor(context, event, popup_width, _invoke)


def invoke_confirm(context, event, operator, *, width: int = 420, **kwargs):
    """対象の右側（画面端だけ左側）へ確認ダイアログを開く。"""

    return _call_at_anchor(
        context,
        event,
        max(1, int(width)),
        lambda: context.window_manager.invoke_confirm(operator, event, **kwargs),
    )


def position_dialog_cursor(context, event, *, key: str = "layer_detail", offset_x: int = 0) -> bool:
    """旧呼出互換。位置候補だけを記録し、実際の配置はinvoke_props_dialogが行う。"""

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
        _logger.exception("detail popup: failed to record dialog position")
        return False


def _call_blender_menu(menu_idname: str) -> None:
    bpy.ops.wm.call_menu(name=menu_idname)


def call_menu_right_of_cursor(
    context,
    event,
    menu_idname: str,
    *,
    half_width_px: int = 130,
) -> bool:
    """対象の右側へメニューを開き、ポインターは見かけ上動かさない。"""

    try:
        result = _call_at_anchor(
            context,
            event,
            max(1, int(half_width_px) * 2),
            lambda: _call_blender_menu(menu_idname),
        )
        _ = result
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("context menu: call_menu failed: %s", menu_idname)
        return False
