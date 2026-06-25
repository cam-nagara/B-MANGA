"""B-MANGA Line — カメラ関連の自動更新.

- カメラ距離による線幅補正
- カメラビュー外のライン非表示（パフォーマンス最適化）
- カメラ距離による内部線の表示制限
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .core import (
    GN_MODIFIER_NAME,
    MODIFIER_NAME,
    PROP_BASE_THICKNESS,
    PROP_REF_DISTANCE,
)


_updating = False


# ------------------------------------------------------------------
# カメラ画角ユーティリティ
# ------------------------------------------------------------------

def _get_camera_half_angle(cam_data, scene) -> float:
    """カメラの有効な半画角（対角線ベース, ラジアン）を取得.

    パースペクティブ・魚眼・エクイレクタングラー等に対応。
    """
    if cam_data.type == "PERSP":
        fov = cam_data.angle
        res_x = scene.render.resolution_x
        res_y = scene.render.resolution_y

        fit = cam_data.sensor_fit
        if fit == "HORIZONTAL" or (fit == "AUTO" and res_x >= res_y):
            half_w = math.tan(fov / 2)
            half_h = half_w * res_y / max(1, res_x)
        else:
            half_h = math.tan(fov / 2)
            half_w = half_h * res_x / max(1, res_y)
        return math.atan(math.sqrt(half_w ** 2 + half_h ** 2))

    if cam_data.type == "ORTHO":
        return math.pi

    if cam_data.type == "PANO":
        ptype = getattr(cam_data, "panorama_type", None)
        if ptype is None:
            cycles = getattr(cam_data, "cycles", None)
            if cycles is not None:
                ptype = getattr(cycles, "panorama_type", None)

        if ptype in (
            "FISHEYE_EQUISOLID",
            "FISHEYE_EQUIDISTANT",
            "FISHEYE_POLYNOMIAL",
        ):
            fov = getattr(cam_data, "fisheye_fov", None)
            if fov is None:
                cycles = getattr(cam_data, "cycles", None)
                if cycles is not None:
                    fov = getattr(cycles, "fisheye_fov", None)
            return (fov / 2) if fov is not None else math.pi

        if ptype == "EQUIRECTANGULAR":
            lon_min = getattr(cam_data, "longitude_min", -math.pi)
            lon_max = getattr(cam_data, "longitude_max", math.pi)
            lat_min = getattr(cam_data, "latitude_min", -math.pi / 2)
            lat_max = getattr(cam_data, "latitude_max", math.pi / 2)
            h = (lon_max - lon_min) / 2
            v = (lat_max - lat_min) / 2
            return min(math.pi, math.sqrt(h ** 2 + v ** 2))

    return math.pi


def _get_object_bound_radius(obj) -> float:
    """ワールドスケール込みのバウンディング球半径."""
    if not obj.bound_box:
        return 0.0
    bb = [Vector(corner) for corner in obj.bound_box]
    center = sum(bb, Vector()) / 8
    local_r = max((v - center).length for v in bb)
    scale = obj.matrix_world.to_scale()
    return local_r * max(abs(scale.x), abs(scale.y), abs(scale.z))


# ------------------------------------------------------------------
# 各機能の更新ロジック
# ------------------------------------------------------------------

def _update_camera_compensation(scene, settings, cam_loc):
    """カメラ距離に応じて Solidify thickness を補正."""
    if not settings.use_camera_compensation:
        return
    influence = settings.camera_compensation_influence
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        mod = obj.modifiers.get(MODIFIER_NAME)
        if mod is None:
            continue
        base_t = obj.get(PROP_BASE_THICKNESS)
        ref_d = obj.get(PROP_REF_DISTANCE)
        if base_t is None or ref_d is None or ref_d <= 0:
            continue
        dist = (cam_loc - obj.matrix_world.translation).length
        factor = dist / ref_d
        adjusted = base_t * (1.0 + (factor - 1.0) * influence)
        mod.thickness = -abs(adjusted)


def _update_visibility(scene, settings, camera, cam_loc, cam_fwd):
    """ビューカリングと内部線距離制限を統合処理."""
    do_culling = settings.use_camera_culling
    do_distance = settings.use_inner_line_distance_limit

    if not do_culling and not do_distance:
        return

    half_angle = margin = 0.0
    if do_culling:
        half_angle = _get_camera_half_angle(camera.data, scene)
        margin = settings.culling_margin

    max_dist = 0.0
    if do_distance:
        max_dist = settings.inner_line_max_distance

    for obj in scene.objects:
        if obj.type != "MESH":
            continue

        outline_mod = obj.modifiers.get(MODIFIER_NAME)
        inner_mod = obj.modifiers.get(GN_MODIFIER_NAME)
        if outline_mod is None and inner_mod is None:
            continue

        to_obj = obj.matrix_world.translation - cam_loc
        dist = to_obj.length

        # ビューカリング判定
        in_view = True
        if do_culling and dist >= 0.001:
            angle = cam_fwd.angle(to_obj)
            bound_r = _get_object_bound_radius(obj)
            angular_r = math.atan2(bound_r, dist)
            in_view = (angle - angular_r) < (half_angle + margin)

        # 内部線距離判定
        inner_in_range = True
        if do_distance:
            inner_in_range = dist <= max_dist

        if outline_mod is not None:
            outline_mod.show_viewport = in_view
            outline_mod.show_render = in_view

        if inner_mod is not None:
            inner_mod.show_viewport = in_view and inner_in_range
            inner_mod.show_render = in_view and inner_in_range


# ------------------------------------------------------------------
# ハンドラ
# ------------------------------------------------------------------

@bpy.app.handlers.persistent
def _on_frame_change(scene, depsgraph=None):
    global _updating
    if _updating:
        return
    _updating = True
    try:
        settings = getattr(scene, "bmanga_line_settings", None)
        if settings is None:
            return
        camera = scene.camera
        if camera is None:
            return

        cam_loc = camera.matrix_world.translation
        _update_camera_compensation(scene, settings, cam_loc)

        cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        _update_visibility(scene, settings, camera, cam_loc, cam_fwd)
    finally:
        _updating = False


# ------------------------------------------------------------------
# 公開 API
# ------------------------------------------------------------------

def refresh(context):
    """ビューポートで全カメラ関連機能を手動更新."""
    global _updating
    if _updating:
        return
    _updating = True
    try:
        scene = context.scene
        settings = getattr(scene, "bmanga_line_settings", None)
        if settings is None:
            return
        camera = scene.camera
        if camera is None:
            return
        cam_loc = camera.matrix_world.translation
        _update_camera_compensation(scene, settings, cam_loc)
        cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        _update_visibility(scene, settings, camera, cam_loc, cam_fwd)
    finally:
        _updating = False


def store_reference(obj, scene):
    """現在のカメラ距離と厚みを基準値として保存."""
    camera = scene.camera
    if camera is None:
        return False
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return False
    dist = (camera.matrix_world.translation - obj.matrix_world.translation).length
    obj[PROP_REF_DISTANCE] = max(dist, 0.001)
    obj[PROP_BASE_THICKNESS] = abs(mod.thickness)
    return True


def register() -> None:
    if _on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame_change)


def unregister() -> None:
    if _on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change)
