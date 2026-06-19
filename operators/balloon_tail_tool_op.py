"""しっぽツール: クリックでポイントを追加し、ダブルクリックで確定する常駐ツール.

- 1 クリック目: フキダシの上でクリックして起点を決める
- 2 クリック目以降: ポイントを追加 (折れ線のように伸ばす)
- ダブルクリック: しっぽを確定
- ESC: 作成中のしっぽを取り消し / 何もなければツール終了
- 右クリック: 作成中なら確定。しっぽポイント上ならメニュー、それ以外はツール終了
"""

from __future__ import annotations

import time

import bpy
from bpy.types import Operator

from ..core.work import get_work
from ..utils import layer_stack as layer_stack_utils
from ..utils import log, page_file_scene
from . import balloon_op, coma_modal_state, object_tool_balloon_tail, view_event_region

_logger = log.get_logger(__name__)

_DOUBLE_CLICK_INTERVAL_SEC = 0.4
_DOUBLE_CLICK_DISTANCE_PX = 8.0
TOOL_NAME = "balloon_tail_tool"


def _selected_tail_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    name = str(getattr(wm, "bmanga_tail_preset_selector", "") or "") if wm is not None else ""
    return "" if name in {"", "NONE"} else name


def _apply_selected_preset(context, tail) -> None:
    name = _selected_tail_preset_name(context)
    if not name:
        return
    try:
        from pathlib import Path

        from ..io import tail_presets

        work = get_work(context)
        work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
        preset = tail_presets.load_preset_by_name(name, work_dir if work_dir and str(work_dir) else None)
        if preset is not None:
            tail_presets.apply_preset_to_tail(preset, tail)
    except Exception:  # noqa: BLE001
        _logger.exception("tail tool: preset apply failed")


class BMANGA_OT_balloon_tail_tool(Operator):
    """しっぽツール (クリックでポイント追加、ダブルクリックで確定)."""

    bl_idname = "bmanga.balloon_tail_tool"
    bl_label = "しっぽツール"

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
        self._last_press_time = 0.0
        self._last_press_xy = (-1.0e9, -1.0e9)
        object_tool_balloon_tail.clear_pending(self)
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active(TOOL_NAME, self, context)
        self.report({"INFO"}, "しっぽツール: フキダシをクリックして開始、クリックでポイント追加、ダブルクリックで決定")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active(TOOL_NAME, self, context)
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
        if not view_event_region.is_view3d_window_event(context, event):
            return {"PASS_THROUGH"}
        if event.type == "ESC" and event.value == "PRESS":
            if self._has_pending():
                self._cancel_pending(context)
                return {"RUNNING_MODAL"}
            return self._finish(context)
        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if self._has_pending():
                self._finalize_pending(context)
                return {"RUNNING_MODAL"}
            if object_tool_balloon_tail.open_point_menu(context, event):
                return {"RUNNING_MODAL"}
            return self._finish(context)
        if event.value == "PRESS" and event.type in {"RET", "NUMPAD_ENTER"}:
            self._finalize_pending(context)
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

    # ---------- クリック処理 ----------

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
            # ダブルクリック: 直前のクリックで追加済みのポイントを終点として確定
            self._finalize_pending(context)
            # 確定直後の素早い次クリックを誤ってダブルクリック扱いしない
            self._last_press_time = 0.0
            return {"RUNNING_MODAL"}
        if not self._has_pending():
            return self._start_pending(context, event)
        work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
        del work
        if page is None or lx is None or ly is None:
            # ページの外をクリックしてもポイントは追加しない
            return {"RUNNING_MODAL"}
        had_tail = int(getattr(self, "_pending_tail_index", -1)) >= 0
        if object_tool_balloon_tail.append_pending_click(self, context, page, float(lx), float(ly)):
            if not had_tail and int(getattr(self, "_pending_tail_index", -1)) >= 0:
                self._apply_preset_to_pending(context)
        return {"RUNNING_MODAL"}

    def _start_pending(self, context, event):
        work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
        if work is None or page is None or lx is None or ly is None:
            return {"RUNNING_MODAL"}
        hit_index, entry, _part = balloon_op._hit_balloon_entry(page, lx, ly)
        if entry is None or hit_index < 0:
            self.report({"INFO"}, "フキダシの上をクリックして、しっぽの起点を決めてください")
            return {"RUNNING_MODAL"}
        self._pending_tail_page_id = str(getattr(page, "id", "") or "")
        self._pending_tail_balloon_id = str(getattr(entry, "id", "") or "")
        self._pending_tail_points = [(float(lx), float(ly))]
        self._pending_tail_index = -1
        return {"RUNNING_MODAL"}

    def _has_pending(self) -> bool:
        return bool(str(getattr(self, "_pending_tail_balloon_id", "") or ""))

    def _pending_entry(self, context):
        work = get_work(context)
        page_id = str(getattr(self, "_pending_tail_page_id", "") or "")
        balloon_id = str(getattr(self, "_pending_tail_balloon_id", "") or "")
        for page in getattr(work, "pages", []) or [] if work is not None else []:
            if str(getattr(page, "id", "") or "") != page_id:
                continue
            idx = balloon_op._find_balloon_index(page, balloon_id)
            if idx >= 0:
                return page, page.balloons[idx]
        return None, None

    def _apply_preset_to_pending(self, context) -> None:
        page, entry = self._pending_entry(context)
        tail_index = int(getattr(self, "_pending_tail_index", -1))
        if entry is None or not (0 <= tail_index < len(entry.tails)):
            return
        _apply_selected_preset(context, entry.tails[tail_index])
        try:
            from ..utils import balloon_curve_object

            balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        except Exception:  # noqa: BLE001
            _logger.exception("tail tool: curve sync after preset failed")

    def _finalize_pending(self, context) -> None:
        if not self._has_pending():
            return
        created = int(getattr(self, "_pending_tail_index", -1)) >= 0
        object_tool_balloon_tail.clear_pending(self)
        if created:
            try:
                bpy.ops.ed.undo_push(message="B-MANGA: しっぽ作成")
            except Exception:  # noqa: BLE001
                pass
            self.report({"INFO"}, "しっぽを確定しました")
        layer_stack_utils.tag_view3d_redraw(context)

    def _cancel_pending(self, context) -> None:
        page, entry = self._pending_entry(context)
        tail_index = int(getattr(self, "_pending_tail_index", -1))
        if entry is not None and 0 <= tail_index < len(entry.tails):
            entry.tails.remove(tail_index)
            try:
                from ..utils import balloon_curve_object

                balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            except Exception:  # noqa: BLE001
                pass
            layer_stack_utils.sync_layer_stack_after_data_change(context)
            try:
                # クリックごとの undo 履歴の上に「取り消し後」を積み、Ctrl+Z で
                # 作りかけが復活して見えないようにする
                bpy.ops.ed.undo_push(message="B-MANGA: しっぽ作成を取り消し")
            except Exception:  # noqa: BLE001
                pass
        object_tool_balloon_tail.clear_pending(self)
        self.report({"INFO"}, "作成中のしっぽを取り消しました")

    # ---------- 終了 ----------

    def _finish(self, context):
        self._finalize_pending(context)
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
        coma_modal_state.clear_active(TOOL_NAME, self, context)
        return {"FINISHED"}

    def finish_from_external(self, context, *, keep_selection: bool = True) -> None:
        del keep_selection
        self._finalize_pending(context)
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
        self._externally_finished = True


_CLASSES = (BMANGA_OT_balloon_tail_tool,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
