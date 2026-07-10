"""Scene-level broad phase for saved intersection-line generation."""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Vector

from . import camera_comp, core, outline_setup, plane_filter, scale_utils


Bounds = tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class _Candidate:
    obj: bpy.types.Object
    pointer: int
    name: str
    bounds: Bounds
    creation_in_range: bool
    excluded: bool
    locked: bool
    enabled: bool
    sheet: bool
    cost: tuple[int, int, str]
    outline_width: float


def _has_outline_source(obj: bpy.types.Object) -> bool:
    return (
        obj.modifiers.get(core.MODIFIER_NAME) is not None
        or obj.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is not None
    )


def _world_bounds(obj: bpy.types.Object) -> Bounds:
    if not obj.bound_box:
        loc = obj.matrix_world.translation
        return (loc.x, loc.x, loc.y, loc.y, loc.z, loc.z)
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        min(corner.x for corner in corners),
        max(corner.x for corner in corners),
        min(corner.y for corner in corners),
        max(corner.y for corner in corners),
        min(corner.z for corner in corners),
        max(corner.z for corner in corners),
    )


def _outline_world_width(obj: bpy.types.Object) -> float:
    mod = obj.modifiers.get(core.MODIFIER_NAME)
    if mod is not None:
        return scale_utils.world_width_from_modifier(obj, mod.thickness)
    return outline_setup.sheet_outline_world_width(obj)


def _source_cost(obj: bpy.types.Object) -> tuple[int, int, str]:
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return (0, 0, obj.name_full)
    return (len(mesh.polygons), len(mesh.vertices), obj.name_full)


def _bounds_overlap(left: Bounds, right: Bounds, margin: float) -> bool:
    return (
        left[0] <= right[1] + margin
        and left[1] + margin >= right[0]
        and left[2] <= right[3] + margin
        and left[3] + margin >= right[2]
        and left[4] <= right[5] + margin
        and left[5] + margin >= right[4]
    )


def _owns_pair(
    source_sheet: bool,
    source_cost: tuple[int, int, str],
    target: _Candidate,
) -> bool:
    if source_sheet != target.sheet:
        return not source_sheet
    if source_cost != target.cost:
        return source_cost < target.cost
    return source_cost[2] < target.name


class SceneCandidateIndex:
    """Cache immutable candidate facts for one intersection refresh."""

    def __init__(self, scene: bpy.types.Scene | None):
        self._items: dict[int, tuple[_Candidate, ...]] = {}
        self._by_pointer: dict[tuple[int, int], _Candidate] = {}
        if scene is not None:
            self._ensure_scene(scene)

    def _ensure_scene(self, scene: bpy.types.Scene) -> tuple[_Candidate, ...]:
        scene_pointer = scene.as_pointer()
        cached = self._items.get(scene_pointer)
        if cached is not None:
            return cached
        items = []
        for obj in scene.objects:
            try:
                if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
                    continue
                if not _has_outline_source(obj):
                    continue
                settings = getattr(obj, "bmanga_line_settings", None)
                item = _Candidate(
                    obj=obj,
                    pointer=obj.as_pointer(),
                    name=obj.name_full,
                    bounds=_world_bounds(obj),
                    creation_in_range=bool(
                        camera_comp.intersection_line_creation_in_range(
                            obj,
                            scene,
                            settings,
                        )
                    ),
                    excluded=plane_filter.should_exclude_generated_lines(
                        obj,
                        settings,
                    ),
                    locked=core.is_settings_locked(obj),
                    enabled=bool(getattr(settings, "intersection_enabled", False)),
                    sheet=plane_filter.is_sheet_mesh(obj),
                    cost=_source_cost(obj),
                    outline_width=_outline_world_width(obj),
                )
            except ReferenceError:
                continue
            items.append(item)
            self._by_pointer[(scene_pointer, item.pointer)] = item
        result = tuple(items)
        self._items[scene_pointer] = result
        return result

    @staticmethod
    def _source_scenes(
        source: bpy.types.Object,
        scene: bpy.types.Scene | None,
    ) -> list[bpy.types.Scene]:
        scenes = []
        if scene is not None:
            scenes.append(scene)
        for item in getattr(source, "users_scene", ()) or ():
            if item is not None and item not in scenes:
                scenes.append(item)
        return scenes

    def targets_for(
        self,
        source: bpy.types.Object,
        scene: bpy.types.Scene | None,
        thickness: float,
        existing_names: set[str],
    ) -> list[bpy.types.Object]:
        source_bounds = _world_bounds(source)
        source_sheet = plane_filter.is_sheet_mesh(source)
        source_cost = _source_cost(source)
        source_outline_width = _outline_world_width(source)
        source_intersection_width = scale_utils.world_width_from_modifier(
            source,
            thickness,
        )
        targets = []
        seen = set()
        for src_scene in self._source_scenes(source, scene):
            for item in self._ensure_scene(src_scene):
                if item.pointer == source.as_pointer() or item.pointer in seen:
                    continue
                already_cached = item.name in existing_names
                if not item.creation_in_range and not already_cached:
                    continue
                if item.excluded or (item.locked and not already_cached):
                    continue
                if item.enabled and not _owns_pair(source_sheet, source_cost, item):
                    continue
                margin = max(
                    source_intersection_width,
                    source_outline_width,
                    item.outline_width,
                    0.001,
                )
                if not _bounds_overlap(source_bounds, item.bounds, margin):
                    continue
                seen.add(item.pointer)
                targets.append(item.obj)
        targets.sort(key=lambda item: item.name_full)
        return targets
