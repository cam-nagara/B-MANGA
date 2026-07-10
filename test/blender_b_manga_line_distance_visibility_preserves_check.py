"""B-MANGA Line: far-distance visibility hides lines without deleting them."""

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
    outline_local_subdivision,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera() -> None:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0))
    bpy.context.scene.camera = bpy.context.object


def _mark_edges(obj: bpy.types.Object) -> None:
    attr = obj.data.attributes.get("freestyle_edge")
    if attr is None:
        attr = obj.data.attributes.new("freestyle_edge", "BOOLEAN", "EDGE")
    for edge in obj.data.edges:
        attr.data[edge.index].value = edge.index < 4
    obj.data.update()


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    _mark_edges(obj)
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.selection_line_enabled = True
    settings.intersection_enabled = True
    settings.use_outline_creation_limit = False
    settings.use_inner_line_creation_limit = False
    settings.use_selection_line_creation_limit = False
    settings.use_intersection_creation_limit = False
    return obj


def _apply(obj: bpy.types.Object) -> None:
    assert presets.apply_line_settings(obj, bpy.context), obj.name


def _line_modifiers(obj: bpy.types.Object) -> list[bpy.types.Modifier]:
    outline = outline_local_subdivision.get_modifier(obj)
    if outline is None:
        outline = obj.modifiers.get(core.MODIFIER_NAME)
    assert outline is not None, f"{obj.name}: アウトラインがありません"
    mods = [outline]
    for name in (
        core.GN_MODIFIER_NAME,
        core.SELECTION_LINE_MODIFIER_NAME,
    ):
        mod = obj.modifiers.get(name)
        assert mod is not None, f"{obj.name}: {name} がありません"
        mods.append(mod)
    intersections = list(core.iter_intersection_modifiers(obj))
    assert intersections, f"{obj.name}: 交差線がありません"
    mods.extend(intersections)
    return mods


def _set_distance_limits(obj: bpy.types.Object, limit: float) -> None:
    settings = obj.bmanga_line_settings
    settings.use_outline_distance_limit = True
    settings.outline_max_distance = limit
    settings.use_inner_line_distance_limit = True
    settings.inner_line_max_distance = limit
    settings.use_intersection_distance_limit = True
    settings.intersection_max_distance = limit
    settings.use_selection_line_distance_limit = True
    settings.selection_line_max_distance = limit


def _assert_visibility(obj: bpy.types.Object, visible: bool) -> None:
    for mod in _line_modifiers(obj):
        assert mod.show_viewport is visible, (mod.name, mod.show_viewport, visible)
        assert mod.show_render is visible, (mod.name, mod.show_render, visible)
    assert not bool(obj.get(core.PROP_LINES_HIDDEN, False))


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        _make_camera()

        source = _make_cube("BML_遠距離表示_A", (0.0, 0.0, -8.0))
        target = _make_cube("BML_遠距離表示_B", (0.35, 0.0, -8.0))
        _apply(target)
        _apply(source)

        source_mods = _line_modifiers(source)
        assert source_mods

        _set_distance_limits(source, 7.0)
        assert camera_comp.refresh_visibility_objects(bpy.context, [source])
        _assert_visibility(source, False)
        assert _line_modifiers(source) == source_mods

        _set_distance_limits(source, 9.0)
        assert camera_comp.refresh_visibility_objects(bpy.context, [source])
        _assert_visibility(source, True)
        assert _line_modifiers(source) == source_mods

        print("[PASS] far-distance visibility hides all line types without deletion")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
