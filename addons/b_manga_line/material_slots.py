"""Material-slot helpers for Geometry Nodes compatible padding bands."""

from __future__ import annotations

from collections.abc import Callable

import bpy


PADDING_SLOT_PROP = "bml_material_padding_slot"


def _pointer(material: bpy.types.Material | None) -> int | None:
    if material is None:
        return None
    try:
        return material.as_pointer()
    except ReferenceError:
        return None


def _padding_copy(base: bpy.types.Material, key: str) -> bpy.types.Material:
    material = base.copy()
    material[PADDING_SLOT_PROP] = key
    return material


def _remove_replaced_padding(material: bpy.types.Material | None) -> None:
    if material is None or not bool(material.get(PADDING_SLOT_PROP, "")):
        return
    if material.users == 0:
        bpy.data.materials.remove(material)


def ensure_unique_band(
    obj: bpy.types.Object,
    start: int,
    end: int,
    base: bpy.types.Material,
    *,
    key: str,
    matches: Callable[[bpy.types.Material | None], bool],
    sync: Callable[[bpy.types.Material], None] | None = None,
) -> None:
    """Fill a slot band without duplicate material datablock pointers.

    Geometry Nodes deduplicates identical material pointers while joining
    geometry.  Distinct padding datablocks keep polygon material indices stable
    when several generated-line modifiers are stacked.
    """
    materials = obj.data.materials
    while len(materials) < end:
        materials.append(base)
    used = {
        pointer
        for material in list(materials)[:start]
        if (pointer := _pointer(material)) is not None
    }
    for index in range(start, end):
        current = materials[index]
        current_pointer = _pointer(current)
        if current is not None and matches(current) and current_pointer not in used:
            candidate = current
        elif _pointer(base) not in used:
            candidate = base
        else:
            candidate = _padding_copy(base, f"{key}:{index - start}")
        if sync is not None:
            sync(candidate)
        candidate_pointer = _pointer(candidate)
        if candidate_pointer is not None:
            used.add(candidate_pointer)
        if current != candidate:
            materials[index] = candidate
            _remove_replaced_padding(current)
