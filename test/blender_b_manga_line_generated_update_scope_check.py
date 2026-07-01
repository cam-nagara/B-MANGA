"""B-MANGA Line: generated line setting updates only touch generated targets."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    intersection_lines,
    presets,
    vertex_analysis,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


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
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    settings.use_inner_line_creation_limit = True
    settings.inner_line_creation_max_distance = 10.0
    settings.use_intersection_creation_limit = True
    settings.intersection_creation_max_distance = 10.0
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

        inner_names = _generated_inner_names(objects)
        intersection_names = _generated_intersection_names(objects)
        assert visible_a.name in inner_names
        assert visible_b.name in inner_names
        assert offscreen_a.name not in inner_names
        assert offscreen_b.name not in inner_names
        assert intersection_names
        assert offscreen_a.name not in intersection_names
        assert offscreen_b.name not in intersection_names

        _select_all(visible_a, objects)

        real_refresh_objects = camera_comp.refresh_objects
        refresh_targets: list[str] = []

        def counted_refresh_objects(context, refresh_objects, **_kwargs):
            refresh_targets.extend(obj.name for obj in refresh_objects)
            return True

        camera_comp.refresh_objects = counted_refresh_objects
        try:
            visible_a.bmanga_line_settings.inner_line_thickness = 0.002
            assert set(refresh_targets) == inner_names, refresh_targets

            refresh_targets.clear()
            visible_a.bmanga_line_settings.intersection_thickness = 0.003
            assert set(refresh_targets) == intersection_names, refresh_targets
        finally:
            camera_comp.refresh_objects = real_refresh_objects

        real_compute_weights = vertex_analysis.compute_and_apply_weights
        weight_targets: list[tuple[str, str]] = []

        def counted_compute_weights(obj, settings, target: str = "outline"):
            weight_targets.append((target, obj.name))
            return 0

        vertex_analysis.compute_and_apply_weights = counted_compute_weights
        try:
            visible_a.bmanga_line_settings.inner_edge_smooth_factor = 0.2
            assert {
                name for target, name in weight_targets if target == "inner"
            } == inner_names, weight_targets

            weight_targets.clear()
            visible_a.bmanga_line_settings.intersection_edge_smooth_factor = 0.2
            assert {
                name for target, name in weight_targets if target == "intersection"
            } == intersection_names, weight_targets
        finally:
            vertex_analysis.compute_and_apply_weights = real_compute_weights

        print("[PASS] generated line updates only touch generated line targets")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
