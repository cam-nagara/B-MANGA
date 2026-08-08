"""Blender実機: 本体・Render・Linerの同時register/unregisterだけを確認する。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_package(name: str, package_dir: Path):
    spec = importlib.util.spec_from_file_location(
        name,
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _assert_registered() -> None:
    assert hasattr(bpy.types.Scene, "bmanga_work")
    assert hasattr(bpy.types.Scene, "bmanga_render_state")
    assert hasattr(bpy.types.Object, "bmanga_line_settings")
    assert hasattr(bpy.types, "BMANGA_PT_work")
    assert hasattr(bpy.types, "BMANGA_RENDER_PT_main")
    assert hasattr(bpy.types, "BMANGA_LINE_PT_main")


def _assert_unregistered() -> None:
    assert not hasattr(bpy.types.Scene, "bmanga_work")
    assert not hasattr(bpy.types.Scene, "bmanga_render_state")
    assert not hasattr(bpy.types.Object, "bmanga_line_settings")
    assert not hasattr(bpy.types, "BMANGA_PT_work")
    assert not hasattr(bpy.types, "BMANGA_RENDER_PT_main")
    assert not hasattr(bpy.types, "BMANGA_LINE_PT_main")


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.view.show_splash = False
    modules = (
        _load_package("bmanga_r3_core", ROOT),
        _load_package("bmanga_r3_render", ROOT / "addons" / "b_manga_render"),
        _load_package("bmanga_r3_liner", ROOT / "addons" / "b_manga_line"),
    )
    registered = []
    try:
        for _cycle in range(2):
            for module in modules:
                module.register()
                registered.append(module)
            _assert_registered()
            for module in reversed(modules):
                module.unregister()
                registered.remove(module)
            _assert_unregistered()
    finally:
        for module in reversed(registered):
            try:
                module.unregister()
            except Exception:
                pass
    print("BMANGA_CROSS_PRODUCT_REGISTER_SMOKE_OK", flush=True)


if __name__ == "__main__":
    main()
