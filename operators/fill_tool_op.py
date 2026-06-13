"""囲い塗りツール: 投げ縄ドラッグでフリーハンド範囲を指定."""

from __future__ import annotations

import json
import math

import bpy
import gpu
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader

from ..core.work import get_active_page, get_work
from ..utils import geom, layer_stack as layer_stack_utils, log, page_file_scene
from . import coma_modal_state, view_event_region

_logger = log.get_logger(__name__)

TOOL_NAME = "fill_tool"
_MIN_POINT_DIST_PX = 2.0
_MIN_POINTS = 3
_SIMPLIFY_TOLERANCE_MM = 0.3
_SMOOTH_ITERATIONS = 3
_COLOR_OUTLINE = (0.2, 0.7, 0.3, 0.9)
_COLOR_FILL = (0.2, 0.7, 0.3, 0.12)


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


def _chaikin_smooth(points: list, iterations: int = 2) -> list:
    """Chaikin corner-cutting for closed polygon smoothing."""
    pts = list(points)
    for _ in range(iterations):
        if len(pts) < 3:
            break
        new_pts: list[tuple[float, float]] = []
        n = len(pts)
        for i in range(n):
            p0 = pts[i]
            p1 = pts[(i + 1) % n]
            new_pts.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
            new_pts.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
        pts = new_pts
    return pts


def _simplify_dp(points: list, tol: float) -> list:
    """Douglas-Peucker simplification."""
    if len(points) <= 2:
        return list(points)
    sx, sy = points[0]
    ex, ey = points[-1]
    dx, dy = ex - sx, ey - sy
    len_sq = dx * dx + dy * dy
    max_d = 0.0
    max_i = 0
    for i in range(1, len(points) - 1):
        px, py = points[i][0] - sx, points[i][1] - sy
        if len_sq < 1e-10:
            d = math.sqrt(px * px + py * py)
        else:
            t = max(0.0, min(1.0, (px * dx + py * dy) / len_sq))
            d = math.sqrt((px - t * dx) ** 2 + (py - t * dy) ** 2)
        if d > max_d:
            max_d = d
            max_i = i
    if max_d > tol:
        left = _simplify_dp(points[: max_i + 1], tol)
        right = _simplify_dp(points[max_i:], tol)
        return left[:-1] + right
    return [points[0], points[-1]]


def _draw_callback(op: "BNAME_OT_fill_tool") -> None:
    pts = op._points_px
    if not pts or len(pts) < 2:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    try:
        gpu.state.blend_set("ALPHA")
    except Exception:
        pass
    try:
        if len(pts) >= 3:
            tris = op._tri_cache
            if tris:
                shader.uniform_float("color", _COLOR_FILL)
                tri_verts = []
                for i, j, k in tris:
                    if i < len(pts) and j < len(pts) and k < len(pts):
                        tri_verts.extend([pts[i], pts[j], pts[k]])
                if tri_verts:
                    batch_for_shader(shader, "TRIS", {"pos": tri_verts}).draw(shader)

        try:
            gpu.state.line_width_set(2.0)
        except Exception:
            pass
        shader.uniform_float("color", _COLOR_OUTLINE)
        line_verts = []
        for i in range(len(pts) - 1):
            line_verts.append(pts[i])
            line_verts.append(pts[i + 1])
        line_verts.append(pts[-1])
        line_verts.append(pts[0])
        batch_for_shader(shader, "LINES", {"pos": line_verts}).draw(shader)
    finally:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
        except Exception:
            pass


class BNAME_OT_fill_tool(Operator):
    """囲い塗りツール: 投げ縄ドラッグでフリーハンド範囲のベタ塗りレイヤーを作成"""

    bl_idname = "bname.fill_tool"
    bl_label = "囲い塗り"
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
        self._points_px: list[tuple[float, float]] = []
        self._points_mm: list[tuple[float, float]] = []
        self._tri_cache: list[tuple[int, int, int]] = []
        self._page_id = ""
        self._page_ox = 0.0
        self._page_oy = 0.0
        self._coma_key = ""
        self._region = None
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active(TOOL_NAME, self, context)
        self.report({"INFO"}, "囲い塗り: ドラッグで範囲を囲む | ESC/右クリックで終了")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        coma_modal_state.mark_heartbeat(self)
        if getattr(self, "_externally_finished", False):
            self._cleanup(context)
            return {"FINISHED"}

        ev_type = str(getattr(event, "type", "") or "")
        ev_value = str(getattr(event, "value", "") or "")

        if ev_type in {"RIGHTMOUSE", "ESC"} and ev_value == "PRESS":
            self._cleanup(context)
            coma_modal_state.clear_active(TOOL_NAME, self, context)
            return {"FINISHED"}

        if not view_event_region.is_view3d_window_event(context, event):
            return {"PASS_THROUGH"}

        if ev_type == "LEFTMOUSE" and ev_value == "PRESS":
            return self._on_press(context, event)
        if ev_type == "MOUSEMOVE" and self._points_px:
            return self._on_move(context, event)
        if ev_type == "LEFTMOUSE" and ev_value == "RELEASE" and self._points_px:
            return self._on_release(context, event)

        return {"PASS_THROUGH"}

    def _on_press(self, context, event):
        result = _world_mm_from_event(context, event)
        if result is None:
            return {"RUNNING_MODAL"}
        area, region, rv3d, mx, my, wx, wy = result
        if not self._lock_page(context, wx, wy):
            return {"RUNNING_MODAL"}
        local_x = wx - self._page_ox
        local_y = wy - self._page_oy
        self._points_px = [(mx, my)]
        self._points_mm = [(local_x, local_y)]
        self._tri_cache = []
        self._region = region
        return {"RUNNING_MODAL"}

    def _on_move(self, context, event):
        view = view_event_region.view3d_window_under_event(context, event)
        if view is None:
            return {"RUNNING_MODAL"}
        _, _, _, mx, my = view
        last = self._points_px[-1]
        dx = mx - last[0]
        dy = my - last[1]
        if dx * dx + dy * dy < _MIN_POINT_DIST_PX * _MIN_POINT_DIST_PX:
            return {"RUNNING_MODAL"}

        result = _world_mm_from_event(context, event)
        if result is None:
            return {"RUNNING_MODAL"}
        _, _, _, _, _, wx, wy = result
        self._points_px.append((mx, my))
        self._points_mm.append((wx - self._page_ox, wy - self._page_oy))
        self._update_tri_cache()
        if self._region is not None:
            self._region.tag_redraw()
        return {"RUNNING_MODAL"}

    def _on_release(self, context, event):
        pts_mm = list(self._points_mm)
        self._points_px = []
        self._points_mm = []
        self._tri_cache = []
        if self._region is not None:
            self._region.tag_redraw()

        if len(pts_mm) < _MIN_POINTS:
            return {"RUNNING_MODAL"}

        simplified = _simplify_dp(pts_mm, _SIMPLIFY_TOLERANCE_MM)
        if len(simplified) < _MIN_POINTS:
            return {"RUNNING_MODAL"}

        smoothed = _chaikin_smooth(simplified, _SMOOTH_ITERATIONS)
        self._create_lasso_fill(context, smoothed)
        bpy.ops.ed.undo_push(message="囲い塗り")
        return {"RUNNING_MODAL"}

    def _update_tri_cache(self) -> None:
        pts = self._points_px
        if len(pts) < 3:
            self._tri_cache = []
            return
        try:
            from mathutils.geometry import tessellate_polygon

            pts_3d = [(x, y, 0.0) for x, y in pts]
            self._tri_cache = tessellate_polygon([pts_3d])
        except Exception:
            self._tri_cache = []

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

    def _create_lasso_fill(self, context, points_mm: list) -> None:
        coll = getattr(context.scene, "bname_fill_layers", None)
        if coll is None:
            return

        xs = [p[0] for p in points_mm]
        ys = [p[1] for p in points_mm]
        rx = min(xs)
        ry = min(ys)
        rw = max(xs) - rx
        rh = max(ys) - ry
        if rw < 0.5 or rh < 0.5:
            return

        used = {entry.id for entry in coll}
        i = 1
        while f"fill_{i:04d}" in used:
            i += 1
        entry = coll.add()
        entry.id = f"fill_{i:04d}"
        entry.fill_type = "solid"
        entry.title = f"ベタ塗り {i}"
        entry.use_region = True
        entry.region_x_mm = rx
        entry.region_y_mm = ry
        entry.region_width_mm = rw
        entry.region_height_mm = rh
        entry.lasso_points_json = json.dumps(points_mm)
        if self._coma_key:
            entry.parent_kind = "coma"
            entry.parent_key = self._coma_key
        elif self._page_id:
            entry.parent_kind = "page"
            entry.parent_key = self._page_id
        context.scene.bname_active_fill_layer_index = len(coll) - 1
        context.scene.bname_active_layer_kind = "fill"
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


_CLASSES = (BNAME_OT_fill_tool,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
