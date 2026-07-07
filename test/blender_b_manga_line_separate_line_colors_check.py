"""B-MANGA Line: outline, inner, and intersection colors are independent."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

import b_manga_line  # noqa: E402
from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402
from b_manga_line import core  # noqa: E402


MATERIAL_SOCKET = "マテリアル"


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(name: str, location) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.5, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    settings.use_inner_line_creation_limit = False
    settings.use_intersection_creation_limit = False
    settings.outline_color = (1.0, 0.0, 0.0, 1.0)
    settings.inner_line_color = (0.0, 1.0, 0.0, 1.0)
    settings.intersection_color = (0.0, 0.0, 1.0, 1.0)
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _socket_id(tree: bpy.types.NodeTree, name: str) -> str:
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item.identifier
    raise AssertionError(f"socket not found: {name}")


def _modifier_material(mod: bpy.types.Modifier) -> bpy.types.Material:
    tree = mod.node_group
    if tree is None:
        raise AssertionError(f"node group missing: {mod.name}")
    mat = mod[_socket_id(tree, MATERIAL_SOCKET)]
    if mat is None:
        raise AssertionError(f"material missing: {mod.name}")
    return mat


def _material_color(mat: bpy.types.Material) -> tuple[float, float, float, float]:
    if not mat.use_nodes:
        raise AssertionError(f"nodes disabled: {mat.name}")
    for node in mat.node_tree.nodes:
        if node.type == "RGB" and node.label == "BML_Color":
            return tuple(float(item) for item in node.outputs[0].default_value)
    raise AssertionError(f"BML color node missing: {mat.name}")


def _assert_color(mat: bpy.types.Material, expected, label: str) -> None:
    actual = _material_color(mat)
    for got, want in zip(actual, expected):
        if abs(got - want) > 1.0e-6:
            raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _outline_material(obj: bpy.types.Object) -> bpy.types.Material:
    mod = obj.modifiers.get(core.MODIFIER_NAME)
    if mod is None:
        raise AssertionError(f"outline modifier missing: {obj.name}")
    index = int(mod.material_offset)
    mat = obj.data.materials[index]
    if mat is None:
        raise AssertionError(f"outline material missing: {obj.name}")
    return mat


def _first_intersection_modifier(objects: list[bpy.types.Object]) -> bpy.types.Modifier:
    for obj in objects:
        mod = next(core.iter_intersection_modifiers(obj), None)
        if mod is not None:
            return mod
    raise AssertionError("intersection modifier missing")


def _run() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, -4.0, 1.2), rotation=(1.25, 0.0, 0.0))
        bpy.context.scene.camera = bpy.context.object

        objects = [
            _make_cube("BML_color_A", (-0.25, 0.0, -4.0)),
            _make_cube("BML_color_B", (0.25, 0.0, -4.0)),
        ]
        _select(objects)
        assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}

        inner_mod = objects[0].modifiers.get(core.GN_MODIFIER_NAME)
        if inner_mod is None:
            raise AssertionError("inner modifier missing")
        intersection_mod = _first_intersection_modifier(objects)

        _assert_color(_outline_material(objects[0]), (1.0, 0.0, 0.0, 1.0), "outline")
        _assert_color(_modifier_material(inner_mod), (0.0, 1.0, 0.0, 1.0), "inner")
        _assert_color(
            _modifier_material(intersection_mod),
            (0.0, 0.0, 1.0, 1.0),
            "intersection",
        )

        objects[0].bmanga_line_settings.inner_line_color = (0.2, 0.4, 0.0, 1.0)
        objects[0].bmanga_line_settings.intersection_color = (0.4, 0.0, 0.6, 1.0)
        assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}
        assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}

        _assert_color(_modifier_material(inner_mod), (0.2, 0.4, 0.0, 1.0), "inner update")
        _assert_color(
            _modifier_material(intersection_mod),
            (0.4, 0.0, 0.6, 1.0),
            "intersection update",
        )

        bpy.context.scene.bmanga_line_preset_name = "line color preset"
        assert bpy.ops.bmanga_line.preset_save("EXEC_DEFAULT") == {"FINISHED"}
        objects[0].bmanga_line_settings.inner_line_color = (0.0, 0.0, 0.0, 1.0)
        assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}
        _assert_color(_modifier_material(inner_mod), (0.0, 0.0, 0.0, 1.0), "inner black")
        assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
        applied_color = tuple(float(item) for item in objects[0].bmanga_line_settings.inner_line_color)
        assert all(
            abs(got - want) < 1.0e-6
            for got, want in zip(applied_color, (0.2, 0.4, 0.0, 1.0))
        ), applied_color
        _assert_color(_modifier_material(inner_mod), (0.0, 0.0, 0.0, 1.0), "preset settings only")
        assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}
        _assert_color(_modifier_material(inner_mod), (0.2, 0.4, 0.0, 1.0), "preset")

        print("BMANGA_LINE_SEPARATE_LINE_COLORS_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


def main() -> None:
    with temporary_line_preset_store():
        _run()


if __name__ == "__main__":
    main()
