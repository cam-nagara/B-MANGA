"""フキダシ結合グループの表示用オブジェクト同期."""

from __future__ import annotations

from collections import defaultdict

import bpy
from mathutils import Vector

from . import balloon_fill_mesh
from . import balloon_line_mesh
from . import balloon_curve_object as bco
from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import python_deps

_logger = log.get_logger(__name__)

MERGE_KIND = "balloon_group"
MERGE_NAME_PREFIX = "balloon_merge_"
PROP_MERGE_DISPLAY_KIND = "bname_balloon_merge_display_kind"
PROP_MERGE_GROUP_ID = "bname_balloon_merge_group_id"
PROP_MERGE_SOURCE_IDS = "bname_balloon_merge_source_ids"

_UNION_OVERLAP_M = 1.0e-7
_LINE_Z_OFFSET_M = 0.00004


def sync_groups_for_page(scene: bpy.types.Scene, work, page) -> None:
    if scene is None or work is None or page is None:
        return
    groups = _valid_groups(page)
    for group_id, entries in groups.items():
        _ensure_group_display(scene, work, page, group_id, entries)
    _cleanup_group_objects(groups.keys())
    _sync_source_visibility(page, groups)


def cleanup_all() -> None:
    _cleanup_group_objects(())


def _valid_groups(page) -> dict[str, list[object]]:
    grouped: dict[str, list[object]] = defaultdict(list)
    for entry in getattr(page, "balloons", []) or []:
        group_id = str(getattr(entry, "merge_group_id", "") or "")
        if not group_id or not bool(getattr(entry, "visible", True)):
            continue
        grouped[group_id].append(entry)
    return {group_id: entries for group_id, entries in grouped.items() if len(entries) >= 2}


def _sync_source_visibility(page, groups: dict[str, list[object]]) -> None:
    merged_ids = {
        str(getattr(entry, "id", "") or "")
        for entries in groups.values()
        for entry in entries
    }
    for entry in getattr(page, "balloons", []) or []:
        balloon_id = str(getattr(entry, "id", "") or "")
        hidden = balloon_id in merged_ids or not bool(getattr(entry, "visible", True))
        _set_source_display_hidden(balloon_id, hidden)


def _set_source_display_hidden(balloon_id: str, hidden: bool) -> None:
    if not balloon_id:
        return
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is not None:
        obj.hide_viewport = hidden
        obj.hide_render = hidden
    for candidate in bpy.data.objects:
        if _is_generated_balloon_display(candidate, balloon_id):
            candidate.hide_viewport = hidden
            candidate.hide_render = hidden


def _is_generated_balloon_display(obj: bpy.types.Object, balloon_id: str) -> bool:
    owner = str(obj.get(balloon_fill_mesh.PROP_BALLOON_FILL_MESH_OWNER_ID, "") or "")
    if owner == balloon_id:
        return True
    owner = str(obj.get(balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
    return owner == balloon_id


def _cleanup_group_objects(valid_group_ids) -> None:
    valid = {str(item or "") for item in valid_group_ids}
    for obj in list(bpy.data.objects):
        if obj.get(PROP_MERGE_DISPLAY_KIND) != "display":
            continue
        group_id = str(obj.get(PROP_MERGE_GROUP_ID, "") or "")
        if group_id in valid:
            continue
        data = getattr(obj, "data", None)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        _remove_unused_mesh(data)


def _ensure_group_display(scene, work, page, group_id: str, entries: list[object]) -> None:
    polygons, max_z = _source_polygons(entries)
    if len(polygons) < 2:
        return
    mesh = _build_union_mesh(group_id, polygons, entries[0])
    if mesh is None:
        return
    obj = _display_object(group_id, mesh)
    _stamp_display_object(scene, work, page, obj, group_id, entries, max_z=max_z)


def _display_object(group_id: str, mesh: bpy.types.Mesh) -> bpy.types.Object:
    obj_name = f"{MERGE_NAME_PREFIX}{group_id}"
    obj = on.find_object_by_bname_id(group_id, kind=MERGE_KIND)
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        old_data = getattr(obj, "data", None)
        obj.data = mesh
        _remove_unused_mesh(old_data)
    obj[PROP_MERGE_DISPLAY_KIND] = "display"
    obj[PROP_MERGE_GROUP_ID] = group_id
    obj.hide_select = True
    obj.hide_viewport = False
    obj.hide_render = False
    return obj


def _stamp_display_object(scene, work, page, obj, group_id: str, entries, *, max_z: float) -> None:
    first = entries[0]
    parent_key = str(getattr(first, "parent_key", "") or str(getattr(page, "id", "") or ""))
    parent_kind = str(getattr(first, "parent_kind", "") or "page")
    if not parent_key:
        parent_kind = "page"
        parent_key = str(getattr(page, "id", "") or "")
    z_index = max(_balloon_z_index(scene, page, entry) for entry in entries) + 5
    los.stamp_layer_object(
        obj,
        kind=MERGE_KIND,
        bname_id=group_id,
        title=group_id.replace("balloon_group_", "フキダシ結合 "),
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        scene=scene,
        apply_page_offset=False,
    )
    obj.location.x = 0.0
    obj.location.y = 0.0
    obj.location.z = max(float(max_z), float(obj.location.z))
    obj[PROP_MERGE_SOURCE_IDS] = ",".join(str(getattr(entry, "id", "") or "") for entry in entries)


def _source_polygons(entries) -> tuple[list[list[tuple[float, float]]], float]:
    polygons: list[list[tuple[float, float]]] = []
    max_z = 0.0
    for entry in entries:
        obj = on.find_object_by_bname_id(str(getattr(entry, "id", "") or ""), kind="balloon")
        if obj is None or getattr(obj, "type", "") != "CURVE":
            continue
        max_z = max(max_z, float(getattr(obj.location, "z", 0.0) or 0.0))
        polygons.extend(_curve_world_polygons(obj))
    return polygons, max_z


def _curve_world_polygons(obj: bpy.types.Object) -> list[list[tuple[float, float]]]:
    curve = getattr(obj, "data", None)
    if curve is None:
        return []
    out: list[list[tuple[float, float]]] = []
    for spline in getattr(curve, "splines", []) or []:
        role_radius = _spline_role_radius(spline)
        if role_radius is not None and role_radius >= 50.0:
            continue
        pts = _sample_spline_world(obj, spline)
        if len(pts) >= 3:
            out.append(_dedupe_polygon(pts))
    return [pts for pts in out if len(pts) >= 3 and abs(_area(pts)) > 1.0e-12]


def _spline_role_radius(spline) -> float | None:
    if getattr(spline, "type", "") == "BEZIER":
        points = getattr(spline, "bezier_points", []) or []
    else:
        points = getattr(spline, "points", []) or []
    for point in points:
        try:
            radius = float(getattr(point, "radius", 1.0))
        except Exception:  # noqa: BLE001
            continue
        if radius >= 50.0:
            return radius
    return None


def _sample_spline_world(obj: bpy.types.Object, spline) -> list[tuple[float, float]]:
    if getattr(spline, "type", "") == "BEZIER":
        return _sample_bezier_world(obj, spline)
    pts = []
    for point in getattr(spline, "points", []) or []:
        co = getattr(point, "co", None)
        if co is None:
            continue
        world = obj.matrix_world @ Vector((float(co.x), float(co.y), float(co.z)))
        pts.append((float(world.x), float(world.y)))
    return pts


def _sample_bezier_world(obj: bpy.types.Object, spline, steps: int = 10) -> list[tuple[float, float]]:
    points = list(getattr(spline, "bezier_points", []) or [])
    count = len(points)
    if count < 2:
        return []
    segment_count = count if getattr(spline, "use_cyclic_u", False) else count - 1
    pts: list[tuple[float, float]] = []
    for index in range(segment_count):
        p0 = points[index]
        p1 = points[(index + 1) % count]
        for step in range(steps):
            pos = _sample_cubic_vec(
                Vector(p0.co),
                Vector(p0.handle_right),
                Vector(p1.handle_left),
                Vector(p1.co),
                step / max(1, steps),
            )
            world = obj.matrix_world @ pos
            pts.append((float(world.x), float(world.y)))
    return pts


def _sample_cubic_vec(p0: Vector, h0: Vector, h1: Vector, p1: Vector, t: float) -> Vector:
    mt = 1.0 - t
    return (mt**3) * p0 + (3.0 * mt * mt * t) * h0 + (3.0 * mt * t * t) * h1 + (t**3) * p1


def _build_union_mesh(group_id: str, polygons, style_entry) -> bpy.types.Mesh | None:
    union = _union_source_polygons(polygons)
    if union is None:
        return None
    mesh = bpy.data.meshes.new(f"{MERGE_NAME_PREFIX}{group_id}_mesh")
    verts, faces, material_indices = _mesh_parts_from_union(union, style_entry)
    if not faces or len(verts) < 3:
        _remove_unused_mesh(mesh)
        return None
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    line_mat = bco._ensure_balloon_curve_material(  # noqa: SLF001
        None,
        material_name=f"{bco.BALLOON_CURVE_MATERIAL_PREFIX}{group_id}",
        entry=style_entry,
    )
    fill_mat = bco._ensure_fill_material(f"{bco.BALLOON_FILL_MATERIAL_PREFIX}{group_id}", style_entry)  # noqa: SLF001
    mesh.materials.append(line_mat)
    mesh.materials.append(fill_mat)
    for index, poly in enumerate(mesh.polygons):
        poly.material_index = material_indices[index] if index < len(material_indices) else 1
    return mesh


def _union_source_polygons(polygons):
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    source = []
    for points in polygons:
        pts = _dedupe_polygon(points)
        if len(pts) < 3:
            continue
        try:
            poly = Polygon(_ccw(pts))
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area <= 0.0:
                continue
            source.append(poly)
        except Exception:  # noqa: BLE001
            continue
    if not source:
        return None
    try:
        buffered = [poly.buffer(_UNION_OVERLAP_M, join_style=2, mitre_limit=50.0) for poly in source]
        union = unary_union(buffered)
        if union.is_empty:
            return None
        union = union.buffer(-_UNION_OVERLAP_M, join_style=2, mitre_limit=50.0)
        if not union.is_valid:
            union = union.buffer(0)
        return None if union.is_empty or union.area <= 0.0 else union
    except Exception:  # noqa: BLE001
        return None


def _mesh_parts_from_union(union, style_entry):
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    material_indices: list[int] = []
    for poly in _iter_polygons(union):
        outer, holes = _polygon_to_outer_holes(poly)
        pts, tris = balloon_line_mesh._triangulate_polygon(outer, holes)  # noqa: SLF001
        if not tris or len(pts) < 3:
            continue
        base = len(verts)
        verts.extend((float(x), float(y), 0.0) for x, y in pts)
        faces.extend(tuple(index + base for index in tri) for tri in tris)
        material_indices.extend([1] * len(tris))
    for outer, holes in _line_band_polygons(union, style_entry):
        pts, tris = balloon_line_mesh._triangulate_polygon(outer, holes)  # noqa: SLF001
        if not tris or len(pts) < 3:
            continue
        base = len(verts)
        verts.extend((float(x), float(y), _LINE_Z_OFFSET_M) for x, y in pts)
        faces.extend(tuple(index + base for index in tri) for tri in tris)
        material_indices.extend([0] * len(tris))
    return verts, faces, material_indices


def _iter_polygons(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return [poly for poly in geom.geoms if not poly.is_empty and poly.area > 0.0]
    try:
        return [
            poly
            for poly in geom.geoms
            if getattr(poly, "geom_type", "") == "Polygon" and not poly.is_empty and poly.area > 0.0
        ]
    except Exception:  # noqa: BLE001
        return []


def _polygon_to_outer_holes(poly) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    outer = [(float(x), float(y)) for x, y in poly.exterior.coords]
    holes = [[(float(x), float(y)) for x, y in inner.coords] for inner in poly.interiors]
    return outer, holes


def _line_band_polygons(union, style_entry) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    if str(getattr(style_entry, "line_style", "") or "") == "none":
        return []
    width = max(0.0, float(getattr(style_entry, "line_width_mm", 0.3) or 0.0)) * 0.001
    if width <= 1.0e-9:
        return []
    try:
        band = union.boundary.buffer(width * 0.5, cap_style=2, join_style=2, mitre_limit=50.0)
        if band.is_empty:
            return []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for poly in _iter_polygons(band):
        outer, holes = _polygon_to_outer_holes(poly)
        if len(outer) >= 3:
            out.append((outer, holes))
    return out


def _balloon_z_index(scene, page, entry) -> int:
    return bco._balloon_z_index(scene, page, str(getattr(entry, "id", "") or ""))  # noqa: SLF001


def _dedupe_polygon(points):
    out = []
    for point in points:
        xy = (float(point[0]), float(point[1]))
        if not out or abs(out[-1][0] - xy[0]) > 1.0e-9 or abs(out[-1][1] - xy[1]) > 1.0e-9:
            out.append(xy)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= 1.0e-9 and abs(out[0][1] - out[-1][1]) <= 1.0e-9:
        out.pop()
    return out


def _ccw(points):
    pts = list(points)
    return pts if _area(pts) >= 0.0 else list(reversed(pts))


def _area(points) -> float:
    total = 0.0
    for i, point in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        total += point[0] * nxt[1] - nxt[0] * point[1]
    return total * 0.5


def _remove_unused_mesh(data) -> None:
    if isinstance(data, bpy.types.Mesh) and getattr(data, "users", 0) == 0:
        try:
            bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass
