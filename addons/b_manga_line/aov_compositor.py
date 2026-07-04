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
GROUP_TREE_NAME = "BML_LineAOVCompositeGroup"


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


def _new_vector_math(
    tree: bpy.types.NodeTree,
    suffix: str,
    operation: str,
    location: tuple[float, float],
) -> bpy.types.Node:
    node = _new_owned_node(tree, "ShaderNodeVectorMath", suffix, location)
    node.operation = operation
    return node


def _new_math(
    tree: bpy.types.NodeTree,
    suffix: str,
    operation: str,
    location: tuple[float, float],
    *,
    clamp: bool = False,
) -> bpy.types.Node:
    node = _new_owned_node(tree, "ShaderNodeMath", suffix, location)
    node.operation = operation
    if hasattr(node, "use_clamp"):
        node.use_clamp = clamp
    return node


def _link_vector_math(
    tree: bpy.types.NodeTree,
    node: bpy.types.Node,
    socket_a,
    socket_b,
):
    tree.links.new(socket_a, node.inputs[0])
    tree.links.new(socket_b, node.inputs[1])
    return node.outputs["Vector"]


def _link_math(
    tree: bpy.types.NodeTree,
    node: bpy.types.Node,
    socket_a,
    socket_b,
):
    tree.links.new(socket_a, node.inputs[0])
    tree.links.new(socket_b, node.inputs[1])
    return node.outputs["Value"]


def _alpha_socket(
    tree: bpy.types.NodeTree,
    image_socket,
    suffix: str,
    location: tuple[float, float],
):
    node = _new_owned_node(tree, "CompositorNodeSeparateColor", suffix, location)
    tree.links.new(image_socket, _socket(node, "inputs", "Image"))
    return _socket(node, "outputs", "Alpha")


def _invert_value(
    tree: bpy.types.NodeTree,
    value_socket,
    suffix: str,
    location: tuple[float, float],
):
    node = _new_math(tree, suffix, "SUBTRACT", location)
    node.inputs[0].default_value = 1.0
    tree.links.new(value_socket, node.inputs[1])
    return node.outputs["Value"]


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


def _create_line_composite_group() -> bpy.types.NodeTree:
    old = bpy.data.node_groups.get(GROUP_TREE_NAME)
    if old is not None:
        bpy.data.node_groups.remove(old)

    tree = bpy.data.node_groups.new(GROUP_TREE_NAME, "CompositorNodeTree")
    for name in (
        AOV_OUTLINE_RAW_NAME,
        AOV_OBJECT_MASK_NAME,
        AOV_INNER_LINES_NAME,
        AOV_INTERSECTION_LINES_NAME,
    ):
        tree.interface.new_socket(
            name=name,
            in_out="INPUT",
            socket_type="NodeSocketColor",
        )
    tree.interface.new_socket(
        name=AOV_COMPOSITE_NAME,
        in_out="OUTPUT",
        socket_type="NodeSocketColor",
    )

    gin = tree.nodes.new("NodeGroupInput")
    gin.name = f"{NODE_PREFIX}_GroupInput"
    gin.label = gin.name
    gin.location = (-980.0, 0.0)
    gout = tree.nodes.new("NodeGroupOutput")
    gout.name = f"{NODE_PREFIX}_GroupOutput"
    gout.label = gout.name
    gout.location = (1240.0, -20.0)

    outline_raw_socket = _socket(gin, "outputs", AOV_OUTLINE_RAW_NAME)
    object_mask_socket = _socket(gin, "outputs", AOV_OBJECT_MASK_NAME)
    inner_lines_socket = _socket(gin, "outputs", AOV_INNER_LINES_NAME)
    intersection_lines_socket = _socket(gin, "outputs", AOV_INTERSECTION_LINES_NAME)

    invert_mask = _new_owned_node(tree, "CompositorNodeInvert", "InvertObjectMask", (-740.0, -120.0))
    invert_mask.inputs["Factor"].default_value = 1.0
    invert_mask.inputs["Invert Color"].default_value = True
    invert_mask.inputs["Invert Alpha"].default_value = False
    tree.links.new(object_mask_socket, _socket(invert_mask, "inputs", "Color"))

    outline_mul = _new_vector_math(tree, "OutlineMinusSurface", "MULTIPLY", (-440.0, 40.0))
    outline_only = _link_vector_math(
        tree,
        outline_mul,
        outline_raw_socket,
        _socket(invert_mask, "outputs", "Color"),
    )

    outline_alpha = _alpha_socket(tree, outline_raw_socket, "OutlineRawAlpha", (-720.0, -320.0))
    object_alpha = _alpha_socket(tree, object_mask_socket, "ObjectMaskAlpha", (-720.0, -460.0))
    inverted_object_alpha = _invert_value(tree, object_alpha, "InvertObjectMaskAlpha", (-460.0, -460.0))
    outline_alpha_mul = _new_math(tree, "OutlineMinusSurfaceAlpha", "MULTIPLY", (-220.0, -360.0))
    outline_only_alpha = _link_math(tree, outline_alpha_mul, outline_alpha, inverted_object_alpha)

    inner_alpha = _alpha_socket(tree, inner_lines_socket, "InnerLinesAlpha", (-400.0, -620.0))
    inverted_inner_alpha = _invert_value(tree, inner_alpha, "InvertInnerLinesAlpha", (-120.0, -620.0))
    outline_without_inner = _new_vector_math(tree, "OutlineWithoutInnerLines", "MULTIPLY", (-120.0, 150.0))
    outline_color_for_inner = _link_vector_math(
        tree,
        outline_without_inner,
        outline_only,
        inverted_inner_alpha,
    )

    add_inner = _new_vector_math(tree, "AddInnerLines", "ADD", (100.0, 80.0))
    outline_and_inner = _link_vector_math(
        tree,
        add_inner,
        outline_color_for_inner,
        inner_lines_socket,
    )

    intersection_alpha = _alpha_socket(
        tree,
        intersection_lines_socket,
        "IntersectionLinesAlpha",
        (-120.0, -760.0),
    )
    inverted_intersection_alpha = _invert_value(
        tree,
        intersection_alpha,
        "InvertIntersectionLinesAlpha",
        (160.0, -760.0),
    )
    color_without_intersection = _new_vector_math(
        tree,
        "ColorWithoutIntersectionLines",
        "MULTIPLY",
        (360.0, 80.0),
    )
    outline_inner_for_intersection = _link_vector_math(
        tree,
        color_without_intersection,
        outline_and_inner,
        inverted_intersection_alpha,
    )

    add_intersection = _new_vector_math(tree, "AddIntersectionLines", "ADD", (600.0, 0.0))
    final_color_socket = _link_vector_math(
        tree,
        add_intersection,
        outline_inner_for_intersection,
        intersection_lines_socket,
    )

    add_outline_inner_alpha = _new_math(tree, "AddOutlineInnerAlpha", "ADD", (100.0, -400.0), clamp=True)
    outline_inner_alpha = _link_math(tree, add_outline_inner_alpha, outline_only_alpha, inner_alpha)
    add_final_alpha = _new_math(tree, "AddIntersectionAlpha", "ADD", (360.0, -460.0), clamp=True)
    final_alpha_socket = _link_math(tree, add_final_alpha, outline_inner_alpha, intersection_alpha)

    set_alpha = _new_owned_node(tree, "CompositorNodeSetAlpha", "SetTransparentLineAlpha", (840.0, -20.0))
    tree.links.new(final_color_socket, _socket(set_alpha, "inputs", "Image"))
    tree.links.new(final_alpha_socket, _socket(set_alpha, "inputs", "Alpha"))
    tree.links.new(_socket(set_alpha, "outputs", "Image"), _socket(gout, "inputs", AOV_COMPOSITE_NAME))
    return tree


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
    group_tree = _create_line_composite_group()

    rlayers = _new_owned_node(tree, "CompositorNodeRLayers", "RenderLayers", (-840.0, 0.0))
    try:
        rlayers.scene = scene
    except Exception:  # noqa: BLE001
        pass
    try:
        rlayers.layer = scene.view_layers[0].name
    except Exception:  # noqa: BLE001
        pass

    outline_raw_socket = _aov_socket(rlayers, AOV_OUTLINE_RAW_NAME)
    object_mask_socket = _aov_socket(rlayers, AOV_OBJECT_MASK_NAME)
    inner_lines_socket = _aov_socket(rlayers, AOV_INNER_LINES_NAME)
    intersection_lines_socket = _aov_socket(rlayers, AOV_INTERSECTION_LINES_NAME)

    group = _new_owned_node(tree, "CompositorNodeGroup", "Group", (-420.0, -20.0))
    group.node_tree = group_tree
    tree.links.new(outline_raw_socket, _socket(group, "inputs", AOV_OUTLINE_RAW_NAME))
    tree.links.new(object_mask_socket, _socket(group, "inputs", AOV_OBJECT_MASK_NAME))
    tree.links.new(inner_lines_socket, _socket(group, "inputs", AOV_INNER_LINES_NAME))
    tree.links.new(intersection_lines_socket, _socket(group, "inputs", AOV_INTERSECTION_LINES_NAME))
    final_socket = _socket(group, "outputs", AOV_COMPOSITE_NAME)

    result = _new_owned_node(tree, "NodeReroute", "Result", (-80.0, -20.0))
    result.label = AOV_COMPOSITE_NAME
    tree.links.new(final_socket, result.inputs[0])
    if output_path is not None:
        _add_file_output(tree, Path(output_path), result.outputs[0])
    return tree
