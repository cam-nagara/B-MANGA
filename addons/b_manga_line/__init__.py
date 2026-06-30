"""B-MANGA Line — 背面法（反転ハル）によるマンガ風アウトラインアドオン."""

from __future__ import annotations

bl_info = {
    "name": "B-MANGA Line",
    "author": "B-MANGA Project",
    "version": (0, 3, 40),
    "blender": (4, 3, 0),
    "description": "背面法（反転ハル）によるマンガ風アウトライン",
    "category": "Render",
}

from . import core
from . import outline_setup
from . import camera_comp
from . import operators
from . import presets
from . import panels

_MODULES = (
    core,
    outline_setup,
    camera_comp,
    operators,
    presets,
    panels,
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        module.unregister()
