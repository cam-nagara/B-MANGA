"""B-MANGA Line — カメラ関連の自動更新.

- カメラ距離による線幅補正
- カメラビュー外のライン非表示（パフォーマンス最適化）
- カメラ距離による線種別の表示制限
"""

from __future__ import annotations

import math

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
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
    SELECTION_LINE_MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
    VG_INNER_LINE_WIDTH,
    VG_INTERSECTION_LINE_WIDTH,
    VG_LINE_WIDTH,
    VG_SELECTION_LINE_WIDTH,
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


def _world_bound_points(obj: bpy.types.Object) -> list[Vector]:
    if obj.bound_box:
        return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return [obj.matrix_world.translation.copy()]


def _object_world_sphere(obj: bpy.types.Object) -> tuple[Vector, float]:
    points = _world_bound_points(obj)
    center = sum(points, Vector()) / max(1, len(points))
    radius = max((point - center).length for point in points) if points else 0.0
    return center, radius


def _object_overlaps_rect_camera(
    obj: bpy.types.Object,
    scene,
    camera: bpy.types.Object,
) -> bool:
    coords = [
        world_to_camera_view(scene, camera, point)
        for point in _world_bound_points(obj)
    ]
    in_front = [coord for coord in coords if coord.z > 0.0]
    if not in_front:
        return False
    min_x = min(coord.x for coord in in_front)
    max_x = max(coord.x for coord in in_front)
    min_y = min(coord.y for coord in in_front)
    max_y = max(coord.y for coord in in_front)
    return min_x <= 1.0 and max_x >= 0.0 and min_y <= 1.0 and max_y >= 0.0


def _object_overlaps_panorama_camera(
    obj: bpy.types.Object,
    scene,
    camera: bpy.types.Object,
) -> bool:
    center, radius = _object_world_sphere(obj)
    to_center = center - camera.matrix_world.translation
    dist = to_center.length
    if dist <= 1.0e-9:
        return True
    half_angle = _get_camera_half_angle(camera.data, scene)
    if half_angle >= math.pi - 1.0e-6:
        return True
    cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    angle = cam_fwd.angle(to_center)
    angular_radius = math.atan2(radius, dist)
    return (angle - angular_radius) <= half_angle


def object_overlaps_camera_view(
    obj: bpy.types.Object,
    scene,
    camera: bpy.types.Object | None = None,
) -> bool:
    """オブジェクトの境界がカメラに写る範囲と重なるかを返す."""
    if camera is None:
        camera = get_line_camera(scene)
    if camera is None or getattr(camera, "type", None) != "CAMERA":
        return True
    cam_data = getattr(camera, "data", None)
    if cam_data is None:
        return True
    if getattr(cam_data, "type", None) == "PANO":
        return _object_overlaps_panorama_camera(obj, scene, camera)
    return _object_overlaps_rect_camera(obj, scene, camera)


def _line_creation_in_range(
    obj: bpy.types.Object,
    scene,
    settings,
    enabled_prop: str,
    distance_prop: str,
) -> bool:
    if settings is None:
        return True
    if not getattr(settings, enabled_prop, False):
        return True
    camera = get_line_camera(scene)
    if camera is None:
        return True
    if not object_overlaps_camera_view(obj, scene, camera):
        return False
    limit = max(0.0, float(getattr(settings, distance_prop, 10.0)))
    return object_distance_from_camera(obj, camera) <= limit


def inner_line_creation_in_range(obj: bpy.types.Object, scene, settings=None) -> bool:
    """稜谷線を作成してよいカメラ距離内か判定."""
    if settings is None:
        settings = getattr(obj, "bmanga_line_settings", None)
    return _line_creation_in_range(
        obj,
        scene,
        settings,
        "use_inner_line_creation_limit",
        "inner_line_creation_max_distance",
    )


def outline_line_creation_in_range(obj: bpy.types.Object, scene, settings=None) -> bool:
    """アウトラインを作成してよいカメラ距離内か判定."""
    if settings is None:
        settings = getattr(obj, "bmanga_line_settings", None)
    return _line_creation_in_range(
        obj,
        scene,
        settings,
        "use_outline_creation_limit",
        "outline_creation_max_distance",
    )


def intersection_line_creation_in_range(obj: bpy.types.Object, scene, settings=None) -> bool:
    """交差線を作成してよいカメラ距離内か判定."""
    if settings is None:
        settings = getattr(obj, "bmanga_line_settings", None)
    return _line_creation_in_range(
        obj,
        scene,
        settings,
        "use_intersection_creation_limit",
        "intersection_creation_max_distance",
    )


def selection_line_creation_in_range(obj: bpy.types.Object, scene, settings=None) -> bool:
    """選択線を作成してよいカメラ距離内か判定."""
    if settings is None:
        settings = getattr(obj, "bmanga_line_settings", None)
    return _line_creation_in_range(
        obj,
        scene,
        settings,
        "use_selection_line_creation_limit",
        "selection_line_creation_max_distance",
    )


# ------------------------------------------------------------------
# カメラ画角ユーティリティ
# ------------------------------------------------------------------

_FISHEYE_PANORAMA_TYPES = (
    "FISHEYE_EQUISOLID",
    "FISHEYE_EQUIDISTANT",
    "FISHEYE_POLYNOMIAL",
)


def _panorama_type(cam_data) -> str | None:
    ptype = getattr(cam_data, "panorama_type", None)
    if ptype is None:
        cycles = getattr(cam_data, "cycles", None)
        if cycles is not None:
            ptype = getattr(cycles, "panorama_type", None)
    return ptype


def _fisheye_fov(cam_data) -> float | None:
    fov = getattr(cam_data, "fisheye_fov", None)
    if fov is None:
        cycles = getattr(cam_data, "cycles", None)
        if cycles is not None:
            fov = getattr(cycles, "fisheye_fov", None)
    return fov


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
        ptype = _panorama_type(cam_data)

        if ptype in _FISHEYE_PANORAMA_TYPES:
            fov = _fisheye_fov(cam_data)
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
    # 魚眼など半画角が90°以上になるカメラでは tan が発散するためクランプする
    t = math.tan(min(half, math.radians(89.5)))
    return t if t > 1e-6 else 1.0


def _effective_render_size(scene) -> tuple[float, float]:
    """線幅計算の基準ピクセルサイズ（最終レンダー解像度）を返す.

    線幅(mm)→px 変換（_target_pixels）が紙の DPI 基準のため、
    ここも解像度パーセンテージやビューポート表示サイズを混ぜず、
    常にフル解像度を返す（印刷 mm 一致の確定仕様）。
    """
    render = scene.render
    width = max(1.0, float(getattr(render, "resolution_x", 1)))
    height = max(1.0, float(getattr(render, "resolution_y", 1)))
    return width, height


def _get_scene_dpi(scene) -> float:
    work = getattr(scene, "bmanga_work", None)
    paper = getattr(work, "paper", None) if work else None
    dpi = float(getattr(paper, "dpi", 0.0) or 0.0) if paper else 0.0
    return dpi if dpi > 0.0 else float(_FALLBACK_DPI)


def _target_pixels(scene, width_m: float) -> float:
    width_mm = max(0.0, float(width_m) * 1000.0)
    return max(0.001, width_mm * _get_scene_dpi(scene) / 25.4)


def target_pixels_for_mm(scene, width_mm: float) -> float:
    """mm指定の太さを紙面DPI基準のpxへ変換する公開ヘルパー.

    バンプ線（画像空間のコンポジター処理で、ジオメトリの世界座標を
    経由しない）など、_target_pixels のように world幅(m)からの換算を
    経由できない呼び出し元向け。DPI取得・換算式は _target_pixels と
    完全に同一（唯一の変換元）。
    """
    return _target_pixels(scene, max(0.0, float(width_mm)) / 1000.0)


def _panorama_radians_per_pixel(cam_data, scene, width: float, height: float) -> float:
    """パノラマカメラの1ピクセルあたりの視角（ラジアン）を返す.

    魚眼は視野角が180°を超え tan(半画角) が発散するため、透視投影の
    ような tan ベースの換算は使えない。角度がピクセルへほぼ線形に
    対応する性質（等距離射影）を使って角度密度で換算する。
    """
    ptype = _panorama_type(cam_data)
    if ptype in _FISHEYE_PANORAMA_TYPES:
        # Cycles の魚眼は横・縦を独立にフレームへ正規化するため、
        # 非正方形レンダーでは横=視野角/横px・縦=視野角/縦px の
        # アナモルフィック射影になる（縦横比・sensor_fit に依存しない
        # ことを実測確認済み: _verify/2026-07-09_bml_fisheye_width/）。
        # 線の向きで画素幅が変わるため、両密度の幾何平均で換算する。
        fov = _fisheye_fov(cam_data)
        fov = float(fov) if fov else math.pi
        return max(1.0e-6, fov) / max(1.0, math.sqrt(width * height))
    if ptype == "EQUIRECTANGULAR":
        # 縦方向（緯度）は画面全域で角度密度が一定なため縦を基準にする
        lat_min = float(getattr(cam_data, "latitude_min", -math.pi / 2))
        lat_max = float(getattr(cam_data, "latitude_max", math.pi / 2))
        return max(1.0e-6, lat_max - lat_min) / max(1.0, height)
    half = min(_get_camera_half_angle(cam_data, scene), math.pi)
    return max(1.0e-6, 2.0 * half) / max(1.0, math.hypot(width, height))


def _world_per_pixel(scene, camera, world_co: Vector) -> float:
    width, height = _effective_render_size(scene)
    cam_data = camera.data
    if cam_data.type == "ORTHO":
        # Blender の Orthographic Scale はカメラビューの横幅（Blender unit）
        # として扱われるため、横解像度で割る。縦解像度で割ると 16:9 では
        # 600dpi 換算の線幅が約 1.78 倍に太くなる。
        return max(1.0e-9, float(cam_data.ortho_scale) / width)

    local = camera.matrix_world.inverted() @ world_co
    if cam_data.type == "PERSP":
        depth = max(0.001, -float(local.z))
        angle_y = float(getattr(cam_data, "angle_y", cam_data.angle))
        view_height = 2.0 * depth * math.tan(angle_y * 0.5)
        return max(1.0e-9, view_height / height)

    # PANO: 魚眼はカメラの真横・後方も写るため、深度はカメラ前方（-Z）
    # 成分ではなくカメラ位置からの距離で測る
    dist = max(0.001, float(local.length))
    return max(
        1.0e-9,
        dist * _panorama_radians_per_pixel(cam_data, scene, width, height),
    )


def _uniform_widths_for_mesh(scene, camera, obj, width_m: float) -> list[float]:
    from . import width_math

    widths, _depths = width_math.vertex_widths_and_depths(scene, camera, obj, width_m)
    return widths.tolist()


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


def _line_width_distance_falloff(settings) -> float:
    raw = getattr(settings, "line_width_distance_falloff", 0.0)
    return max(0.0, min(2.0, float(raw or 0.0)))


def _limit_uniform_width_to_setting(settings) -> bool:
    return bool(getattr(settings, "limit_uniform_width_to_setting", False))


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


def _compensated_width_for_mesh(
    scene,
    camera,
    obj,
    settings,
    width_m: float,
) -> float:
    mesh_width = _reference_width_for_mesh(scene, camera, obj, width_m)
    ref_width = _reference_width_for_distance(
        scene,
        camera,
        width_m,
        _line_width_reference_distance(settings),
    )
    influence = min(1.0, max(0.0, float(
        getattr(settings, "camera_compensation_influence", 1.0) or 0.0
    )))
    adjusted = ref_width + (mesh_width - ref_width) * influence
    # 低い補正値で近距離オブジェクトが基準距離の太い線幅へ膨らむのを防ぐ。
    # 「線幅の均一化（オブジェクト単位）」がオンの間は、設定した線幅より
    # 太い方向へはブレンドせず、最低でもオブジェクト位置の指定線幅に留める。
    if ref_width > mesh_width:
        return mesh_width
    return adjusted


def _prepare_style_weights(obj, settings, target: str) -> bool:
    from . import outline_width_attribute, vertex_analysis

    group_name = vertex_analysis.width_group_name(target)
    if vertex_analysis.has_width_controls(settings, target):
        vertex_analysis.compute_and_apply_weights(obj, settings, target)
        if target == "outline":
            outline_width_attribute.ensure_outline_width_attribute(obj, settings)
        return True
    vertex_analysis.clear_width_weights(obj, group_name=group_name)
    if target == "outline":
        outline_width_attribute.remove_outline_width_attribute(obj)
    return False


def _style_weights_for_uniform(obj, settings, target: str) -> list[float] | None:
    from . import outline_width_attribute, vertex_analysis

    group_name = vertex_analysis.width_group_name(target)
    if vertex_analysis.has_width_controls(settings, target):
        weights = vertex_analysis.compute_weights(obj, settings, target)
        if target == "outline":
            outline_width_attribute.ensure_outline_width_attribute(obj, settings)
        return weights
    vertex_analysis.clear_width_weights(obj, group_name=group_name)
    if target == "outline":
        outline_width_attribute.remove_outline_width_attribute(obj)
    return None


def _uniform_width_basis(scene, camera, obj, settings) -> tuple[np.ndarray, float]:
    from . import width_math

    widths, _depths = width_math.vertex_widths_and_depths(
        scene,
        camera,
        obj,
        1.0,
        distance_falloff=_line_width_distance_falloff(settings),
        reference_distance=_line_width_reference_distance(settings),
        limit_to_setting=_limit_uniform_width_to_setting(settings),
    )
    if widths.size == 0:
        return widths, 0.0
    max_width = max(float(widths.max()), 1.0e-9)
    return widths / max_width, max_width


def _apply_uniform_line_width(scene, camera, obj, settings, mod) -> None:
    from . import outline_width_attribute, vertex_analysis

    del mod
    if not obj.data.vertices:
        return

    basis = _uniform_width_basis(scene, camera, obj, settings)
    _apply_uniform_target_line_width(scene, camera, obj, settings, "outline", basis)
    outline_width_attribute.ensure_outline_width_attribute(obj, settings)

    if _has_inner_modifier(obj):
        _apply_uniform_target_line_width(scene, camera, obj, settings, "inner", basis)
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INNER_LINE_WIDTH)

    if _has_intersection_modifier(obj):
        _apply_uniform_target_line_width(
            scene, camera, obj, settings, "intersection", basis,
        )
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INTERSECTION_LINE_WIDTH)
    if _has_selection_modifier(obj):
        _apply_uniform_target_line_width(scene, camera, obj, settings, "selection", basis)
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_SELECTION_LINE_WIDTH)


def _apply_reference_line_width(scene, camera, obj, settings, mod) -> None:
    from . import inner_lines, intersection_lines, outline_setup, selection_lines, vertex_analysis

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
    outline_setup.sync_local_outline_from_state(obj)
    outline_setup.sync_sheet_outline_width(obj)

    if _prepare_style_weights(obj, settings, "outline"):
        mod.vertex_group = VG_LINE_WIDTH
        mod.thickness_vertex_group = 0.0
    else:
        mod.vertex_group = ""
    has_inner = _has_inner_modifier(obj)
    has_intersection = _has_intersection_modifier(obj)
    has_selection = _has_selection_modifier(obj)
    if has_inner:
        _prepare_style_weights(obj, settings, "inner")
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INNER_LINE_WIDTH)
    if has_intersection:
        _prepare_style_weights(obj, settings, "intersection")
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_INTERSECTION_LINE_WIDTH)
    if has_selection:
        _prepare_style_weights(obj, settings, "selection")
    else:
        vertex_analysis.clear_width_weights(obj, group_name=VG_SELECTION_LINE_WIDTH)

    outline_base = max(abs(float(settings.outline_thickness)), 1.0e-9)
    inner_scale = abs(float(settings.inner_line_thickness)) / outline_base
    intersection_scale = abs(float(settings.intersection_thickness)) / outline_base
    selection_scale = abs(float(settings.selection_line_thickness)) / outline_base
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
    if has_selection:
        selection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                outline_width_world * selection_scale,
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
    if visible:
        from . import intersection_lines
        if intersection_lines.is_deferred_viewport_modifier(mod):
            if mod.show_render != visible:
                mod.show_render = visible
            return
    if mod.show_viewport != visible:
        mod.show_viewport = visible
    if mod.show_render != visible:
        mod.show_render = visible


def _has_inner_modifier(obj) -> bool:
    return obj.modifiers.get(GN_MODIFIER_NAME) is not None


def _has_intersection_modifier(obj) -> bool:
    return any(iter_intersection_modifiers(obj))


def _has_selection_modifier(obj) -> bool:
    return obj.modifiers.get(SELECTION_LINE_MODIFIER_NAME) is not None


def _normalize_width_targets(width_targets) -> tuple[str, ...] | None:
    if width_targets is None:
        return None
    requested = set(width_targets)
    return tuple(
        target for target in ("outline", "inner", "intersection", "selection")
        if target in requested
    )


def _target_width_setting(settings, target: str) -> float:
    if target == "inner":
        return float(settings.inner_line_thickness)
    if target == "intersection":
        return float(settings.intersection_thickness)
    if target == "selection":
        return float(settings.selection_line_thickness)
    return float(settings.outline_thickness)


def _apply_target_width(
    obj,
    target: str,
    width: float,
) -> None:
    from . import inner_lines, intersection_lines, outline_setup, selection_lines

    scaled = modifier_thickness_for_world_width(obj, max(width, 1.0e-9))
    if target == "inner":
        if _has_inner_modifier(obj):
            inner_lines.update_parameters(obj, thickness=scaled)
        return
    if target == "intersection":
        if _has_intersection_modifier(obj):
            intersection_lines.update_parameters(obj, thickness=scaled)
        return
    if target == "selection":
        if _has_selection_modifier(obj):
            selection_lines.update_parameters(obj, thickness=scaled)
        return
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        mod.thickness = scaled
        outline_setup.sync_local_outline_from_state(obj)
        outline_setup.sync_sheet_outline_width(obj)
    elif obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is not None:
        outline_setup.sync_sheet_outline_width(obj, scaled)


def _apply_target_style_weights(obj, settings, target: str) -> None:
    from . import vertex_analysis

    group_name = vertex_analysis.width_group_name(target)
    if target == "outline":
        mod = obj.modifiers.get(MODIFIER_NAME)
        if mod is None:
            return
        if _prepare_style_weights(obj, settings, target):
            mod.vertex_group = VG_LINE_WIDTH
            mod.thickness_vertex_group = 0.0
        else:
            mod.vertex_group = ""
        return

    if target == "inner" and not _has_inner_modifier(obj):
        vertex_analysis.clear_width_weights(obj, group_name=group_name)
        return
    if target == "intersection" and not _has_intersection_modifier(obj):
        vertex_analysis.clear_width_weights(obj, group_name=group_name)
        return
    if target == "selection" and not _has_selection_modifier(obj):
        vertex_analysis.clear_width_weights(obj, group_name=group_name)
        return
    _prepare_style_weights(obj, settings, target)


def _apply_uniform_target_line_width(
    scene,
    camera,
    obj,
    settings,
    target: str,
    basis: tuple[np.ndarray, float] | None = None,
) -> None:
    from . import vertex_analysis, width_math

    if target == "inner" and not _has_inner_modifier(obj):
        vertex_analysis.clear_width_weights(obj, group_name=VG_INNER_LINE_WIDTH)
        return
    if target == "intersection" and not _has_intersection_modifier(obj):
        vertex_analysis.clear_width_weights(obj, group_name=VG_INTERSECTION_LINE_WIDTH)
        return
    if target == "selection" and not _has_selection_modifier(obj):
        vertex_analysis.clear_width_weights(obj, group_name=VG_SELECTION_LINE_WIDTH)
        return

    style_weights = _style_weights_for_uniform(obj, settings, target)
    if basis is None:
        widths, _depths = width_math.vertex_widths_and_depths(
            scene,
            camera,
            obj,
            _target_width_setting(settings, target),
            distance_falloff=_line_width_distance_falloff(settings),
            reference_distance=_line_width_reference_distance(settings),
            limit_to_setting=_limit_uniform_width_to_setting(settings),
        )
        if widths.size == 0:
            return
        max_width = max(float(widths.max()), 1.0e-9)
        normalized = widths / max_width
    else:
        normalized, unit_max_width = basis
        if normalized.size == 0:
            return
        unit_pixels = max(_target_pixels(scene, 1.0), 1.0e-12)
        target_pixels = _target_pixels(scene, _target_width_setting(settings, target))
        max_width = max(unit_max_width * (target_pixels / unit_pixels), 1.0e-9)
    if style_weights is not None:
        style = np.asarray(style_weights, dtype=np.float64)
        combined = normalized.copy()
        count = min(combined.size, style.size)
        if count:
            combined[:count] *= style[:count]
        if count < combined.size:
            combined[count:] *= 1.0
    else:
        combined = normalized
    vertex_analysis.write_width_weights(obj, combined, target)
    if target == "outline":
        from . import outline_setup

        mod = obj.modifiers.get(MODIFIER_NAME)
        if mod is not None:
            mod.vertex_group = VG_LINE_WIDTH
            mod.thickness_vertex_group = 0.0
    _apply_target_width(obj, target, max_width)
    if (
        target == "outline"
        and _limit_uniform_width_to_setting(settings)
        and obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is not None
    ):
        # 境界チューブは頂点ウェイトを直接使えないため、最も細い頂点幅を
        # オブジェクト全体へ採用し、どの頂点でも設定線幅を超えない側へ倒す。
        scalar_width = max(
            max_width * float(normalized.min()),
            1.0e-9,
        )
        outline_setup.sync_sheet_outline_width(
            obj,
            modifier_thickness_for_world_width(obj, scalar_width),
        )


def _apply_reference_target_line_width(scene, camera, obj, settings, target: str) -> None:
    ref_distance = _line_width_reference_distance(settings)
    width = _reference_width_for_distance(
        scene,
        camera,
        _target_width_setting(settings, target),
        ref_distance,
    )
    _apply_target_style_weights(obj, settings, target)
    _apply_target_width(obj, target, width)


def _apply_compensated_target_line_width(scene, camera, obj, settings, target: str) -> None:
    adjusted = _compensated_width_for_mesh(
        scene,
        camera,
        obj,
        settings,
        _target_width_setting(settings, target),
    )
    _apply_target_style_weights(obj, settings, target)
    _apply_target_width(obj, target, adjusted)


def _has_uniform_width_target(obj, target: str) -> bool:
    if target == "inner":
        return _has_inner_modifier(obj)
    if target == "intersection":
        return _has_intersection_modifier(obj)
    if target == "selection":
        return _has_selection_modifier(obj)
    return (
        obj.modifiers.get(MODIFIER_NAME) is not None
        or obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is not None
    )


def _apply_targeted_line_widths(scene, camera, obj, settings, targets: tuple[str, ...]) -> None:
    uniform_basis = None
    for target in targets:
        if settings.use_uniform_line_width:
            if uniform_basis is None and _has_uniform_width_target(obj, target):
                uniform_basis = _uniform_width_basis(scene, camera, obj, settings)
            _apply_uniform_target_line_width(
                scene, camera, obj, settings, target, uniform_basis,
            )
        elif settings.use_camera_compensation:
            _apply_compensated_target_line_width(scene, camera, obj, settings, target)
        else:
            _apply_reference_target_line_width(scene, camera, obj, settings, target)


def _update_camera_compensation(scene, camera, objects=None, width_targets=None):
    """線幅 (mm) をカメラビュー基準の太さとして各オブジェクトへ反映."""
    from . import intersection_lines, vertex_analysis

    normalized_targets = _normalize_width_targets(width_targets)
    outline_targets: list[bpy.types.Object] = []
    for obj in _line_width_objects(scene, objects):
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is None:
            continue
        mod = obj.modifiers.get(MODIFIER_NAME)
        sheet_mod = obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME)
        if mod is None and sheet_mod is None:
            continue
        if normalized_targets is not None:
            _apply_targeted_line_widths(scene, camera, obj, settings, normalized_targets)
            if "outline" in normalized_targets:
                outline_targets.append(obj)
            continue
        if mod is None:
            _apply_targeted_line_widths(
                scene,
                camera,
                obj,
                settings,
                ("outline", "inner", "intersection", "selection"),
            )
            outline_targets.append(obj)
            continue
        if settings.use_uniform_line_width:
            _apply_uniform_line_width(scene, camera, obj, settings, mod)
            outline_targets.append(obj)
            continue
        if not settings.use_camera_compensation:
            _apply_reference_line_width(scene, camera, obj, settings, mod)
            outline_targets.append(obj)
            continue

        _apply_compensated_target_line_width(scene, camera, obj, settings, "outline")
        outline_targets.append(obj)
        if _has_inner_modifier(obj):
            _apply_compensated_target_line_width(scene, camera, obj, settings, "inner")
        else:
            vertex_analysis.clear_width_weights(obj, group_name=VG_INNER_LINE_WIDTH)
        if _has_intersection_modifier(obj):
            _apply_compensated_target_line_width(
                scene, camera, obj, settings, "intersection",
            )
        else:
            vertex_analysis.clear_width_weights(obj, group_name=VG_INTERSECTION_LINE_WIDTH)
        if _has_selection_modifier(obj):
            _apply_compensated_target_line_width(
                scene, camera, obj, settings, "selection",
            )
        else:
            vertex_analysis.clear_width_weights(obj, group_name=VG_SELECTION_LINE_WIDTH)
    if normalized_targets is None and outline_targets:
        intersection_lines.update_target_width_references(scene, outline_targets)


def _normalize_visibility_targets(targets) -> set[str] | None:
    if targets is None:
        return None
    if isinstance(targets, str):
        targets = (targets,)
    allowed = {"outline", "inner", "intersection", "selection"}
    normalized = {str(target) for target in targets if str(target) in allowed}
    return normalized or None


def _update_visibility(scene, camera, cam_loc, cam_fwd, objects=None, line_targets=None):
    """ビューカリングと線種別の距離制限を統合処理."""
    half_angle_cache = None
    target_set = _normalize_visibility_targets(line_targets)

    for obj in _line_width_objects(scene, objects):
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is None:
            continue

        do_culling = settings.use_camera_culling
        do_outline_distance = settings.use_outline_distance_limit
        do_inner_distance = settings.use_inner_line_distance_limit
        do_intersection_distance = settings.use_intersection_distance_limit
        do_selection_distance = settings.use_selection_line_distance_limit
        if not (
            do_culling
            or do_outline_distance
            or do_inner_distance
            or do_intersection_distance
            or do_selection_distance
        ):
            continue

        from . import outline_local_subdivision

        local_outline_mod = outline_local_subdivision.get_modifier(obj)
        outline_mods = [
            mod
            for mod in (
                obj.modifiers.get(MODIFIER_NAME),
                local_outline_mod,
                obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME),
            )
            if mod is not None
        ]
        inner_mod = obj.modifiers.get(GN_MODIFIER_NAME)
        selection_mod = obj.modifiers.get(SELECTION_LINE_MODIFIER_NAME)
        intersection_mods = list(iter_intersection_modifiers(obj))
        if not outline_mods and inner_mod is None and selection_mod is None and not intersection_mods:
            continue

        if bool(obj.get(PROP_LINES_HIDDEN, False)):
            if target_set is None or "outline" in target_set:
                for outline_mod in outline_mods:
                    _set_modifier_visibility(outline_mod, False)
            if target_set is None or "inner" in target_set:
                if inner_mod is not None:
                    _set_modifier_visibility(inner_mod, False)
            if target_set is None or "selection" in target_set:
                if selection_mod is not None:
                    _set_modifier_visibility(selection_mod, False)
            if target_set is None or "intersection" in target_set:
                for intersection_mod in intersection_mods:
                    _set_modifier_visibility(intersection_mod, False)
            continue

        # 原点はメッシュ中心から大きく離れていることがある（インポート資産等）ため、
        # 原点ではなくワールド境界球を基準に判定する。
        center, bound_r = _object_world_sphere(obj)
        to_obj = center - cam_loc
        dist = to_obj.length

        # ビューカリング判定
        in_view = True
        if do_culling and dist >= 0.001:
            if half_angle_cache is None:
                half_angle_cache = _get_camera_half_angle(camera.data, scene)
            margin = settings.culling_margin
            angle = cam_fwd.angle(to_obj)
            angular_r = math.atan2(bound_r, dist)
            in_view = (angle - angular_r) < (half_angle_cache + margin)

        if (
            do_outline_distance
            or do_inner_distance
            or do_intersection_distance
            or do_selection_distance
        ):
            # 距離制限は作成時の判定と同じ「境界への最短距離」で測る。
            # 巨大オブジェクト（道路等）が中心距離だけで丸ごと消えないように。
            dist = object_distance_from_camera(obj, camera)

        outline_in_range = (
            not do_outline_distance or dist < settings.outline_max_distance
        )
        outline_enabled = bool(getattr(settings, "outline_enabled", True))
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
        selection_in_range = (
            settings.selection_line_enabled
            and (
                not do_selection_distance
                or dist < settings.selection_line_max_distance
            )
        )

        if outline_mods and (target_set is None or "outline" in target_set):
            visible = in_view and outline_in_range and outline_enabled
            for outline_mod in outline_mods:
                state_only = (
                    outline_mod.name == MODIFIER_NAME
                    and local_outline_mod is not None
                    and bool(
                        getattr(
                            settings,
                            "auto_subdivision_for_midpoint",
                            False,
                        )
                    )
                )
                _set_modifier_visibility(
                    outline_mod,
                    False if state_only else visible,
                )

        if target_set is None or "intersection" in target_set:
            for intersection_mod in intersection_mods:
                visible = in_view and intersection_in_range
                _set_modifier_visibility(intersection_mod, visible)

        if inner_mod is not None and (target_set is None or "inner" in target_set):
            visible = in_view and inner_in_range
            _set_modifier_visibility(inner_mod, visible)

        if selection_mod is not None and (target_set is None or "selection" in target_set):
            visible = in_view and selection_in_range
            _set_modifier_visibility(selection_mod, visible)


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


def refresh_objects(
    context,
    objects,
    *,
    update_visibility: bool = False,
    width_targets=None,
    visibility_targets=None,
) -> bool:
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
        _update_camera_compensation(scene, camera, targets, width_targets=width_targets)
        if update_visibility:
            cam_loc = camera.matrix_world.translation
            cam_fwd = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
            _update_visibility(
                scene,
                camera,
                cam_loc,
                cam_fwd,
                targets,
                line_targets=visibility_targets,
            )
        return True
    finally:
        _updating = False


def refresh_visibility_objects(context, objects, *, visibility_targets=None) -> bool:
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
        _update_visibility(
            scene,
            camera,
            cam_loc,
            cam_fwd,
            targets,
            line_targets=visibility_targets,
        )
        return True
    finally:
        _updating = False


def store_reference(obj, scene):
    """現在のカメラ距離・厚み・FOV を基準値として保存."""
    camera = get_line_camera(scene)
    if camera is None:
        return False
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None and obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is None:
        return False
    dist = (camera.matrix_world.translation - obj.matrix_world.translation).length
    ref_distance = max(dist, 0.001)
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is not None:
        settings.line_width_reference_distance = ref_distance
    obj[PROP_REF_DISTANCE] = ref_distance
    obj[PROP_BASE_THICKNESS] = (
        settings.outline_thickness
        if settings
        else abs(float(getattr(mod, "thickness", 0.0)))
    )
    obj[PROP_REF_FOV_TAN] = _get_fov_factor(camera.data, scene)
    obj[PROP_REF_MODE] = REF_MODE_LOCKED
    return True


def store_unit_reference(obj, scene):
    """設定された線幅基準距離を補正基準として保存."""
    camera = get_line_camera(scene)
    if camera is None:
        return False
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None and obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is None:
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    obj[PROP_REF_DISTANCE] = _line_width_reference_distance(settings)
    obj[PROP_BASE_THICKNESS] = (
        settings.outline_thickness
        if settings
        else abs(float(getattr(mod, "thickness", 0.0)))
    )
    obj[PROP_REF_FOV_TAN] = _get_fov_factor(camera.data, scene)
    obj[PROP_REF_MODE] = REF_MODE_VIEW
    return True


def register() -> None:
    if _on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame_change)


def unregister() -> None:
    if _on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame_change)
