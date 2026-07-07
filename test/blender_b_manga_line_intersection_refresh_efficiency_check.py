"""Blender実機用: 交差線の単体適用で同じ作成処理を二重実行しないことを確認."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_cache, intersection_lines, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.use_intersection_creation_limit = False
    return obj


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        source = _cube("BML_refresh_source", (0.0, 0.0, 0.0))
        target = _cube("BML_refresh_target", (0.5, 0.0, 0.0))

        target.bmanga_line_settings.intersection_enabled = False
        assert presets.apply_line_settings(target, bpy.context, refresh_scene=False)

        bpy.ops.object.select_all(action="DESELECT")
        source.select_set(True)
        bpy.context.view_layer.objects.active = source
        source.bmanga_line_settings.intersection_enabled = True
        intersection_lines.remove_intersection_lines(source)
        intersection_lines.remove_intersection_lines(target)

        counts = {"build_segments": 0, "scene_refresh": 0}
        real_build_segments = intersection_cache.build_cached_segments
        real_scene_refresh = intersection_lines.refresh_scene_intersections

        def counted_build_segments(*args, **kwargs):
            counts["build_segments"] += 1
            return real_build_segments(*args, **kwargs)

        def counted_scene_refresh(*args, **kwargs):
            counts["scene_refresh"] += 1
            return real_scene_refresh(*args, **kwargs)

        intersection_cache.build_cached_segments = counted_build_segments
        intersection_lines.refresh_scene_intersections = counted_scene_refresh
        try:
            assert presets.apply_line_settings(source, bpy.context, refresh_scene=True)
        finally:
            intersection_cache.build_cached_segments = real_build_segments
            intersection_lines.refresh_scene_intersections = real_scene_refresh

        assert counts == {"build_segments": 1, "scene_refresh": 1}, counts
        assert source.modifiers.get(core.INTERSECTION_MODIFIER_NAME) is not None
        print(f"[PASS] intersection refresh is not duplicated: {counts}")
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
