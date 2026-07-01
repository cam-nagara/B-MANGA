"""B-MANGA Line — カメラ関連の自動更新.

- カメラ距離による線幅補正
- カメラビュー外のライン非表示（パフォーマンス最適化）
- カメラ距離による線種別の表示制限
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .core import (
    DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
    GN_MODIFIER_NAME,
    MODIFIER_NAME,
    PROP_LINES_HIDDEN,
    PROP_BASE_THICKNESS,
    PROP_REF_DISTANCE,
    PROP_REF_FOV_TAN,
    PROP_REF_MODE,
    REF_MODE_LOCKED,
    REF_MODE_VIEW,
    VG_INNER_LINE_WIDTH,
    VG_INTERSECTION_LINE_WIDTH,
    VG_LINE_WIDTH,
    iter_intersection_modifiers,
)
from .scale_utils import modifier_thickness_for_world_width


_updating = False
_FALLBACK_DPI = 600


def get_line_camera(scene) -> bpy.types.Object | None:
    """B-MANGA Line が基準にするカメラを返す."""
    if scene is None:
        return None
    camera = getattr(scene, "bmanga_line_camera", None)
    if camera is not None and getattr(camera, "type", None) == "CAMERA":
        return camera
    return scene.camera


def object_distance_from_camera(obj: bpy.types.Object, camera: bpy.types.Object) -> float:
    """カメラ位置からオブジェクトのワールド境界までの最短距離."""
    cam_loc = camera.matrix_world.translation
    if obj.bound_box:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        min_x = min(corner.x for corner in corners)
        max_x = max(corner.x for corner in corners)
        min_y = min(corner.y for corner in corners)
        max_y = max(corner.y for corner in corners)
        min_z = min(corner.z for corner in corners)
        max_z = max(corner.z for corner in corners)
        closest = Vector((
            min(max(cam_loc.x, min_x), max_x),
            min(max(cam_loc.y, min_y), max_y),
            min(max(cam_loc.z, min_z), max_z),
        ))
        return (closest - cam_loc).length
    return (obj.matrix_world.translation - cam_loc).length


def inner_line_creation_in_range(obj: bpy.types.Object, scene, settings=None) -> bool:
    """内部線を作成してよいカメラ距離内か判定."""
    if settings is None:
        settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None:
        return True
    if not getattr(settings, "use_inner_line_creation_limit", False):
        return True
    camera = get_line_camera(scene)
    if camera is None:
        return True
    limit = max(0.0, float(getattr(settings, "inner_line_creation_max_distance", 10.0)))
    return object_distance_from_camera(obj, camera) <= limit


def intersection_line_creation_in_range(obj: bpy.types.Object, scene, settings=None) -> bool:
    """交差線を作成してよいカメラ距離内か判定."""
    if settings is None:
        settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None:
        return True
    if not getattr(settings, "use_intersection_creation_limit", False):
        return True
    camera = get_line_camera(scene)
    if camera is None:
        return True
    limit = max(0.0, float(getattr(settings, "intersection_creation_max_distance", 10.0)))
    return object_distance_from_camera(obj, camera) <= limit


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


def _reference_point_for_mesh(obj) -> Vector:
    if obj.bound_box:
        corners = [Vector(corner) for corner in obj.bound_box]
        local_center = sum(corners, Vector()) / 8
        return obj.matrix_world @ local_center
    return obj.matrix_world.translation


def _line_width_reference_distance(settings) -> float:
    raw = getattr(
        settings,
        "line_width_reference_distance",
        DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
    )
    return max(0.001, float(raw or DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE))


def _reference_point_for_distance(camera, distance: float) -> Vector:
    cam_loc = camera.matrix_world.translation
    cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    return cam_loc + cam_fwd * max(0.001, float(distance))


def _reference_width_for_distance(
    scene,
    camera,
    width_m: float,
    distance: float,
) -> float:
    target_px = _target_pixels(scene, width_m)
    return target_px * _world_per_pixel(
        scene,
        camera,
        _reference_point_for_distance(camera, distance),
    )


def _reference_width_for_mesh(scene, camera, obj, width_m: float) -> float:
    target_px = _target_pixels(scene, width_m)
    return target_px * _world_per_pixel(
        scene,
        camera,
        _reference_point_for_mesh(obj),
    )


def _prepare_style_weights(obj, settings, target: str) -> bool:
    from . import vertex_analysis

    group_name = vertex_analysis.width_group_name(target)
    if vertex_analysis.has_width_controls(settings, target):
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
        return True
    vertex_analysis.clear_width_weights(obj, group_name=group_name)
    return False


def _apply_uniform_line_width(scene, camera, obj, settings, mod) -> None:
    from . import inner_lines, intersection_lines, vertex_analysis

    if not obj.data.vertices:
        return

    _prepare_style_weights(obj, settings, "outline")
    outline_widths = _uniform_widths_for_mesh(
        scene, camera, obj, settings.outline_thickness,
    )
    max_outline_world = max(max(outline_widths), 1.0e-9)
    mod.thickness = modifier_thickness_for_world_width(obj, max_outline_world)
    mod.vertex_group = VG_LINE_WIDTH
    mod.thickness_vertex_group = 0.0
    vertex_analysis.multiply_width_weights(
        obj,
        [width / max_outline_world for width in outline_widths],
        group_name=VG_LINE_WIDTH,
    )

    has_inner = _has_inner_modifier(obj)
    has_intersection = _has_intersection_modifier(obj)
    if has_inner:
        _prepare_style_weights(obj, settings, "inner")
        vertex_analysis.multiply_width_weights(
            obj,
            [width / max_outline_world for width in outline_widths],
            group_name=VG_INNER_LINE_WIDTH,
        )
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INNER_LINE_WIDTH)

    if has_intersection:
        _prepare_style_weights(obj, settings, "intersection")
        vertex_analysis.multiply_width_weights(
            obj,
            [width / max_outline_world for width in outline_widths],
            group_name=VG_INTERSECTION_LINE_WIDTH,
        )
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INTERSECTION_LINE_WIDTH)

    outline_base = max(abs(float(settings.outline_thickness)), 1.0e-9)
    inner_scale = abs(float(settings.inner_line_thickness)) / outline_base
    intersection_scale = abs(float(settings.intersection_thickness)) / outline_base
    if has_inner:
        inner_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                max_outline_world * inner_scale,
            ),
        )
    if has_intersection:
        intersection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                max_outline_world * intersection_scale,
            ),
        )


def _apply_reference_line_width(scene, camera, obj, settings, mod) -> None:
    from . import inner_lines, intersection_lines, vertex_analysis

    ref_distance = _line_width_reference_distance(settings)
    outline_width_world = max(
        _reference_width_for_distance(
            scene,
            camera,
            settings.outline_thickness,
            ref_distance,
        ),
        1.0e-9,
    )
    mod.thickness = modifier_thickness_for_world_width(obj, outline_width_world)

    if _prepare_style_weights(obj, settings, "outline"):
        mod.vertex_group = VG_LINE_WIDTH
        mod.thickness_vertex_group = 0.0
    else:
        mod.vertex_group = ""
    has_inner = _has_inner_modifier(obj)
    has_intersection = _has_intersection_modifier(obj)
    if has_inner:
        _prepare_style_weights(obj, settings, "inner")
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INNER_LINE_WIDTH)
    if has_intersection:
        _prepare_style_weights(obj, settings, "intersection")
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INTERSECTION_LINE_WIDTH)

    outline_base = max(abs(float(settings.outline_thickness)), 1.0e-9)
    inner_scale = abs(float(settings.inner_line_thickness)) / outline_base
    intersection_scale = abs(float(settings.intersection_thickness)) / outline_base
    if has_inner:
        inner_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                outline_width_world * inner_scale,
            ),
        )
    if has_intersection:
        intersection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                outline_width_world * intersection_scale,
            ),
        )


# ------------------------------------------------------------------
# 各機能の更新ロジック（オブジェクトごとの設定を参照）
# ------------------------------------------------------------------

def _line_width_objects(scene, objects=None):
    source = scene.objects if objects is None else objects
    seen: set[int] = set()
    for obj in source:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        pointer = obj.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        yield obj


def _set_modifier_visibility(mod, visible: bool) -> None:
    if mod.show_viewport != visible:
        mod.show_viewport = visible
    if mod.show_render != visible:
        mod.show_render = visible


def _has_inner_modifier(obj) -> bool:
    return obj.modifiers.get(GN_MODIFIER_NAME) is not None


def _has_intersection_modifier(obj) -> bool:
    return any(iter_intersection_modifiers(obj))


def _update_camera_compensation(scene, camera, objects=None):
    """線幅 (mm) をカメラビュー基準の太さとして各オブジェクトへ反映."""
    from . import inner_lines, intersection_lines, vertex_analysis

    cam_loc = camera.matrix_world.translation
    for obj in _line_width_objects(scene, objects):
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
            _apply_reference_line_width(scene, camera, obj, settings, mod)
            continue

        if _prepare_style_weights(obj, settings, "outline"):
            mod.vertex_group = VG_LINE_WIDTH
            mod.thickness_vertex_group = 0.0
        else:
            mod.vertex_group = ""
        has_inner = _has_inner_modifier(obj)
        has_intersection = _has_intersection_modifier(obj)
        if has_inner:
            _prepare_style_weights(obj, settings, "inner")
        else:
            vertex_analysis.clear_width_weights(obj, group_name=VG_INNER_LINE_WIDTH)
        if has_intersection:
            _prepare_style_weights(obj, settings, "intersection")
        else:
            vertex_analysis.clear_width_weights(obj, group_name=VG_INTERSECTION_LINE_WIDTH)

        influence = settings.camera_compensation_influence
        ref_d = _line_width_reference_distance(settings)
        base_t = max(
            _reference_width_for_distance(
                scene,
                camera,
                settings.outline_thickness,
                ref_d,
            ),
            1.0e-9,
        )
        dist = (cam_loc - obj.matrix_world.translation).length
        factor = dist / ref_d
        adjusted = base_t * (1.0 + (factor - 1.0) * influence)
        mod.thickness = modifier_thickness_for_world_width(obj, adjusted)

        outline_base = max(abs(float(settings.outline_thickness)), 1.0e-9)
        inner_scale = abs(float(settings.inner_line_thickness)) / outline_base
        intersection_scale = abs(float(settings.intersection_thickness)) / outline_base
        inner_adjusted = base_t * inner_scale * (
            1.0 + (factor - 1.0) * influence
        )
        intersection_adjusted = base_t * intersection_scale * (
            1.0 + (factor - 1.0) * influence
        )
        if has_inner:
            inner_lines.update_parameters(
                obj,
                thickness=modifier_thickness_for_world_width(obj, inner_adjusted),
            )
        if has_intersection:
            intersection_lines.update_parameters(
                obj,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    intersection_adjusted,
                ),
            )


def _update_visibility(scene, camera, cam_loc, cam_fwd, objects=None):
    """ビューカリングと線種別の距離制限を統合処理."""
    half_angle_cache = None

    for obj in _line_width_objects(scene, objects):
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is None:
            continue

        do_culling = settings.use_camera_culling
        do_outline_distance = settings.use_outline_distance_limit
        do_inner_distance = settings.use_inner_line_distance_limit
        do_intersection_distance = settings.use_intersection_distance_limit
        if not (
            do_culling
            or do_outline_distance
            or do_inner_distance
            or do_intersection_distance
        ):
            continue

        outline_mod = obj.modifiers.get(MODIFIER_NAME)
        inner_mod = obj.modifiers.get(GN_MODIFIER_NAME)
        intersection_mods = list(iter_intersection_modifiers(obj))
        if outline_mod is None and inner_mod is None and not intersection_mods:
            continue

        if bool(obj.get(PROP_LINES_HIDDEN, False)):
            for mod in (outline_mod, inner_mod, *intersection_mods):
                if mod is not None:
                    _set_modifier_visibility(mod, False)
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

        outline_in_range = (
            not do_outline_distance or dist < settings.outline_max_distance
        )
        inner_in_range = (
            settings.inner_line_enabled
            and (not do_inner_distance or dist < settings.inner_line_max_distance)
        )
        intersection_in_range = (
            settings.intersection_enabled
            and (
                not do_intersection_distance
                or dist < settings.intersection_max_distance
            )
        )

        if outline_mod is not None:
            visible = in_view and outline_in_range
            _set_modifier_visibility(outline_mod, visible)

        for intersection_mod in intersection_mods:
            visible = in_view and intersection_in_range
            _set_modifier_visibility(intersection_mod, visible)

        if inner_mod is not None:
            visible = in_view and inner_in_range
            _set_modifier_visibility(inner_mod, visible)


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


def refresh_objects(context, objects, *, update_visibility: bool = False) -> bool:
    """指定オブジェクトだけカメラ基準の線幅を更新."""
    global _updating
    if _updating:
        return False
    _updating = True
    try:
        scene = context.scene
        camera = get_line_camera(scene)
        if camera is None:
            return False
        targets = list(_line_width_objects(scene, objects))
        if not targets:
            return True
        _update_camera_compensation(scene, camera, targets)
        if update_visibility:
            cam_loc = camera.matrix_world.translation
            cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
            _update_visibility(scene, camera, cam_loc, cam_fwd, targets)
        return True
    finally:
        _updating = False


def refresh_visibility_objects(context, objects) -> bool:
    """指定オブジェクトだけ表示距離・カメラ範囲外の表示状態を更新."""
    global _updating
    if _updating:
        return False
    _updating = True
    try:
        scene = context.scene
        camera = get_line_camera(scene)
        if camera is None:
            return False
        targets = list(_line_width_objects(scene, objects))
        if not targets:
            return True
        cam_loc = camera.matrix_world.translation
        cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        _update_visibility(scene, camera, cam_loc, cam_fwd, targets)
        return True
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
    ref_distance = max(dist, 0.001)
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is not None:
        settings.line_width_reference_distance = ref_distance
    obj[PROP_REF_DISTANCE] = ref_distance
    obj[PROP_BASE_THICKNESS] = settings.outline_thickness if settings else abs(mod.thickness)
    obj[PROP_REF_FOV_TAN] = _get_fov_factor(camera.data, scene)
    obj[PROP_REF_MODE] = REF_MODE_LOCKED
    return True


def store_unit_reference(obj, scene):
    """設定された線幅基準距離を補正基準として保存."""
    camera = get_line_camera(scene)
    if camera is None:
        return False
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    obj[PROP_REF_DISTANCE] = _line_width_reference_distance(settings)
    obj[PROP_BASE_THICKNESS] = settings.outline_thickness if settings else abs(mod.thickness)
    obj[PROP_REF_FOV_TAN] = _get_fov_factor(camera.data, scene)
    obj[PROP_REF_MODE] = REF_MODE_VIEW
    return True


def register() -> None:
    if _on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame_change)


def unregister() -> None:
    if _on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change)
