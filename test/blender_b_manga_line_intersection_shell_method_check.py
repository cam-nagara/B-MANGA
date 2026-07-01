"""B-MANGA Line: default intersection lines use object-local line shells."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_lines,
    intersection_shell,
    outline_setup,
    presets,
)


THICKNESS_SOCKET = "線の太さ"
OFFSET_SOCKET = "オフセット"
MATERIAL_SOCKET = "マテリアル"


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    assert settings.intersection_method == "SHELL"
    settings.intersection_enabled = True
    settings.use_intersection_creation_limit = False
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


def _socket_value(mod: bpy.types.Modifier, name: str):
    tree = getattr(mod, "node_group", None)
    if tree is None:
        raise AssertionError(f"node group missing: {mod.name}")
    return mod[_socket_id(tree, name)]


def _intersection_material_polygons(obj: bpy.types.Object) -> int:
    mat = outline_setup.get_line_material(obj, "intersection")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_index = None
        for index, item in enumerate(mesh.materials):
            if item is not None and item.name.startswith(mat.name):
                line_index = index
                break
        assert line_index is not None, "交差線素材が評価済みメッシュにありません"
        return sum(1 for poly in mesh.polygons if poly.material_index == line_index)
    finally:
        bpy.data.meshes.remove(mesh)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        objects = [
            _make_cube("BML_shell_intersection_A", (-0.25, 0.0, 0.0)),
            _make_cube("BML_shell_intersection_B", (0.25, 0.0, 0.0)),
            _make_cube("BML_shell_intersection_C", (0.0, 0.25, 0.0)),
        ]
        _select(objects)

        real_auto_targets = intersection_lines._auto_targets

        def forbidden_auto_targets(*_args, **_kwargs):
            raise AssertionError("交差相手の候補列挙が呼ばれています")

        intersection_lines._auto_targets = forbidden_auto_targets
        try:
            for obj in objects:
                assert presets.apply_line_settings(
                    obj,
                    bpy.context,
                    refresh_scene=False,
                ), obj.name

            old_threshold = intersection_lines._DEFERRED_VIEWPORT_THRESHOLD
            intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = 0
            try:
                refreshed = intersection_lines.refresh_scene_intersections(bpy.context.scene)
            finally:
                intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = old_threshold
        finally:
            intersection_lines._auto_targets = real_auto_targets

        assert set(refreshed) == set(objects), [obj.name for obj in refreshed]
        for obj in objects:
            mods = list(core.iter_intersection_modifiers(obj))
            assert len(mods) == 1, (obj.name, [mod.name for mod in mods])
            mod = mods[0]
            assert mod.name == intersection_shell.SHELL_MODIFIER_NAME
            assert mod.node_group is not None
            assert intersection_lines._modifier_target(mod) is None
            assert mod.show_viewport
            assert mod.show_render
            assert not intersection_lines.is_deferred_viewport_modifier(mod)
            assert _intersection_material_polygons(obj) > 0

        source = objects[0]
        mod = next(core.iter_intersection_modifiers(source))
        mat = outline_setup.get_line_material(source, "intersection")
        intersection_lines.update_parameters(
            source,
            thickness=0.0123,
            offset=-0.25,
            material=mat,
        )
        assert abs(float(_socket_value(mod, THICKNESS_SOCKET)) - 0.0123) < 1.0e-7
        assert abs(float(_socket_value(mod, OFFSET_SOCKET)) + 0.25) < 1.0e-7
        assert _socket_value(mod, MATERIAL_SOCKET) == mat

        print("[PASS] default intersection lines use shell method without target scan")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
