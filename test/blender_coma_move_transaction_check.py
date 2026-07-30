"""Blender実機用: コマのページ間移動が全失敗点で原子的に戻ることを検証。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_coma_move_transaction"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _page_by_id(work, page_id: str):
    return next(page for page in work.pages if str(page.id) == page_id)


def _memory_bytes(work) -> bytes:
    from bmanga_dev_coma_move_transaction.io import schema

    value = {
        "work": schema.work_to_dict(work),
        "pages": schema.pages_to_dict(work),
        "details": {
            str(page.id): schema.page_to_dict(page)
            for page in work.pages
        },
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _domain_bytes(work_dir: Path, page_ids: tuple[str, str]) -> dict[Path, bytes]:
    from bmanga_dev_coma_move_transaction.utils import paths

    candidates = [paths.project_meta_path(work_dir)]
    candidates.extend(paths.page_meta_path(work_dir, page_id) for page_id in page_ids)
    return {path: path.read_bytes() for path in candidates}


def _store_bytes(work_dir: Path) -> bytes:
    from bmanga_dev_coma_move_transaction.io import domain_runtime

    store = domain_runtime.store_for(work_dir)
    value = {
        "project": store.project.to_dict(),
        "pages": {
            uid: page.to_dict()
            for uid, page in sorted(store.pages.items())
        },
        "dirtyProject": store.dirty_project,
        "dirtyPages": sorted(store.dirty_page_uids),
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _assert_rollback(
    work,
    work_dir: Path,
    page_ids: tuple[str, str],
    *,
    expected_memory: bytes,
    expected_domain: dict[Path, bytes],
    expected_store: bytes,
    expected_baseline,
    source_dir: Path,
    destination_dir: Path,
) -> None:
    from bmanga_dev_coma_move_transaction.io.save_baseline import (
        snapshot_baseline_registry,
    )

    actual_memory = _memory_bytes(work)
    if actual_memory != expected_memory:
        expected_value = json.loads(expected_memory)
        actual_value = json.loads(actual_memory)
        print(
            "MEMORY_ROLLBACK_EXPECTED="
            + json.dumps(expected_value, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        print(
            "MEMORY_ROLLBACK_ACTUAL="
            + json.dumps(actual_value, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
    assert actual_memory == expected_memory, "PropertyGroupが元に戻っていません"
    assert _domain_bytes(work_dir, page_ids) == expected_domain, (
        "Domain JSONがバイト単位で元に戻っていません"
    )
    assert _store_bytes(work_dir) == expected_store, "Domain Storeが元に戻っていません"
    assert snapshot_baseline_registry() == expected_baseline, (
        "保存競合検知の基準が元に戻っていません"
    )
    assert source_dir.is_dir(), "移動元コマフォルダーが復元されていません"
    assert (source_dir / "scene.blend").read_bytes() == b"coma-native-sentinel"
    assert not destination_dir.exists(), "移動先コマフォルダーが残っています"
    source = _page_by_id(work, page_ids[0])
    target = _page_by_id(work, page_ids[1])
    assert [str(coma.coma_id) for coma in source.comas] == ["c01"]
    assert not target.comas and not target.detail_loaded
    assert int(target.coma_count) == 1
    assert not target.balloons
    images = {
        str(entry.id): str(entry.parent_key)
        for entry in bpy.context.scene.bmanga_image_layers
    }
    if images != {
        "source_image": f"{page_ids[0]}:c01",
        "target_image": f"{page_ids[1]}:c01",
    }:
        print("IMAGE_ROLLBACK_ACTUAL=" + repr(images), flush=True)
    assert images == {
        "source_image": f"{page_ids[0]}:c01",
        "target_image": f"{page_ids[1]}:c01",
    }


def _prepare_work(temp_root: Path):
    from bmanga_dev_coma_move_transaction.bmanga_core.domain_ids import (
        UIDKind,
        derived_uid,
    )
    from bmanga_dev_coma_move_transaction.io import (
        domain_projection,
        domain_projection_ids,
        page_io,
        schema,
    )
    from bmanga_dev_coma_move_transaction.io.save_baseline import (
        record_successful_tree_change,
    )
    from bmanga_dev_coma_move_transaction.utils import paths

    work_dir = temp_root / "ComaMoveTransaction.bmanga"
    assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
    assert bpy.ops.bmanga.page_add() == {"FINISHED"}
    work = bpy.context.scene.bmanga_work
    source = _page_by_id(work, "p0001")
    target = _page_by_id(work, "p0002")
    if not bool(source.detail_loaded):
        page_io.load_page_json(work_dir, source)
    if not bool(target.detail_loaded):
        page_io.load_page_json(work_dir, target)
    source = _page_by_id(work, "p0001")
    target = _page_by_id(work, "p0002")
    from bmanga_dev_coma_move_transaction.operators import coma_op

    if not source.comas:
        coma_op.create_basic_frame_coma(work, source, work_dir)
    if not target.comas:
        coma_op.create_basic_frame_coma(work, target, work_dir)
    source = _page_by_id(work, "p0001")
    target = _page_by_id(work, "p0002")
    assert len(source.comas) == 1 and len(target.comas) == 1, (
        len(source.comas),
        len(target.comas),
    )
    source.active_coma_index = 0
    work.active_page_index = 0
    with schema._suspend_load_property_side_effects():
        source_image = bpy.context.scene.bmanga_image_layers.add()
        source_image.id = "source_image"
        source_image.parent_kind = "coma"
        source_image.parent_key = "p0001:c01"
        target_image = bpy.context.scene.bmanga_image_layers.add()
        target_image.id = "target_image"
        target_image.parent_kind = "coma"
        target_image.parent_key = "p0002:c01"
    page_io.save_page_json(work_dir, source)
    page_io.save_page_json(work_dir, target)
    from bmanga_dev_coma_move_transaction.utils import page_detail

    page_detail.clear_page_detail(target)
    source = _page_by_id(work, "p0001")
    target = _page_by_id(work, "p0002")
    assert source.detail_loaded and not target.detail_loaded

    project_uid = domain_projection.ensure_project_uid(work)
    source_page_uid = domain_projection.ensure_page_uid(source, project_uid)
    target_page_uid = domain_projection.ensure_page_uid(target, project_uid)
    source_coma_uid = domain_projection_ids.ensure_coma_uid(
        source.comas[0],
        source_page_uid,
    )
    destination_coma_uid = derived_uid(UIDKind.COMA, target_page_uid, "c02")
    source_dir = (
        work_dir / paths.PAGES_DIR_NAME / source_page_uid
        / paths.COMAS_DIR_NAME / source_coma_uid
    )
    destination_dir = (
        work_dir / paths.PAGES_DIR_NAME / target_page_uid
        / paths.COMAS_DIR_NAME / destination_coma_uid
    )
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "scene.blend").write_bytes(b"coma-native-sentinel")
    record_successful_tree_change(source_dir)
    return work, work_dir, source_dir, destination_dir


def _exercise_process_crash_recovery(
    temp_root: Path,
    work,
    work_dir: Path,
    source_dir: Path,
    destination_dir: Path,
):
    """移動先公開直後に別Blender processを終了し、再開可能性を検証する。"""

    from bmanga_dev_coma_move_transaction.io import (
        coma_move_recovery,
        domain_runtime,
    )

    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
        filepath=str(work_dir / "work.blend"),
        check_existing=False,
    )
    child_script = temp_root / "coma_move_process_crash_child.py"
    child_script.write_text(
        """
import importlib.util
import os
from pathlib import Path
import sys
import bpy

root = Path(os.environ["BMANGA_COMA_MOVE_ROOT"])
work_dir = Path(os.environ["BMANGA_COMA_MOVE_WORK"])
name = "bmanga_dev_coma_move_transaction"
spec = importlib.util.spec_from_file_location(
    name,
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
assert spec.loader is not None
spec.loader.exec_module(module)
module.register()
assert "FINISHED" in bpy.ops.wm.open_mainfile(
    filepath=str(work_dir / "work.blend"),
    load_ui=False,
)
work = bpy.context.scene.bmanga_work
source = next(page for page in work.pages if str(page.id) == "p0001")
target = next(page for page in work.pages if str(page.id) == "p0002")
if not bool(source.detail_loaded):
    from bmanga_dev_coma_move_transaction.io import page_io
    page_io.load_page_json(work_dir, source)

from bmanga_dev_coma_move_transaction.io import coma_move_transaction

def crash_after_publish(phase):
    if phase == "after_directory_move":
        os._exit(79)

coma_move_transaction.move_coma_to_page(
    bpy.context,
    work,
    source,
    target,
    0,
    fault_hook=crash_after_publish,
)
os._exit(78)
""".lstrip(),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "BMANGA_COMA_MOVE_ROOT": str(ROOT),
            "BMANGA_COMA_MOVE_WORK": str(work_dir),
        }
    )
    completed = subprocess.run(
        [
            bpy.app.binary_path,
            "--background",
            "--factory-startup",
            "--python",
            str(child_script),
        ],
        cwd=str(ROOT),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    output = completed.stdout + "\n" + completed.stderr
    assert completed.returncode == 79, output
    assert source_dir.is_dir()
    assert destination_dir.is_dir()
    assert (destination_dir / coma_move_recovery.MARKER_FILE_NAME).is_file()
    repository = domain_runtime.repository_for(work_dir)
    assert any(repository.journal_dir.glob("checkpoint-*.json"))
    repository.recover()
    recovered = coma_move_recovery.recover_interrupted_coma_moves(
        work_dir,
        repository=repository,
    )
    assert recovered == (source_dir,)
    assert source_dir.is_dir()
    assert not destination_dir.exists()
    assert not any(repository.journal_dir.glob("checkpoint-*.json"))
    assert not any(
        work_dir.rglob(coma_move_recovery.MARKER_FILE_NAME)
    )
    refreshed_work = bpy.context.scene.bmanga_work
    assert bool(refreshed_work.loaded)
    from bmanga_dev_coma_move_transaction.io import page_io
    from bmanga_dev_coma_move_transaction.utils import page_detail

    source = _page_by_id(refreshed_work, "p0001")
    target = _page_by_id(refreshed_work, "p0002")
    if not bool(source.detail_loaded):
        page_io.load_page_json(work_dir, source)
    if not bool(target.detail_loaded):
        page_io.load_page_json(work_dir, target)
    page_detail.clear_page_detail(target)
    if not any(
        str(entry.id) == "source_image"
        for entry in bpy.context.scene.bmanga_image_layers
    ):
        from bmanga_dev_coma_move_transaction.io import schema

        with schema._suspend_load_property_side_effects():
            source_image = bpy.context.scene.bmanga_image_layers.add()
            source_image.id = "source_image"
            source_image.parent_kind = "coma"
            source_image.parent_key = "p0001:c01"
    assert source.detail_loaded and not target.detail_loaded
    return refreshed_work


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_move_tx_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        work, work_dir, source_dir, destination_dir = _prepare_work(temp_root)
        page_ids = ("p0001", "p0002")
        work = _exercise_process_crash_recovery(
            temp_root,
            work,
            work_dir,
            source_dir,
            destination_dir,
        )

        from bmanga_dev_coma_move_transaction.io import coma_move_transaction
        from bmanga_dev_coma_move_transaction.io.save_baseline import (
            snapshot_baseline_registry,
        )

        expected_memory = _memory_bytes(work)
        expected_domain = _domain_bytes(work_dir, page_ids)
        expected_store = _store_bytes(work_dir)
        expected_baseline = snapshot_baseline_registry()
        phases = (
            "after_effect_translation",
            "after_gp_reparent",
            "after_effect_reparent",
            "after_child_projection",
            "before_checkpoint",
            "after_directory_move",
            "repository:INSTALLING:1",
            "repository:COMMITTED:3",
        )
        for phase in phases:
            source = _page_by_id(work, page_ids[0])
            target = _page_by_id(work, page_ids[1])

            def fail_here(current: str, *, expected: str = phase) -> None:
                if current == expected:
                    raise RuntimeError(f"forced failure: {expected}")

            try:
                coma_move_transaction.move_coma_to_page(
                    bpy.context,
                    work,
                    source,
                    target,
                    0,
                    fault_hook=fail_here,
                )
            except RuntimeError as exc:
                assert "forced failure" in str(exc), (phase, exc)
            else:
                raise AssertionError(f"強制失敗が発生しませんでした: {phase}")
            _assert_rollback(
                work,
                work_dir,
                page_ids,
                expected_memory=expected_memory,
                expected_domain=expected_domain,
                expected_store=expected_store,
                expected_baseline=expected_baseline,
                source_dir=source_dir,
                destination_dir=destination_dir,
            )

        source = _page_by_id(work, page_ids[0])
        target = _page_by_id(work, page_ids[1])
        result = coma_move_transaction.move_coma_to_page(
            bpy.context,
            work,
            source,
            target,
            0,
        )
        assert result == "c02"
        assert not source_dir.exists()
        assert destination_dir.is_dir()
        from bmanga_dev_coma_move_transaction.io import coma_move_recovery

        assert not (
            destination_dir / coma_move_recovery.MARKER_FILE_NAME
        ).exists()
        assert (destination_dir / "scene.blend").read_bytes() == b"coma-native-sentinel"
        source = _page_by_id(work, page_ids[0])
        target = _page_by_id(work, page_ids[1])
        assert not source.comas
        assert not target.comas and not target.detail_loaded
        assert int(target.coma_count) == 2
        assert not source.balloons
        assert not target.balloons
        images = {
            str(entry.id): str(entry.parent_key)
            for entry in bpy.context.scene.bmanga_image_layers
        }
        assert images == {
            "source_image": f"{page_ids[1]}:c02",
            "target_image": f"{page_ids[1]}:c01",
        }
        from bmanga_dev_coma_move_transaction.io import domain_runtime

        repository = domain_runtime.repository_for(work_dir)
        source_document = repository.load_page(
            next(
                page.uid
                for page in repository.load_project().pages
                if page.display_id == page_ids[0]
            )
        )
        target_document = repository.load_page(
            next(
                page.uid
                for page in repository.load_project().pages
                if page.display_id == page_ids[1]
            )
        )
        assert not any(
            node.kind == "image" for node in source_document.nodes.values()
        )
        assert {
            node.display_id
            for node in target_document.nodes.values()
            if node.kind == "image"
        } == {"source_image", "target_image"}
        print("BMANGA_COMA_MOVE_TRANSACTION_OK", flush=True)
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:  # noqa: BLE001
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    import traceback

    try:
        main()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        os._exit(1)
