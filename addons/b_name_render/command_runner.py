"""Command execution engine for B-Name-Render cards."""

from __future__ import annotations

from dataclasses import dataclass, field

import bpy

from . import core, eevr_bridge


@dataclass
class _RenderSession:
    film_transparent: bool = False
    engine: str = ""
    view_layers: dict[str, bool] = field(default_factory=dict)
    node_mutes: list[tuple[object, bool]] = field(default_factory=list)


_SESSION: _RenderSession | None = None


def _iter_node_trees(scene):
    seen: set[int] = set()
    for attr in ("node_tree", "compositing_node_group"):
        tree = getattr(scene, attr, None)
        if tree is None:
            continue
        key = _node_tree_key(tree)
        if key not in seen:
            seen.add(key)
            yield tree
    for node_group in bpy.data.node_groups:
        if node_group is not None and _node_tree_key(node_group) not in seen:
            seen.add(_node_tree_key(node_group))
            yield node_group


def _node_tree_key(node_tree) -> int:
    try:
        return int(node_tree.as_pointer())
    except (AttributeError, ReferenceError, TypeError, ValueError):
        return id(node_tree)


def _iter_nodes_recursive(node_tree, seen=None):
    if node_tree is None:
        return
    seen = set() if seen is None else seen
    key = _node_tree_key(node_tree)
    if key in seen:
        return
    seen.add(key)
    for node in getattr(node_tree, "nodes", []):
        yield node
        if getattr(node, "type", "") == "GROUP":
            yield from _iter_nodes_recursive(getattr(node, "node_tree", None), seen)


def _begin_session(scene) -> None:
    global _SESSION
    if _SESSION is not None:
        _restore_session(scene)
    session = _RenderSession()
    session.film_transparent = bool(getattr(scene.render, "film_transparent", False))
    session.engine = str(getattr(scene.render, "engine", ""))
    for layer in scene.view_layers:
        if hasattr(layer, "use"):
            session.view_layers[layer.name] = bool(layer.use)
            layer.use = False
    for tree in _iter_node_trees(scene):
        for node in _iter_nodes_recursive(tree):
            if hasattr(node, "mute"):
                session.node_mutes.append((node, bool(node.mute)))
                if getattr(node, "type", "") == "OUTPUT_FILE":
                    node.mute = True
    scene.render.film_transparent = True
    _SESSION = session


def _restore_session(scene) -> None:
    global _SESSION
    session = _SESSION
    if session is None:
        return
    scene.render.film_transparent = session.film_transparent
    if session.engine:
        scene.render.engine = session.engine
    for layer in scene.view_layers:
        if hasattr(layer, "use") and layer.name in session.view_layers:
            layer.use = session.view_layers[layer.name]
    for node, mute in session.node_mutes:
        try:
            node.mute = mute
        except ReferenceError:
            pass
    _SESSION = None


def _set_view_layer(scene, name: str, enabled: bool) -> None:
    layer = scene.view_layers.get(name)
    if layer is not None and hasattr(layer, "use"):
        layer.use = enabled


def _find_layer_collection(layer_collection, collection_name: str):
    if getattr(layer_collection.collection, "name", "") == collection_name:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, collection_name)
        if found is not None:
            return found
    return None


def _set_collection_exclude(scene, collection_name: str, exclude: bool, view_layer_name: str = "") -> None:
    view_layers = [scene.view_layers.get(view_layer_name)] if view_layer_name else scene.view_layers
    for view_layer in view_layers:
        if view_layer is None:
            continue
        layer_coll = _find_layer_collection(view_layer.layer_collection, collection_name)
        if layer_coll is not None:
            layer_coll.exclude = exclude


def _set_node_mute(scene, node_name: str, mute: bool) -> int:
    count = 0
    for tree in _iter_node_trees(scene):
        for node in _iter_nodes_recursive(tree):
            if getattr(node, "name", "") == node_name or getattr(node, "label", "") == node_name:
                if hasattr(node, "mute"):
                    node.mute = mute
                    count += 1
    return count


def _node_matches_label(node, label: str) -> bool:
    if not label:
        return True
    parent = getattr(node, "parent", None)
    values = (
        getattr(node, "name", ""),
        getattr(node, "label", ""),
        getattr(parent, "name", "") if parent is not None else "",
        getattr(parent, "label", "") if parent is not None else "",
    )
    return any(label in str(value) for value in values)


def _set_output_group(group_name: str, label: str, mute: bool) -> int:
    group = bpy.data.node_groups.get(group_name)
    if group is None:
        return 0
    count = 0
    for node in _iter_nodes_recursive(group):
        if getattr(node, "type", "") == "OUTPUT_FILE" and _node_matches_label(node, label):
            node.mute = mute
            count += 1
    return count


def _set_input_in_node_tree(node_tree, input_name: str, value: float) -> int:
    count = 0
    for node in _iter_nodes_recursive(node_tree):
        for socket in getattr(node, "inputs", []):
            if getattr(socket, "name", "") == input_name and hasattr(socket, "default_value"):
                socket.default_value = value
                count += 1
    return count


def _set_aov_input(target_name: str, input_name: str, value: float) -> int:
    collection = bpy.data.collections.get(target_name)
    if collection is not None:
        count = 0
        for obj in collection.all_objects:
            if getattr(obj, "type", "") != "MESH":
                continue
            for slot in getattr(obj, "material_slots", []):
                material = getattr(slot, "material", None)
                if material is None or not getattr(material, "use_nodes", False):
                    continue
                count += _set_input_in_node_tree(material.node_tree, input_name, value)
        return count

    count = 0
    for group in bpy.data.node_groups:
        if target_name and target_name not in group.name:
            continue
        count += _set_input_in_node_tree(group, input_name, value)
    return count


def _set_output_name(scene, name: str) -> None:
    if not name:
        return
    scene.render.filepath = name
    for tree in _iter_node_trees(scene):
        for node in _iter_nodes_recursive(tree):
            if getattr(node, "type", "") != "OUTPUT_FILE":
                continue
            for slot in getattr(node, "file_slots", []):
                slot.path = name


def _set_output_folder(scene, folder: str) -> None:
    if not folder:
        return
    for tree in _iter_node_trees(scene):
        for node in _iter_nodes_recursive(tree):
            if getattr(node, "type", "") == "OUTPUT_FILE":
                node.base_path = folder


def _reload_images() -> int:
    count = 0
    for image in bpy.data.images:
        try:
            image.reload()
            count += 1
        except Exception:  # noqa: BLE001
            pass
    return count


def _configure_render(scene, engine: str, sample_count: int) -> None:
    scene.render.engine = engine
    if engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = max(1, int(sample_count))
    elif engine == "BLENDER_EEVEE_NEXT" and hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = max(1, int(sample_count))


def _ensure_renderable_view_layers(scene) -> None:
    layers = [layer for layer in scene.view_layers if hasattr(layer, "use")]
    if layers and not any(bool(layer.use) for layer in layers):
        for layer in layers:
            layer.use = True


def _render(scene, engine: str, sample_count: int) -> None:
    _configure_render(scene, engine, sample_count)
    _ensure_renderable_view_layers(scene)
    bpy.ops.render.render()


def _is_fisheye_enabled(scene) -> bool:
    return bool(
        getattr(scene, "fisheye_layout_mode", False)
        or getattr(scene, "bname_coma_camera_fisheye_layout_mode", False)
    )


def _render_layer(scene, group_name: str, label: str, engine: str, sample_count: int) -> None:
    _set_output_group(group_name, "", True)
    _set_output_group(group_name, label, False)
    _render(scene, engine, sample_count)


def _run_fisheye_or_layer(scene, command, mode: str) -> None:
    if not _is_fisheye_enabled(scene):
        _render_layer(scene, command.node_group_name, command.label_contains, command.engine, command.sample_count)
        return
    _setup_eevr_from_command(scene, command)
    if mode == "IMAGE":
        eevr_bridge.render_image()
    elif mode == "FACES":
        eevr_bridge.render_faces()
    elif mode == "ASSEMBLE":
        eevr_bridge.assemble_images()


def _setup_eevr_from_command(scene, command) -> None:
    if not eevr_bridge.setup(
        scene,
        getattr(scene, "camera", None),
        output_dir=str(getattr(command, "folder_path", "") or ""),
        output_name=str(getattr(command, "text_value", "") or ""),
    ):
        raise RuntimeError("魚眼設定が見つかりません")


def _run_command(context, command) -> None:
    scene = context.scene
    kind = command.command_type
    if kind == "STATE_BEGIN":
        _begin_session(scene)
    elif kind == "STATE_END":
        _restore_session(scene)
    elif kind == "SET_VIEW_LAYER":
        _set_view_layer(scene, command.view_layer_name, command.view_layer_enabled)
    elif kind == "SET_COLLECTION_EXCLUDE":
        _set_collection_exclude(scene, command.collection_name, command.exclude_collection, command.view_layer_name)
    elif kind == "SET_NODE_MUTE":
        _set_node_mute(scene, command.node_name, command.mute)
    elif kind == "SET_OUTPUT_GROUP":
        _set_output_group(command.node_group_name, command.label_contains, command.mute)
    elif kind == "SET_AOV_INPUT":
        _set_aov_input(command.node_group_name, command.input_name, command.float_value)
    elif kind == "SET_OUTPUT_NAME":
        _set_output_name(scene, command.text_value)
    elif kind == "SET_OUTPUT_FOLDER":
        _set_output_folder(scene, command.folder_path)
    elif kind == "RELOAD_IMAGES":
        _reload_images()
    elif kind == "RENDER":
        _render(scene, command.engine, command.sample_count)
    elif kind == "RENDER_LAYER":
        _render_layer(scene, command.node_group_name, command.label_contains, command.engine, command.sample_count)
    elif kind == "FISHEYE_RENDER_IMAGE_OR_LAYER":
        _run_fisheye_or_layer(scene, command, "IMAGE")
    elif kind == "FISHEYE_RENDER_FACES_OR_LAYER":
        _run_fisheye_or_layer(scene, command, "FACES")
    elif kind == "FISHEYE_ASSEMBLE_OR_LAYER":
        _run_fisheye_or_layer(scene, command, "ASSEMBLE")
    elif kind == "EEVR_SETUP":
        _setup_eevr_from_command(scene, command)
    elif kind == "EEVR_RENDER_IMAGE":
        _setup_eevr_from_command(scene, command)
        eevr_bridge.render_image()
    elif kind == "EEVR_RENDER_FACES":
        _setup_eevr_from_command(scene, command)
        eevr_bridge.render_faces()
    elif kind == "EEVR_ASSEMBLE":
        _setup_eevr_from_command(scene, command)
        eevr_bridge.assemble_images()
    elif kind == "OPERATOR" and command.operator_idname:
        eevr_bridge.run_operator(command.operator_idname)


def run_active_preset(context) -> int:
    preset = core.active_preset(context)
    if preset is None:
        return 0
    count = 0
    try:
        for command in preset.commands:
            if not command.enabled:
                continue
            _run_command(context, command)
            count += 1
    finally:
        _restore_session(context.scene)
    return count
