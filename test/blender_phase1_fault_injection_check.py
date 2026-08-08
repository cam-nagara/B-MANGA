"""現行JSON/asset/open/export adapterの共通失敗注入・復旧契約。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_phase1_fault"
SENTINEL = "BMANGA_PHASE1_FAULT_INJECTION_CHECK_OK"


def _load_package():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _expect_injected(callable_):
    from bmanga_phase1_fault.bmanga_core.faults import (
        FaultInjectedError,
        fault_snapshot,
    )

    try:
        result = callable_()
    except FaultInjectedError:
        return
    raise AssertionError(
        "armed fault point did not raise FaultInjectedError: "
        f"result={result!r}, faults={fault_snapshot()!r}"
    )


def main() -> None:
    module = _load_package()
    module.register()
    from bmanga_phase1_fault.bmanga_core.faults import (
        FaultPoint,
        arm_fault,
        fault_snapshot,
        isolated_faults,
    )
    from bmanga_phase1_fault.bmanga_core.observability import (
        observability_snapshot,
        reset_observability,
    )
    from bmanga_phase1_fault.io import blend_io
    from bmanga_phase1_fault.io import page_io, work_io
    from bmanga_phase1_fault.io.save_baseline import (
        capture_loaded_baseline,
        snapshot_baseline_registry,
    )
    from bmanga_phase1_fault.operators import io_op
    from bmanga_phase1_fault.utils import (
        asset_bundle,
        asset_instantiation_transaction,
        json_io,
        paths,
    )

    reset_observability()
    with isolated_faults(), tempfile.TemporaryDirectory(
        prefix="bmanga_phase1_fault_"
    ) as temp:
        root = Path(temp)
        json_path = root / "state.json"
        json_io.write_json(json_path, {"generation": 1})
        original = json_path.read_bytes()

        arm_fault(FaultPoint.JSON_WRITE)
        _expect_injected(lambda: json_io.write_json(json_path, {"generation": 2}))
        assert json_path.read_bytes() == original
        arm_fault(FaultPoint.JSON_WRITE_AFTER_STAGE)
        _expect_injected(lambda: json_io.write_json(json_path, {"generation": 2}))
        assert json_path.read_bytes() == original
        arm_fault(FaultPoint.JSON_WRITE_AFTER_COMMIT)
        _expect_injected(lambda: json_io.write_json(json_path, {"generation": 2}))
        assert json_path.read_bytes() == original
        json_io.write_json(json_path, {"generation": 2})
        assert json_io.read_json(json_path) == {"generation": 2}

        arm_fault(FaultPoint.JSON_READ)
        _expect_injected(lambda: json_io.read_json(json_path))
        assert json_io.read_json(json_path) == {"generation": 2}

        collection_name = "Phase1InjectedAsset"
        arm_fault(FaultPoint.ASSET_CREATE)
        _expect_injected(
            lambda: asset_bundle.create_collection_asset(
                bpy.context,
                {"name": collection_name, "entries": []},
            )
        )
        assert bpy.data.collections.get(collection_name) is None
        arm_fault(FaultPoint.ASSET_CREATE_AFTER_STAGE)
        _expect_injected(
            lambda: asset_bundle.create_collection_asset(
                bpy.context,
                {"name": collection_name, "entries": []},
            )
        )
        assert bpy.data.collections.get(collection_name) is None
        arm_fault(FaultPoint.ASSET_CREATE_AFTER_COMMIT)
        _expect_injected(
            lambda: asset_bundle.create_collection_asset(
                bpy.context,
                {"name": collection_name, "entries": []},
            )
        )
        assert bpy.data.collections.get(collection_name) is None
        bpy.ops.mesh.primitive_cube_add()
        preview_source = bpy.context.active_object
        assert preview_source is not None
        preview_source.name = "Phase1AssetPreviewSource"
        before_objects = set(bpy.data.objects.keys())
        before_meshes = set(bpy.data.meshes.keys())
        original_preview_objects = asset_bundle._preview_objects_for_payload
        try:
            asset_bundle._preview_objects_for_payload = (
                lambda _context, _payload: [preview_source]
            )
            arm_fault(FaultPoint.ASSET_CREATE_AFTER_COMMIT)
            _expect_injected(
                lambda: asset_bundle.create_collection_asset(
                    bpy.context,
                    {"name": collection_name, "entries": [{"kind": "image"}]},
                )
            )
        finally:
            asset_bundle._preview_objects_for_payload = original_preview_objects
        assert set(bpy.data.objects.keys()) == before_objects
        assert set(bpy.data.meshes.keys()) == before_meshes
        assert bpy.data.collections.get(collection_name) is None
        external_library = root / "external-assets"
        arm_fault(FaultPoint.ASSET_CREATE_AFTER_COMMIT)
        _expect_injected(
            lambda: asset_bundle.create_collection_asset(
                bpy.context,
                {"name": collection_name, "entries": []},
                target=asset_bundle.AssetBrowserTarget(
                    "PHASE1_TEST",
                    "",
                    str(external_library),
                    True,
                ),
            )
        )
        assert not list(external_library.glob("*.blend"))
        assert bpy.data.collections.get(collection_name) is None
        created_asset = asset_bundle.create_collection_asset(
            bpy.context,
            {"name": collection_name, "entries": []},
        )
        assert created_asset is not None
        bpy.data.collections.remove(created_asset, do_unlink=True)

        arm_fault(FaultPoint.ASSET_INSTANTIATE)
        _expect_injected(
            lambda: asset_bundle.instantiate_payload(
                bpy.context,
                {"name": "never-created", "entries": []},
            )
        )

        asset_work_dir = root / "AssetStage.bmanga"
        work = bpy.context.scene.bmanga_work
        work.loaded = True
        work.work_dir = str(asset_work_dir)
        work.pages.clear()
        page = work.pages.add()
        page.id = "p0001"
        page.in_page_range = True
        work.active_page_index = 0
        work_io.create_bmanga_skeleton(asset_work_dir)
        work_io.save_work_json(asset_work_dir, work)
        page_io.save_page_json(asset_work_dir, page)
        page_io.load_page_json(asset_work_dir, page)

        # 素材生成とrollbackの二重故障は、呼出側が通常の生成失敗と区別して
        # ページ全体snapshotを復元できる専用例外へ変換する。
        original_asset_rollback = asset_instantiation_transaction._rollback

        def fail_asset_rollback(*_args, **_kwargs):
            raise RuntimeError("phase5 injected asset rollback failure")

        asset_instantiation_transaction._rollback = fail_asset_rollback
        arm_fault(FaultPoint.ASSET_INSTANTIATE)
        try:
            asset_bundle.instantiate_payload(
                bpy.context,
                {"name": "rollback-failure", "entries": []},
                target_page=page,
                defer_to_page_file=False,
            )
        except asset_instantiation_transaction.AssetInstantiationRollbackError as exc:
            assert "rollback" in str(exc)
            assert isinstance(exc.operation_error, RuntimeError)
            assert isinstance(exc.rollback_error, RuntimeError)
        else:
            raise AssertionError("素材rollback二重故障が専用例外になりませんでした")
        finally:
            asset_instantiation_transaction._rollback = original_asset_rollback

        arm_fault(FaultPoint.ASSET_INSTANTIATE_AFTER_STAGE)
        _expect_injected(
            lambda: asset_bundle.instantiate_payload(
                bpy.context,
                {"name": "staged-then-rolled-back", "entries": []},
                target_page=page,
            )
        )
        from bmanga_phase1_fault.utils import cross_page_stage

        assert not cross_page_stage.staged_path(
            asset_work_dir,
            "p0001",
        ).exists()
        arm_fault(FaultPoint.ASSET_INSTANTIATE_AFTER_COMMIT)
        _expect_injected(
            lambda: asset_bundle.instantiate_payload(
                bpy.context,
                {"name": "committed-stage-then-rolled-back", "entries": []},
                target_page=page,
            )
        )
        assert not cross_page_stage.staged_path(
            asset_work_dir,
            "p0001",
        ).exists()
        before_folders = len(work.layer_folders)
        folder_payload = {
            "name": "folder-rollback",
            "origin": {"x": 0.0, "y": 0.0},
            "entries": [
                {
                    "kind": "layer_folder",
                    "source_id": "source-folder",
                    "data": {"id": "source-folder", "name": "一時フォルダ"},
                }
            ],
        }
        arm_fault(FaultPoint.ASSET_INSTANTIATE_AFTER_COMMIT)
        _expect_injected(
            lambda: asset_bundle.instantiate_payload(
                bpy.context,
                folder_payload,
                target_page=page,
                defer_to_page_file=False,
            )
        )
        assert len(work.layer_folders) == before_folders
        result = asset_bundle.instantiate_payload(
            bpy.context,
            folder_payload,
            target_page=page,
            defer_to_page_file=False,
        )
        assert result["created_new_count"] == 1
        assert len(work.layer_folders) == before_folders + 1

        # 複数レイヤー素材の後半が失敗しても、前半だけを残さない。
        before_balloons = len(page.balloons)
        before_rasters = len(bpy.context.scene.bmanga_raster_layers)
        before_page_json = paths.page_meta_path(
            asset_work_dir,
            "p0001",
        ).read_bytes()
        partial_payload = {
            "name": "partial-failure",
            "origin": {"x": 0.0, "y": 0.0},
            "entries": [
                {
                    "kind": "balloon",
                    "source_id": "source-balloon",
                    "data": {
                        "id": "source-balloon",
                        "shape": "ellipse",
                        "width_mm": 40.0,
                        "height_mm": 30.0,
                    },
                },
                {
                    "kind": "raster",
                    "source_id": "source-raster",
                    "data": {"id": "source-raster", "name": "壊れたラスター"},
                    "png_base64": "not-valid-base64***",
                },
            ],
        }
        try:
            asset_bundle.instantiate_payload(
                bpy.context,
                partial_payload,
                target_page=page,
                defer_to_page_file=False,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("partial asset failure was accepted")
        assert len(page.balloons) == before_balloons
        assert len(bpy.context.scene.bmanga_raster_layers) == before_rasters
        assert paths.page_meta_path(
            asset_work_dir,
            "p0001",
        ).read_bytes() == before_page_json

        # Domain checkpoint失敗も生成物を残さず、呼び出し元へ伝播する。
        persist_payload = {
            "name": "persist-failure",
            "origin": {"x": 0.0, "y": 0.0},
            "entries": [partial_payload["entries"][0]],
        }
        original_page_save = page_io.save_page_json
        page_io.save_page_json = lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(OSError("forced checkpoint failure"))
        )
        try:
            try:
                asset_bundle.instantiate_payload(
                    bpy.context,
                    persist_payload,
                    target_page=page,
                    defer_to_page_file=False,
                )
            except OSError:
                pass
            else:
                raise AssertionError("asset checkpoint failure was swallowed")
        finally:
            page_io.save_page_json = original_page_save
        assert len(page.balloons) == before_balloons

        # ドロップ元は全成功後だけ完了・削除する。失敗時は再試行可能に残す。
        drop_collection = bpy.data.collections.new("Phase1FailedDrop")
        drop_collection[asset_bundle.ASSET_KIND_PROP] = (
            asset_bundle.ASSET_KIND_LAYER_BUNDLE
        )
        drop_collection[asset_bundle.ASSET_PAYLOAD_PROP] = (
            '{"version":1,"entries":[{"kind":"raster",'
            '"png_base64":"not-valid-base64***"}]}'
        )
        drop_object = bpy.data.objects.new("Phase1FailedDropInstance", None)
        bpy.context.scene.collection.objects.link(drop_object)
        drop_object.instance_type = "COLLECTION"
        drop_object.instance_collection = drop_collection
        original_target_page = asset_bundle._is_target_page_file
        asset_bundle._is_target_page_file = lambda *_args, **_kwargs: True
        try:
            assert not asset_bundle.process_dropped_collection_instance(
                bpy.context,
                drop_object,
            )
        finally:
            asset_bundle._is_target_page_file = original_target_page
        assert bpy.data.objects.get(drop_object.name) is drop_object
        assert not bool(
            drop_object.get(asset_bundle.ASSET_INSTANCE_DONE_PROP, False)
        )
        bpy.data.objects.remove(drop_object, do_unlink=True)
        bpy.data.collections.remove(drop_collection, do_unlink=True)

        open_work_dir = asset_work_dir
        work_io.create_bmanga_skeleton(open_work_dir)
        work_io.save_work_json(open_work_dir, work)
        page_io.save_pages_json(open_work_dir, work)
        page_io.save_page_json(open_work_dir, page)
        source_blend = open_work_dir / "work.blend"
        blend_path = paths.page_blend_path(open_work_dir, "p0001")
        # 製品内の初回正規ファイル生成と同じ、保存先を明示許可するadapterを
        # 通す。直接Save Asは別の正規ファイルを誤上書きし得るため拒否対象。
        assert blend_io.save_work_blend(open_work_dir)
        blend_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_blend, blend_path)
        capture_loaded_baseline(open_work_dir, source_blend)
        baseline_before_open = snapshot_baseline_registry()
        arm_fault(FaultPoint.OPEN_MAINFILE)
        _expect_injected(lambda: blend_io.open_mainfile(blend_path))
        assert Path(bpy.data.filepath).resolve() == source_blend.resolve()
        assert snapshot_baseline_registry() == baseline_before_open
        arm_fault(FaultPoint.OPEN_MAINFILE_AFTER_STAGE)
        _expect_injected(lambda: blend_io.open_mainfile(blend_path))
        assert Path(bpy.data.filepath).resolve() == source_blend.resolve()
        assert snapshot_baseline_registry() == baseline_before_open
        arm_fault(FaultPoint.OPEN_MAINFILE_AFTER_COMMIT)
        _expect_injected(lambda: blend_io.open_mainfile(blend_path))
        assert Path(bpy.data.filepath).resolve() == source_blend.resolve()
        assert snapshot_baseline_registry() == baseline_before_open
        assert blend_io.open_mainfile(blend_path)
        assert Path(bpy.data.filepath).resolve() == blend_path.resolve()

        bpy.ops.wm.read_factory_settings(use_empty=True)
        unsaved_marker = bpy.data.objects.new("Phase1UnsavedRollbackMarker", None)
        bpy.context.scene.collection.objects.link(unsaved_marker)
        assert not bpy.data.filepath
        unsaved_baseline = snapshot_baseline_registry()
        arm_fault(FaultPoint.OPEN_MAINFILE_AFTER_COMMIT)
        _expect_injected(lambda: blend_io.open_mainfile(blend_path))
        recovered_path = Path(bpy.data.filepath)
        assert recovered_path.is_file()
        assert recovered_path.resolve() != blend_path.resolve()
        assert "bmanga-open-rollback-" in str(recovered_path.parent)
        assert bpy.data.objects.get("Phase1UnsavedRollbackMarker") is not None
        assert snapshot_baseline_registry() == unsaved_baseline

        bpy.ops.wm.read_factory_settings(use_empty=True)
        exception_marker = bpy.data.objects.new("Phase1ExceptionRollbackMarker", None)
        bpy.context.scene.collection.objects.link(exception_marker)
        assert not bpy.data.filepath
        exception_baseline = snapshot_baseline_registry()
        original_check_fault = blend_io.check_fault

        def _raise_regular_after_commit(point, **_context):
            if point == FaultPoint.OPEN_MAINFILE_AFTER_COMMIT:
                raise RuntimeError("regular open failure after commit")
            return original_check_fault(point, **_context)

        original_log_exception = blend_io._logger.exception
        try:
            blend_io.check_fault = _raise_regular_after_commit
            blend_io._logger.exception = lambda *_args, **_kwargs: None
            assert not blend_io.open_mainfile(blend_path)
        finally:
            blend_io.check_fault = original_check_fault
            blend_io._logger.exception = original_log_exception
        recovered_exception_path = Path(bpy.data.filepath)
        assert recovered_exception_path.is_file()
        assert recovered_exception_path.resolve() != blend_path.resolve()
        assert bpy.data.objects.get("Phase1ExceptionRollbackMarker") is not None
        assert snapshot_baseline_registry() == exception_baseline

        export_path = root / "never-written.png"
        arm_fault(FaultPoint.EXPORT_WRITE)
        _expect_injected(lambda: io_op._save_image(None, export_path, "png"))
        assert not export_path.exists()
        image = io_op.export_pipeline.Image.new("RGBA", (8, 8), (10, 20, 30, 255))
        arm_fault(FaultPoint.EXPORT_WRITE_AFTER_STAGE)
        _expect_injected(lambda: io_op._save_image(image, export_path, "png"))
        assert not export_path.exists()
        io_op._save_image(image, export_path, "png")
        original_export = export_path.read_bytes()
        arm_fault(FaultPoint.EXPORT_WRITE_AFTER_COMMIT)
        _expect_injected(lambda: io_op._save_image(image, export_path, "png"))
        assert export_path.read_bytes() == original_export

        faults = fault_snapshot()
        expected_points = {
            FaultPoint.JSON_READ.value,
            FaultPoint.JSON_WRITE.value,
            FaultPoint.JSON_WRITE_AFTER_STAGE.value,
            FaultPoint.JSON_WRITE_AFTER_COMMIT.value,
            FaultPoint.ASSET_CREATE.value,
            FaultPoint.ASSET_CREATE_AFTER_STAGE.value,
            FaultPoint.ASSET_CREATE_AFTER_COMMIT.value,
            FaultPoint.ASSET_INSTANTIATE.value,
            FaultPoint.ASSET_INSTANTIATE_AFTER_STAGE.value,
            FaultPoint.ASSET_INSTANTIATE_AFTER_COMMIT.value,
            FaultPoint.OPEN_MAINFILE.value,
            FaultPoint.OPEN_MAINFILE_AFTER_STAGE.value,
            FaultPoint.OPEN_MAINFILE_AFTER_COMMIT.value,
            FaultPoint.EXPORT_WRITE.value,
            FaultPoint.EXPORT_WRITE_AFTER_STAGE.value,
            FaultPoint.EXPORT_WRITE_AFTER_COMMIT.value,
        }
        assert expected_points <= set(faults["injections"])
        repeated_points = {
            FaultPoint.ASSET_CREATE_AFTER_COMMIT.value: 3,
            FaultPoint.ASSET_INSTANTIATE.value: 2,
            FaultPoint.ASSET_INSTANTIATE_AFTER_COMMIT.value: 2,
            FaultPoint.OPEN_MAINFILE_AFTER_COMMIT.value: 2,
        }
        assert all(
            faults["injections"][point] == repeated_points.get(point, 1)
            for point in expected_points
        )

    observed = observability_snapshot()
    counters = observed["counters"]
    assert counters["json.write.failure"] >= 3
    assert counters["json.write.success"] >= 2
    assert counters["json.read.failure"] == 1
    # 正常な open_mainfile は load_post 経由でも JSON を読むため、直接呼出し2回を下限にする。
    assert counters["json.read.success"] >= 2
    assert counters["asset.create.failure"] == 5
    assert counters["asset.create.success"] == 1
    assert counters["asset.instantiate.failure"] == 8
    assert counters["asset.instantiate.success"] == 1
    assert counters["open.mainfile.failure"] == 5
    assert counters["open.mainfile.success"] == 1
    assert counters["export.write.failure"] == 3
    assert counters["export.write.success"] == 1
    print(SENTINEL, flush=True)


if __name__ == "__main__":
    main()
