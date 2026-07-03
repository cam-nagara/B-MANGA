"""B-MANGA Line: intersection lines are created only within the camera range."""

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
    intersection_lines,
    intersection_shell,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    return camera


def _make_cube(name: str, z: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, z))
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = False
    settings.intersection_enabled = True
    settings.intersection_method = "BOOLEAN"
    return obj


def _make_data_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    verts = [
        (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),
    ]
    faces = [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
        (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0),
    ]
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = False
    settings.intersection_enabled = True
    settings.intersection_method = "BOOLEAN"
    return obj


def _target_names(obj: bpy.types.Object) -> set[str]:
    names: set[str] = set()
    for mod in core.iter_intersection_modifiers(obj):
        if intersection_shell.is_shell_modifier(mod):
            names.update(
                target.name for target in intersection_shell.modifier_targets(mod)
            )
            continue
        target = intersection_lines._modifier_target(mod)
        if target is not None:
            names.add(target.name)
    return names


def _apply(obj: bpy.types.Object) -> None:
    assert presets.apply_line_settings(obj, bpy.context), obj.name
    assert obj.modifiers.get(core.MODIFIER_NAME) is not None, obj.name


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        camera = _make_camera()

        source = _make_cube("BML_intersection_range_A_source", -10.5)
        exact = _make_cube("BML_intersection_range_B_exact", -10.5)
        exact.location.x = 0.35
        far = _make_cube("BML_intersection_range_C_far", -12.0)

        for obj in (source, exact, far):
            settings = obj.bmanga_line_settings
            assert abs(settings.intersection_creation_max_distance - 10.0) < 1.0e-7
            assert settings.use_intersection_creation_limit is True
            _apply(obj)

        assert math.isclose(
            camera_comp.object_distance_from_camera(source, camera),
            10.0,
            rel_tol=0.0,
            abs_tol=0.05,
        )
        assert math.isclose(
            camera_comp.object_distance_from_camera(exact, camera),
            10.0,
            rel_tol=0.0,
            abs_tol=0.05,
        )
        assert camera_comp.object_distance_from_camera(far, camera) > 10.0

        targets = _target_names(source)
        assert exact.name in targets, "10m境界上の交差相手が作成対象に入っていません"
        assert far.name not in targets, "10mより遠い交差相手が作成対象に残っています"
        assert not list(core.iter_intersection_modifiers(far)), (
            "10mより遠いオブジェクト自身に交差線が作成されています"
        )

        source.bmanga_line_settings.intersection_creation_max_distance = 9.0
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert not _target_names(source), (
            "作成距離を狭めても交差線が残っています"
        )

        source.bmanga_line_settings.intersection_creation_max_distance = 10.0
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert exact.name in _target_names(source), (
            "作成距離を戻しても重なっている交差相手が戻っていません"
        )

        stale_transform = _make_data_cube(
            "BML_intersection_range_D_stale_transform", (-4.0, -11.5, 0.0),
        )
        assert stale_transform.bmanga_line_settings.use_intersection_creation_limit is True
        _apply(stale_transform)
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert stale_transform.name not in _target_names(source), (
            "移動直後の遠距離交差相手が作成対象に残っています"
        )
        assert not list(core.iter_intersection_modifiers(stale_transform)), (
            "移動直後の遠距離オブジェクト自身に交差線が作成されています"
        )

        source.bmanga_line_settings.use_intersection_creation_limit = False
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert exact.name in _target_names(source), (
            "作成範囲をオフにしても重なっている交差相手が作成対象に戻っていません"
        )

        bpy.context.scene.camera = None
        source.bmanga_line_settings.use_intersection_creation_limit = True
        source.bmanga_line_settings.intersection_creation_max_distance = 0.1
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert exact.name in _target_names(source), (
            "カメラ未設定時に交差線作成が止まっています"
        )

        print("[PASS] intersection creation range limits generated modifiers")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
