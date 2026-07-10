"""Blender実機用: 交差候補の範囲判定をシーン単位で再利用する."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, intersection_lines, presets  # noqa: E402


def _set_without_update(obj, name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(obj.bmanga_line_settings, name, value)
    finally:
        core._propagating = old


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        objects = []
        for index in range(24):
            bpy.ops.mesh.primitive_cube_add(
                size=1.0,
                location=(float(index) * 3.0, 0.0, 0.0),
            )
            obj = bpy.context.object
            obj.name = f"BML_candidate_{index:02d}"
            _set_without_update(obj, "intersection_enabled", True)
            _set_without_update(obj, "use_intersection_creation_limit", False)
            assert presets.apply_line_settings(
                obj,
                bpy.context,
                refresh_scene=False,
                line_targets=("outline",),
            )
            objects.append(obj)

        calls = {"range": 0}
        original = camera_comp.intersection_line_creation_in_range

        def counted(*args, **kwargs):
            calls["range"] += 1
            return original(*args, **kwargs)

        camera_comp.intersection_line_creation_in_range = counted
        try:
            refreshed = intersection_lines.refresh_scene_intersections(
                bpy.context.scene,
                sources=objects,
            )
        finally:
            camera_comp.intersection_line_creation_in_range = original

        assert not refreshed, [obj.name for obj in refreshed]
        # 候補索引・各ソース判定・反映指紋の3経路で各1回。候補組数に
        # 比例する二重ループ（24*24回）へ戻っていないことを保証する。
        assert calls["range"] <= len(objects) * 3 + 2, calls
        print(f"[PASS] intersection candidate range calls are linear: {calls}")
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
