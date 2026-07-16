"""オブジェクトツール上のフキダシしっぽ編集補助."""

from __future__ import annotations

import bpy

from ..core.work import get_work
from ..utils import (
    balloon_curve_object,
    balloon_merge_object,
    balloon_tail_geom,
    layer_stack as layer_stack_utils,
    object_selection,
    undo_transaction,
)
from . import balloon_op, balloon_tail_op


def clear_pending(tool) -> None:
    tool._pending_tail_page_id = ""
    tool._pending_tail_balloon_id = ""
    tool._pending_tail_points = []
    tool._pending_tail_index = -1


def handle_ctrl_press(tool, context, event) -> bool:
    work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
    if work is None or page is None or lx is None or ly is None:
        return False
    hit_index, entry, part = balloon_op._hit_balloon_entry(page, lx, ly)
    if entry is not None and hit_index >= 0:
        if part == "body":
            start_create_drag(tool, page, entry, float(lx), float(ly))
            return True
        if str(part).startswith("tail_segment:"):
            _prefix, tail_index, insert_index = str(part).split(":")
            if balloon_op._insert_tail_point_page(entry, int(tail_index), int(insert_index), lx, ly) >= 0:
                layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
                _push_undo("B-MANGA: しっぽ制御点追加")
            return True
        if str(part).startswith("tail_point:"):
            _prefix, tail_index, point_index = str(part).split(":")
            start_point_drag(tool, page, entry, int(tail_index), int(point_index), float(lx), float(ly))
            return True
    return append_pending_click(tool, context, page, float(lx), float(ly))


def handle_plain_press(tool, context, event) -> bool:
    """Ctrl無しの通常クリックで、既存しっぽポイントのドラッグを開始する.

    ポイントのハンドルは選択中のフキダシにだけ表示されるため、
    つかめる対象も選択中 (またはアクティブ) のフキダシに限定する。
    フキダシ実体が編集できるのはページ編集画面だけなので、ページ一覧
    (プレビュー表示) で不可視のポイントをつかんでしまわないよう、
    ページ編集シーン以外では何もしない。
    新規しっぽの作成は従来どおり Ctrl+ドラッグだけで行う。
    """
    from ..utils import page_file_scene

    if not page_file_scene.is_page_edit_scene(getattr(context, "scene", None)):
        return False
    work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
    if work is None or page is None or lx is None or ly is None:
        return False
    hit_index, entry, part = balloon_op._hit_balloon_entry(page, lx, ly)
    if entry is None or hit_index < 0 or not str(part).startswith("tail_point:"):
        return False
    key = object_selection.balloon_key(page, entry)
    if not object_selection.is_selected(context, key):
        from . import object_tool_selection

        if key != object_tool_selection.active_selection_key(context):
            return False
    _prefix, tail_index, point_index = str(part).split(":")
    start_point_drag(tool, page, entry, int(tail_index), int(point_index), float(lx), float(ly))
    return True


def open_point_menu(context, event) -> bool:
    work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
    if work is None or page is None or lx is None or ly is None:
        return False
    hit_index, entry, part = balloon_op._hit_balloon_entry(page, lx, ly)
    if entry is None or hit_index < 0 or not str(part).startswith("tail_point:"):
        return False
    _prefix, tail_index, point_index = str(part).split(":")
    tail = entry.tails[int(tail_index)]
    if len(balloon_tail_geom.tail_local_points(tail)) < 2:
        balloon_tail_geom.write_polyline_points(tail, list(balloon_tail_geom.local_axis_points(entry, tail)))
    return balloon_tail_op.open_tail_point_context_menu(
        context,
        str(getattr(page, "id", "") or ""),
        str(getattr(entry, "id", "") or ""),
        int(tail_index),
        int(point_index),
        event=event,
    )


def start_create_drag(tool, page, entry, x_mm: float, y_mm: float) -> None:
    tool._dragging = True
    tool._drag_action = "balloon_tail_create"
    tool._drag_start_x = float(x_mm)
    tool._drag_start_y = float(y_mm)
    tool._drag_page_id = str(getattr(page, "id", "") or "")
    tool._drag_balloon_id = str(getattr(entry, "id", "") or "")
    tool._drag_keys = []
    tool._snapshots = []
    tool._drag_moved = False


def start_point_drag(tool, page, entry, tail_index: int, point_index: int, x_mm: float, y_mm: float) -> None:
    tool._dragging = True
    tool._drag_action = "balloon_tail_point"
    tool._drag_start_x = float(x_mm)
    tool._drag_start_y = float(y_mm)
    tool._drag_page_id = str(getattr(page, "id", "") or "")
    tool._drag_balloon_id = str(getattr(entry, "id", "") or "")
    tool._tail_drag_tail_index = int(tail_index)
    tool._tail_drag_point_index = int(point_index)
    tail = entry.tails[int(tail_index)]
    points = balloon_tail_geom.tail_local_points(tail)
    if len(points) < 2:
        points = list(balloon_tail_geom.local_axis_points(entry, tail))
        balloon_tail_geom.write_polyline_points(tail, points)
    tool._tail_drag_points = points
    tool._drag_keys = []
    tool._snapshots = []
    tool._drag_moved = False


def append_pending_click(tool, context, page, x_mm: float, y_mm: float) -> bool:
    page_id = str(getattr(page, "id", "") or "")
    balloon_id = str(getattr(tool, "_pending_tail_balloon_id", "") or "")
    if not page_id or not balloon_id or page_id != str(getattr(tool, "_pending_tail_page_id", "") or ""):
        return False
    idx = balloon_op._find_balloon_index(page, balloon_id)
    if idx < 0:
        clear_pending(tool)
        return False
    entry = page.balloons[idx]
    points = list(getattr(tool, "_pending_tail_points", []) or [])
    points.append((float(x_mm), float(y_mm)))
    tail_index = int(getattr(tool, "_pending_tail_index", -1))
    if tail_index < 0:
        tail_index = balloon_op._add_tail_polyline(entry, points)
        if tail_index < 0:
            return False
        tool._pending_tail_index = tail_index
        tool._pending_tail_points = points
    else:
        if balloon_op._append_tail_point_page(entry, tail_index, x_mm, y_mm) < 0:
            return False
        tool._pending_tail_points = [
            (float(point.x_mm) + float(entry.x_mm), float(point.y_mm) + float(entry.y_mm))
            for point in entry.tails[tail_index].points
        ]
    layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
    _push_undo("B-MANGA: しっぽ編集")
    return True


def update_drag(tool, context, event) -> bool:
    if tool._drag_action not in {"balloon_tail_create", "balloon_tail_point"}:
        return False
    _work, page, lx, ly = balloon_op._resolve_local_xy_for_page_from_event(
        context,
        event,
        str(getattr(tool, "_drag_page_id", "") or ""),
    )
    if page is None or lx is None or ly is None:
        return True
    tool._last_tail_xy = (float(lx), float(ly))
    dx = float(lx) - tool._drag_start_x
    dy = float(ly) - tool._drag_start_y
    if abs(dx) > 0.05 or abs(dy) > 0.05:
        tool._drag_moved = True
    if tool._drag_action == "balloon_tail_point":
        _apply_point_drag(tool, page, dx, dy)
    layer_stack_utils.tag_view3d_redraw(context)
    return True


def finish_create_drag(tool, context) -> None:
    work = get_work(context)
    page = _find_page(work, str(getattr(tool, "_drag_page_id", "") or ""))
    if page is None:
        return
    idx = balloon_op._find_balloon_index(page, str(getattr(tool, "_drag_balloon_id", "") or ""))
    if idx < 0:
        return
    entry = page.balloons[idx]
    if not bool(getattr(tool, "_drag_moved", False)):
        tool._pending_tail_page_id = str(getattr(page, "id", "") or "")
        tool._pending_tail_balloon_id = str(getattr(entry, "id", "") or "")
        tool._pending_tail_points = [(float(tool._drag_start_x), float(tool._drag_start_y))]
        tool._pending_tail_index = -1
        return
    current = tuple(getattr(tool, "_last_tail_xy", (tool._drag_start_x, tool._drag_start_y)))
    if balloon_op._add_tail_polyline(entry, [(tool._drag_start_x, tool._drag_start_y), current]) >= 0:
        clear_pending(tool)
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        _push_undo("B-MANGA: しっぽ作成")


def cancel_point_drag(tool, context) -> bool:
    if tool._drag_action != "balloon_tail_point":
        return False
    work = get_work(context)
    page = _find_page(work, str(getattr(tool, "_drag_page_id", "") or ""))
    if page is None:
        return True
    idx = balloon_op._find_balloon_index(page, str(getattr(tool, "_drag_balloon_id", "") or ""))
    if idx < 0:
        return True
    entry = page.balloons[idx]
    tail_index = int(getattr(tool, "_tail_drag_tail_index", -1))
    points = list(getattr(tool, "_tail_drag_points", []) or [])
    if 0 <= tail_index < len(entry.tails) and len(points) >= 2:
        balloon_tail_geom.write_polyline_points(entry.tails[tail_index], points)
    return True


def point_drag_changed(tool, context) -> bool:
    """しっぽ制御点の開始時と現在値を比較する."""

    if str(getattr(tool, "_drag_action", "") or "") not in {
        "balloon_tail_point",
        "tail_point",
    }:
        return False
    work = get_work(context)
    page = _find_page(work, str(getattr(tool, "_drag_page_id", "") or ""))
    if page is None:
        return False
    idx = balloon_op._find_balloon_index(
        page,
        str(getattr(tool, "_drag_balloon_id", "") or ""),
    )
    if idx < 0:
        return False
    entry = page.balloons[idx]
    tail_index = int(getattr(tool, "_tail_drag_tail_index", -1))
    if not (0 <= tail_index < len(entry.tails)):
        return False
    return undo_transaction.states_differ(
        list(getattr(tool, "_tail_drag_points", []) or []),
        balloon_tail_geom.tail_local_points(entry.tails[tail_index]),
    )


def _apply_point_drag(tool, page, dx: float, dy: float) -> None:
    idx = balloon_op._find_balloon_index(page, str(getattr(tool, "_drag_balloon_id", "") or ""))
    if idx < 0:
        return
    entry = page.balloons[idx]
    tail_index = int(getattr(tool, "_tail_drag_tail_index", -1))
    point_index = int(getattr(tool, "_tail_drag_point_index", -1))
    points = list(getattr(tool, "_tail_drag_points", []) or [])
    if 0 <= tail_index < len(entry.tails) and 0 <= point_index < len(points):
        with balloon_curve_object.defer_auto_sync():
            changed = balloon_tail_geom.set_point(
                entry.tails[tail_index],
                point_index,
                (points[point_index][0] + dx, points[point_index][1] + dy),
            )
        if changed:
            with balloon_curve_object.suspend_auto_sync():
                balloon_curve_object.on_balloon_entry_changed(entry)
            if str(getattr(entry, "merge_group_id", "") or ""):
                bpy.context.view_layer.update()
                balloon_merge_object.sync_groups_for_page(bpy.context.scene, get_work(bpy.context), page)


def _find_page(work, page_id: str):
    if work is None:
        return None
    for page in getattr(work, "pages", []) or []:
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return page
    return None


def _push_undo(message: str) -> None:
    undo_transaction.push_undo(message)
