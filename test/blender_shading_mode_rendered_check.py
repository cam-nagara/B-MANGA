"""B-MANGA 作品を開いたとき、 ビューポートのシェーディングが RENDERED に
切り替わることを確認する.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --factory-startup --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_shading_mode_rendered_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_shading",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_shading"] = mod
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_initial_shading_to_solid():
    """factory startup 直後を SOLID にしてから work_new。 切替が起きるか確認。"""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.type = "SOLID"


def _get_shading_types() -> list[str]:
    out = []
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                space = area.spaces.active
                if space:
                    out.append(str(space.shading.type))
    return out


def main():
    _load_addon()
    _set_initial_shading_to_solid()
    print(f"  factory startup direct: {_get_shading_types()}")

    tmp = Path(tempfile.mkdtemp(prefix="bmanga_shading_"))
    res = bpy.ops.bmanga.work_new(filepath=str(tmp / "ShadingCheck.bmanga"))
    assert "FINISHED" in res, res

    print(f"  after work_new: {_get_shading_types()}")
    types = _get_shading_types()
    assert all(t == "RENDERED" for t in types), f"Expected all RENDERED, got {types}"
    print("  [OK] ページ一覧モードで RENDERED に切替された")


if __name__ == "__main__":
    main()
