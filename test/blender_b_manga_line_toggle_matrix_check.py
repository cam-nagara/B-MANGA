"""B-MANGA Line: all outline/inner/intersection toggle transitions are scoped."""

from __future__ import annotations

import itertools
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


PROPS = ("outline_enabled", "inner_line_enabled", "intersection_enabled")
LABELS = ("アウトライン", "内部線", "交差線")


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
    bpy.ops.mesh.primitive_cube_add(size=1.4, location=((index - 1) * 0.45, 0.0, -4.0))
    obj = bpy.context.object
    obj.name = f"BML_toggle_matrix_{index}"
    settings = obj.bmanga_line_settings
    settings.use_inner_line_creation_limit = False
    settings.use_intersection_creation_limit = False
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _set_state(objects: list[bpy.types.Object], state: tuple[bool, bool, bool]) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for obj in objects:
            settings = obj.bmanga_line_settings
            for prop, value in zip(PROPS, state):
                setattr(settings, prop, value)
    finally:
        core._propagating = old


def _setup_state(state: tuple[bool, bool, bool]) -> list[bpy.types.Object]:
    _clear_scene()
    bpy.ops.object.camera_add(location=(0.0, -4.0, 1.0), rotation=(1.25, 0.0, 0.0))
    bpy.context.scene.camera = bpy.context.object
    objects = [_make_cube(i) for i in range(3)]
    _set_state(objects, state)
    _select(objects)
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    _select(objects)
    return objects


def _install_counters():
    counts = {
        "line_settings_apply": 0,
        "outline_apply": 0,
        "inner_apply": 0,
        "intersection_apply": 0,
        "intersection_refresh": 0,
        "camera": 0,
        "camera_objects": 0,
        "view_update": 0,
    }
    originals = {
        "line_settings_apply": presets.apply_line_settings,
        "outline_apply": outline_setup.apply_outline,
        "inner_apply": inner_lines.apply_inner_lines,
        "intersection_apply": intersection_lines.apply_intersection_lines,
        "intersection_refresh": intersection_lines.refresh_scene_intersections,
        "camera": camera_comp.refresh,
        "camera_objects": camera_comp.refresh_objects,
        "view_update": presets._update_view_layer,
    }

    def counted_line_settings_apply(*args, **kwargs):
        counts["line_settings_apply"] += 1
        return originals["line_settings_apply"](*args, **kwargs)

    def counted_outline_apply(*args, **kwargs):
        counts["outline_apply"] += 1
        return originals["outline_apply"](*args, **kwargs)

    def counted_inner_apply(*args, **kwargs):
        counts["inner_apply"] += 1
        return originals["inner_apply"](*args, **kwargs)

    def counted_intersection_apply(*args, **kwargs):
        counts["intersection_apply"] += 1
        return originals["intersection_apply"](*args, **kwargs)

    def counted_intersection_refresh(*args, **kwargs):
        counts["intersection_refresh"] += 1
        return originals["intersection_refresh"](*args, **kwargs)

    def counted_camera(*args, **kwargs):
        counts["camera"] += 1
        return originals["camera"](*args, **kwargs)

    def counted_camera_objects(*args, **kwargs):
        counts["camera_objects"] += 1
        return originals["camera_objects"](*args, **kwargs)

    def counted_view_update(*args, **kwargs):
        counts["view_update"] += 1
        return originals["view_update"](*args, **kwargs)

    presets.apply_line_settings = counted_line_settings_apply
    outline_setup.apply_outline = counted_outline_apply
    inner_lines.apply_inner_lines = counted_inner_apply
    intersection_lines.apply_intersection_lines = counted_intersection_apply
    intersection_lines.refresh_scene_intersections = counted_intersection_refresh
    camera_comp.refresh = counted_camera
    camera_comp.refresh_objects = counted_camera_objects
    presets._update_view_layer = counted_view_update

    def restore() -> None:
        presets.apply_line_settings = originals["line_settings_apply"]
        outline_setup.apply_outline = originals["outline_apply"]
        inner_lines.apply_inner_lines = originals["inner_apply"]
        intersection_lines.apply_intersection_lines = originals["intersection_apply"]
        intersection_lines.refresh_scene_intersections = originals["intersection_refresh"]
        camera_comp.refresh = originals["camera"]
        camera_comp.refresh_objects = originals["camera_objects"]
        presets._update_view_layer = originals["view_update"]

    return counts, restore


def _state_label(state: tuple[bool, bool, bool]) -> str:
    return "".join("1" if item else "0" for item in state)


def _assert_transition_counts(
    start: tuple[bool, bool, bool],
    prop_index: int,
    counts: dict[str, int],
) -> None:
    prop = PROPS[prop_index]
    turning_on = not start[prop_index]
    context = f"{_state_label(start)} -> {prop}={'ON' if turning_on else 'OFF'}"

    assert counts["line_settings_apply"] == 0, (context, counts)
    assert counts["camera"] == 0, (context, counts)

    if prop == "outline_enabled":
        assert counts["outline_apply"] == 0, (context, counts)
        assert counts["inner_apply"] == 0, (context, counts)
        assert counts["intersection_apply"] == 0, (context, counts)
        assert counts["intersection_refresh"] == 0, (context, counts)
        assert counts["view_update"] == 0, (context, counts)
        assert counts["camera_objects"] == 0, (context, counts)
        return

    if prop == "inner_line_enabled":
        assert counts["outline_apply"] == 0, (context, counts)
        assert counts["intersection_apply"] == 0, (context, counts)
        assert counts["intersection_refresh"] == 0, (context, counts)
        if turning_on:
            assert counts["inner_apply"] == 3, (context, counts)
            assert counts["view_update"] == 1, (context, counts)
            assert counts["camera_objects"] == 2, (context, counts)
        else:
            assert counts["inner_apply"] == 0, (context, counts)
            assert counts["view_update"] == 0, (context, counts)
            assert counts["camera_objects"] == 0, (context, counts)
        return

    if prop == "intersection_enabled":
        assert counts["outline_apply"] == 0, (context, counts)
        assert counts["inner_apply"] == 0, (context, counts)
        assert counts["view_update"] == 0, (context, counts)
        if turning_on:
            assert counts["intersection_refresh"] == 1, (context, counts)
            assert counts["intersection_apply"] == 3, (context, counts)
            assert counts["camera_objects"] == 2, (context, counts)
        else:
            assert counts["intersection_refresh"] == 0, (context, counts)
            assert counts["intersection_apply"] == 0, (context, counts)
            assert counts["camera_objects"] == 0, (context, counts)


def _assert_state(objects: list[bpy.types.Object], expected: tuple[bool, bool, bool]) -> None:
    intersection_count = 0
    for obj in objects:
        settings = obj.bmanga_line_settings
        actual = tuple(bool(getattr(settings, prop)) for prop in PROPS)
        assert actual == expected, (obj.name, actual, expected)
        outline = obj.modifiers.get(core.MODIFIER_NAME)
        assert outline is not None, obj.name
        assert bool(outline.show_viewport) == expected[0], obj.name
        inner = obj.modifiers.get(core.GN_MODIFIER_NAME)
        if expected[1]:
            assert inner is not None and inner.show_viewport, obj.name
        elif inner is not None:
            assert not inner.show_viewport, obj.name
        intersections = list(core.iter_intersection_modifiers(obj))
        intersection_count += len(intersections)
        if not expected[2]:
            assert not intersections, obj.name
    if expected[2]:
        assert intersection_count > 0, [obj.name for obj in objects]


def main() -> None:
    b_manga_line.register()
    checked = []
    try:
        for start in itertools.product((False, True), repeat=3):
            for prop_index, prop in enumerate(PROPS):
                objects = _setup_state(start)
                counts, restore = _install_counters()
                try:
                    settings = objects[0].bmanga_line_settings
                    setattr(settings, prop, not start[prop_index])
                    expected = list(start)
                    expected[prop_index] = not start[prop_index]
                    expected_state = tuple(expected)
                    _assert_state(objects, expected_state)
                    _assert_transition_counts(start, prop_index, counts)
                    checked.append((_state_label(start), LABELS[prop_index], counts.copy()))
                finally:
                    restore()
        print(f"BMANGA_LINE_TOGGLE_MATRIX_OK {len(checked)} transitions")
        for state, label, counts in checked:
            print(f"  {state} toggle {label}: {counts}")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
