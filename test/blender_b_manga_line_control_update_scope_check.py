"""B-MANGA Line: setting edits defer heavy work until explicit updates."""

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
    outline_fast_update,
    outline_setup,
    presets,
    selection_lines,
    update_state,
    vertex_analysis,
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


def _set_without_update(settings, prop_name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(settings, prop_name, value)
    finally:
        core._propagating = old


def _make_cube(index: int) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.2, location=((index - 1) * 0.45, 0.0, -4.0))
    obj = bpy.context.object
    obj.name = f"BML_manual_update_scope_{index}"
    settings = obj.bmanga_line_settings
    _set_without_update(settings, "inner_line_enabled", True)
    _set_without_update(settings, "intersection_enabled", True)
    _set_without_update(settings, "selection_line_enabled", True)
    _set_without_update(settings, "use_inner_line_creation_limit", False)
    _set_without_update(settings, "use_intersection_creation_limit", False)
    _set_without_update(settings, "use_selection_line_creation_limit", False)
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
        "outline_fast_update": 0,
        "inner_apply": 0,
        "intersection_apply": 0,
        "intersection_width_refs": 0,
        "selection_apply": 0,
        "intersection_refresh": 0,
        "camera": 0,
        "camera_objects": 0,
        "weights": 0,
        "view_update": 0,
        "camera_scopes": [],
    }
    originals = {
        "line_settings_apply": presets.apply_line_settings,
        "outline_apply": outline_setup.apply_outline,
        "outline_fast_update": outline_fast_update.update_existing_outline,
        "inner_apply": inner_lines.apply_inner_lines,
        "intersection_apply": intersection_lines.apply_intersection_lines,
        "intersection_width_refs": intersection_lines.update_target_width_references,
        "selection_apply": selection_lines.apply_selection_lines,
        "intersection_refresh": intersection_lines.refresh_scene_intersections,
        "camera": camera_comp.refresh,
        "camera_objects": camera_comp.refresh_objects,
        "weights": vertex_analysis.compute_and_apply_weights,
        "view_update": presets._update_view_layer,
    }

    def counted_line_settings_apply(*args, **kwargs):
        counts["line_settings_apply"] += 1
        return originals["line_settings_apply"](*args, **kwargs)

    def counted_outline_apply(*args, **kwargs):
        counts["outline_apply"] += 1
        return originals["outline_apply"](*args, **kwargs)

    def counted_outline_fast_update(*args, **kwargs):
        counts["outline_fast_update"] += 1
        return originals["outline_fast_update"](*args, **kwargs)

    def counted_inner_apply(*args, **kwargs):
        counts["inner_apply"] += 1
        return originals["inner_apply"](*args, **kwargs)

    def counted_intersection_apply(*args, **kwargs):
        counts["intersection_apply"] += 1
        return originals["intersection_apply"](*args, **kwargs)

    def counted_intersection_width_refs(*args, **kwargs):
        counts["intersection_width_refs"] += 1
        return originals["intersection_width_refs"](*args, **kwargs)

    def counted_selection_apply(*args, **kwargs):
        counts["selection_apply"] += 1
        return originals["selection_apply"](*args, **kwargs)

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

    def counted_weights(*args, **kwargs):
        counts["weights"] += 1
        return originals["weights"](*args, **kwargs)

    def counted_view_update(*args, **kwargs):
        counts["view_update"] += 1
        return originals["view_update"](*args, **kwargs)

    presets.apply_line_settings = counted_line_settings_apply
    outline_setup.apply_outline = counted_outline_apply
    outline_fast_update.update_existing_outline = counted_outline_fast_update
    inner_lines.apply_inner_lines = counted_inner_apply
    intersection_lines.apply_intersection_lines = counted_intersection_apply
    intersection_lines.update_target_width_references = counted_intersection_width_refs
    selection_lines.apply_selection_lines = counted_selection_apply
    intersection_lines.refresh_scene_intersections = counted_intersection_refresh
    camera_comp.refresh = counted_camera
    camera_comp.refresh_objects = counted_camera_objects
    vertex_analysis.compute_and_apply_weights = counted_weights
    presets._update_view_layer = counted_view_update

    def reset() -> None:
        for key in counts:
            counts[key] = [] if key == "camera_scopes" else 0

    def restore() -> None:
        presets.apply_line_settings = originals["line_settings_apply"]
        outline_setup.apply_outline = originals["outline_apply"]
        outline_fast_update.update_existing_outline = originals["outline_fast_update"]
        inner_lines.apply_inner_lines = originals["inner_apply"]
        intersection_lines.apply_intersection_lines = originals["intersection_apply"]
        intersection_lines.update_target_width_references = originals["intersection_width_refs"]
        selection_lines.apply_selection_lines = originals["selection_apply"]
        intersection_lines.refresh_scene_intersections = originals["intersection_refresh"]
        camera_comp.refresh = originals["camera"]
        camera_comp.refresh_objects = originals["camera_objects"]
        vertex_analysis.compute_and_apply_weights = originals["weights"]
        presets._update_view_layer = originals["view_update"]

    return counts, reset, restore


def _assert_no_heavy_work(prop_name: str, counts: dict) -> None:
    heavy_keys = (
        "line_settings_apply",
        "outline_apply",
        "outline_fast_update",
        "inner_apply",
        "intersection_apply",
        "intersection_width_refs",
        "selection_apply",
        "intersection_refresh",
        "camera",
        "camera_objects",
        "weights",
        "view_update",
    )
    assert all(counts[key] == 0 for key in heavy_keys), (prop_name, counts)


def _assert_pending(objects: list[bpy.types.Object], targets: set[str]) -> None:
    for obj in objects:
        assert targets.issubset(set(update_state.pending_targets(obj))), (
            obj.name,
            targets,
            update_state.pending_targets(obj),
        )


def _values_equal(actual, expected) -> bool:
    if isinstance(expected, tuple):
        return all(
            math.isclose(float(a), float(b), abs_tol=1.0e-8)
            for a, b in zip(actual, expected)
        )
    if isinstance(expected, float):
        return math.isclose(float(actual), expected, abs_tol=1.0e-8)
    return actual == expected


def _change_setting(
    objects: list[bpy.types.Object],
    prop_name: str,
    value,
    expected_targets: set[str],
    counts: dict,
    reset,
) -> None:
    settings = objects[0].bmanga_line_settings
    reset()
    setattr(settings, prop_name, value)
    _assert_no_heavy_work(prop_name, counts)
    _assert_pending(objects, expected_targets)
    for obj in objects[1:]:
        other_value = getattr(obj.bmanga_line_settings, prop_name)
        assert _values_equal(other_value, value), (
            obj.name,
            prop_name,
            other_value,
            value,
        )


def _test_setting_edits_are_deferred(objects, counts, reset) -> None:
    for obj in objects:
        update_state.clear_pending(obj)

    cases = (
        ("outline_thickness", 0.0011, {"outline"}),
        ("outline_color", (0.15, 0.25, 0.35, 1.0), {"outline"}),
        ("outline_enabled", False, {"outline"}),
        ("outline_enabled", True, {"outline"}),
        ("inner_line_enabled", False, {"inner"}),
        ("inner_line_enabled", True, {"inner"}),
        ("inner_line_thickness", 0.0012, {"inner"}),
        ("inner_edge_smooth_factor", 0.12, {"inner"}),
        ("intersection_thickness", 0.0013, {"intersection"}),
        ("intersection_enabled", False, {"intersection"}),
        ("intersection_enabled", True, {"intersection"}),
        ("selection_line_enabled", False, {"selection"}),
        ("selection_line_enabled", True, {"selection"}),
        ("selection_line_thickness", 0.0014, {"selection"}),
        ("use_camera_compensation", True, set(update_state.LINE_TARGETS)),
        ("match_subsurf_viewport_to_render", True, set(update_state.LINE_TARGETS)),
        ("use_camera_culling", False, set(update_state.LINE_TARGETS)),
    )
    for prop_name, value, targets in cases:
        _change_setting(objects, prop_name, value, targets, counts, reset)


def _test_target_update_clears_only_target(objects, counts, reset) -> None:
    for obj in objects:
        update_state.mark_pending(obj)

    def _intersection_visibility_state() -> tuple[tuple[str, bool, bool], ...]:
        state = []
        for obj in objects:
            for mod in core.iter_intersection_modifiers(obj):
                state.append((obj.name, bool(mod.show_viewport), bool(mod.show_render)))
        return tuple(state)

    objects[0].bmanga_line_settings.intersection_enabled = False
    before_intersection_visibility = _intersection_visibility_state()
    assert before_intersection_visibility

    reset()
    assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}
    assert counts["line_settings_apply"] == len(objects), counts
    assert counts["outline_apply"] + counts["outline_fast_update"] > 0, counts
    assert counts["inner_apply"] == 0, counts
    assert counts["intersection_apply"] == 0, counts
    assert counts["selection_apply"] == 0, counts
    assert counts["intersection_refresh"] == 0, counts
    assert counts["intersection_width_refs"] == 0, counts
    assert ("outline",) in counts["camera_scopes"], counts
    assert _intersection_visibility_state() == before_intersection_visibility
    for obj in objects:
        pending = set(update_state.pending_targets(obj))
        assert "outline" not in pending, (obj.name, pending)
        assert {"inner", "intersection", "selection"}.issubset(pending), (
            obj.name,
            pending,
        )
    objects[0].bmanga_line_settings.intersection_enabled = True

    for obj in objects:
        update_state.mark_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="inner") == {"FINISHED"}
    assert counts["line_settings_apply"] == len(objects), counts
    assert counts["inner_apply"] > 0, counts
    assert counts["outline_apply"] == 0, counts
    assert counts["outline_fast_update"] == 0, counts
    assert counts["intersection_refresh"] == 0, counts
    assert counts["intersection_width_refs"] == 0, counts
    assert ("inner",) in counts["camera_scopes"], counts
    for obj in objects:
        pending = set(update_state.pending_targets(obj))
        assert "inner" not in pending, (obj.name, pending)
        assert {"outline", "intersection", "selection"}.issubset(pending), (
            obj.name,
            pending,
        )

    for obj in objects:
        update_state.mark_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="intersection") == {"FINISHED"}
    assert counts["line_settings_apply"] == len(objects), counts
    assert counts["intersection_refresh"] > 0, counts
    for obj in objects:
        assert "intersection" not in set(update_state.pending_targets(obj)), obj.name


def _test_full_apply_clears_pending(objects, counts, reset) -> None:
    for obj in objects:
        update_state.mark_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    assert counts["line_settings_apply"] == len(objects), counts
    for obj in objects:
        assert update_state.pending_targets(obj) == (), obj.name


def _test_preset_apply_is_settings_only(objects, counts, reset) -> None:
    scene = bpy.context.scene
    scene.bmanga_line_presets.clear()
    preset = scene.bmanga_line_presets.add()
    preset.name = "manual update preset"
    presets.copy_settings_to_preset(objects[0].bmanga_line_settings, preset)
    preset.outline_thickness = 0.004
    preset.inner_line_thickness = 0.005
    scene.bmanga_line_preset_index = 0
    presets._loaded_scene_pointers.add(scene.as_pointer())

    for obj in objects:
        update_state.clear_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
    _assert_no_heavy_work("preset_apply_selected", counts)
    _assert_pending(objects, set(update_state.LINE_TARGETS))
    for obj in objects:
        settings = obj.bmanga_line_settings
        assert math.isclose(settings.outline_thickness, 0.004, abs_tol=1.0e-8)
        assert math.isclose(settings.inner_line_thickness, 0.005, abs_tol=1.0e-8)


def main() -> None:
    b_manga_line.register()
    counts, reset, restore = _install_counters()
    try:
        objects = _setup_scene()
        _test_setting_edits_are_deferred(objects, counts, reset)
        _test_target_update_clears_only_target(objects, counts, reset)
        _test_full_apply_clears_pending(objects, counts, reset)
        _test_preset_apply_is_settings_only(objects, counts, reset)
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
