"""B-MANGA Line fast shell-based intersection lines.

ライン素材のソリッド面と他メッシュ表面の交差境界を使い、
個別ペアのモディファイアを作らずに交差線を作る。
"""

from __future__ import annotations

import bpy

from . import intersection_lines, outline_setup, scale_utils
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
_LINE_MATERIAL_INDEX_SOCKET = "ライン素材番号"
_HAS_TARGET_SOCKET = "交差対象あり"
_SHELL_BOOLEAN_NODE_LABEL = "BML_IntersectionShellBoolean"
_TARGET_COLLECTION_PROP = "bml_intersection_shell_target_collection"
_TARGET_COLLECTION_PREFIX = "BML_IntersectionTargets"
_PROXY_SOURCE_PROP = "bml_intersection_shell_proxy_source"
_PROXY_PREFIX = "BML_IntersectionProxy"


def is_shell_modifier(mod: bpy.types.Modifier) -> bool:
    return mod.name == SHELL_MODIFIER_NAME


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
    line_material_sock = tree.interface.new_socket(
        name=_LINE_MATERIAL_INDEX_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketInt",
    )
    line_material_sock.default_value = 999
    line_material_sock.min_value = 0
    has_target_sock = tree.interface.new_socket(
        name=_HAS_TARGET_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketBool",
    )
    has_target_sock.default_value = False


def _create_node_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(name=SHELL_TREE_NAME, type="GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1300, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (900, 0)

    collection_info = nodes.new("GeometryNodeCollectionInfo")
    collection_info.location = (-1060, -460)
    collection_info.inputs["Separate Children"].default_value = True
    collection_info.inputs["Reset Children"].default_value = False
    links.new(gin.outputs[_TARGET_COLLECTION_SOCKET], collection_info.inputs["Collection"])

    realize = nodes.new("GeometryNodeRealizeInstances")
    realize.location = (-840, -460)
    links.new(collection_info.outputs["Instances"], realize.inputs["Geometry"])

    target_geo = intersection_lines._add_target_solidify(
        nodes, links, realize.outputs["Geometry"], 0.0001, (-640, -460),
    )
    has_line_faces = intersection_lines._add_target_has_line_faces(
        nodes, links, target_geo, (-320, -560),
    )
    radius = intersection_lines._add_effective_radius(
        nodes, links, gin, has_line_faces, (80, -820),
    )

    mat_idx = nodes.new("GeometryNodeInputMaterialIndex")
    mat_idx.location = (-1060, 200)

    line_shell = nodes.new("FunctionNodeCompare")
    line_shell.location = (-840, 200)
    line_shell.data_type = "INT"
    line_shell.operation = "GREATER_EQUAL"
    links.new(mat_idx.outputs[0], line_shell.inputs[2])
    links.new(gin.outputs[_LINE_MATERIAL_INDEX_SOCKET], line_shell.inputs[3])

    generated_attr = nodes.new("GeometryNodeInputNamedAttribute")
    generated_attr.location = (-1060, 20)
    generated_attr.data_type = "BOOLEAN"
    generated_attr.inputs["Name"].default_value = intersection_lines.GENERATED_LINE_ATTR

    generated_marked = nodes.new("FunctionNodeBooleanMath")
    generated_marked.location = (-840, 20)
    generated_marked.operation = "AND"
    links.new(generated_attr.outputs["Exists"], generated_marked.inputs[0])
    links.new(generated_attr.outputs["Attribute"], generated_marked.inputs[1])

    not_generated = nodes.new("FunctionNodeBooleanMath")
    not_generated.location = (-620, 20)
    not_generated.operation = "NOT"
    links.new(generated_marked.outputs[0], not_generated.inputs[0])

    shell_and_clean = nodes.new("FunctionNodeBooleanMath")
    shell_and_clean.location = (-380, 120)
    shell_and_clean.operation = "AND"
    links.new(line_shell.outputs[0], shell_and_clean.inputs[0])
    links.new(not_generated.outputs[0], shell_and_clean.inputs[1])

    shell_only = nodes.new("GeometryNodeSeparateGeometry")
    shell_only.location = (-160, 100)
    shell_only.domain = "FACE"
    links.new(gin.outputs[0], shell_only.inputs["Geometry"])
    links.new(shell_and_clean.outputs[0], shell_only.inputs["Selection"])

    active_shell = nodes.new("GeometryNodeSeparateGeometry")
    active_shell.location = (80, 80)
    active_shell.domain = "FACE"
    links.new(shell_only.outputs["Selection"], active_shell.inputs["Geometry"])
    links.new(gin.outputs[_HAS_TARGET_SOCKET], active_shell.inputs["Selection"])

    boolean = nodes.new("GeometryNodeMeshBoolean")
    boolean.label = _SHELL_BOOLEAN_NODE_LABEL
    boolean.location = (320, -120)
    boolean.operation = "DIFFERENCE"
    boolean.solver = "EXACT"
    links.new(active_shell.outputs["Selection"], boolean.inputs["Mesh 1"])
    links.new(target_geo, boolean.inputs["Mesh 2"])

    separate = nodes.new("GeometryNodeSeparateGeometry")
    separate.location = (560, -120)
    separate.domain = "EDGE"
    links.new(boolean.outputs["Mesh"], separate.inputs["Geometry"])
    links.new(boolean.outputs["Intersecting Edges"], separate.inputs["Selection"])

    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (780, -120)
    links.new(separate.outputs["Selection"], m2c.inputs["Mesh"])

    join = intersection_lines._add_tube_nodes(
        nodes, links, m2c.outputs["Curve"], gin, radius, x_offset=1000,
    )
    links.new(join.outputs[0], gout.inputs[0])
    return tree


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
        getattr(node, "label", "") == _SHELL_BOOLEAN_NODE_LABEL
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
            and _find_socket_id(tree, _LINE_MATERIAL_INDEX_SOCKET) is not None
            and _find_socket_id(tree, _HAS_TARGET_SOCKET) is not None
            and any(
                node.bl_idname == "GeometryNodeMeshBoolean"
                and getattr(node, "label", "") == _SHELL_BOOLEAN_NODE_LABEL
                for node in tree.nodes
            )
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
    sid_target_thickness = _find_socket_id(tree, _TARGET_THICKNESS_SOCKET)
    if sid_target_thickness is not None:
        mod[sid_target_thickness] = _max_target_thickness(obj, modifier_targets(mod))
    sid_has_target = _find_socket_id(tree, _HAS_TARGET_SOCKET)
    if sid_has_target is not None:
        mod[sid_has_target] = bool(modifier_targets(mod))
    _set_line_material_index(mod)


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


def _target_list(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
    thickness: float,
) -> list[bpy.types.Object]:
    targets: list[bpy.types.Object] = []
    for candidate in _target_candidates(obj, scene):
        margin = intersection_lines._intersection_margin(obj, candidate, thickness)
        if intersection_lines._bounds_overlap(obj, candidate, margin):
            targets.append(candidate)
    return targets


def _sync_target_collection(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
    thickness: float,
) -> tuple[bpy.types.Collection, list[bpy.types.Object]]:
    collection = _get_or_create_target_collection(obj)
    targets = _target_list(obj, scene, thickness)
    proxies = [_get_or_create_proxy(obj, target) for target in targets]
    proxy_set = set(proxies)
    for item in list(collection.objects):
        if item not in proxy_set:
            collection.objects.unlink(item)
            if bool(item.get(_PROXY_SOURCE_PROP, "")) and not item.users_collection:
                bpy.data.objects.remove(item)
    for item in proxies:
        if item.name not in collection.objects:
            collection.objects.link(item)
    return collection, targets


def _proxy_name(source: bpy.types.Object, target: bpy.types.Object) -> str:
    raw = f"{source.name_full}_{target.name_full}"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or "Object"
    return f"{_PROXY_PREFIX}_{cleaned[:48]}_{source.as_pointer():x}_{target.as_pointer():x}"


def _get_or_create_proxy(
    source: bpy.types.Object,
    target: bpy.types.Object,
) -> bpy.types.Object:
    name = _proxy_name(source, target)
    proxy = bpy.data.objects.get(name)
    if proxy is None:
        proxy = bpy.data.objects.new(name=name, object_data=target.data)
    elif proxy.data is not target.data:
        proxy.data = target.data
    proxy.matrix_world = source.matrix_world.inverted() @ target.matrix_world
    proxy.hide_viewport = True
    proxy.hide_render = True
    proxy[_PROXY_SOURCE_PROP] = target.name_full
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
    sid_has_target = _find_socket_id(tree, _HAS_TARGET_SOCKET)
    if sid_has_target is not None:
        mod[sid_has_target] = bool(targets)
    _set_line_material_index(mod)


def modifier_targets(mod: bpy.types.Modifier) -> list[bpy.types.Object]:
    return collection_real_targets(_modifier_target_collection(mod))


def _set_line_material_index(mod: bpy.types.Modifier) -> None:
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    sid_line_material = _find_socket_id(tree, _LINE_MATERIAL_INDEX_SOCKET)
    if sid_line_material is not None:
        mod[sid_line_material] = outline_setup.first_line_material_slot(obj)


def update_target_width_reference(mod: bpy.types.Modifier) -> bool:
    targets = modifier_targets(mod)
    if not targets:
        return False
    collection = _modifier_target_collection(mod)
    if collection is None:
        return False
    _set_target_collection_parameters(mod, collection, targets)
    return True


def refresh_target_collection(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> bool:
    mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
    if mod is None:
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None or not getattr(settings, "intersection_enabled", False):
        obj.modifiers.remove(mod)
        return False
    return apply_intersection_shell(
        obj,
        scale_utils.modifier_thickness_for_world_width(
            obj,
            float(getattr(settings, "intersection_thickness", 0.0003)),
        ),
        float(getattr(settings, "intersection_line_offset", 0.0)),
        outline_setup.get_line_material(obj, "intersection"),
        scene,
    )


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
    _ensure_material_slot(obj, material)
    collection, targets = _sync_target_collection(obj, scene, thickness)
    if not targets:
        mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
        if mod is not None:
            obj.modifiers.remove(mod)
        cleanup_target_collection(obj)
        return True
    tree = _get_or_create_tree()
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
