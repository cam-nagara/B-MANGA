"""B-MANGA Line: every UI checkbox/slider update is scoped to needed work."""

from __future__ import annotations

import math
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
    outline_setup,
    presets,
)


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


def _make_cube(index: int) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.3, location=((index - 1) * 0.45, 0.0, -4.0))
    obj = bpy.context.object
    obj.name = f"BML_control_scope_{index}"
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    settings.use_inner_line_creation_limit = False
    settings.use_intersection_creation_limit = False
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _setup_scene() -> list[bpy.types.Object]:
    _clear_scene()
    bpy.ops.object.camera_add(location=(0.0, -4.0, 1.0), rotation=(1.25, 0.0, 0.0))
    bpy.context.scene.camera = bpy.context.object
    objects = [_make_cube(i) for i in range(3)]
    _select(objects)
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    _select(objects)
    return objects


def _install_counters():
    counts = {
        "line_settings_apply": 0,
        "outline_apply": 0,
        "outline_thickness_update": 0,
        "inner_apply": 0,
        "inner_update": 0,
        "intersection_apply": 0,
        "intersection_update": 0,
        "intersection_refresh": 0,
        "camera": 0,
        "camera_objects": 0,
        "visibility_objects": 0,
        "visibility_rule": 0,
        "view_update": 0,
        "camera_scopes": [],
    }
    originals = {
        "line_settings_apply": presets.apply_line_settings,
        "outline_apply": outline_setup.apply_outline,
        "outline_thickness_update": outline_setup.update_modifier_thickness,
        "inner_apply": inner_lines.apply_inner_lines,
        "inner_update": inner_lines.update_parameters,
        "intersection_apply": intersection_lines.apply_intersection_lines,
        "intersection_update": intersection_lines.update_parameters,
        "intersection_refresh": intersection_lines.refresh_scene_intersections,
        "camera": camera_comp.refresh,
        "camera_objects": camera_comp.refresh_objects,
        "visibility_objects": camera_comp.refresh_visibility_objects,
        "visibility_rule": core._refresh_visibility_rules,
        "view_update": presets._update_view_layer,
    }

    def counted_line_settings_apply(*args, **kwargs):
        counts["line_settings_apply"] += 1
        return originals["line_settings_apply"](*args, **kwargs)

    def counted_outline_apply(*args, **kwargs):
        counts["outline_apply"] += 1
        return originals["outline_apply"](*args, **kwargs)

    def counted_outline_thickness_update(*args, **kwargs):
        counts["outline_thickness_update"] += 1
        return originals["outline_thickness_update"](*args, **kwargs)

    def counted_inner_apply(*args, **kwargs):
        counts["inner_apply"] += 1
        return originals["inner_apply"](*args, **kwargs)

    def counted_inner_update(*args, **kwargs):
        counts["inner_update"] += 1
        return originals["inner_update"](*args, **kwargs)

    def counted_intersection_apply(*args, **kwargs):
        counts["intersection_apply"] += 1
        return originals["intersection_apply"](*args, **kwargs)

    def counted_intersection_update(*args, **kwargs):
        counts["intersection_update"] += 1
        return originals["intersection_update"](*args, **kwargs)

    def counted_intersection_refresh(*args, **kwargs):
        counts["intersection_refresh"] += 1
        return originals["intersection_refresh"](*args, **kwargs)

    def counted_camera(*args, **kwargs):
        counts["camera"] += 1
        return originals["camera"](*args, **kwargs)

    def counted_camera_objects(*args, **kwargs):
        counts["camera_objects"] += 1
        scope = kwargs.get("width_targets")
        counts["camera_scopes"].append(tuple(scope) if scope is not None else ("all",))
        return originals["camera_objects"](*args, **kwargs)

    def counted_visibility_objects(*args, **kwargs):
        counts["visibility_objects"] += 1
        return originals["visibility_objects"](*args, **kwargs)

    def counted_visibility_rule(*args, **kwargs):
        counts["visibility_rule"] += 1
        settings = args[0]
        return originals["visibility_rule"](*args, **kwargs)

    def counted_view_update(*args, **kwargs):
        counts["view_update"] += 1
        return originals["view_update"](*args, **kwargs)

    presets.apply_line_settings = counted_line_settings_apply
    outline_setup.apply_outline = counted_outline_apply
    outline_setup.update_modifier_thickness = counted_outline_thickness_update
    inner_lines.apply_inner_lines = counted_inner_apply
    inner_lines.update_parameters = counted_inner_update
    intersection_lines.apply_intersection_lines = counted_intersection_apply
    intersection_lines.update_parameters = counted_intersection_update
    intersection_lines.refresh_scene_intersections = counted_intersection_refresh
    camera_comp.refresh = counted_camera
    camera_comp.refresh_objects = counted_camera_objects
    camera_comp.refresh_visibility_objects = counted_visibility_objects
    core._refresh_visibility_rules = counted_visibility_rule
    presets._update_view_layer = counted_view_update

    def reset() -> None:
        for key in counts:
            counts[key] = [] if key == "camera_scopes" else 0

    def restore() -> None:
        presets.apply_line_settings = originals["line_settings_apply"]
        outline_setup.apply_outline = originals["outline_apply"]
        outline_setup.update_modifier_thickness = originals["outline_thickness_update"]
        inner_lines.apply_inner_lines = originals["inner_apply"]
        inner_lines.update_parameters = originals["inner_update"]
        intersection_lines.apply_intersection_lines = originals["intersection_apply"]
        intersection_lines.update_parameters = originals["intersection_update"]
        intersection_lines.refresh_scene_intersections = originals["intersection_refresh"]
        camera_comp.refresh = originals["camera"]
        camera_comp.refresh_objects = originals["camera_objects"]
        camera_comp.refresh_visibility_objects = originals["visibility_objects"]
        core._refresh_visibility_rules = originals["visibility_rule"]
        presets._update_view_layer = originals["view_update"]

    return counts, reset, restore


def _assert_common(prop_name: str, counts: dict) -> None:
    assert counts["line_settings_apply"] == 0, (prop_name, counts)
    assert counts["outline_apply"] == 0, (prop_name, counts)
    assert counts["camera"] == 0, (prop_name, counts)


def _assert_no_generated_rebuild(prop_name: str, counts: dict) -> None:
    assert counts["inner_apply"] == 0, (prop_name, counts)
    assert counts["intersection_apply"] == 0, (prop_name, counts)
    assert counts["intersection_refresh"] == 0, (prop_name, counts)


def _assert_width_scope(prop_name: str, counts: dict, target: str | None) -> None:
    if target is None:
        assert counts["camera_objects"] == 0, (prop_name, counts)
        return
    expected = ("all",) if target == "all" else (target,)
    assert counts["camera_objects"] > 0, (prop_name, counts)
    assert all(scope == expected for scope in counts["camera_scopes"]), (prop_name, counts)


def _silent_set(settings, prop_name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(settings, prop_name, value)
    finally:
        core._propagating = old


def _ensure_changed_value(settings, prop_name: str, value) -> None:
    current = getattr(settings, prop_name)
    if not _values_equal(current, value):
        return
    if isinstance(value, bool):
        _silent_set(settings, prop_name, not value)
    elif isinstance(value, (int, float)):
        _silent_set(settings, prop_name, value + 0.01)
    elif isinstance(value, str):
        fallback = "BOOLEAN" if value != "BOOLEAN" else "SDF"
        _silent_set(settings, prop_name, fallback)


def _values_equal(actual, expected) -> bool:
    if isinstance(expected, tuple):
        return all(
            abs(float(a) - float(b)) < 1.0e-6
            for a, b in zip(actual, expected)
        )
    if isinstance(expected, float):
        return abs(float(actual) - expected) < 1.0e-6
    return actual == expected


def _change(settings, prop_name: str, value, counts: dict, reset, *, width_target=None) -> None:
    _ensure_changed_value(settings, prop_name, value)
    assert not core._propagating, prop_name
    assert not _values_equal(getattr(settings, prop_name), value), prop_name
    reset()
    setattr(settings, prop_name, value)
    assert _values_equal(getattr(settings, prop_name), value), prop_name
    _assert_common(prop_name, counts)
    _assert_width_scope(prop_name, counts, width_target)


def _run_baseline_cases(settings, counts, reset) -> None:
    no_rebuild_cases = [
        ("outline_enabled", False, None),
        ("outline_enabled", True, None),
        ("outline_color", (0.15, 0.25, 0.35, 1.0), None),
        ("outline_offset", 0.35, None),
        ("even_thickness", False, None),
        ("use_rim", False, None),
        ("hide_through_transparent", True, None),
        ("exclude_sheet_meshes", True, None),
        ("exclude_sheet_meshes", False, None),
        ("use_vertex_color", True, None),
        ("use_vertex_color", False, None),
        ("use_ao_influence", True, None),
        ("ao_influence_strength", 0.25, None),
        ("use_ao_influence", False, None),
        ("edge_smooth_factor", 0.15, None),
        ("edge_midpoint_jitter_percent", 3.0, None),
        ("edge_midpoint_angle", math.radians(55.0), None),
        ("edge_width_curve_25", 0.20, None),
        ("edge_width_curve_50", 0.45, None),
        ("edge_width_curve_75", 0.80, None),
        ("edge_smooth_factor", 0.0, None),
        ("inner_line_angle", math.radians(70), None),
        ("use_marked_inner_edges", True, None),
        ("use_marked_inner_edges", False, None),
        ("inner_line_offset", 0.25, None),
        ("inner_edge_smooth_factor", 0.12, None),
        ("inner_edge_midpoint_jitter_percent", 2.0, None),
        ("inner_edge_midpoint_angle", math.radians(55.0), None),
        ("inner_edge_width_curve_25", 0.22, None),
        ("inner_edge_width_curve_50", 0.52, None),
        ("inner_edge_width_curve_75", 0.82, None),
        ("inner_edge_smooth_factor", 0.0, None),
        ("use_inner_line_creation_limit", False, None),
        ("use_inner_line_creation_limit", True, None),
        ("inner_line_creation_max_distance", 12.0, None),
        ("intersection_line_offset", -0.25, None),
        ("intersection_edge_smooth_factor", 0.12, None),
        ("intersection_edge_midpoint_jitter_percent", 2.0, None),
        ("intersection_edge_midpoint_angle", math.radians(55.0), None),
        ("intersection_edge_width_curve_25", 0.22, None),
        ("intersection_edge_width_curve_50", 0.52, None),
        ("intersection_edge_width_curve_75", 0.82, None),
        ("intersection_edge_smooth_factor", 0.0, None),
        ("use_intersection_creation_limit", False, None),
        ("use_intersection_creation_limit", True, None),
        ("intersection_creation_max_distance", 12.0, None),
    ]
    for prop_name, value, width_target in no_rebuild_cases:
        _change(settings, prop_name, value, counts, reset, width_target=width_target)
        _assert_no_generated_rebuild(prop_name, counts)

    width_cases = [
        ("outline_thickness", 0.0011, "outline"),
        ("inner_line_thickness", 0.0012, "inner"),
        ("intersection_thickness", 0.0013, "intersection"),
        ("use_uniform_line_width", True, "all"),
        ("use_uniform_line_width", False, "all"),
        ("use_camera_compensation", True, "all"),
        ("camera_compensation_influence", 0.55, "all"),
        ("line_width_reference_distance", 3.0, "all"),
        ("use_camera_compensation", False, "all"),
    ]
    for prop_name, value, width_target in width_cases:
        _change(settings, prop_name, value, counts, reset, width_target=width_target)
        _assert_no_generated_rebuild(prop_name, counts)
        if prop_name in {"outline_thickness", "use_camera_compensation"}:
            assert counts["outline_thickness_update"] == 0, (prop_name, counts)
        if prop_name == "use_camera_compensation":
            assert counts["inner_update"] <= 3, (prop_name, counts)
            assert counts["intersection_update"] <= 3, (prop_name, counts)

    visibility_cases = [
        ("use_camera_culling", True),
        ("culling_margin", math.radians(5)),
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
    ]
    for prop_name, value in visibility_cases:
        _change(settings, prop_name, value, counts, reset, width_target=None)
        _assert_no_generated_rebuild(prop_name, counts)
        assert counts["visibility_rule"] > 0, (prop_name, counts)


def _run_uniform_mode_cases(settings, counts, reset) -> None:
    settings.use_uniform_line_width = True

    no_effect_cases = [
        ("use_camera_compensation", True),
        ("camera_compensation_influence", 0.25),
        ("line_width_reference_distance", 4.0),
        ("use_camera_compensation", False),
    ]
    for prop_name, value in no_effect_cases:
        _change(settings, prop_name, value, counts, reset, width_target=None)
        _assert_no_generated_rebuild(prop_name, counts)

    target_cases = [
        ("outline_thickness", 0.0015, "outline"),
        ("edge_smooth_factor", 0.18, "outline"),
        ("inner_line_thickness", 0.0016, "inner"),
        ("inner_edge_smooth_factor", 0.18, "inner"),
        ("intersection_thickness", 0.0017, "intersection"),
        ("intersection_edge_smooth_factor", 0.18, "intersection"),
    ]
    for prop_name, value, width_target in target_cases:
        _change(settings, prop_name, value, counts, reset, width_target=width_target)
        _assert_no_generated_rebuild(prop_name, counts)


def _run_mixed_uniform_cases(objects, counts, reset) -> None:
    settings = objects[0].bmanga_line_settings
    _silent_set(objects[1].bmanga_line_settings, "use_uniform_line_width", True)
    _silent_set(objects[2].bmanga_line_settings, "use_uniform_line_width", False)
    _select(objects)
    _ensure_changed_value(settings, "inner_line_angle", math.radians(75))
    reset()
    settings.inner_line_angle = math.radians(75)
    _assert_common("inner_line_angle_mixed_uniform", counts)
    _assert_no_generated_rebuild("inner_line_angle_mixed_uniform", counts)
    assert counts["camera_objects"] == 1, counts
    assert counts["camera_scopes"] == [("inner",)], counts
    assert counts["inner_update"] <= 4, counts


def main() -> None:
    b_manga_line.register()
    counts, reset, restore = _install_counters()
    try:
        objects = _setup_scene()
        settings = objects[0].bmanga_line_settings
        _run_baseline_cases(settings, counts, reset)
        objects = _setup_scene()
        settings = objects[0].bmanga_line_settings
        _run_uniform_mode_cases(settings, counts, reset)
        objects = _setup_scene()
        _run_mixed_uniform_cases(objects, counts, reset)
        print("BMANGA_LINE_CONTROL_UPDATE_SCOPE_OK")
    finally:
        restore()
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
