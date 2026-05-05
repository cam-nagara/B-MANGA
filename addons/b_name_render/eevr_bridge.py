"""Bridge to eeVR-compatible rendering.

The original eeVR addon is GPL-3.0. B-Name-Render keeps this bridge small and
calls eeVR operators/properties when available.
"""

from __future__ import annotations

from math import radians

import bpy


def setup(scene, camera=None) -> bool:
    props = getattr(scene, "eeVR", None)
    if props is None:
        return False
    camera = camera or getattr(scene, "camera", None)
    setattr(props, "renderModeEnum", "DOME")
    setattr(props, "domeMethodEnum", "1")
    setattr(props, "fovModeEnum", "180")
    fov = getattr(getattr(camera, "data", None), "fisheye_fov", radians(180))
    try:
        props.HFOV180 = min(max(float(fov), radians(1)), radians(180))
        props.VFOV = radians(180)
    except Exception:  # noqa: BLE001
        pass
    if hasattr(props, "save_images_to_directory"):
        props.save_images_to_directory = True
    if hasattr(props, "images_save_directory"):
        props.images_save_directory = "//passes/"
    return True


def run_operator(idname: str) -> set[str]:
    if not idname:
        return {"CANCELLED"}
    try:
        module, op = idname.split(".", 1)
        ops_module = getattr(bpy.ops, module)
        callable_op = getattr(ops_module, op)
        return callable_op()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"オペレータを実行できません: {idname}") from exc


def render_image() -> set[str]:
    return run_operator("eevr.render_image")


def render_faces() -> set[str]:
    return run_operator("eevr.render_faces")


def assemble_images() -> set[str]:
    return run_operator("eevr.assemble_images")
