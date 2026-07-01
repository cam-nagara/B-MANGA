"""フキダシ結合グループの表示用オブジェクト同期."""

from __future__ import annotations

import hashlib
from collections import defaultdict

import bpy
from mathutils import Matrix, Vector

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
PROP_MERGE_DISPLAY_KIND = "bmanga_balloon_merge_display_kind"
PROP_MERGE_GROUP_ID = "bmanga_balloon_merge_group_id"
PROP_MERGE_SOURCE_IDS = "bmanga_balloon_merge_source_ids"
PROP_MERGE_SOURCE_SIGNATURE = "bmanga_balloon_merge_source_signature"

_LINE_Z_OFFSET_M = 0.00004


def sync_groups_for_page(scene: bpy.types.Scene, work, page) -> None:
    if scene is None or work is None or page is None:
        return
    groups = _valid_groups(page)
    for group_id, entries in groups.items():
        _ensure_group_display(scene, work, page, group_id, entries)
    _cleanup_group_objects(groups.keys())
    _sync_source_visibility(page, groups)


def sync_group_for_entry(scene: bpy.types.Scene, work, page, entry) -> None:
    if scene is None or work is None or page is None or entry is None:
        return
    group_id = str(getattr(entry, "merge_group_id", "") or "")
    if not group_id:
        return
    entries = _valid_groups(page).get(group_id)
    if not entries:
        return
    _ensure_group_display(scene, work, page, group_id, entries)
    for source in entries:
        balloon_id = str(getattr(source, "id", "") or "")
        _set_source_display_hidden(balloon_id, True)


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
    obj = on.find_object_by_bmanga_id(balloon_id, kind="balloon")
    if obj is not None:
        _set_object_hidden(obj, hidden)
    for candidate in bpy.data.objects:
        if _is_generated_balloon_display(candidate, balloon_id):
            _set_object_hidden(candidate, hidden)


def _set_object_hidden(obj: bpy.types.Object, hidden: bool) -> None:
    if bool(getattr(obj, "hide_viewport", False)) != hidden:
        obj.hide_viewport = hidden
    if bool(getattr(obj, "hide_render", False)) != hidden:
        obj.hide_render = hidden


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
    signature = _group_signature(entries)
    current = _find_group_display_object(group_id)
    if current is not None and str(current.get(PROP_MERGE_SOURCE_SIGNATURE, "") or "") == signature:
        _set_object_hidden(current, False)
        return
    polygons, max_z = _source_polygons(entries)
    if len(polygons) < 2:
        return
    mesh = _build_union_mesh(group_id, polygons, entries[0])
    if mesh is None:
        return
    obj = _display_object(group_id, mesh)
    obj[PROP_MERGE_SOURCE_SIGNATURE] = signature
    _stamp_display_object(scene, work, page, obj, group_id, entries, max_z=max_z)


def _find_group_display_object(group_id: str) -> bpy.types.Object | None:
    obj_name = f"{MERGE_NAME_PREFIX}{group_id}"
    obj = on.find_object_by_bmanga_id(group_id, kind=MERGE_KIND)
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    return obj


def is_merge_display_object(obj: bpy.types.Object | None) -> bool:
    return bool(obj is not None and obj.get(PROP_MERGE_DISPLAY_KIND) == "display")


def sync_display_transform_from_object(scene, obj: bpy.types.Object | None) -> bool:
    if scene is None or not is_merge_display_object(obj):
        return False
    changed = False
    with los.suppress_sync():
        if (
            abs(float(getattr(obj.location, "x", 0.0) or 0.0)) > 1.0e-9
            or abs(float(getattr(obj.location, "y", 0.0) or 0.0)) > 1.0e-9
        ):
            obj.location.x = 0.0
            obj.location.y = 0.0
            changed = True
        if (
            abs(float(getattr(obj.rotation_euler, "x", 0.0) or 0.0)) > 1.0e-9
            or abs(float(getattr(obj.rotation_euler, "y", 0.0) or 0.0)) > 1.0e-9
            or abs(float(getattr(obj.rotation_euler, "z", 0.0) or 0.0)) > 1.0e-9
        ):
            obj.rotation_euler = (0.0, 0.0, 0.0)
            changed = True
        if (
            abs(float(getattr(obj.scale, "x", 1.0) or 1.0) - 1.0) > 1.0e-9
            or abs(float(getattr(obj.scale, "y", 1.0) or 1.0) - 1.0) > 1.0e-9
            or abs(float(getattr(obj.scale, "z", 1.0) or 1.0) - 1.0) > 1.0e-9
        ):
            obj.scale = (1.0, 1.0, 1.0)
            changed = True
        if not bool(getattr(obj, "hide_select", False)):
            obj.hide_select = True
            changed = True
        try:
            if obj.select_get():
                obj.select_set(False)
                changed = True
        except Exception:  # noqa: BLE001
            pass
    return changed


def _display_object(group_id: str, mesh: bpy.types.Mesh) -> bpy.types.Object:
    obj_name = f"{MERGE_NAME_PREFIX}{group_id}"
    obj = _find_group_display_object(group_id)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    else:
        obj.data = mesh
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
        bmanga_id=group_id,
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
        obj = on.find_object_by_bmanga_id(str(getattr(entry, "id", "") or ""), kind="balloon")
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
    matrix = _object_transform_matrix(obj)
    if getattr(spline, "type", "") == "BEZIER":
        return _sample_bezier_world(matrix, spline)
    pts = []
    for point in getattr(spline, "points", []) or []:
        co = getattr(point, "co", None)
        if co is None:
            continue
        world = matrix @ Vector((float(co.x), float(co.y), float(co.z)))
        pts.append((float(world.x), float(world.y)))
    return pts


def _object_transform_matrix(obj: bpy.types.Object) -> Matrix:
    if getattr(obj, "parent", None) is None:
        return Matrix.LocRotScale(obj.location, obj.rotation_euler, obj.scale)
    return obj.matrix_world.copy()


def _sample_bezier_world(matrix: Matrix, spline, steps: int = 10) -> list[tuple[float, float]]:
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
            world = matrix @ pos
            pts.append((float(world.x), float(world.y)))
    return pts


def _sample_cubic_vec(p0: Vector, h0: Vector, h1: Vector, p1: Vector, t: float) -> Vector:
    mt = 1.0 - t
    return (mt**3) * p0 + (3.0 * mt * mt * t) * h0 + (3.0 * mt * t * t) * h1 + (t**3) * p1


def _build_union_mesh(group_id: str, polygons, style_entry) -> bpy.types.Mesh | None:
    union = _union_source_polygons(polygons)
    if union is None:
        return None
    verts, faces, material_indices = _mesh_parts_from_union(union, style_entry)
    if not faces or len(verts) < 3:
        return None
    mesh = bpy.data.meshes.new(f"{MERGE_NAME_PREFIX}{group_id}_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.validate(clean_customdata=False)
    mesh.update()
    line_mat = bco._ensure_balloon_curve_material(  # noqa: SLF001
        None,
        material_name=f"{bco.BALLOON_CURVE_MATERIAL_PREFIX}{group_id}",
        entry=style_entry,
    )
    fill_mat = bco._ensure_fill_material(f"{bco.BALLOON_FILL_MATERIAL_PREFIX}{group_id}", style_entry)  # noqa: SLF001
    mesh.materials.clear()
    mesh.materials.append(line_mat)
    mesh.materials.append(fill_mat)
    for index, poly in enumerate(mesh.polygons):
        poly.material_index = material_indices[index] if index < len(material_indices) else 1
    return mesh


def _group_signature(entries) -> str:
    raw = repr([_entry_signature(entry) for entry in entries]).encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()


def _entry_signature(entry) -> tuple:
    entry_id = str(getattr(entry, "id", "") or "")
    return (
        entry_id,
        _source_object_signature(entry_id),
        _entry_geometry_signature(entry),
        _entry_style_signature(entry),
        _shape_params_signature(getattr(entry, "shape_params", None)),
        _tails_signature(entry),
    )


def _entry_geometry_signature(entry) -> tuple:
    return (
        bool(getattr(entry, "visible", True)),
        str(getattr(entry, "shape", "") or ""),
        _round_float(getattr(entry, "x_mm", 0.0)),
        _round_float(getattr(entry, "y_mm", 0.0)),
        _round_float(getattr(entry, "width_mm", 0.0)),
        _round_float(getattr(entry, "height_mm", 0.0)),
        _round_float(getattr(entry, "rotation_deg", 0.0)),
        _round_float(getattr(entry, "center_offset_x_mm", 0.0)),
        _round_float(getattr(entry, "center_offset_y_mm", 0.0)),
        bool(getattr(entry, "free_transform_enabled", False)),
        tuple(_round_float(v) for v in getattr(entry, "free_transform_bottom_left", ())[:2]),
        tuple(_round_float(v) for v in getattr(entry, "free_transform_bottom_right", ())[:2]),
        tuple(_round_float(v) for v in getattr(entry, "free_transform_top_left", ())[:2]),
        tuple(_round_float(v) for v in getattr(entry, "free_transform_top_right", ())[:2]),
        bool(getattr(entry, "flip_h", False)),
        bool(getattr(entry, "flip_v", False)),
        str(getattr(entry, "corner_type", "") or ""),
        bool(getattr(entry, "rounded_corner_enabled", False)),
        _round_float(getattr(entry, "rounded_corner_radius_mm", 0.0)),
        str(getattr(entry, "rounded_corner_radius_unit", "") or ""),
        _round_float(getattr(entry, "rounded_corner_radius_percent", 0.0)),
    )


def _entry_style_signature(entry) -> tuple:
    return (
        str(getattr(entry, "line_style", "") or ""),
        _round_float(getattr(entry, "line_width_mm", 0.0)),
        tuple(_round_float(v) for v in getattr(entry, "line_color", ())[:4]),
        tuple(_round_float(v) for v in getattr(entry, "fill_color", ())[:4]),
        _round_float(getattr(entry, "fill_opacity", 0.0)),
        _round_float(getattr(entry, "opacity", 100.0)),
        _round_float(getattr(entry, "dashed_segment_length_mm", 0.0)),
        _round_float(getattr(entry, "dashed_gap_mm", 0.0)),
        _round_float(getattr(entry, "dotted_gap_mm", 0.0)),
        int(getattr(entry, "multi_line_count", 0) or 0),
        _round_float(getattr(entry, "multi_line_width_mm", 0.0)),
        _round_float(getattr(entry, "multi_line_spacing_mm", 0.0)),
        _round_float(getattr(entry, "multi_line_width_scale_percent", 0.0)),
        _round_float(getattr(entry, "multi_line_spacing_scale_percent", 0.0)),
        str(getattr(entry, "multi_line_direction", "") or ""),
        _round_float(getattr(entry, "line_valley_width_pct", 0.0)),
        _round_float(getattr(entry, "line_peak_width_pct", 0.0)),
        _round_float(getattr(entry, "thorn_multi_line_valley_width_pct", 0.0)),
        _round_float(getattr(entry, "thorn_multi_line_peak_width_pct", 0.0)),
        _round_float(getattr(entry, "thorn_multi_line_length_scale_near_percent", 0.0)),
        _round_float(getattr(entry, "thorn_multi_line_length_scale_far_percent", 0.0)),
        bool(getattr(entry, "thorn_multi_line_cross_enabled", False)),
        bool(getattr(entry, "outer_white_margin_enabled", False)),
        _round_float(getattr(entry, "outer_white_margin_width_mm", 0.0)),
        bool(getattr(entry, "inner_white_margin_enabled", False)),
        _round_float(getattr(entry, "inner_white_margin_width_mm", 0.0)),
    )


def _shape_params_signature(sp) -> tuple:
    if sp is None:
        return ()
    return (
        _round_float(getattr(sp, "cloud_bump_width_mm", 0.0)),
        _round_float(getattr(sp, "cloud_bump_width_jitter", 0.0)),
        _round_float(getattr(sp, "cloud_bump_height_mm", 0.0)),
        _round_float(getattr(sp, "cloud_bump_height_jitter", 0.0)),
        _round_float(getattr(sp, "cloud_offset_percent", 0.0)),
        int(getattr(sp, "shape_seed", 0) or 0),
        _round_float(getattr(sp, "cloud_sub_width_ratio", 0.0)),
        _round_float(getattr(sp, "cloud_sub_width_jitter", 0.0)),
        _round_float(getattr(sp, "cloud_sub_height_ratio", 0.0)),
        _round_float(getattr(sp, "cloud_sub_height_jitter", 0.0)),
        bool(getattr(sp, "cloud_valley_sharp", False)),
        str(getattr(sp, "dynamic_shape_base_kind", "") or ""),
        bool(getattr(sp, "dynamic_base_rounded_corner_enabled", False)),
        _round_float(getattr(sp, "dynamic_base_rounded_corner_radius_mm", 0.0)),
        str(getattr(sp, "dynamic_base_rounded_corner_radius_unit", "") or ""),
        _round_float(getattr(sp, "dynamic_base_rounded_corner_radius_percent", 0.0)),
        int(getattr(sp, "cloud_wave_count", 0) or 0),
        _round_float(getattr(sp, "cloud_wave_amplitude_mm", 0.0)),
        int(getattr(sp, "spike_count", 0) or 0),
        _round_float(getattr(sp, "spike_depth_mm", 0.0)),
        _round_float(getattr(sp, "spike_jitter", 0.0)),
    )


def _tails_signature(entry) -> tuple:
    tails = []
    for tail in getattr(entry, "tails", []) or []:
        tails.append(_tail_signature(tail))
    return tuple(tails)


def _tail_signature(tail) -> tuple:
    points = tuple(
        (
            _round_float(getattr(point, "x_mm", 0.0)),
            _round_float(getattr(point, "y_mm", 0.0)),
            str(getattr(point, "corner_type", "") or ""),
        )
        for point in getattr(tail, "points", []) or []
    )
    return (
        str(getattr(tail, "type", "") or ""),
        str(getattr(tail, "curve_mode", "polyline") or "polyline"),
        str(getattr(tail, "line_type", "wedge") or "wedge"),
        _round_float(getattr(tail, "ellipse_gap_mm", 1.5)),
        _round_float(getattr(tail, "ellipse_angle_deg", 0.0)),
        str(getattr(tail, "ellipse_orient", "start_end") or "start_end"),
        bool(getattr(tail, "sharp_corners", False)),
        _round_float(getattr(tail, "taper_in_percent", 0.0)),
        _round_float(getattr(tail, "taper_out_percent", 0.0)),
        _round_float(getattr(tail, "direction_deg", 0.0)),
        _round_float(getattr(tail, "length_mm", 0.0)),
        _round_float(getattr(tail, "root_width_mm", 0.0)),
        _round_float(getattr(tail, "tip_width_mm", 0.0)),
        _round_float(getattr(tail, "curve_bend", 0.0)),
        bool(getattr(tail, "custom_points_enabled", False)),
        _round_float(getattr(tail, "start_x_mm", 0.0)),
        _round_float(getattr(tail, "start_y_mm", 0.0)),
        _round_float(getattr(tail, "end_x_mm", 0.0)),
        _round_float(getattr(tail, "end_y_mm", 0.0)),
        points,
    )


def _round_float(value) -> float:
    try:
        return round(float(value), 6)
    except Exception:  # noqa: BLE001
        return 0.0


def _source_object_signature(balloon_id: str) -> tuple:
    if not balloon_id:
        return ()
    obj = on.find_object_by_bmanga_id(balloon_id, kind="balloon")
    if obj is None or getattr(obj, "type", "") != "CURVE":
        return ()
    transform = (
        tuple(_round_float(v) for v in getattr(obj, "location", ())[:3]),
        tuple(_round_float(v) for v in getattr(obj, "rotation_euler", ())[:3]),
        tuple(_round_float(v) for v in getattr(obj, "scale", ())[:3]),
    )
    curve = getattr(obj, "data", None)
    splines = []
    for spline in getattr(curve, "splines", []) or []:
        spline_type = str(getattr(spline, "type", "") or "")
        if spline_type == "BEZIER":
            points = tuple(
                (
                    tuple(_round_float(v) for v in point.co[:3]),
                    tuple(_round_float(v) for v in point.handle_left[:3]),
                    tuple(_round_float(v) for v in point.handle_right[:3]),
                )
                for point in getattr(spline, "bezier_points", []) or []
            )
        else:
            points = tuple(
                tuple(_round_float(v) for v in getattr(point, "co", ())[:4])
                for point in getattr(spline, "points", []) or []
            )
        splines.append((spline_type, bool(getattr(spline, "use_cyclic_u", False)), points))
    return (transform, tuple(splines))


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
                continue
            if poly.is_empty or poly.area <= 0.0:
                continue
            source.append(poly)
        except Exception:  # noqa: BLE001
            continue
    if not source:
        return None
    try:
        union = unary_union(source)
        if union.is_empty:
            return None
        if not union.is_valid:
            return None
        return None if union.is_empty or union.area <= 0.0 else union
    except Exception:  # noqa: BLE001
        return None


def _mesh_parts_from_union(union, style_entry):
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    material_indices: list[int] = []
    fill_geom = _fill_geometry_for_union(union, style_entry)
    for poly in _iter_polygons(fill_geom):
        outer, holes = _polygon_to_outer_holes(poly)
        pts, tris = balloon_line_mesh._triangulate_polygon(outer, holes)  # noqa: SLF001
        if not tris or len(pts) < 3:
            continue
        _append_triangles(verts, faces, material_indices, pts, tris, z=0.0, material_index=1)
    for outer, holes in _line_band_polygons(union, style_entry):
        pts, tris = balloon_line_mesh._triangulate_polygon(outer, holes)  # noqa: SLF001
        if not tris or len(pts) < 3:
            continue
        _append_triangles(verts, faces, material_indices, pts, tris, z=_LINE_Z_OFFSET_M, material_index=0)
    return verts, faces, material_indices


def _fill_geometry_for_union(union, style_entry):
    extent_m = _outer_fill_extent_m(style_entry)
    if extent_m <= 1.0e-9:
        return union
    join, mitre = _buffer_join_params(style_entry)
    try:
        expanded = union.buffer(extent_m, join_style=join, mitre_limit=mitre)
    except Exception:  # noqa: BLE001
        return union
    if expanded is None or expanded.is_empty:
        return union
    return expanded


def _outer_fill_extent_m(style_entry) -> float:
    line_style = str(getattr(style_entry, "line_style", "") or "")
    if line_style == "none":
        line_width_mm = 0.0
    else:
        line_width_mm = max(0.0, float(getattr(style_entry, "line_width_mm", 0.3) or 0.0))
    extent_mm = 0.0
    if bool(getattr(style_entry, "outer_white_margin_enabled", False)):
        extent_mm += max(0.0, float(getattr(style_entry, "outer_white_margin_width_mm", 0.0) or 0.0))
    if line_style == "double":
        extent_mm = max(extent_mm, line_width_mm)
        direction = str(getattr(style_entry, "multi_line_direction", "outside") or "outside")
        if direction in {"outside", "both", ""}:
            count = max(1, min(12, int(getattr(style_entry, "multi_line_count", 3) or 3)))
            width_mm = max(0.0, float(getattr(style_entry, "multi_line_width_mm", 0.0) or 0.0))
            spacing_mm = max(0.0, float(getattr(style_entry, "multi_line_spacing_mm", 0.0) or 0.0))
            width_scale = max(0.0, float(getattr(style_entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
            spacing_scale = max(0.0, float(getattr(style_entry, "multi_line_spacing_scale_percent", 100.0) or 0.0)) / 100.0
            running_mm = line_width_mm
            for index in range(count):
                ring_width = width_mm * (width_scale ** index)
                ring_spacing = spacing_mm * (spacing_scale ** index)
                if ring_width > 1.0e-6:
                    running_mm += ring_spacing + ring_width
                    extent_mm = max(extent_mm, running_mm)
    return extent_mm * 0.001


def _append_triangles(
    verts: list[tuple[float, float, float]],
    faces: list[tuple[int, ...]],
    material_indices: list[int],
    pts,
    tris,
    *,
    z: float,
    material_index: int,
) -> None:
    base = len(verts)
    verts.extend((float(x), float(y), float(z)) for x, y in pts)
    for tri in tris:
        if len(tri) != 3:
            continue
        face = tuple(int(index) + base for index in tri)
        if len(set(face)) != 3:
            continue
        if _triangle_area_abs(verts[face[0]], verts[face[1]], verts[face[2]]) <= 1.0e-14:
            continue
        faces.append(face)
        material_indices.append(material_index)


def _triangle_area_abs(a, b, c) -> float:
    return abs(((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) * 0.5)


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
    line_style = str(getattr(style_entry, "line_style", "") or "")
    if line_style == "none":
        return []
    width = max(0.0, float(getattr(style_entry, "line_width_mm", 0.3) or 0.0)) * 0.001
    if width <= 1.0e-9:
        return []
    if line_style in {"dashed", "dotted"}:
        return _dashed_union_line_bands(union, style_entry, line_style=line_style, line_width_m=width)
    join, mitre = _buffer_join_params(style_entry)
    out = _boundary_band_polygons(union, width * 0.5, join=join, mitre=mitre)
    if line_style == "double":
        out.extend(_multi_line_union_bands(union, style_entry, join=join, mitre=mitre))
    return out


def _boundary_band_polygons(geom, half_width_m: float, *, join: int, mitre: float):
    try:
        band = geom.boundary.buffer(half_width_m, cap_style=2, join_style=join, mitre_limit=mitre)
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


def _dashed_union_line_bands(union, style_entry, *, line_style: str, line_width_m: float):
    valley_sharp = _valley_sharp_for_entry(style_entry)
    dash_segment = max(0.0, float(getattr(style_entry, "dashed_segment_length_mm", 3.6) or 0.0))
    dash_gap = max(0.0, float(getattr(style_entry, "dashed_gap_mm", 1.8) or 0.0))
    dot_gap = max(0.0, float(getattr(style_entry, "dotted_gap_mm", 0.45) or 0.0))
    out = []
    for poly in _iter_polygons(union):
        rings = [poly.exterior]
        rings.extend(poly.interiors)
        for ring in rings:
            coords = [(float(x), float(y), 0.0) for x, y in list(ring.coords)]
            if len(coords) < 4:
                continue
            out.extend(
                balloon_line_mesh._build_dashed_band_polygons(  # noqa: SLF001
                    coords,
                    line_width_m=line_width_m,
                    line_style=line_style,
                    valley_sharp=valley_sharp,
                    dash_segment_mm=dash_segment,
                    dash_gap_mm=dash_gap,
                    dotted_gap_mm=dot_gap,
                )
            )
    return out


def _multi_line_union_bands(union, style_entry, *, join: int, mitre: float):
    count = max(1, min(12, int(getattr(style_entry, "multi_line_count", 3) or 3)))
    width_mm = max(0.0, float(getattr(style_entry, "multi_line_width_mm", 0.0) or 0.0))
    spacing_mm = max(0.0, float(getattr(style_entry, "multi_line_spacing_mm", 0.0) or 0.0))
    if count < 1 or width_mm <= 1.0e-6:
        return []
    width_scale = max(0.0, float(getattr(style_entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    spacing_scale = max(0.0, float(getattr(style_entry, "multi_line_spacing_scale_percent", 100.0) or 0.0)) / 100.0
    line_width_mm = max(0.0, float(getattr(style_entry, "line_width_mm", 0.3) or 0.0))
    direction = str(getattr(style_entry, "multi_line_direction", "outside") or "outside")
    if direction == "both":
        sides = ("inside", "outside")
    elif direction == "inside":
        sides = ("inside",)
    else:
        sides = ("outside",)
    running = {"inside": 0.0, "outside": line_width_mm}
    out = []
    for index in range(count):
        ring_width_mm = width_mm * (width_scale ** index)
        ring_spacing_mm = spacing_mm * (spacing_scale ** index)
        if ring_width_mm <= 1.0e-6:
            continue
        for side in sides:
            inner_m = (running[side] + ring_spacing_mm) * 0.001
            width_m = ring_width_mm * 0.001
            out.extend(_offset_band_polygons(union, inner_m, width_m, side=side, join=join, mitre=mitre))
            running[side] += ring_spacing_mm + ring_width_mm
    return out


def _offset_band_polygons(union, inner_m: float, width_m: float, *, side: str, join: int, mitre: float):
    if width_m <= 1.0e-9:
        return []
    try:
        if side == "inside":
            outer = union.buffer(-inner_m, join_style=join, mitre_limit=mitre)
            inner = union.buffer(-(inner_m + width_m), join_style=join, mitre_limit=mitre)
            band = outer if inner.is_empty else outer.difference(inner)
        else:
            outer = union.buffer(inner_m + width_m, join_style=join, mitre_limit=mitre)
            inner = union.buffer(inner_m, join_style=join, mitre_limit=mitre)
            band = outer.difference(inner)
    except Exception:  # noqa: BLE001
        return []
    if band is None or band.is_empty:
        return []
    out = []
    for poly in _iter_polygons(band):
        outer_ring, holes = _polygon_to_outer_holes(poly)
        if len(outer_ring) >= 3:
            out.append((outer_ring, holes))
    return out


def _buffer_join_params(style_entry) -> tuple[int, float]:
    if _valley_sharp_for_entry(style_entry):
        return 2, 20.0
    shape = str(getattr(style_entry, "shape", "") or "")
    if shape in {"thorn", "thorn-curve", "octagon"}:
        return 2, 20.0
    return 1, 8.0


def _valley_sharp_for_entry(style_entry) -> bool:
    sp = getattr(style_entry, "shape_params", None)
    return bool(getattr(sp, "cloud_valley_sharp", False))


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
