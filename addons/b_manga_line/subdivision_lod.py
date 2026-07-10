"""B-MANGA Liner generated-line subdivision and legacy cleanup.

The current implementation never creates or edits a Subdivision Surface on the
source object.  The old names are retained only to identify safely-owned legacy
data during an explicit reflect/delete operation.
"""

from __future__ import annotations

import bpy


AUTO_SUBSURF_MODIFIER_NAME = "BML_MidpointSubsurf"
AUTO_SUBSURF_CREASE_EDGES_PROP = "bml_auto_midpoint_subsurf_crease_edges"
AUTO_QUADIFIED_FACES_PROP = "bml_auto_midpoint_quadified_faces"
CREASE_EDGE_ATTR = "crease_edge"
DEFAULT_LINE_RESAMPLE_COUNT = 1
_MIDPOINT_DISPLAY_RESAMPLE_COUNT = 3
_ROUND_LOOP_RESAMPLE_CAP = 96


def _legacy_owner(mod: bpy.types.Modifier | None) -> bpy.types.Object | None:
    owner = getattr(mod, "id_data", None)
    return owner if isinstance(owner, bpy.types.Object) else None


def is_auto_subsurf_modifier(mod: bpy.types.Modifier | None) -> bool:
    """Return True only for an old modifier with verifiable Liner ownership."""
    if mod is None or mod.type != "SUBSURF":
        return False
    if not (
        mod.name == AUTO_SUBSURF_MODIFIER_NAME
        or mod.name.startswith(AUTO_SUBSURF_MODIFIER_NAME + ".")
    ):
        return False
    owner = _legacy_owner(mod)
    return bool(
        owner is not None
        and sum(
            1
            for candidate in owner.modifiers
            if candidate.type == "SUBSURF"
            and (
                candidate.name == AUTO_SUBSURF_MODIFIER_NAME
                or candidate.name.startswith(AUTO_SUBSURF_MODIFIER_NAME + ".")
            )
        )
        == 1
        and (
            AUTO_SUBSURF_CREASE_EDGES_PROP in owner
            or AUTO_QUADIFIED_FACES_PROP in owner
        )
    )


def auto_subsurf_modifiers(obj: bpy.types.Object) -> list[bpy.types.Modifier]:
    return [mod for mod in obj.modifiers if is_auto_subsurf_modifier(mod)]


def auto_subsurf_modifier(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    modifiers = auto_subsurf_modifiers(obj)
    return modifiers[0] if modifiers else None


def auto_subdivision_supported(obj: bpy.types.Object) -> bool:
    return obj.type == "MESH" and obj.data is not None and bool(obj.data.polygons)


def _remove_owned_legacy_subdivision(obj: bpy.types.Object) -> int:
    # Multiple matching names are ambiguous: an old generated modifier and a
    # user modifier may coexist. Preserve all rather than risking user data.
    candidates = [
        mod
        for mod in obj.modifiers
        if mod.type == "SUBSURF"
        and (
            mod.name == AUTO_SUBSURF_MODIFIER_NAME
            or mod.name.startswith(AUTO_SUBSURF_MODIFIER_NAME + ".")
        )
    ]
    if len(candidates) != 1:
        return 0
    removed = 0
    for mod in list(obj.modifiers):
        if is_auto_subsurf_modifier(mod):
            obj.modifiers.remove(mod)
            removed += 1
    if removed:
        # Metadata belongs to the retired implementation. The mesh crease
        # attribute itself is deliberately preserved because it may also carry
        # user-authored data and cannot be separated reliably.
        for key in (AUTO_SUBSURF_CREASE_EDGES_PROP, AUTO_QUADIFIED_FACES_PROP):
            if key in obj:
                del obj[key]
    return removed


def ensure_auto_subdivision(obj: bpy.types.Object, _scene=None):
    """Compatibility entry point: retire legacy source Subsurf and sync lines."""
    if obj.type != "MESH":
        return None
    _remove_owned_legacy_subdivision(obj)
    sync_generated_line_subdivision(obj)
    try:
        from . import outline_local_subdivision

        for mod in obj.modifiers:
            if outline_local_subdivision.is_modifier(mod):
                return mod
    except Exception:  # noqa: BLE001 - optional outline may not exist yet
        pass
    return None


def remove_auto_subdivision(obj: bpy.types.Object) -> bool:
    if obj.type != "MESH":
        return False
    changed = bool(_remove_owned_legacy_subdivision(obj))
    try:
        from . import outline_local_subdivision

        changed = outline_local_subdivision.remove(obj) or changed
    except Exception:  # noqa: BLE001 - deletion must continue for remaining lines
        pass
    sync_generated_line_subdivision(obj)
    return changed


def repair_auto_subdivision_modifiers(scene: bpy.types.Scene | None = None) -> int:
    """Explicitly remove only verifiably-owned legacy modifiers."""
    objects = tuple(scene.objects) if scene is not None else tuple(bpy.data.objects)
    return sum(_remove_owned_legacy_subdivision(obj) for obj in objects if obj.type == "MESH")


def sync_viewport_levels_to_render(_obj: bpy.types.Object) -> int:
    """Retired compatibility shim. User Subsurf levels are never changed."""
    return 0


def reset_viewport_levels_to_zero(_obj: bpy.types.Object) -> int:
    """Retired compatibility shim. User Subsurf levels are never changed."""
    return 0


def line_resample_count(obj: bpy.types.Object, *, for_render: bool = False) -> int:
    del for_render
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is not None and bool(
        getattr(settings, "auto_subdivision_for_midpoint", False)
    ):
        return _MIDPOINT_DISPLAY_RESAMPLE_COUNT
    return DEFAULT_LINE_RESAMPLE_COUNT


def midpoint_display_resample_count(requested: int | None = None) -> int:
    del requested
    return _MIDPOINT_DISPLAY_RESAMPLE_COUNT


def display_resample_count(
    needs_midpoint_controls: bool,
    requested: int | None = None,
) -> int:
    del requested
    return _MIDPOINT_DISPLAY_RESAMPLE_COUNT if needs_midpoint_controls else 1


def _closed_loop_min_resample_count(obj: bpy.types.Object) -> int:
    if obj.type != "MESH" or obj.data is None:
        return 1
    try:
        from . import inner_line_chains
    except Exception:  # noqa: BLE001
        return 1
    attr = obj.data.attributes.get(inner_line_chains.CHAIN_ID_ATTR)
    if attr is None or getattr(attr, "domain", None) != "EDGE":
        return 1
    chains: dict[int, list[bpy.types.MeshEdge]] = {}
    for edge in obj.data.edges:
        if edge.index >= len(attr.data):
            continue
        chain_id = int(getattr(attr.data[edge.index], "value", -1))
        if chain_id >= 0:
            chains.setdefault(chain_id, []).append(edge)
    minimum = 1
    for edges in chains.values():
        if len(edges) < 3:
            continue
        degrees: dict[int, int] = {}
        for edge in edges:
            for vertex_index in edge.vertices:
                degrees[vertex_index] = degrees.get(vertex_index, 0) + 1
        if degrees and all(degree == 2 for degree in degrees.values()):
            minimum = max(minimum, min(_ROUND_LOOP_RESAMPLE_CAP, len(edges) + 1))
    return minimum


def sync_generated_line_subdivision(
    obj: bpy.types.Object,
    *,
    for_render: bool = False,
) -> bool:
    if obj.type != "MESH":
        return False
    try:
        from . import core, inner_line_cache, inner_lines

        mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
        if mod is None or mod.node_group is None:
            return False
        socket_id = inner_lines._find_socket_id(mod.node_group, "線の分割数")
        if socket_id is None:
            return False
        count = line_resample_count(obj, for_render=for_render)
        if inner_line_cache.is_cached_modifier(mod):
            settings = getattr(obj, "bmanga_line_settings", None)
            needs_controls = bool(
                settings is not None
                and bool(getattr(settings, "auto_subdivision_for_midpoint", False))
                and (
                    abs(float(getattr(settings, "inner_edge_smooth_factor", 0.0))) > 1.0e-7
                    or abs(float(getattr(settings, "inner_edge_midpoint_jitter_percent", 0.0))) > 1.0e-7
                )
            )
            count = display_resample_count(needs_controls, count)
        count = max(count, _closed_loop_min_resample_count(obj))
        try:
            current = int(mod[socket_id])
        except (KeyError, TypeError, ValueError):
            current = -1
        if current == count:
            return False
        return bool(inner_lines.update_parameters(obj, resample_count=count))
    except Exception:  # noqa: BLE001 - line updates must remain usable
        return False


def sync_scene_generated_line_subdivision(
    scene: bpy.types.Scene | None,
    *,
    for_render: bool = False,
) -> int:
    if scene is None:
        return 0
    return sum(
        1
        for obj in scene.objects
        if obj.type == "MESH"
        and sync_generated_line_subdivision(obj, for_render=for_render)
    )


def register() -> None:
    # No depsgraph/load/render handlers: source and user Subsurf are read-only.
    return None


def unregister() -> None:
    return None
