"""ウニフラ / 白抜き線フキダシ用の白線メッシュ."""

from __future__ import annotations

from typing import Optional

import bpy

from . import balloon_line_mesh
from . import balloon_shapes
from . import line_pattern


def _pct(entry, attr: str, default: float) -> float:
    try:
        return max(0.0, min(200.0, float(getattr(entry, attr, default) or 0.0)))
    except Exception:  # noqa: BLE001
        return default


def _white_widths_m(entry, line_width_m: float) -> tuple[float, float, bool]:
    _dynamic, _black_valley_pct, _black_peak_pct, black_both_zero = balloon_line_mesh._line_dynamic_width_params(entry)
    if black_both_zero:
        return 0.0, 0.0, True
    white_base_m = line_width_m * _pct(entry, "flash_white_line_width_percent", 100.0) / 100.0
    white_valley_m = white_base_m * _pct(entry, "flash_white_line_valley_width_pct", 0.0) / 100.0
    white_peak_m = white_base_m * _pct(entry, "flash_white_line_peak_width_pct", 100.0) / 100.0
    both_zero = white_valley_m <= 1.0e-9 and white_peak_m <= 1.0e-9
    return white_valley_m, white_peak_m, both_zero


def _polygons_to_body_clipped(polygons, body_poly):
    balloon_line_mesh.python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    pieces = []
    for outer, holes in polygons:
        try:
            poly = Polygon(outer, holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty and poly.area > 0:
                pieces.append(poly)
        except Exception:  # noqa: BLE001
            continue
    if not pieces:
        return []
    try:
        band = unary_union(pieces).intersection(body_poly)
    except Exception:  # noqa: BLE001
        return []
    return balloon_line_mesh._shapely_geom_to_outer_holes_list(band)


def _uniform_inner_band(body_poly, width_m: float, *, valley_sharp: bool):
    if width_m <= 1.0e-9:
        return []
    join = 2 if valley_sharp else 1
    mitre = balloon_line_mesh._SHARP_MITRE_LIMIT if valley_sharp else balloon_line_mesh._ROUND_MITRE_LIMIT
    try:
        inner = body_poly.buffer(-width_m, join_style=join, mitre_limit=mitre)
        band = body_poly if inner.is_empty else body_poly.difference(inner)
    except Exception:  # noqa: BLE001
        return []
    return balloon_line_mesh._shapely_geom_to_outer_holes_list(band)


def _body_samples_and_poly(entry, body_object: bpy.types.Object):
    body_spline = balloon_line_mesh._resolve_body_spline(body_object)
    if body_spline is None:
        return None
    samples = balloon_line_mesh._sample_body_bezier(body_spline, balloon_line_mesh.SAMPLES_PER_SEGMENT)
    if len(samples) < 3:
        return None
    samples, _tails_merged = balloon_line_mesh._outline_samples_with_tails(entry, samples)
    body_poly = balloon_line_mesh._build_body_polygon(samples)
    if body_poly is None:
        return None
    return samples, body_poly


def _flash_white_line_polygons(entry, samples, body_poly, line_style: str, line_width_mm: float, shape_norm: str):
    line_width_m = line_width_mm * 0.001
    white_valley_m, white_peak_m, both_zero = _white_widths_m(entry, line_width_m)
    if both_zero:
        return []

    valley_sharp = balloon_line_mesh._valley_sharp_for_entry(entry)
    max_width_m = max(white_valley_m, white_peak_m)
    if line_style in {"dashed", "dotted"}:
        polygons = balloon_line_mesh._build_dashed_band_polygons(
            samples,
            line_width_m=max_width_m * 2.0,
            line_style=line_style,
            valley_sharp=valley_sharp,
            dash_segment_mm=line_pattern.dashed_segment_mm(entry, line_width_mm),
            dash_gap_mm=line_pattern.dashed_gap_mm(entry, line_width_mm),
            dotted_gap_mm=line_pattern.dotted_gap_mm(entry, line_width_mm),
        )
        return _polygons_to_body_clipped(polygons, body_poly)
    elif abs(white_valley_m - white_peak_m) <= 1.0e-9:
        return _uniform_inner_band(body_poly, max_width_m, valley_sharp=valley_sharp)
    body_center_m = balloon_line_mesh._balloon_center_m_from_samples(samples)
    sub_polys = balloon_line_mesh._build_dynamic_multi_line_polygons(
        body_samples=samples,
        signed_offset_m=0.0,
        base_width_m=max(max_width_m * 2.0, 1.0e-9),
        valley_width_m=white_valley_m * 2.0,
        peak_width_m=white_peak_m * 2.0,
        length_scale=1.0,
        valley_sharp=valley_sharp,
        balloon_center_m=body_center_m,
        peak_extension_m=0.0,
        outside_align=False,
        peaks_rounded=(shape_norm in balloon_line_mesh._ROUNDED_PEAK_SHAPES),
    )
    return _polygons_to_body_clipped(sub_polys, body_poly)


def ensure_balloon_flash_white_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    white_line_material: bpy.types.Material,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    """黒線の内側に、黒線幅を 100% とする白線を生成する."""

    del work, page
    balloon_id = str(getattr(entry, "id", "") or "")
    shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    enabled = bool(getattr(entry, "flash_white_line_enabled", True))
    if (
        not balloon_id
        or not balloon_shapes.is_flash_balloon_shape(shape_norm)
        or not enabled
        or line_style == "none"
        or line_width_mm <= 1.0e-6
    ):
        balloon_line_mesh.remove_balloon_flash_white_line_mesh(balloon_id)
        return None

    body = _body_samples_and_poly(entry, body_object)
    if body is None:
        balloon_line_mesh.remove_balloon_flash_white_line_mesh(balloon_id)
        return None
    samples, body_poly = body
    polygons = _flash_white_line_polygons(entry, samples, body_poly, line_style, line_width_mm, shape_norm)
    if not polygons:
        balloon_line_mesh.remove_balloon_flash_white_line_mesh(balloon_id)
        return None

    mesh_name = balloon_line_mesh._flash_white_line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    balloon_line_mesh._build_band_mesh_from_polygons(
        mesh,
        polygons,
        balloon_line_mesh.FLASH_WHITE_LINE_Z_OFFSET_M,
    )
    return balloon_line_mesh._attach_band_mesh_object(
        obj_name=balloon_line_mesh._flash_white_line_mesh_object_name(balloon_id),
        mesh=mesh,
        material=white_line_material,
        body_object=body_object,
        scene=scene,
        kind=balloon_line_mesh._KIND_FLASH_WHITE_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )
