"""World background switching for B-MANGA Liner's line-only display."""

from __future__ import annotations

import json

import bpy


STATE_PROP = "bml_line_only_world"
BACKGROUND_NODE_NAME = "BML_LineOnly_WorldBackground"
TEMP_WORLD_NAME = "BML_LineOnly_World"


def _node_tree(world: bpy.types.World | None):
    if world is None or not bool(getattr(world, "use_nodes", False)):
        return None
    return getattr(world, "node_tree", None)


def _active_output(world: bpy.types.World | None):
    tree = _node_tree(world)
    if tree is None:
        return None
    outputs = [node for node in tree.nodes if node.type == "OUTPUT_WORLD"]
    return next(
        (node for node in outputs if bool(getattr(node, "is_active_output", False))),
        outputs[0] if outputs else None,
    )


def _surface_link(output):
    tree = getattr(output, "id_data", None)
    if tree is None:
        return None
    surface = output.inputs.get("Surface")
    if surface is None:
        return None
    return next((link for link in tree.links if link.to_socket == surface), None)


def _capture_state(scene: bpy.types.Scene) -> dict:
    world = scene.world
    output = _active_output(world)
    link = _surface_link(output) if output is not None else None
    state = {
        "had_world": world is not None,
        "world_name": world.name if world is not None else "",
        "world_library": (
            world.library.filepath
            if world is not None and world.library is not None
            else ""
        ),
        "use_nodes": bool(getattr(world, "use_nodes", False)) if world else False,
        "color": tuple(getattr(world, "color", (0.05, 0.05, 0.05))) if world else None,
        "output_name": output.name if output is not None else "",
        "surface_link": None,
        "temporary_world_name": "",
    }
    if link is not None:
        state["surface_link"] = {
            "from_node": link.from_node.name,
            "from_socket": link.from_socket.name,
            "to_node": link.to_node.name,
            "to_socket": link.to_socket.name,
        }
    return state


def _editable_world(scene: bpy.types.Scene, state: dict) -> bpy.types.World:
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new(TEMP_WORLD_NAME)
        scene.world = world
        state["temporary_world_name"] = world.name
        return world
    if world.library is not None and world.override_library is None:
        world = world.copy()
        world.name = TEMP_WORLD_NAME
        scene.world = world
        state["temporary_world_name"] = world.name
    return world


def _ensure_output(world: bpy.types.World):
    output = _active_output(world)
    if output is not None:
        return output
    tree = world.node_tree
    if tree is None:
        return None
    output = tree.nodes.new("ShaderNodeOutputWorld")
    try:
        output.is_active_output = True
    except AttributeError:
        pass
    return output


def _ensure_white_background(world: bpy.types.World):
    tree = world.node_tree
    if tree is None:
        return None
    nodes = tree.nodes
    node = nodes.get(BACKGROUND_NODE_NAME)
    if node is not None and node.type != "BACKGROUND":
        nodes.remove(node)
        node = None
    if node is None:
        node = nodes.new("ShaderNodeBackground")
        node.name = BACKGROUND_NODE_NAME
    node.label = "B-MANGA ラインのみ表示（白背景）"
    for socket_name in ("Color", "Strength"):
        socket = node.inputs.get(socket_name)
        if socket is None:
            continue
        for link in list(tree.links):
            if link.to_socket == socket:
                tree.links.remove(link)
    node.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    node.inputs["Strength"].default_value = 1.0
    return node


def enable(scene: bpy.types.Scene | None) -> bool:
    if scene is None:
        return False
    if STATE_PROP not in scene:
        state = _capture_state(scene)
        world = _editable_world(scene, state)
        scene[STATE_PROP] = json.dumps(state, ensure_ascii=False)
    else:
        state = _load_state(scene)
        world = _editable_world(scene, state)
        if str(state.get("temporary_world_name", "") or ""):
            scene[STATE_PROP] = json.dumps(state, ensure_ascii=False)
    world.color = (1.0, 1.0, 1.0)
    world.use_nodes = True
    output = _ensure_output(world)
    background = _ensure_white_background(world)
    if output is None or background is None or world.node_tree is None:
        return False
    surface = output.inputs.get("Surface")
    if surface is None:
        return False
    for link in list(world.node_tree.links):
        if link.to_socket == surface:
            world.node_tree.links.remove(link)
    world.node_tree.links.new(background.outputs["Background"], surface)
    return True


def _load_state(scene: bpy.types.Scene) -> dict:
    raw = scene.get(STATE_PROP, "{}")
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        state = {}
    return state if isinstance(state, dict) else {}


def _restore_surface_link(world: bpy.types.World, state: dict) -> None:
    tree = getattr(world, "node_tree", None)
    if tree is None:
        return
    output_name = str(state.get("output_name", "") or "")
    output = tree.nodes.get(output_name) or _active_output(world)
    if output is not None:
        surface = output.inputs.get("Surface")
        if surface is not None:
            for link in list(tree.links):
                if link.to_socket == surface:
                    tree.links.remove(link)
    link_state = state.get("surface_link")
    if isinstance(link_state, dict):
        from_node = tree.nodes.get(str(link_state.get("from_node", "") or ""))
        to_node = tree.nodes.get(str(link_state.get("to_node", "") or "")) or output
        from_socket = (
            from_node.outputs.get(str(link_state.get("from_socket", "") or ""))
            if from_node is not None
            else None
        )
        to_socket = (
            to_node.inputs.get(str(link_state.get("to_socket", "Surface") or "Surface"))
            if to_node is not None
            else None
        )
        if from_socket is not None and to_socket is not None:
            tree.links.new(from_socket, to_socket)
    background = tree.nodes.get(BACKGROUND_NODE_NAME)
    if background is not None:
        tree.nodes.remove(background)


def _restore_legacy_background(world: bpy.types.World, state: dict) -> None:
    tree = getattr(world, "node_tree", None)
    if tree is None:
        return
    background = next((node for node in tree.nodes if node.type == "BACKGROUND"), None)
    if background is None:
        return
    color = state.get("background_color")
    strength = state.get("background_strength")
    if color is not None:
        background.inputs["Color"].default_value = tuple(color)
    if strength is not None:
        background.inputs["Strength"].default_value = float(strength)


def _original_world(state: dict) -> bpy.types.World | None:
    name = str(state.get("world_name", "") or "")
    library_path = str(state.get("world_library", "") or "")
    if not name:
        return None
    for world in bpy.data.worlds:
        if world.name != name:
            continue
        current_library = world.library.filepath if world.library is not None else ""
        if current_library == library_path:
            return world
    return bpy.data.worlds.get(name)


def restore(scene: bpy.types.Scene | None) -> bool:
    if scene is None or STATE_PROP not in scene:
        return False
    state = _load_state(scene)
    current_world = scene.world
    original_world = _original_world(state)
    temporary_name = str(state.get("temporary_world_name", "") or "")
    if original_world is None and not temporary_name and current_world is not None:
        original_world = current_world

    if not temporary_name and current_world is not None:
        _restore_surface_link(current_world, state)
        _restore_legacy_background(current_world, state)
        color = state.get("color")
        if color is not None:
            current_world.color = tuple(color[:3])
        current_world.use_nodes = bool(state.get("use_nodes", False))

    scene.world = original_world if bool(state.get("had_world", False)) else None
    temporary_world = bpy.data.worlds.get(temporary_name) if temporary_name else None
    if temporary_world is not None and temporary_world.users == 0:
        bpy.data.worlds.remove(temporary_world)
    del scene[STATE_PROP]
    return True
