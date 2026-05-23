"""フキダシ結合グループの表示用オブジェクト同期."""

from __future__ import annotations

from collections import defaultdict

import bpy
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from . import balloon_curve_object as bco
from . import layer_object_sync as los
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

MERGE_KIND = "balloon_group"
MERGE_NAME_PREFIX = "balloon_merge_"
PROP_MERGE_DISPLAY_KIND = "bname_balloon_merge_display_kind"
PROP_MERGE_GROUP_ID = "bname_balloon_merge_group_id"
PROP_MERGE_SOURCE_IDS = "bname_balloon_merge_source_ids"

_TEMP_PREFIX = "__bname_balloon_merge_tmp_"


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
        obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
        if obj is None:
            continue
        hidden = balloon_id in merged_ids or not bool(getattr(entry, "visible", True))
        obj.hide_viewport = hidden
        obj.hide_render = hidden


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
        pts = _sample_spline_world(obj, spline)
        if len(pts) >= 3:
            out.append(_dedupe_polygon(pts))
    return [pts for pts in out if len(pts) >= 3 and abs(_area(pts)) > 1.0e-12]


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
            pos = bco._sample_cubic_vec(  # noqa: SLF001
                Vector(p0.co),
                Vector(p0.handle_right),
                Vector(p1.handle_left),
                Vector(p1.co),
                step / max(1, steps),
            )
            world = obj.matrix_world @ pos
            pts.append((float(world.x), float(world.y)))
    return pts


def _build_union_mesh(group_id: str, polygons, style_entry) -> bpy.types.Mesh | None:
    temp_objects = _make_temp_prisms(group_id, polygons)
    try:
        base = _boolean_union(temp_objects)
        if base is None:
            return None
        return _mesh_from_union(group_id, base.data, style_entry)
    finally:
        for obj in temp_objects:
            if obj.name in bpy.data.objects:
                data = getattr(obj, "data", None)
                bpy.data.objects.remove(obj, do_unlink=True)
                _remove_unused_mesh(data)


def _make_temp_prisms(group_id: str, polygons) -> list[bpy.types.Object]:
    objects = []
    for index, polygon in enumerate(polygons):
        mesh = _prism_mesh(f"{_TEMP_PREFIX}{group_id}_{index}_mesh", polygon)
        if mesh is None:
            continue
        obj = bpy.data.objects.new(f"{_TEMP_PREFIX}{group_id}_{index}", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.hide_viewport = True
        obj.hide_render = True
        objects.append(obj)
    return objects


def _prism_mesh(name: str, polygon) -> bpy.types.Mesh | None:
    pts = _ccw(_dedupe_polygon(polygon))
    if len(pts) < 3:
        return None
    half = 0.00005
    verts = [(x, y, half) for x, y in pts] + [(x, y, -half) for x, y in pts]
    tris = tessellate_polygon([[Vector((x, y, half)) for x, y in pts]])
    faces = [tuple(tri) for tri in tris]
    count = len(pts)
    faces.extend(tuple(count + i for i in reversed(tri)) for tri in tris)
    for i in range(count):
        faces.append((i, (i + 1) % count, count + (i + 1) % count, count + i))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _boolean_union(objects: list[bpy.types.Object]) -> bpy.types.Object | None:
    if not objects:
        return None
    base = objects[0]
    for other in objects[1:]:
        mod = base.modifiers.new("B-Name フキダシ結合", "BOOLEAN")
        mod.operation = "UNION"
        try:
            mod.solver = "EXACT"
        except Exception:  # noqa: BLE001
            pass
        mod.object = other
        _apply_modifier(base, mod)
    return base


def _apply_modifier(obj: bpy.types.Object, modifier) -> None:
    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    for selected in tuple(getattr(bpy.context, "selected_objects", []) or []):
        selected.select_set(False)
    obj.select_set(True)
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=modifier.name)


def _mesh_from_union(group_id: str, source_mesh: bpy.types.Mesh, style_entry) -> bpy.types.Mesh:
    source_mesh.update(calc_edges=True)
    top_polys = [poly for poly in source_mesh.polygons if float(poly.normal.z) > 0.5]
    mesh = bpy.data.meshes.new(f"{MERGE_NAME_PREFIX}{group_id}_mesh")
    verts, faces, material_indices = _top_faces(source_mesh, top_polys)
    _append_outline_quads(source_mesh, top_polys, verts, faces, material_indices, style_entry)
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


def _top_faces(source_mesh, top_polys):
    vertex_map: dict[int, int] = {}
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    material_indices: list[int] = []
    for poly in top_polys:
        face = []
        for vi in poly.vertices:
            if vi not in vertex_map:
                co = source_mesh.vertices[vi].co
                vertex_map[vi] = len(verts)
                verts.append((float(co.x), float(co.y), 0.0))
            face.append(vertex_map[vi])
        if len(face) >= 3:
            faces.append(tuple(face))
            material_indices.append(1)
    return verts, faces, material_indices


def _append_outline_quads(source_mesh, top_polys, verts, faces, material_indices, style_entry) -> None:
    if str(getattr(style_entry, "line_style", "") or "") == "none":
        return
    top_counts: dict[tuple[int, int], int] = defaultdict(int)
    for poly in top_polys:
        for edge_key in poly.edge_keys:
            top_counts[tuple(sorted(edge_key))] += 1
    half = float(getattr(style_entry, "line_width_mm", 0.3) or 0.3) * 0.001 * 0.5
    if half <= 0.0:
        return
    for edge_key, count in top_counts.items():
        if count != 1:
            continue
        p0 = source_mesh.vertices[edge_key[0]].co
        p1 = source_mesh.vertices[edge_key[1]].co
        quad = _segment_quad((float(p0.x), float(p0.y)), (float(p1.x), float(p1.y)), half)
        if quad is None:
            continue
        start = len(verts)
        verts.extend((x, y, 0.00004) for x, y in quad)
        faces.append((start, start + 1, start + 2, start + 3))
        material_indices.append(0)


def _segment_quad(p0, p1, half_width: float):
    dx = float(p1[0]) - float(p0[0])
    dy = float(p1[1]) - float(p0[1])
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 1.0e-12:
        return None
    nx = -dy / length * half_width
    ny = dx / length * half_width
    return (
        (p0[0] + nx, p0[1] + ny),
        (p1[0] + nx, p1[1] + ny),
        (p1[0] - nx, p1[1] - ny),
        (p0[0] - nx, p0[1] - ny),
    )


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
