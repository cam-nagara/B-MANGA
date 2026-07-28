"""Blender 5.2実機: 通常ドラッグ途中のAlt押下をページ／コマ移送へ切り替える。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_mid_drag_alt_test",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_mid_drag_alt_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


class _FakeMoveTransaction:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self, _context) -> None:
        self.cancelled = True


def main() -> None:
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        from bmanga_mid_drag_alt_test.operators import object_tool_op

        operator = SimpleNamespace()
        transaction = _FakeMoveTransaction()
        target = SimpleNamespace(
            kind="page",
            page=SimpleNamespace(id="p0002"),
            panel=None,
            world_xy_mm=(420.0, 180.0),
        )
        operator._dragging = True
        operator._drag_action = "move"
        operator._drag_start_px = (100.0, 120.0)
        operator._drag_moved = True
        operator._drag_keys = ["text|p0001|text01"]
        operator._snapshots = [{"kind": "text"}]
        operator._object_move_drag = transaction
        operator._layer_drag = None

        originals = (
            object_tool_op._reparent_has_targets,
            object_tool_op._reparent_count,
            object_tool_op._reparent_set_overlay,
            object_tool_op.layer_reparent.find_target_for_drop,
            object_tool_op.reparent_overlay.set_preview,
        )
        previews = []
        try:
            object_tool_op._reparent_has_targets = lambda _context: True
            object_tool_op._reparent_count = lambda _context: 2
            object_tool_op._reparent_set_overlay = lambda _target: None
            object_tool_op.layer_reparent.find_target_for_drop = lambda _context, _event: target
            object_tool_op.reparent_overlay.set_preview = lambda **kwargs: previews.append(kwargs)
            alt_press = SimpleNamespace(
                type="LEFT_ALT",
                value="PRESS",
                alt=True,
                ctrl=False,
                mouse_x=170,
                mouse_y=190,
            )
            assert object_tool_op.BMANGA_OT_object_tool._switch_drag_to_reparent(
                operator,
                bpy.context,
                alt_press,
            )
            assert transaction.cancelled
            assert operator._drag_action == "reparent"
            assert operator._reparent_start_px == (100.0, 120.0)
            assert operator._object_move_drag is None
            assert previews[-1]["world_xy_mm"] == target.world_xy_mm

            move = SimpleNamespace(
                type="MOUSEMOVE",
                value="NOTHING",
                alt=True,
                ctrl=False,
                mouse_x=180,
                mouse_y=200,
            )
            object_tool_op.BMANGA_OT_object_tool._update_drag(
                operator,
                bpy.context,
                move,
            )
            assert operator._drag_moved

            layer_operator = SimpleNamespace(
                _dragging=True,
                _drag_action="layer_move",
                _drag_start_px=(210.0, 230.0),
                _drag_moved=True,
                _drag_keys=[],
                _snapshots=[],
                _object_move_drag=None,
                _layer_drag=_FakeMoveTransaction(),
            )
            layer_transaction = layer_operator._layer_drag
            assert object_tool_op.BMANGA_OT_object_tool._switch_drag_to_reparent(
                layer_operator,
                bpy.context,
                move,
            )
            assert layer_transaction.cancelled
            assert layer_operator._layer_drag is None
            assert layer_operator._drag_action == "reparent"
            assert layer_operator._reparent_start_px == (210.0, 230.0)
        finally:
            (
                object_tool_op._reparent_has_targets,
                object_tool_op._reparent_count,
                object_tool_op._reparent_set_overlay,
                object_tool_op.layer_reparent.find_target_for_drop,
                object_tool_op.reparent_overlay.set_preview,
            ) = originals
        print("BMANGA_OBJECT_TOOL_MID_DRAG_ALT_OK")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
