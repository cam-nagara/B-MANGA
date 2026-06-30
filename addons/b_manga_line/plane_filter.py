"""Lightweight sheet-mesh detection for generated B-MANGA Line features."""

from __future__ import annotations

import math

import bpy


PROP_SHEET_CACHE = "bml_sheet_mesh"
PROP_SHEET_SIGNATURE = "bml_sheet_signature"

_PARALLEL_DOT_MIN = math.cos(math.radians(8.0))
_PLANE_THICKNESS_RATIO = 0.005
_PLANE_THICKNESS_EPS = 1.0e-6
_MIN_EXTENT_EPS = 1.0e-9


def clear_cache(obj: bpy.types.Object) -> None:
    """Remove cached sheet detection result from an object."""
    for key in (PROP_SHEET_CACHE, PROP_SHEET_SIGNATURE):
        if key in obj:
            try:
                del obj[key]
            except (AttributeError, TypeError, RuntimeError):
                pass


def should_exclude_generated_lines(obj: bpy.types.Object, settings=None) -> bool:
    """Return True when inner/intersection lines should be skipped."""
    if settings is None:
        settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None:
        return False
    if not bool(getattr(settings, "exclude_sheet_meshes", False)):
        return False
    return is_sheet_mesh(obj)


def is_sheet_mesh(obj: bpy.types.Object, *, use_cache: bool = True) -> bool:
    """Detect flat polygon-card meshes without evaluated mesh conversion."""
    if obj.type != "MESH" or obj.data is None:
        return False
    signature = _mesh_signature(obj)
    if use_cache and obj.get(PROP_SHEET_SIGNATURE) == signature:
        return bool(obj.get(PROP_SHEET_CACHE, False))

    result = _detect_sheet_mesh(obj)
    try:
        obj[PROP_SHEET_SIGNATURE] = signature
        obj[PROP_SHEET_CACHE] = bool(result)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return result


def _mesh_signature(obj: bpy.types.Object) -> str:
    mesh = obj.data
    dims = _local_dimensions(mesh)
    return ":".join((
        str(mesh.as_pointer()),
        str(len(mesh.vertices)),
        str(len(mesh.polygons)),
        str(len(mesh.edges)),
        f"{dims[0]:.6g}",
        f"{dims[1]:.6g}",
        f"{dims[2]:.6g}",
    ))


def _local_dimensions(mesh: bpy.types.Mesh) -> tuple[float, float, float]:
    if not mesh.vertices:
        return (0.0, 0.0, 0.0)
    first = mesh.vertices[0].co
    min_x = max_x = float(first.x)
    min_y = max_y = float(first.y)
    min_z = max_z = float(first.z)
    for vertex in mesh.vertices[1:]:
        co = vertex.co
        min_x = min(min_x, float(co.x))
        max_x = max(max_x, float(co.x))
        min_y = min(min_y, float(co.y))
        max_y = max(max_y, float(co.y))
        min_z = min(min_z, float(co.z))
        max_z = max(max_z, float(co.z))
    return (max_x - min_x, max_y - min_y, max_z - min_z)


def _detect_sheet_mesh(obj: bpy.types.Object) -> bool:
    mesh = obj.data
    if len(mesh.vertices) < 3 or len(mesh.polygons) < 1:
        return False
    max_extent = max(_local_dimensions(mesh))
    if max_extent <= _MIN_EXTENT_EPS:
        return False
    normal = _dominant_parallel_normal(mesh)
    if normal is None:
        return False
    return _vertices_near_plane(mesh, normal, max_extent)


def _dominant_parallel_normal(mesh: bpy.types.Mesh):
    normal = None
    for poly in mesh.polygons:
        current = poly.normal
        if current.length <= _MIN_EXTENT_EPS:
            continue
        current = current.normalized()
        if normal is None:
            normal = current
            continue
        if abs(float(normal.dot(current))) < _PARALLEL_DOT_MIN:
            return None
    return normal


def _vertices_near_plane(
    mesh: bpy.types.Mesh,
    normal,
    max_extent: float,
) -> bool:
    origin = mesh.vertices[0].co
    min_distance = max_distance = 0.0
    for vertex in mesh.vertices:
        distance = float((vertex.co - origin).dot(normal))
        min_distance = min(min_distance, distance)
        max_distance = max(max_distance, distance)
    thickness = max_distance - min_distance
    limit = max(_PLANE_THICKNESS_EPS, max_extent * _PLANE_THICKNESS_RATIO)
    return thickness <= limit
