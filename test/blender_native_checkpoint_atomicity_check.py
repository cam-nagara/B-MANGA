"""Blender 5.2実機: JSON/PNG/native blendを同じcheckpoint成否で確定する。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zlib
from array import array
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_native_checkpoint_atomicity"
SENTINEL = "BMANGA_NATIVE_CHECKPOINT_ATOMICITY_OK"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hashes(paths: tuple[Path, ...]) -> dict[str, str]:
    return {str(path): _sha256(path) for path in paths}


def _page_node_x(document, kind: str, display_id: str) -> float:
    node = next(
        value
        for value in document.nodes.values()
        if value.kind == kind and value.display_id == display_id
    )
    return float(node.settings["xMm"])


def _add_raster(scene, raster_layer_op, raster_id: str, color):
    raster = scene.bmanga_raster_layers.add()
    raster.id = raster_id
    raster.title = raster_id
    raster.parent_kind = "page"
    raster.parent_key = "p0001"
    raster.filepath_rel = f"raster/{raster_id}.png"
    raster.image_name = f"BManga_{raster_id}"
    raster.dpi = 72
    image = bpy.data.images.new(
        raster.image_name,
        width=2,
        height=2,
        alpha=True,
    )
    image.pixels[:] = list(color) * 4
    image.update()
    raster_layer_op.mark_raster_dirty(raster)
    return raster, image


def _replace_pixels(raster_layer_op, raster, color):
    image = raster_layer_op.ensure_raster_image(
        bpy.context,
        raster,
        create_missing=False,
    )
    assert image is not None
    image.pixels[:] = list(color) * 4
    image.update()
    raster_layer_op.mark_raster_dirty(raster)
    return image


def _assert_red_pixel(image, expected: float) -> None:
    # 8bit PNG往復の1段階量子化を許容する。
    actual = float(image.pixels[0])
    assert abs(actual - expected) < 5.0e-3, (actual, expected)


def _snapshot_red(snapshot) -> float:
    pixels = array("f")
    pixels.frombytes(
        zlib.decompress(snapshot.compressed_path.read_bytes())
    )
    assert len(pixels) == (
        snapshot.width * snapshot.height * snapshot.channels
    )
    return float(pixels[0])


class _SuppressExpectedExceptionTrace:
    def __init__(self, logger):
        self._logger = logger

    def exception(self, *_args, **_kwargs) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._logger, name)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_native_checkpoint_"))
    succeeded = False
    handlers = None
    try:
        from bmanga_native_checkpoint_atomicity.io import (
            domain_projection,
            domain_runtime,
            native_checkpoint_runtime,
        )
        from bmanga_native_checkpoint_atomicity.operators import raster_layer_op
        from bmanga_native_checkpoint_atomicity.utils import (
            handlers,
            lifecycle_checkpoint,
        )

        work_dir = temp_root / "Atomic.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
        assert bpy.ops.bmanga.open_page_file(index=0) == {"FINISHED"}
        scene = bpy.context.scene
        work = scene.bmanga_work
        page = work.pages[0]
        page.detail_loaded = True

        balloon = page.balloons.add()
        balloon.id = "balloon_native_atomic"
        balloon.title = "Native Atomic"
        balloon.x_mm = 24.0
        balloon.y_mm = 30.0
        balloon.width_mm = 40.0
        balloon.height_mm = 25.0
        balloon.parent_kind = "page"
        balloon.parent_key = str(page.id)

        raster, image = _add_raster(
            scene,
            raster_layer_op,
            "raster_native_atomic",
            (0.1, 0.2, 0.3, 1.0),
        )
        partial_raster, partial_image = _add_raster(
            scene,
            raster_layer_op,
            "raster_partial_failure",
            (0.2, 0.3, 0.4, 1.0),
        )

        initial = lifecycle_checkpoint.checkpoint_current(
            bpy.context,
            reason="atomicity baseline",
            force_native=True,
        )
        assert initial.succeeded, initial
        project_uid = domain_projection.ensure_project_uid(work)
        page_uid = domain_projection.ensure_page_uid(page, project_uid)
        repository = domain_runtime.repository_for(work_dir)
        store = domain_runtime.store_for(work_dir)
        page_path = repository.page_path(page_uid)
        raster_path = work_dir / raster.filepath_rel
        partial_raster_path = work_dir / partial_raster.filepath_rel
        blend_path = Path(bpy.data.filepath)
        tracked = (
            repository.project_path,
            page_path,
            raster_path,
            partial_raster_path,
            blend_path,
        )
        before = _hashes(tracked)
        assert not store.dirty_project
        assert not store.dirty_page_uids
        assert not bool(raster.get("bmanga_raster_dirty", False))
        assert not bool(partial_raster.get("bmanga_raster_dirty", False))

        # 復旧journalのhashを検証する前にImage.scaleしてはならない。
        # 用紙仕様上は正しい寸法でもpayloadが不正なら現在Imageを維持する。
        malicious_width, malicious_height = raster_layer_op._raster_size_px(
            work,
            int(raster.dpi),
        )
        malicious_path = temp_root / "malicious-raster.pixels.zlib"
        malicious_path.write_bytes(zlib.compress(b"not enough pixels"))
        malicious_snapshot = native_checkpoint_runtime.RasterPixelSnapshot(
            malicious_width,
            malicious_height,
            4,
            malicious_path,
            "0" * 64,
            malicious_width * malicious_height * 4 * 4,
        )
        malicious_pending = (
            native_checkpoint_runtime.PendingNativeCheckpoint(
                work_dir,
                (),
                (raster.id,),
                raster_snapshots={raster.id: malicious_snapshot},
            )
        )
        original_image_size = tuple(image.size)
        handlers_logger = handlers._logger
        handlers._logger = _SuppressExpectedExceptionTrace(
            handlers_logger
        )
        try:
            try:
                handlers._restore_pending_raster_pixels(
                    malicious_pending,
                    strict=True,
                )
            except RuntimeError as exc:
                assert "未保存ラスター画素" in str(exc)
            else:
                raise AssertionError("invalid raster digest was accepted")
        finally:
            handlers._logger = handlers_logger
            malicious_path.unlink(missing_ok=True)
        assert tuple(image.size) == original_image_size

        # 1枚目のPNG確定後に2枚目だけ失敗しても、両方の未保存画素を
        # 書込み前payloadから復元し、旧ディスク世代を公開し続ける。
        balloon.x_mm = 27.0
        image = _replace_pixels(
            raster_layer_op,
            raster,
            (0.7, 0.3, 0.1, 1.0),
        )
        partial_image = _replace_pixels(
            raster_layer_op,
            partial_raster,
            (0.6, 0.2, 0.1, 1.0),
        )
        expected_red = float(image.pixels[0])
        expected_partial_red = float(partial_image.pixels[0])
        original_save_raster = raster_layer_op.save_raster_png

        def _fail_second_raster(context, entry, *, force=False):
            if str(getattr(entry, "id", "") or "") == partial_raster.id:
                raise OSError("injected second raster failure")
            return original_save_raster(context, entry, force=force)

        assert handlers._begin_native_save_guard(str(blend_path)) is True
        handlers._prepare_native_save_sidecars()
        assert native_checkpoint_runtime.is_pending(work_dir)
        pending = native_checkpoint_runtime.pending_for(work_dir)
        assert pending is not None
        partial_snapshot_dir = pending.snapshot_dir
        assert partial_snapshot_dir is not None
        assert partial_snapshot_dir.is_dir()
        snapshot_red = _snapshot_red(pending.raster_snapshots[raster.id])
        assert abs(snapshot_red - expected_red) < 1.0e-7, snapshot_red
        partial_snapshot_red = _snapshot_red(
            pending.raster_snapshots[partial_raster.id]
        )
        assert (
            abs(partial_snapshot_red - expected_partial_red) < 1.0e-7
        ), partial_snapshot_red
        raster_layer_op.save_raster_png = _fail_second_raster
        raster_logger = raster_layer_op._logger
        handlers_logger = handlers._logger
        raster_layer_op._logger = _SuppressExpectedExceptionTrace(
            raster_logger
        )
        handlers._logger = _SuppressExpectedExceptionTrace(handlers_logger)
        try:
            assert not handlers.save_scene_work_to_disk(
                bpy.context,
                reason="injected partial raster failure",
                strict_rasters=True,
                refresh_runtime=False,
            )
        finally:
            raster_layer_op.save_raster_png = original_save_raster
            raster_layer_op._logger = raster_logger
            handlers._logger = handlers_logger
        handlers._mark_native_save_metadata_result(False)
        blend_path.write_bytes(b"injected partial raster generation")
        partial_result, _source = handlers._finish_native_save_guard(
            native_save_succeeded=False,
        )
        assert partial_result.restored
        assert _hashes(tracked) == before
        assert not store.dirty_page_uids
        assert bool(raster.get("bmanga_raster_dirty", False))
        assert bool(partial_raster.get("bmanga_raster_dirty", False))
        _assert_red_pixel(image, 0.7)
        _assert_red_pixel(partial_image, 0.6)
        assert not native_checkpoint_runtime.is_pending(work_dir)
        assert not partial_snapshot_dir.exists()

        partial_retry = lifecycle_checkpoint.checkpoint_current(
            bpy.context,
            reason="partial raster retry",
            force_native=True,
        )
        assert partial_retry.succeeded, partial_retry
        assert not bool(raster.get("bmanga_raster_dirty", False))
        assert not bool(partial_raster.get("bmanga_raster_dirty", False))
        assert _page_node_x(
            repository.load_page(page_uid),
            "balloon",
            "balloon_native_atomic",
        ) == 27.0

        # PNG/JSONが全件成功してからnativeだけ失敗する場合も同じ契約。
        before = _hashes(tracked)
        balloon.x_mm = 31.0
        image = _replace_pixels(
            raster_layer_op,
            raster,
            (0.8, 0.4, 0.2, 1.0),
        )

        assert handlers._begin_native_save_guard(str(blend_path)) is True
        handlers._prepare_native_save_sidecars()
        assert native_checkpoint_runtime.is_pending(work_dir)
        native_pending = native_checkpoint_runtime.pending_for(work_dir)
        assert native_pending is not None
        native_snapshot_dir = native_pending.snapshot_dir
        assert native_snapshot_dir is not None
        assert native_snapshot_dir.is_dir()
        assert handlers.save_scene_work_to_disk(
            bpy.context,
            reason="injected native failure",
            strict_rasters=True,
            refresh_runtime=False,
        )
        handlers._mark_native_save_metadata_result(True)
        blend_path.write_bytes(b"injected incomplete native generation")
        result, _source = handlers._finish_native_save_guard(
            native_save_succeeded=False,
        )
        assert result.restored
        assert _hashes(tracked) == before
        assert page_uid in store.dirty_page_uids
        assert bool(raster.get("bmanga_raster_dirty", False))
        _assert_red_pixel(image, 0.8)
        assert not native_checkpoint_runtime.is_pending(work_dir)
        assert not native_snapshot_dir.exists()

        retry = lifecycle_checkpoint.checkpoint_current(
            bpy.context,
            reason="atomicity retry",
            force_native=True,
        )
        assert retry.succeeded, retry
        assert not store.dirty_project
        assert page_uid not in store.dirty_page_uids
        assert not bool(raster.get("bmanga_raster_dirty", False))
        assert _sha256(page_path) != before[str(page_path)]
        assert _sha256(raster_path) != before[str(raster_path)]
        document = repository.load_page(page_uid)
        assert _page_node_x(
            document,
            "balloon",
            "balloon_native_atomic",
        ) == 31.0
        payload = json.loads(repository.project_path.read_text(encoding="utf-8"))
        assert payload["projectUid"] == project_uid

        succeeded = True
        print(SENTINEL, flush=True)
    finally:
        if handlers is not None and handlers._native_save_token is not None:
            handlers._finish_native_save_guard(native_save_succeeded=False)
        addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}", flush=True)


if __name__ == "__main__":
    main()
