"""Shared evaluated geometry for one saved-intersection refresh."""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from .core import (
    GN_MODIFIER_NAME,
    INTERSECTION_MODIFIER_NAME,
    INTERSECTION_MODIFIER_PREFIX,
    MODIFIER_NAME,
    OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME,
    OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
    SELECTION_LINE_MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
)


_EPS = 1.0e-6


@dataclass
class MeshData:
    vertices: list[Vector]
    triangles: list[tuple[int, int, int]]
    normals: list[Vector]


def _line_modifier_names() -> tuple[str, ...]:
    return (
        MODIFIER_NAME,
        OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME,
        OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
        SHEET_OUTLINE_MODIFIER_NAME,
        GN_MODIFIER_NAME,
        SELECTION_LINE_MODIFIER_NAME,
        INTERSECTION_MODIFIER_NAME,
    )


def is_line_modifier(mod: bpy.types.Modifier) -> bool:
    if mod.name == OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME:
        from . import outline_local_subdivision

        return outline_local_subdivision.is_modifier(mod)
    if mod.name in _line_modifier_names() or mod.name.startswith(
        INTERSECTION_MODIFIER_PREFIX
    ):
        return True
    from . import outline_local_subdivision

    return outline_local_subdivision.is_modifier(mod)


def disabled_line_modifiers(objects: list[bpy.types.Object]):
    states = []
    seen = set()
    for obj in objects:
        try:
            if obj.type != "MESH" or obj.as_pointer() in seen:
                continue
            seen.add(obj.as_pointer())
        except ReferenceError:
            continue
        for mod in obj.modifiers:
            if not is_line_modifier(mod):
                continue
            states.append((mod, bool(mod.show_viewport), bool(mod.show_render)))
            mod.show_viewport = False
            mod.show_render = False
    return states


def restore_modifier_states(states) -> None:
    for mod, show_viewport, show_render in states:
        try:
            mod.show_viewport = show_viewport
            mod.show_render = show_render
        except ReferenceError:
            continue


def set_target_outline_state(states, target: bpy.types.Object, enabled: bool) -> None:
    from . import outline_local_subdivision

    local = outline_local_subdivision.get_modifier(target)
    use_solid_proxy = local is not None and target.modifiers.get(MODIFIER_NAME) is not None
    for mod, show_viewport, show_render in states:
        try:
            if getattr(mod, "id_data", None) != target:
                continue
            if outline_local_subdivision.is_modifier(mod):
                mod.show_viewport = False
                mod.show_render = False
                continue
            if use_solid_proxy and mod.name == MODIFIER_NAME:
                mod.show_viewport = bool(enabled)
                mod.show_render = bool(enabled)
                continue
            keep_outline = mod.name in (
                MODIFIER_NAME,
                SHEET_OUTLINE_MODIFIER_NAME,
            )
            mod.show_viewport = bool(enabled and keep_outline and show_viewport)
            mod.show_render = bool(enabled and keep_outline and show_render)
        except ReferenceError:
            continue


def target_outline_was_visible(states, target: bpy.types.Object) -> bool:
    from . import outline_local_subdivision

    for mod, show_viewport, _show_render in states:
        try:
            if (
                getattr(mod, "id_data", None) == target
                and (
                    mod.name in (MODIFIER_NAME, SHEET_OUTLINE_MODIFIER_NAME)
                    or outline_local_subdivision.is_modifier(mod)
                )
                and show_viewport
            ):
                return True
        except ReferenceError:
            continue
    return False


def triangle_normal(
    vertices: list[Vector],
    triangle: tuple[int, int, int],
) -> Vector:
    a, b, c = (vertices[index] for index in triangle)
    normal = (b - a).cross(c - a)
    if normal.length <= _EPS:
        return Vector((0.0, 0.0, 1.0))
    normal.normalize()
    return normal


def evaluated_mesh_data(
    obj: bpy.types.Object,
    depsgraph,
    transform: Matrix,
) -> MeshData:
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        mesh.calc_loop_triangles()
        vertices = [transform @ vertex.co for vertex in mesh.vertices]
        triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
    finally:
        eval_obj.to_mesh_clear()
    normals = [triangle_normal(vertices, triangle) for triangle in triangles]
    return MeshData(vertices, triangles, normals)


def matrix_relative_to_origin(obj: bpy.types.Object, origin: Vector) -> Matrix:
    transform = obj.matrix_world.copy()
    transform.translation = transform.translation - origin
    return transform


class BatchGeometryCache:
    """Reuse world-space evaluated meshes and BVHs during one refresh."""

    def __init__(
        self,
        objects: list[bpy.types.Object],
        *,
        origin: Vector | None = None,
    ):
        self._objects = objects
        self.origin = Vector(origin) if origin is not None else Vector()
        self._states = []
        self._known_modifiers = set()
        self._base: dict[int, tuple[MeshData, BVHTree | None]] = {}
        self._outline: dict[int, tuple[MeshData | None, BVHTree | None]] = {}

    def __enter__(self):
        self._states = disabled_line_modifiers(self._objects)
        self._known_modifiers = {
            mod.as_pointer()
            for mod, _show_viewport, _show_render in self._states
        }
        bpy.context.view_layer.update()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        restore_modifier_states(self._states)
        bpy.context.view_layer.update()
        self._base.clear()
        self._outline.clear()

    @staticmethod
    def _bvh(data: MeshData) -> BVHTree | None:
        if not data.triangles:
            return None
        return BVHTree.FromPolygons(data.vertices, data.triangles)

    def base(self, obj: bpy.types.Object) -> tuple[MeshData, BVHTree | None]:
        pointer = obj.as_pointer()
        cached = self._base.get(pointer)
        if cached is not None:
            return cached
        data = evaluated_mesh_data(
            obj,
            bpy.context.evaluated_depsgraph_get(),
            matrix_relative_to_origin(obj, self.origin),
        )
        result = (data, self._bvh(data))
        self._base[pointer] = result
        return result

    def outline(
        self,
        obj: bpy.types.Object,
    ) -> tuple[MeshData | None, BVHTree | None]:
        pointer = obj.as_pointer()
        cached = self._outline.get(pointer)
        if cached is not None:
            return cached
        if not target_outline_was_visible(self._states, obj):
            result = (None, None)
            self._outline[pointer] = result
            return result
        set_target_outline_state(self._states, obj, True)
        bpy.context.view_layer.update()
        try:
            data = evaluated_mesh_data(
                obj,
                bpy.context.evaluated_depsgraph_get(),
                matrix_relative_to_origin(obj, self.origin),
            )
        finally:
            set_target_outline_state(self._states, obj, False)
            bpy.context.view_layer.update()
        result = (data, self._bvh(data))
        self._outline[pointer] = result
        return result

    def suspend_new_line_modifiers(self, obj: bpy.types.Object) -> None:
        for mod in obj.modifiers:
            if not is_line_modifier(mod):
                continue
            pointer = mod.as_pointer()
            if pointer in self._known_modifiers:
                continue
            self._known_modifiers.add(pointer)
            self._states.append(
                (mod, bool(mod.show_viewport), bool(mod.show_render))
            )
            mod.show_viewport = False
            mod.show_render = False
