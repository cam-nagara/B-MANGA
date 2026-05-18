"""B-Name object tool for viewport selection, moving and box resizing."""

from __future__ import annotations

import time

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..ui import reparent_overlay
from ..utils import (
    edge_selection,
    gp_layer_parenting as gp_parent,
    layer_reparent,
    layer_stack as layer_stack_utils,
    object_selection,
)
from ..utils.geom import Rect
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY, outside_child_key
from .alt_reparent_op import (
    _DRAG_THRESHOLD_PX as _REPARENT_DRAG_PX,
    _has_selected_targets as _reparent_has_targets,
    _selected_count as _reparent_count,
    _set_confirm_for_target as _reparent_set_confirm,
    _set_error_for_target as _reparent_set_error,
    _set_overlay_for_target as _reparent_set_overlay,
)
from . import (
    balloon_op,
    object_tool_balloon_tail,
    effect_line_op,
    layer_move_session,
    coma_edge_drag_session,
    layer_move_op,
    coma_edge_move_op,
    coma_modal_state,
    coma_picker,
    object_tool_selection,
    raster_layer_op,
    selection_context_menu,
    text_op,
    view_event_region,
)

_DRAG_EPS_MM = 0.05
_EDGE_PICK_WORLD_TOLERANCE_MIN_MM = 3.0
_EDGE_PICK_WORLD_TOLERANCE_MAX_MM = 12.0
_DOUBLE_CLICK_INTERVAL_SEC = 0.4
_DOUBLE_CLICK_DISTANCE_PX = 8.0


def _find_page_by_id(work, page_id: str):
    if work is None:
        return -1, None
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return i, page
    return -1, None


def _coma_identity(panel) -> str:
    return str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")


def _find_coma_by_key(work, page_id: str, coma_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, panel in enumerate(getattr(page, "comas", []) or []):
        if _coma_identity(panel) == str(coma_id or ""):
            return page_index, page, i, panel
    return page_index, page, -1, None


def _find_balloon_by_key(work, page_id: str, item_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "balloons", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def _find_text_by_key(work, page_id: str, item_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "texts", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def _find_image_by_key(context, item_id: str):
    return object_tool_selection.find_image_by_key(context, item_id)


def _find_raster_by_key(context, item_id: str):
    return object_tool_selection.find_raster_by_key(context, item_id)


def _find_gp_layer(key: str):
    return object_tool_selection.find_gp_layer(key)


def _find_effect_layer(key: str):
    return object_tool_selection.find_effect_layer(key)


def _event_world_xy_mm(context, event) -> tuple[float | None, float | None]:
    return effect_line_op._event_world_xy_mm(context, event)


def _selection_mode(event) -> str:
    if bool(getattr(event, "ctrl", False)):
        return "toggle"
    if bool(getattr(event, "shift", False)):
        return "add"
    return "single"


def _point_segment_distance_mm(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
    nearest = (start[0] + dx * t, start[1] + dy * t)
    return ((point[0] - nearest[0]) ** 2 + (point[1] - nearest[1]) ** 2) ** 0.5


def _edge_pick_world_tolerance_mm(region, rv3d, mx: float, my: float, px_tolerance: float) -> float:
    here = coma_edge_move_op._region_to_world_mm(region, rv3d, mx, my)
    side = coma_edge_move_op._region_to_world_mm(region, rv3d, mx + 1.0, my)
    if here is None or side is None:
        return _EDGE_PICK_WORLD_TOLERANCE_MAX_MM
    mm_per_px = ((side[0] - here[0]) ** 2 + (side[1] - here[1]) ** 2) ** 0.5
    return min(
        max(_EDGE_PICK_WORLD_TOLERANCE_MIN_MM, mm_per_px * float(px_tolerance) * 1.5),
        _EDGE_PICK_WORLD_TOLERANCE_MAX_MM,
    )


def _edge_hit_close_in_world(context, work, edge_hit: dict, area, region, rv3d, mx: float, my: float) -> bool:
    point = coma_edge_move_op._region_to_world_mm(region, rv3d, mx, my)
    if point is None:
        return True
    page_index = int(edge_hit.get("page", -1))
    coma_index = int(edge_hit.get("coma", -1))
    if not (0 <= page_index < len(work.pages)):
        return False
    page = work.pages[page_index]
    if not (0 <= coma_index < len(page.comas)):
        return False
    poly = coma_edge_move_op._coma_polygon(page.comas[coma_index])
    if len(poly) < 2:
        return False
    ox, oy = coma_edge_move_op._page_offset_for_area(context, work, area, page_index)
    world_poly = [(float(x) + ox, float(y) + oy) for x, y in poly]
    if edge_hit.get("type") == "vertex":
        vertex_index = int(edge_hit.get("vertex", -1))
        if not (0 <= vertex_index < len(world_poly)):
            return False
        vertex = world_poly[vertex_index]
        dist = ((point[0] - vertex[0]) ** 2 + (point[1] - vertex[1]) ** 2) ** 0.5
        tolerance = _edge_pick_world_tolerance_mm(region, rv3d, mx, my, coma_edge_move_op.VERTEX_PICK_TOLERANCE_PX)
        return dist <= tolerance
    edge_index = int(edge_hit.get("edge", -1))
    if not (0 <= edge_index < len(world_poly)):
        return False
    start = world_poly[edge_index]
    end = world_poly[(edge_index + 1) % len(world_poly)]
    dist = _point_segment_distance_mm(point, start, end)
    tolerance = _edge_pick_world_tolerance_mm(region, rv3d, mx, my, coma_edge_move_op.EDGE_PICK_TOLERANCE_PX)
    return dist <= tolerance


def hit_object_at_event(context, event) -> dict | None:
    """Return the selectable B-Name object under a viewport event."""
    work = get_work(context)
    if work is None:
        return None
    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None
    area, region, rv3d, mx, my = view
    for resolver in (
        _hit_text_at_event,
        _hit_shared_text_at_event,
        _hit_balloon_at_event,
        _hit_shared_balloon_at_event,
        _hit_effect_at_event,
        _hit_image_at_event,
        _hit_gp_at_event,
        _hit_raster_at_event,
    ):
        hit = resolver(context, event)
        if hit is not None:
            return hit
    edge_hit = coma_edge_move_op._pick_edge_or_vertex(
        work,
        region,
        rv3d,
        int(mx),
        int(my),
        context=context,
        area=area,
    )
    if edge_hit is not None and not _edge_hit_close_in_world(context, work, edge_hit, area, region, rv3d, mx, my):
        edge_hit = None
    if edge_hit is not None:
        page = work.pages[int(edge_hit["page"])]
        panel = page.comas[int(edge_hit["coma"])]
        kind = "coma_vertex" if edge_hit.get("type") == "vertex" else "coma_edge"
        hit = dict(edge_hit)
        hit.update({
            "kind": kind,
            "key": object_selection.coma_key(page, panel),
            "area": area,
            "region": region,
            "rv3d": rv3d,
        })
        return hit
    for resolver in (
        _hit_coma_at_event,
        _hit_page_at_event,
    ):
        hit = resolver(context, event)
        if hit is not None:
            return hit
    return None


def _hit_text_at_event(context, event) -> dict | None:
    work, page, lx, ly, hit_index, hit_entry, hit_part, _can_create = (
        text_op._resolve_text_hit_from_event(context, event)
    )
    if work is None or page is None or hit_entry is None or hit_index < 0 or lx is None or ly is None:
        return None
    return {
        "kind": "text",
        "page_id": getattr(page, "id", ""),
        "index": hit_index,
        "part": "move" if hit_part == "body" else hit_part,
        "key": object_selection.text_key(page, hit_entry),
        "local": (float(lx), float(ly)),
    }


def _hit_shared_text_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_shared_text_at_event(context, event, _event_world_xy_mm)


def _hit_balloon_at_event(context, event) -> dict | None:
    work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
    if work is None or page is None or lx is None or ly is None:
        return None
    hit_index, hit_entry, hit_part = balloon_op._hit_balloon_entry(page, lx, ly)
    if hit_entry is None or hit_index < 0:
        return None
    return {
        "kind": "balloon",
        "page_id": getattr(page, "id", ""),
        "index": hit_index,
        "part": "move" if hit_part == "body" else hit_part,
        "key": object_selection.balloon_key(page, hit_entry),
        "local": (float(lx), float(ly)),
    }


def _hit_shared_balloon_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_shared_balloon_at_event(context, event, _event_world_xy_mm)


def _hit_effect_at_event(context, event) -> dict | None:
    x_mm, y_mm = _event_world_xy_mm(context, event)
    if x_mm is None or y_mm is None:
        return None
    obj, layer, bounds, part = effect_line_op._hit_effect_layer(context, x_mm, y_mm)
    if obj is None or layer is None or bounds is None:
        return None
    return {
        "kind": "effect",
        "layer_name": object_selection.parse_key(object_selection.effect_key(layer))[2],
        "part": "move" if part == "body" else part,
        "key": object_selection.effect_key(layer),
        "world": (float(x_mm), float(y_mm)),
    }


def _hit_image_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_image_at_event(context, event, _event_world_xy_mm)


def _hit_gp_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_gp_at_event(context, event, _event_world_xy_mm)


def _hit_raster_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_raster_at_event(context, event, _event_world_xy_mm)


def selection_bounds_for_key(context, key: str) -> Rect | None:
    return object_tool_selection.selection_bounds_for_key(context, key)


def active_selection_key(context) -> str:
    return object_tool_selection.active_selection_key(context)


def _hit_coma_at_event(context, event) -> dict | None:
    work = get_work(context)
    panel_hit = coma_picker.find_coma_at_event(context, event)
    if work is None or panel_hit is None:
        return None
    page_index, coma_index = panel_hit
    page = work.pages[page_index]
    panel = page.comas[coma_index]
    return {
        "kind": "coma",
        "page": page_index,
        "coma": coma_index,
        "part": "body",
        "key": object_selection.coma_key(page, panel),
    }


def _hit_page_at_event(context, event) -> dict | None:
    work = get_work(context)
    page_index = coma_picker.find_page_at_event(context, event)
    if work is None or page_index is None or not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    return {
        "kind": "page",
        "page": page_index,
        "part": "body",
        "key": object_selection.page_key(page),
    }


def _select_stack_target(context, kind: str, key: str) -> bool:
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return False
    uid = layer_stack_utils.target_uid(kind, key)
    for i, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return bool(layer_stack_utils.select_stack_index(context, i))
    return False


def activate_hit(context, hit: dict, *, mode: str) -> None:
    """Activate a hit object in the same way as the object tool selection path."""
    work = get_work(context)
    if work is None:
        return
    kind = hit["kind"]
    key = str(hit.get("key", "") or "")
    if kind == "page":
        page_index = int(hit["page"])
        if 0 <= page_index < len(work.pages):
            from ..utils import active_target as _at

            _at.focus_active_page(context.scene, work, page_index)
            _select_stack_target(context, "page", getattr(work.pages[page_index], "id", ""))
        edge_selection.clear_selection(context)
    elif kind in {"coma", "coma_edge", "coma_vertex"}:
        page_index = int(hit["page"])
        coma_index = int(hit["coma"])
        if page_index < 0:
            _select_stack_target(context, "coma", outside_child_key(object_selection.parse_key(key)[2]))
            edge_selection.clear_selection(context)
            object_selection.select_key(context, key, mode=mode)
            object_tool_selection.sync_outliner_selection_for_keys(
                context,
                object_selection.get_keys(context),
            )
            return
        page = work.pages[page_index]
        # 新規レイヤー追加 (resolve_active_target) もこのコマを active として
        # 扱えるよう、active_page_index / active_coma_index に加えて
        # bname_current_coma_id も同期する
        from ..utils import active_target as _at

        _at.focus_active_coma(context.scene, work, page_index, coma_index)
        if kind == "coma_edge":
            edge_selection.set_selection(
                context,
                "edge",
                page_index=page_index,
                coma_index=coma_index,
                edge_index=int(hit.get("edge", -1)),
            )
        elif kind == "coma_vertex":
            vertex_mode = "toggle" if mode == "toggle" else "add" if mode == "add" else "single"
            edge_selection.set_vertex_selection(
                context,
                page_index=page_index,
                coma_index=coma_index,
                vertex_index=int(hit.get("vertex", -1)),
                mode=vertex_mode,
            )
        else:
            edge_selection.set_selection(
                context,
                "border",
                page_index=page_index,
                coma_index=coma_index,
            )
    elif kind == "balloon":
        page_index, page = _find_page_by_id(work, hit.get("page_id", ""))
        if page is None and hit.get("page_id", "") == OUTSIDE_STACK_KEY:
            _select_stack_target(context, "balloon", outside_child_key(object_selection.parse_key(key)[2]))
        elif page is not None:
            balloon_op._select_balloon_index(
                context,
                work,
                page,
                int(hit.get("index", -1)),
                mode=mode,
            )
            work.active_page_index = page_index
        edge_selection.clear_selection(context)
    elif kind == "text":
        _page_index, page = _find_page_by_id(work, hit.get("page_id", ""))
        if page is None and hit.get("page_id", "") == OUTSIDE_STACK_KEY:
            _select_stack_target(context, "text", outside_child_key(object_selection.parse_key(key)[2]))
        elif page is not None:
            text_op._select_text_index(context, work, page, int(hit.get("index", -1)))
        edge_selection.clear_selection(context)
    elif kind == "effect":
        obj, layer = _find_effect_layer(hit.get("layer_name", ""))
        if layer is not None:
            effect_line_op._select_effect_layer(context, obj, layer)
        edge_selection.clear_selection(context)
    elif kind == "image":
        index, entry = _find_image_by_key(context, object_selection.parse_key(key)[2])
        if entry is not None:
            if not _select_stack_target(context, "image", getattr(entry, "id", "")):
                context.scene.bname_active_image_layer_index = index
                context.scene.bname_active_layer_kind = "image"
        edge_selection.clear_selection(context)
    elif kind == "raster":
        index, entry = _find_raster_by_key(context, object_selection.parse_key(key)[2])
        if entry is not None:
            if not _select_stack_target(context, "raster", getattr(entry, "id", "")):
                context.scene.bname_active_raster_layer_index = index
                context.scene.bname_active_layer_kind = "raster"
        edge_selection.clear_selection(context)
    elif kind == "gp":
        obj, layer = _find_gp_layer(hit.get("layer_key", object_selection.parse_key(key)[2]))
        if layer is not None:
            if not _select_stack_target(context, "gp", layer_stack_utils._node_stack_key(layer)):
                try:
                    context.view_layer.objects.active = obj
                    obj.select_set(True)
                    obj.data.layers.active = layer
                except Exception:  # noqa: BLE001
                    pass
                context.scene.bname_active_layer_kind = "gp"
        edge_selection.clear_selection(context)
    if kind != "balloon" or hit.get("page_id", "") == OUTSIDE_STACK_KEY:
        object_selection.select_key(context, key, mode=mode)
    object_tool_selection.sync_outliner_selection_for_keys(
        context,
        object_selection.get_keys(context),
    )


def enter_coma_from_hit(context, hit: dict) -> bool:
    if str(hit.get("kind", "") or "") != "coma":
        return False
    activate_hit(context, hit, mode="single")
    try:
        # ダブルクリックは EXEC_DEFAULT 呼び出し (ウィンドウ/モーダル無し)。
        # 未作成コマのテンプレート選択ダイアログ (fileselect_add) は
        # この文脈では機能せず RUNNING_MODAL のまま何も開かない。
        # ダブルクリックでは確実にコマを開くため、プロンプトは抑止し
        # 既存 cNN.blend / 解決済みテンプレート / 空シーンから開く。
        result = bpy.ops.bname.enter_coma_mode(
            "EXEC_DEFAULT", prompt_template_if_missing=False
        )
    except Exception:  # noqa: BLE001
        return False
    return result in ({"FINISHED"}, {"RUNNING_MODAL"})


def _rect_resize_result(
    action: str,
    x: float,
    y: float,
    w: float,
    h: float,
    dx: float,
    dy: float,
    min_size: float,
) -> tuple[float, float, float, float]:
    if action == "move":
        return x + dx, y + dy, w, h
    right = x + w
    top = y + h
    new_left = x
    new_right = right
    new_bottom = y
    new_top = top
    if "left" in action:
        new_left = min(right - min_size, x + dx)
    if "right" in action:
        new_right = max(x + min_size, right + dx)
    if "bottom" in action:
        new_bottom = min(top - min_size, y + dy)
    if "top" in action:
        new_top = max(y + min_size, top + dy)
    return new_left, new_bottom, new_right - new_left, new_top - new_bottom


class BNAME_OT_object_tool(Operator):
    bl_idname = "bname.object_tool"
    bl_label = "オブジェクトツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _dragging: bool
    _drag_action: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_keys: list[str]
    _snapshots: list[dict]
    _drag_moved: bool
    _edge_drag: object | None
    _layer_drag: object | None
    _marquee_mode: str
    _marquee_start_x: float
    _marquee_start_y: float
    _marquee_current_x: float
    _marquee_current_y: float

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work is not None and getattr(work, "loaded", False))

    def invoke(self, context, _event):
        active = coma_modal_state.get_active("object_tool")
        if active is not None:
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        coma_modal_state.finish_all(context, except_tool="object_tool")
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "DEFAULT")
        self._clear_drag_state()
        self._clear_click_state()
        object_tool_balloon_tail.clear_pending(self)
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active("object_tool", self, context)
        self.report({"INFO"}, "オブジェクトツール: クリックで選択、ドラッグで移動/リサイズ")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("object_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        # ▲ ハンドル hover ハイライト用にカーソル位置を WM に記録
        # (overlay_coma_selection.draw が読む)
        if event.type == "MOUSEMOVE":
            self._update_overlay_pointer(context, event)
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if view_event_region.modal_navigation_ui_passthrough(self, context, event):
            return {"PASS_THROUGH"}
        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if object_tool_balloon_tail.open_point_menu(context, event):
                return {"RUNNING_MODAL"}
            if selection_context_menu.open_for_object_tool(self, context, event):
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.value == "PRESS" and event.type in {"P", "F", "K", "T"} and not event.ctrl and not event.alt:
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.value == "PRESS" and event.type in {"Z", "Y"} and event.ctrl:
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if not view_event_region.is_view3d_window_event(context, event):
            return {"PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value not in {"PRESS", "DOUBLE_CLICK"}:
            return {"PASS_THROUGH"}
        return self._handle_left_press(context, event)

    def _handle_left_press(self, context, event):
        mode = _selection_mode(event)
        if mode != "single":
            self._clear_click_state()
        if event.value == "DOUBLE_CLICK" and mode == "single":
            hit = self._hit_object(context, event)
            coma_hit = self._coma_open_hit_from_hit(hit)
            if coma_hit is not None and self._try_enter_coma_from_hit(context, coma_hit):
                return {"FINISHED"}
        if (
            event.value == "PRESS"
            and mode == "single"
            and coma_edge_move_op.extend_selected_handle_at_event(context, event)
        ):
            self._clear_click_state()
            return {"RUNNING_MODAL"}
        # Alt+ドラッグ: 選択レイヤーを別コマ/ページへ移動 (Ctrl 併用はブラシ
        # サイズ用なので除外)。オブジェクトツールが常駐モーダルとして左
        # クリックを専有するため、ここで Alt を解釈する必要がある。
        if (
            event.value == "PRESS"
            and bool(getattr(event, "alt", False))
            and not bool(getattr(event, "ctrl", False))
            and _reparent_has_targets(context)
        ):
            self._clear_click_state()
            if bool(getattr(event, "shift", False)):
                self._do_reparent_out(context, event)
                return {"RUNNING_MODAL"}
            if self._start_reparent_drag(context, event):
                return {"RUNNING_MODAL"}
        if event.value == "PRESS" and bool(getattr(event, "ctrl", False)):
            if object_tool_balloon_tail.handle_ctrl_press(self, context, event):
                self._clear_click_state()
                return {"RUNNING_MODAL"}
        if event.value == "PRESS" and not bool(getattr(event, "ctrl", False)):
            object_tool_balloon_tail.clear_pending(self)
        hit = self._hit_object(context, event)
        if event.value == "PRESS" and mode == "single":
            coma_hit = self._coma_open_hit_from_hit(hit)
            if self._is_manual_coma_double_click(event, coma_hit):
                self._clear_click_state()
                if self._try_enter_coma_from_hit(context, coma_hit):
                    return {"FINISHED"}
            self._remember_coma_click(event, coma_hit)
        if hit is None:
            if mode == "single" and self._try_start_layer_drag(context, event):
                return {"RUNNING_MODAL"}
            if self._start_marquee_select(context, event, mode):
                return {"RUNNING_MODAL"}
            if mode == "single":
                object_selection.clear(context)
                object_tool_selection.sync_outliner_selection_for_keys(context, [])
                edge_selection.clear_selection(context)
                self._clear_click_state()
            return {"RUNNING_MODAL"}
        if event.value == "DOUBLE_CLICK" and mode == "single" and self._try_enter_coma_from_hit(context, hit):
            return {"FINISHED"}
        self._activate_hit(context, hit, mode=mode)
        if mode in {"toggle", "add"}:
            return {"RUNNING_MODAL"}
        x_mm, y_mm = self._start_point_for_hit(context, event, hit)
        if x_mm is None or y_mm is None:
            return {"RUNNING_MODAL"}
        if hit["kind"] in {"coma_edge", "coma_vertex"}:
            self._start_coma_edge_drag(context, hit, event, x_mm, y_mm)
        else:
            self._start_object_drag(context, hit, x_mm, y_mm)
        return {"RUNNING_MODAL"}

    def _is_manual_coma_double_click(self, event, hit: dict | None) -> bool:
        if hit is None:
            return False
        key = str(hit.get("key", "") or "")
        if not key or key != str(getattr(self, "_last_click_key", "") or ""):
            return False
        now = time.time()
        if now - float(getattr(self, "_last_click_time", 0.0) or 0.0) > _DOUBLE_CLICK_INTERVAL_SEC:
            return False
        last_x, last_y = getattr(self, "_last_click_xy", (-1.0e9, -1.0e9))
        dx = float(getattr(event, "mouse_x", 0.0)) - float(last_x)
        dy = float(getattr(event, "mouse_y", 0.0)) - float(last_y)
        return (dx * dx + dy * dy) ** 0.5 <= _DOUBLE_CLICK_DISTANCE_PX

    def _remember_coma_click(self, event, hit: dict | None) -> None:
        if hit is None:
            self._clear_click_state()
            return
        self._last_click_time = time.time()
        self._last_click_xy = (
            float(getattr(event, "mouse_x", 0.0)),
            float(getattr(event, "mouse_y", 0.0)),
        )
        self._last_click_key = str(hit.get("key", "") or "")

    def _clear_click_state(self) -> None:
        self._last_click_time = 0.0
        self._last_click_xy = (-1.0e9, -1.0e9)
        self._last_click_key = ""

    def _coma_open_hit_from_hit(self, hit: dict | None) -> dict | None:
        if hit is None or str(hit.get("kind", "") or "") not in {"coma", "coma_edge", "coma_vertex"}:
            return None
        page_index = int(hit.get("page", -1))
        coma_index = int(hit.get("coma", -1))
        if page_index < 0 or coma_index < 0:
            return None
        return {
            "kind": "coma",
            "page": page_index,
            "coma": coma_index,
            "part": "body",
            "key": str(hit.get("key", "") or ""),
        }

    def _try_enter_coma_from_hit(self, context, hit: dict) -> bool:
        return enter_coma_from_hit(context, hit)

    def _hit_object(self, context, event) -> dict | None:
        return hit_object_at_event(context, event)

    def _hit_text(self, context, event) -> dict | None:
        return _hit_text_at_event(context, event)

    def _hit_balloon(self, context, event) -> dict | None:
        return _hit_balloon_at_event(context, event)

    def _hit_effect(self, context, event) -> dict | None:
        return _hit_effect_at_event(context, event)

    def _hit_image(self, context, event) -> dict | None:
        return _hit_image_at_event(context, event)

    def _hit_gp(self, context, event) -> dict | None:
        return _hit_gp_at_event(context, event)

    def _hit_raster(self, context, event) -> dict | None:
        return _hit_raster_at_event(context, event)

    def _activate_hit(self, context, hit: dict, *, mode: str) -> None:
        activate_hit(context, hit, mode=mode)

    def _start_point_for_hit(self, context, event, hit: dict) -> tuple[float | None, float | None]:
        if "world" in hit:
            return hit["world"]
        if hit["kind"] in {"coma", "coma_edge", "coma_vertex"}:
            view = view_event_region.view3d_window_under_event(context, event)
            if view is None:
                return None, None
            _area, region, rv3d, mx, my = view
            return coma_edge_move_op._region_to_world_mm(region, rv3d, mx, my)
        return _event_world_xy_mm(context, event)

    def _start_coma_edge_drag(self, context, hit: dict, event, x_mm: float, y_mm: float) -> None:
        selection = {
            "type": "vertex" if hit["kind"] == "coma_vertex" else "edge",
            "page": int(hit["page"]),
            "coma": int(hit["coma"]),
        }
        if selection["type"] == "vertex":
            selection["vertex"] = int(hit.get("vertex", -1))
            selected_vertices = edge_selection.selected_vertices(
                context,
                page_index=selection["page"],
                coma_index=selection["coma"],
            )
            if selection["vertex"] in selected_vertices and len(selected_vertices) > 1:
                selection["vertices"] = sorted(selected_vertices)
        else:
            selection["edge"] = int(hit.get("edge", -1))
        view = view_event_region.view3d_window_under_event(context, event)
        if view is None:
            return
        area, region, rv3d, _mx, _my = view
        self._edge_drag = coma_edge_drag_session.ComaEdgeDragSession(
            context,
            get_work(context),
            area,
            region,
            rv3d,
            selection,
            (float(x_mm), float(y_mm)),
        )
        self._dragging = True
        self._drag_action = "coma_edge"
        self._drag_moved = False

    def _try_start_layer_drag(self, context, event) -> bool:
        scene = getattr(context, "scene", None)
        if scene is None or getattr(scene, "bname_active_layer_kind", "") not in {"gp", "image"}:
            return False
        item = layer_stack_utils.active_stack_item(context)
        if item is None or getattr(item, "kind", "") not in {"gp", "image"}:
            return False
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return False
        session = layer_move_session.LayerMoveDragSession(context, (float(x_mm), float(y_mm)))
        if not session.started:
            return False
        self._layer_drag = session
        self._dragging = True
        self._drag_action = "layer_move"
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        return True

    # ---------- Alt+ドラッグ: 別コマ/ページへ移動 (reparent) ----------

    def _start_reparent_drag(self, context, event) -> bool:
        target = layer_reparent.find_target_for_drop(context, event)
        self._dragging = True
        self._drag_action = "reparent"
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        self._reparent_start_px = (float(event.mouse_x), float(event.mouse_y))
        self._reparent_target = target
        _reparent_set_overlay(target)
        if target.world_xy_mm is not None:
            reparent_overlay.set_preview(
                world_xy_mm=target.world_xy_mm,
                count=_reparent_count(context),
            )
        return True

    def _do_reparent_out(self, context, event) -> None:
        click_target = layer_reparent.find_click_target(context, event)
        scene = context.scene
        stack = getattr(scene, "bname_layer_stack", None)
        if stack is None:
            return
        active_idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
        candidates = []
        if 0 <= active_idx < len(stack):
            candidates.append(stack[active_idx])
        for item in stack:
            if layer_stack_utils.is_item_selected(context, item) and not any(
                layer_stack_utils.stack_item_uid(c) == layer_stack_utils.stack_item_uid(item)
                for c in candidates
            ):
                candidates.append(item)
        target = None
        for item in candidates:
            t = layer_reparent.shallower_target_for_item(context, item, click_target)
            if t is not None:
                target = t
                break
        if target is None:
            reparent_overlay.flash_error("page", duration=0.3)
            return
        changed = layer_reparent.reparent_selected(context, target)
        if changed > 0:
            _reparent_set_confirm(target)
            try:
                bpy.ops.ed.undo_push(message="B-Name: Alt+Shift で外へ移動")
            except Exception:  # noqa: BLE001
                pass
            layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        else:
            _reparent_set_error(target)

    def _finish_reparent(self, context) -> None:
        target = getattr(self, "_reparent_target", None)
        reparent_overlay.clear_hover()
        reparent_overlay.clear_preview()
        if target is None:
            return
        moved = bool(getattr(self, "_drag_moved", False))
        new_xy = target.world_xy_mm if moved else None
        changed = layer_reparent.reparent_selected(
            context, target, new_world_xy_mm=new_xy
        )
        if changed > 0:
            _reparent_set_confirm(target)
            try:
                bpy.ops.ed.undo_push(message="B-Name: Alt+ドラッグで移動")
            except Exception:  # noqa: BLE001
                pass
            layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        else:
            _reparent_set_error(target)

    def _start_object_drag(self, context, hit: dict, x_mm: float, y_mm: float) -> None:
        action = str(hit.get("part", "move") or "move")
        key = str(hit.get("key", "") or "")
        selected = object_selection.get_keys(context)
        if action == "move" and key in selected:
            keys = selected
        else:
            keys = [key]
        self._dragging = True
        self._drag_action = action
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_keys = keys
        self._snapshots = self._make_snapshots(context, keys, primary_key=key, action=action)
        self._drag_moved = False

    def _start_marquee_select(self, context, event, mode: str) -> bool:
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return False
        self._dragging = True
        self._drag_action = "marquee"
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._marquee_start_x = float(x_mm)
        self._marquee_start_y = float(y_mm)
        self._marquee_current_x = float(x_mm)
        self._marquee_current_y = float(y_mm)
        self._marquee_mode = str(mode or "single")
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        return True

    def _make_snapshots(self, context, keys: list[str], *, primary_key: str, action: str) -> list[dict]:
        work = get_work(context)
        snapshots: list[dict] = []
        for key in keys:
            kind, page_id, item_id = object_selection.parse_key(key)
            if action != "move" and key != primary_key:
                continue
            if kind == "coma":
                page_index, page, coma_index, panel = _find_coma_by_key(work, page_id, item_id)
                if panel is None:
                    continue
                poly = coma_edge_move_op._coma_polygon(panel)
                gp_key = layer_stack_utils.gp_parent_key_for_coma(page, panel)
                snapshots.append({
                    "kind": "coma",
                    "page_index": page_index,
                    "page_id": page_id,
                    "coma_id": item_id,
                    "shape": getattr(panel, "shape_type", ""),
                    "rect": (
                        float(getattr(panel, "rect_x_mm", 0.0)),
                        float(getattr(panel, "rect_y_mm", 0.0)),
                        float(getattr(panel, "rect_width_mm", 0.0)),
                        float(getattr(panel, "rect_height_mm", 0.0)),
                    ),
                    "poly": poly,
                    "children": self._panel_child_snapshots(page, panel),
                    "gp": layer_stack_utils.capture_gp_layers_for_parent_keys(context, {gp_key}),
                    "effect_gp": layer_stack_utils.capture_effect_layers_for_parent_keys(context, {gp_key}),
                    "raster": layer_stack_utils.capture_raster_layers_for_parent_keys(context, {gp_key}),
                    "gp_key": gp_key,
                })
            elif kind == "balloon":
                _page_index, page, _idx, entry = _find_balloon_by_key(work, page_id, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "balloon",
                    "page_id": page_id,
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                    "center_offset": (
                        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
                        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
                    ),
                })
            elif kind == "text":
                _page_index, _page, _idx, entry = _find_text_by_key(work, page_id, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "text",
                    "page_id": page_id,
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                })
            elif kind == "effect":
                obj, layer = _find_effect_layer(item_id)
                bounds = effect_line_op.effect_layer_bounds(obj, layer)
                if layer is None or bounds is None:
                    continue
                center = effect_line_op.effect_layer_center(obj, layer, bounds) or (
                    float(bounds[0]) + float(bounds[2]) * 0.5,
                    float(bounds[1]) + float(bounds[3]) * 0.5,
                )
                snapshots.append({
                    "kind": "effect",
                    "item_id": item_id,
                    "rect": (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])),
                    "center": (float(center[0]), float(center[1])),
                })
            elif kind == "image":
                _idx, entry = _find_image_by_key(context, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "image",
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                })
            elif kind == "raster":
                _idx, entry = _find_raster_by_key(context, item_id)
                if entry is None:
                    continue
                image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
                if image is None:
                    continue
                try:
                    pixels = image.pixels[:]
                except Exception:  # noqa: BLE001
                    pixels = ()
                snapshots.append({
                    "kind": "raster",
                    "item_id": item_id,
                    "image_name": str(getattr(image, "name", "") or ""),
                    "pixels": tuple(pixels),
                })
            elif kind == "gp":
                obj, layer = _find_gp_layer(item_id)
                bounds = object_tool_selection.gp_layer_local_bounds(layer)
                if layer is None or bounds is None:
                    continue
                snapshots.append({
                    "kind": "gp",
                    "item_id": item_id,
                    "rect": (bounds.x, bounds.y, bounds.width, bounds.height),
                    "points": gp_parent.capture_layers([layer]),
                })
        return snapshots

    def _panel_child_snapshots(self, page, panel) -> list[tuple[str, str, float, float]]:
        balloons, texts = layer_move_op._panel_children(page, panel)
        snapshots = []
        for balloon in balloons:
            snapshots.append(("balloon", getattr(balloon, "id", ""), float(balloon.x_mm), float(balloon.y_mm)))
        for text in texts:
            snapshots.append(("text", getattr(text, "id", ""), float(text.x_mm), float(text.y_mm)))
        return snapshots

    def _modal_dragging(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update_overlay_pointer(context, event)
            self._update_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._update_drag(context, event)
            self._finish_drag(context)
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cancel_drag(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _update_overlay_pointer(self, context, event) -> None:
        try:
            view = view_event_region.view3d_window_under_event(context, event)
        except Exception:  # noqa: BLE001
            view = None
        if view is None:
            edge_selection.update_overlay_pointer(context, None, event)
            return
        _area, region, _rv3d, _mx, _my = view
        edge_selection.update_overlay_pointer(context, region, event)
        try:
            region.tag_redraw()
        except Exception:  # noqa: BLE001
            pass

    def _update_drag(self, context, event) -> None:
        if self._drag_action == "marquee":
            x_mm, y_mm = _event_world_xy_mm(context, event)
            if x_mm is None or y_mm is None:
                return
            self._marquee_current_x = float(x_mm)
            self._marquee_current_y = float(y_mm)
            dx = float(x_mm) - self._marquee_start_x
            dy = float(y_mm) - self._marquee_start_y
            if abs(dx) > _DRAG_EPS_MM or abs(dy) > _DRAG_EPS_MM:
                self._drag_moved = True
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "coma_edge":
            if self._edge_drag is not None and self._edge_drag.apply(event):
                self._drag_moved = True
            return
        if self._drag_action == "layer_move":
            if self._layer_drag is not None and self._layer_drag.apply(context, event):
                self._drag_moved = True
            return
        if self._drag_action == "reparent":
            target = layer_reparent.find_target_for_drop(context, event)
            self._reparent_target = target
            _reparent_set_overlay(target)
            if target.world_xy_mm is not None:
                reparent_overlay.set_preview(
                    world_xy_mm=target.world_xy_mm,
                    count=_reparent_count(context),
                )
            sx, sy = getattr(self, "_reparent_start_px", (0.0, 0.0))
            if (
                abs(float(event.mouse_x) - sx) >= _REPARENT_DRAG_PX
                or abs(float(event.mouse_y) - sy) >= _REPARENT_DRAG_PX
            ):
                self._drag_moved = True
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if object_tool_balloon_tail.update_drag(self, context, event):
            return
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return
        dx = float(x_mm) - self._drag_start_x
        dy = float(y_mm) - self._drag_start_y
        if abs(dx) > _DRAG_EPS_MM or abs(dy) > _DRAG_EPS_MM:
            self._drag_moved = True
        self._apply_snapshots(context, dx, dy)
        layer_stack_utils.tag_view3d_redraw(context)

    def _apply_snapshots(self, context, dx: float, dy: float) -> None:
        work = get_work(context)
        for snapshot in self._snapshots:
            kind = snapshot["kind"]
            x, y, w, h = snapshot.get("rect", (0.0, 0.0, 0.0, 0.0))
            if kind == "coma":
                _page_index, page, _coma_index, panel = _find_coma_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["coma_id"],
                )
                if panel is None or page is None:
                    continue
                if self._drag_action == "move":
                    self._apply_panel_move(context, page, panel, snapshot, dx, dy)
                elif getattr(panel, "shape_type", "") == "rect":
                    nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                    panel.rect_x_mm = nx
                    panel.rect_y_mm = ny
                    panel.rect_width_mm = nw
                    panel.rect_height_mm = nh
                # NOTE: rect_*_mm 変更は core/coma.py の update callback 経由で
                # coma_plane Mesh が自動追従する。
            elif kind == "balloon":
                _page_index, page, _idx, entry = _find_balloon_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["item_id"],
                )
                if entry is None or page is None:
                    continue
                if self._drag_action == "center":
                    cx, cy = snapshot.get("center_offset", (0.0, 0.0))
                    if hasattr(entry, "center_offset_x_mm"):
                        entry.center_offset_x_mm = float(cx) + dx
                    if hasattr(entry, "center_offset_y_mm"):
                        entry.center_offset_y_mm = float(cy) + dy
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                balloon_op._set_balloon_rect(page, entry, nx, ny, nw, nh)
            elif kind == "text":
                _page_index, _page, _idx, entry = _find_text_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["item_id"],
                )
                if entry is None:
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                text_op._set_text_rect(entry, nx, ny, nw, nh)
            elif kind == "effect":
                obj, layer = _find_effect_layer(snapshot["item_id"])
                if layer is None:
                    continue
                if self._drag_action == "center":
                    nx, ny, nw, nh = x, y, w, h
                else:
                    nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                cx, cy = snapshot.get("center", (x + w * 0.5, y + h * 0.5))
                if self._drag_action in {"move", "center"}:
                    center = (float(cx) + dx, float(cy) + dy)
                else:
                    center = (
                        float(cx) + (float(nx) + float(nw) * 0.5) - (float(x) + float(w) * 0.5),
                        float(cy) + (float(ny) + float(nh) * 0.5) - (float(y) + float(h) * 0.5),
                    )
                effect_line_op._write_effect_strokes(context, obj, layer, (nx, ny, nw, nh), center_xy_mm=center)
            elif kind == "image":
                _idx, entry = _find_image_by_key(context, snapshot["item_id"])
                if entry is None:
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                entry.x_mm = nx
                entry.y_mm = ny
                entry.width_mm = nw
                entry.height_mm = nh
            elif kind == "raster":
                _idx, entry = _find_raster_by_key(context, snapshot["item_id"])
                if entry is None:
                    continue
                image = bpy.data.images.get(str(snapshot.get("image_name", "") or ""))
                pixels = snapshot.get("pixels", ())
                if image is not None and pixels:
                    try:
                        image.pixels[:] = pixels
                        image.update()
                    except Exception:  # noqa: BLE001
                        pass
                if self._drag_action == "move":
                    raster_layer_op.translate_raster_layer_pixels(context, entry, dx, dy)
            elif kind == "gp":
                _obj, layer = _find_gp_layer(snapshot["item_id"])
                if layer is None:
                    continue
                layer_stack_utils.restore_gp_layer_snapshots(snapshot.get("points", []))
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 0.5)
                if self._drag_action == "move":
                    gp_parent.translate_layer(layer, dx, dy)
                else:
                    object_tool_selection.scale_gp_layer_from_snapshot(
                        layer,
                        (x, y, w, h),
                        (nx, ny, nw, nh),
                    )

    def _apply_panel_move(self, context, page, panel, snapshot: dict, dx: float, dy: float) -> None:
        if snapshot["shape"] == "rect":
            x, y, w, h = snapshot["rect"]
            panel.shape_type = "rect"
            panel.rect_x_mm = x + dx
            panel.rect_y_mm = y + dy
            panel.rect_width_mm = w
            panel.rect_height_mm = h
        else:
            coma_edge_move_op._set_coma_polygon(
                panel,
                [(x + dx, y + dy) for x, y in snapshot["poly"]],
            )
        for child_kind, child_id, x, y in snapshot.get("children", []):
            if child_kind == "balloon":
                idx = balloon_op._find_balloon_index(page, child_id)
                if 0 <= idx < len(page.balloons):
                    balloon_op._move_balloon_with_texts(page, page.balloons[idx], x + dx, y + dy)
            elif child_kind == "text":
                idx = text_op._find_text_index(page, child_id)
                if 0 <= idx < len(page.texts):
                    page.texts[idx].x_mm = x + dx
                    page.texts[idx].y_mm = y + dy
        layer_stack_utils.restore_gp_layer_snapshots(snapshot.get("gp", []))
        layer_stack_utils.translate_gp_layers_for_parent_keys(context, {snapshot["gp_key"]}, dx, dy)
        layer_stack_utils.restore_gp_layer_snapshots(snapshot.get("effect_gp", []))
        layer_stack_utils.translate_effect_layers_for_parent_keys(context, {snapshot["gp_key"]}, dx, dy)
        layer_stack_utils.restore_raster_layer_snapshots(context, snapshot.get("raster", []))
        layer_stack_utils.translate_raster_layers_for_parent_keys(context, {snapshot["gp_key"]}, dx, dy)

    def _finish_drag(self, context) -> None:
        if self._drag_action == "marquee":
            self._finish_marquee_select(context)
            self._clear_click_state()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "reparent":
            self._finish_reparent(context)
            self._clear_click_state()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "balloon_tail_create":
            object_tool_balloon_tail.finish_create_drag(self, context)
            self._clear_click_state()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "balloon_tail_point":
            moved = bool(getattr(self, "_drag_moved", False))
            if moved:
                self._clear_click_state()
                try:
                    bpy.ops.ed.undo_push(message="B-Name: しっぽ制御点移動")
                except Exception:  # noqa: BLE001
                    pass
                layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
            else:
                layer_stack_utils.tag_view3d_redraw(context)
            self._clear_drag_state()
            return
        moved = bool(getattr(self, "_drag_moved", False))
        if moved:
            self._clear_click_state()
        changed = moved
        edge_session = self._drag_action == "coma_edge"
        layer_session = self._drag_action == "layer_move"
        if self._drag_action == "coma_edge" and self._edge_drag is not None:
            changed = bool(self._edge_drag.finish())
        elif self._drag_action == "layer_move" and self._layer_drag is not None:
            changed = bool(self._layer_drag.finish(context))
        if changed:
            if not edge_session and not layer_session:
                try:
                    bpy.ops.ed.undo_push(message="B-Name: オブジェクト編集")
                except Exception:  # noqa: BLE001
                    pass
            if not layer_session:
                layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        elif not layer_session:
            layer_stack_utils.tag_view3d_redraw(context)
        self._clear_drag_state()

    def _finish_marquee_select(self, context) -> None:
        moved = bool(getattr(self, "_drag_moved", False))
        mode = str(getattr(self, "_marquee_mode", "single") or "single")
        if not moved:
            if mode == "single":
                object_selection.clear(context)
                edge_selection.clear_selection(context)
            return
        x0 = float(getattr(self, "_marquee_start_x", 0.0))
        y0 = float(getattr(self, "_marquee_start_y", 0.0))
        x1 = float(getattr(self, "_marquee_current_x", x0))
        y1 = float(getattr(self, "_marquee_current_y", y0))
        rect = Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        object_tool_selection.select_keys_in_world_rect(
            context,
            rect,
            mode=mode,
            activate=lambda ctx, hit, hit_mode: activate_hit(ctx, hit, mode=hit_mode),
        )

    def _cancel_drag(self, context) -> None:
        if self._drag_action == "reparent":
            reparent_overlay.clear_hover()
            reparent_overlay.clear_preview()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "layer_move" and self._layer_drag is not None:
            self._layer_drag.cancel(context)
        elif self._drag_action == "coma_edge" and self._edge_drag is not None:
            self._edge_drag.cancel()
        elif object_tool_balloon_tail.cancel_point_drag(self, context):
            pass
        elif self._drag_action == "balloon_tail_create":
            pass
        elif self._drag_action != "coma_edge":
            self._apply_snapshots(context, 0.0, 0.0)
        self._clear_drag_state()
        layer_stack_utils.tag_view3d_redraw(context)

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        self._edge_drag = None
        self._layer_drag = None
        self._drag_page_id = ""
        self._drag_balloon_id = ""
        self._tail_drag_tail_index = -1
        self._tail_drag_point_index = -1
        self._tail_drag_points = []
        self._last_tail_xy = (0.0, 0.0)
        self._marquee_mode = "single"
        self._marquee_start_x = 0.0
        self._marquee_start_y = 0.0
        self._marquee_current_x = 0.0
        self._marquee_current_y = 0.0
        self._reparent_start_px = (0.0, 0.0)
        self._reparent_target = None

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        if getattr(self, "_drag_action", "") == "coma_edge" and self._edge_drag is not None:
            self._edge_drag.cancel()
        elif getattr(self, "_drag_action", "") == "layer_move" and self._layer_drag is not None:
            self._layer_drag.cancel(context)
        elif getattr(self, "_drag_action", "") == "reparent":
            reparent_overlay.clear_hover()
            reparent_overlay.clear_preview()
        self._clear_drag_state()
        object_tool_balloon_tail.clear_pending(self)

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        if not keep_selection:
            object_selection.clear(context)
        self._cleanup(context)
        edge_selection.clear_overlay_pointer(context)
        coma_modal_state.clear_active("object_tool", self, context)


_CLASSES = (BNAME_OT_object_tool,)


def register() -> None:
    bpy.types.WindowManager.bname_object_selection_keys = StringProperty(default="")
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.WindowManager.bname_object_selection_keys
    except AttributeError:
        pass
