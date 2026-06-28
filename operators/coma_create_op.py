"""コマ作成ツール (矩形ドラッグ / 折れ線クリック 自動判別).

- 最初の押下でドラッグ開始地点のページをロックし、そのページの
  ``page.comas`` にコマを作成する。
- 押下からドラッグして離す → 矩形コマ。
- クリックを繰り返す → 折れ線コマ。始点付近を再クリックで閉じて確定。
- 選択中の枠線プリセットを作成したコマへ適用する。
- ツールは ESC / 右クリックで明示終了するまで継続する (§8.1)。
"""

from __future__ import annotations

from pathlib import Path

import bpy
import gpu
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader

from ..core.work import get_active_page, get_work
from ..io import coma_io, page_io
from ..utils import geom, layer_stack as layer_stack_utils, log, page_file_scene
from . import coma_modal_state, view_event_region
from .coma_knife_cut_op import _set_coma_polygon
from .coma_op import create_rect_coma

_logger = log.get_logger(__name__)

_DRAG_THRESHOLD_PX = 6.0
_MIN_RECT_MM = 3.0
_CLOSE_HIT_PX = 12.0
_COLOR_PREVIEW = (0.15, 0.55, 1.0, 0.9)


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


def _region_pos_from_local(region, rv3d, ox, oy, lx, ly):
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    co = location_3d_to_region_2d(
        region, rv3d, (geom.mm_to_m(lx + ox), geom.mm_to_m(ly + oy), 0.0)
    )
    return None if co is None else (co[0], co[1])


def _draw_callback(op: "BMANGA_OT_coma_create_tool") -> None:
    region = getattr(op, "_region", None)
    rv3d = getattr(op, "_rv3d", None)
    if region is None or rv3d is None:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", _COLOR_PREVIEW)
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(2.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        if op._mode == "rect" and op._press_px is not None and op._cursor_px is not None:
            x1, y1 = op._press_px
            x2, y2 = op._cursor_px
            corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            verts = []
            for i in range(4):
                verts.append(corners[i])
                verts.append(corners[(i + 1) % 4])
            batch_for_shader(shader, "LINES", {"pos": verts}).draw(shader)
        elif op._mode == "poly" and op._points_mm:
            pts_px = []
            for lx, ly in op._points_mm:
                p = _region_pos_from_local(region, rv3d, op._page_ox, op._page_oy, lx, ly)
                if p is not None:
                    pts_px.append(p)
            chain = list(pts_px)
            if op._cursor_px is not None:
                chain.append(op._cursor_px)
            verts = []
            for i in range(len(chain) - 1):
                verts.append(chain[i])
                verts.append(chain[i + 1])
            if verts:
                batch_for_shader(shader, "LINES", {"pos": verts}).draw(shader)
    finally:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
        except Exception:  # noqa: BLE001
            pass


class BMANGA_OT_coma_create_tool(Operator):
    """コマ作成ツール: ドラッグで矩形、クリック連続で折れ線。"""

    bl_idname = "bmanga.coma_create_tool"
    bl_label = "コマ作成ツール"
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
        if coma_modal_state.get_active("coma_create") is not None:
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        for slot in (
            "coma_vertex_edit",
            "knife_cut",
            "edge_move",
            "layer_move",
            "balloon_tool",
            "balloon_tail_tool",
            "balloon_nurbs_tool",
            "text_tool",
            "effect_line_tool",
        ):
            coma_modal_state.finish_active(slot, context, keep_selection=True)
        self._reset_shape()
        self._page_id = ""
        self._page_index = -1
        self._page_ox = 0.0
        self._page_oy = 0.0
        self._region = None
        self._rv3d = None
        self._area = None
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active("coma_create", self, context)
        self.report(
            {"INFO"},
            "コマ作成: ドラッグで矩形 / クリック連続で折れ線 (始点クリックで閉じる) | ESC/右で終了",
        )
        return {"RUNNING_MODAL"}

    # ---------- 状態 ----------

    def _reset_shape(self) -> None:
        self._points_mm: list[tuple[float, float]] = []
        self._mode = None  # None | "rect" | "poly"
        self._press_px = None
        self._cursor_px = None
        self._press_world_mm = None
        self._maybe_dragging = False

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        h = getattr(self, "_draw_handler", None)
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None
        region = getattr(self, "_region", None)
        if region is not None:
            try:
                region.tag_redraw()
            except Exception:  # noqa: BLE001
                pass

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        coma_modal_state.clear_active("coma_create", self, context)

    # ---------- ロックされたページ ----------

    def _locked_page(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return None
        for i, page in enumerate(work.pages):
            if str(getattr(page, "id", "")) == self._page_id:
                return work, page, i
        return None

    def _lock_page(self, context, world_x_mm, world_y_mm) -> bool:
        from ..utils import page_grid

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
        self._page_index = page_idx
        self._page_ox = ox
        self._page_oy = oy
        return True

    # ---------- modal ----------

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("coma_create", self, context)
            return {"FINISHED", "PASS_THROUGH"}
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
        if view_event_region.toggle_modal_sidebar_if_requested(context, event):
            return {"RUNNING_MODAL"}
        if view_event_region.modal_navigation_ui_passthrough(self, context, event):
            return {"PASS_THROUGH"}
        coma_modal_state.sync_modal_cursor_for_event_region(context, event, self, "CROSSHAIR")
        if not view_event_region.is_view3d_window_event(context, event):
            return {"PASS_THROUGH"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            if self._points_mm:
                # 作成途中の折れ線をキャンセル (ツールは継続)
                self._reset_shape()
                self._tag_redraw()
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}

        if (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "K", "T"}
            and not event.ctrl
            and not event.alt
        ):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            view = _world_mm_from_event(context, event)
            if view is not None:
                _area, region, rv3d, mx, my, _wx, _wy = view
                self._region = region
                self._rv3d = rv3d
                self._area = _area
                self._cursor_px = (mx, my)
            if (
                self._maybe_dragging
                and self._mode is None
                and not self._points_mm
                and self._press_px is not None
                and self._cursor_px is not None
            ):
                dx = self._cursor_px[0] - self._press_px[0]
                dy = self._cursor_px[1] - self._press_px[1]
                if (dx * dx + dy * dy) ** 0.5 >= _DRAG_THRESHOLD_PX:
                    self._mode = "rect"
            self._tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if handle_intercept.try_intercept_press(context, event, self):
                return {"RUNNING_MODAL"}
            return self._on_press(context, event)
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return self._on_release(context, event)
        # 扱わないイベント (ショートカットキー等) は素通しする
        return {"PASS_THROUGH"}

    def _tag_redraw(self) -> None:
        region = getattr(self, "_region", None)
        if region is not None:
            try:
                region.tag_redraw()
            except Exception:  # noqa: BLE001
                pass

    def _on_press(self, context, event):
        view = _world_mm_from_event(context, event)
        if view is None:
            return {"PASS_THROUGH"}
        area, region, rv3d, mx, my, wx, wy = view
        self._region = region
        self._rv3d = rv3d
        self._area = area
        if not self._page_id:
            if not self._lock_page(context, wx, wy):
                self.report({"WARNING"}, "ページ上でドラッグを開始してください")
                return {"RUNNING_MODAL"}
        self._press_px = (mx, my)
        self._cursor_px = (mx, my)
        self._press_world_mm = (wx, wy)
        self._maybe_dragging = True
        return {"RUNNING_MODAL"}

    def _on_release(self, context, event):
        if self._press_px is None:
            return {"RUNNING_MODAL"}
        view = _world_mm_from_event(context, event)
        if view is None:
            self._maybe_dragging = False
            return {"RUNNING_MODAL"}
        _area, _region, _rv3d, mx, my, wx, wy = view
        self._maybe_dragging = False
        if self._mode == "rect" or (
            not self._points_mm
            and self._press_world_mm is not None
            and (
                (mx - self._press_px[0]) ** 2 + (my - self._press_px[1]) ** 2
            ) ** 0.5 >= _DRAG_THRESHOLD_PX
        ):
            return self._finish_rect(context, wx, wy)
        # クリック扱い → 折れ線の頂点
        return self._add_poly_point(context, mx, my, wx, wy)

    # ---------- 矩形 ----------

    def _finish_rect(self, context, world_x_mm, world_y_mm):
        if self._press_world_mm is None:
            self._reset_shape()
            return {"RUNNING_MODAL"}
        px, py = self._press_world_mm
        lx0 = min(px, world_x_mm) - self._page_ox
        ly0 = min(py, world_y_mm) - self._page_oy
        w = abs(world_x_mm - px)
        h = abs(world_y_mm - py)
        if w < _MIN_RECT_MM or h < _MIN_RECT_MM:
            self._reset_shape()
            self.report({"INFO"}, "コマが小さすぎます。もう少し大きくドラッグしてください")
            return {"RUNNING_MODAL"}
        self._create_coma(context, "rect", x=lx0, y=ly0, w=w, h=h, poly=None)
        self._reset_shape()
        return {"RUNNING_MODAL"}

    # ---------- 折れ線 ----------

    def _add_poly_point(self, context, mx, my, world_x_mm, world_y_mm):
        lx = world_x_mm - self._page_ox
        ly = world_y_mm - self._page_oy
        if len(self._points_mm) >= 3:
            from bpy_extras.view3d_utils import location_3d_to_region_2d

            first = self._points_mm[0]
            co = location_3d_to_region_2d(
                self._region,
                self._rv3d,
                (geom.mm_to_m(first[0] + self._page_ox), geom.mm_to_m(first[1] + self._page_oy), 0.0),
            )
            if co is not None:
                d = ((co[0] - mx) ** 2 + (co[1] - my) ** 2) ** 0.5
                if d <= _CLOSE_HIT_PX:
                    poly = list(self._points_mm)
                    self._create_coma(context, "polygon", x=0.0, y=0.0, w=0.0, h=0.0, poly=poly)
                    self._reset_shape()
                    return {"RUNNING_MODAL"}
        if self._points_mm:
            last = self._points_mm[-1]
            if abs(last[0] - lx) < 1.0e-4 and abs(last[1] - ly) < 1.0e-4:
                return {"RUNNING_MODAL"}
        self._points_mm.append((lx, ly))
        self._mode = "poly"
        self._tag_redraw()
        return {"RUNNING_MODAL"}

    # ---------- 生成 ----------

    def _create_coma(self, context, shape, *, x, y, w, h, poly):
        locked = self._locked_page(context)
        if locked is None:
            self.report({"ERROR"}, "作成先のページが見つかりません")
            return
        work, page, page_index = locked
        work_dir = Path(work.work_dir)
        try:
            if shape == "polygon" and poly is not None and len(poly) >= 3:
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                entry = create_rect_coma(
                    work,
                    page,
                    work_dir,
                    min(xs),
                    min(ys),
                    max(xs) - min(xs),
                    max(ys) - min(ys),
                )
                _set_coma_polygon(entry, poly)
            else:
                entry = create_rect_coma(work, page, work_dir, x, y, w, h)
            self._apply_border_preset(context, entry)
            coma_io.save_coma_meta(work_dir, page.id, entry)
            page_io.save_page_json(work_dir, page)
            page.coma_count = len(page.comas)
            page_io.save_pages_json(work_dir, work)
            work.active_page_index = page_index
            self._refresh_coma_objects(context, work, page, entry)
            if hasattr(context.scene, "bmanga_active_layer_kind"):
                context.scene.bmanga_active_layer_kind = "coma"
            layer_stack_utils.sync_layer_stack_after_data_change(
                context, align_coma_order=True
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("coma_create failed")
            self.report({"ERROR"}, f"コマ作成失敗: {exc}")
            return
        self.report({"INFO"}, f"コマ作成: {entry.coma_id}")

    def _apply_border_preset(self, context, entry) -> None:
        wm = context.window_manager
        name = getattr(wm, "bmanga_border_preset_selector", "") if wm is not None else ""
        if not name:
            return
        try:
            from ..io import border_presets

            work = get_work(context)
            work_dir = Path(work.work_dir) if (work and work.work_dir) else None
            preset = border_presets.load_preset_by_name(name, work_dir)
            if preset is not None:
                border_presets.apply_preset_to_coma(preset, entry)
        except Exception:  # noqa: BLE001
            _logger.exception("coma_create: apply border preset failed")

    def _refresh_coma_objects(self, context, work, page, entry) -> None:
        scene = context.scene
        if scene is None:
            return
        try:
            from ..utils import page_file_scene

            if not page_file_scene.is_current_page_edit_scene(scene, getattr(page, "id", "")):
                return
        except Exception:  # noqa: BLE001
            return
        try:
            from ..utils import coma_plane as _cp

            _cp.ensure_coma_plane(scene, work, page, entry)
        except Exception:  # noqa: BLE001
            _logger.exception("coma_create: ensure_coma_plane failed")
        try:
            from ..utils import coma_border_object as _cbo

            _cbo.ensure_coma_border_object(scene, work, page, entry)
        except Exception:  # noqa: BLE001
            _logger.exception("coma_create: ensure_coma_border failed")


_CLASSES = (BMANGA_OT_coma_create_tool,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
