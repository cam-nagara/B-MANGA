"""グラデーションツール: ドラッグ方向と範囲でグラデーションを作成."""

from __future__ import annotations

import math

import bpy
import gpu
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader

from ..core.work import get_active_page, get_work
from ..utils import geom, layer_stack as layer_stack_utils, log, page_file_scene
from . import coma_modal_state, selection_context_menu, view_event_region

_logger = log.get_logger(__name__)

TOOL_NAME = "gradient_tool"
_COLOR_LINE = (0.9, 0.4, 0.1, 0.9)
_COLOR_START = (0.1, 0.5, 0.9, 0.9)
_COLOR_END = (0.9, 0.2, 0.2, 0.9)
_CIRCLE_SEGMENTS = 16
_CIRCLE_RADIUS_PX = 6.0
_MIN_DRAG_PX = 8.0


def _world_mm_from_event(context, event):
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None
    area, region, rv3d, mx, my = view
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None
    return area, region, rv3d, mx, my, geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _circle_verts(cx, cy, r, segments=_CIRCLE_SEGMENTS):
    verts = []
    for i in range(segments):
        a0 = 2.0 * math.pi * i / segments
        a1 = 2.0 * math.pi * (i + 1) / segments
        verts.append((cx + r * math.cos(a0), cy + r * math.sin(a0)))
        verts.append((cx + r * math.cos(a1), cy + r * math.sin(a1)))
    return verts


def _draw_callback(op: "BMANGA_OT_gradient_tool") -> None:
    if op._press_px is None or op._cursor_px is None:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(2.5)
    except Exception:
        pass
    try:
        sx, sy = op._press_px
        ex, ey = op._cursor_px
        shader.uniform_float("color", _COLOR_LINE)
        batch_for_shader(shader, "LINES", {"pos": [(sx, sy), (ex, ey)]}).draw(shader)
        gpu.state.line_width_set(1.5)
        shader.uniform_float("color", _COLOR_START)
        batch_for_shader(
            shader, "LINES", {"pos": _circle_verts(sx, sy, _CIRCLE_RADIUS_PX)},
        ).draw(shader)
        shader.uniform_float("color", _COLOR_END)
        batch_for_shader(
            shader, "LINES", {"pos": _circle_verts(ex, ey, _CIRCLE_RADIUS_PX)},
        ).draw(shader)
    finally:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
        except Exception:
            pass


class BMANGA_OT_gradient_tool(Operator):
    """グラデーションツール: ドラッグ方向と範囲でグラデーションレイヤーを作成"""

    bl_idname = "bmanga.gradient_tool"
    bl_label = "グラデーション"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and work.loaded
            and get_active_page(context) is not None
            and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
        )

    def invoke(self, context, _event):
        if coma_modal_state.get_active(TOOL_NAME) is not None:
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        coma_modal_state.finish_all(context, except_tool=TOOL_NAME)
        self._press_px = None
        self._cursor_px = None
        self._press_world_mm = None
        self._page_id = ""
        self._page_ox = 0.0
        self._page_oy = 0.0
        self._coma_key = ""
        self._region = None
        self._rv3d = None
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active(TOOL_NAME, self, context)
        self.report({"INFO"}, "グラデーション: ドラッグで方向と範囲を指定 | ESC/右クリックで終了")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        coma_modal_state.mark_heartbeat(self)
        if getattr(self, "_externally_finished", False):
            self._cleanup(context)
            return {"FINISHED"}

        from . import handle_intercept
        if handle_intercept.is_dragging(self):
            if event.type == "MOUSEMOVE":
                handle_intercept.update_drag(context, event, self)
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                handle_intercept.finish_drag(context, event, self)
                return {"RUNNING_MODAL"}
            if event.type == "ESC" and event.value == "PRESS":
                handle_intercept.cancel_drag(context, self)
                return {"RUNNING_MODAL"}
            return {"RUNNING_MODAL"}

        ev_type = str(getattr(event, "type", "") or "")
        ev_value = str(getattr(event, "value", "") or "")

        if view_event_region.modal_navigation_ui_passthrough(self, context, event):
            return {"PASS_THROUGH"}

        if ev_type == "RIGHTMOUSE" and ev_value == "PRESS":
            if selection_context_menu.open_for_viewport_object(context, event):
                return {"RUNNING_MODAL"}
            self._cleanup(context)
            coma_modal_state.clear_active(TOOL_NAME, self, context)
            return {"FINISHED"}

        if ev_type == "ESC" and ev_value == "PRESS":
            self._cleanup(context)
            coma_modal_state.clear_active(TOOL_NAME, self, context)
            return {"FINISHED"}

        coma_modal_state.sync_modal_cursor_for_event_region(context, event, self, "CROSSHAIR")
        if not view_event_region.is_view3d_window_event(context, event):
            return {"PASS_THROUGH"}

        if (
            ev_type == "LEFTMOUSE"
            and ev_value == "PRESS"
            and handle_intercept.try_intercept_press(context, event, self)
        ):
            return {"RUNNING_MODAL"}

        if ev_type == "LEFTMOUSE" and ev_value == "PRESS":
            return self._on_press(context, event)
        if ev_type == "MOUSEMOVE" and self._press_px is not None:
            return self._on_move(context, event)
        if ev_type == "LEFTMOUSE" and ev_value == "RELEASE" and self._press_px is not None:
            return self._on_release(context, event)

        return {"PASS_THROUGH"}

    def _on_press(self, context, event):
        result = _world_mm_from_event(context, event)
        if result is None:
            return {"RUNNING_MODAL"}
        area, region, rv3d, mx, my, wx, wy = result
        if not self._lock_page(context, wx, wy):
            return {"RUNNING_MODAL"}
        self._press_px = (mx, my)
        self._cursor_px = (mx, my)
        self._press_world_mm = (wx, wy)
        self._region = region
        self._rv3d = rv3d
        return {"RUNNING_MODAL"}

    def _on_move(self, context, event):
        view = view_event_region.view3d_window_under_event(context, event)
        if view is not None:
            self._cursor_px = (view[3], view[4])
            if self._region is not None:
                self._region.tag_redraw()
        return {"RUNNING_MODAL"}

    def _on_release(self, context, event):
        result = _world_mm_from_event(context, event)
        press_px = self._press_px
        self._press_px = None
        self._cursor_px = None
        if self._region is not None:
            self._region.tag_redraw()

        if result is None or press_px is None:
            return {"RUNNING_MODAL"}
        _, _, _, mx, my, wx, wy = result
        dx_px = mx - press_px[0]
        dy_px = my - press_px[1]
        if math.sqrt(dx_px * dx_px + dy_px * dy_px) < _MIN_DRAG_PX:
            return {"RUNNING_MODAL"}

        sx, sy = self._press_world_mm
        start_x = sx - self._page_ox
        start_y = sy - self._page_oy
        end_x = wx - self._page_ox
        end_y = wy - self._page_oy

        self._create_gradient(context, start_x, start_y, end_x, end_y)
        bpy.ops.ed.undo_push(message="グラデーション")
        return {"RUNNING_MODAL"}

    def _lock_page(self, context, world_x_mm, world_y_mm) -> bool:
        from ..utils import page_grid
        from ..utils.layer_hierarchy import coma_containing_point, coma_stack_key

        work = get_work(context)
        scene = context.scene
        if work is None or not work.loaded:
            return False
        page_idx = page_grid.page_index_at_world_mm(work, scene, world_x_mm, world_y_mm)
        if page_idx is None or not (0 <= page_idx < len(work.pages)):
            return False
        page = work.pages[page_idx]
        ox, oy = page_grid.page_total_offset_mm(work, scene, page_idx)
        self._page_id = str(getattr(page, "id", ""))
        self._page_ox = ox
        self._page_oy = oy
        local_x = world_x_mm - ox
        local_y = world_y_mm - oy
        coma = coma_containing_point(page, local_x, local_y)
        if coma is not None:
            self._coma_key = coma_stack_key(page, coma)
        else:
            self._coma_key = ""
        return True

    def _create_gradient(self, context, sx, sy, ex, ey) -> None:
        coll = getattr(context.scene, "bmanga_fill_layers", None)
        if coll is None:
            return
        used = {entry.id for entry in coll}
        i = 1
        while f"fill_{i:04d}" in used:
            i += 1
        entry = coll.add()
        entry.id = f"fill_{i:04d}"
        entry.fill_type = "gradient"
        entry.gradient_type = "linear"
        entry.title = f"グラデーション {i}"
        entry.use_gradient_endpoints = True
        entry.gradient_start_x_mm = sx
        entry.gradient_start_y_mm = sy
        entry.gradient_end_x_mm = ex
        entry.gradient_end_y_mm = ey
        dx = ex - sx
        dy = ey - sy
        entry.gradient_angle = math.atan2(dy, dx)
        if self._coma_key:
            entry.parent_kind = "coma"
            entry.parent_key = self._coma_key
        elif self._page_id:
            entry.parent_kind = "page"
            entry.parent_key = self._page_id
        try:
            from . import preset_op
            preset_op.apply_gradient_preset_to_entry(context, entry)
        except Exception:  # noqa: BLE001
            pass
        context.scene.bmanga_active_fill_layer_index = len(coll) - 1
        context.scene.bmanga_active_layer_kind = "fill"
        try:
            from ..utils import fill_real_object as _fro

            work = get_work(context)
            page = _fro.page_for_entry(context.scene, work, entry)
            _fro.ensure_fill_real_object(scene=context.scene, entry=entry, page=page)
            from ..utils import layer_object_sync as _los
            _los.assign_per_page_z_ranks(context.scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("gradient real object creation failed")
        layer_stack_utils.sync_layer_stack_after_data_change(context)

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        h = getattr(self, "_draw_handler", None)
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
            except Exception:
                pass
            self._draw_handler = None
        region = getattr(self, "_region", None)
        if region is not None:
            try:
                region.tag_redraw()
            except Exception:
                pass

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        coma_modal_state.clear_active(TOOL_NAME, self, context)


_CLASSES = (BMANGA_OT_gradient_tool,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
