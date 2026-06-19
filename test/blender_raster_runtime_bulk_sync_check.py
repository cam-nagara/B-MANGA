"""ラスター一括準備が表示更新と重なり順再計算を繰り返さないことを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_raster_bulk_sync",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_raster_bulk_sync"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_raster_bulk_sync_"))
    mod = None
    try:
        mod = _load_addon()
        if "FINISHED" not in bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "RasterBulkSync.bmanga")
        ):
            raise AssertionError("作品作成に失敗しました")

        from bmanga_dev_raster_bulk_sync.core.work import get_work
        from bmanga_dev_raster_bulk_sync.operators import raster_layer_op
        from bmanga_dev_raster_bulk_sync.utils import layer_object_sync, mask_apply

        scene = bpy.context.scene
        work = get_work(bpy.context)
        page_id = str(work.pages[0].id)
        scene.bmanga_raster_layers.clear()
        for index in range(4):
            entry = scene.bmanga_raster_layers.add()
            entry.id = f"bulk_raster_{index + 1}"
            entry.title = f"ラスター{index + 1}"
            entry.image_name = ""
            entry.filepath_rel = f"raster/bulk_raster_{index + 1}.png"
            entry.dpi = 30
            entry.parent_kind = "page"
            entry.parent_key = page_id

        calls = {"z": 0, "view_update": 0}
        original_z = layer_object_sync.assign_per_page_z_ranks
        original_update = mask_apply._update_view_layer_now

        def _count_z(*args, **kwargs):
            calls["z"] += 1
            return original_z(*args, **kwargs)

        def _count_update(*args, **kwargs):
            calls["view_update"] += 1
            return original_update(*args, **kwargs)

        layer_object_sync.assign_per_page_z_ranks = _count_z
        mask_apply._update_view_layer_now = _count_update
        try:
            count = raster_layer_op.ensure_all_raster_runtime(bpy.context)
        finally:
            layer_object_sync.assign_per_page_z_ranks = original_z
            mask_apply._update_view_layer_now = original_update

        if count != 4:
            raise AssertionError(f"ラスター準備数が不正です: {count}")
        if calls["z"] != 1:
            raise AssertionError(f"重なり順再計算が一括化されていません: {calls['z']}")
        if calls["view_update"] > 1:
            raise AssertionError(f"表示更新が一括化されていません: {calls['view_update']}")

        print("BMANGA_RASTER_RUNTIME_BULK_SYNC_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
