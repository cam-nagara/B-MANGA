"""B-Name-Render — render preset and fisheye output addon."""

from __future__ import annotations

bl_info = {
    "name": "B-Name-Render",
    "author": "B-Name Project",
    "version": (0, 1, 25),
    "blender": (4, 3, 0),
    "description": "Command based render presets and fisheye output for B-Name workflows",
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
