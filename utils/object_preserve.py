"""Helpers for preserving existing Blender objects during compatibility sync."""

from __future__ import annotations

import bpy

from . import object_naming as on

PROP_PRESERVED = "bname_preserved_external_object"
PROP_PRESERVE_REASON = "bname_preserve_reason"


def preserve_object(obj: bpy.types.Object | None, reason: str = "") -> bool:
    """Keep an object in the file but detach it from automatic B-Name rewrites."""
    if obj is None:
        return False
    try:
        obj[PROP_PRESERVED] = True
        obj[PROP_PRESERVE_REASON] = str(reason or "")
        obj[on.PROP_MANAGED] = False
        obj[on.PROP_NO_NORMALIZE] = True
        obj.hide_select = False
    except Exception:  # noqa: BLE001
        return False
    return True


def is_preserved(obj: bpy.types.Object | None) -> bool:
    return bool(obj is not None and obj.get(PROP_PRESERVED, False))
