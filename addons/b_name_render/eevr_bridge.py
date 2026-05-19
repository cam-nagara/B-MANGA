"""Bridge to eeVR-compatible rendering.

The original eeVR addon is GPL-3.0. B-Name-Render keeps this bridge small and
calls eeVR operators/properties when available.
"""

from __future__ import annotations

import os
from math import radians

import bpy
from mathutils import Vector

from . import bname_context


_ANGLE_EPS = 1.0e-6


def setup(scene, camera=None, *, output_dir: str = "", output_name: str = "") -> bool:
    props = getattr(scene, "eeVR", None)
    camera = camera or getattr(scene, "camera", None)
    fov = _fisheye_fov(scene, camera)
    stored_dir = bname_context.default_output_folder(
        scene,
        output_dir or str(scene.get("bname_render_fisheye_output_dir", "") or ""),
    )
    stored_name = bname_context.default_output_name(
        scene,
        output_name or str(scene.get("bname_render_fisheye_output_name", "") or ""),
    )
    projection = bname_context.camera_fisheye_projection(camera)
    dome_method = bname_context.eevr_dome_method_for_projection(projection)
    if props is not None:
        if not dome_method:
            if bname_context.scene_context(scene).is_bname_coma:
                scene["bname_render_fisheye_warning"] = "対応する魚眼投影方式が見つかりません"
                return False
            dome_method = "2"
        setattr(props, "renderModeEnum", "DOME")
        setattr(props, "domeMethodEnum", dome_method)
        _set_eevr_fov(props, fov)
        try:
            props.VFOV = min(max(float(fov), radians(1)), radians(360))
        except Exception:  # noqa: BLE001
            pass
        if hasattr(props, "save_images_to_directory"):
            props.save_images_to_directory = True
        if hasattr(props, "images_save_directory"):
            props.images_save_directory = stored_dir
    _configure_native_fisheye(scene, camera, fov)
    if output_dir:
        scene["outputFolderName"] = output_dir
    if output_name:
        scene["outputImageName"] = output_name
    scene["bname_render_fisheye_output_dir"] = stored_dir
    scene["bname_render_fisheye_output_name"] = stored_name
    return True


def _fisheye_fov(scene, camera) -> float:
    camera_data = getattr(camera, "data", None)
    value = getattr(camera_data, "fisheye_fov", None)
    if bool(getattr(scene, "bname_coma_camera_fisheye_layout_mode", False)):
        value = getattr(scene, "bname_coma_camera_fisheye_fov", value)
    if value is None:
        value = getattr(scene, "fisheye_fov", radians(180))
    return min(max(float(value or radians(180)), radians(1)), radians(360))


def _configure_native_fisheye(scene, camera, fov: float) -> None:
    camera = camera or getattr(scene, "camera", None)
    camera_data = getattr(camera, "data", None)
    if camera_data is None:
        return
    try:
        camera_data.type = "PANO"
        if hasattr(camera_data, "fisheye_fov"):
            camera_data.fisheye_fov = float(fov)
    except Exception:  # noqa: BLE001
        pass


def _set_eevr_fov(props, fov: float) -> None:
    fov = min(max(float(fov), radians(1)), radians(360))
    try:
        if fov <= radians(180) + _ANGLE_EPS:
            props.fovModeEnum = "180"
            props.HFOV180 = fov
        elif fov >= radians(360) - _ANGLE_EPS:
            props.fovModeEnum = "360"
            props.HFOV360 = fov
        else:
            props.fovModeEnum = "ANY"
            props.HFOV360 = fov
    except Exception:  # noqa: BLE001
        pass


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


def _try_external_operator(idname: str) -> set[str] | None:
    try:
        return run_operator(idname)
    except RuntimeError:
        return None


def _output_base(scene, suffix: str) -> str:
    folder = bname_context.default_output_folder(
        scene,
        str(scene.get("bname_render_fisheye_output_dir", "") or ""),
    )
    name = bname_context.default_output_name(
        scene,
        str(scene.get("bname_render_fisheye_output_name", "") or ""),
    )
    directory = bpy.path.abspath(folder)
    os.makedirs(directory, exist_ok=True)
    safe_suffix = f"_{suffix}" if suffix else ""
    return os.path.join(directory, f"{name}{safe_suffix}")


def _native_render(scene, suffix: str = "") -> set[str]:
    setup(scene, getattr(scene, "camera", None))
    previous_path = str(getattr(scene.render, "filepath", "") or "")
    previous_format = str(getattr(scene.render.image_settings, "file_format", "") or "")
    try:
        scene.render.filepath = _output_base(scene, suffix)
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True)
    finally:
        scene.render.filepath = previous_path
        if previous_format:
            scene.render.image_settings.file_format = previous_format
    return {"FINISHED"}


def render_image() -> set[str]:
    result = _try_external_operator("eevr.render_image")
    if result is not None:
        return result
    return _native_render(bpy.context.scene, "image")


def render_faces() -> set[str]:
    result = _try_external_operator("eevr.render_faces")
    if result is not None:
        return result
    scene = bpy.context.scene
    camera = getattr(scene, "camera", None)
    if camera is None:
        return _native_render(scene, "faces")
    original_rotation = camera.rotation_euler.copy()
    directions = (
        ("front", Vector((0.0, -1.0, 0.0))),
        ("right", Vector((1.0, 0.0, 0.0))),
        ("back", Vector((0.0, 1.0, 0.0))),
        ("left", Vector((-1.0, 0.0, 0.0))),
        ("top", Vector((0.0, 0.0, 1.0))),
        ("bottom", Vector((0.0, 0.0, -1.0))),
    )
    try:
        for suffix, direction in directions:
            camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            _native_render(scene, suffix)
    finally:
        camera.rotation_euler = original_rotation
    return {"FINISHED"}


def assemble_images() -> set[str]:
    result = _try_external_operator("eevr.assemble_images")
    if result is not None:
        return result
    return _native_render(bpy.context.scene, "assembled")
