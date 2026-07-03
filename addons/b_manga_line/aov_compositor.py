"""B-MANGA Line AOV compositor helpers."""

from __future__ import annotations

from pathlib import Path

import bpy

from .core import (
    AOV_COMPOSITE_NAME,
    AOV_INNER_LINES_NAME,
    AOV_INTERSECTION_LINES_NAME,
    AOV_OBJECT_MASK_NAME,
    AOV_OUTLINE_RAW_NAME,
)


NODE_PREFIX = "BML_LineAOVComposite"
TREE_NAME = "B-MANGA Line AOV Composite"


def _ensure_compositor_tree(scene: bpy.types.Scene) -> bpy.types.NodeTree:
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None:
        tree = bpy.data.node_groups.new(TREE_NAME, "CompositorNodeTree")
        scene.compositing_node_group = tree
    try:
        scene.use_nodes = True
    except Exception:  # noqa: BLE001
        pass
    return tree


def _owned(node: bpy.types.Node) -> bool:
    return node.name.startswith(NODE_PREFIX) or node.label.startswith(NODE_PREFIX)


def _clear_owned_nodes(tree: bpy.types.NodeTree) -> None:
    for node in list(tree.nodes):
        if _owned(node):
            tree.nodes.remove(node)


def _ensure_group_output(tree: bpy.types.NodeTree) -> bpy.types.Node:
    for node in tree.nodes:
        if getattr(node, "bl_idname", "") == "NodeGroupOutput":
            return node
    node = tree.nodes.new("NodeGroupOutput")
    node.location = (920.0, 0.0)
    return node


def _ensure_output_socket(tree: bpy.types.NodeTree, group_output: bpy.types.Node):
    interface = getattr(tree, "interface", None)
    if interface is not None:
        found = False
        for item in interface.items_tree:
            if (
                item.item_type == "SOCKET"
                and item.in_out == "OUTPUT"
                and item.name == AOV_COMPOSITE_NAME
            ):
                found = True
                break
        if not found:
            interface.new_socket(
                name=AOV_COMPOSITE_NAME,
                in_out="OUTPUT",
                socket_type="NodeSocketColor",
            )
    socket = group_output.inputs.get(AOV_COMPOSITE_NAME)
    if socket is None:
        raise RuntimeError(f"{AOV_COMPOSITE_NAME} 出力ソケットを作成できません")
    return socket


def _socket(node: bpy.types.Node, collection: str, name: str):
    sockets = getattr(node, collection)
    socket = sockets.get(name)
    if socket is None:
        raise RuntimeError(f"{node.name} に {name} ソケットがありません")
    return socket


def _aov_socket(rlayers: bpy.types.Node, name: str):
    socket = rlayers.outputs.get(name)
    if socket is None:
        raise RuntimeError(f"{name} AOV が Render Layers に見つかりません")
    return socket


def _new_owned_node(
    tree: bpy.types.NodeTree,
    node_type: str,
    suffix: str,
    location: tuple[float, float],
) -> bpy.types.Node:
    node = tree.nodes.new(node_type)
    node.name = f"{NODE_PREFIX}_{suffix}"
    node.label = node.name
    node.location = location
    return node


def _new_mix(
    tree: bpy.types.NodeTree,
    suffix: str,
    blend_type: str,
    location: tuple[float, float],
) -> bpy.types.Node:
    node = _new_owned_node(tree, "ShaderNodeMix", suffix, location)
    node.data_type = "RGBA"
    node.factor_mode = "UNIFORM"
    node.blend_type = blend_type
    node.inputs[0].default_value = 1.0
    try:
        node.clamp_result = True
    except Exception:  # noqa: BLE001
        pass
    return node


def _link_mix_rgba(
    tree: bpy.types.NodeTree,
    node: bpy.types.Node,
    socket_a,
    socket_b,
):
    tree.links.new(socket_a, node.inputs[6])
    tree.links.new(socket_b, node.inputs[7])
    return node.outputs[2]


def _add_file_output(tree: bpy.types.NodeTree, output_path: Path, image_socket) -> None:
    node = _new_owned_node(tree, "CompositorNodeOutputFile", "FileOutput", (920.0, -260.0))
    if hasattr(node, "directory"):
        node.directory = str(output_path.parent)
        node.file_name = ""
    if hasattr(node, "base_path"):
        node.base_path = str(output_path.parent)
    fmt = getattr(node, "format", None)
    if fmt is not None:
        fmt.media_type = "IMAGE"
        fmt.file_format = "PNG"
        fmt.color_mode = "RGBA"
    name = output_path.stem
    items = getattr(node, "file_output_items", None)
    if items is not None:
        for item in list(items):
            items.remove(item)
        items.new("RGBA", name)
        target_input = node.inputs.get(name)
    else:
        slots = getattr(node, "file_slots", None)
        if slots is not None:
            slots.clear()
            slots.new(name)
        target_input = next((s for s in node.inputs if getattr(s, "enabled", True)), None)
    if target_input is None:
        raise RuntimeError("ファイル出力ソケットを作成できません")
    tree.links.new(image_socket, target_input)


def setup_line_aov_compositor(
    scene: bpy.types.Scene,
    *,
    output_path: str | Path | None = None,
) -> bpy.types.NodeTree:
    """Create a line-only compositor output from split B-MANGA Line AOVs.

    The raw inverted-hull outline AOV contains the inflated source surface.
    Multiplying it by the inverted source-object mask removes that fill, then
    inner lines and intersection lines are added back without subtraction.
    """
    tree = _ensure_compositor_tree(scene)
    _clear_owned_nodes(tree)

    group_output = _ensure_group_output(tree)
    output_socket = _ensure_output_socket(tree, group_output)
    for link in list(tree.links):
        if link.to_node == group_output and link.to_socket == output_socket:
            tree.links.remove(link)

    rlayers = _new_owned_node(tree, "CompositorNodeRLayers", "RenderLayers", (-840.0, 0.0))
    try:
        rlayers.scene = scene
    except Exception:  # noqa: BLE001
        pass
    try:
        rlayers.layer = scene.view_layers[0].name
    except Exception:  # noqa: BLE001
        pass

    invert_mask = _new_owned_node(tree, "CompositorNodeInvert", "InvertObjectMask", (-540.0, -120.0))
    invert_mask.inputs["Factor"].default_value = 1.0
    invert_mask.inputs["Invert Color"].default_value = True
    invert_mask.inputs["Invert Alpha"].default_value = False
    tree.links.new(_aov_socket(rlayers, AOV_OBJECT_MASK_NAME), _socket(invert_mask, "inputs", "Color"))

    outline_mul = _new_mix(tree, "OutlineMinusSurface", "MULTIPLY", (-240.0, 40.0))
    outline_only = _link_mix_rgba(
        tree,
        outline_mul,
        _aov_socket(rlayers, AOV_OUTLINE_RAW_NAME),
        _socket(invert_mask, "outputs", "Color"),
    )

    add_inner = _new_mix(tree, "AddInnerLines", "ADD", (80.0, 20.0))
    outline_and_inner = _link_mix_rgba(
        tree,
        add_inner,
        outline_only,
        _aov_socket(rlayers, AOV_INNER_LINES_NAME),
    )

    add_intersection = _new_mix(tree, "AddIntersectionLines", "ADD", (420.0, 0.0))
    final_socket = _link_mix_rgba(
        tree,
        add_intersection,
        outline_and_inner,
        _aov_socket(rlayers, AOV_INTERSECTION_LINES_NAME),
    )

    tree.links.new(final_socket, output_socket)
    if output_path is not None:
        _add_file_output(tree, Path(output_path), final_socket)
    return tree
