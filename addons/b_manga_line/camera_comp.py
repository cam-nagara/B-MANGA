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
    INTERSECTION_MODIFIER_NAME,
    MODIFIER_NAME,
    PROP_BASE_THICKNESS,
    PROP_LINES_HIDDEN,
    PROP_REF_DISTANCE,
    PROP_REF_FOV_TAN,
    PROP_REF_MODE,
    REF_MODE_LOCKED,
    REF_MODE_VIEW,
    VG_LINE_WIDTH,
)


_updating = False
_FALLBACK_DPI = 600


def get_line_camera(scene) -> bpy.types.Object | None:
    """B-MANGA Line が基準にするカメラを返す."""
    camera = getattr(scene, "bmanga_line_camera", None)
    if camera is not None and getattr(camera, "type", None) == "CAMERA":
        return camera
    return scene.camera


# ------------------------------------------------------------------
# カメラ画角ユーティリティ
# ------------------------------------------------------------------

def _get_camera_half_angle(cam_data, scene) -> float:
    """カメラの有効な半画角（対角線ベース, ラジアン）を取得."""
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
# FOV スケール
# ------------------------------------------------------------------

def _get_fov_factor(cam_data, scene) -> float:
    """カメラの FOV に比例するスケール値を返す.

    PERSP/PANO: tan(半画角) — 広角ほど大きい
    ORTHO: ortho_scale — ビュー範囲が広いほど大きい
    """
    if cam_data.type == "ORTHO":
        return cam_data.ortho_scale
    half = _get_camera_half_angle(cam_data, scene)
    t = math.tan(half)
    return t if t > 1e-6 else 1.0


def _effective_render_size(scene) -> tuple[float, float]:
    render = scene.render
    scale = max(0.001, float(getattr(render, "resolution_percentage", 100)) / 100.0)
    width = max(1.0, float(getattr(render, "resolution_x", 1)) * scale)
    height = max(1.0, float(getattr(render, "resolution_y", 1)) * scale)
    return width, height


def _get_scene_dpi(scene) -> float:
    work = getattr(scene, "bmanga_work", None)
    paper = getattr(work, "paper", None) if work else None
    dpi = float(getattr(paper, "dpi", 0.0) or 0.0) if paper else 0.0
    return dpi if dpi > 0.0 else float(_FALLBACK_DPI)


def _target_pixels(scene, width_m: float) -> float:
    width_mm = max(0.0, float(width_m) * 1000.0)
    return max(0.001, width_mm * _get_scene_dpi(scene) / 25.4)


def _world_per_pixel(scene, camera, world_co: Vector) -> float:
    _, height = _effective_render_size(scene)
    cam_data = camera.data
    if cam_data.type == "ORTHO":
        return max(1.0e-9, float(cam_data.ortho_scale) / height)

    local = camera.matrix_world.inverted() @ world_co
    depth = max(0.001, -float(local.z))
    if cam_data.type == "PERSP":
        angle_y = float(getattr(cam_data, "angle_y", cam_data.angle))
        view_height = 2.0 * depth * math.tan(angle_y * 0.5)
        return max(1.0e-9, view_height / height)

    width, _ = _effective_render_size(scene)
    half = _get_camera_half_angle(cam_data, scene)
    view_diag = 2.0 * depth * math.tan(half)
    pixel_diag = math.hypot(width, height)
    return max(1.0e-9, view_diag / pixel_diag)


def _uniform_widths_for_mesh(scene, camera, obj, width_m: float) -> list[float]:
    target_px = _target_pixels(scene, width_m)
    matrix = obj.matrix_world
    return [
        target_px * _world_per_pixel(scene, camera, matrix @ vertex.co)
        for vertex in obj.data.vertices
    ]


def _has_style_width_weights(settings) -> bool:
    return (
        settings.use_vertex_color
        or settings.use_ao_influence
        or abs(settings.edge_smooth_factor) > 0.001
    )


def _prepare_style_weights(obj, settings) -> None:
    from . import vertex_analysis

    if _has_style_width_weights(settings):
        vertex_analysis.compute_and_apply_weights(obj, settings)
    else:
        vertex_analysis.reset_width_weights(obj)


def _apply_uniform_line_width(scene, camera, obj, settings, mod) -> None:
    from . import inner_lines, intersection_lines, vertex_analysis

    if not obj.data.vertices:
        return

    _prepare_style_weights(obj, settings)
    outline_widths = _uniform_widths_for_mesh(
        scene, camera, obj, settings.outline_thickness,
    )
    max_outline = max(max(outline_widths), 1.0e-9)
    mod.thickness = max_outline
    mod.vertex_group = VG_LINE_WIDTH
    mod.thickness_vertex_group = 0.0
    vertex_analysis.multiply_width_weights(
        obj,
        [width / max_outline for width in outline_widths],
    )

    outline_base = max(abs(float(settings.outline_thickness)), 1.0e-9)
    inner_scale = abs(float(settings.inner_line_thickness)) / outline_base
    intersection_scale = abs(float(settings.intersection_thickness)) / outline_base
    inner_lines.update_parameters(obj, thickness=max_outline * inner_scale)
    intersection_lines.update_parameters(
        obj,
        thickness=max_outline * intersection_scale,
    )


# ------------------------------------------------------------------
# 各機能の更新ロジック（オブジェクトごとの設定を参照）
# ------------------------------------------------------------------

def _update_camera_compensation(scene, camera):
    """カメラ距離 + FOV に応じて Solidify thickness を補正."""
    from . import inner_lines, intersection_lines

    cam_loc = camera.matrix_world.translation
    current_fov = _get_fov_factor(camera.data, scene)

    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is None:
            continue
        mod = obj.modifiers.get(MODIFIER_NAME)
        if mod is None:
            continue
        if settings.use_uniform_line_width:
            _apply_uniform_line_width(scene, camera, obj, settings, mod)
            continue
        if not settings.use_camera_compensation:
            continue
        influence = settings.camera_compensation_influence
        base_t = settings.outline_thickness
        ref_d = obj.get(PROP_REF_DISTANCE, 1.0)
        if ref_d <= 0:
            ref_d = 1.0
        dist = (cam_loc - obj.matrix_world.translation).length
        factor = dist / ref_d
        mode = obj.get(PROP_REF_MODE)
        if mode is None:
            mode = REF_MODE_VIEW if abs(ref_d - 1.0) < 1e-6 else REF_MODE_LOCKED
        ref_fov = obj.get(PROP_REF_FOV_TAN)
        if mode == REF_MODE_LOCKED and ref_fov and ref_fov > 0:
            factor *= current_fov / ref_fov
        adjusted = base_t * (1.0 + (factor - 1.0) * influence)
        mod.thickness = abs(adjusted)

        inner_adjusted = settings.inner_line_thickness * (
            1.0 + (factor - 1.0) * influence
        )
        intersection_adjusted = settings.intersection_thickness * (
            1.0 + (factor - 1.0) * influence
        )
        inner_lines.update_parameters(obj, thickness=abs(inner_adjusted))
        intersection_lines.update_parameters(obj, thickness=abs(intersection_adjusted))


def _update_visibility(scene, camera, cam_loc, cam_fwd):
    """ビューカリングと内部線距離制限を統合処理."""
    half_angle_cache = None

    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is None:
            continue

        do_culling = settings.use_camera_culling
        do_distance = settings.use_inner_line_distance_limit
        if not do_culling and not do_distance:
            continue

        outline_mod = obj.modifiers.get(MODIFIER_NAME)
        inner_mod = obj.modifiers.get(GN_MODIFIER_NAME)
        intersection_mod = obj.modifiers.get(INTERSECTION_MODIFIER_NAME)
        if outline_mod is None and inner_mod is None and intersection_mod is None:
            continue

        if bool(obj.get(PROP_LINES_HIDDEN, False)):
            for mod in (outline_mod, inner_mod, intersection_mod):
                if mod is not None:
                    mod.show_viewport = False
                    mod.show_render = False
            continue

        to_obj = obj.matrix_world.translation - cam_loc
        dist = to_obj.length

        # ビューカリング判定
        in_view = True
        if do_culling and dist >= 0.001:
            if half_angle_cache is None:
                half_angle_cache = _get_camera_half_angle(camera.data, scene)
            margin = settings.culling_margin
            angle = cam_fwd.angle(to_obj)
            bound_r = _get_object_bound_radius(obj)
            angular_r = math.atan2(bound_r, dist)
            in_view = (angle - angular_r) < (half_angle_cache + margin)

        # 内部線距離判定
        inner_in_range = True
        if do_distance:
            inner_in_range = dist <= settings.inner_line_max_distance

        if outline_mod is not None:
            outline_mod.show_viewport = in_view
            outline_mod.show_render = in_view

        if intersection_mod is not None:
            intersection_mod.show_viewport = in_view
            intersection_mod.show_render = in_view

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
        camera = get_line_camera(scene)
        if camera is None:
            return

        _update_camera_compensation(scene, camera)

        cam_loc = camera.matrix_world.translation
        cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        _update_visibility(scene, camera, cam_loc, cam_fwd)
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
        camera = get_line_camera(scene)
        if camera is None:
            return
        _update_camera_compensation(scene, camera)
        cam_loc = camera.matrix_world.translation
        cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        _update_visibility(scene, camera, cam_loc, cam_fwd)
    finally:
        _updating = False


def store_reference(obj, scene):
    """現在のカメラ距離・厚み・FOV を基準値として保存."""
    camera = get_line_camera(scene)
    if camera is None:
        return False
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return False
    dist = (camera.matrix_world.translation - obj.matrix_world.translation).length
    obj[PROP_REF_DISTANCE] = max(dist, 0.001)
    settings = getattr(obj, "bmanga_line_settings", None)
    obj[PROP_BASE_THICKNESS] = settings.outline_thickness if settings else abs(mod.thickness)
    obj[PROP_REF_FOV_TAN] = _get_fov_factor(camera.data, scene)
    obj[PROP_REF_MODE] = REF_MODE_LOCKED
    return True


def store_unit_reference(obj, scene):
    """カメラビューのカメラを基準に、1m距離を補正基準として保存."""
    camera = get_line_camera(scene)
    if camera is None:
        return False
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    obj[PROP_REF_DISTANCE] = 1.0
    obj[PROP_BASE_THICKNESS] = settings.outline_thickness if settings else abs(mod.thickness)
    obj[PROP_REF_FOV_TAN] = 1.0
    obj[PROP_REF_MODE] = REF_MODE_VIEW
    return True


def register() -> None:
    if _on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame_change)


def unregister() -> None:
    if _on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change)
