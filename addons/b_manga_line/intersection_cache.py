"""Cached intersection lines for B-MANGA Liner.

The heavy part is extracting center-line segments from intersecting meshes.
Once extracted, the owner keeps a lightweight modifier that only turns the
saved center lines into tubes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import intersection_shell_node_helpers, modifier_stack
from .core import (
    GENERATED_LINE_ATTR,
    GN_MODIFIER_NAME,
    INTERSECTION_MODIFIER_NAME,
    INTERSECTION_MODIFIER_PREFIX,
    MODIFIER_NAME,
    OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
    PROP_LINES_HIDDEN,
    SELECTION_LINE_MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
)


CACHE_TREE_NAME = "BML_Intersection_Cached"
CACHE_OBJECT_PREFIX = "BML_IntersectionCache"
CACHE_COLLECTION_NAME = "BML_IntersectionCacheObjects"
CACHE_OBJECT_PROP = "bml_intersection_cache_object"
CACHE_OWNER_PROP = "bml_intersection_cache_owner"
CACHE_TARGETS_PROP = "bml_intersection_cache_targets"

_CACHE_OBJECT_SOCKET = "保存済み交差線"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_MATERIAL_SOCKET = "マテリアル"
_MIDPOINT_FACTOR_SOCKET = "中間頂点の線幅調整"
_MIDPOINT_JITTER_SOCKET = "中間頂点の乱れ (%)"
_MIDPOINT_ANGLE_SOCKET = "検出角度"
_WIDTH_CURVE_25_SOCKET = "変化グラフ 25%"
_WIDTH_CURVE_50_SOCKET = "変化グラフ 50%"
_WIDTH_CURVE_75_SOCKET = "変化グラフ 75%"

_NORMAL_ATTR = "BML_IntersectionCachedNormal"
_SPLIT_ATTR = "BML_IntersectionCachedEndpoint"
_SPLIT_LABEL = "BML_IntersectionCachedEndpoint"
_SUBDIVIDE_LABEL = "BML_IntersectionCachedSubdivide"
_PROFILE_RESOLUTION = 12
_SUBDIVIDE_CUTS = 3
_VISUAL_RADIUS_FACTOR = 0.8
_EPS = 1.0e-6
_KEY_SCALE = 100000.0


@dataclass
class _MeshData:
    vertices: list[Vector]
    triangles: list[tuple[int, int, int]]
    normals: list[Vector]


@dataclass(frozen=True)
class _CachedSegment:
    start: Vector
    end: Vector
    normal: Vector


def _setup_interface(tree: bpy.types.NodeTree) -> None:
    tree.interface.new_socket(
        name="Geometry",
        in_out="INPUT",
        socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name=_CACHE_OBJECT_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketObject",
    )
    radius_sock = tree.interface.new_socket(
        name=_THICKNESS_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    radius_sock.default_value = 0.0005
    radius_sock.min_value = 0.00001
    radius_sock.max_value = 1.0
    offset_sock = tree.interface.new_socket(
        name=_OFFSET_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    offset_sock.default_value = 0.0
    offset_sock.min_value = -1.0
    offset_sock.max_value = 1.0
    tree.interface.new_socket(
        name=_MATERIAL_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketMaterial",
    )
    factor_sock = tree.interface.new_socket(
        name=_MIDPOINT_FACTOR_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    factor_sock.default_value = 0.0
    factor_sock.min_value = -1.0
    factor_sock.max_value = 1.0
    jitter_sock = tree.interface.new_socket(
        name=_MIDPOINT_JITTER_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    jitter_sock.default_value = 0.0
    jitter_sock.min_value = 0.0
    jitter_sock.max_value = 50.0
    angle_sock = tree.interface.new_socket(
        name=_MIDPOINT_ANGLE_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    angle_sock.default_value = 1.7453292520
    angle_sock.min_value = 0.0
    angle_sock.max_value = 3.1415926536
    if hasattr(angle_sock, "subtype"):
        angle_sock.subtype = "ANGLE"
    for name, default in (
        (_WIDTH_CURVE_25_SOCKET, 0.25),
        (_WIDTH_CURVE_50_SOCKET, 0.50),
        (_WIDTH_CURVE_75_SOCKET, 0.75),
    ):
        sock = tree.interface.new_socket(
            name=name,
            in_out="INPUT",
            socket_type="NodeSocketFloat",
        )
        sock.default_value = default
        sock.min_value = 0.0
        sock.max_value = 1.0


def _vector_scale_input(node):
    return node.inputs.get("Scale") or node.inputs[min(3, len(node.inputs) - 1)]


def _create_display_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(name=CACHE_TREE_NAME, type="GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-980, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (1100, 0)

    obj_info = nodes.new("GeometryNodeObjectInfo")
    obj_info.location = (-760, -260)
    obj_info.transform_space = "RELATIVE"
    links.new(gin.outputs[_CACHE_OBJECT_SOCKET], obj_info.inputs["Object"])

    normal_attr = nodes.new("GeometryNodeInputNamedAttribute")
    normal_attr.location = (-760, -560)
    normal_attr.data_type = "FLOAT_VECTOR"
    normal_attr.inputs["Name"].default_value = _NORMAL_ATTR

    offset_amount = nodes.new("ShaderNodeMath")
    offset_amount.location = (-560, -560)
    offset_amount.operation = "MULTIPLY"
    links.new(gin.outputs[_THICKNESS_SOCKET], offset_amount.inputs[0])
    links.new(gin.outputs[_OFFSET_SOCKET], offset_amount.inputs[1])

    offset_vector = nodes.new("ShaderNodeVectorMath")
    offset_vector.location = (-360, -520)
    offset_vector.operation = "SCALE"
    links.new(normal_attr.outputs["Attribute"], offset_vector.inputs[0])
    links.new(offset_amount.outputs[0], _vector_scale_input(offset_vector))

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (-540, -260)
    links.new(obj_info.outputs["Geometry"], set_position.inputs["Geometry"])
    links.new(offset_vector.outputs[0], set_position.inputs["Offset"])

    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (-540, -260)
    links.new(set_position.outputs["Geometry"], m2c.inputs["Mesh"])

    split_curve = intersection_shell_node_helpers.store_curve_midpoint_split_attribute(
        nodes,
        links,
        m2c.outputs["Curve"],
        gin.outputs[_MIDPOINT_ANGLE_SOCKET],
        (-520, -820),
        attribute_name=_SPLIT_ATTR,
        label=_SPLIT_LABEL,
        angle_split_min_segment_fraction=0.0,
        angle_split_confirmation_offset=1,
        include_curve_endpoints=True,
    )

    subdivide = nodes.new("GeometryNodeSubdivideCurve")
    subdivide.label = _SUBDIVIDE_LABEL
    subdivide.location = (-260, -260)
    subdivide.inputs["Cuts"].default_value = _SUBDIVIDE_CUTS
    links.new(split_curve, subdivide.inputs["Curve"])

    scale = intersection_shell_node_helpers.add_curve_midpoint_width_scale_from_split_attribute(
        nodes,
        links,
        subdivide.outputs["Curve"],
        gin.outputs[_MIDPOINT_FACTOR_SOCKET],
        (-260, -820),
        attribute_name=_SPLIT_ATTR,
        label=_SPLIT_LABEL + "Scale",
        width_curve_outputs=(
            gin.outputs[_WIDTH_CURVE_25_SOCKET],
            gin.outputs[_WIDTH_CURVE_50_SOCKET],
            gin.outputs[_WIDTH_CURVE_75_SOCKET],
        ),
        jitter_output=gin.outputs[_MIDPOINT_JITTER_SOCKET],
    )

    radius_half = nodes.new("ShaderNodeMath")
    radius_half.location = (20, -560)
    radius_half.operation = "MULTIPLY"
    radius_half.inputs[1].default_value = 0.5 * _VISUAL_RADIUS_FACTOR
    links.new(gin.outputs[_THICKNESS_SOCKET], radius_half.inputs[0])

    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.location = (220, -560)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = _PROFILE_RESOLUTION
    links.new(radius_half.outputs[0], circle.inputs["Radius"])

    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (100, -260)
    links.new(subdivide.outputs["Curve"], c2m.inputs["Curve"])
    links.new(circle.outputs["Curve"], c2m.inputs["Profile Curve"])
    if "Scale" in c2m.inputs:
        links.new(scale, c2m.inputs["Scale"])
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True

    mark = nodes.new("GeometryNodeStoreNamedAttribute")
    mark.location = (340, -420)
    mark.data_type = "BOOLEAN"
    mark.domain = "FACE"
    mark.inputs["Name"].default_value = GENERATED_LINE_ATTR
    mark.inputs["Value"].default_value = True
    links.new(c2m.outputs["Mesh"], mark.inputs["Geometry"])

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (560, -260)
    links.new(mark.outputs["Geometry"], setmat.inputs["Geometry"])
    links.new(gin.outputs[_MATERIAL_SOCKET], setmat.inputs["Material"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (820, 0)
    links.new(gin.outputs["Geometry"], join.inputs["Geometry"])
    links.new(setmat.outputs["Geometry"], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], gout.inputs["Geometry"])
    return tree


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    for item in tree.interface.items_tree:
        if getattr(item, "item_type", None) == "SOCKET" and item.name == name:
            return getattr(item, "identifier", None)
    return None


def _tree_valid(tree: bpy.types.NodeTree | None) -> bool:
    if tree is None:
        return False
    return (
        _find_socket_id(tree, _CACHE_OBJECT_SOCKET) is not None
        and _find_socket_id(tree, _THICKNESS_SOCKET) is not None
        and _find_socket_id(tree, _OFFSET_SOCKET) is not None
        and _find_socket_id(tree, _MATERIAL_SOCKET) is not None
        and any(getattr(node, "label", "") == _SUBDIVIDE_LABEL for node in tree.nodes)
    )


def _get_or_create_display_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(CACHE_TREE_NAME)
    if _tree_valid(tree):
        assert tree is not None
        return tree
    if tree is not None:
        bpy.data.node_groups.remove(tree)
    return _create_display_tree()


def _set_modifier_input_if_changed(mod, socket_id: str | None, value) -> None:
    if socket_id is None:
        return
    try:
        old = mod[socket_id]
    except (KeyError, TypeError):
        old = None
    if old == value:
        return
    mod[socket_id] = value


def _ensure_material_slot(obj: bpy.types.Object, material: bpy.types.Material | None) -> None:
    if material is None or obj.type != "MESH" or obj.data is None:
        return
    if not any(slot.material == material for slot in obj.material_slots):
        obj.data.materials.append(material)


def _line_modifier_names() -> tuple[str, ...]:
    return (
        MODIFIER_NAME,
        OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
        SHEET_OUTLINE_MODIFIER_NAME,
        GN_MODIFIER_NAME,
        SELECTION_LINE_MODIFIER_NAME,
        INTERSECTION_MODIFIER_NAME,
    )


def _is_line_modifier(mod: bpy.types.Modifier) -> bool:
    return mod.name in _line_modifier_names() or mod.name.startswith(
        INTERSECTION_MODIFIER_PREFIX
    )


def _cache_collection(scene: bpy.types.Scene | None) -> bpy.types.Collection:
    collection = bpy.data.collections.get(CACHE_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(CACHE_COLLECTION_NAME)
    scene = scene or getattr(bpy.context, "scene", None)
    child_names = {child.name for child in scene.collection.children} if scene is not None else set()
    if scene is not None and collection.name not in child_names:
        try:
            scene.collection.children.link(collection)
        except RuntimeError:
            pass
    return collection


def _cache_name(owner: bpy.types.Object) -> str:
    saved = str(owner.get(CACHE_OBJECT_PROP, "") or "")
    if saved:
        return saved
    raw = owner.name_full or owner.name or "Object"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or "Object"
    return f"{CACHE_OBJECT_PREFIX}_{cleaned[:48]}_{owner.as_pointer():x}"


def _get_cache_object(owner: bpy.types.Object) -> bpy.types.Object | None:
    name = str(owner.get(CACHE_OBJECT_PROP, "") or "")
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    return obj if getattr(obj, "type", None) == "MESH" else None


def _get_or_create_cache_object(
    owner: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> bpy.types.Object:
    name = _cache_name(owner)
    obj = bpy.data.objects.get(name)
    if obj is None:
        mesh = bpy.data.meshes.new(name=f"{name}_Mesh")
        obj = bpy.data.objects.new(name=name, object_data=mesh)
    collection = _cache_collection(scene)
    object_names = {item.name for item in collection.objects}
    if obj.name not in object_names:
        collection.objects.link(obj)
    obj.hide_viewport = True
    obj.hide_render = True
    obj[CACHE_OWNER_PROP] = owner.name_full
    owner[CACHE_OBJECT_PROP] = obj.name
    return obj


def _remove_cache_object(owner: bpy.types.Object) -> bool:
    cache = _get_cache_object(owner)
    removed = False
    if cache is not None:
        mesh = cache.data
        bpy.data.objects.remove(cache)
        removed = True
        if mesh is not None and not mesh.users:
            bpy.data.meshes.remove(mesh)
    for prop in (CACHE_OBJECT_PROP, CACHE_TARGETS_PROP):
        try:
            del owner[prop]
        except (KeyError, TypeError):
            pass
    return removed


def _set_target_names(owner: bpy.types.Object, targets: list[bpy.types.Object]) -> None:
    owner[CACHE_TARGETS_PROP] = json.dumps(
        [target.name_full for target in targets],
        ensure_ascii=False,
    )


def target_names(owner: bpy.types.Object) -> set[str]:
    raw = str(owner.get(CACHE_TARGETS_PROP, "") or "")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(item) for item in parsed if str(item)}


def modifier_targets(mod: bpy.types.Modifier) -> list[bpy.types.Object]:
    owner = getattr(mod, "id_data", None)
    if getattr(owner, "type", None) != "MESH":
        return []
    return [
        target for target in (bpy.data.objects.get(name) for name in target_names(owner))
        if getattr(target, "type", None) == "MESH"
    ]


def _normal_key(normal: Vector) -> tuple[int, int, int]:
    return (
        int(round(normal.x * _KEY_SCALE)),
        int(round(normal.y * _KEY_SCALE)),
        int(round(normal.z * _KEY_SCALE)),
    )


def _write_cache_mesh(cache: bpy.types.Object, segments: list[_CachedSegment]) -> None:
    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    normals: list[tuple[float, float, float]] = []
    vertex_map: dict[tuple[tuple[int, int, int], tuple[int, int, int]], int] = {}

    def index_for(point: Vector, normal: Vector) -> int:
        key = (_point_key(point), _normal_key(normal))
        found = vertex_map.get(key)
        if found is not None:
            return found
        vertex_map[key] = len(vertices)
        vertices.append((point.x, point.y, point.z))
        normals.append((normal.x, normal.y, normal.z))
        return vertex_map[key]

    edge_keys: set[tuple[int, int]] = set()
    for segment in segments:
        a = index_for(segment.start, segment.normal)
        b = index_for(segment.end, segment.normal)
        if a == b:
            continue
        key = (a, b) if a < b else (b, a)
        if key in edge_keys:
            continue
        edge_keys.add(key)
        edges.append((a, b))

    old_mesh = cache.data
    mesh = bpy.data.meshes.new(name=f"{cache.name}_Mesh")
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    attr = mesh.attributes.new(_NORMAL_ATTR, "FLOAT_VECTOR", "POINT")
    for index, normal in enumerate(normals):
        if index < len(attr.data):
            attr.data[index].vector = normal
    cache.data = mesh
    if old_mesh is not None and not old_mesh.users:
        bpy.data.meshes.remove(old_mesh)


def _apply_display_modifier(
    owner: bpy.types.Object,
    cache: bpy.types.Object,
    targets: list[bpy.types.Object],
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
) -> None:
    tree = _get_or_create_display_tree()
    for mod in list(owner.modifiers):
        if (
            mod.name.startswith(INTERSECTION_MODIFIER_PREFIX)
            or mod.name == INTERSECTION_MODIFIER_NAME
        ) and mod.type != "NODES":
            owner.modifiers.remove(mod)
    mod = owner.modifiers.get(INTERSECTION_MODIFIER_NAME)
    if mod is None:
        mod = owner.modifiers.new(name=INTERSECTION_MODIFIER_NAME, type="NODES")
    mod.node_group = tree
    _ensure_material_slot(owner, material)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _CACHE_OBJECT_SOCKET), cache)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _THICKNESS_SOCKET), thickness)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _OFFSET_SOCKET), offset)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _MATERIAL_SOCKET), material)
    _set_width_control_parameters(mod)
    _set_target_names(owner, targets)

    settings = getattr(owner, "bmanga_line_settings", None)
    visible = (
        not bool(owner.get(PROP_LINES_HIDDEN, False))
        and (settings is None or bool(getattr(settings, "intersection_enabled", False)))
    )
    mod.show_viewport = visible
    mod.show_render = visible
    modifier_stack.reorder_line_modifiers(owner)


def _set_width_control_parameters(mod: bpy.types.Modifier) -> None:
    tree = getattr(mod, "node_group", None)
    owner = getattr(mod, "id_data", None)
    settings = getattr(owner, "bmanga_line_settings", None)
    if tree is None or settings is None:
        return
    values = {
        _MIDPOINT_FACTOR_SOCKET: float(
            getattr(settings, "intersection_edge_smooth_factor", 0.0)
        ),
        _MIDPOINT_JITTER_SOCKET: float(
            getattr(settings, "intersection_edge_midpoint_jitter_percent", 0.0)
        ),
        _MIDPOINT_ANGLE_SOCKET: float(
            getattr(settings, "intersection_edge_midpoint_angle", 1.7453292520)
        ),
        _WIDTH_CURVE_25_SOCKET: float(
            getattr(settings, "intersection_edge_width_curve_25", 0.25)
        ),
        _WIDTH_CURVE_50_SOCKET: float(
            getattr(settings, "intersection_edge_width_curve_50", 0.50)
        ),
        _WIDTH_CURVE_75_SOCKET: float(
            getattr(settings, "intersection_edge_width_curve_75", 0.75)
        ),
    }
    for name, value in values.items():
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, name), value)


def update_cached_parameters(
    owner: bpy.types.Object,
    thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
) -> bool:
    mod = owner.modifiers.get(INTERSECTION_MODIFIER_NAME)
    if mod is None or not _tree_valid(getattr(mod, "node_group", None)):
        return False
    tree = mod.node_group
    if material is not None:
        _ensure_material_slot(owner, material)
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, _MATERIAL_SOCKET), material)
    if thickness is not None:
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, _THICKNESS_SOCKET), thickness)
    if offset is not None:
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, _OFFSET_SOCKET), offset)
    _set_width_control_parameters(mod)
    return True


def remove_cached_intersection_lines(owner: bpy.types.Object) -> bool:
    removed = _remove_cache_object(owner)
    for mod in list(owner.modifiers):
        if mod.name == INTERSECTION_MODIFIER_NAME or mod.name.startswith(
            INTERSECTION_MODIFIER_PREFIX
        ):
            owner.modifiers.remove(mod)
            removed = True
    return removed


def apply_cached_intersection_lines(
    owner: bpy.types.Object,
    targets: list[bpy.types.Object],
    *,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
    scene: bpy.types.Scene | None,
) -> bool:
    targets = [target for target in targets if getattr(target, "type", None) == "MESH"]
    if not targets:
        remove_cached_intersection_lines(owner)
        return True
    segments = build_cached_segments(owner, targets, scene, thickness=thickness, offset=offset)
    if not segments:
        remove_cached_intersection_lines(owner)
        return True
    cache = _get_or_create_cache_object(owner, scene)
    cache.matrix_world = owner.matrix_world.copy()
    _write_cache_mesh(cache, segments)
    _apply_display_modifier(owner, cache, targets, thickness, offset, material)
    return True


def cleanup_orphan_cache_objects() -> int:
    owners = {
        str(owner.get(CACHE_OBJECT_PROP, "") or "")
        for owner in bpy.data.objects
        if getattr(owner, "type", None) == "MESH"
    }
    removed = 0
    for obj in list(bpy.data.objects):
        if not obj.name.startswith(CACHE_OBJECT_PREFIX):
            continue
        if obj.name in owners:
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj)
        removed += 1
        if mesh is not None and not mesh.users:
            bpy.data.meshes.remove(mesh)
    return removed


def _disabled_line_modifiers(objects: list[bpy.types.Object]):
    states = []
    seen: set[int] = set()
    for obj in objects:
        if getattr(obj, "type", None) != "MESH" or obj.as_pointer() in seen:
            continue
        seen.add(obj.as_pointer())
        for mod in obj.modifiers:
            if not _is_line_modifier(mod):
                continue
            states.append((mod, bool(mod.show_viewport), bool(mod.show_render)))
            mod.show_viewport = False
            mod.show_render = False
    return states


def _restore_modifier_states(states) -> None:
    for mod, show_viewport, show_render in states:
        try:
            mod.show_viewport = show_viewport
            mod.show_render = show_render
        except ReferenceError:
            continue


def _set_target_outline_state(states, target: bpy.types.Object, enabled: bool) -> None:
    for mod, show_viewport, show_render in states:
        try:
            if getattr(mod, "id_data", None) != target:
                continue
            keep_outline = mod.name in (MODIFIER_NAME, SHEET_OUTLINE_MODIFIER_NAME)
            mod.show_viewport = bool(enabled and keep_outline and show_viewport)
            mod.show_render = bool(enabled and keep_outline and show_render)
        except ReferenceError:
            continue


def _target_outline_was_visible(states, target: bpy.types.Object) -> bool:
    for mod, show_viewport, _show_render in states:
        try:
            if getattr(mod, "id_data", None) != target:
                continue
            if mod.name in (MODIFIER_NAME, SHEET_OUTLINE_MODIFIER_NAME):
                return bool(show_viewport)
        except ReferenceError:
            continue
    return False


def _evaluated_mesh_data(
    obj: bpy.types.Object,
    depsgraph,
    transform: Matrix,
) -> _MeshData:
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        mesh.calc_loop_triangles()
        vertices = [transform @ vertex.co for vertex in mesh.vertices]
        triangles = [tuple(tri.vertices) for tri in mesh.loop_triangles]
    finally:
        eval_obj.to_mesh_clear()
    normals = [_triangle_normal(vertices, tri) for tri in triangles]
    return _MeshData(vertices, triangles, normals)


def _triangle_normal(
    vertices: list[Vector],
    tri: tuple[int, int, int],
) -> Vector:
    a, b, c = (vertices[index] for index in tri)
    normal = (b - a).cross(c - a)
    if normal.length <= _EPS:
        return Vector((0.0, 0.0, 1.0))
    normal.normalize()
    return normal


def build_cached_segments(
    owner: bpy.types.Object,
    targets: list[bpy.types.Object],
    scene: bpy.types.Scene | None,
    *,
    thickness: float,
    offset: float,
) -> list[_CachedSegment]:
    del thickness, offset
    if owner.type != "MESH" or owner.data is None:
        return []
    states = _disabled_line_modifiers([owner, *targets])
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is not None:
        bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    owner_inv = owner.matrix_world.inverted()
    try:
        source = _evaluated_mesh_data(owner, depsgraph, Matrix.Identity(4))
        if not source.triangles:
            return []
        source_bvh = BVHTree.FromPolygons(source.vertices, source.triangles)
        segments: list[_CachedSegment] = []
        seen: set[
            tuple[
                tuple[tuple[int, int, int], tuple[int, int, int]],
                tuple[int, int, int],
            ]
        ] = set()
        for target in targets:
            if target == owner or target.type != "MESH" or target.data is None:
                continue
            target_transform = owner_inv @ target.matrix_world
            target_data = _evaluated_mesh_data(target, depsgraph, target_transform)
            added = 0
            if target_data.triangles:
                added = _append_target_segments(
                    source=source,
                    source_bvh=source_bvh,
                    target_data=target_data,
                    segments=segments,
                    seen=seen,
                )
            if added == 0 and _target_outline_was_visible(states, target):
                _set_target_outline_state(states, target, True)
                bpy.context.view_layer.update()
                fallback_depsgraph = bpy.context.evaluated_depsgraph_get()
                fallback_data = _evaluated_mesh_data(
                    target,
                    fallback_depsgraph,
                    target_transform,
                )
                if fallback_data.triangles:
                    _append_target_segments(
                        source=source,
                        source_bvh=source_bvh,
                        target_data=fallback_data,
                        segments=segments,
                        seen=seen,
                    )
                _set_target_outline_state(states, target, False)
                bpy.context.view_layer.update()
        return segments
    finally:
        _restore_modifier_states(states)


def _append_target_segments(
    *,
    source: _MeshData,
    source_bvh: BVHTree,
    target_data: _MeshData,
    segments: list[_CachedSegment],
    seen: set[
        tuple[
            tuple[tuple[int, int, int], tuple[int, int, int]],
            tuple[int, int, int],
        ]
    ],
) -> int:
    added = 0
    for source_index, target_index in source_bvh.overlap(
        BVHTree.FromPolygons(target_data.vertices, target_data.triangles)
    ):
        segment = _triangle_intersection_segment(
            source,
            source_index,
            target_data,
            target_index,
        )
        if segment is None:
            continue
        start, end = segment
        if (start - end).length <= _EPS:
            continue
        normal = source.normals[source_index]
        key = (_segment_key(start, end), _normal_key(normal))
        if key in seen:
            continue
        seen.add(key)
        segments.append(_CachedSegment(start.copy(), end.copy(), normal.copy()))
        added += 1
    return added


def _triangle_intersection_segment(
    source: _MeshData,
    source_index: int,
    target: _MeshData,
    target_index: int,
) -> tuple[Vector, Vector] | None:
    tri_a = [source.vertices[index] for index in source.triangles[source_index]]
    tri_b = [target.vertices[index] for index in target.triangles[target_index]]
    normal_a = source.normals[source_index]
    normal_b = target.normals[target_index]
    if normal_a.length <= _EPS or normal_b.length <= _EPS:
        return None
    da = -normal_a.dot(tri_a[0])
    db = -normal_b.dot(tri_b[0])
    points: list[Vector] = []
    for p0, p1 in _edges(tri_a):
        point = _segment_plane_point(p0, p1, normal_b, db)
        if point is not None and _point_in_triangle(point, tri_b, normal_b):
            _append_unique_point(points, point)
    for p0, p1 in _edges(tri_b):
        point = _segment_plane_point(p0, p1, normal_a, da)
        if point is not None and _point_in_triangle(point, tri_a, normal_a):
            _append_unique_point(points, point)
    if len(points) < 2:
        return None
    best: tuple[Vector, Vector] | None = None
    best_len = 0.0
    for index, start in enumerate(points):
        for end in points[index + 1:]:
            length = (start - end).length
            if length > best_len:
                best_len = length
                best = (start, end)
    if best is None or best_len <= _EPS:
        return None
    return best


def _edges(points: list[Vector]):
    return (
        (points[0], points[1]),
        (points[1], points[2]),
        (points[2], points[0]),
    )


def _segment_plane_point(
    start: Vector,
    end: Vector,
    normal: Vector,
    plane_d: float,
) -> Vector | None:
    d0 = normal.dot(start) + plane_d
    d1 = normal.dot(end) + plane_d
    if abs(d0) <= _EPS and abs(d1) <= _EPS:
        return None
    if d0 * d1 > _EPS * _EPS:
        return None
    denom = d0 - d1
    if abs(denom) <= _EPS:
        return None
    t = d0 / denom
    if t < -_EPS or t > 1.0 + _EPS:
        return None
    return start.lerp(end, max(0.0, min(1.0, t)))


def _point_in_triangle(point: Vector, tri: list[Vector], normal: Vector) -> bool:
    for start, end in _edges(tri):
        edge = end - start
        to_point = point - start
        if normal.dot(edge.cross(to_point)) < -_EPS:
            return False
    return True


def _append_unique_point(points: list[Vector], point: Vector) -> None:
    for existing in points:
        if (existing - point).length <= _EPS:
            return
    points.append(point.copy())


def _point_key(point: Vector) -> tuple[int, int, int]:
    return (
        int(round(point.x * _KEY_SCALE)),
        int(round(point.y * _KEY_SCALE)),
        int(round(point.z * _KEY_SCALE)),
    )


def _segment_key(
    start: Vector,
    end: Vector,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    a = _point_key(start)
    b = _point_key(end)
    return (a, b) if a <= b else (b, a)
