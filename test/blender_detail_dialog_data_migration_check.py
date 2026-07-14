"""Blender 5.1実機: 80ページ作品の全件移行・復旧・再起動検証。

生成した一時 ``.bmanga`` だけを使う。既存作品の探索や読込みは行わない。

実行例::

    blender.exe --background --factory-startup \
      --python test/blender_detail_dialog_data_migration_check.py
"""

from __future__ import annotations

import argparse
import hashlib
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
MIGRATION_FILE = ROOT / "io" / "project_content_migration.py"
PAGE_COUNT = 80
CRASH_EXIT_CODE = 91


def _load_migration_module():
    name = "bmanga_detail_data_migration_test_module"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _page_paths(work_dir: Path) -> list[Path]:
    return [work_dir / f"p{index:04d}" / "page.blend" for index in range(1, PAGE_COUNT + 1)]


def _create_seed_blend(path: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene["bmanga_test_legacy"] = True
    scene["bmanga_test_payload"] = "全ページ移行テスト"
    mesh = bpy.data.meshes.new("MigrationProbeMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("MigrationProbe", mesh)
    scene.collection.objects.link(obj)
    material = bpy.data.materials.new("MigrationProbeMaterial")
    obj.data.materials.append(material)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)


def _create_work(root: Path) -> Path:
    work = root / "GeneratedMigration80.bmanga"
    work.mkdir(parents=True)
    _write_json(work / "work.json", {"schemaVersion": 9, "detailDataVersion": 0})
    pages = [{"id": f"p{index:04d}", "title": f"{index}ページ"} for index in range(1, 81)]
    _write_json(work / "pages.json", {"schemaVersion": 2, "pages": pages})
    seed = root / "generated_seed.blend"
    _create_seed_blend(seed)
    for page in _page_paths(work):
        page.parent.mkdir(parents=True)
        shutil.copy2(seed, page)
    return work


def _loaded_scene(path: Path):
    with bpy.data.libraries.load(str(path), link=False) as (data_from, data_to):
        if not data_from.scenes:
            raise AssertionError(f"Sceneがありません: {path}")
        data_to.scenes = [data_from.scenes[0]]
    scene = data_to.scenes[0]
    if scene is None:
        raise AssertionError(f"Sceneを読めません: {path}")
    return scene


def _remove_loaded_scene(scene) -> None:
    for obj in tuple(scene.objects):
        if obj.users <= 1:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and getattr(data, "users", 0) == 0:
                collection = getattr(bpy.data, data.__class__.__name__.lower() + "s", None)
                if collection is not None:
                    try:
                        collection.remove(data)
                    except (ReferenceError, RuntimeError, TypeError):
                        pass
    bpy.data.scenes.remove(scene, do_unlink=True)


def _inspect_page(module, page_id: str, path: Path):
    scene = _loaded_scene(path)
    try:
        assert bool(scene.get("bmanga_test_legacy", False))
        assert scene.get("bmanga_test_payload") == "全ページ移行テスト"
        assert any(obj.name.startswith("MigrationProbe") for obj in scene.objects)
    finally:
        _remove_loaded_scene(scene)
    return module.PageInspection(
        estimated_output_bytes=max(path.stat().st_size * 2, path.stat().st_size),
        facts={"pageId": page_id, "legacyChecked": True},
    )


def _convert_page(task) -> None:
    bpy.ops.wm.open_mainfile(filepath=str(task.staged_path), load_ui=False)
    scene = bpy.context.scene
    assert bool(scene.get("bmanga_test_legacy", False))
    assert task.inspection_facts["pageId"] == task.page_id
    scene["bmanga_test_legacy"] = False
    scene["bmanga_test_migrated"] = 1
    scene["bmanga_test_page_id"] = task.page_id
    bpy.ops.wm.save_as_mainfile(filepath=str(task.staged_path), compress=False)


def _validate_page(page_id: str, path: Path) -> bool:
    is_current = bool(bpy.data.filepath) and Path(bpy.data.filepath).resolve() == path.resolve()
    scene = bpy.context.scene if is_current else _loaded_scene(path)
    try:
        assert not bool(scene.get("bmanga_test_legacy", True))
        assert int(scene.get("bmanga_test_migrated", 0)) == 1
        assert scene.get("bmanga_test_page_id") == page_id
        probe = next(obj for obj in scene.objects if obj.name.startswith("MigrationProbe"))
        assert len(probe.data.polygons) == 1
        assert len(probe.data.materials) == 1
    finally:
        if not is_current:
            _remove_loaded_scene(scene)
    return True


def _original_hashes(work: Path) -> dict[str, str]:
    return {path.parent.name: _sha256(path) for path in _page_paths(work)}


def _assert_hashes(work: Path, expected: dict[str, str]) -> None:
    actual = _original_hashes(work)
    assert actual == expected, "全ページが移行前のバイト列へ戻っていません"


def _blocking_preflight(module, work: Path, root: Path) -> None:
    def blocked(page_id: str, page_path: Path):
        if page_id != "p0003":
            return module.PageInspection(estimated_output_bytes=page_path.stat().st_size)
        return module.PageInspection(issues=(
            module.unresolved_pointer_issue(page_id, page_path, "gp:ptr_abc123", "link-A"),
            module.unsupported_gp_mask_issue(page_id, page_path, "任意マスクの変換規則がありません"),
        ))

    del root
    plan = module.build_migration_plan(work, inspector=blocked)
    tx = plan.transaction_dir
    codes = {issue.code for issue in plan.issues}
    assert {"unresolved_pointer_uid", "unsupported_gp_mask"} <= codes
    assert not tx.exists(), "事前検査は退避先を作ってはいけません"
    try:
        module.execute_migration(
            plan,
            confirmed=True,
            converter=_convert_page,
            validator=_validate_page,
        )
    except module.PreflightBlocked:
        pass
    else:
        raise AssertionError("危険な旧UID・GPマスクを事前停止できませんでした")
    assert not tx.exists(), "事前検査不合格時に書込みが発生しました"


def _confirmation_and_automatic_rollback(module, work: Path, root: Path) -> None:
    del root
    expected = _original_hashes(work)
    inspector = lambda page_id, path: _inspect_page(module, page_id, path)
    plan = module.build_migration_plan(work, inspector=inspector)
    tx = plan.transaction_dir
    assert plan.page_count == PAGE_COUNT
    assert plan.required_bytes > plan.source_bytes * 2
    assert plan.capacity_ok
    try:
        module.execute_migration(
            plan,
            confirmed=False,
            converter=_convert_page,
            validator=_validate_page,
        )
    except module.ConfirmationRequired:
        pass
    else:
        raise AssertionError("明示確認なしの書込みが拒否されませんでした")
    assert not tx.exists()
    _assert_hashes(work, expected)

    def fail_at_page_37(event: str, _page_id: str, index: int) -> None:
        if event == "after_swap" and index == 37:
            raise RuntimeError("任意ページ入替え失敗")

    try:
        module.execute_migration(
            plan,
            confirmed=True,
            converter=_convert_page,
            validator=_validate_page,
            fault_hook=fail_at_page_37,
        )
    except module.MigrationExecutionError as exc:
        assert exc.rollback is not None and exc.rollback.status == "rolled_back"
    else:
        raise AssertionError("任意ページ失敗を注入できませんでした")
    _assert_hashes(work, expected)
    assert _read_json(work / "work.json")["detailDataVersion"] == 0
    assert _read_json(plan.journal_path)["status"] == "rolled_back"


def _child_command(mode: str, work: Path, journal: Path | None = None) -> list[str]:
    command = [
        bpy.app.binary_path,
        "--background",
        "--factory-startup",
        "--python",
        str(Path(__file__).resolve()),
        "--",
        "--mode",
        mode,
        "--work",
        str(work),
    ]
    if journal is not None:
        command.extend(("--journal", str(journal)))
    return command


def _run_child(command: list[str], expected_code: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    if result.returncode != expected_code:
        raise AssertionError(
            f"子Blenderの終了コードが不正です: {result.returncode}\n"
            f"STDOUT:\n{result.stdout[-4000:]}\nSTDERR:\n{result.stderr[-4000:]}"
        )
    return result


def _crash_recovery_and_success(module, work: Path, root: Path) -> Path:
    expected = _original_hashes(work)
    journals_before = set(root.rglob(module.JOURNAL_FILE_NAME))
    _run_child(_child_command("crash", work), CRASH_EXIT_CODE)
    new_journals = set(root.rglob(module.JOURNAL_FILE_NAME)) - journals_before
    assert len(new_journals) == 1, "中断トランザクションを一意に特定できません"
    crash_journal = new_journals.pop()
    assert crash_journal.is_file()
    assert _read_json(work / "work.json")["detailDataVersion"] == 0
    changed = sum(_sha256(path) != expected[path.parent.name] for path in _page_paths(work))
    assert 1 <= changed <= 41, "PC終了を模した途中状態が作られていません"
    _run_child(_child_command("recover", work, crash_journal), 0)
    _assert_hashes(work, expected)
    assert _read_json(work / "work.json")["detailDataVersion"] == 0

    inspector = lambda page_id, path: _inspect_page(module, page_id, path)
    plan = module.build_migration_plan(work, inspector=inspector)
    result = module.execute_migration(
        plan,
        confirmed=True,
        converter=_convert_page,
        validator=_validate_page,
    )
    assert result.status == "committed"
    assert _read_json(work / "work.json")["detailDataVersion"] == 1
    _assert_marker_was_last(plan.journal_path)
    _run_child(_child_command("verify", work, plan.journal_path), 0)
    assert _read_json(plan.journal_path)["status"] == "verified_after_restart"
    assert plan.backup_dir.is_dir(), "再起動後検証後も退避データを自動削除してはいけません"
    return plan.journal_path


def _assert_marker_was_last(journal_path: Path) -> None:
    journal = _read_json(journal_path)
    events = [event["event"] for event in journal["events"]]
    last_installed = max(i for i, name in enumerate(events) if name == "installed_validated")
    before_marker = events.index("before_marker")
    committed = events.index("committed")
    assert last_installed < before_marker < committed
    assert sum(name == "installed_validated" for name in events) == PAGE_COUNT


def _assert_current_version_is_noop(module, work: Path, root: Path) -> None:
    del root
    def current_inspector(_page_id: str, path: Path):
        return module.PageInspection(
            estimated_output_bytes=path.stat().st_size,
            facts={"pageDetailDataVersion": 1},
        )

    plan = module.build_migration_plan(work, inspector=current_inspector)
    tx = plan.transaction_dir
    assert plan.already_current and not plan.issues
    result = module.execute_migration(
        plan,
        confirmed=False,
        converter=_convert_page,
        validator=_validate_page,
    )
    assert result.status == "already_current"
    assert not tx.exists()


def _controller() -> None:
    module = _load_migration_module()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_detail_data_migration_"))
    succeeded = False
    try:
        work = _create_work(temp_root)
        _blocking_preflight(module, work, temp_root)
        _confirmation_and_automatic_rollback(module, work, temp_root)
        journal = _crash_recovery_and_success(module, work, temp_root)
        _assert_current_version_is_noop(module, work, temp_root)
        assert journal.is_file()
        succeeded = True
        print("DETAIL_DIALOG_DATA_MIGRATION_CHECK_OK")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}")


def _crash_mode(module, work: Path) -> None:
    inspector = lambda page_id, path: _inspect_page(module, page_id, path)
    plan = module.build_migration_plan(work, inspector=inspector)

    def hard_exit(event: str, _page_id: str, index: int) -> None:
        if event == "after_swap_replace" and index == 41:
            os._exit(CRASH_EXIT_CODE)

    module.execute_migration(
        plan,
        confirmed=True,
        converter=_convert_page,
        validator=_validate_page,
        fault_hook=hard_exit,
        auto_rollback_on_error=False,
    )
    raise AssertionError("ハード終了が発生しませんでした")


def _parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mode", default="controller")
    parser.add_argument("--work", default="")
    parser.add_argument("--journal", default="")
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    module = _load_migration_module()
    if args.mode == "controller":
        _controller()
        return
    work = Path(args.work).resolve()
    if args.mode == "crash":
        _crash_mode(module, work)
    elif args.mode == "recover":
        result = module.recover_transaction(
            Path(args.journal),
            expected_work_dir=work,
            force=True,
        )
        assert result.status == "rolled_back"
        print("DETAIL_DIALOG_DATA_MIGRATION_RECOVERY_OK")
    elif args.mode == "verify":
        result = module.verify_after_restart(
            Path(args.journal),
            expected_work_dir=work,
            validator=_validate_page,
        )
        assert result.status == "verified_after_restart"
        print("DETAIL_DIALOG_DATA_MIGRATION_RESTART_VERIFY_OK")
    else:
        raise AssertionError(f"unknown mode: {args.mode}")


if __name__ == "__main__":
    main()
