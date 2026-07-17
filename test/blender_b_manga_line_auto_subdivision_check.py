"""B-MANGA Liner local line subdivision integration checks."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    outline_local_subdivision,
    presets,
    subdivision_lod,
)
from b_manga_line.gn_socket_compat import get_gn_modifier_input  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.object.camera_add(location=(4.0, -6.0, 4.0))
    camera = bpy.context.object
    camera.rotation_euler = (
        Vector((0.0, 0.0, 0.0)) - camera.location
    ).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera


def _make_cube(name: str, location=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    material = bpy.data.materials.new(name + "_Surface")
    obj.data.materials.append(material)
    settings = obj.bmanga_line_settings
    settings.weld_mesh_for_outline = False
    settings.auto_subdivision_for_midpoint = True
    settings.edge_smooth_factor = -0.5
    return obj


def _mesh_signature(obj: bpy.types.Object):
    mesh = obj.data
    return (
        tuple(tuple(vertex.co) for vertex in mesh.vertices),
        tuple(tuple(polygon.vertices) for polygon in mesh.polygons),
        tuple(polygon.material_index for polygon in mesh.polygons),
    )


def _evaluated_generated_counts(obj: bpy.types.Object) -> tuple[int, int]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        generated = mesh.attributes.get(core.GENERATED_LINE_ATTR)
        assert generated is None, "生成ラインが元メッシュ成分へ混入しています"
        return len(mesh.polygons), 0
    finally:
        evaluated.to_mesh_clear()


def _assert_camera_fallback_switches_safely() -> None:
    scene = bpy.context.scene
    camera = scene.camera
    scene.camera = None
    if camera is not None:
        bpy.data.objects.remove(camera, do_unlink=True)

    obj = _make_cube("カメラ未設定フォールバック")
    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    state = obj.modifiers.get(core.MODIFIER_NAME)
    assert state is not None and state.show_viewport and state.show_render
    assert outline_local_subdivision.get_modifier(obj) is None

    bpy.ops.object.camera_add(location=(4.0, -6.0, 4.0))
    camera = bpy.context.object
    camera.rotation_euler = (
        Vector((0.0, 0.0, 0.0)) - camera.location
    ).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    assert outline_local_subdivision.get_modifier(obj) is not None
    assert not state.show_viewport and not state.show_render

    scene.camera = None
    bpy.data.objects.remove(camera, do_unlink=True)
    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    assert outline_local_subdivision.get_modifier(obj) is None
    assert state.show_viewport and state.show_render


def _assert_source_mesh_unchanged_and_shell_local() -> None:
    obj = _make_cube("ライン局所細分化")
    before = _mesh_signature(obj)
    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    assert _mesh_signature(obj) == before
    assert not any(mod.type == "SUBSURF" for mod in obj.modifiers)
    state = obj.modifiers.get(core.MODIFIER_NAME)
    local = obj.modifiers.get(core.OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME)
    assert state is not None and state.type == "SOLIDIFY"
    assert not state.show_viewport and not state.show_render
    assert local is not None and outline_local_subdivision.is_modifier(local)
    assert _evaluated_generated_counts(obj) == (6, 0)
    assert any(
        node.bl_idname == "GeometryNodeGeometryToInstance"
        for node in local.node_group.nodes
    )

    old_tree = local.node_group
    old_tree_pointer = old_tree.as_pointer()
    for node in old_tree.nodes:
        if "2026-07-10 V5" in node.label:
            node.label = node.label.replace("2026-07-10 V5", "2026-07-10 V4")
    assert not outline_local_subdivision._tree_is_current(old_tree)
    outline_local_subdivision.sync(obj, settings=obj.bmanga_line_settings)
    local = outline_local_subdivision.get_modifier(obj)
    assert local is not None and local.node_group.as_pointer() != old_tree_pointer
    assert outline_local_subdivision._tree_is_current(local.node_group)


def _assert_render_camera_switch_syncs_immediately() -> None:
    scene = bpy.context.scene
    obj = bpy.data.objects.get("ライン局所細分化")
    assert obj is not None
    local = outline_local_subdivision.get_modifier(obj)
    assert local is not None
    camera_a = scene.camera
    assert camera_a is not None
    camera_socket = outline_local_subdivision._socket_id(
        local.node_group,
        outline_local_subdivision._CAMERA_SOCKET,
    )
    assert camera_socket is not None and get_gn_modifier_input(local, camera_socket, None) == camera_a

    bpy.ops.object.camera_add(location=(-4.0, -6.0, 3.0))
    camera_b = bpy.context.object
    scene.camera = camera_b
    scene.bmanga_line_camera = None
    camera_comp._on_render_pre(scene)
    assert get_gn_modifier_input(local, camera_socket, None) == camera_b

    scene.bmanga_line_camera = camera_a
    camera_comp._on_render_pre(scene)
    assert get_gn_modifier_input(local, camera_socket, None) == camera_a
    scene.bmanga_line_camera = None
    scene.camera = camera_a


def _assert_user_subsurf_is_read_only() -> None:
    obj = _make_cube("ユーザー細分化保護", (3.0, 0.0, 0.0))
    manual = obj.modifiers.new("ユーザーのサブディビジョンサーフェス", "SUBSURF")
    manual.levels = 1
    manual.render_levels = 3
    manual.show_viewport = False
    manual.show_render = True
    if hasattr(manual, "subdivision_type"):
        manual.subdivision_type = "SIMPLE"
    before = (
        manual.name,
        manual.levels,
        manual.render_levels,
        manual.show_viewport,
        manual.show_render,
        getattr(manual, "subdivision_type", None),
        list(obj.modifiers).index(manual),
    )
    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    subdivision_lod.sync_scene_generated_line_subdivision(bpy.context.scene)
    after = (
        manual.name,
        manual.levels,
        manual.render_levels,
        manual.show_viewport,
        manual.show_render,
        getattr(manual, "subdivision_type", None),
        list(obj.modifiers).index(manual),
    )
    assert after == before, (before, after)

    save_path = ROOT / "_verify" / "2026-07-10_line_local_subdivision" / "user_subsurf_roundtrip.blend"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(save_path))
    bpy.ops.wm.open_mainfile(filepath=str(save_path))
    restored = bpy.data.objects.get("ユーザー細分化保護")
    assert restored is not None
    restored_mod = restored.modifiers.get("ユーザーのサブディビジョンサーフェス")
    assert restored_mod is not None
    restored_state = (
        restored_mod.name,
        restored_mod.levels,
        restored_mod.render_levels,
        restored_mod.show_viewport,
        restored_mod.show_render,
        getattr(restored_mod, "subdivision_type", None),
        list(restored.modifiers).index(restored_mod),
    )
    assert restored_state == before, (before, restored_state)
    restored_local = outline_local_subdivision.get_modifier(restored)
    assert restored_local is not None
    assert any(
        node.bl_idname == "GeometryNodeGeometryToInstance"
        for node in restored_local.node_group.nodes
    )
    assert _evaluated_generated_counts(restored) == (6, 0)


def _assert_legacy_cleanup_is_ownership_safe() -> None:
    owned = _make_cube("旧自動生成_単独", (6.0, 0.0, 0.0))
    old = owned.modifiers.new(subdivision_lod.AUTO_SUBSURF_MODIFIER_NAME, "SUBSURF")
    owned[subdivision_lod.AUTO_SUBSURF_CREASE_EDGES_PROP] = [0]
    assert subdivision_lod.remove_auto_subdivision(owned)
    assert old.name not in owned.modifiers

    ambiguous = _make_cube("旧名重複_保護", (9.0, 0.0, 0.0))
    first = ambiguous.modifiers.new(subdivision_lod.AUTO_SUBSURF_MODIFIER_NAME, "SUBSURF")
    second = ambiguous.modifiers.new(subdivision_lod.AUTO_SUBSURF_MODIFIER_NAME, "SUBSURF")
    ambiguous[subdivision_lod.AUTO_SUBSURF_CREASE_EDGES_PROP] = [0]
    assert not subdivision_lod.remove_auto_subdivision(ambiguous)
    assert first in ambiguous.modifiers[:] and second in ambiguous.modifiers[:]


def _assert_delete_switches_back_without_source_subdivision() -> None:
    obj = bpy.data.objects.get("ライン局所細分化")
    assert obj is not None
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.bmanga_line.update_auto_subdivision(
        "EXEC_DEFAULT", action="DELETE"
    ) == {"FINISHED"}
    assert not obj.bmanga_line_settings.auto_subdivision_for_midpoint
    assert obj.modifiers.get(core.OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME) is None
    state = obj.modifiers.get(core.MODIFIER_NAME)
    assert state is not None and state.show_viewport and state.show_render
    assert not any(mod.type == "SUBSURF" for mod in obj.modifiers)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        assert camera_comp._on_render_pre in bpy.app.handlers.render_pre
        _clear_scene()
        _assert_camera_fallback_switches_safely()
        _clear_scene()
        _assert_source_mesh_unchanged_and_shell_local()
        _assert_render_camera_switch_syncs_immediately()
        _assert_user_subsurf_is_read_only()
        _assert_legacy_cleanup_is_ownership_safe()
        _assert_delete_switches_back_without_source_subdivision()
        print("BMANGA_LINE_LOCAL_SUBDIVISION_INTEGRATION_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
