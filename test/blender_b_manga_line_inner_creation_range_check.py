"""B-MANGA Line: internal lines are created only within the camera range."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
import math

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, presets  # noqa: E402


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
    settings.intersection_enabled = False
    return obj


def _apply(obj: bpy.types.Object) -> None:
    assert presets.apply_line_settings(obj, bpy.context), obj.name
    assert obj.modifiers.get(core.MODIFIER_NAME) is not None, obj.name


def _has_inner(obj: bpy.types.Object) -> bool:
    return obj.modifiers.get(core.GN_MODIFIER_NAME) is not None


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        camera = _make_camera()

        near = _make_cube("BML_内部線_作成範囲内", -8.0)
        exact = _make_cube("BML_内部線_境界上", -10.5)
        far = _make_cube("BML_内部線_作成範囲外", -12.0)

        for obj in (near, exact, far):
            settings = obj.bmanga_line_settings
            assert settings.use_inner_line_creation_limit is True
            assert abs(settings.inner_line_creation_max_distance - 10.0) < 1.0e-7
            _apply(obj)

        assert camera_comp.object_distance_from_camera(near, camera) < 10.0
        assert math.isclose(
            camera_comp.object_distance_from_camera(exact, camera),
            10.0,
            rel_tol=0.0,
            abs_tol=1.0e-7,
        )
        assert camera_comp.object_distance_from_camera(far, camera) > 10.0
        assert _has_inner(near), "10m以内の内部線が作成されていません"
        assert _has_inner(exact), "10m境界上の内部線が作成されていません"
        assert not _has_inner(far), "10mより遠い内部線が作成されています"

        far.bmanga_line_settings.inner_line_creation_max_distance = 12.0
        _apply(far)
        assert _has_inner(far), "作成距離を広げても内部線が作成されていません"

        far.bmanga_line_settings.inner_line_creation_max_distance = 10.0
        _apply(far)
        assert not _has_inner(far), "作成距離を戻しても遠距離内部線が残っています"

        far.bmanga_line_settings.use_inner_line_creation_limit = False
        _apply(far)
        assert _has_inner(far), "作成範囲をオフにしても内部線が作成されていません"

        bpy.context.scene.camera = None
        far.bmanga_line_settings.use_inner_line_creation_limit = True
        far.bmanga_line_settings.inner_line_creation_max_distance = 0.1
        _apply(far)
        assert _has_inner(far), "カメラ未設定時に内部線作成が止まっています"

        print("[PASS] inner line creation range limits generated modifiers")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
