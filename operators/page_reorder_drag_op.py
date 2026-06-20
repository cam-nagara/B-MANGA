"""作品ファイル上でのページ並べ替え Alt+D&D Operator.

Alt+ドラッグで選択ページを別の位置へ移動する。
複数ページ選択時はまとめて移動。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_work
from ..io import page_io
from ..ui import reparent_overlay
from ..utils import (
    layer_stack as layer_stack_utils,
    log,
    object_selection,
    page_file_scene,
    page_grid,
    page_range,
    shortcut_visibility,
)

_logger = log.get_logger(__name__)

_DRAG_THRESHOLD_PX = 4.0


def _is_work_overview(context) -> bool:
    role, _, _ = page_file_scene.current_role(context)
    return (
        role == page_file_scene.ROLE_WORK
        and get_mode(context) == MODE_PAGE
        and bool(getattr(context.scene, "bmanga_overview_mode", False))
    )


def _selected_page_indices(context, work) -> list[int]:
    """object_selection で選択中のページ index を返す (昇順)."""
    keys = object_selection.get_keys(context)
    indices: list[int] = []
    for key in keys:
        kind, _page_id, item_id = object_selection.parse_key(key)
        if kind != "page":
            continue
        for i, page in enumerate(work.pages):
            if str(getattr(page, "id", "") or "") == item_id:
                indices.append(i)
                break
    return sorted(set(indices))


def _selection_is_page_reorder_ready(context) -> bool:
    keys = object_selection.get_keys(context)
    if keys:
        return all(object_selection.parse_key(key)[0] == "page" for key in keys)
    scene = getattr(context, "scene", None)
    return str(getattr(scene, "bmanga_active_layer_kind", "") or "") == "page"


def _page_index_for_id(work, page_id: str) -> int:
    for i, page in enumerate(work.pages):
        if str(getattr(page, "id", "") or "") == page_id:
            return i
    return -1


def _page_offsets_by_id(context, work) -> dict[str, tuple[float, float]]:
    return {
        str(page.id): page_grid.page_total_offset_mm(work, context.scene, i)
        for i, page in enumerate(getattr(work, "pages", []) or [])
    }


def _translate_layers_for_offset_changes(
    context, work, old_offsets: dict[str, tuple[float, float]]
) -> None:
    for i, page in enumerate(getattr(work, "pages", []) or []):
        old = old_offsets.get(str(getattr(page, "id", "") or ""))
        if old is None:
            continue
        new = page_grid.page_total_offset_mm(work, context.scene, i)
        dx = new[0] - old[0]
        dy = new[1] - old[1]
        if abs(dx) <= 1.0e-6 and abs(dy) <= 1.0e-6:
            continue
        parent_keys = layer_stack_utils.gp_parent_keys_for_page(page)
        layer_stack_utils.translate_gp_layers_for_parent_keys(
            context, parent_keys, dx, dy
        )
        layer_stack_utils.translate_effect_layers_for_parent_keys(
            context, parent_keys, dx, dy
        )


def _reorder_pages(context, work, src_indices: list[int], dst_index: int) -> bool:
    """選択ページを dst_index の直前に移動する。

    src_indices: 移動するページの現在のインデックス (昇順)
    dst_index: 移動先のインデックス (移動前基準)

    Returns: 変更があれば True
    """
    n = len(work.pages)
    if not src_indices or not (0 <= dst_index <= n):
        return False
    src_set = set(src_indices)
    page_ids = [str(getattr(work.pages[i], "id", "") or "") for i in range(n)]
    selected_ids = [page_ids[i] for i in src_indices]
    remaining_ids = [pid for i, pid in enumerate(page_ids) if i not in src_set]
    adj_dst = sum(1 for i in src_indices if i < dst_index)
    insert_at = max(0, min(dst_index - adj_dst, len(remaining_ids)))
    new_order = remaining_ids[:insert_at] + selected_ids + remaining_ids[insert_at:]
    if new_order == page_ids:
        return False
    old_offsets = _page_offsets_by_id(context, work)
    for step, target_id in enumerate(new_order):
        old_idx = -1
        for i, page in enumerate(work.pages):
            if str(getattr(page, "id", "") or "") == target_id:
                old_idx = i
                break
        if old_idx < 0:
            continue
        if old_idx != step:
            work.pages.move(old_idx, step)
    active_page_id = page_ids[work.active_page_index] if 0 <= work.active_page_index < n else ""
    if active_page_id:
        new_active = _page_index_for_id(work, active_page_id)
        if new_active >= 0:
            work.active_page_index = new_active
    page_range.update_page_range_visibility(work)
    _translate_layers_for_offset_changes(context, work, old_offsets)
    page_grid.apply_page_collection_transforms(context, work)
    work_dir = Path(str(work.work_dir))
    page_io.save_pages_json(work_dir, work)
    return True


class BMANGA_OT_page_reorder_drag(Operator):
    """Alt+ドラッグで選択ページを別の位置へ移動."""

    bl_idname = "bmanga.page_reorder_drag"
    bl_label = "ページ並べ替え"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and work.loaded
            and _is_work_overview(context)
            and getattr(context, "mode", "") == "OBJECT"
            and shortcut_visibility.shortcuts_allowed(context)
        )

    def invoke(self, context, event):
        if event.value != "PRESS":
            return {"PASS_THROUGH"}
        if not bool(getattr(event, "alt", False)):
            return {"PASS_THROUGH"}
        if bool(getattr(event, "ctrl", False)):
            return {"PASS_THROUGH"}
        if bool(getattr(event, "shift", False)):
            return {"PASS_THROUGH"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        from . import coma_picker

        page_index = coma_picker.find_page_at_event(context, event)
        if page_index is None or not (0 <= page_index < len(work.pages)):
            return {"PASS_THROUGH"}
        if not page_range.page_in_range(work.pages[page_index]):
            return {"PASS_THROUGH"}
        if not _selection_is_page_reorder_ready(context):
            return {"PASS_THROUGH"}
        self._start_page_index = page_index
        self._start_xy = (float(event.mouse_x), float(event.mouse_y))
        self._drag_moved = False
        self._target_index: int | None = None
        self._dst_index: int | None = None
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            sx, sy = self._start_xy
            if (
                not self._drag_moved
                and (
                    abs(float(event.mouse_x) - sx) >= _DRAG_THRESHOLD_PX
                    or abs(float(event.mouse_y) - sy) >= _DRAG_THRESHOLD_PX
                )
            ):
                self._drag_moved = True
            if self._drag_moved:
                self._update_target(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if self._drag_moved:
                self._update_target(context, event)
                self._execute_reorder(context)
            reparent_overlay.clear_hover()
            layer_stack_utils.tag_view3d_redraw(context)
            return {"FINISHED"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            reparent_overlay.clear_hover()
            layer_stack_utils.tag_view3d_redraw(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _update_target(self, context, event) -> None:
        work = get_work(context)
        if work is None:
            return
        from . import coma_picker

        page_index = coma_picker.find_page_at_event(context, event)
        if page_index is not None and 0 <= page_index < len(work.pages):
            self._target_index = page_index
            insert_after = self._mouse_past_page_center(
                context, event, work, page_index
            )
            self._dst_index = page_index + (1 if insert_after else 0)
            page = work.pages[page_index]
            reparent_overlay.set_hover(
                "page",
                page_id=str(getattr(page, "id", "") or ""),
                page_index=page_index,
            )
        else:
            self._target_index = None
            self._dst_index = None
            reparent_overlay.clear_hover()
        layer_stack_utils.tag_view3d_redraw(context)

    @staticmethod
    def _mouse_past_page_center(context, event, work, page_index: int) -> bool:
        """マウスがページ中心より右（横並び）か下（縦並び）なら True."""
        from . import coma_picker

        coords = coma_picker._event_world_mm(context, event)
        if coords is None:
            return False
        scene = getattr(context, "scene", None)
        if scene is None:
            return False
        ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
        cw = float(work.paper.canvas_width_mm)
        cx = ox + cw * 0.5
        return coords[0] > cx

    def _execute_reorder(self, context) -> None:
        target = self._dst_index
        if target is None:
            return
        work = get_work(context)
        if work is None or not work.loaded:
            return
        src = _selected_page_indices(context, work)
        if not src:
            src = [self._start_page_index]
        elif self._start_page_index not in src:
            src = [self._start_page_index]
        try:
            if _reorder_pages(context, work, src, target):
                try:
                    bpy.ops.ed.undo_push(message="B-MANGA: ページ並べ替え")
                except Exception:  # noqa: BLE001
                    pass
                self._sync_after_reorder(context)
        except Exception:  # noqa: BLE001
            _logger.exception("page_reorder_drag failed")

    def _sync_after_reorder(self, context) -> None:
        try:
            layer_stack_utils.sync_layer_stack(
                context, align_page_order=True
            )
            layer_stack_utils.remember_layer_stack_signature(context)
            layer_stack_utils.tag_view3d_redraw(context)
        except Exception:  # noqa: BLE001
            _logger.exception("page_reorder_drag: sync failed")
        try:
            work = get_work(context)
            scene = getattr(context, "scene", None)
            if scene is not None and work is not None:
                from ..utils import page_preview_object

                page_preview_object.sync_page_previews(context, work)
        except Exception:  # noqa: BLE001
            _logger.exception("page_reorder_drag: preview sync failed")

_CLASSES = (BMANGA_OT_page_reorder_drag,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
