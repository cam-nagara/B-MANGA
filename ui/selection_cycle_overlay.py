"""同一点クリック循環で選ばれたレイヤー名をカーソル付近へ一時表示する."""

from __future__ import annotations

import time
from dataclasses import dataclass

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..utils import log

_logger = log.get_logger(__name__)

_FONT_SIZE_PX = 14
_OFFSET_X_PX = 18.0
_OFFSET_Y_PX = 22.0
_PAD_X_PX = 9.0
_PAD_Y_PX = 6.0


@dataclass
class _State:
    label: str = ""
    region_pointer: int = 0
    x_px: float = 0.0
    y_px: float = 0.0
    expires_at: float = 0.0
    generation: int = 0


_state = _State()
_handle = None


def _tag_redraw_all() -> None:
    context = getattr(bpy, "context", None)
    wm = getattr(context, "window_manager", None) if context is not None else None
    for window in getattr(wm, "windows", []) or []:
        for area in getattr(getattr(window, "screen", None), "areas", []) or []:
            if getattr(area, "type", "") == "VIEW_3D":
                area.tag_redraw()


def show(
    region,
    x_px: float,
    y_px: float,
    label: str,
    *,
    index: int,
    total: int,
    duration: float = 1.25,
) -> None:
    """選択名と循環位置を表示し、期限後に確実に再描画して消す."""
    if region is None or not label or total < 2:
        clear()
        return
    _state.generation += 1
    generation = _state.generation
    _state.label = f"{label}  {max(1, int(index))}/{max(1, int(total))}"
    _state.region_pointer = int(region.as_pointer())
    _state.x_px = float(x_px)
    _state.y_px = float(y_px)
    _state.expires_at = time.monotonic() + max(0.1, float(duration))
    _tag_redraw_all()

    def _expire():
        if generation == _state.generation and time.monotonic() >= _state.expires_at:
            clear()
        return None

    try:
        bpy.app.timers.register(_expire, first_interval=max(0.1, float(duration)))
    except Exception:  # noqa: BLE001
        _logger.exception("selection cycle overlay timer registration failed")


def clear() -> None:
    changed = bool(_state.label)
    _state.generation += 1
    _state.label = ""
    _state.region_pointer = 0
    _state.expires_at = 0.0
    if changed:
        _tag_redraw_all()


def _draw_background(x: float, y: float, width: float, height: float) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    vertices = (
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
    )
    previous_blend = gpu.state.blend_get()
    try:
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("color", (0.035, 0.035, 0.045, 0.88))
        batch_for_shader(shader, "TRI_FAN", {"pos": vertices}).draw(shader)
    finally:
        gpu.state.blend_set(previous_blend or "NONE")


def _draw_callback() -> None:
    if not _state.label:
        return
    if time.monotonic() >= _state.expires_at:
        clear()
        return
    context = bpy.context
    region = getattr(context, "region", None)
    if region is None or int(region.as_pointer()) != _state.region_pointer:
        return

    font_id = 0
    blf.size(font_id, _FONT_SIZE_PX)
    text_width, text_height = blf.dimensions(font_id, _state.label)
    box_width = text_width + _PAD_X_PX * 2.0
    box_height = text_height + _PAD_Y_PX * 2.0
    x = min(
        max(4.0, _state.x_px + _OFFSET_X_PX),
        max(4.0, float(region.width) - box_width - 4.0),
    )
    y = min(
        max(4.0, _state.y_px + _OFFSET_Y_PX),
        max(4.0, float(region.height) - box_height - 4.0),
    )
    _draw_background(x, y, box_width, box_height)
    blf.color(font_id, 0.92, 0.97, 1.0, 1.0)
    blf.position(font_id, x + _PAD_X_PX, y + _PAD_Y_PX, 0.0)
    blf.draw(font_id, _state.label)


def register() -> None:
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback,
            (),
            "WINDOW",
            "POST_PIXEL",
        )


def unregister() -> None:
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle = None
    clear()
