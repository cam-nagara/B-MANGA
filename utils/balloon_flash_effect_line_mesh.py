"""Flash balloon line mesh generated from effect-line radial strokes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Optional

import bpy

from ..operators import effect_line_gen
from . import balloon_line_mesh
from . import balloon_shapes
from . import effect_line_object
from . import free_transform
from . import object_preserve
from .geom import Rect, mm_to_m

BALLOON_FLASH_EFFECT_LINE_MESH_NAME_PREFIX = "balloon_flash_effect_line_mesh_"
_KIND_FLASH_EFFECT_LINE = "balloon_flash_effect_line_mesh"
_FLASH_LINE_Z_M = balloon_line_mesh.LINE_Z_OFFSET_M


def _flash_effect_line_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_FLASH_EFFECT_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _flash_effect_line_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_FLASH_EFFECT_LINE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _line_width_and_endpoint_pct(
    base_width_mm: float,
    middle_pct: float,
    endpoint_pct: float,
) -> tuple[float, float]:
    middle = max(0.0, float(middle_pct))
    width = max(0.0, float(base_width_mm)) * middle / 100.0
    if width <= 1.0e-9 or middle <= 1.0e-9:
        return 0.0, 0.0
    return width, _clamp(float(endpoint_pct) / middle * 100.0, 0.0, 100.0)


def _base_rect(entry) -> tuple[tuple[float, float], float, float]:
    width = max(0.001, float(getattr(entry, "width_mm", 0.0) or 0.0))
    height = max(0.001, float(getattr(entry, "height_mm", 0.0) or 0.0))
    rect = Rect(0.0, 0.0, width, height)
    points = balloon_shapes.flash_base_outline_for_entry(entry, rect) or balloon_shapes.outline_for_shape("ellipse", rect)
    min_x = min(float(x) for x, _y in points)
    max_x = max(float(x) for x, _y in points)
    min_y = min(float(y) for _x, y in points)
    max_y = max(float(y) for _x, y in points)
    center = ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
    return center, max(0.001, (max_x - min_x) * 0.5), max(0.001, (max_y - min_y) * 0.5)


def _default_easing_curve() -> str:
    return "0.0000,0.0000;1.0000,1.0000"


def _focus_params(entry, *, brush_mm: float, endpoint_pct: float) -> SimpleNamespace:
    spacing = max(0.01, float(getattr(entry, "flash_line_spacing_mm", 1.0) or 1.0))
    max_count = max(1, int(getattr(entry, "flash_line_count", 120) or 120))
    return SimpleNamespace(
        effect_type="focus",
        rotation_deg=0.0,
        start_shape="ellipse",
        end_shape="ellipse",
        start_rounded_corner_enabled=False,
        end_rounded_corner_enabled=False,
        brush_size_mm=max(0.01, brush_mm),
        brush_jitter_enabled=False,
        brush_jitter_amount=0.0,
        length_jitter_enabled=False,
        length_jitter_amount=0.0,
        end_length_jitter_enabled=False,
        end_length_jitter_amount=0.0,
        spacing_mode="distance",
        spacing_angle_deg=5.0,
        spacing_distance_mm=spacing,
        spacing_density_compensation=True,
        spacing_jitter_enabled=False,
        spacing_jitter_amount=0.0,
        max_line_count=max_count,
        bundle_enabled=False,
        bundle_line_count=1,
        bundle_line_count_jitter=0.0,
        bundle_gap_mm=0.0,
        bundle_gap_jitter_amount=0.0,
        bundle_jagged_enabled=False,
        bundle_jagged_height_percent=0.0,
        inout_apply="brush_size",
        in_percent=endpoint_pct,
        out_percent=endpoint_pct,
        in_start_percent=50.0,
        out_start_percent=50.0,
        in_easing_curve=_default_easing_curve(),
        out_easing_curve=_default_easing_curve(),
        inout_range_mode="percent",
        in_range_percent=50.0,
        out_range_percent=50.0,
        in_range_mm=10.0,
        out_range_mm=10.0,
        white_underlay_enabled=False,
        white_underlay_width_percent=0.0,
    )


def _white_outline_params(entry, *, black_brush_mm: float) -> SimpleNamespace:
    white_brush, white_endpoint = _line_width_and_endpoint_pct(
        black_brush_mm,
        float(getattr(entry, "flash_white_line_peak_width_pct", 100.0) or 100.0),
        float(getattr(entry, "flash_white_line_valley_width_pct", 0.0) or 0.0),
    )
    white_width_scale = max(0.0, float(getattr(entry, "flash_white_line_width_percent", 100.0) or 100.0)) / 100.0
    white_brush = max(0.01, white_brush * white_width_scale)
    spacing = max(0.0, float(getattr(entry, "flash_white_outline_spacing_mm", 0.25) or 0.25))
    return SimpleNamespace(
        effect_type="white_outline",
        rotation_deg=0.0,
        start_shape="ellipse",
        end_shape="ellipse",
        start_rounded_corner_enabled=False,
        end_rounded_corner_enabled=False,
        white_outline_count=max(1, int(getattr(entry, "flash_white_outline_count", 5) or 5)),
        white_outline_spacing_mm=spacing,
        white_outline_white_line_count_auto=False,
        white_outline_white_line_count=max(1, int(getattr(entry, "flash_white_outline_white_line_count", 24) or 24)),
        white_outline_width_mm=max(0.01, float(getattr(entry, "flash_white_outline_width_mm", 10.0) or 10.0)),
        white_outline_width_jitter_enabled=False,
        white_outline_width_min_percent=100.0,
        white_outline_length_jitter_enabled=False,
        white_outline_length_min_percent=100.0,
        white_outline_white_ratio_percent=70.0,
        white_outline_white_brush_mm=white_brush,
        white_outline_white_attenuation=0.0,
        white_outline_white_in_percent=white_endpoint,
        white_outline_white_out_percent=white_endpoint,
        white_outline_white_inout_range_mode="percent",
        white_outline_white_in_range_percent=50.0,
        white_outline_white_out_range_percent=50.0,
        white_outline_white_in_range_mm=10.0,
        white_outline_white_out_range_mm=10.0,
        white_outline_black_line_count_auto=False,
        white_outline_black_line_count=max(1, int(getattr(entry, "flash_white_outline_black_line_count", 3) or 3)),
        white_outline_black_direction="outside",
        white_outline_black_brush_mm=max(0.01, black_brush_mm),
        white_outline_black_spacing_mm=max(0.0, float(getattr(entry, "flash_white_outline_black_spacing_mm", spacing) or spacing)),
        white_outline_black_width_scale_percent=100.0,
        white_outline_black_length_scale_near_percent=100.0,
        white_outline_black_length_scale_far_percent=100.0,
        white_outline_black_attenuation=0.0,
        white_outline_angle_deg=0.0,
    )


def _stroke_distances(points_xyz: Sequence[tuple[float, float, float]]) -> list[float]:
    values = [0.0]
    for index in range(1, len(points_xyz)):
        ax, ay, az = points_xyz[index - 1]
        bx, by, bz = points_xyz[index]
        values.append(values[-1] + math.sqrt((bx - ax) ** 2 + (by - ay) ** 2 + (bz - az) ** 2))
    return values


def _middle_profile(distance: float, total: float, endpoint_factor: float) -> float:
    if total <= 1.0e-12:
        return endpoint_factor
    half = total * 0.5
    if distance <= half:
        return endpoint_factor + (1.0 - endpoint_factor) * (distance / max(half, 1.0e-12))
    return endpoint_factor + (1.0 - endpoint_factor) * ((total - distance) / max(half, 1.0e-12))


def _white_underlay_strokes(entry, line_strokes, black_brush_mm: float):
    if not bool(getattr(entry, "flash_white_line_enabled", True)):
        return []
    white_mid_width, white_endpoint_pct = _line_width_and_endpoint_pct(
        black_brush_mm,
        float(getattr(entry, "flash_white_line_peak_width_pct", 100.0) or 100.0),
        float(getattr(entry, "flash_white_line_valley_width_pct", 0.0) or 0.0),
    )
    width_scale = max(-300.0, min(300.0, float(getattr(entry, "flash_white_line_width_percent", 100.0) or 100.0))) / 100.0
    underlay_radius = max(0.0, (white_mid_width * abs(width_scale)) * 0.001)
    if underlay_radius <= 1.0e-12:
        return []
    endpoint_factor = _clamp(white_endpoint_pct / 100.0, 0.0, 1.0)
    side = 1.0 if width_scale >= 0.0 else -1.0
    out = []
    for stroke in line_strokes:
        if str(getattr(stroke, "role", "") or "line") != "line" or bool(getattr(stroke, "cyclic", False)):
            continue
        points = list(getattr(stroke, "points_xyz", None) or [])
        if len(points) < 2:
            continue
        distances = _stroke_distances(points)
        total = distances[-1]
        radii = [underlay_radius * _middle_profile(value, total, endpoint_factor) for value in distances]
        out.append(
            effect_line_gen.EffectLineStroke(
                points_xyz=points,
                radius=underlay_radius,
                cyclic=False,
                radii=radii,
                opacities=getattr(stroke, "opacities", None),
                role="underlay",
                curve_type=getattr(stroke, "curve_type", "POLY"),
                bezier_smooth=bool(getattr(stroke, "bezier_smooth", False)),
                density_end=float(getattr(stroke, "density_end", 1.0) or 1.0),
                side=side,
            )
        )
    return out


def _transform_stroke_to_local(entry, stroke) -> effect_line_gen.EffectLineStroke:
    ox_mm, oy_mm = balloon_line_mesh._entry_local_offset_mm(entry)
    points = []
    for x_m, y_m, z_m in list(getattr(stroke, "points_xyz", None) or []):
        x_mm = float(x_m) * 1000.0
        y_mm = float(y_m) * 1000.0
        x_mm, y_mm = free_transform.transform_entry_local_point(entry, x_mm, y_mm)
        points.append((mm_to_m(x_mm + ox_mm), mm_to_m(y_mm + oy_mm), _FLASH_LINE_Z_M + float(z_m)))
    return effect_line_gen.EffectLineStroke(
        points_xyz=points,
        radius=float(getattr(stroke, "radius", 0.0) or 0.0),
        cyclic=bool(getattr(stroke, "cyclic", False)),
        radii=list(getattr(stroke, "radii", []) or []) or None,
        opacities=list(getattr(stroke, "opacities", []) or []) or None,
        role=str(getattr(stroke, "role", "") or "line"),
        curve_type=str(getattr(stroke, "curve_type", "POLY") or "POLY"),
        bezier_smooth=bool(getattr(stroke, "bezier_smooth", False)),
        density_end=float(getattr(stroke, "density_end", 1.0) or 1.0),
        side=float(getattr(stroke, "side", 0.0) or 0.0),
    )


def _generated_strokes(entry):
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    black_brush_mm, black_endpoint_pct = _line_width_and_endpoint_pct(
        line_width_mm,
        float(getattr(entry, "line_peak_width_pct", 100.0) or 100.0),
        float(getattr(entry, "line_valley_width_pct", 0.0) or 0.0),
    )
    if black_brush_mm <= 1.0e-9:
        return []
    center, rx, ry = _base_rect(entry)
    shape = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
    if shape == "white_outline":
        params = _white_outline_params(entry, black_brush_mm=black_brush_mm)
        strokes = effect_line_gen.generate_white_outline_strokes(
            params,
            center,
            rx,
            ry,
            seed=int(getattr(getattr(entry, "shape_params", None), "shape_seed", 0) or 0),
        )
    else:
        params = _focus_params(entry, brush_mm=black_brush_mm, endpoint_pct=black_endpoint_pct)
        black_strokes = effect_line_gen.generate_strokes(
            params,
            center_xy_mm=center,
            radius_xy_mm=(rx, ry),
            seed=int(getattr(getattr(entry, "shape_params", None), "shape_seed", 0) or 0),
        )
        strokes = _white_underlay_strokes(entry, black_strokes, black_brush_mm) + black_strokes
    return [_transform_stroke_to_local(entry, stroke) for stroke in strokes]


def _set_mesh_materials(mesh: bpy.types.Mesh, materials: Sequence[bpy.types.Material | None]) -> None:
    try:
        mesh.materials.clear()
    except Exception:  # noqa: BLE001
        while len(mesh.materials) > 0:
            mesh.materials.pop(index=len(mesh.materials) - 1)
    for mat in materials:
        if mat is not None:
            mesh.materials.append(mat)


def ensure_balloon_flash_effect_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    white_material: bpy.types.Material,
    underlay_material: bpy.types.Material,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    del work, page
    balloon_id = str(getattr(entry, "id", "") or "")
    shape = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
    line_style = str(getattr(entry, "line_style", "") or "")
    if (
        not balloon_id
        or not balloon_shapes.is_flash_balloon_shape(shape)
        or line_style == "none"
        or float(getattr(entry, "line_width_mm", 0.0) or 0.0) <= 1.0e-9
    ):
        remove_balloon_flash_effect_line_mesh(balloon_id)
        return None
    strokes = _generated_strokes(entry)
    if not strokes:
        remove_balloon_flash_effect_line_mesh(balloon_id)
        return None
    mesh_name = _flash_effect_line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _set_mesh_materials(mesh, (line_material, white_material, underlay_material))
    effect_line_object._rebuild_effect_display_mesh(mesh, strokes)
    return balloon_line_mesh._attach_band_mesh_object(
        obj_name=_flash_effect_line_mesh_object_name(balloon_id),
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_FLASH_EFFECT_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )


def remove_balloon_flash_effect_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    obj_name = _flash_effect_line_mesh_object_name(balloon_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is None or object_preserve.is_preserved(obj):
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass


def cleanup_orphan_flash_effect_line_meshes(valid_balloon_ids: set[str]) -> int:
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        kind = str(obj.get(balloon_line_mesh.PROP_BALLOON_LINE_MESH_KIND, "") or "")
        if kind != _KIND_FLASH_EFFECT_LINE:
            continue
        owner_id = str(obj.get(balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
        if owner_id and owner_id in valid_balloon_ids:
            continue
        object_preserve.preserve_object(obj, "作品データにないフキダシ集中線メッシュを保持")
        removed += 1
    return removed
