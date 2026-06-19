"""カメラビュー内の表示パン/ズーム/回転補助."""

from __future__ import annotations

import math

import bpy

_CAMERA_ZOOM_MIN = -30.0
_CAMERA_ZOOM_MAX = 600.0
_CAMERA_ZOOM_DRAG_SCALE = 0.18
_CAMERA_ZOOM_STEP = 8.0
_CAMERA_ZOOM_DEADZONE_PX = 3.0
_OVERVIEW_CAMERA_PROP = "bmanga_overview_camera"


def is_camera_view(rv3d) -> bool:
    return str(getattr(rv3d, "view_perspective", "")) == "CAMERA"


def pan(rv3d, region, dx_px: float, dy_px: float) -> bool:
    if not is_camera_view(rv3d):
        return False
    frame_w, frame_h = _camera_frame_size_px(region, rv3d)
    ox, oy = _camera_offset(rv3d)
    rv3d.view_camera_offset = (
        ox - float(dx_px) / max(1.0, frame_w * 2.0),
        oy - float(dy_px) / max(1.0, frame_h * 2.0),
    )
    _update(rv3d)
    return True


def zoom_absolute(rv3d, start_zoom: float, dx_px: float) -> bool:
    if not is_camera_view(rv3d):
        return False
    dx = float(dx_px)
    if abs(dx) < _CAMERA_ZOOM_DEADZONE_PX:
        rv3d.view_camera_zoom = _clamp_zoom(start_zoom)
        _update(rv3d)
        return True
    signed_dx = dx - _CAMERA_ZOOM_DEADZONE_PX * (1.0 if dx > 0.0 else -1.0)
    rv3d.view_camera_zoom = _clamp_zoom(float(start_zoom) + signed_dx * _CAMERA_ZOOM_DRAG_SCALE)
    _update(rv3d)
    return True


def step_zoom(rv3d, direction: str) -> bool:
    if not is_camera_view(rv3d):
        return False
    zoom = float(getattr(rv3d, "view_camera_zoom", 0.0) or 0.0)
    delta = _CAMERA_ZOOM_STEP if direction == "IN" else -_CAMERA_ZOOM_STEP
    rv3d.view_camera_zoom = _clamp_zoom(zoom + delta)
    _update(rv3d)
    return True


def reset_view(rv3d) -> bool:
    if not is_camera_view(rv3d):
        return False
    rv3d.view_camera_zoom = _CAMERA_ZOOM_MIN
    rv3d.view_camera_offset = (0.0, 0.0)
    _update(rv3d)
    return True


def rotate_overview_camera(rv3d, delta_angle: float) -> bool:
    camera = _overview_camera(rv3d)
    if camera is None:
        return False
    camera.rotation_euler = (0.0, 0.0, _wrap_angle(float(camera.rotation_euler.z) + float(delta_angle)))
    _update(rv3d)
    return True


def reset_overview_camera_rotation(rv3d) -> bool:
    camera = _overview_camera(rv3d)
    if camera is None:
        return False
    camera.rotation_euler = (0.0, 0.0, 0.0)
    _update(rv3d)
    return True


def _overview_camera(rv3d):
    if not is_camera_view(rv3d):
        return None
    scene = bpy.context.scene
    camera = getattr(scene, "camera", None)
    if camera is None or getattr(camera, "type", "") != "CAMERA":
        return None
    try:
        if not bool(camera.get(_OVERVIEW_CAMERA_PROP, False)):
            return None
    except Exception:  # noqa: BLE001
        return None
    return camera


def _camera_offset(rv3d) -> tuple[float, float]:
    value = getattr(rv3d, "view_camera_offset", (0.0, 0.0)) or (0.0, 0.0)
    return (float(value[0]), float(value[1]))


def _camera_frame_size_px(region, rv3d) -> tuple[float, float]:
    scene = bpy.context.scene
    camera = getattr(scene, "camera", None)
    if camera is None or getattr(camera, "type", "") != "CAMERA":
        return _fallback_frame_size(region)
    try:
        from bpy_extras.view3d_utils import location_3d_to_region_2d

        frame = [camera.matrix_world @ corner for corner in camera.data.view_frame(scene=scene)]
        projected = [location_3d_to_region_2d(region, rv3d, corner) for corner in frame]
        xs = [float(point.x) for point in projected if point is not None]
        ys = [float(point.y) for point in projected if point is not None]
        if len(xs) >= 2 and len(ys) >= 2:
            return (
                max(1.0, max(xs) - min(xs)),
                max(1.0, max(ys) - min(ys)),
            )
    except Exception:  # noqa: BLE001
        pass
    return _fallback_frame_size(region)


def _fallback_frame_size(region) -> tuple[float, float]:
    return (
        max(1.0, float(getattr(region, "width", 1)) * 0.5),
        max(1.0, float(getattr(region, "height", 1)) * 0.5),
    )


def _clamp_zoom(value: float) -> float:
    return max(_CAMERA_ZOOM_MIN, min(_CAMERA_ZOOM_MAX, float(value)))


def _wrap_angle(value: float) -> float:
    return (float(value) + math.pi) % (math.pi * 2.0) - math.pi


def _update(rv3d) -> None:
    try:
        bpy.context.view_layer.update()
    except Exception:  # noqa: BLE001
        pass
    try:
        rv3d.update()
    except Exception:  # noqa: BLE001
        pass
