"""B-MANGA Line — 背面法（反転ハル）によるマンガ風アウトラインアドオン."""

from __future__ import annotations

bl_info = {
    "name": "B-MANGA Line",
    "author": "B-MANGA Project",
    "version": (0, 3, 1),
    "blender": (4, 3, 0),
    "description": "背面法（反転ハル）によるマンガ風アウトライン",
    "category": "Render",
}

from . import core
from . import camera_comp
from . import operators
from . import panels

_MODULES = (
    core,
    camera_comp,
    operators,
    panels,
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        module.unregister()
