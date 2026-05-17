"""Blender 実機(背景)用: ページ一覧カメラの自動生成確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_overview_camera",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_overview_camera"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    from bname_dev_overview_camera.utils import overview_camera, page_grid

    scene = bpy.context.scene
    work = scene.bname_work
    work.loaded = True
    work.paper.canvas_width_mm = 210.0
    work.paper.canvas_height_mm = 297.0
    work.paper.start_side = "right"
    work.paper.read_direction = "left"
    scene.bname_overview_cols = 4
    scene.bname_overview_gap_mm = 30.0
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080

    for idx in range(3):
        page = work.pages.add()
        page.id = f"p{idx + 1:04d}"
        page.title = f"{idx + 1}ページ"

    assert scene.camera is None or scene.camera.name != overview_camera.OVERVIEW_CAMERA_NAME
    page_grid.apply_page_collection_transforms(bpy.context, work)

    camera = scene.camera
    assert camera is not None, "ページ一覧用カメラが生成されていません"
    assert camera.name.startswith(overview_camera.OVERVIEW_CAMERA_NAME), camera.name
    assert camera.type == "CAMERA", camera.type
    assert camera.data.type == "ORTHO", camera.data.type
    assert bool(camera.get(overview_camera.PROP_OVERVIEW_CAMERA, False)), "ページ一覧用カメラとして識別できません"
    assert camera.hide_viewport is False and camera.hide_render is False

    bbox = overview_camera._visible_pages_bbox_mm(work, scene)
    assert bbox is not None
    x, y, w, h = bbox
    cx = x + w * 0.5
    cy = y + h * 0.5
    assert abs(camera.location.x - cx * 0.001) < 1.0e-6
    assert abs(camera.location.y - cy * 0.001) < 1.0e-6
    assert camera.data.ortho_scale >= h * 0.001

    scene.bname_overview_gap_mm = 8.0
    camera.rotation_euler = (0.0, 0.0, 0.4)
    page_grid.apply_page_collection_transforms(bpy.context, work)
    assert scene.camera is camera, "ページ一覧用カメラが再利用されていません"
    assert abs(float(camera.rotation_euler.z) - 0.4) < 1.0e-6, "ページ一覧用カメラの回転が更新時に失われています"
    assert camera.data.ortho_scale > 0.0

    print("BNAME_OVERVIEW_CAMERA_CHECK_OK")


main()
