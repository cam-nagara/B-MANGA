"""Blender 実機用: 魚眼 F1+F2 の最小回帰チェック."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _load_render_addon():
    package_root = ROOT / "addons" / "b_manga_render"
    spec = importlib.util.spec_from_file_location(
        "bmanga_render_dev_fisheye",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_render_dev_fisheye"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _FakePencil4Node:
    def __init__(self, size: float):
        self.name = "Brush Settings"
        self.size = size
        self._props = {}

    def __contains__(self, key):
        return key in self._props

    def __getitem__(self, key):
        return self._props[key]

    def __setitem__(self, key, value):
        self._props[key] = value


def _check_fisheye_panorama_sync(coma_camera) -> None:
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("bmanga_fisheye_test_camera")
    cam = bpy.data.objects.new("bmanga_fisheye_test_camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.bmanga_coma_camera_original_resolution_x = 800
    scene.bmanga_coma_camera_original_resolution_y = 600
    scene.bmanga_coma_camera_fisheye_layout_mode = True

    cam.data.type = "PANO"
    cam.data.panorama_type = "FISHEYE_EQUIDISTANT"
    coma_camera.apply_fisheye_mode(bpy.context)
    assert cam.data.panorama_type == "FISHEYE_EQUIDISTANT"

    cam.data.panorama_type = "EQUIRECTANGULAR"
    coma_camera.apply_fisheye_mode(bpy.context)
    assert cam.data.panorama_type == "FISHEYE_EQUISOLID"


def _check_pencil4_link(pencil4_link) -> None:
    node = _FakePencil4Node(8.0)
    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(
            node_groups=[
                types.SimpleNamespace(
                    name="Pencil+ 4 Line Node Tree",
                    nodes=[node],
                )
            ]
        )
    )
    real_bpy = pencil4_link.bpy
    pencil4_link.bpy = fake_bpy
    try:
        assert pencil4_link.save_widths() == 1
        node.size = 4.0
        assert pencil4_link.apply_scale(0.5) == 1
        assert node.size == 4.0
        assert pencil4_link.restore() == 1
        assert node.size == 8.0
    finally:
        pencil4_link.bpy = real_bpy


def _check_pencil4_save_operator_while_reduced(pencil4_link) -> None:
    node = _FakePencil4Node(4.0)
    node["original_size"] = 8.0
    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(
            node_groups=[
                types.SimpleNamespace(
                    name="Pencil+ 4 Line Node Tree",
                    nodes=[node],
                )
            ]
        )
    )
    scene = bpy.context.scene
    scene.bmanga_mode = "COMA"
    scene.bmanga_coma_camera_reduction_mode = True
    scene.bmanga_coma_camera_preview_scale_percentage = 50.0
    real_bpy = pencil4_link.bpy
    pencil4_link.bpy = fake_bpy
    try:
        result = bpy.ops.bmanga_render.save_pencil4_widths()
        assert result == {"FINISHED"}, result
        assert node["original_size"] == 8.0
        assert node.size == 4.0
    finally:
        pencil4_link.bpy = real_bpy


def main() -> None:
    mod = _load_addon()
    render = _load_render_addon()
    try:
        from bmanga_dev.core.fisheye import pencil4_link
        from bmanga_dev.utils import coma_camera

        _check_fisheye_panorama_sync(coma_camera)
        _check_pencil4_link(pencil4_link)
        _check_pencil4_save_operator_while_reduced(pencil4_link)
        print("BMANGA_FISHEYE_F1F2_OK")
    finally:
        render.unregister()
        mod.unregister()


if __name__ == "__main__":
    main()
