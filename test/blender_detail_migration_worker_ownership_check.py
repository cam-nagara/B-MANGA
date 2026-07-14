"""Blender 5.1実機: 移行ワーカーが親トランザクションを自己復旧しない。

1ページの生成作品を実際の子Blender validatorで stage／installed／再起動相当
検証し、未完了ジャーナルがload_postからrollbackされないことを確認する。
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_detail_migration_worker_ownership_test"


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


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _create_work(root: Path) -> Path:
    work = root / "WorkerOwnershipProbe.bmanga"
    page = work / "p0001" / "page.blend"
    page.parent.mkdir(parents=True)
    _write_json(
        work / "work.json",
        {"schemaVersion": 9, "detailDataVersion": 0, "title": "所有境界テスト"},
    )
    _write_json(
        work / "pages.json",
        {
            "schemaVersion": 2,
            "pages": [{"id": "p0001", "title": "1ページ", "dirRel": "p0001"}],
        },
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene["bmanga_worker_ownership_probe"] = True
    runtime = scene.bmanga_work
    runtime.loaded = True
    runtime.work_dir = str(work)
    runtime.detail_data_version = 0
    page_entry = runtime.pages.add()
    page_entry.id = "p0001"
    page_entry.title = "1ページ"
    page_entry.dir_rel = "p0001"
    runtime.active_page_index = 0
    baseline = importlib.import_module(
        f"{MODULE_NAME}.io.project_content_save_baseline"
    )
    baseline.capture_loaded_baseline(
        work,
        page,
        page_json_paths=(work / "p0001" / "page.json",),
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
    _write_json(work / "p0001" / "page.json", {"pageId": "p0001", "sentinel": True})
    raster = work / "p0001" / "raster" / "source-pixels.bin"
    raster.parent.mkdir(parents=True)
    raster.write_bytes(b"ORIGINAL_RASTER_BYTES\x00\x01")
    notes = work / "notes" / "do-not-touch.txt"
    notes.parent.mkdir(parents=True)
    notes.write_text("移行対象外の元作品ファイル\n", encoding="utf-8")
    return work


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_hashes(work: Path) -> dict[str, str]:
    return {
        path.relative_to(work).as_posix(): _sha256(path)
        for path in sorted(work.rglob("*"))
        if path.is_file()
    }


def _assert_stage_and_rollback_preserve_source(project, worker, work: Path, root: Path) -> None:
    expected = _project_hashes(work)
    source = work / "p0001" / "page.blend"
    inspection = worker.inspect_page("p0001", source)
    assert not inspection.issues, inspection.issues

    direct_stage = root / "direct_stage" / "p0001" / "page.blend"
    direct_stage.parent.mkdir(parents=True)
    shutil.copy2(source, direct_stage)
    worker.convert_page(SimpleNamespace(
        page_id="p0001",
        staged_path=direct_stage,
        inspection_facts=inspection.facts,
    ))
    assert worker.validate_page("p0001", direct_stage)
    assert _project_hashes(work) == expected, (
        "stage変換が埋込み作品フォルダーへ書き戻しました"
    )

    plan = project.build_migration_plan(
        work,
        inspector=worker.inspect_page,
    )

    def fail_after_install(event: str, _page_id: str, index: int) -> None:
        if event == "after_swap_replace" and index == 1:
            raise RuntimeError("故障rollback検証")

    try:
        project.execute_migration(
            plan,
            confirmed=True,
            converter=worker.convert_page,
            validator=worker.validate_page,
            fault_hook=fail_after_install,
        )
    except project.MigrationExecutionError as exc:
        assert exc.rollback is not None and exc.rollback.status == "rolled_back"
    else:
        raise AssertionError("故障を注入できませんでした")
    assert _project_hashes(work) == expected, (
        "故障rollback後に元作品配下のファイルが変化しました"
    )


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_worker_ownership_"))
    succeeded = False
    try:
        project = importlib.import_module(
            f"{MODULE_NAME}.io.project_content_migration"
        )
        worker = importlib.import_module(
            f"{MODULE_NAME}.io.detail_data_blender_migration"
        )
        migration_op = importlib.import_module(
            f"{MODULE_NAME}.operators.detail_data_migration_op"
        )
        assert bpy.app.background
        assert not migration_op.migration_worker_owns_runtime(), (
            "通常のbackground Blenderを移行ワーカーと誤認しています"
        )

        work = _create_work(temp_root)
        _assert_stage_and_rollback_preserve_source(
            project, worker, work, temp_root
        )
        plan = project.build_migration_plan(
            work,
            inspector=worker.inspect_page,
        )

        # stage変換・stage検証・installed検証をすべて実ワーカーで対象blendへ行う。
        # 修正前はinstalled検証のload_postがこのjournalをrollbackして失敗した。
        result = project.execute_migration(
            plan,
            confirmed=True,
            converter=worker.convert_page,
            validator=worker.validate_page,
        )
        assert result.status == "committed"
        assert _read_json(plan.journal_path)["status"] == "committed"
        assert _read_json(work / "work.json")["detailDataVersion"] == 1

        restarted = project.verify_after_restart(
            plan.journal_path,
            validator=worker.validate_page,
            rollback_on_error=True,
            expected_work_dir=work,
        )
        assert restarted.status == "verified_after_restart"
        assert _read_json(plan.journal_path)["status"] == "verified_after_restart"
        assert _read_json(work / "work.json")["detailDataVersion"] == 1
        assert plan.backup_dir.is_dir()
        succeeded = True
        print("DETAIL_MIGRATION_WORKER_OWNERSHIP_CHECK_OK")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon.unregister()
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}")


if __name__ == "__main__":
    main()
