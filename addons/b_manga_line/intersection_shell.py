"""B-MANGA Line natural intersection shell.

交差相手を探さず、各オブジェクト自身のライン幅ぶんのシェルを作る。
ラインを持つオブジェクト同士が重なったとき、シェル同士の重なりが
交差線として見えるようにする軽量方式。
"""

from __future__ import annotations

import bpy

from .core import (
    GENERATED_LINE_ATTR,
    INTERSECTION_MODIFIER_PREFIX,
    MATERIAL_NAME,
    VG_INTERSECTION_LINE_WIDTH,
)


SHELL_TREE_NAME = "BML_Intersection_Shell"
SHELL_MODIFIER_NAME = f"{INTERSECTION_MODIFIER_PREFIX}Shell"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_MATERIAL_SOCKET = "マテリアル"
_LINE_MATERIAL_INDEX_SOCKET = "ライン素材番号"
_GENERATED_LINE_NODE_LABEL = "BML_GeneratedLineMark"


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
    index_sock = tree.interface.new_socket(
        name=_LINE_MATERIAL_INDEX_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketInt",
    )
    index_sock.default_value = 999
    index_sock.min_value = 0


def _create_node_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(name=SHELL_TREE_NAME, type="GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1100, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (900, 0)

    mat_idx = nodes.new("GeometryNodeInputMaterialIndex")
    mat_idx.location = (-1050, -420)

    is_line_material = nodes.new("FunctionNodeCompare")
    is_line_material.location = (-850, -420)
    is_line_material.data_type = "INT"
    is_line_material.operation = "GREATER_EQUAL"
    links.new(mat_idx.outputs[0], is_line_material.inputs[2])
    links.new(gin.outputs[_LINE_MATERIAL_INDEX_SOCKET], is_line_material.inputs[3])

    generated_attr = nodes.new("GeometryNodeInputNamedAttribute")
    generated_attr.location = (-850, -600)
    generated_attr.data_type = "BOOLEAN"
    generated_attr.inputs["Name"].default_value = GENERATED_LINE_ATTR

    generated_marked = nodes.new("FunctionNodeBooleanMath")
    generated_marked.location = (-650, -600)
    generated_marked.operation = "AND"
    links.new(generated_attr.outputs["Exists"], generated_marked.inputs[0])
    links.new(generated_attr.outputs["Attribute"], generated_marked.inputs[1])

    delete_selection = nodes.new("FunctionNodeBooleanMath")
    delete_selection.location = (-650, -460)
    delete_selection.operation = "OR"
    links.new(is_line_material.outputs[0], delete_selection.inputs[0])
    links.new(generated_marked.outputs[0], delete_selection.inputs[1])

    del_line = nodes.new("GeometryNodeDeleteGeometry")
    del_line.location = (-450, -360)
    del_line.domain = "FACE"
    links.new(gin.outputs[0], del_line.inputs["Geometry"])
    links.new(delete_selection.outputs[0], del_line.inputs["Selection"])

    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.location = (-650, 160)
    width_attr.data_type = "FLOAT"
    width_attr.inputs["Name"].default_value = VG_INTERSECTION_LINE_WIDTH

    width_switch = nodes.new("GeometryNodeSwitch")
    width_switch.location = (-450, 160)
    width_switch.input_type = "FLOAT"
    width_switch.inputs["False"].default_value = 1.0
    links.new(width_attr.outputs["Exists"], width_switch.inputs["Switch"])
    links.new(width_attr.outputs["Attribute"], width_switch.inputs["True"])

    width_min = nodes.new("ShaderNodeMath")
    width_min.location = (-260, 160)
    width_min.operation = "MAXIMUM"
    width_min.inputs[1].default_value = 0.0
    links.new(width_switch.outputs["Output"], width_min.inputs[0])

    width_max = nodes.new("ShaderNodeMath")
    width_max.location = (-80, 160)
    width_max.operation = "MINIMUM"
    width_max.inputs[1].default_value = 1.0
    links.new(width_min.outputs[0], width_max.inputs[0])

    offset_plus = nodes.new("ShaderNodeMath")
    offset_plus.location = (-450, -80)
    offset_plus.operation = "ADD"
    offset_plus.inputs[1].default_value = 1.0
    links.new(gin.outputs[_OFFSET_SOCKET], offset_plus.inputs[0])

    offset_min = nodes.new("ShaderNodeMath")
    offset_min.location = (-260, -80)
    offset_min.operation = "MAXIMUM"
    offset_min.inputs[1].default_value = 0.0
    links.new(offset_plus.outputs[0], offset_min.inputs[0])

    radius = nodes.new("ShaderNodeMath")
    radius.location = (-80, -80)
    radius.operation = "MULTIPLY"
    links.new(gin.outputs[_THICKNESS_SOCKET], radius.inputs[0])
    links.new(offset_min.outputs[0], radius.inputs[1])

    radius_weighted = nodes.new("ShaderNodeMath")
    radius_weighted.location = (100, 20)
    radius_weighted.operation = "MULTIPLY"
    links.new(radius.outputs[0], radius_weighted.inputs[0])
    links.new(width_max.outputs[0], radius_weighted.inputs[1])

    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-260, -240)

    offset_vector = nodes.new("ShaderNodeVectorMath")
    offset_vector.location = (100, -200)
    offset_vector.operation = "SCALE"
    links.new(normal.outputs[0], offset_vector.inputs[0])
    links.new(radius_weighted.outputs[0], _vector_scale_input(offset_vector))

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (300, -160)
    links.new(del_line.outputs["Geometry"], set_position.inputs["Geometry"])
    links.new(offset_vector.outputs[0], set_position.inputs["Offset"])

    flip = nodes.new("GeometryNodeFlipFaces")
    flip.location = (480, -160)
    links.new(set_position.outputs["Geometry"], flip.inputs["Mesh"])

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (650, -160)
    links.new(flip.outputs["Mesh"], setmat.inputs["Geometry"])
    links.new(gin.outputs[_MATERIAL_SOCKET], setmat.inputs["Material"])

    mark_generated = nodes.new("GeometryNodeStoreNamedAttribute")
    mark_generated.label = _GENERATED_LINE_NODE_LABEL
    mark_generated.location = (650, -360)
    mark_generated.data_type = "BOOLEAN"
    mark_generated.domain = "FACE"
    mark_generated.inputs["Name"].default_value = GENERATED_LINE_ATTR
    mark_generated.inputs["Value"].default_value = True
    links.new(setmat.outputs["Geometry"], mark_generated.inputs["Geometry"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (780, 0)
    links.new(gin.outputs[0], join.inputs[0])
    links.new(mark_generated.outputs["Geometry"], join.inputs[0])
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
            and _find_socket_id(tree, _LINE_MATERIAL_INDEX_SOCKET) is not None
            and any(node.bl_idname == "GeometryNodeFlipFaces" for node in tree.nodes)
            and _tree_uses_generated_mark(tree)
        )
        if ok:
            return tree
        bpy.data.node_groups.remove(tree)
    return _create_node_tree()


def _line_material_index(obj: bpy.types.Object) -> int:
    for index, mat in enumerate(obj.data.materials):
        if mat is not None and mat.name.startswith(MATERIAL_NAME):
            return index
    return 999


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
    sid_line_material = _find_socket_id(tree, _LINE_MATERIAL_INDEX_SOCKET)
    if sid_line_material is not None:
        mod[sid_line_material] = _line_material_index(obj)


def apply_intersection_shell(
    obj: bpy.types.Object,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
) -> bool:
    if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
        return False
    tree = _get_or_create_tree()
    _ensure_material_slot(obj, material)
    mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=SHELL_MODIFIER_NAME, type="NODES")
    mod.node_group = tree
    update_modifier_parameters(mod, thickness, offset, material)

    mod.show_viewport = True
    mod.show_render = True
    _position_modifier(obj, mod)
    return True
