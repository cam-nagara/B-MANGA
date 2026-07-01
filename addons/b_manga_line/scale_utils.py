"""Scale conversion helpers for B-MANGA Line widths."""

from __future__ import annotations

import bpy


_MIN_SCALE = 1.0e-9


def object_width_scale(obj: bpy.types.Object) -> float:
    """Return the object scale that converts local modifier width to world width."""
    scale = obj.matrix_world.to_scale()
    values = sorted(
        abs(float(value))
        for value in (scale.x, scale.y, scale.z)
        if abs(float(value)) > _MIN_SCALE
    )
    if not values:
        return 1.0
    return max(values[len(values) // 2], _MIN_SCALE)


def modifier_thickness_for_world_width(
    obj: bpy.types.Object,
    world_width: float,
) -> float:
    """Convert a world-space line width to a local modifier thickness."""
    return abs(float(world_width)) / object_width_scale(obj)


def world_width_from_modifier(
    obj: bpy.types.Object,
    modifier_thickness: float,
) -> float:
    """Convert local modifier thickness to approximate world-space width."""
    return abs(float(modifier_thickness)) * object_width_scale(obj)
