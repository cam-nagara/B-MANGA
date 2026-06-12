"""フキダシの線しっぽ (線種「線」) の Mesh 焼き込み.

しっぽの中心線に沿った 1 本のストローク線を、親フキダシの線素材で塗る。
幅は根元幅→先端幅を補間し、「入り」「抜き」(%) で端を細く絞る。
本体とは結合しない (描いた線がそのまま見える)。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import balloon_tail_geom, free_transform, log
from .balloon_line_mesh import (
    LINE_Z_OFFSET_M,
    _attach_band_mesh_object,
    _build_band_mesh_from_polygons,
    _entry_local_offset_mm,
)
from .balloon_shapes import Rect
from .geom import mm_to_m

_logger = log.get_logger(__name__)

KIND_TAIL_STROKE = "balloon_tail_stroke_mesh"
_STROKE_OBJ_PREFIX = "balloon_tail_stroke_"


def _stroke_polygons_local_m(entry) -> list[list[tuple[float, float]]]:
    """全しっぽのうち線種「線」のストローク多角形を balloon-local m で返す."""
    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    ox_mm, oy_mm = _entry_local_offset_mm(entry)
    polygons: list[list[tuple[float, float]]] = []
    for tail in getattr(entry, "tails", []) or []:
        if not balloon_tail_geom.is_line_stroke(tail):
            continue
        try:
            pts_mm = balloon_tail_geom.line_stroke_polygon_for_tail(rect, tail)
            pts_mm = free_transform.transform_entry_local_points(entry, pts_mm)
            if len(pts_mm) < 3:
                continue
            polygons.append([(mm_to_m(x + ox_mm), mm_to_m(y + oy_mm)) for x, y in pts_mm])
        except Exception:  # noqa: BLE001
            _logger.exception("balloon tail line stroke build failed")
    return polygons


def ensure_balloon_tail_stroke_meshes(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material | None,
    mask_info=None,
    geometry_sig=None,
) -> Optional[bpy.types.Object]:
    """線しっぽのストロークメッシュを生成・更新する。対象が無ければ撤去する."""
    del work, page
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    from .balloon_line_mesh import band_geometry_cache_hit

    cached = band_geometry_cache_hit(f"{_STROKE_OBJ_PREFIX}{balloon_id}", geometry_sig)
    if cached is not None and line_material is not None:
        return _attach_band_mesh_object(
            obj_name=f"{_STROKE_OBJ_PREFIX}{balloon_id}",
            mesh=cached.data,
            material=line_material,
            body_object=body_object,
            scene=scene,
            kind=KIND_TAIL_STROKE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
            geometry_sig=geometry_sig,
        )
    line_style = str(getattr(entry, "line_style", "solid") or "solid")
    polygons = _stroke_polygons_local_m(entry)
    if (
        not polygons
        or line_material is None
        or line_style == "none"
        or not bool(getattr(entry, "visible", True))
    ):
        remove_balloon_tail_stroke_meshes(balloon_id)
        return None
    mesh_name = f"{_STROKE_OBJ_PREFIX}{balloon_id}_mesh"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_polygons(mesh, [(poly, []) for poly in polygons], LINE_Z_OFFSET_M)
    return _attach_band_mesh_object(
        obj_name=f"{_STROKE_OBJ_PREFIX}{balloon_id}",
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=KIND_TAIL_STROKE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
        geometry_sig=geometry_sig,
    )


def remove_balloon_tail_stroke_meshes(balloon_id: str) -> None:
    if not balloon_id:
        return
    obj = bpy.data.objects.get(f"{_STROKE_OBJ_PREFIX}{balloon_id}")
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass
