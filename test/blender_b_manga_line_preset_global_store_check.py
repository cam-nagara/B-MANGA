"""Blender実機用: ラインプリセットが.blendではなく共有保存先に残ることを確認."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import presets  # noqa: E402


STORE_FILE = "b_manga_line_presets.json"


def _store_names(store_dir: Path) -> list[str]:
    path = store_dir / STORE_FILE
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [item["name"] for item in data.get("presets", [])]


def _set_store_dir(store_dir: Path) -> None:
    os.environ["BMANGA_LINE_PRESET_STORE_DIR"] = str(store_dir)
    presets._loaded_scene_pointers.clear()


def _make_source_cube() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.object
    obj.name = "BML_global_preset_source"
    obj.bmanga_line_settings.outline_thickness = 0.012
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def _assert_scene_preset_names(expected: list[str]) -> None:
    presets.ensure_presets_loaded(bpy.context.scene)
    actual = [item.name for item in bpy.context.scene.bmanga_line_presets]
    assert actual == expected, (actual, expected)


def main() -> None:
    old_store = os.environ.get("BMANGA_LINE_PRESET_STORE_DIR")
    with tempfile.TemporaryDirectory(prefix="bmanga_line_global_store_") as temp_root:
        temp_root_path = Path(temp_root)
        store_a = temp_root_path / "store_a"
        store_b = temp_root_path / "store_b"
        blend_path = temp_root_path / "line_preset_source.blend"
        try:
            _set_store_dir(store_a)
            bpy.ops.wm.read_factory_settings(use_empty=True)
            b_manga_line.register()
            _make_source_cube()
            assert bpy.ops.bmanga_line.preset_add("EXEC_DEFAULT") == {"FINISHED"}
            scene = bpy.context.scene
            assert scene.bmanga_line_presets[0].name == "ラインプリセット"
            assert _store_names(store_a) == ["ラインプリセット"]
            scene.bmanga_line_presets[0].name = "共有保存テスト"
            assert _store_names(store_a) == ["共有保存テスト"]
            assert bpy.ops.bmanga_line.preset_save("EXEC_DEFAULT") == {"FINISHED"}
            assert _store_names(store_a) == ["共有保存テスト"]
            bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

            b_manga_line.unregister()
            bpy.ops.wm.read_factory_settings(use_empty=True)
            _set_store_dir(store_b)
            b_manga_line.register()
            bpy.ops.wm.open_mainfile(filepath=str(blend_path))
            _assert_scene_preset_names([])
            assert _store_names(store_b) == []

            _set_store_dir(store_a)
            bpy.context.scene.bmanga_line_presets.clear()
            _assert_scene_preset_names(["共有保存テスト"])
            print("BMANGA_LINE_PRESET_GLOBAL_STORE_OK")
        finally:
            try:
                b_manga_line.unregister()
            except Exception:
                pass
            bpy.ops.wm.read_factory_settings(use_empty=True)
            if old_store is None:
                os.environ.pop("BMANGA_LINE_PRESET_STORE_DIR", None)
            else:
                os.environ["BMANGA_LINE_PRESET_STORE_DIR"] = old_store


if __name__ == "__main__":
    main()
