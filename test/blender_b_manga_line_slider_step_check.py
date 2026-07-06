"""B-MANGA Line: slider-like numeric controls accept left/right nudges."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core  # noqa: E402


ANGLE_PROPS = (
    "inner_line_angle",
    "edge_midpoint_angle",
    "intersection_edge_midpoint_angle",
    "selection_line_angle",
    "selection_edge_midpoint_angle",
    "culling_margin",
)

NUDGE_PROPS = (
    ("outline_thickness_mm", 0.1),
    ("outline_offset", -0.1),
    ("inner_line_angle", math.radians(1.0)),
    ("inner_line_thickness_mm", 0.1),
    ("inner_line_offset", -0.1),
    ("inner_line_creation_max_distance", 1.0),
    ("intersection_thickness_mm", 0.1),
    ("intersection_line_offset", -0.1),
    ("intersection_creation_max_distance", 1.0),
    ("selection_line_angle", math.radians(1.0)),
    ("selection_line_thickness_mm", 0.1),
    ("selection_line_offset", -0.1),
    ("selection_line_creation_max_distance", 1.0),
    ("camera_compensation_influence", -0.1),
    ("line_width_reference_distance", 0.5),
    ("edge_smooth_factor", 0.1),
    ("edge_midpoint_jitter_percent", 1.0),
    ("edge_midpoint_angle", math.radians(1.0)),
    ("edge_width_curve_25", 0.1),
    ("edge_width_curve_50", 0.1),
    ("edge_width_curve_75", -0.1),
    ("inner_edge_smooth_factor", 0.1),
    ("inner_edge_midpoint_jitter_percent", 1.0),
    ("inner_edge_width_curve_25", 0.1),
    ("inner_edge_width_curve_50", 0.1),
    ("inner_edge_width_curve_75", -0.1),
    ("intersection_edge_smooth_factor", 0.1),
    ("intersection_edge_midpoint_jitter_percent", 1.0),
    ("intersection_edge_midpoint_angle", math.radians(1.0)),
    ("intersection_edge_width_curve_25", 0.1),
    ("intersection_edge_width_curve_50", 0.1),
    ("intersection_edge_width_curve_75", -0.1),
    ("selection_edge_smooth_factor", 0.1),
    ("selection_edge_midpoint_jitter_percent", 1.0),
    ("selection_edge_midpoint_angle", math.radians(1.0)),
    ("selection_edge_width_curve_25", 0.1),
    ("selection_edge_width_curve_50", 0.1),
    ("selection_edge_width_curve_75", -0.1),
    ("culling_margin", math.radians(1.0)),
    ("outline_max_distance", 1.0),
    ("inner_line_max_distance", 1.0),
    ("intersection_max_distance", 1.0),
    ("selection_line_max_distance", 1.0),
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _assert_close(actual: float, expected: float, label: str) -> None:
    if abs(float(actual) - float(expected)) > 1.0e-6:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _assert_angle_steps_are_visible() -> None:
    props = core.BMangaLineSettings.bl_rna.properties
    for prop_name in ANGLE_PROPS:
        prop = props[prop_name]
        assert getattr(prop, "subtype", None) == "ANGLE", prop_name
        assert getattr(prop, "step", 0.0) >= 100.0, (
            f"{prop_name} の左右ボタン増減幅が小さすぎます: {prop.step}"
        )


def _assert_numeric_nudges(settings) -> None:
    for prop_name, delta in NUDGE_PROPS:
        before = float(getattr(settings, prop_name))
        setattr(settings, prop_name, before + delta)
        _assert_close(getattr(settings, prop_name), before + delta, prop_name)
        setattr(settings, prop_name, before)
        _assert_close(getattr(settings, prop_name), before, prop_name)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        obj = bpy.context.object
        settings = obj.bmanga_line_settings
        _assert_angle_steps_are_visible()
        _assert_numeric_nudges(settings)
        print("BMANGA_LINE_SLIDER_STEP_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
