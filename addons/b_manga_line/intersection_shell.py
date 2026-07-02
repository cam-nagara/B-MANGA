"""B-MANGA Line fast material-style intersection lines.

ライン適用済みの他メッシュを軽量な参照用オブジェクトとしてまとめ、
個別ペアのモディファイアを作らずに交差境界をチューブ化する。
"""

from __future__ import annotations

import bpy

from . import intersection_lines, scale_utils
from .core import (
    INTERSECTION_MODIFIER_PREFIX,
    MODIFIER_NAME,
    VG_INTERSECTION_LINE_WIDTH,
)


SHELL_TREE_NAME = "BML_Intersection_Shell"
SHELL_MODIFIER_NAME = f"{INTERSECTION_MODIFIER_PREFIX}Shell"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_MATERIAL_SOCKET = "マテリアル"
_TARGET_COLLECTION_SOCKET = "交差対象グループ"
_TARGET_THICKNESS_SOCKET = "交差対象の線幅"
_GENERATED_LINE_NODE_LABEL = "BML_GeneratedLineMark"
_TARGET_COLLECTION_PROP = "bml_intersection_shell_target_collection"
_TARGET_COLLECTION_PREFIX = "BML_IntersectionTargets"
_PROXY_OBJECT_PROP = "bml_intersection_shell_proxy"
_PROXY_SOURCE_PROP = "bml_intersection_shell_proxy_source"
_PROXY_PREFIX = "BML_IntersectionProxy"


def is_shell_modifier(mod: bpy.types.Modifier) -> bool:
    return mod.name == SHELL_MODIFIER_NAME


def _vector_scale_input(node):
    return node.inputs.get("Scale") or node.inputs[min(3, len(node.inputs) - 1)]


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
    radius_sock = tree.interface.new_socket(
        name=_THICKNESS_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    radius_sock.default_value = 0.0005
    radius_sock.min_value = 0.0001
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
    tree.interface.new_socket(
        name=_TARGET_COLLECTION_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketCollection",
    )
    target_radius_sock = tree.interface.new_socket(
        name=_TARGET_THICKNESS_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    target_radius_sock.default_value = 0.0
    target_radius_sock.min_value = 0.0
    target_radius_sock.max_value = 1.0


def _create_node_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(name=SHELL_TREE_NAME, type="GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1500, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (1400, 0)

    source_geo = intersection_lines._add_shell_strip_nodes(nodes, links, gin)

    collection_info = nodes.new("GeometryNodeCollectionInfo")
    collection_info.location = (-1200, -560)
    collection_info.inputs["Separate Children"].default_value = True
    collection_info.inputs["Reset Children"].default_value = False
    links.new(gin.outputs[_TARGET_COLLECTION_SOCKET], collection_info.inputs["Collection"])

    realize = nodes.new("GeometryNodeRealizeInstances")
    realize.location = (-1000, -560)
    links.new(collection_info.outputs["Instances"], realize.inputs["Geometry"])

    target_geo = intersection_lines._add_target_solidify(
        nodes, links, realize.outputs["Geometry"], 0.0001, (-760, -560),
    )
    expanded_target = _add_target_expansion(nodes, links, target_geo, gin, (-360, -560))
    has_line_faces = intersection_lines._add_target_has_line_faces(
        nodes, links, expanded_target, (-160, -580),
    )
    radius = intersection_lines._add_effective_radius(
        nodes, links, gin, has_line_faces, (280, -820),
    )

    boolean = nodes.new("GeometryNodeMeshBoolean")
    boolean.location = (-160, -160)
    boolean.operation = "DIFFERENCE"
    boolean.solver = "EXACT"
    links.new(source_geo.outputs["Geometry"], boolean.inputs["Mesh 1"])
    links.new(expanded_target, boolean.inputs["Mesh 2"])

    separate = nodes.new("GeometryNodeSeparateGeometry")
    separate.location = (80, -160)
    separate.domain = "EDGE"
    links.new(boolean.outputs["Mesh"], separate.inputs["Geometry"])
    links.new(boolean.outputs["Intersecting Edges"], separate.inputs["Selection"])

    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (300, -160)
    links.new(separate.outputs["Selection"], m2c.inputs["Mesh"])

    join = intersection_lines._add_tube_nodes(
        nodes, links, m2c.outputs["Curve"], gin, radius, x_offset=520,
    )
    links.new(join.outputs[0], gout.inputs[0])
    return tree


def _add_target_expansion(nodes, links, target_geo, gin, loc):
    target_half = nodes.new("ShaderNodeMath")
    target_half.location = (loc[0] - 220, loc[1] + 160)
    target_half.operation = "MULTIPLY"
    target_half.inputs[1].default_value = 0.5
    links.new(gin.outputs[_TARGET_THICKNESS_SOCKET], target_half.inputs[0])

    expand_radius = nodes.new("ShaderNodeMath")
    expand_radius.location = (loc[0], loc[1] + 160)
    expand_radius.operation = "MAXIMUM"
    links.new(gin.outputs[_THICKNESS_SOCKET], expand_radius.inputs[0])
    links.new(target_half.outputs[0], expand_radius.inputs[1])

    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (loc[0] - 220, loc[1])

    offset_vector = nodes.new("ShaderNodeVectorMath")
    offset_vector.location = (loc[0], loc[1])
    offset_vector.operation = "SCALE"
    links.new(normal.outputs[0], offset_vector.inputs[0])
    links.new(expand_radius.outputs[0], _vector_scale_input(offset_vector))

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (loc[0] + 240, loc[1])
    links.new(target_geo, set_position.inputs["Geometry"])
    links.new(offset_vector.outputs[0], set_position.inputs["Offset"])
    return set_position.outputs["Geometry"]


def _find_interface_socket(tree: bpy.types.NodeTree, name: str):
    for item in tree.interface.items_tree:
        if getattr(item, "name", None) == name and getattr(item, "in_out", None) == "INPUT":
            return item
    return None


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    item = _find_interface_socket(tree, name)
    return getattr(item, "identifier", None) if item is not None else None


def _tree_uses_generated_mark(tree: bpy.types.NodeTree) -> bool:
    return any(
        getattr(node, "label", "") == _GENERATED_LINE_NODE_LABEL
        for node in tree.nodes
    )


def _get_or_create_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(SHELL_TREE_NAME)
    if tree is not None:
        ok = (
            _find_socket_id(tree, _THICKNESS_SOCKET) is not None
            and _find_socket_id(tree, _OFFSET_SOCKET) is not None
            and _find_socket_id(tree, _MATERIAL_SOCKET) is not None
            and _find_socket_id(tree, _TARGET_COLLECTION_SOCKET) is not None
            and _find_socket_id(tree, _TARGET_THICKNESS_SOCKET) is not None
            and any(node.bl_idname == "GeometryNodeMeshBoolean" for node in tree.nodes)
            and _tree_uses_generated_mark(tree)
            and intersection_lines._uses_named_attribute(tree, VG_INTERSECTION_LINE_WIDTH)
        )
        if ok:
            return tree
        bpy.data.node_groups.remove(tree)
    return _create_node_tree()


def _ensure_surface_slot(obj: bpy.types.Object) -> None:
    if obj.data.materials:
        return
    surface = bpy.data.materials.new(name=f"{obj.name}_Surface")
    surface.use_nodes = True
    obj.data.materials.append(surface)


def _ensure_material_slot(
    obj: bpy.types.Object,
    material: bpy.types.Material | None,
) -> None:
    _ensure_surface_slot(obj)
    if material is None:
        return
    if not any(slot_mat == material for slot_mat in obj.data.materials):
        obj.data.materials.append(material)


def _position_modifier(obj: bpy.types.Object, mod: bpy.types.Modifier) -> None:
    current = list(obj.modifiers).index(mod)
    target = len(obj.modifiers) - 1
    if current < target:
        obj.modifiers.move(current, target)


def update_modifier_parameters(
    mod: bpy.types.Modifier,
    thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
) -> None:
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    if material is not None:
        _ensure_material_slot(obj, material)
    sid_thickness = _find_socket_id(tree, _THICKNESS_SOCKET)
    if sid_thickness is not None and thickness is not None:
        mod[sid_thickness] = thickness
    sid_offset = _find_socket_id(tree, _OFFSET_SOCKET)
    if sid_offset is not None and offset is not None:
        mod[sid_offset] = offset
    sid_material = _find_socket_id(tree, _MATERIAL_SOCKET)
    if sid_material is not None and material is not None:
        mod[sid_material] = material
    collection = _modifier_target_collection(mod)
    sid_target_thickness = _find_socket_id(tree, _TARGET_THICKNESS_SOCKET)
    if sid_target_thickness is not None and collection is not None:
        mod[sid_target_thickness] = _max_target_thickness(obj, list(collection.objects))


def _collection_name(obj: bpy.types.Object) -> str:
    saved = str(obj.get(_TARGET_COLLECTION_PROP, "") or "")
    if saved:
        return saved
    raw = obj.name_full or obj.name or "Object"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or "Object"
    return f"{_TARGET_COLLECTION_PREFIX}_{cleaned[:48]}_{obj.as_pointer():x}"


def _get_or_create_target_collection(obj: bpy.types.Object) -> bpy.types.Collection:
    name = _collection_name(obj)
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    obj[_TARGET_COLLECTION_PROP] = collection.name
    return collection


def _iter_source_scenes(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> list[bpy.types.Scene]:
    scenes: list[bpy.types.Scene] = []
    if scene is not None:
        scenes.append(scene)
    for item in getattr(obj, "users_scene", ()) or ():
        if item is not None and item not in scenes:
            scenes.append(item)
    return scenes


def _target_candidates(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> list[bpy.types.Object]:
    from . import camera_comp, plane_filter

    targets: list[bpy.types.Object] = []
    for src_scene in _iter_source_scenes(obj, scene):
        for candidate in src_scene.objects:
            if candidate == obj or candidate.type != "MESH" or candidate.data is None:
                continue
            if bool(candidate.get(_PROXY_SOURCE_PROP, "")):
                continue
            if not getattr(candidate.data, "polygons", None):
                continue
            if candidate.modifiers.get(MODIFIER_NAME) is None:
                continue
            settings = getattr(candidate, "bmanga_line_settings", None)
            if plane_filter.should_exclude_generated_lines(candidate, settings):
                continue
            if not camera_comp.intersection_line_creation_in_range(
                candidate, src_scene, settings,
            ):
                continue
            if candidate not in targets:
                targets.append(candidate)
    targets.sort(key=lambda item: item.name_full)
    return targets


def _sync_target_collection(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> tuple[bpy.types.Collection, list[bpy.types.Object]]:
    collection = _get_or_create_target_collection(obj)
    targets = _target_candidates(obj, scene)
    proxies = [_get_or_create_proxy(target) for target in targets]
    proxy_set = set(proxies)
    for item in list(collection.objects):
        if item not in proxy_set:
            collection.objects.unlink(item)
    for item in proxies:
        if item.name not in collection.objects:
            collection.objects.link(item)
    return collection, targets


def _proxy_name(obj: bpy.types.Object) -> str:
    saved = str(obj.get(_PROXY_OBJECT_PROP, "") or "")
    if saved:
        return saved
    raw = obj.name_full or obj.name or "Object"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or "Object"
    return f"{_PROXY_PREFIX}_{cleaned[:48]}_{obj.as_pointer():x}"


def _get_or_create_proxy(obj: bpy.types.Object) -> bpy.types.Object:
    name = _proxy_name(obj)
    proxy = bpy.data.objects.get(name)
    if proxy is None:
        proxy = bpy.data.objects.new(name=name, object_data=obj.data)
    elif proxy.data is not obj.data:
        proxy.data = obj.data
    proxy.matrix_world = obj.matrix_world.copy()
    proxy.hide_viewport = True
    proxy.hide_render = True
    proxy[_PROXY_SOURCE_PROP] = obj.name_full
    obj[_PROXY_OBJECT_PROP] = proxy.name
    return proxy


def _outline_world_width(target: bpy.types.Object | None) -> float:
    if target is None or target.type != "MESH":
        return 0.0
    mod = target.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return 0.0
    return scale_utils.world_width_from_modifier(target, mod.thickness)


def _target_outline_thickness(
    source: bpy.types.Object | None,
    target: bpy.types.Object | None,
) -> float:
    world_width = _outline_world_width(target)
    if source is None or source.type != "MESH":
        return world_width
    return scale_utils.modifier_thickness_for_world_width(source, world_width)


def _max_target_thickness(
    source: bpy.types.Object | None,
    targets: list[bpy.types.Object],
) -> float:
    if not targets:
        return 0.0
    return max(_target_outline_thickness(source, target) for target in targets)


def _modifier_target_collection(mod: bpy.types.Modifier) -> bpy.types.Collection | None:
    tree = getattr(mod, "node_group", None)
    sid = _find_socket_id(tree, _TARGET_COLLECTION_SOCKET) if tree is not None else None
    if sid is None:
        return None
    try:
        collection = mod[sid]
    except (KeyError, TypeError):
        return None
    return collection if isinstance(collection, bpy.types.Collection) else None


def collection_real_targets(collection: bpy.types.Collection | None) -> list[bpy.types.Object]:
    if collection is None:
        return []
    targets: list[bpy.types.Object] = []
    for item in collection.objects:
        source_name = str(item.get(_PROXY_SOURCE_PROP, "") or "")
        source = bpy.data.objects.get(source_name)
        if getattr(source, "type", None) == "MESH" and source not in targets:
            targets.append(source)
    return targets


def _set_target_collection_parameters(
    mod: bpy.types.Modifier,
    collection: bpy.types.Collection,
    targets: list[bpy.types.Object],
) -> None:
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    sid_collection = _find_socket_id(tree, _TARGET_COLLECTION_SOCKET)
    if sid_collection is not None:
        mod[sid_collection] = collection
    sid_target_thickness = _find_socket_id(tree, _TARGET_THICKNESS_SOCKET)
    if sid_target_thickness is not None:
        mod[sid_target_thickness] = _max_target_thickness(obj, targets)


def update_target_width_reference(mod: bpy.types.Modifier) -> bool:
    collection = _modifier_target_collection(mod)
    if collection is None:
        return False
    _set_target_collection_parameters(mod, collection, collection_real_targets(collection))
    return True


def refresh_target_collection(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> bool:
    mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
    if mod is None:
        return False
    collection, targets = _sync_target_collection(obj, scene)
    _set_target_collection_parameters(mod, collection, targets)
    return True


def cleanup_target_collection(obj: bpy.types.Object) -> None:
    name = str(obj.get(_TARGET_COLLECTION_PROP, "") or "")
    if not name:
        return
    collection = bpy.data.collections.get(name)
    if collection is not None:
        for item in list(collection.objects):
            collection.objects.unlink(item)
            if bool(item.get(_PROXY_SOURCE_PROP, "")) and not item.users_collection:
                bpy.data.objects.remove(item)
        bpy.data.collections.remove(collection)
    try:
        del obj[_TARGET_COLLECTION_PROP]
    except (KeyError, TypeError):
        pass


def apply_intersection_shell(
    obj: bpy.types.Object,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
    scene: bpy.types.Scene | None = None,
) -> bool:
    if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
        return False
    tree = _get_or_create_tree()
    _ensure_material_slot(obj, material)
    collection, targets = _sync_target_collection(obj, scene)
    mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=SHELL_MODIFIER_NAME, type="NODES")
    mod.node_group = tree
    update_modifier_parameters(mod, thickness, offset, material)
    _set_target_collection_parameters(mod, collection, targets)

    mod.show_viewport = True
    mod.show_render = True
    _position_modifier(obj, mod)
    return True
