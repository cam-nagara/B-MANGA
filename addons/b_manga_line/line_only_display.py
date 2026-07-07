"""B-MANGA Liner line-only display mode."""

from __future__ import annotations

import bpy

from . import outline_setup


LINE_ONLY_OUTPUT_NAME = "BML_LineOnly_WhiteOutput"
LINE_ONLY_EMISSION_NAME = "BML_LineOnly_WhiteEmission"
PROP_LINE_ONLY_ORIGINAL_OUTPUT = "bml_line_only_original_output"
PROP_LINE_ONLY_ORIGINAL_USE_NODES = "bml_line_only_original_use_nodes"


def material_output_nodes(mat: bpy.types.Material) -> list[bpy.types.Node]:
    if not mat.use_nodes or mat.node_tree is None:
        return []
    return [node for node in mat.node_tree.nodes if node.type == "OUTPUT_MATERIAL"]


def active_material_output(mat: bpy.types.Material) -> bpy.types.Node | None:
    outputs = material_output_nodes(mat)
    for node in outputs:
        if bool(getattr(node, "is_active_output", False)):
            return node
    return outputs[0] if outputs else None


def set_active_material_output(
    mat: bpy.types.Material,
    target: bpy.types.Node,
) -> None:
    for node in material_output_nodes(mat):
        try:
            node.is_active_output = node == target
        except AttributeError:
            pass
    try:
        target.is_active_output = True
    except AttributeError:
        pass


def is_line_only_surface_material(mat: bpy.types.Material | None) -> bool:
    return (
        mat is not None
        and not outline_setup._is_line_material(mat)
        and not outline_setup._material_name_matches(
            mat, outline_setup.SHEET_RIM_HIDDEN_MATERIAL_NAME,
        )
        and not outline_setup._material_name_matches(
            mat, outline_setup.LINE_ONLY_MATERIAL_NAME,
        )
    )


def _ensure_line_only_output(mat: bpy.types.Material) -> bpy.types.Node | None:
    try:
        mat.use_nodes = True
    except RuntimeError:
        return None
    if mat.node_tree is None:
        return None
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    output = nodes.get(LINE_ONLY_OUTPUT_NAME)
    if output is None:
        output = nodes.new("ShaderNodeOutputMaterial")
        output.name = LINE_ONLY_OUTPUT_NAME
        output.label = "B-MANGA ラインのみ表示"
    emission = nodes.get(LINE_ONLY_EMISSION_NAME)
    if emission is None:
        emission = nodes.new("ShaderNodeEmission")
        emission.name = LINE_ONLY_EMISSION_NAME
        emission.label = "B-MANGA 白表示"
    emission.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    for link in list(links):
        if link.to_node == output and link.to_socket == output.inputs["Surface"]:
            links.remove(link)
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return output


def _enable_material_line_only(mat: bpy.types.Material) -> bool:
    if not is_line_only_surface_material(mat):
        return False
    if PROP_LINE_ONLY_ORIGINAL_USE_NODES not in mat:
        mat[PROP_LINE_ONLY_ORIGINAL_USE_NODES] = bool(mat.use_nodes)
        active = active_material_output(mat)
        mat[PROP_LINE_ONLY_ORIGINAL_OUTPUT] = active.name if active else ""
    output = _ensure_line_only_output(mat)
    if output is None:
        return False
    set_active_material_output(mat, output)
    return True


def _restore_material_line_only(mat: bpy.types.Material) -> bool:
    if PROP_LINE_ONLY_ORIGINAL_USE_NODES not in mat:
        return False
    original_use_nodes = bool(mat.get(PROP_LINE_ONLY_ORIGINAL_USE_NODES, True))
    original_output = str(mat.get(PROP_LINE_ONLY_ORIGINAL_OUTPUT, "") or "")
    if original_use_nodes:
        try:
            mat.use_nodes = True
        except RuntimeError:
            return False
        target = mat.node_tree.nodes.get(original_output) if mat.node_tree else None
        if target is None:
            target = next(
                (
                    node for node in material_output_nodes(mat)
                    if node.name != LINE_ONLY_OUTPUT_NAME
                ),
                None,
            )
        if target is not None:
            set_active_material_output(mat, target)
    else:
        try:
            mat.use_nodes = False
        except RuntimeError:
            return False
    del mat[PROP_LINE_ONLY_ORIGINAL_USE_NODES]
    if PROP_LINE_ONLY_ORIGINAL_OUTPUT in mat:
        del mat[PROP_LINE_ONLY_ORIGINAL_OUTPUT]
    return True


def set_materials_line_only(enabled: bool) -> int:
    """通常マテリアルの出力だけを白い放射出力へ切り替える."""
    changed = 0
    for mat in bpy.data.materials:
        try:
            ok = (
                _enable_material_line_only(mat)
                if enabled
                else _restore_material_line_only(mat)
            )
        except ReferenceError:
            ok = False
        if ok:
            changed += 1
    return changed
