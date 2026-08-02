"""Blender 5.2実機: 新規作品の全Lifecycle phase失敗を元作品へ戻す。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_work_new_rollback"
SENTINEL = "BMANGA_WORK_NEW_TRANSITION_ROLLBACK_OK"


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


class _SuppressExpectedException:
    def __init__(self, logger):
        self._logger = logger

    def exception(self, *_args, **_kwargs) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._logger, name)


def _uid(owner, key: str) -> str:
    return str(owner.get(key, "") or "")


def _ensure_raster(context, raster_layer_op):
    scene = context.scene
    raster = scene.bmanga_raster_layers.add()
    raster.id = "raster_work_new_rollback"
    raster.title = "新規作品rollback"
    raster.parent_kind = "page"
    raster.parent_key = str(scene.bmanga_work.pages[0].id)
    raster.filepath_rel = f"raster/{raster.id}.png"
    raster.image_name = f"BManga_{raster.id}"
    raster.dpi = 72
    image = bpy.data.images.new(raster.image_name, 8, 8, alpha=True)
    image.pixels[:] = [0.1, 0.2, 0.3, 1.0] * 64
    image.update()
    raster_layer_op.mark_raster_dirty(raster)
    raster_layer_op.ensure_raster_plane(context, raster)
    return raster


def _set_unsaved_state(context, raster_layer_op, raster_id: str, value: float):
    work = context.scene.bmanga_work
    page = work.pages[int(work.active_page_index)]
    balloon = next(
        (
            entry
            for entry in page.balloons
            if str(entry.id) == "balloon_work_new_rollback"
        ),
        None,
    )
    if balloon is None:
        balloon = page.balloons.add()
        balloon.id = "balloon_work_new_rollback"
        balloon.title = "新規作品rollback"
        balloon.parent_kind = "page"
        balloon.parent_key = str(page.id)
    balloon.x_mm = value
    raster, _index = raster_layer_op.find_raster_entry(
        context.scene,
        raster_id,
    )
    assert raster is not None
    image = raster_layer_op.ensure_raster_image(
        context,
        raster,
        create_missing=False,
    )
    assert image is not None
    color = (value / 100.0, 0.25, 0.5, 1.0)
    image.pixels[:] = list(color) * (len(image.pixels) // 4)
    image.update()
    raster_layer_op.mark_raster_dirty(raster)
    return float(image.pixels[0]), float(balloon.x_mm)


def _assert_source_restored(
    source_path: Path,
    project_uid: str,
    page_uid: str,
    raster_layer_op,
    expected_red: float,
    expected_x: float,
) -> None:
    from bmanga_work_new_rollback.io import domain_projection_ids

    assert Path(bpy.data.filepath).resolve() == source_path.resolve()
    context = bpy.context
    scene = context.scene
    work = scene.bmanga_work
    assert work.loaded
    assert _uid(work, domain_projection_ids.PROJECT_UID_PROP) == project_uid
    page = work.pages[int(work.active_page_index)]
    assert _uid(page, domain_projection_ids.PAGE_UID_PROP) == page_uid
    assert str(scene.bmanga_current_page_id) == str(page.id)
    balloon = next(
        entry
        for entry in page.balloons
        if str(entry.id) == "balloon_work_new_rollback"
    )
    assert abs(float(balloon.x_mm) - expected_x) < 1.0e-6
    raster, _index = raster_layer_op.find_raster_entry(
        scene,
        "raster_work_new_rollback",
    )
    assert raster is not None
    image = raster_layer_op.ensure_raster_image(
        context,
        raster,
        create_missing=False,
    )
    assert image is not None
    assert abs(float(image.pixels[0]) - expected_red) < 5.0e-3


def _inject_phase_failure(
    lifecycle_coordinator,
    phase,
    action,
):
    original = lifecycle_coordinator.run_transition

    def _run(*args, **kwargs):
        original_hook = kwargs.get("phase_hook")

        def _hook(state):
            if original_hook is not None:
                original_hook(state)
            if state is phase:
                raise RuntimeError(f"injected work_new {phase.value}")

        kwargs["phase_hook"] = _hook
        return original(*args, **kwargs)

    lifecycle_coordinator.run_transition = _run
    original_logger = lifecycle_coordinator._logger
    lifecycle_coordinator._logger = _SuppressExpectedException(
        original_logger
    )
    try:
        return action()
    finally:
        lifecycle_coordinator.run_transition = original
        lifecycle_coordinator._logger = original_logger


def _assert_external_artifact_preserved(
    temp_root: Path,
    work_op,
    lifecycle_coordinator,
    raster_layer_op,
    source_path: Path,
    project_uid: str,
    page_uid: str,
    raster_id: str,
) -> None:
    expected_red, expected_x = _set_unsaved_state(
        bpy.context,
        raster_layer_op,
        raster_id,
        19.0,
    )
    target_dir = temp_root / "ExternalCollision.bmanga"
    external_path = target_dir / "external-owner.txt"
    replaced_path = target_dir / "project.json"
    replaced_bytes = b'{"externalReplacement":true}\n'
    original_open = work_op._open_new_work_target

    def _open_with_external(*args, **kwargs):
        assert original_open(*args, **kwargs) is True
        external_path.write_text("external owner data\n", encoding="utf-8")
        replaced_path.write_bytes(replaced_bytes)
        raise RuntimeError("injected external collision")

    work_op._open_new_work_target = _open_with_external
    original_logger = lifecycle_coordinator._logger
    lifecycle_coordinator._logger = _SuppressExpectedException(
        original_logger
    )
    try:
        try:
            result = bpy.ops.bmanga.work_new(
                "EXEC_DEFAULT",
                filepath=str(target_dir),
            )
        except RuntimeError as exc:
            assert "作成失敗" in str(exc), exc
            result = {"CANCELLED"}
        assert result == {"CANCELLED"}, result
    finally:
        work_op._open_new_work_target = original_open
        lifecycle_coordinator._logger = original_logger

    assert target_dir.is_dir()
    assert external_path.read_text(encoding="utf-8") == (
        "external owner data\n"
    )
    assert replaced_path.read_bytes() == replaced_bytes
    assert {path.name for path in target_dir.iterdir()} == {
        external_path.name,
        replaced_path.name,
    }
    _assert_source_restored(
        source_path,
        project_uid,
        page_uid,
        raster_layer_op,
        expected_red,
        expected_x,
    )


def _assert_planned_name_collision_preserved(
    temp_root: Path,
    work_op,
    lifecycle_coordinator,
    raster_layer_op,
    source_path: Path,
    project_uid: str,
    page_uid: str,
    raster_id: str,
) -> None:
    expected_red, expected_x = _set_unsaved_state(
        bpy.context,
        raster_layer_op,
        raster_id,
        20.0,
    )
    target_dir = temp_root / "PlannedNameCollision.bmanga"
    external_path = target_dir / "project.json"
    external_bytes = b'{"externalOwner":true}\n'
    original_create = work_op.work_io.create_bmanga_skeleton

    def _create_with_planned_collision(work_dir):
        original_create(work_dir)
        external_path.write_bytes(external_bytes)

    work_op.work_io.create_bmanga_skeleton = _create_with_planned_collision
    original_logger = lifecycle_coordinator._logger
    lifecycle_coordinator._logger = _SuppressExpectedException(
        original_logger
    )
    try:
        try:
            result = bpy.ops.bmanga.work_new(
                "EXEC_DEFAULT",
                filepath=str(target_dir),
            )
        except RuntimeError as exc:
            assert "作成失敗" in str(exc), exc
            result = {"CANCELLED"}
        assert result == {"CANCELLED"}, result
    finally:
        work_op.work_io.create_bmanga_skeleton = original_create
        lifecycle_coordinator._logger = original_logger

    assert target_dir.is_dir()
    assert external_path.read_bytes() == external_bytes
    assert {path.name for path in target_dir.iterdir()} == {
        external_path.name
    }
    _assert_source_restored(
        source_path,
        project_uid,
        page_uid,
        raster_layer_op,
        expected_red,
        expected_x,
    )


def _assert_post_commit_replacement_preserved(
    temp_root: Path,
    work_op,
    lifecycle_coordinator,
    raster_layer_op,
    source_path: Path,
    project_uid: str,
    page_uid: str,
    raster_id: str,
) -> None:
    expected_red, expected_x = _set_unsaved_state(
        bpy.context,
        raster_layer_op,
        raster_id,
        21.0,
    )
    target_dir = temp_root / "PostCommitReplacement.bmanga"
    external_path = target_dir / "project.json"
    external_bytes = b'{"externalAfterCommit":true}\n'
    original_save = work_op.work_io.save_work_json
    original_next = work_op.page_io.save_pages_json

    def _save_then_replace(*args, **kwargs):
        result = original_save(*args, **kwargs)
        external_path.write_bytes(external_bytes)
        return result

    def _fail_next_step(*_args, **_kwargs):
        raise RuntimeError("injected failure after external replacement")

    work_op.work_io.save_work_json = _save_then_replace
    work_op.page_io.save_pages_json = _fail_next_step
    original_logger = lifecycle_coordinator._logger
    lifecycle_coordinator._logger = _SuppressExpectedException(
        original_logger
    )
    try:
        try:
            result = bpy.ops.bmanga.work_new(
                "EXEC_DEFAULT",
                filepath=str(target_dir),
            )
        except RuntimeError as exc:
            assert "作成失敗" in str(exc), exc
            result = {"CANCELLED"}
        assert result == {"CANCELLED"}, result
    finally:
        work_op.work_io.save_work_json = original_save
        work_op.page_io.save_pages_json = original_next
        lifecycle_coordinator._logger = original_logger

    assert target_dir.is_dir()
    assert external_path.read_bytes() == external_bytes
    assert {path.name for path in target_dir.iterdir()} == {
        external_path.name
    }
    _assert_source_restored(
        source_path,
        project_uid,
        page_uid,
        raster_layer_op,
        expected_red,
        expected_x,
    )


def _assert_domain_install_boundary_preserved(
    temp_root: Path,
    work_op,
    lifecycle_coordinator,
    raster_layer_op,
    source_path: Path,
    project_uid: str,
    page_uid: str,
    raster_id: str,
) -> None:
    expected_red, expected_x = _set_unsaved_state(
        bpy.context,
        raster_layer_op,
        raster_id,
        22.0,
    )
    target_dir = temp_root / "DomainInstallBoundary.bmanga"
    external_path = target_dir / "project.json"
    external_bytes = b'{"externalDuringDomainCommit":true}\n'
    repository = work_op.work_io.domain_runtime.repository_for(target_dir)
    repository_type = type(repository)
    original_cleanup = repository_type._cleanup
    replaced = False

    def _cleanup_then_replace(self, *args, **kwargs):
        nonlocal replaced
        result = original_cleanup(self, *args, **kwargs)
        if (
            not replaced
            and Path(self.root).resolve() == target_dir.resolve()
            and external_path.is_file()
        ):
            external_path.write_bytes(external_bytes)
            replaced = True
        return result

    repository_type._cleanup = _cleanup_then_replace
    original_logger = lifecycle_coordinator._logger
    lifecycle_coordinator._logger = _SuppressExpectedException(
        original_logger
    )
    try:
        try:
            result = bpy.ops.bmanga.work_new(
                "EXEC_DEFAULT",
                filepath=str(target_dir),
            )
        except RuntimeError as exc:
            assert "作成失敗" in str(exc), exc
            result = {"CANCELLED"}
        assert result == {"CANCELLED"}, result
    finally:
        repository_type._cleanup = original_cleanup
        lifecycle_coordinator._logger = original_logger

    assert replaced
    assert target_dir.is_dir()
    assert external_path.read_bytes() == external_bytes
    assert {path.name for path in target_dir.iterdir()} == {
        external_path.name
    }
    _assert_source_restored(
        source_path,
        project_uid,
        page_uid,
        raster_layer_op,
        expected_red,
        expected_x,
    )


def _assert_blend_capture_boundary_preserved(
    temp_root: Path,
    work_op,
    lifecycle_coordinator,
    raster_layer_op,
    source_path: Path,
    project_uid: str,
    page_uid: str,
    raster_id: str,
) -> None:
    expected_red, expected_x = _set_unsaved_state(
        bpy.context,
        raster_layer_op,
        raster_id,
        23.0,
    )
    target_dir = temp_root / "BlendCaptureBoundary.bmanga"
    external_path = target_dir / "work.blend"
    external_bytes = b"external replacement during baseline hash\n"
    original_record = work_op.blend_io.record_successful_write
    replaced = False

    def _record_then_replace(path):
        nonlocal replaced
        result = original_record(path)
        if Path(path).resolve() == external_path.resolve():
            external_path.write_bytes(external_bytes)
            replaced = True
        return result

    work_op.blend_io.record_successful_write = _record_then_replace
    original_logger = lifecycle_coordinator._logger
    original_blend_logger = work_op.blend_io._logger
    lifecycle_coordinator._logger = _SuppressExpectedException(
        original_logger
    )
    work_op.blend_io._logger = _SuppressExpectedException(
        original_blend_logger
    )
    try:
        try:
            result = bpy.ops.bmanga.work_new(
                "EXEC_DEFAULT",
                filepath=str(target_dir),
            )
        except RuntimeError as exc:
            assert "作成失敗" in str(exc), exc
            result = {"CANCELLED"}
        assert result == {"CANCELLED"}, result
    finally:
        work_op.blend_io.record_successful_write = original_record
        lifecycle_coordinator._logger = original_logger
        work_op.blend_io._logger = original_blend_logger

    assert replaced
    assert target_dir.is_dir()
    assert external_path.read_bytes() == external_bytes
    assert {path.name for path in target_dir.iterdir()} == {
        external_path.name
    }
    _assert_source_restored(
        source_path,
        project_uid,
        page_uid,
        raster_layer_op,
        expected_red,
        expected_x,
    )


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_work_new_rollback_"))
    succeeded = False
    try:
        from bmanga_work_new_rollback.bmanga_core.lifecycle import (
            LifecycleState,
        )
        from bmanga_work_new_rollback.io import domain_projection_ids
        from bmanga_work_new_rollback.operators import (
            raster_layer_op,
            work_op,
        )
        from bmanga_work_new_rollback.utils import lifecycle_coordinator

        open_target = temp_root / "OpenTarget.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(open_target)) == {
            "FINISHED"
        }
        bpy.ops.wm.read_factory_settings(use_empty=True)

        # B-MANGA外の未保存Blender編集は、新規作成でも作品切替でも破棄しない。
        unrelated_path = temp_root / "Unrelated.blend"
        assert bpy.ops.wm.save_as_mainfile(
            filepath=str(unrelated_path),
            check_existing=False,
        ) == {"FINISHED"}
        assert bpy.ops.mesh.primitive_cube_add() == {"FINISHED"}
        unsaved = bpy.context.active_object
        assert unsaved is not None
        unsaved.name = "UnsavedSource"
        assert bpy.ops.ed.undo_push(message="Unsaved source edit") == {
            "FINISHED"
        }
        assert bpy.data.is_dirty
        try:
            bpy.ops.bmanga.work_open(
                "EXEC_DEFAULT",
                filepath=str(open_target / "work.blend"),
            )
        except RuntimeError as exc:
            assert "未保存の変更" in str(exc)
        else:
            raise AssertionError("dirty non-B-MANGA source was opened over")
        assert Path(bpy.data.filepath).resolve() == unrelated_path.resolve()
        assert bpy.data.objects.get("UnsavedSource") is not None
        assert bpy.data.is_dirty

        dirty_target = temp_root / "DirtySourceRejected.bmanga"
        try:
            bpy.ops.bmanga.work_new(
                "EXEC_DEFAULT",
                filepath=str(dirty_target),
            )
        except RuntimeError as exc:
            assert "未保存の変更" in str(exc)
        else:
            raise AssertionError("dirty non-B-MANGA source was discarded")
        assert bpy.data.objects.get("UnsavedSource") is not None
        assert not dirty_target.exists()
        bpy.ops.wm.read_factory_settings(use_empty=True)

        source_dir = temp_root / "Source.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(source_dir)) == {
            "FINISHED"
        }
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {
            "FINISHED"
        }
        context = bpy.context
        work = context.scene.bmanga_work
        project_uid = _uid(
            work,
            domain_projection_ids.PROJECT_UID_PROP,
        )
        page_uid = _uid(
            work.pages[int(work.active_page_index)],
            domain_projection_ids.PAGE_UID_PROP,
        )
        source_path = Path(bpy.data.filepath)
        raster = _ensure_raster(context, raster_layer_op)
        raster_id = str(raster.id)

        for number, phase in enumerate(
            (
                LifecycleState.PREPARING,
                LifecycleState.SAVING_SOURCE,
                LifecycleState.OPENING_TARGET,
                LifecycleState.HYDRATING,
            ),
            start=1,
        ):
            context = bpy.context
            expected_red, expected_x = _set_unsaved_state(
                context,
                raster_layer_op,
                raster_id,
                10.0 + number,
            )
            target_dir = temp_root / f"Failed{number}.bmanga"
            try:
                result = _inject_phase_failure(
                    lifecycle_coordinator,
                    phase,
                    lambda: bpy.ops.bmanga.work_new(
                        "EXEC_DEFAULT",
                        filepath=str(target_dir),
                    ),
                )
            except RuntimeError as exc:
                assert "作成失敗" in str(exc), (phase, exc)
                result = {"CANCELLED"}
            assert result == {"CANCELLED"}, (phase, result)
            assert not target_dir.exists(), phase
            _assert_source_restored(
                source_path,
                project_uid,
                page_uid,
                raster_layer_op,
                expected_red,
                expected_x,
            )

        _assert_external_artifact_preserved(
            temp_root,
            work_op,
            lifecycle_coordinator,
            raster_layer_op,
            source_path,
            project_uid,
            page_uid,
            raster_id,
        )
        _assert_planned_name_collision_preserved(
            temp_root,
            work_op,
            lifecycle_coordinator,
            raster_layer_op,
            source_path,
            project_uid,
            page_uid,
            raster_id,
        )
        _assert_post_commit_replacement_preserved(
            temp_root,
            work_op,
            lifecycle_coordinator,
            raster_layer_op,
            source_path,
            project_uid,
            page_uid,
            raster_id,
        )
        _assert_domain_install_boundary_preserved(
            temp_root,
            work_op,
            lifecycle_coordinator,
            raster_layer_op,
            source_path,
            project_uid,
            page_uid,
            raster_id,
        )
        _assert_blend_capture_boundary_preserved(
            temp_root,
            work_op,
            lifecycle_coordinator,
            raster_layer_op,
            source_path,
            project_uid,
            page_uid,
            raster_id,
        )

        retry_dir = temp_root / "Retry.bmanga"
        assert bpy.ops.bmanga.work_new(
            "EXEC_DEFAULT",
            filepath=str(retry_dir),
        ) == {"FINISHED"}
        assert Path(bpy.data.filepath).resolve() == (
            retry_dir / "work.blend"
        ).resolve()
        assert bpy.context.scene.bmanga_work.loaded
        assert not (retry_dir / ".bmanga-new-work-transaction").exists()

        succeeded = True
        print(SENTINEL, flush=True)
    finally:
        addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if succeeded:
            shutil.rmtree(temp_root)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}", flush=True)


if __name__ == "__main__":
    main()
