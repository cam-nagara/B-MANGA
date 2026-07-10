"""B-MANGA Line: intersection targets are detected automatically."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_lines, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _target_names(obj: bpy.types.Object) -> set[str]:
    mod = obj.modifiers.get(core.INTERSECTION_MODIFIER_NAME)
    if mod is None:
        return set()
    return {target.name for target in intersection_lines.modifier_targets(mod)}


def _set_intersection_enabled(obj: bpy.types.Object, enabled: bool) -> None:
    old = core._propagating
    core._propagating = True
    try:
        obj.bmanga_line_settings.intersection_enabled = enabled
    finally:
        core._propagating = old


def _apply_all(objects: list[bpy.types.Object]) -> None:
    enabled_states = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        enabled_states.append((settings, bool(settings.intersection_enabled)))
        _set_intersection_enabled(obj, False)
        assert presets.apply_line_settings(obj, bpy.context)
    for settings, enabled in enabled_states:
        old = core._propagating
        core._propagating = True
        try:
            settings.intersection_enabled = enabled
        finally:
            core._propagating = old
    for obj in objects:
        assert presets.apply_line_settings(obj, bpy.context)


def _expected_targets(obj: bpy.types.Object, objects: list[bpy.types.Object]) -> set[str]:
    expected: set[str] = set()
    for item in objects:
        if item == obj:
            continue
        if not intersection_lines._bounds_overlap(obj, item, 0.01):
            continue
        item_enabled = item.bmanga_line_settings.intersection_enabled
        if item_enabled and not intersection_lines._source_owns_intersection_pair(
            obj,
            item,
            bpy.context.scene,
        ):
            continue
        expected.add(item.name)
    return expected


def _test_name_fallback_without_camera() -> None:
    _clear_scene()

    first = _make_cube("BML_auto_target_A", (-0.35, 0.0, 0.0))
    second = _make_cube("BML_auto_target_B", (0.35, 0.0, 0.0))
    third = _make_cube("BML_auto_target_C", (0.0, 0.35, 0.0))
    distant = _make_cube("BML_auto_target_D_no_overlap", (4.0, 0.0, 0.0))
    objects = [first, second, third, distant]

    for obj in objects:
        settings = obj.bmanga_line_settings
        _set_intersection_enabled(obj, True)
        settings.intersection_method = "BOOLEAN"

    _apply_all(objects)

    for obj in objects:
        expected = _expected_targets(obj, objects)
        actual = _target_names(obj)
        assert actual == expected, (obj.name, actual, expected)
        assert all(
            mod.name == core.INTERSECTION_MODIFIER_NAME
            for mod in core.iter_intersection_modifiers(obj)
        )

    bpy.ops.object.select_all(action="DESELECT")
    first.select_set(True)
    bpy.context.view_layer.objects.active = first
    _set_intersection_enabled(first, False)
    assert presets.apply_line_settings(second, bpy.context)
    assert presets.apply_line_settings(third, bpy.context)
    assert first.name in _target_names(second)
    assert first.name in _target_names(third)


def _test_active_object_does_not_change_pair_owner() -> None:
    _clear_scene()

    active_side = _make_cube("Z_active_should_own", (0.0, 0.0, 0.0))
    name_side = _make_cube("A_name_would_own", (0.35, 0.0, 0.0))
    for obj in (active_side, name_side):
        settings = obj.bmanga_line_settings
        _set_intersection_enabled(obj, True)
        settings.intersection_method = "BOOLEAN"

    bpy.context.view_layer.objects.active = active_side
    _apply_all([active_side, name_side])

    active_targets = _target_names(active_side)
    name_targets = _target_names(name_side)
    assert active_side.name in name_targets, (
        "決定的な所有側に交差線が作られていません",
        active_targets,
        name_targets,
        intersection_lines._source_owns_intersection_pair(
            name_side,
            active_side,
            bpy.context.scene,
        ),
    )
    assert name_side.name not in active_targets, (
        "アクティブ側にも交差線が重複作成されています"
    )

    bpy.context.view_layer.objects.active = name_side
    assert presets.apply_line_settings(active_side, bpy.context)
    assert presets.apply_line_settings(name_side, bpy.context)
    assert active_side.name in _target_names(name_side)
    assert name_side.name not in _target_names(active_side)


def main() -> None:
    b_manga_line.register()
    _test_name_fallback_without_camera()
    _test_active_object_does_not_change_pair_owner()

    print("[PASS] intersection targets are detected automatically")


if __name__ == "__main__":
    main()
