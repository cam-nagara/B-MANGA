"""Material node helpers for non-destructive coma content opacity masks."""

from __future__ import annotations

from typing import Any

import bpy


def _socket_by_name(sockets: Any, *names: str):
    for name in names:
        try:
            return sockets[name]
        except Exception:  # noqa: BLE001
            continue
    for socket in sockets:
        if getattr(socket, "name", "") in names:
            return socket
    return None


def _link(nt: bpy.types.NodeTree, output_socket, input_socket) -> None:
    if output_socket is None or input_socket is None:
        return
    try:
        nt.links.new(output_socket, input_socket)
    except Exception:  # noqa: BLE001
        pass


def mask_alpha_socket(
    nt: bpy.types.NodeTree,
    *,
    mask_object: bpy.types.Object | None,
    mask_image: bpy.types.Image | None,
    location: tuple[float, float] = (-900.0, -520.0),
):
    """Return a shader alpha socket sampled from the coma content mask image."""
    if nt is None or mask_object is None or mask_image is None:
        return None
    nodes = nt.nodes
    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.label = "コマ内容マスク座標"
    texcoord.location = location
    try:
        texcoord.object = mask_object
    except Exception:  # noqa: BLE001
        pass
    mapping = nodes.new("ShaderNodeMapping")
    mapping.label = "コマ内容マスク範囲"
    mapping.location = (location[0] + 220.0, location[1])
    try:
        mapping.vector_type = "POINT"
        mapping.inputs["Location"].default_value[0] = 0.5
        mapping.inputs["Location"].default_value[1] = 0.5
        mapping.inputs["Location"].default_value[2] = 0.0
    except Exception:  # noqa: BLE001
        pass
    tex = nodes.new("ShaderNodeTexImage")
    tex.label = "コマ内容マスク"
    tex.location = (location[0] + 460.0, location[1])
    try:
        tex.image = mask_image
        tex.extension = "CLIP"
        tex.interpolation = "Linear"
    except Exception:  # noqa: BLE001
        pass
    _link(nt, _socket_by_name(texcoord.outputs, "Object"), _socket_by_name(mapping.inputs, "Vector"))
    _link(nt, _socket_by_name(mapping.outputs, "Vector"), _socket_by_name(tex.inputs, "Vector"))
    return _socket_by_name(tex.outputs, "Alpha", "Fac", "Color")


def multiply_alpha_by_mask(
    nt: bpy.types.NodeTree,
    alpha_socket,
    *,
    mask_object: bpy.types.Object | None,
    mask_image: bpy.types.Image | None,
    location: tuple[float, float] = (-900.0, -520.0),
    label: str = "コマ内容マスク不透明度",
    power: float = 1.0,
):
    """Multiply an existing alpha socket by the sampled coma content mask."""
    mask_alpha = mask_alpha_socket(
        nt,
        mask_object=mask_object,
        mask_image=mask_image,
        location=location,
    )
    if alpha_socket is None:
        return mask_alpha
    if mask_alpha is None:
        return alpha_socket
    power_value = max(0.01, float(power or 1.0))
    if abs(power_value - 1.0) > 1.0e-6:
        power_node = nt.nodes.new("ShaderNodeMath")
        power_node.label = f"{label}の強さ"
        power_node.location = (location[0] + 700.0, location[1] + 120.0)
        try:
            power_node.operation = "POWER"
        except Exception:  # noqa: BLE001
            pass
        _link(nt, mask_alpha, power_node.inputs[0])
        try:
            power_node.inputs[1].default_value = power_value
        except Exception:  # noqa: BLE001
            pass
        mask_alpha = power_node.outputs["Value"]
    node = nt.nodes.new("ShaderNodeMath")
    node.label = label
    node.location = (location[0] + 700.0, location[1])
    try:
        node.operation = "MULTIPLY"
    except Exception:  # noqa: BLE001
        pass
    _link(nt, alpha_socket, node.inputs[0])
    _link(nt, mask_alpha, node.inputs[1])
    return node.outputs["Value"]


def setup_flat_emission_material(
    mat: bpy.types.Material,
    rgba: tuple[float, float, float, float],
    *,
    mask_object: bpy.types.Object | None = None,
    mask_image: bpy.types.Image | None = None,
) -> None:
    """Build a simple emission material whose alpha can be masked."""
    mat.diffuse_color = rgba
    mat.use_nodes = True
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    output = nt.nodes.new("ShaderNodeOutputMaterial")
    output.location = (360.0, 0.0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-180.0, -120.0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-180.0, 80.0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (120.0, 0.0)
    emission.inputs["Color"].default_value = (rgba[0], rgba[1], rgba[2], 1.0)
    emission.inputs["Strength"].default_value = 1.0
    value = nt.nodes.new("ShaderNodeValue")
    value.label = "不透明度"
    value.location = (-580.0, -240.0)
    value.outputs[0].default_value = max(0.0, min(1.0, float(rgba[3])))
    alpha = multiply_alpha_by_mask(
        nt,
        value.outputs[0],
        mask_object=mask_object,
        mask_image=mask_image,
        location=(-580.0, -520.0),
    )
    nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    _link(nt, alpha, mix.inputs["Fac"])
    nt.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    try:
        mat.blend_method = "BLEND" if mask_image is not None or float(rgba[3]) < 0.999 else "OPAQUE"
        mat.surface_render_method = "BLENDED" if mat.blend_method == "BLEND" else "DITHERED"
        mat.show_transparent_back = True
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
