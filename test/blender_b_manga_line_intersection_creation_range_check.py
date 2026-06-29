"""B-MANGA Line: intersection lines are created only within the camera range."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, intersection_lines, presets  # noqa: E402


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
    return obj


def _target_names(obj: bpy.types.Object) -> set[str]:
    names: set[str] = set()
    for mod in core.iter_intersection_modifiers(obj):
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

        source = _make_cube("BML_intersection_range_A_source", -8.0)
        exact = _make_cube("BML_intersection_range_B_exact", -10.5)
        far = _make_cube("BML_intersection_range_C_far", -12.0)

        for obj in (source, exact, far):
            settings = obj.bmanga_line_settings
            assert settings.use_intersection_creation_limit is True
            assert abs(settings.intersection_creation_max_distance - 10.0) < 1.0e-7
            _apply(obj)

        assert camera_comp.object_distance_from_camera(source, camera) < 10.0
        assert math.isclose(
            camera_comp.object_distance_from_camera(exact, camera),
            10.0,
            rel_tol=0.0,
            abs_tol=1.0e-7,
        )
        assert camera_comp.object_distance_from_camera(far, camera) > 10.0

        targets = _target_names(source)
        assert exact.name in targets, "10m境界上の交差相手が作成対象に入っていません"
        assert far.name not in targets, "10mより遠い交差相手が作成対象に残っています"
        assert not list(core.iter_intersection_modifiers(far)), (
            "10mより遠いオブジェクト自身に交差線が作成されています"
        )

        far.bmanga_line_settings.intersection_creation_max_distance = 12.0
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert far.name in _target_names(source), (
            "作成距離を広げても遠い交差相手が作成対象に戻っていません"
        )

        far.bmanga_line_settings.intersection_creation_max_distance = 10.0
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert far.name not in _target_names(source), (
            "作成距離を戻しても遠い交差相手の交差線が残っています"
        )

        far.bmanga_line_settings.use_intersection_creation_limit = False
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert far.name in _target_names(source), (
            "作成範囲をオフにしても交差相手が作成対象に戻っていません"
        )

        bpy.context.scene.camera = None
        far.bmanga_line_settings.use_intersection_creation_limit = True
        far.bmanga_line_settings.intersection_creation_max_distance = 0.1
        intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert far.name in _target_names(source), (
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
