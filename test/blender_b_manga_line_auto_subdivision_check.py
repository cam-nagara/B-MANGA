"""B-MANGA Line: auto midpoint Subdivision Surface setup."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, subdivision_lod  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _auto_subsurf(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    for mod in obj.modifiers:
        if subdivision_lod.is_auto_subsurf_modifier(mod):
            return mod
    return None


def _assert_render_level_ladder() -> None:
    cases = (
        (0.0, 4),
        (4.99, 4),
        (5.0, 3),
        (9.99, 3),
        (10.0, 2),
        (14.99, 2),
        (15.0, 1),
        (19.99, 1),
        (20.0, 0),
        (25.0, 0),
    )
    for distance, expected in cases:
        actual = subdivision_lod.render_levels_for_distance(distance)
        if actual != expected:
            raise AssertionError((distance, actual, expected))


def _assert_auto_modifier_before_lines(obj: bpy.types.Object) -> None:
    auto_mod = _auto_subsurf(obj)
    assert auto_mod is not None, "自動サブディビジョンサーフェスがありません"
    names = [mod.name for mod in obj.modifiers]
    auto_index = names.index(auto_mod.name)
    outline_index = names.index(core.MODIFIER_NAME)
    assert auto_index < outline_index, names


def _assert_cube_edges_are_creased(obj: bpy.types.Object) -> None:
    attr = obj.data.attributes.get(subdivision_lod.CREASE_EDGE_ATTR)
    assert attr is not None, "クリース属性がありません"
    creased = [
        index for index, item in enumerate(attr.data)
        if abs(float(item.value) - 1.0) < 1.0e-6
    ]
    assert len(creased) == 12, creased


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _assert_render_level_ladder()
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, -3.0, 0.0))
        bpy.context.scene.camera = bpy.context.object
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.object
        obj.data.materials.append(bpy.data.materials.new("BML_auto_subsurf_surface"))
        obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
        obj.bmanga_line_settings.edge_smooth_factor = -0.5

        assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
        auto_mod = _auto_subsurf(obj)
        assert auto_mod is not None
        assert auto_mod.levels == 0
        assert auto_mod.render_levels == 4
        _assert_auto_modifier_before_lines(obj)
        _assert_cube_edges_are_creased(obj)

        obj.location.y = 9.0
        bpy.context.view_layer.update()
        subdivision_lod.ensure_auto_subdivision(obj, bpy.context.scene)
        assert _auto_subsurf(obj).render_levels == 2

        obj.bmanga_line_settings.auto_subdivision_for_midpoint = False
        assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
        assert _auto_subsurf(obj) is None

        print("BMANGA_LINE_AUTO_SUBDIVISION_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
