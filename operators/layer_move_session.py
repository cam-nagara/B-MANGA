"""Reusable layer move drag session for tools outside the layer-move modal."""

from __future__ import annotations

from ..utils import layer_stack as layer_stack_utils
from . import layer_move_op, coma_picker


class LayerMoveDragSession:
    """既存のレイヤー移動処理をオブジェクトツールから再利用するセッション."""

    def __init__(self, context, start_world: tuple[float, float]) -> None:
        self._target = None
        self._snapshots = []
        self._last_world = start_world
        self._dragging = False
        self._moved = False
        self._drag_origin_world = start_world
        self._last_applied_total = (0.0, 0.0)
        self._center_snap_targets = []
        self._original_center = None
        self._center_snap_armed = False
        self._effect_meta_origin = None
        self._drag_transaction = None
        self._started = bool(layer_move_op.BMANGA_OT_layer_move_tool._begin_drag(self, context, start_world))

    @property
    def started(self) -> bool:
        return bool(self._started)

    @property
    def moved(self) -> bool:
        return bool(self._moved)

    def report(self, _levels, _message: str) -> None:
        return

    def _capture_snapshot(self, context, kind: str, resolved: dict) -> None:
        layer_move_op.BMANGA_OT_layer_move_tool._capture_snapshot(self, context, kind, resolved)

    def _restore_snapshots(self, context) -> None:
        layer_move_op.BMANGA_OT_layer_move_tool._restore_snapshots(self, context)

    def _apply_delta(self, context, dx_mm: float, dy_mm: float) -> bool:
        return bool(layer_move_op.BMANGA_OT_layer_move_tool._apply_delta(self, context, dx_mm, dy_mm))

    def _can_apply_total(self, context, dx_mm: float, dy_mm: float) -> bool:
        return bool(
            layer_move_op.BMANGA_OT_layer_move_tool._can_apply_total(
                self,
                context,
                dx_mm,
                dy_mm,
            )
        )

    def _setup_center_snap(self, context, kind: str, resolved: dict) -> None:
        layer_move_op.BMANGA_OT_layer_move_tool._setup_center_snap(
            self,
            context,
            kind,
            resolved,
        )

    def _commit_effect_meta(self) -> None:
        layer_move_op.BMANGA_OT_layer_move_tool._commit_effect_meta(self)

    def _finalize_committed_drag(self, context) -> None:
        layer_move_op.BMANGA_OT_layer_move_tool._finalize_committed_drag(
            self,
            context,
        )

    def _push_undo_step(self) -> None:
        layer_move_op.BMANGA_OT_layer_move_tool._push_undo_step(self)

    def apply(self, context, event) -> bool:
        coords = coma_picker._event_world_mm(context, event)
        if coords is None or self._last_world is None or not self._dragging:
            return False
        total_dx = coords[0] - self._drag_origin_world[0]
        total_dy = coords[1] - self._drag_origin_world[1]
        if (total_dx, total_dy) == self._last_applied_total:
            return False
        transaction = getattr(self, "_drag_transaction", None)
        if transaction is not None and transaction.update_overlay(
            context,
            total_dx,
            total_dy,
        ):
            self._last_world = coords
            self._last_applied_total = (total_dx, total_dy)
            self._moved = True
            layer_stack_utils.tag_view3d_redraw(context)
            return True
        return False

    def finish(self, context) -> bool:
        moved = bool(self._moved)
        if moved:
            transaction = getattr(self, "_drag_transaction", None)
            moved = bool(transaction and transaction.commit(context))
            if moved:
                self._commit_effect_meta()
                self._finalize_committed_drag(context)
                self._push_undo_step()
        elif getattr(self, "_drag_transaction", None) is not None:
            self._drag_transaction.cancel()
        self._drag_transaction = None
        self._target = None
        self._snapshots = []
        self._last_world = None
        self._dragging = False
        self._moved = False
        return moved

    def cancel(self, context) -> None:
        transaction = getattr(self, "_drag_transaction", None)
        if transaction is not None:
            transaction.cancel()
        else:
            self._restore_snapshots(context)
        self._drag_transaction = None
        self._target = None
        self._snapshots = []
        self._last_world = None
        self._dragging = False
        self._moved = False
        layer_stack_utils.tag_view3d_redraw(context)
