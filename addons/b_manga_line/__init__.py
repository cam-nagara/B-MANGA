"""B-MANGA Liner — 背面法（反転ハル）によるマンガ風アウトラインアドオン."""

from __future__ import annotations

bl_info = {
    "name": "B-MANGA Liner",
    "author": "B-MANGA Project",
    "version": (0, 3, 156),
    "blender": (4, 3, 0),
    "description": "背面法（反転ハル）によるマンガ風アウトライン",
    "category": "Render",
}

from . import core
from . import inner_lines
from . import selection_lines
from . import outline_setup
from . import auto_smooth_guard
from . import camera_comp
from . import edge_width_curve
from . import intersection_lines
from . import subdivision_lod
from . import operators
from . import presets
from . import panels

_MODULES = (
    core,
    inner_lines,
    selection_lines,
    outline_setup,
    auto_smooth_guard,
    camera_comp,
    edge_width_curve,
    subdivision_lod,
    operators,
    presets,
    panels,
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    intersection_lines.cancel_deferred_viewport_refresh()
    for module in reversed(_MODULES):
        module.unregister()
