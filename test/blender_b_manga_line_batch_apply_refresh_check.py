"""B-MANGA Line: selected batch apply refreshes scene-level work once."""

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
    inner_lines,
    intersection_lines,
    presets,
    update_state,
    vertex_analysis,
)


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

        def counted_intersection(scene, **kwargs):
            counts["intersection"] += 1
            return real_intersection(scene, **kwargs)

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
            assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {"FINISHED"}
        finally:
            intersection_lines.refresh_scene_intersections = real_intersection
            camera_comp.refresh = real_camera
            presets._update_view_layer = real_view_update

        assert counts["intersection"] == 1, counts
        assert counts["camera"] == 1, counts
        assert counts["view_update"] == 2, counts
        print(f"[PASS] batch apply refresh count: {counts}")

        assert objects[0].bmanga_line_settings.use_uniform_line_width is True
        assert abs(objects[0].bmanga_line_settings.line_width_distance_falloff - 1.0) < 1.0e-7
        objects[0].bmanga_line_settings.use_uniform_line_width = False

        refresh_counts = {
            "apply": 0,
            "inner_apply": 0,
            "intersection": 0,
            "camera": 0,
            "camera_objects": 0,
        }
        real_apply = presets.apply_line_settings
        real_inner_apply = inner_lines.apply_inner_lines
        real_intersection = intersection_lines.refresh_scene_intersections
        real_camera = camera_comp.refresh
        real_camera_objects = camera_comp.refresh_objects
        real_reset_weights = vertex_analysis.reset_width_weights

        def counted_apply(obj, context, **kwargs):
            refresh_counts["apply"] += 1
            return real_apply(obj, context, **kwargs)

        def counted_inner_apply(obj, *args, **kwargs):
            refresh_counts["inner_apply"] += 1
            return real_inner_apply(obj, *args, **kwargs)

        def counted_intersection(scene, **kwargs):
            refresh_counts["intersection"] += 1
            return real_intersection(scene, **kwargs)

        def counted_camera(context):
            refresh_counts["camera"] += 1
            return real_camera(context)

        def counted_camera_objects(context, refresh_objects, **kwargs):
            refresh_counts["camera_objects"] += 1
            return real_camera_objects(context, refresh_objects, **kwargs)

        def forbidden_reset_weights(*args, **kwargs):
            raise AssertionError("線幅の均一化オフ時に全頂点の書き戻しが走っています")

        def reset_refresh_counts():
            refresh_counts.update({
                "apply": 0,
                "inner_apply": 0,
                "intersection": 0,
                "camera": 0,
                "camera_objects": 0,
            })

        presets.apply_line_settings = counted_apply
        inner_lines.apply_inner_lines = counted_inner_apply
        intersection_lines.refresh_scene_intersections = counted_intersection
        camera_comp.refresh = counted_camera
        camera_comp.refresh_objects = counted_camera_objects
        vertex_analysis.reset_width_weights = forbidden_reset_weights
        try:
            update_state.clear_pending_many(objects)
            objects[0].bmanga_line_settings.use_uniform_line_width = True
            assert all(obj.bmanga_line_settings.use_uniform_line_width for obj in objects)
            assert refresh_counts["apply"] == 0, refresh_counts
            assert refresh_counts["inner_apply"] == 0, refresh_counts
            assert refresh_counts["intersection"] == 0, refresh_counts
            assert refresh_counts["camera"] == 0, refresh_counts
            assert refresh_counts["camera_objects"] == 0, refresh_counts
            assert all("outline" in update_state.pending_targets(obj) for obj in objects)

            reset_refresh_counts()
            update_state.clear_pending_many(objects)
            objects[0].bmanga_line_settings.use_uniform_line_width = False
            assert not any(obj.bmanga_line_settings.use_uniform_line_width for obj in objects)
            assert refresh_counts["apply"] == 0, refresh_counts
            assert refresh_counts["inner_apply"] == 0, refresh_counts
            assert refresh_counts["intersection"] == 0, refresh_counts
            assert refresh_counts["camera"] == 0, refresh_counts
            assert refresh_counts["camera_objects"] == 0, refresh_counts
            assert all("outline" in update_state.pending_targets(obj) for obj in objects)

            settings = objects[0].bmanga_line_settings
            setting_changes = [
                ("outline_enabled", False),
                ("outline_enabled", True),
                ("outline_color", (0.1, 0.2, 0.3, 1.0)),
                ("outline_thickness", 0.0012),
                ("outline_offset", 0.35),
                ("even_thickness", False),
                ("use_rim", False),
                ("hide_through_transparent", True),
                ("exclude_sheet_meshes", False),
                ("exclude_sheet_meshes", True),
                ("use_vertex_color", True),
                ("use_vertex_color", False),
                ("edge_smooth_factor", 0.15),
                ("edge_midpoint_jitter_percent", 3.0),
                ("edge_midpoint_angle", 0.9),
                ("edge_width_curve_25", 0.2),
                ("edge_width_curve_50", 0.45),
                ("edge_width_curve_75", 0.8),
                ("edge_smooth_factor", 0.0),
                ("use_outline_creation_limit", True),
                ("outline_creation_max_distance", 12.0),
                ("use_outline_creation_limit", False),
                ("inner_line_angle", 0.7),
                ("inner_line_thickness", 0.0011),
                ("inner_line_offset", 0.25),
                ("inner_edge_smooth_factor", 0.12),
                ("inner_edge_midpoint_jitter_percent", 2.0),
                ("inner_edge_width_curve_25", 0.22),
                ("inner_edge_width_curve_50", 0.52),
                ("inner_edge_width_curve_75", 0.82),
                ("inner_edge_smooth_factor", 0.0),
                ("use_inner_line_creation_limit", False),
                ("use_inner_line_creation_limit", True),
                ("inner_line_creation_max_distance", 12.0),
                ("inner_line_enabled", False),
                ("inner_line_enabled", True),
                ("intersection_thickness", 0.0013),
                ("intersection_edge_smooth_factor", 0.12),
                ("intersection_edge_midpoint_jitter_percent", 2.0),
                ("intersection_edge_midpoint_angle", 0.9),
                ("intersection_edge_width_curve_25", 0.22),
                ("intersection_edge_width_curve_50", 0.52),
                ("intersection_edge_width_curve_75", 0.82),
                ("intersection_edge_smooth_factor", 0.0),
                ("use_intersection_creation_limit", False),
                ("use_intersection_creation_limit", True),
                ("intersection_creation_max_distance", 12.0),
                ("intersection_enabled", False),
                ("intersection_enabled", True),
                ("use_camera_compensation", True),
                ("intersection_line_offset", -0.25),
                ("camera_compensation_influence", 0.6),
                ("line_width_reference_distance", 3.0),
                ("use_camera_compensation", False),
                ("use_camera_culling", False),
                ("use_camera_culling", True),
                ("culling_margin", 0.05),
                ("use_camera_culling", False),
                ("use_outline_distance_limit", True),
                ("outline_max_distance", 18.0),
                ("use_outline_distance_limit", False),
                ("use_inner_line_distance_limit", True),
                ("inner_line_max_distance", 18.0),
                ("use_inner_line_distance_limit", False),
                ("use_intersection_distance_limit", True),
                ("intersection_max_distance", 18.0),
                ("use_intersection_distance_limit", False),
                ("selection_line_angle", 0.7),
                ("selection_line_thickness", 0.0014),
                ("selection_line_offset", 0.25),
                ("selection_line_color", (1.0, 0.0, 1.0, 1.0)),
                ("use_selection_line_creation_limit", False),
                ("use_selection_line_creation_limit", True),
                ("selection_line_creation_max_distance", 12.0),
                ("selection_edge_smooth_factor", 0.12),
                ("selection_edge_midpoint_jitter_percent", 2.0),
                ("selection_edge_midpoint_angle", 0.9),
                ("selection_edge_width_curve_25", 0.22),
                ("selection_edge_width_curve_50", 0.52),
                ("selection_edge_width_curve_75", 0.82),
                ("selection_edge_smooth_factor", 0.0),
                ("use_selection_line_distance_limit", True),
                ("selection_line_max_distance", 18.0),
                ("use_selection_line_distance_limit", False),
            ]
            for prop_name, value in setting_changes:
                reset_refresh_counts()
                setattr(settings, prop_name, value)
                assert refresh_counts["apply"] == 0, (prop_name, refresh_counts)
                assert refresh_counts["inner_apply"] == 0, (prop_name, refresh_counts)
                assert refresh_counts["camera"] == 0, (prop_name, refresh_counts)
                assert refresh_counts["intersection"] == 0, (prop_name, refresh_counts)
                assert refresh_counts["camera_objects"] == 0, (prop_name, refresh_counts)
        finally:
            presets.apply_line_settings = real_apply
            inner_lines.apply_inner_lines = real_inner_apply
            intersection_lines.refresh_scene_intersections = real_intersection
            camera_comp.refresh = real_camera
            camera_comp.refresh_objects = real_camera_objects
            vertex_analysis.reset_width_weights = real_reset_weights
        print(f"[PASS] uniform width toggle refresh count: {refresh_counts}")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
