"""用紙白背景のGPUオーバーレイ描画 (POST_VIEW, depth書込み)."""

from __future__ import annotations

import gpu
from gpu_extras.batch import batch_for_shader

from ..utils.geom import Rect, mm_to_m


def draw_for_page(
    paper,
    rects,
    ox_mm: float,
    oy_mm: float,
    is_spread: bool = False,
    spread_width_mm: float = 0.0,
) -> None:
    canvas = rects.canvas
    w = spread_width_mm if is_spread and spread_width_mm > 0.0 else canvas.width
    r = Rect(canvas.x + ox_mm, canvas.y + oy_mm, w, canvas.height)
    pc = paper.paper_color
    color = (float(pc[0]), float(pc[1]), float(pc[2]), 1.0)
    try:
        prev_mask = gpu.state.depth_mask_get()
    except Exception:  # noqa: BLE001
        prev_mask = False
    try:
        gpu.state.depth_mask_set(True)
        _draw_rect(r, color)
    finally:
        try:
            gpu.state.depth_mask_set(prev_mask)
        except Exception:  # noqa: BLE001
            pass


def _draw_rect(rect: Rect, color: tuple) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
    ]
    indices = [(0, 1, 2), (0, 2, 3)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
