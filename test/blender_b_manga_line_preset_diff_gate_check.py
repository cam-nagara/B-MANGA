"""B-MANGA Line: applying an unchanged preset must not mark lines pending."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402
import b_manga_line  # noqa: E402
from b_manga_line import presets, update_state  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_cube(name: str, x: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    settings.selection_line_enabled = True
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _install_preset_from(obj: bpy.types.Object) -> None:
    scene = bpy.context.scene
    scene.bmanga_line_presets.clear()
    preset = scene.bmanga_line_presets.add()
    preset.name = "diff gate preset"
    presets.copy_settings_to_preset(obj.bmanga_line_settings, preset)
    scene.bmanga_line_preset_index = 0
    presets._loaded_scene_pointers.add(scene.as_pointer())


def _pending_objects(objects: list[bpy.types.Object]) -> list[str]:
    return [obj.name for obj in objects if update_state.pending_targets(obj)]


def main() -> None:
    with temporary_line_preset_store():
        b_manga_line.register()
        try:
            _clear_scene()
            objects = [_make_cube("BML_preset_diff_A", 0.0), _make_cube("BML_preset_diff_B", 1.5)]
            _select(objects)
            _install_preset_from(objects[0])

            for obj in objects:
                update_state.clear_pending(obj)
            assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
            assert not _pending_objects(objects), (
                "同じプリセットの初回再適用で反映待ちが付いています",
                _pending_objects(objects),
            )

            preset = bpy.context.scene.bmanga_line_presets[0]
            preset.outline_thickness = 0.004
            assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
            assert set(_pending_objects(objects)) == {obj.name for obj in objects}

            for obj in objects:
                update_state.clear_pending(obj)
            assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
            assert not _pending_objects(objects), (
                "同じプリセットの2回目再適用で反映待ちが付いています",
                _pending_objects(objects),
            )

            print("[PASS] preset diff gate skips unchanged objects")
        finally:
            try:
                b_manga_line.unregister()
            except Exception:
                pass
            bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
