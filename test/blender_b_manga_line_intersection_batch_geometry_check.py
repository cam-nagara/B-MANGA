"""Blender実機用: 交差線の共有形状計算が従来計算と一致する."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_cache, intersection_geometry  # noqa: E402


def _make_cube(name: str, location, rotation, scale) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.4, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    return obj


def _point_key(point: Vector) -> tuple[float, float, float]:
    return tuple(round(float(value), 4) for value in point)


def _segment_key(segment) -> tuple:
    endpoints = sorted((_point_key(segment.start), _point_key(segment.end)))
    return (*endpoints, _point_key(segment.normal))


def _add_outline(obj: bpy.types.Object) -> bpy.types.Modifier:
    outline = obj.modifiers.new(core.MODIFIER_NAME, "SOLIDIFY")
    outline.thickness = 0.035
    outline.offset = 1.0
    return outline


def _direct(source: bpy.types.Object, target: bpy.types.Object):
    return intersection_cache.build_cached_segments(
        source,
        [target],
        bpy.context.scene,
        thickness=0.001,
        offset=0.0,
    )


def _shared(source: bpy.types.Object, target: bpy.types.Object, origin: Vector):
    with intersection_geometry.BatchGeometryCache(
        [source, target],
        origin=origin,
    ) as geometry_cache:
        return intersection_cache.build_cached_segments(
            source,
            [target],
            bpy.context.scene,
            thickness=0.001,
            offset=0.0,
            geometry_cache=geometry_cache,
        )


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        source_rotation = (
            math.radians(17.0),
            math.radians(-11.0),
            math.radians(23.0),
        )
        target_rotation = (
            math.radians(-9.0),
            math.radians(15.0),
            math.radians(-19.0),
        )
        source_scale = (1.3, 0.75, 1.1)
        target_scale = (0.85, 1.2, 0.9)

        origin = Vector((125000.0, -230000.0, 81000.0))
        camera_offset = Vector((0.0, -8.0, 3.0))
        bpy.ops.object.camera_add(location=origin + camera_offset)
        camera = bpy.context.object
        bpy.context.scene.camera = camera

        source = _make_cube(
            "BML_batch_source",
            origin,
            source_rotation,
            source_scale,
        )
        target = _make_cube(
            "BML_batch_target",
            origin + Vector((0.42, 0.08, -0.03)),
            target_rotation,
            target_scale,
        )
        outline = _add_outline(target)
        bpy.context.view_layer.update()
        world_delta = target.matrix_world.translation - source.matrix_world.translation
        shared = _shared(source, target, camera.matrix_world.translation)

        reference_source = _make_cube(
            "BML_batch_reference_source",
            Vector(),
            source_rotation,
            source_scale,
        )
        reference_target = _make_cube(
            "BML_batch_reference_target",
            world_delta,
            target_rotation,
            target_scale,
        )
        reference_outline = _add_outline(reference_target)
        bpy.context.view_layer.update()
        direct = _direct(reference_source, reference_target)
        reference_shared = _shared(
            reference_source,
            reference_target,
            camera_offset,
        )

        assert direct, "従来経路で交差線が検出されません"
        assert len(shared) == len(direct), (len(direct), len(shared))
        direct_keys = sorted(_segment_key(item) for item in direct)
        shared_keys = sorted(_segment_key(item) for item in shared)
        assert shared_keys == direct_keys
        assert sorted(_segment_key(item) for item in reference_shared) == direct_keys
        assert outline.show_viewport and outline.show_render
        assert reference_outline.show_viewport and reference_outline.show_render
        print(
            "[PASS] shared intersection geometry matches direct path: "
            f"{len(shared)} segments"
        )
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
