"""B-MANGA Line offset controls propagate to selected objects."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core  # noqa: E402


OFFSET_SOCKET = "オフセット"


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
    return obj


def _socket_id(tree: bpy.types.NodeTree, name: str) -> str:
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item.identifier
    raise AssertionError(f"socket not found: {name}")


def _socket_value(mod: bpy.types.Modifier, name: str) -> float:
    tree = getattr(mod, "node_group", None)
    if tree is None:
        raise AssertionError(f"node group missing: {mod.name}")
    return float(mod[_socket_id(tree, name)])


def _assert_close(actual: float, expected: float, label: str) -> None:
    if abs(actual - expected) > 1.0e-6:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _select_all(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, -4.0, 1.2), rotation=(1.25, 0.0, 0.0))
        bpy.context.scene.camera = bpy.context.object

        objects = [
            _make_cube("BML_offset_A", (-0.25, 0.0, -4.0)),
            _make_cube("BML_offset_B", (0.25, 0.0, -4.0)),
        ]
        for obj in objects:
            _assert_close(obj.bmanga_line_settings.outline_offset, 1.0, obj.name)
            _assert_close(obj.bmanga_line_settings.inner_line_offset, 1.0, obj.name)
            _assert_close(
                obj.bmanga_line_settings.intersection_line_offset,
                1.0,
                obj.name,
            )
        _select_all(objects)
        assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}

        for obj in objects:
            assert obj.modifiers.get(core.MODIFIER_NAME) is not None
            assert obj.modifiers.get(core.GN_MODIFIER_NAME) is not None

        intersection_mods = [
            mod for obj in objects for mod in core.iter_intersection_modifiers(obj)
        ]
        assert intersection_mods, "交差線モディファイアが作成されていません"

        settings = objects[0].bmanga_line_settings
        settings.outline_offset = 0.25
        settings.inner_line_offset = 0.4
        settings.intersection_line_offset = -0.2

        for obj in objects:
            obj_settings = obj.bmanga_line_settings
            _assert_close(obj_settings.outline_offset, 0.25, obj.name)
            _assert_close(obj_settings.inner_line_offset, 0.4, obj.name)
            _assert_close(obj_settings.intersection_line_offset, -0.2, obj.name)
            _assert_close(obj.modifiers[core.MODIFIER_NAME].offset, 0.25, obj.name)
            _assert_close(
                _socket_value(obj.modifiers[core.GN_MODIFIER_NAME], OFFSET_SOCKET),
                0.4,
                obj.name,
            )

        for mod in intersection_mods:
            _assert_close(_socket_value(mod, OFFSET_SOCKET), -0.2, mod.name)

        assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=True) == {"FINISHED"}
        for obj in objects:
            _assert_close(obj.modifiers[core.MODIFIER_NAME].offset, 0.25, obj.name)
        assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=False) == {"FINISHED"}
        for obj in objects:
            _assert_close(obj.modifiers[core.MODIFIER_NAME].offset, 0.25, obj.name)

        bpy.context.scene.bmanga_line_preset_name = "offset preset"
        assert bpy.ops.bmanga_line.preset_save("EXEC_DEFAULT") == {"FINISHED"}
        settings.outline_offset = 1.0
        settings.inner_line_offset = 0.0
        settings.intersection_line_offset = 0.0
        assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
        for obj in objects:
            obj_settings = obj.bmanga_line_settings
            _assert_close(obj_settings.outline_offset, 0.25, obj.name)
            _assert_close(obj_settings.inner_line_offset, 0.4, obj.name)
            _assert_close(obj_settings.intersection_line_offset, -0.2, obj.name)

        print("BMANGA_LINE_OFFSET_CONTROLS_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
