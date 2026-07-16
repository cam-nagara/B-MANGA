"""B-MANGA Render — render preset and fisheye output addon."""

from __future__ import annotations

bl_info = {
    "name": "B-MANGA Render",
    "author": "B-MANGA Project",
    "version": (0, 1, 36),
    "blender": (4, 3, 0),
    "description": "Command based render presets and fisheye output for B-MANGA workflows",
    "category": "Render",
}

from . import core
from . import operators
from . import panels

_MODULES = (
    core,
    operators,
    panels,
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        module.unregister()
