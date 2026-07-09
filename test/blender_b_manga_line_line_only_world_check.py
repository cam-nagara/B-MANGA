"""B-MANGA Liner: line-only mode uses and restores a white World background."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, line_only_world  # noqa: E402


def _make_camera() -> None:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 2.0))
    bpy.context.scene.camera = bpy.context.object


def _set_line_only(enabled: bool) -> None:
    bpy.context.scene.bmanga_line_line_only_visible = enabled
    assert bool(bpy.context.scene.bmanga_line_line_only_visible) is enabled


def _incoming_link(node, socket_name: str):
    socket = node.inputs[socket_name]
    return next(
        (link for link in node.id_data.links if link.to_socket == socket),
        None,
    )


def _active_output(world: bpy.types.World):
    outputs = [node for node in world.node_tree.nodes if node.type == "OUTPUT_WORLD"]
    return next(node for node in outputs if bool(node.is_active_output))


def _configure_linked_input_world() -> tuple:
    world = bpy.data.worlds.new("BML_World_Original")
    world.color = (0.03, 0.04, 0.05)
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    unused_output = tree.nodes.new("ShaderNodeOutputWorld")
    unused_output.name = "Unused World Output"
    output = tree.nodes.new("ShaderNodeOutputWorld")
    output.name = "Original World Output"
    output.is_active_output = True
    background = tree.nodes.new("ShaderNodeBackground")
    background.name = "Original Background"
    color = tree.nodes.new("ShaderNodeRGB")
    color.name = "Original Color Source"
    color.outputs["Color"].default_value = (0.04, 0.10, 0.20, 1.0)
    strength = tree.nodes.new("ShaderNodeValue")
    strength.name = "Original Strength Source"
    strength.outputs["Value"].default_value = 0.30
    tree.links.new(color.outputs["Color"], background.inputs["Color"])
    tree.links.new(strength.outputs["Value"], background.inputs["Strength"])
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    bpy.context.scene.world = world
    return world, output, background, color, strength


def _render_center_pixel() -> tuple[float, float, float, float]:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 16
    scene.render.resolution_y = 16
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    render_path = Path(tempfile.gettempdir()) / "bml_line_only_world_render.png"
    scene.render.filepath = str(render_path)
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(render_path), check_existing=False)
    try:
        width, height = (int(value) for value in image.size)
        pixels = tuple(float(value) for value in image.pixels[:])
        assert width > 0 and height > 0 and len(pixels) == width * height * 4
        offset = (((height // 2) * width) + (width // 2)) * 4
        return pixels[offset : offset + 4]
    finally:
        bpy.data.images.remove(image)
        try:
            render_path.unlink()
        except OSError:
            pass


def _assert_white_background_node(world: bpy.types.World) -> None:
    node = world.node_tree.nodes.get(line_only_world.BACKGROUND_NODE_NAME)
    assert node is not None and node.type == "BACKGROUND"
    assert _incoming_link(node, "Color") is None
    assert _incoming_link(node, "Strength") is None
    assert all(
        math.isclose(float(value), 1.0, abs_tol=1.0e-7)
        for value in node.inputs["Color"].default_value
    )
    assert math.isclose(
        float(node.inputs["Strength"].default_value), 1.0, abs_tol=1.0e-7,
    )
    link = _incoming_link(_active_output(world), "Surface")
    assert link is not None and link.from_node == node


def _test_linked_inputs_and_active_output_restore() -> None:
    world, output, background, color, strength = _configure_linked_input_world()
    original_color = tuple(world.color)
    before_pixel = _render_center_pixel()
    assert max(before_pixel[:3]) < 0.5, before_pixel

    _set_line_only(True)
    assert bpy.context.scene.world == world
    assert tuple(world.color) == (1.0, 1.0, 1.0)
    _assert_white_background_node(world)
    assert _incoming_link(background, "Color").from_node == color
    assert _incoming_link(background, "Strength").from_node == strength
    white_pixel = _render_center_pixel()
    assert min(white_pixel[:3]) > 0.99, white_pixel

    _set_line_only(False)
    assert bpy.context.scene.world == world
    assert tuple(world.color) == original_color
    assert world.node_tree.nodes.get(line_only_world.BACKGROUND_NODE_NAME) is None
    assert _incoming_link(output, "Surface").from_node == background
    assert _incoming_link(background, "Color").from_node == color
    assert _incoming_link(background, "Strength").from_node == strength
    restored_pixel = _render_center_pixel()
    assert all(
        math.isclose(before_pixel[index], restored_pixel[index], abs_tol=1.0e-5)
        for index in range(4)
    ), (before_pixel, restored_pixel)
    assert core.PROP_LINE_ONLY_WORLD not in bpy.context.scene


def _test_missing_world_round_trip() -> None:
    bpy.context.scene.world = None
    _set_line_only(True)
    temporary = bpy.context.scene.world
    assert temporary is not None
    temporary_name = temporary.name_full
    _assert_white_background_node(temporary)
    _set_line_only(False)
    assert bpy.context.scene.world is None
    assert bpy.data.worlds.get(temporary_name) is None


def _test_nodes_disabled_round_trip() -> None:
    world = bpy.data.worlds.new("BML_World_NodesDisabled")
    world.color = (0.12, 0.23, 0.34)
    world.use_nodes = False
    original_color = tuple(world.color)
    original_use_nodes = bool(world.use_nodes)
    bpy.context.scene.world = world
    _set_line_only(True)
    assert bool(world.use_nodes)
    _assert_white_background_node(world)
    _set_line_only(False)
    assert bool(world.use_nodes) is original_use_nodes
    assert tuple(world.color) == original_color
    assert world.node_tree.nodes.get(line_only_world.BACKGROUND_NODE_NAME) is None


def _test_linked_world_uses_temporary_local_copy() -> None:
    source = bpy.data.worlds.new("BML_Linked_World_Source")
    source.use_nodes = True
    library_path = Path(tempfile.gettempdir()) / "bml_line_only_world_source.blend"
    bpy.data.libraries.write(str(library_path), {source})
    bpy.data.worlds.remove(source)
    with bpy.data.libraries.load(str(library_path), link=True) as (data_from, data_to):
        assert "BML_Linked_World_Source" in data_from.worlds
        data_to.worlds = ["BML_Linked_World_Source"]
    linked = data_to.worlds[0]
    bpy.context.scene.world = linked

    _set_line_only(True)
    temporary = bpy.context.scene.world
    assert temporary is not linked
    assert temporary.library is None
    temporary_name = temporary.name_full
    _assert_white_background_node(temporary)
    _set_line_only(False)
    assert bpy.context.scene.world == linked, (
        getattr(bpy.context.scene.world, "name_full", None),
        linked.name_full,
        tuple(world.name_full for world in bpy.data.worlds),
    )
    assert bpy.data.worlds.get(temporary_name) is None
    try:
        library_path.unlink()
    except OSError:
        pass


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _make_camera()
        _test_linked_inputs_and_active_output_restore()
        _test_missing_world_round_trip()
        _test_nodes_disabled_round_trip()
        _test_linked_world_uses_temporary_local_copy()
        print("BMANGA_LINE_LINE_ONLY_WORLD_OK")
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
