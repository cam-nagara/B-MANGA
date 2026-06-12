"""フキダシの連続楕円しっぽ (線種「楕円」) の Mesh 焼き込み.

心の声のように、しっぽの先へ向かって小さくなる楕円を連ねて描く。
塗りは親フキダシの塗り素材、線は親フキダシの線素材をそのまま使う
(しっぽ独自の色は持たない)。
"""

from __future__ import annotations

import math
from typing import Optional

import bpy

from . import balloon_tail_boolean, balloon_tail_geom, free_transform, log
from .balloon_line_mesh import (
    LINE_Z_OFFSET_M,
    _attach_band_mesh_object,
    _body_samples_for_line_mesh,
    _entry_local_offset_mm,
    scaled_entry_width_mm,
)
from .balloon_shapes import Rect
from .geom import mm_to_m

_logger = log.get_logger(__name__)

KIND_TAIL_ELLIPSE_FILL = "balloon_tail_ellipse_fill_mesh"
KIND_TAIL_ELLIPSE_LINE = "balloon_tail_ellipse_line_mesh"
_FILL_OBJ_PREFIX = "balloon_tail_ellipse_fill_"
_LINE_OBJ_PREFIX = "balloon_tail_ellipse_line_"
# 塗りは主線より僅かに下、本体塗りより上に置く
_FILL_Z_OFFSET_M = LINE_Z_OFFSET_M * 0.5
_ELLIPSE_SEGMENTS = 48


def _ellipse_polygons_local_m(entry) -> list[list[tuple[float, float]]]:
    """全しっぽの楕円列を balloon-local m の polygon 点列に変換する."""
    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    ox_mm, oy_mm = _entry_local_offset_mm(entry)
    polygons: list[list[tuple[float, float]]] = []
    for tail in getattr(entry, "tails", []) or []:
        if not balloon_tail_geom.is_ellipse_chain(tail):
            continue
        try:
            for ellipse in balloon_tail_geom.ellipse_chain_for_tail(rect, tail):
                pts_mm = balloon_tail_geom.ellipse_polygon(ellipse, _ELLIPSE_SEGMENTS)
                pts_mm = free_transform.transform_entry_local_points(entry, pts_mm)
                if len(pts_mm) < 3:
                    continue
                polygons.append([(mm_to_m(x + ox_mm), mm_to_m(y + oy_mm)) for x, y in pts_mm])
        except Exception:  # noqa: BLE001
            _logger.exception("balloon tail ellipse chain build failed")
    return polygons


def _build_fill_mesh(mesh: bpy.types.Mesh, polygons: list[list[tuple[float, float]]], z_m: float) -> None:
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    for poly in polygons:
        start = len(verts)
        verts.extend((x, y, z_m) for x, y in poly)
        faces.append(tuple(range(start, start + len(poly))))
    mesh.clear_geometry()
    if verts:
        mesh.from_pydata(verts, [], faces)
    mesh.update()


def _build_ring_mesh(
    mesh: bpy.types.Mesh,
    polygons: list[list[tuple[float, float]]],
    line_width_m: float,
    z_m: float,
) -> None:
    """各楕円の外周に沿った線の帯 (リング) を四角形ストリップで作る."""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    half = max(1.0e-6, line_width_m * 0.5)
    for poly in polygons:
        n = len(poly)
        if n < 3:
            continue
        cx = sum(p[0] for p in poly) / n
        cy = sum(p[1] for p in poly) / n
        start = len(verts)
        for x, y in poly:
            dx = x - cx
            dy = y - cy
            dist = math.hypot(dx, dy)
            if dist <= 1.0e-9:
                ux, uy = 1.0, 0.0
            else:
                ux, uy = dx / dist, dy / dist
            verts.append((x + ux * half, y + uy * half, z_m))
            verts.append((x - ux * half, y - uy * half, z_m))
        for i in range(n):
            a = start + i * 2
            b = start + ((i + 1) % n) * 2
            faces.append((a, a + 1, b + 1, b))
    mesh.clear_geometry()
    if verts:
        mesh.from_pydata(verts, [], faces)
    mesh.update()


def _mesh_for(name: str) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.get(name)
    if mesh is None:
        mesh = bpy.data.meshes.new(name)
    return mesh


def ensure_balloon_tail_ellipse_meshes(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    fill_material: bpy.types.Material | None,
    line_material: bpy.types.Material | None,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    """連続楕円しっぽの塗り・線メッシュを生成・更新する。対象が無ければ撤去する."""
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    polygons = _ellipse_polygons_local_m(entry)
    # 本体に重なる楕円は (三角しっぽと同様に) 本体の輪郭へ結合されるため、
    # 個別の塗り・線メッシュからは除外する。
    if polygons:
        body_samples = _body_samples_for_line_mesh(entry, body_object)
        if len(body_samples) >= 3:
            _touching, separate = balloon_tail_boolean.split_indices_touching_body(
                [(float(s[0]), float(s[1])) for s in body_samples], polygons
            )
            polygons = [polygons[i] for i in separate]
    if not polygons or not bool(getattr(entry, "visible", True)):
        remove_balloon_tail_ellipse_meshes(balloon_id)
        return None

    result = None
    if fill_material is not None:
        fill_mesh = _mesh_for(f"{_FILL_OBJ_PREFIX}{balloon_id}_mesh")
        _build_fill_mesh(fill_mesh, polygons, _FILL_Z_OFFSET_M)
        result = _attach_band_mesh_object(
            obj_name=f"{_FILL_OBJ_PREFIX}{balloon_id}",
            mesh=fill_mesh,
            material=fill_material,
            body_object=body_object,
            scene=scene,
            kind=KIND_TAIL_ELLIPSE_FILL,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
        )
    else:
        _remove_named(f"{_FILL_OBJ_PREFIX}{balloon_id}")

    line_width_mm = scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    line_style = str(getattr(entry, "line_style", "solid") or "solid")
    if line_material is not None and line_style != "none" and line_width_mm > 1.0e-6:
        line_mesh = _mesh_for(f"{_LINE_OBJ_PREFIX}{balloon_id}_mesh")
        _build_ring_mesh(line_mesh, polygons, line_width_mm * 0.001, LINE_Z_OFFSET_M)
        result = _attach_band_mesh_object(
            obj_name=f"{_LINE_OBJ_PREFIX}{balloon_id}",
            mesh=line_mesh,
            material=line_material,
            body_object=body_object,
            scene=scene,
            kind=KIND_TAIL_ELLIPSE_LINE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
        )
    else:
        _remove_named(f"{_LINE_OBJ_PREFIX}{balloon_id}")
    return result


def _remove_named(obj_name: str) -> None:
    obj = bpy.data.objects.get(obj_name)
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


def remove_balloon_tail_ellipse_meshes(balloon_id: str) -> None:
    if not balloon_id:
        return
    _remove_named(f"{_FILL_OBJ_PREFIX}{balloon_id}")
    _remove_named(f"{_LINE_OBJ_PREFIX}{balloon_id}")
