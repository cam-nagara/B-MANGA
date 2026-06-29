"""B-MANGA Line: selected batch apply refreshes scene-level work once."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, intersection_lines, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(index: int) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(index * 1.25, 0.0, -4.0))
    obj = bpy.context.object
    obj.name = f"BML_batch_apply_{index:02d}"
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    return obj


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0))
        bpy.context.scene.camera = bpy.context.object

        objects = [_make_cube(i) for i in range(8)]
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]

        counts = {"intersection": 0, "camera": 0, "view_update": 0}
        real_intersection = intersection_lines.refresh_scene_intersections
        real_camera = camera_comp.refresh
        real_view_update = presets._update_view_layer

        def counted_intersection(scene):
            counts["intersection"] += 1
            return real_intersection(scene)

        def counted_camera(context):
            counts["camera"] += 1
            return real_camera(context)

        def counted_view_update(context):
            counts["view_update"] += 1
            return real_view_update(context)

        intersection_lines.refresh_scene_intersections = counted_intersection
        camera_comp.refresh = counted_camera
        presets._update_view_layer = counted_view_update
        try:
            assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
        finally:
            intersection_lines.refresh_scene_intersections = real_intersection
            camera_comp.refresh = real_camera
            presets._update_view_layer = real_view_update

        assert counts["intersection"] == 1, counts
        assert counts["camera"] == 1, counts
        assert counts["view_update"] == 1, counts
        print(f"[PASS] batch apply refresh count: {counts}")

        refresh_counts = {"apply": 0, "intersection": 0, "camera": 0}
        real_apply = presets.apply_line_settings
        real_intersection = intersection_lines.refresh_scene_intersections
        real_camera = camera_comp.refresh

        def counted_apply(obj, context, **kwargs):
            refresh_counts["apply"] += 1
            return real_apply(obj, context, **kwargs)

        def counted_intersection(scene):
            refresh_counts["intersection"] += 1
            return real_intersection(scene)

        def counted_camera(context):
            refresh_counts["camera"] += 1
            return real_camera(context)

        presets.apply_line_settings = counted_apply
        intersection_lines.refresh_scene_intersections = counted_intersection
        camera_comp.refresh = counted_camera
        try:
            objects[0].bmanga_line_settings.use_uniform_line_width = True
            assert all(obj.bmanga_line_settings.use_uniform_line_width for obj in objects)
            assert refresh_counts["apply"] == 0, refresh_counts
            assert refresh_counts["intersection"] == 0, refresh_counts
            assert refresh_counts["camera"] <= 2, refresh_counts

            refresh_counts.update({"apply": 0, "intersection": 0, "camera": 0})
            objects[0].bmanga_line_settings.use_uniform_line_width = False
            assert not any(obj.bmanga_line_settings.use_uniform_line_width for obj in objects)
            assert refresh_counts["apply"] == 0, refresh_counts
            assert refresh_counts["intersection"] == 0, refresh_counts
            assert refresh_counts["camera"] <= 2, refresh_counts
        finally:
            presets.apply_line_settings = real_apply
            intersection_lines.refresh_scene_intersections = real_intersection
            camera_comp.refresh = real_camera
        print(f"[PASS] uniform width toggle refresh count: {refresh_counts}")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
