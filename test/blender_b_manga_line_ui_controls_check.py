"""B-MANGA Line: camera distance button and display checkboxes."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _select(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _line_modifiers(obj: bpy.types.Object):
    return list(core.iter_line_modifiers(obj))


def _assert_line_visibility(obj: bpy.types.Object, visible: bool) -> None:
    assert bool(obj.bmanga_line_settings.lines_visible) is visible
    assert bool(obj.get(core.PROP_LINES_HIDDEN, False)) is (not visible)
    for mod in _line_modifiers(obj):
        assert bool(mod.show_viewport) is visible, (obj.name, mod.name, mod.show_viewport)
        assert bool(mod.show_render) is visible, (obj.name, mod.name, mod.show_render)


def _assert_distance_button(active: bpy.types.Object, other: bpy.types.Object) -> None:
    _select(active, [active, other])
    assert bpy.ops.bmanga_line.reset_camera_ref("EXEC_DEFAULT") == {"FINISHED"}
    expected = (
        bpy.context.scene.camera.matrix_world.translation
        - active.matrix_world.translation
    ).length
    for obj in (active, other):
        assert math.isclose(
            obj.bmanga_line_settings.line_width_reference_distance,
            expected,
            rel_tol=0.0,
            abs_tol=1.0e-6,
        ), obj.name


def _assert_visibility_checkboxes(active: bpy.types.Object, other: bpy.types.Object) -> None:
    _select(active, [active, other])
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    _select(active, [active, other])
    for obj in (active, other):
        _assert_line_visibility(obj, True)

    active.bmanga_line_settings.lines_visible = False
    for obj in (active, other):
        _assert_line_visibility(obj, False)

    active.bmanga_line_settings.lines_visible = True
    for obj in (active, other):
        _assert_line_visibility(obj, True)

    assert bpy.ops.bmanga_line.set_visibility("EXEC_DEFAULT", visible=False) == {"FINISHED"}
    for obj in (active, other):
        _assert_line_visibility(obj, False)
    active.bmanga_line_settings.lines_visible = True


def _assert_line_only_checkbox(active: bpy.types.Object, other: bpy.types.Object) -> None:
    _select(active, [active, other])
    active.bmanga_line_settings.line_only_visible = True
    for obj in (active, other):
        assert bool(obj.bmanga_line_settings.line_only_visible)
        assert bool(obj.get(core.PROP_LINE_ONLY, False)), obj.name
        _assert_line_visibility(obj, True)

    active.bmanga_line_settings.line_only_visible = False
    for obj in (active, other):
        assert not bool(obj.bmanga_line_settings.line_only_visible)
        assert not bool(obj.get(core.PROP_LINE_ONLY, False)), obj.name

    assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=True) == {"FINISHED"}
    for obj in (active, other):
        assert bool(obj.bmanga_line_settings.line_only_visible), obj.name
    assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=False) == {"FINISHED"}
    for obj in (active, other):
        assert not bool(obj.bmanga_line_settings.line_only_visible), obj.name


def _set_setting_without_update(settings, name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(settings, name, value)
    finally:
        core._propagating = old


def _assert_subsurf_checkbox(active: bpy.types.Object, other: bpy.types.Object) -> None:
    for index, obj in enumerate((active, other), start=2):
        mod = obj.modifiers.new(f"ユーザーSubsurf_{index}", "SUBSURF")
        mod.levels = 0
        mod.render_levels = index

    _select(active, [active, other])
    active.bmanga_line_settings.match_subsurf_viewport_to_render = True
    for obj in (active, other):
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                assert int(mod.levels) == int(mod.render_levels), (obj.name, mod.name)

    for obj in (active, other):
        _set_setting_without_update(
            obj.bmanga_line_settings,
            "match_subsurf_viewport_to_render",
            False,
        )
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                mod.levels = int(mod.render_levels)
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    for obj in (active, other):
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                assert int(mod.levels) == 0, (obj.name, mod.name, mod.levels)

    for obj in (active, other):
        _set_setting_without_update(
            obj.bmanga_line_settings,
            "match_subsurf_viewport_to_render",
            True,
        )
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                mod.levels = 0
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    for obj in (active, other):
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                assert int(mod.levels) == int(mod.render_levels), (obj.name, mod.name)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, 0.0, 5.0))
        bpy.context.scene.camera = bpy.context.object
        active = _make_cube("BML_UI_active", (0.0, 0.0, 1.0))
        other = _make_cube("BML_UI_other", (2.0, 0.0, 0.0))

        _assert_distance_button(active, other)
        _assert_visibility_checkboxes(active, other)
        _assert_line_only_checkbox(active, other)
        _assert_subsurf_checkbox(active, other)
        print("BMANGA_LINE_UI_CONTROLS_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
