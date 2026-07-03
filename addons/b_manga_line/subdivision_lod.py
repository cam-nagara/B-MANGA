"""B-MANGA Line midpoint subdivision setup."""

from __future__ import annotations

import math

import bmesh
import bpy


AUTO_SUBSURF_MODIFIER_NAME = "BML_MidpointSubsurf"
AUTO_SUBSURF_CREASE_EDGES_PROP = "bml_auto_midpoint_subsurf_crease_edges"
CREASE_EDGE_ATTR = "crease_edge"
SHARP_EDGE_ANGLE = math.radians(60.0)
MAX_RENDER_LEVELS = 4
DISTANCE_STEP_METERS = 5.0


def is_auto_subsurf_modifier(mod: bpy.types.Modifier | None) -> bool:
    return (
        mod is not None
        and mod.type == "SUBSURF"
        and (
            mod.name == AUTO_SUBSURF_MODIFIER_NAME
            or mod.name.startswith(AUTO_SUBSURF_MODIFIER_NAME + ".")
        )
    )


def render_levels_for_distance(distance: float) -> int:
    if distance < 0.0:
        distance = 0.0
    level = MAX_RENDER_LEVELS - int(distance // DISTANCE_STEP_METERS)
    return max(0, min(MAX_RENDER_LEVELS, level))


def _line_camera(scene) -> bpy.types.Object | None:
    if scene is None:
        return None
    try:
        from . import camera_comp

        return camera_comp.get_line_camera(scene)
    except Exception:  # noqa: BLE001 - カメラ取得失敗時は既定密度で続行
        return getattr(scene, "camera", None)


def _distance_to_camera(obj: bpy.types.Object, scene) -> float:
    camera = _line_camera(scene)
    if camera is None:
        return 0.0
    return float((camera.matrix_world.translation - obj.matrix_world.translation).length)


def _auto_modifier(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    for mod in obj.modifiers:
        if is_auto_subsurf_modifier(mod):
            return mod
    return None


def _ensure_crease_attribute(mesh: bpy.types.Mesh):
    attr = mesh.attributes.get(CREASE_EDGE_ATTR)
    if attr is None:
        attr = mesh.attributes.new(CREASE_EDGE_ATTR, "FLOAT", "EDGE")
    return attr


def mark_sharp_edges_for_subsurf(
    obj: bpy.types.Object,
    threshold: float = SHARP_EDGE_ANGLE,
) -> int:
    """Set edge crease 1.0 for mesh edges sharper than the threshold."""
    if obj.type != "MESH" or obj.data is None:
        return 0
    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        sharp_indices: list[int] = []
        for edge in bm.edges:
            if len(edge.link_faces) < 2:
                sharp_indices.append(edge.index)
                continue
            try:
                if edge.calc_face_angle() >= threshold:
                    sharp_indices.append(edge.index)
            except ValueError:
                continue
    finally:
        bm.free()

    if not sharp_indices:
        obj[AUTO_SUBSURF_CREASE_EDGES_PROP] = []
        return 0

    attr = _ensure_crease_attribute(mesh)
    for edge_index in sharp_indices:
        if edge_index < len(attr.data):
            attr.data[edge_index].value = 1.0
    obj[AUTO_SUBSURF_CREASE_EDGES_PROP] = sharp_indices
    mesh.update()
    return len(sharp_indices)


def ensure_auto_subdivision(obj: bpy.types.Object, scene) -> bpy.types.Modifier | None:
    """Create/update the auto Subdivision Surface modifier used by midpoint widths."""
    if obj.type != "MESH" or obj.data is None:
        return None

    mark_sharp_edges_for_subsurf(obj)
    mod = _auto_modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(AUTO_SUBSURF_MODIFIER_NAME, "SUBSURF")

    if hasattr(mod, "subdivision_type"):
        mod.subdivision_type = "CATMULL_CLARK"
    mod.levels = 0
    mod.render_levels = render_levels_for_distance(_distance_to_camera(obj, scene))
    mod.show_viewport = True
    mod.show_render = True
    return mod


def remove_auto_subdivision(obj: bpy.types.Object) -> bool:
    if obj.type != "MESH":
        return False
    removed = False
    for mod in list(obj.modifiers):
        if is_auto_subsurf_modifier(mod):
            obj.modifiers.remove(mod)
            removed = True
    if AUTO_SUBSURF_CREASE_EDGES_PROP in obj:
        del obj[AUTO_SUBSURF_CREASE_EDGES_PROP]
    return removed
