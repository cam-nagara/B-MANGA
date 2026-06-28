"""Blender実機用: B-MANGA Lineの再有効化登録を確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "addons" / "b_manga_line"


def _load_package(package_name: str):
    spec = importlib.util.spec_from_file_location(
        package_name,
        PACKAGE_ROOT / "__init__.py",
        submodule_search_locations=[str(PACKAGE_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _assert_registered() -> None:
    from b_manga_line_reenable_check import core

    assert bool(getattr(core.BMangaLineSettings, "is_registered", False))
    assert getattr(bpy.types.Object, "bmanga_line_settings", None) is not None
    assert getattr(bpy.types.Scene, "bmanga_line_camera", None) is not None
    assert getattr(bpy.types, "BMANGA_LINE_PT_main", None) is not None


def _assert_unregistered() -> None:
    from b_manga_line_reenable_check import core

    assert not bool(getattr(core.BMangaLineSettings, "is_registered", False))
    assert getattr(bpy.types.Object, "bmanga_line_settings", None) is None
    assert getattr(bpy.types.Scene, "bmanga_line_camera", None) is None


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_package("b_manga_line_reenable_check")
    try:
        mod.register()
        _assert_registered()
        mod.register()
        _assert_registered()
        mod.unregister()
        _assert_unregistered()
        mod.register()
        _assert_registered()
        print("BMANGA_LINE_REGISTER_REENABLE_OK")
    finally:
        try:
            mod.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
