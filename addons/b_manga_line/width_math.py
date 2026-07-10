"""B-MANGA Liner の頂点単位線幅計算."""

from __future__ import annotations

import math

import numpy as np


def _render_size(scene) -> tuple[float, float]:
    from . import camera_comp

    return camera_comp._effective_render_size(scene)


def _target_pixels(scene, width_m: float) -> float:
    from . import camera_comp

    return camera_comp._target_pixels(scene, width_m)


def _panorama_radians_per_pixel(cam_data, scene, width: float, height: float) -> float:
    from . import camera_comp

    return camera_comp._panorama_radians_per_pixel(cam_data, scene, width, height)


def _world_per_pixel_from_depth(scene, camera, depth: np.ndarray) -> np.ndarray:
    width, height = _render_size(scene)
    cam_data = camera.data
    if cam_data.type == "ORTHO":
        return np.full_like(
            depth,
            max(1.0e-9, float(cam_data.ortho_scale) / max(1.0, width)),
            dtype=np.float64,
        )
    if cam_data.type == "PERSP":
        angle_y = float(getattr(cam_data, "angle_y", cam_data.angle))
        coeff = 2.0 * math.tan(angle_y * 0.5) / max(1.0, height)
        return np.maximum(1.0e-9, depth * coeff)
    coeff = _panorama_radians_per_pixel(cam_data, scene, width, height)
    return np.maximum(1.0e-9, depth * coeff)


def _vertex_depths(camera, world: np.ndarray) -> np.ndarray:
    inv = np.array(camera.matrix_world.inverted(), dtype=np.float64)
    local = world @ inv[:3, :3].T + inv[:3, 3]
    if camera.data.type == "PANO":
        return np.maximum(0.001, np.linalg.norm(local, axis=1))
    return np.maximum(0.001, -local[:, 2])


def vertex_world_positions(obj) -> np.ndarray:
    mesh = obj.data
    count = len(mesh.vertices)
    if count == 0:
        return np.empty((0, 3), dtype=np.float64)
    co = np.empty(count * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(count, 3)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    return co @ matrix[:3, :3].T + matrix[:3, 3]


def vertex_widths_and_depths(
    scene,
    camera,
    obj,
    width_m: float,
    *,
    distance_falloff: float = 0.0,
    reference_distance: float = 2.0,
    limit_to_setting: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """各頂点の目標ワールド線幅と距離を一括計算する."""
    world = vertex_world_positions(obj)
    if world.size == 0:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty

    depth = _vertex_depths(camera, world)
    setting_widths = _target_pixels(scene, width_m) * _world_per_pixel_from_depth(
        scene,
        camera,
        depth,
    )
    widths = setting_widths
    power = max(0.0, float(distance_falloff or 0.0))
    if power > 0.0:
        ref = max(0.001, float(reference_distance or 0.001))
        widths = widths * np.power(ref / depth, power)
    if limit_to_setting:
        widths = np.minimum(widths, setting_widths)
    return widths, depth
