"""共通ハンドル/ポイント操作インターセプト.

テキストツール・フキダシツール等、オブジェクトツール以外が
アクティブな場合でも選択ハンドルやしっぽポイントを操作可能にする。

使い方:
    1. ツールの modal で LEFTMOUSE PRESS を受けた直後に
       ``try_intercept_press(context, event, operator)`` を呼ぶ。
       True が返ったら ``{"RUNNING_MODAL"}`` を返す。
    2. MOUSEMOVE / LEFTMOUSE RELEASE で ``update_drag`` / ``finish_drag``
       を呼ぶ。
"""

from __future__ import annotations

from ..core.work import get_work
from ..utils import (
    free_transform,
    object_selection,
)
from . import (
    coma_edge_move_op,
    coma_modal_state,
    coma_picker,
    object_tool_balloon_tail,
    object_tool_free_transform,
    object_tool_selection,
)


_ATTR = "_handle_intercept_session"


class _DragSession:
    __slots__ = (
        "kind",
        "action",
        "keys",
        "snapshots",
        "start_x",
        "start_y",
        "moved",
        "center",
        "prev_x",
        "prev_y",
        "move_session",
    )

    def __init__(self, kind: str, action: str) -> None:
        self.kind = kind
        self.action = action
        self.keys: list[str] = []
        self.snapshots: list[dict] = []
        self.start_x: float = 0.0
        self.start_y: float = 0.0
        self.moved: bool = False
        self.center: tuple[float, float] = (0.0, 0.0)
        self.prev_x: float = 0.0
        self.prev_y: float = 0.0
        self.move_session = None


def is_dragging(operator) -> bool:
    return getattr(operator, _ATTR, None) is not None


def try_intercept_press(context, event, operator) -> bool:
    """LEFTMOUSE PRESS でハンドルを掴む処理。掴んだら True を返す."""
    if event.type != "LEFTMOUSE" or event.value != "PRESS":
        return False
    if bool(getattr(event, "alt", False)):
        return False

    from . import object_tool_op

    work = get_work(context)
    if work is None:
        return False

    # フキダシしっぽポイント (Ctrl なしでも選択中のものは掴める)
    if not bool(getattr(event, "ctrl", False)):
        if object_tool_balloon_tail.handle_plain_press(operator, context, event):
            return True

    # Ctrl+クリックのしっぽポイント追加/移動
    if bool(getattr(event, "ctrl", False)):
        if object_tool_balloon_tail.handle_ctrl_press(operator, context, event):
            return True

    # コマ枠線ハンドル ▲
    if coma_edge_move_op.extend_selected_handle_at_event(context, event):
        return True

    # 自由変形ハンドル (角)
    world_fn = object_tool_op._event_world_xy_mm
    hit = object_tool_free_transform.hit_transformed_handle_at_event(
        context, event, world_fn,
    )
    if hit is not None:
        free_action = object_tool_free_transform.free_action_for_hit(
            hit,
            ctrl=bool(getattr(event, "ctrl", False)),
            context=context,
        )
        if free_action:
            object_tool_op.activate_hit(context, hit, mode="single")
            session = _DragSession("free_transform", free_action)
            session.keys = list(object_selection.get_keys(context))
            coords = world_fn(context, event)
            if coords[0] is not None:
                session.start_x, session.start_y = coords
                session.snapshots = _capture_free_transform_snapshots(
                    context, session.keys,
                )
                setattr(operator, _ATTR, session)
                return True

    # 選択済みオブジェクトの body/edge ドラッグ (移動)
    hit = object_tool_op.hit_object_at_event(context, event)
    if hit is not None:
        part = str(hit.get("part", "") or "")
        if part in free_transform.CORNER_PARTS:
            free_action = object_tool_free_transform.free_action_for_hit(
                hit,
                ctrl=bool(getattr(event, "ctrl", False)),
                context=context,
            )
            if free_action:
                object_tool_op.activate_hit(context, hit, mode="single")
                session = _DragSession("free_transform", free_action)
                session.keys = list(object_selection.get_keys(context))
                coords = world_fn(context, event)
                if coords[0] is not None:
                    session.start_x, session.start_y = coords
                    session.snapshots = _capture_free_transform_snapshots(
                        context, session.keys,
                    )
                    setattr(operator, _ATTR, session)
                    return True
            else:
                object_tool_op.activate_hit(context, hit, mode="single")
                session = _DragSession("resize", part)
                session.keys = list(object_selection.get_keys(context))
                coords = world_fn(context, event)
                if coords[0] is not None:
                    session.start_x, session.start_y = coords
                    session.snapshots = _capture_resize_snapshots(context, session.keys)
                    setattr(operator, _ATTR, session)
                    return True

    # 回転ゾーン (角の少し外側)
    rot_hit = object_tool_free_transform.hit_rotation_zone_at_event(
        context, event, world_fn,
    )
    if rot_hit is not None:
        session = _DragSession("rotate", "")
        session.center = rot_hit["center"]
        coords = world_fn(context, event)
        if coords[0] is not None:
            session.start_x, session.start_y = coords
            session.prev_x, session.prev_y = coords
            session.keys = [rot_hit["key"]]
            session.snapshots = _capture_rotation_snapshots(context, session.keys)
            setattr(operator, _ATTR, session)
            coma_modal_state.set_modal_cursor(context, "SCROLL_XY")
            return True

    if bool(getattr(event, "ctrl", False)):
        hit = object_tool_op.hit_object_at_event(context, event)
        if hit is not None:
            object_tool_op.activate_hit(context, hit, mode="single")
            coords = world_fn(context, event)
            if coords[0] is not None:
                from . import layer_move_session
                move_ses = layer_move_session.LayerMoveDragSession(
                    context, (coords[0], coords[1]),
                )
                if move_ses.started:
                    session = _DragSession("move", "move")
                    session.move_session = move_ses
                    setattr(operator, _ATTR, session)
                    coma_modal_state.set_modal_cursor(context, "HAND")
                    return True
            return True

    return False


def update_drag(context, event, operator) -> bool:
    """MOUSEMOVE で進行中のドラッグを更新。ドラッグ中なら True."""
    session: _DragSession | None = getattr(operator, _ATTR, None)
    if session is None:
        return False

    from . import object_tool_op
    coords = object_tool_op._event_world_xy_mm(context, event)
    if coords[0] is None:
        return True
    x_mm, y_mm = coords

    if session.kind == "move":
        if session.move_session is not None:
            if session.move_session.apply(context, event):
                session.moved = True
        return True

    if session.kind == "rotate":
        delta_deg = object_tool_free_transform.compute_rotation_delta(
            session.center,
            session.start_x, session.start_y,
            x_mm, y_mm,
        )
        if abs(delta_deg) > 0.001:
            session.moved = True
        for snap in session.snapshots:
            entry = snap.get("entry")
            if entry is not None:
                entry.rotation_deg = float(snap.get("rotation_deg", 0.0)) + delta_deg
        return True

    if session.kind == "resize":
        dx = x_mm - session.start_x
        dy = y_mm - session.start_y
        if abs(dx) > 0.01 or abs(dy) > 0.01:
            session.moved = True
        from . import object_tool_op, balloon_op, effect_line_op
        for snap in session.snapshots:
            kind = snap.get("kind", "")
            x, y, w, h = snap["x"], snap["y"], snap["w"], snap["h"]
            nx, ny, nw, nh = object_tool_op._uniform_scale_result(
                session.action, x, y, w, h, dx, dy, 2.0,
            )
            if kind == "balloon":
                entry = snap.get("entry")
                page = snap.get("page")
                if entry is not None and page is not None:
                    balloon_op._set_balloon_rect(page, entry, nx, ny, nw, nh)
            elif kind == "effect":
                obj, layer = snap.get("effect_obj"), snap.get("effect_layer")
                if obj is not None and layer is not None:
                    cx, cy = snap.get("center", (x + w * 0.5, y + h * 0.5))
                    effect_line_op._write_effect_strokes(
                        context, obj, layer, (nx, ny, nw, nh),
                        center_xy_mm=(cx + (nx - x), cy + (ny - y)),
                    )
        return True

    if session.kind != "free_transform":
        return False

    dx = x_mm - session.start_x
    dy = y_mm - session.start_y
    if abs(dx) > 0.01 or abs(dy) > 0.01:
        session.moved = True
    corner = free_transform.corner_from_action(session.action)
    if corner is None:
        return True
    for snap in session.snapshots:
        entry = snap.get("entry")
        if entry is not None:
            free_transform.apply_corner_drag_to_entry(
                entry,
                snap.get("free_transform"),
                corner,
                dx,
                dy,
            )
        obj = snap.get("effect_obj")
        layer = snap.get("effect_layer")
        if obj is not None and layer is not None:
            free_transform.apply_corner_drag_to_effect_entry(
                obj, layer,
                snap.get("free_transform"),
                corner,
                dx,
                dy,
            )
    return True


def finish_drag(context, event, operator) -> bool:
    """LEFTMOUSE RELEASE でドラッグを終了。ドラッグ中だったら True."""
    session: _DragSession | None = getattr(operator, _ATTR, None)
    if session is None:
        return False
    moved = getattr(session, "moved", False)
    if session.kind == "move":
        if session.move_session is not None:
            session.move_session.finish(context)
        coma_modal_state.restore_modal_cursor(context)
        setattr(operator, _ATTR, None)
        return True
    if session.kind == "rotate":
        coma_modal_state.restore_modal_cursor(context)
    if moved:
        import bpy
        try:
            bpy.ops.ed.undo_push(message="B-MANGA: ハンドル操作")
        except Exception:  # noqa: BLE001
            pass
        from ..utils import layer_stack as _ls
        _ls.sync_layer_stack_after_data_change(context, align_coma_order=True)
    setattr(operator, _ATTR, None)
    return True


def cancel_drag(context, operator) -> None:
    """ESC などでドラッグをキャンセル。"""
    session: _DragSession | None = getattr(operator, _ATTR, None)
    if session is None:
        return
    if session.kind == "move":
        if session.move_session is not None:
            session.move_session.cancel(context)
        coma_modal_state.restore_modal_cursor(context)
        setattr(operator, _ATTR, None)
        return
    if session.kind == "free_transform":
        for snap in session.snapshots:
            entry = snap.get("entry")
            ft = snap.get("free_transform")
            if entry is not None and ft is not None:
                free_transform.restore_entry_snapshot(entry, ft)
    elif session.kind == "resize":
        from . import balloon_op, effect_line_op
        for snap in session.snapshots:
            kind = snap.get("kind", "")
            x, y, w, h = snap["x"], snap["y"], snap["w"], snap["h"]
            if kind == "balloon":
                entry = snap.get("entry")
                page = snap.get("page")
                if entry is not None and page is not None:
                    balloon_op._set_balloon_rect(page, entry, x, y, w, h)
            elif kind == "effect":
                obj, layer = snap.get("effect_obj"), snap.get("effect_layer")
                if obj is not None and layer is not None:
                    cx, cy = snap.get("center", (x + w * 0.5, y + h * 0.5))
                    effect_line_op._write_effect_strokes(context, obj, layer, (x, y, w, h), center_xy_mm=(cx, cy))
    elif session.kind == "rotate":
        for snap in session.snapshots:
            entry = snap.get("entry")
            if entry is not None:
                entry.rotation_deg = float(snap.get("rotation_deg", 0.0))
        coma_modal_state.restore_modal_cursor(context)
    setattr(operator, _ATTR, None)


def _capture_free_transform_snapshots(context, keys: list[str]) -> list[dict]:
    snapshots: list[dict] = []
    work = get_work(context)
    if work is None:
        return snapshots
    for key in keys:
        kind, page_id, item_id = object_selection.parse_key(key)
        if kind == "balloon":
            entry = _find_entry(context, kind, page_id, item_id)
            if entry is not None:
                snapshots.append({
                    "entry": entry,
                    "free_transform": free_transform.entry_snapshot(entry),
                })
        elif kind == "effect":
            obj, layer = object_tool_selection.find_effect_layer(item_id)
            if obj is not None and layer is not None:
                snapshots.append({
                    "effect_obj": obj,
                    "effect_layer": layer,
                    "free_transform": free_transform.effect_payload_for_layer(obj, layer),
                })
    return snapshots


def _capture_rotation_snapshots(context, keys: list[str]) -> list[dict]:
    snapshots: list[dict] = []
    work = get_work(context)
    if work is None:
        return snapshots
    for key in keys:
        kind, page_id, item_id = object_selection.parse_key(key)
        if kind == "balloon":
            entry = _find_entry(context, kind, page_id, item_id)
            if entry is not None:
                snapshots.append({
                    "entry": entry,
                    "rotation_deg": float(getattr(entry, "rotation_deg", 0.0)),
                })
        elif kind == "effect":
            scene = getattr(context, "scene", None)
            params = getattr(scene, "bmanga_effect_line_params", None) if scene else None
            if params is not None:
                snapshots.append({
                    "entry": params,
                    "rotation_deg": float(getattr(params, "rotation_deg", 0.0)),
                })
        elif kind == "image":
            from . import object_tool_op
            _idx, entry = object_tool_op._find_image_by_key(context, item_id)
            if entry is not None:
                snapshots.append({
                    "entry": entry,
                    "rotation_deg": float(getattr(entry, "rotation_deg", 0.0)),
                })
    return snapshots


def _capture_resize_snapshots(context, keys: list[str]) -> list[dict]:
    snapshots: list[dict] = []
    work = get_work(context)
    if work is None:
        return snapshots
    from . import effect_line_op
    for key in keys:
        kind, page_id, item_id = object_selection.parse_key(key)
        if kind == "balloon":
            entry, page = _find_entry_and_page(context, kind, page_id, item_id)
            if entry is not None:
                snapshots.append({
                    "kind": "balloon",
                    "entry": entry,
                    "page": page,
                    "x": float(getattr(entry, "x_mm", 0)),
                    "y": float(getattr(entry, "y_mm", 0)),
                    "w": float(getattr(entry, "width_mm", 0)),
                    "h": float(getattr(entry, "height_mm", 0)),
                })
        elif kind == "effect":
            obj, layer = object_tool_selection.find_effect_layer(item_id)
            if obj is not None and layer is not None:
                bounds = effect_line_op.effect_layer_bounds(obj, layer)
                if bounds is not None:
                    x, y, w, h = bounds
                    snapshots.append({
                        "kind": "effect",
                        "effect_obj": obj,
                        "effect_layer": layer,
                        "x": float(x),
                        "y": float(y),
                        "w": float(w),
                        "h": float(h),
                        "center": (float(x) + float(w) * 0.5, float(y) + float(h) * 0.5),
                    })
    return snapshots


def _find_entry(context, kind: str, page_id: str, item_id: str):
    from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
    work = get_work(context)
    if work is None:
        return None
    if kind == "balloon":
        if page_id == OUTSIDE_STACK_KEY:
            _idx, entry = object_tool_selection.find_shared_balloon_by_key(work, item_id)
            return entry
        _pi, _p, _idx, entry = object_tool_selection.find_balloon_by_key(work, page_id, item_id)
        return entry
    return None


def _find_entry_and_page(context, kind: str, page_id: str, item_id: str):
    from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
    work = get_work(context)
    if work is None:
        return None, None
    if kind == "balloon":
        if page_id == OUTSIDE_STACK_KEY:
            _idx, entry = object_tool_selection.find_shared_balloon_by_key(work, item_id)
            return entry, None
        _pi, page, _idx, entry = object_tool_selection.find_balloon_by_key(work, page_id, item_id)
        return entry, page
    return None, None
