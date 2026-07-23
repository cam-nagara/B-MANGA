"""NURBSフキダシツール: クリックした点を通るなめらかな閉曲線でフキダシを作る.

- クリック: 輪郭ポイントを追加
- ダブルクリック / Enter: 閉じて確定 (3 点以上)
- ESC: 作成中のポイントを破棄 / 何もなければツール終了
- 右クリック: 作成中なら確定、それ以外はツール終了

確定すると NURBS カーブの実体が作られ、自由形状フキダシとして登録される
(「選択カーブをフキダシに登録」と同じ仕組み)。
"""

from __future__ import annotations

import time

import bpy
import gpu
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader

from ..core.work import get_active_page, get_work
from ..utils import geom, layer_stack as layer_stack_utils, log, page_file_scene, page_grid
from ..utils.layer_hierarchy import coma_containing_point, coma_stack_key, page_stack_key
from . import balloon_op, coma_modal_state, view_event_region

_logger = log.get_logger(__name__)

_DOUBLE_CLICK_INTERVAL_SEC = 0.4
_DOUBLE_CLICK_DISTANCE_PX = 8.0
TOOL_NAME = "balloon_nurbs_tool"

# 作成中の輪郭ポイントを示すマーカー色。用紙 (白) の上でも art の上でも視認できる
# よう、暗いリング (外側) の中に明るいオレンジの芯 (内側) を重ねて描く。
_POINT_RING_COLOR = (0.0, 0.0, 0.0, 1.0)
_POINT_CORE_COLOR = (1.0, 0.6, 0.05, 1.0)
_PREVIEW_LINE_COLOR = (0.1, 0.55, 0.95, 0.9)


def _draw_callback(op: "BMANGA_OT_balloon_nurbs_tool") -> None:
    """作成中にクリックした輪郭ポイントと閉曲線プレビューをビューポートへ描く.

    ポイントは world (ページ) 座標で保持しているため、 描画のたびに現在の
    リージョン/視点で region 2D へ射影する。 これでビューをパン/ズームしても
    ポイントは用紙に貼り付いたまま追従する。
    """
    pts_world = getattr(op, "_points_world_mm", None)
    if not pts_world:
        return
    region = getattr(bpy.context, "region", None)
    rv3d = getattr(bpy.context, "region_data", None)
    if region is None or rv3d is None or getattr(region, "type", "") != "WINDOW":
        return
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    screen: list[tuple[float, float]] = []
    for x_mm, y_mm in pts_world:
        co = location_3d_to_region_2d(
            region, rv3d, Vector((geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0))
        )
        if co is None:
            continue
        screen.append((float(co[0]), float(co[1])))
    if not screen:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    try:
        gpu.state.blend_set("ALPHA")
        # 閉曲線プレビュー (最後→最初を含めて輪郭の閉じ方を示す)
        if len(screen) >= 2:
            try:
                gpu.state.line_width_set(1.5)
            except Exception:  # noqa: BLE001
                pass
            line_verts: list[tuple[float, float]] = []
            for i in range(len(screen) - 1):
                line_verts.append(screen[i])
                line_verts.append(screen[i + 1])
            line_verts.append(screen[-1])
            line_verts.append(screen[0])
            shader.uniform_float("color", _PREVIEW_LINE_COLOR)
            batch_for_shader(shader, "LINES", {"pos": line_verts}).draw(shader)
        # ポイント: 暗いリング → 明るい芯 の 2 パスで白背景でも視認できるようにする
        try:
            gpu.state.point_size_set(11.0)
        except Exception:  # noqa: BLE001
            pass
        shader.uniform_float("color", _POINT_RING_COLOR)
        batch_for_shader(shader, "POINTS", {"pos": screen}).draw(shader)
        try:
            gpu.state.point_size_set(7.0)
        except Exception:  # noqa: BLE001
            pass
        shader.uniform_float("color", _POINT_CORE_COLOR)
        batch_for_shader(shader, "POINTS", {"pos": screen}).draw(shader)
    finally:
        try:
            gpu.state.point_size_set(1.0)
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
        except Exception:  # noqa: BLE001
            pass


def _interpolating_controls(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """閉じた一様 3 次 B-spline がクリック点を通るような制御点列を解く.

    各クリック点 P[i] について (C[i-1] + 4*C[i] + C[i+1]) / 6 = P[i] が成り立つ
    巡回連立方程式をガウス消去で解く。点数が少ない場合はそのまま返す。
    """
    n = len(points)
    if n < 4:
        return list(points)
    try:
        size = n
        # 拡大係数行列 (巡回 [1, 4, 1] / 6)
        matrix = [[0.0] * size for _ in range(size)]
        bx = [6.0 * p[0] for p in points]
        by = [6.0 * p[1] for p in points]
        for i in range(size):
            matrix[i][(i - 1) % size] += 1.0
            matrix[i][i] += 4.0
            matrix[i][(i + 1) % size] += 1.0
        # ガウス消去 (部分ピボット)
        for col in range(size):
            pivot = max(range(col, size), key=lambda r: abs(matrix[r][col]))
            if abs(matrix[pivot][col]) < 1.0e-9:
                return list(points)
            if pivot != col:
                matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
                bx[col], bx[pivot] = bx[pivot], bx[col]
                by[col], by[pivot] = by[pivot], by[col]
            inv = 1.0 / matrix[col][col]
            for row in range(col + 1, size):
                factor = matrix[row][col] * inv
                if factor == 0.0:
                    continue
                for k in range(col, size):
                    matrix[row][k] -= factor * matrix[col][k]
                bx[row] -= factor * bx[col]
                by[row] -= factor * by[col]
        cx = [0.0] * size
        cy = [0.0] * size
        for row in range(size - 1, -1, -1):
            sx = bx[row]
            sy = by[row]
            for k in range(row + 1, size):
                sx -= matrix[row][k] * cx[k]
                sy -= matrix[row][k] * cy[k]
            cx[row] = sx / matrix[row][row]
            cy[row] = sy / matrix[row][row]
        return list(zip(cx, cy, strict=False))
    except Exception:  # noqa: BLE001
        _logger.exception("nurbs interpolation solve failed")
        return list(points)


class BMANGA_OT_balloon_nurbs_tool(Operator):
    """NURBSフキダシツール (クリックで輪郭ポイント追加、ダブルクリックで確定)."""

    bl_idname = "bmanga.balloon_nurbs_tool"
    bl_label = "NURBSフキダシツール"

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and getattr(work, "loaded", False)
            and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
        )

    def invoke(self, context, _event):
        if coma_modal_state.get_active(TOOL_NAME) is not None:
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        coma_modal_state.finish_all(context, except_tool=TOOL_NAME)
        self._externally_finished = False
        self._points_world_mm: list[tuple[float, float]] = []
        self._last_press_time = 0.0
        self._last_press_xy = (-1.0e9, -1.0e9)
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active(TOOL_NAME, self, context)
        self.report({"INFO"}, "NURBSフキダシ: クリックで輪郭ポイントを追加、ダブルクリックで決定 (3点以上)")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            self._remove_draw_handler()
            coma_modal_state.clear_active(TOOL_NAME, self, context)
            return {"FINISHED", "PASS_THROUGH"}
        from . import handle_intercept, object_rotation
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
        # 選択ハンドル回転リングのホバーカーソル
        if event.type == "MOUSEMOVE":
            object_rotation.update_rotation_hover_cursor(context, event, self, restore_cursor="CROSSHAIR")
        if event.type == "ESC" and event.value == "PRESS":
            if self._points_world_mm:
                self._points_world_mm = []
                layer_stack_utils.tag_view3d_redraw(context)
                self.report({"INFO"}, "作成中の輪郭を取り消しました")
                return {"RUNNING_MODAL"}
            return self._finish(context)
        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if self._points_world_mm:
                self._create_balloon(context)
                return {"RUNNING_MODAL"}
            return self._finish(context)
        if event.value == "PRESS" and event.type in {"RET", "NUMPAD_ENTER"}:
            self._create_balloon(context)
            return {"RUNNING_MODAL"}
        if (
            event.type == "LEFTMOUSE"
            and event.value == "PRESS"
            and handle_intercept.try_intercept_press(context, event, self)
        ):
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            return self._handle_press(context, event)
        return {"PASS_THROUGH"}

    def _handle_press(self, context, event):
        now = time.monotonic()
        mx = float(getattr(event, "mouse_x", 0.0))
        my = float(getattr(event, "mouse_y", 0.0))
        dx = mx - self._last_press_xy[0]
        dy = my - self._last_press_xy[1]
        is_double = (
            event.value == "DOUBLE_CLICK"
            or (
                (now - self._last_press_time) <= _DOUBLE_CLICK_INTERVAL_SEC
                and (dx * dx + dy * dy) ** 0.5 <= _DOUBLE_CLICK_DISTANCE_PX
            )
        )
        self._last_press_time = now
        self._last_press_xy = (mx, my)
        if is_double:
            self._create_balloon(context)
            # 確定直後の素早い次クリックを誤ってダブルクリック扱いしない
            self._last_press_time = 0.0
            return {"RUNNING_MODAL"}
        work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
        if work is None or page is None or lx is None or ly is None:
            return {"RUNNING_MODAL"}
        page_index = -1
        page_id = str(getattr(page, "id", "") or "")
        for i, candidate in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(candidate, "id", "") or "") == page_id:
                page_index = i
                break
        if page_index < 0:
            return {"RUNNING_MODAL"}
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
        self._points_world_mm.append((ox + float(lx), oy + float(ly)))
        layer_stack_utils.tag_view3d_redraw(context)
        self.report({"INFO"}, f"輪郭ポイント {len(self._points_world_mm)} 点")
        return {"RUNNING_MODAL"}

    def _create_balloon(self, context) -> None:
        points = list(self._points_world_mm)
        self._points_world_mm = []
        if len(points) < 3:
            if points:
                self.report({"WARNING"}, "輪郭は3点以上クリックしてください")
            return
        try:
            # クリックした点を曲線が「通る」よう、NURBS の制御点を補間計算する
            controls = _interpolating_controls(points)
            curve = bpy.data.curves.new("NURBSフキダシ", "CURVE")
            curve.dimensions = "2D"
            spline = curve.splines.new("NURBS")
            spline.points.add(len(controls) - 1)
            for spline_point, (x_mm, y_mm) in zip(spline.points, controls, strict=False):
                spline_point.co = (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0, 1.0)
            spline.use_cyclic_u = True
            spline.order_u = min(4, len(controls))
            obj = bpy.data.objects.new("NURBSフキダシ", curve)
            context.scene.collection.objects.link(obj)
            for selected in list(getattr(context, "selected_objects", []) or []):
                selected.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            result = bpy.ops.bmanga.balloon_register_selected_curve()
            if "FINISHED" not in result:
                bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.curves.remove(curve)
                self._points_world_mm = points
                self.report({"WARNING"}, "フキダシとして登録できませんでした")
                return
            # クリック位置の重心からコマを特定し、親を修正する
            self._fix_parent_from_points(context, points)
            try:
                bpy.ops.ed.undo_push(message="B-MANGA: NURBSフキダシ作成")
            except Exception:  # noqa: BLE001
                pass
            self.report({"INFO"}, "NURBSフキダシを作成しました")
            layer_stack_utils.tag_view3d_redraw(context)
        except Exception:  # noqa: BLE001
            _logger.exception("nurbs balloon create failed")
            self.report({"ERROR"}, "NURBSフキダシの作成に失敗しました")

    def _fix_parent_from_points(self, context, points: list[tuple[float, float]]) -> None:
        """クリック点の重心位置から正しい親コマを特定して設定する."""
        if not points:
            return
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return
        balloons = getattr(page, "balloons", None)
        if not balloons:
            return
        entry = balloons[len(balloons) - 1]
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        page_index = -1
        page_id = str(getattr(page, "id", "") or "")
        for i, candidate in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(candidate, "id", "") or "") == page_id:
                page_index = i
                break
        if page_index < 0:
            return
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
        lx = cx - ox
        ly = cy - oy
        panel = coma_containing_point(page, lx, ly)
        if panel is not None:
            entry.parent_kind = "coma"
            entry.parent_key = coma_stack_key(page, panel)
        else:
            entry.parent_kind = "page"
            entry.parent_key = page_stack_key(page)

    def _remove_draw_handler(self) -> None:
        handler = getattr(self, "_draw_handler", None)
        if handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None

    def _finish(self, context):
        self._remove_draw_handler()
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
        self._rotate_cursor_active = False
        layer_stack_utils.tag_view3d_redraw(context)
        coma_modal_state.clear_active(TOOL_NAME, self, context)
        return {"FINISHED"}

    def finish_from_external(self, context, *, keep_selection: bool = True) -> None:
        del keep_selection
        self._remove_draw_handler()
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
        self._rotate_cursor_active = False
        self._externally_finished = True


_CLASSES = (BMANGA_OT_balloon_nurbs_tool,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
