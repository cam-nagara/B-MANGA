"""B-MANGA Liner: panel edits stay outside scene objects until confirmation."""

from __future__ import annotations

import ast
import math
import sys
import time
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    edge_width_curve,
    presets,
    settings_draft,
    update_state,
)
from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.cameras):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _test_all_drawn_settings_use_draft() -> None:
    source = (ROOT / "addons" / "b_manga_line" / "panels.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    draw_functions = {
        "_draw_outline",
        "_draw_inner_line",
        "_draw_intersection",
        "_draw_selection_line",
        "_draw_bump_line",
        "_draw_camera",
        "_draw_line_settings",
        "_draw_line_detail_grid",
        "_draw_midpoint_width_controls",
    }
    setting_names = set(core.BMangaLineSettings.bl_rna.properties.keys())
    drawn = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in draw_functions:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if child.value in setting_names:
                    drawn.add(child.value)
    drawn.discard("settings_locked")
    missing = drawn - set(settings_draft.DRAFT_FIELDS)
    assert not missing, sorted(missing)


def _new_mesh(name: str, location) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _select(objects, active) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _setup_scene():
    _clear_scene()
    bpy.ops.object.camera_add(location=(0.0, -8.0, 3.0))
    primary_camera = bpy.context.object
    primary_camera.name = "DraftPrimaryCamera"
    bpy.context.scene.camera = primary_camera
    bpy.ops.object.camera_add(location=(4.0, -8.0, 3.0))
    alternate_camera = bpy.context.object
    alternate_camera.name = "DraftAlternateCamera"

    first = _new_mesh("DraftFirst", (-1.5, 0.0, 0.0))
    second = _new_mesh("DraftSecond", (0.0, 0.0, 0.0))
    locked = _new_mesh("DraftLocked", (1.5, 0.0, 0.0))
    locked.bmanga_line_settings.settings_locked = True
    _select((first, second, locked), first)
    assert presets.apply_line_settings(first, bpy.context)
    return first, second, locked, primary_camera, alternate_camera


def _assert_close(actual, expected, label: str) -> None:
    assert math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1.0e-7), (
        label,
        actual,
        expected,
    )


def _assert_vector_close(actual, expected, label: str) -> None:
    assert len(actual) == len(expected), label
    for index, (left, right) in enumerate(zip(actual, expected)):
        _assert_close(left, right, f"{label}[{index}]")


def _test_input_is_scene_silent(first, second, locked, primary_camera, alternate_camera) -> None:
    settings_draft.invalidate(bpy.context)
    draft = settings_draft.ensure(bpy.context)
    assert draft is not None
    for obj in (first, second, locked):
        update_state.clear_pending(obj)

    before = {
        obj.name: (
            obj.bmanga_line_settings.outline_thickness_mm,
            tuple(obj.bmanga_line_settings.outline_color),
            obj.bmanga_line_settings.inner_line_angle,
            obj.bmanga_line_settings.line_width_distance_falloff,
        )
        for obj in (first, second, locked)
    }
    modifier = first.modifiers.get(core.MODIFIER_NAME)
    assert modifier is not None
    modifier_width = float(modifier.thickness)
    material = bpy.data.materials.get(core.MATERIAL_NAME)
    material_color = tuple(material.diffuse_color) if material is not None else None

    draft.outline_thickness_mm = 0.85
    draft.outline_color = (0.20, 0.35, 0.50, 1.0)
    draft.inner_line_angle = math.radians(72.0)
    draft.edge_smooth_factor = 0.45
    draft.line_width_distance_falloff = 1.7
    draft.use_camera_culling = False
    draft.bump_line_threshold = 0.42
    draft.line_camera_override = alternate_camera
    bpy.context.view_layer.update()

    for obj in (first, second, locked):
        current = obj.bmanga_line_settings
        original = before[obj.name]
        _assert_close(current.outline_thickness_mm, original[0], obj.name)
        assert tuple(current.outline_color) == original[1], obj.name
        _assert_close(current.inner_line_angle, original[2], obj.name)
        _assert_close(current.line_width_distance_falloff, original[3], obj.name)
        assert update_state.pending_targets(obj) == (), obj.name
    _assert_close(modifier.thickness, modifier_width, "modifier width")
    if material_color is not None:
        assert tuple(material.diffuse_color) == material_color
    assert bpy.context.scene.bmanga_line_camera is None
    assert settings_draft.get_line_camera(bpy.context) == alternate_camera
    assert {
        "outline_thickness_mm",
        "outline_color",
        "inner_line_angle",
        "edge_smooth_factor",
        "line_width_distance_falloff",
        "use_camera_culling",
        "bump_line_threshold",
        settings_draft.CAMERA_FIELD,
    }.issubset(settings_draft.dirty_fields(bpy.context))
    assert settings_draft.get_line_camera(bpy.context) != primary_camera


def _test_single_flush_and_lock(first, second, locked, alternate_camera) -> None:
    changed = settings_draft.flush(bpy.context)
    assert changed == 2, changed
    for obj in (first, second):
        settings = obj.bmanga_line_settings
        _assert_close(settings.outline_thickness_mm, 0.85, obj.name)
        _assert_vector_close(
            tuple(settings.outline_color),
            (0.20, 0.35, 0.50, 1.0),
            obj.name,
        )
        _assert_close(settings.inner_line_angle, math.radians(72.0), obj.name)
        _assert_close(settings.line_width_distance_falloff, 1.7, obj.name)
        assert {"outline", "inner", "bump"}.issubset(update_state.pending_targets(obj))
    _assert_close(locked.bmanga_line_settings.outline_thickness_mm, 0.3, "locked")
    assert update_state.pending_targets(locked) == ()
    assert bpy.context.scene.bmanga_line_camera == alternate_camera
    assert settings_draft.dirty_fields(bpy.context) == frozenset()


def _test_detail_snapshot_cancel(first) -> None:
    draft = settings_draft.ensure(bpy.context)
    draft.inner_line_thickness_mm = 0.41
    saved = settings_draft.snapshot(bpy.context)
    draft.inner_line_thickness_mm = 0.77
    draft.selection_edge_midpoint_jitter_percent = 12.0
    settings_draft.restore_snapshot(bpy.context, saved)
    _assert_close(draft.inner_line_thickness_mm, 0.41, "detail cancel")
    assert "selection_edge_midpoint_jitter_percent" not in settings_draft.dirty_fields(
        bpy.context
    )
    _assert_close(first.bmanga_line_settings.inner_line_thickness_mm, 0.3, "scene unchanged")


def _test_selection_change_flushes_previous_group(first, second, locked) -> None:
    settings_draft.discard(bpy.context)
    draft = settings_draft.ensure(bpy.context)
    draft.selection_line_thickness_mm = 0.64
    _select((first, locked), first)
    settings_draft.ensure(bpy.context)
    _assert_close(first.bmanga_line_settings.selection_line_thickness_mm, 0.64, "first")
    _assert_close(second.bmanga_line_settings.selection_line_thickness_mm, 0.64, "second")
    _assert_close(locked.bmanga_line_settings.selection_line_thickness_mm, 0.3, "locked")
    assert settings_draft.dirty_fields(bpy.context) == frozenset()


def _test_preset_save_and_save_pre(first) -> None:
    _select((first,), first)
    settings_draft.ensure(bpy.context)
    draft = settings_draft.ensure(bpy.context)
    draft.outline_thickness_mm = 0.66
    with temporary_line_preset_store():
        bpy.context.scene.bmanga_line_presets.clear()
        bpy.context.scene.bmanga_line_preset_index = -1
        presets._loaded_scene_pointers.add(bpy.context.scene.as_pointer())
        assert bpy.ops.bmanga_line.preset_add("EXEC_DEFAULT") == {"FINISHED"}
        _assert_close(first.bmanga_line_settings.outline_thickness_mm, 0.66, "preset flush")
        _assert_close(
            bpy.context.scene.bmanga_line_presets[0].outline_thickness,
            0.00066,
            "preset value",
        )
        draft = settings_draft.ensure(bpy.context)
        draft.outline_thickness_mm = 0.99
        assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
        _assert_close(
            first.bmanga_line_settings.outline_thickness_mm,
            0.66,
            "preset overrides draft",
        )
        _assert_close(
            settings_draft.ensure(bpy.context).outline_thickness_mm,
            0.66,
            "draft reload after preset",
        )

    draft = settings_draft.ensure(bpy.context)
    draft.intersection_color = (0.60, 0.30, 0.10, 1.0)
    settings_draft._on_save_pre(None)
    _assert_vector_close(
        tuple(first.bmanga_line_settings.intersection_color),
        (0.60, 0.30, 0.10, 1.0),
        "save pre",
    )


def _test_explicit_operators_flush(first) -> None:
    draft = settings_draft.ensure(bpy.context)
    draft.outline_thickness_mm = 0.71
    assert bpy.ops.bmanga_line.reflect_target(
        "EXEC_DEFAULT", target="outline"
    ) == {"FINISHED"}
    _assert_close(first.bmanga_line_settings.outline_thickness_mm, 0.71, "reflect")
    assert settings_draft.dirty_fields(bpy.context) == frozenset()

    draft = settings_draft.ensure(bpy.context)
    draft.edge_smooth_factor = 0.31
    node = edge_width_curve.reset_node_from_settings(draft, "outline")
    assert node is not None
    curve = node.mapping.curves[0]
    midpoint = min(curve.points, key=lambda point: abs(float(point.location.x) - 0.25))
    midpoint.location.y = 0.88
    node.mapping.update()
    _assert_close(first.bmanga_line_settings.edge_width_curve_25, 0.25, "curve pending")
    assert bpy.ops.bmanga_line.detail_settings("EXEC_DEFAULT") == {"FINISHED"}
    _assert_close(first.bmanga_line_settings.edge_smooth_factor, 0.31, "detail OK")
    _assert_close(first.bmanga_line_settings.edge_width_curve_25, 0.88, "curve OK")

    draft = settings_draft.ensure(bpy.context)
    draft.line_width_distance_falloff = 1.25
    assert bpy.ops.bmanga_line.refresh_camera("EXEC_DEFAULT") == {"FINISHED"}
    _assert_close(
        first.bmanga_line_settings.line_width_distance_falloff,
        1.25,
        "camera refresh",
    )


def _test_undo_redo_state_reset(first) -> None:
    draft = settings_draft.ensure(bpy.context)
    draft.selection_line_offset = 0.37
    assert "selection_line_offset" in settings_draft.dirty_fields(bpy.context)
    settings_draft._on_state_reset(None)
    draft = settings_draft.ensure(bpy.context)
    _assert_close(
        draft.selection_line_offset,
        first.bmanga_line_settings.selection_line_offset,
        "undo/redo reload",
    )
    assert settings_draft.dirty_fields(bpy.context) == frozenset()


def _test_external_setting_change_reloads_draft(first) -> None:
    settings_draft.ensure(bpy.context)
    first.bmanga_line_settings.outline_offset = 0.22
    draft = settings_draft.ensure(bpy.context)
    _assert_close(draft.outline_offset, 0.22, "external setting reload")


def _test_rapid_edits_do_not_scale_with_selection(first) -> tuple[float, float, float]:
    shared_mesh = bpy.data.meshes.new("DraftSharedMesh")
    shared_mesh.from_pydata(
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.0, 0.5, 0.0)],
        [],
        [(0, 1, 2)],
    )
    extra = []
    for index in range(640):
        obj = bpy.data.objects.new(f"DraftPerf_{index:03d}", shared_mesh)
        bpy.context.scene.collection.objects.link(obj)
        extra.append(obj)
    _select((first, *extra), first)
    settings_draft.invalidate(bpy.context)
    draft = settings_draft.ensure(bpy.context)
    original = extra[-1].bmanga_line_settings.outline_thickness_mm
    started = time.perf_counter()
    for index in range(200):
        draft.outline_thickness_mm = 0.20 + index * 0.001
        draft.outline_color = (index / 200.0, 0.25, 0.50, 1.0)
    elapsed = time.perf_counter() - started
    redraw_started = time.perf_counter()
    for _index in range(200):
        assert settings_draft.ensure(bpy.context).as_pointer() == draft.as_pointer()
    redraw_elapsed = time.perf_counter() - redraw_started
    _assert_close(extra[-1].bmanga_line_settings.outline_thickness_mm, original, "rapid edit")
    assert update_state.pending_targets(extra[-1]) == ()
    assert elapsed < 0.25, elapsed
    assert redraw_elapsed < 0.25, redraw_elapsed
    flush_started = time.perf_counter()
    changed = settings_draft.flush(bpy.context)
    flush_elapsed = time.perf_counter() - flush_started
    assert changed == len(extra) + 1, changed
    assert "outline" in update_state.pending_targets(extra[-1])
    assert flush_elapsed < 1.0, flush_elapsed
    return elapsed, redraw_elapsed, flush_elapsed


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _test_all_drawn_settings_use_draft()
        first, second, locked, primary_camera, alternate_camera = _setup_scene()
        _test_input_is_scene_silent(
            first,
            second,
            locked,
            primary_camera,
            alternate_camera,
        )
        _test_single_flush_and_lock(first, second, locked, alternate_camera)
        _test_detail_snapshot_cancel(first)
        _test_selection_change_flushes_previous_group(first, second, locked)
        _test_preset_save_and_save_pre(first)
        _test_explicit_operators_flush(first)
        _test_undo_redo_state_reset(first)
        _test_external_setting_change_reloads_draft(first)
        elapsed, redraw_elapsed, flush_elapsed = _test_rapid_edits_do_not_scale_with_selection(
            first
        )
        print(
            "BMANGA_LINE_SETTINGS_DRAFT_OK "
            f"rapid_640x200={elapsed:.6f}s redraw_640x200={redraw_elapsed:.6f}s "
            f"flush_641={flush_elapsed:.6f}s"
        )
    finally:
        b_manga_line.unregister()


if __name__ == "__main__":
    main()
