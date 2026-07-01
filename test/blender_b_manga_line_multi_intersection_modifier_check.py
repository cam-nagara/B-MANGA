"""B-MANGA Line: many intersection targets are grouped into one modifier."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_lines, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.collections,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _make_cube(name: str, location: tuple[float, float, float], size: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.intersection_enabled = True
    settings.intersection_method = "BOOLEAN"
    settings.use_intersection_creation_limit = False
    return obj


def _make_plane(name: str, location: tuple[float, float, float], size: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.intersection_enabled = True
    settings.intersection_method = "BOOLEAN"
    settings.use_intersection_creation_limit = False
    return obj


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, -10.0, 0.0))
    camera = bpy.context.object
    camera.name = "BML_multi_intersection_camera"
    bpy.context.scene.camera = camera
    return camera


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        source = _make_cube("BML_multi_intersection_source", (0.0, 0.0, 0.0), 2.0)
        cube_targets = [
            _make_cube(f"BML_multi_intersection_target_{index}", (0.55, index * 0.2 - 0.3, 0.0), 0.8)
            for index in range(3)
        ]
        sheet_target = _make_plane("BML_multi_intersection_sheet_target", (0.55, 0.35, 0.0), 0.8)
        targets = [*cube_targets, sheet_target]
        bpy.context.view_layer.objects.active = source
        for obj in [source, *targets]:
            assert presets.apply_line_settings(
                obj,
                bpy.context,
                refresh_scene=False,
                transforms_fresh=False,
            )
        old_threshold = intersection_lines._DEFERRED_VIEWPORT_THRESHOLD
        intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = 0
        try:
            refreshed = intersection_lines.refresh_scene_intersections(bpy.context.scene)
            assert source in refreshed, "まとめ交差線の生成元が更新対象に含まれていません"
        finally:
            intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = old_threshold

        mods = list(core.iter_intersection_modifiers(source))
        assert len(mods) == 1, [mod.name for mod in mods]
        mod = mods[0]
        assert mod.name.startswith(core.INTERSECTION_MODIFIER_PREFIX + "Targets")
        assert mod.node_group is not None
        assert "_Multi_" in mod.node_group.name
        assert not mod.show_viewport
        assert intersection_lines.is_deferred_viewport_modifier(mod)
        actual_targets = {
            item.name
            for item in intersection_lines._multi_modifier_targets(mod)
        }
        assert actual_targets == {item.name for item in targets}, actual_targets
        for _ in range(20):
            if not intersection_lines.is_deferred_viewport_modifier(mod):
                break
            intersection_lines._restore_deferred_viewport_step()
        assert mod.show_viewport
        assert not intersection_lines.is_deferred_viewport_modifier(mod)

        _make_camera()
        source.bmanga_line_settings.use_intersection_distance_limit = True
        source.bmanga_line_settings.intersection_max_distance = 0.5
        old_threshold = intersection_lines._DEFERRED_VIEWPORT_THRESHOLD
        intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = 0
        try:
            intersection_lines.refresh_scene_intersections(bpy.context.scene)
        finally:
            intersection_lines._DEFERRED_VIEWPORT_THRESHOLD = old_threshold
        deferred_mods = list(core.iter_intersection_modifiers(source))
        assert deferred_mods
        assert all(intersection_lines.is_deferred_viewport_modifier(item) for item in deferred_mods)
        for _ in range(20):
            if not any(intersection_lines.is_deferred_viewport_modifier(item) for item in deferred_mods):
                break
            intersection_lines._restore_deferred_viewport_step()
        assert all(not item.show_viewport for item in deferred_mods)
        assert all(not item.show_render for item in deferred_mods)
        source.bmanga_line_settings.use_intersection_distance_limit = False
        source.bmanga_line_settings.intersection_max_distance = 20.0

        sheet_target.bmanga_line_settings.exclude_sheet_meshes = True
        intersection_lines.prune_excluded_intersections(bpy.context.scene)
        remaining_targets = set()
        for current in core.iter_intersection_modifiers(source):
            if intersection_lines._is_grouped_modifier(current):
                remaining_targets.update(
                    item.name
                    for item in intersection_lines._multi_modifier_targets(current)
                )
            else:
                target = intersection_lines._modifier_target(current)
                if target is not None:
                    remaining_targets.add(target.name)
        assert remaining_targets == {item.name for item in cube_targets}, remaining_targets

        bpy.context.view_layer.update()
        print("[PASS] multi intersection targets are grouped")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
