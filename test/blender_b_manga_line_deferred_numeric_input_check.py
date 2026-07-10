"""B-MANGA Liner: numeric edits stay pending until explicit reflection."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    presets,
    outline_local_subdivision,
    update_state,
)


NUMERIC_CASES = (
    ("outline_thickness_mm", 0.73),
    ("outline_offset", 0.12),
    ("outline_creation_max_distance", 12.0),
    ("inner_line_angle", math.radians(75.0)),
    ("inner_line_thickness_mm", 0.42),
    ("inner_line_offset", 0.11),
    ("inner_line_creation_max_distance", 13.0),
    ("intersection_thickness_mm", 0.44),
    ("intersection_line_offset", 0.13),
    ("intersection_creation_max_distance", 14.0),
    ("selection_line_angle", math.radians(80.0)),
    ("selection_line_thickness_mm", 0.46),
    ("selection_line_offset", 0.14),
    ("selection_line_creation_max_distance", 15.0),
    ("camera_compensation_influence", 0.75),
    ("line_width_reference_distance", 11.0),
    ("line_width_distance_falloff", 0.80),
    ("edge_smooth_factor", 0.20),
    ("edge_midpoint_jitter_percent", 5.0),
    ("edge_midpoint_angle", math.radians(110.0)),
    ("inner_edge_smooth_factor", 0.22),
    ("inner_edge_midpoint_jitter_percent", 6.0),
    ("intersection_edge_smooth_factor", 0.24),
    ("intersection_edge_midpoint_jitter_percent", 7.0),
    ("intersection_edge_midpoint_angle", math.radians(115.0)),
    ("selection_edge_smooth_factor", 0.26),
    ("selection_edge_midpoint_jitter_percent", 8.0),
    ("selection_edge_midpoint_angle", math.radians(120.0)),
    ("culling_margin", math.radians(12.0)),
    ("outline_max_distance", 21.0),
    ("inner_line_max_distance", 22.0),
    ("intersection_max_distance", 23.0),
    ("selection_line_max_distance", 24.0),
    ("bump_line_thickness", 0.45),
    ("bump_line_threshold", 0.80),
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _setup_applied_line() -> bpy.types.Object:
    _clear_scene()
    bpy.ops.object.camera_add(location=(0.0, -6.0, 2.0))
    bpy.context.scene.camera = bpy.context.object
    bpy.ops.mesh.primitive_cube_add(size=1.5)
    obj = bpy.context.object
    obj.name = "BML_deferred_numeric_input"
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
    obj.bmanga_line_settings.use_camera_culling = False
    obj.bmanga_line_settings.weld_mesh_for_outline = False
    assert presets.apply_line_settings(obj, bpy.context)
    assert core.has_line(obj)
    assert outline_local_subdivision.has_active(obj)
    bpy.context.view_layer.update()
    return obj


def _test_numeric_edits_do_not_queue_subdivision_sync(obj: bpy.types.Object) -> None:
    settings = obj.bmanga_line_settings
    before = outline_local_subdivision.local_thickness(obj)
    for prop_name, value in NUMERIC_CASES:
        update_state.clear_pending(obj)
        setattr(settings, prop_name, value)
        assert update_state.pending_targets(obj), f"反映待ちになりません: {prop_name}"
        bpy.context.view_layer.update()
        assert outline_local_subdivision.local_thickness(obj) == before, (
            f"数値入力でライン殻が更新されました: {prop_name}"
        )


def _test_outline_width_changes_only_after_reflect(obj: bpy.types.Object) -> None:
    settings = obj.bmanga_line_settings
    modifier = obj.modifiers.get(core.MODIFIER_NAME)
    assert modifier is not None
    update_state.clear_pending(obj)
    before = float(modifier.thickness)
    settings.outline_thickness_mm = 0.91
    bpy.context.view_layer.update()
    assert math.isclose(float(modifier.thickness), before, abs_tol=1.0e-10)
    assert "outline" in update_state.pending_targets(obj)

    assert bpy.ops.bmanga_line.reflect_target(
        "EXEC_DEFAULT", target="outline",
    ) == {"FINISHED"}
    assert not math.isclose(float(modifier.thickness), before, abs_tol=1.0e-10)
    assert "outline" not in update_state.pending_targets(obj)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        obj = _setup_applied_line()
        _test_numeric_edits_do_not_queue_subdivision_sync(obj)
        _test_outline_width_changes_only_after_reflect(obj)
        print("BMANGA_LINE_DEFERRED_NUMERIC_INPUT_OK")
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
