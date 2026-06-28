"""B-MANGA Line registration helpers."""

from __future__ import annotations

import bpy


def _registered_class(cls):
    registered = getattr(bpy.types, cls.__name__, None)
    if registered is not None:
        return registered
    for base_name in ("PropertyGroup", "Operator", "Panel", "UIList", "Menu"):
        base = getattr(bpy.types, base_name, None)
        finder = getattr(base, "bl_rna_get_subclass_py", None)
        if finder is None:
            continue
        try:
            registered = finder(cls.__name__, None)
        except TypeError:
            try:
                registered = finder(cls.__name__)
            except Exception:  # noqa: BLE001
                registered = None
        except Exception:  # noqa: BLE001
            registered = None
        if registered is not None:
            return registered
    return cls if bool(getattr(cls, "is_registered", False)) else None


def unregister_class(cls) -> None:
    target = _registered_class(cls)
    if target is None:
        return
    try:
        bpy.utils.unregister_class(target)
    except RuntimeError:
        pass


def register_class(cls) -> None:
    unregister_class(cls)
    bpy.utils.register_class(cls)
