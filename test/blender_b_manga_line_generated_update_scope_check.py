"""B-MANGA Line: generated line targets update only when explicitly requested."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_lines, presets, update_state  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _set_without_update(settings, prop_name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(settings, prop_name, value)
    finally:
        core._propagating = old


def _make_camera() -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "PERSP"
    camera.data.lens = 50.0
    scene.camera = camera
    return camera


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    _set_without_update(settings, "inner_line_enabled", True)
    _set_without_update(settings, "intersection_enabled", True)
    _set_without_update(settings, "use_inner_line_creation_limit", True)
    _set_without_update(settings, "inner_line_creation_max_distance", 10.0)
    _set_without_update(settings, "use_intersection_creation_limit", True)
    _set_without_update(settings, "intersection_creation_max_distance", 10.0)
    return obj


def _select_all(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _generated_inner_names(objects: list[bpy.types.Object]) -> set[str]:
    return {
        obj.name
        for obj in objects
        if obj.modifiers.get(core.GN_MODIFIER_NAME) is not None
    }


def _generated_intersection_names(objects: list[bpy.types.Object]) -> set[str]:
    return {
        obj.name
        for obj in objects
        if any(core.iter_intersection_modifiers(obj))
    }


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        _make_camera()

        visible_a = _make_cube("BML_scope_visible_A", (0.0, 0.0, -5.0))
        visible_b = _make_cube("BML_scope_visible_B", (0.35, 0.0, -5.0))
        offscreen_a = _make_cube("BML_scope_offscreen_A", (5.0, 0.0, -5.0))
        offscreen_b = _make_cube("BML_scope_offscreen_B", (5.35, 0.0, -5.0))
        objects = [visible_a, visible_b, offscreen_a, offscreen_b]

        presets._update_view_layer(bpy.context)
        for obj in objects:
            assert presets.apply_line_settings(
                obj,
                bpy.context,
                refresh_scene=False,
                transforms_fresh=True,
            ), obj.name
        intersection_lines.refresh_scene_intersections(bpy.context.scene)

        initial_inner_names = _generated_inner_names(objects)
        initial_intersection_names = _generated_intersection_names(objects)
        assert visible_a.name in initial_inner_names
        assert visible_b.name in initial_inner_names
        assert offscreen_a.name not in initial_inner_names
        assert offscreen_b.name not in initial_inner_names
        assert initial_intersection_names
        assert offscreen_a.name not in initial_intersection_names
        assert offscreen_b.name not in initial_intersection_names

        _select_all(visible_a, objects)

        visible_a.bmanga_line_settings.inner_line_thickness = 0.002
        assert _generated_inner_names(objects) == initial_inner_names
        for obj in objects:
            assert "inner" in set(update_state.pending_targets(obj)), obj.name

        assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}
        assert _generated_inner_names(objects) == initial_inner_names
        for obj in objects:
            assert "inner" not in set(update_state.pending_targets(obj)), obj.name

        visible_a.bmanga_line_settings.intersection_thickness = 0.003
        assert _generated_intersection_names(objects) == initial_intersection_names
        for obj in objects:
            assert "intersection" in set(update_state.pending_targets(obj)), obj.name

        assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}
        assert _generated_intersection_names(objects) == initial_intersection_names
        for obj in objects:
            assert "intersection" not in set(update_state.pending_targets(obj)), obj.name

        print("[PASS] generated line targets update only on explicit request")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
