"""Blender 5.1実機: 開いている作品ファイルの移行前後を安全に再読込する。

生成した一時作品だけを使い、次を確認する。

* 未保存変更がある場合は変換を開始しない
* 開いているページは成功後に入替え済みファイルを再読込する
* 失敗時は退避済みの旧ページへ戻して再読込する
* ページ一覧は成功後に作品の版情報とページ一覧を再読込する
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_detail_migration_open_file_reload_test"


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


def _save_probe_blend(
    path: Path,
    value: str,
    *,
    detail_data_version: int = 0,
) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.scene["bmanga_open_file_reload_probe"] = value
    if detail_data_version:
        bpy.context.scene["bmanga_detail_data_version"] = detail_data_version
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)


def _create_work(root: Path, name: str, *, with_work_blend: bool = False) -> Path:
    work = root / f"{name}.bmanga"
    _write_json(
        work / "work.json",
        {
            "schemaVersion": 9,
            "detailDataVersion": 0,
            "title": name,
        },
    )
    _write_json(
        work / "pages.json",
        {
            "schemaVersion": 2,
            "pages": [
                {"id": "p0001", "title": "1ページ", "dirRel": "p0001"},
            ],
        },
    )
    _save_probe_blend(work / "p0001" / "page.blend", "old")
    if with_work_blend:
        _save_probe_blend(work / "work.blend", "work")
    return work


def _build_plan(project, work: Path):
    return project.build_migration_plan(
        work,
        inspector=lambda _page_id, path: project.PageInspection(
            estimated_output_bytes=path.stat().st_size
        ),
    )


class _OperatorProbe:
    def __init__(self, migration_op, plan) -> None:
        self._plan = plan
        self.messages: list[tuple[frozenset[str], str]] = []
        self._finish_failure_impl = (
            migration_op.BMANGA_OT_detail_data_migrate._finish_failure
        )

    def report(self, levels, message: str) -> None:
        self.messages.append((frozenset(levels), str(message)))

    def _finish_failure(self, plan, open_state, display, failure):
        return self._finish_failure_impl(
            self,
            plan,
            open_state,
            display,
            failure,
        )


def _execute(migration_op, probe: _OperatorProbe):
    return migration_op.BMANGA_OT_detail_data_migrate.execute(
        probe,
        bpy.context,
    )


def _callbacks(project, template: Path, *, reject_installed: Path | None = None):
    expected_bytes = template.read_bytes()

    def _converter(task) -> None:
        shutil.copy2(template, task.staged_path)

    def _validator(_page_id: str, path: Path) -> bool:
        if reject_installed is not None and path.resolve() == reject_installed.resolve():
            return False
        # 現在開いているページはBlenderのライブラリ読込対象にできないため、
        # ここでは入替え対象そのもののバイト列を検証する。再読込結果は別assert。
        return path.read_bytes() == expected_bytes

    inspector = lambda _page_id, path: project.PageInspection(
        estimated_output_bytes=path.stat().st_size
    )
    return inspector, _converter, _validator


def _assert_current(path: Path, value: str) -> None:
    assert Path(bpy.data.filepath).resolve() == path.resolve()
    assert bpy.context.scene.get("bmanga_open_file_reload_probe") == value


def _assert_gate(expected: bool) -> None:
    work_state = bpy.context.scene.bmanga_work
    assert bool(work_state.loaded) is (not expected)


def _unsaved_and_success_case(project, migration_op, root: Path, template: Path) -> None:
    work = _create_work(root, "OpenPageSuccess")
    page = work / "p0001" / "page.blend"
    bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    _assert_current(page, "old")
    _assert_gate(True)
    assert not bpy.data.is_dirty, "読込同期だけで未保存扱いになっています"

    plan = _build_plan(project, work)
    probe = _OperatorProbe(migration_op, plan)
    migration_op.BMANGA_OT_detail_data_migrate.cancel(probe, bpy.context)
    _assert_gate(True)
    assert any("通常の編集機能は停止" in message for _levels, message in probe.messages)
    original_callbacks = migration_op._migration_callbacks
    conversion_count = 0

    def _blocked_callbacks():
        nonlocal conversion_count
        inspector, converter, validator = _callbacks(project, template)

        def _counted_converter(task) -> None:
            nonlocal conversion_count
            conversion_count += 1
            converter(task)

        return inspector, _counted_converter, validator

    migration_op._migration_callbacks = _blocked_callbacks
    original_capture = migration_op._capture_open_blend_state
    try:
        # background Blenderは編集Operatorを実行してもdirtyフラグを更新しない。
        # そのためGUIで得られる同じ状態値を注入して機械的停止経路を検証する。
        def _capture_dirty(work_dir, active_plan=None):
            state = original_capture(work_dir, active_plan)
            return migration_op._OpenBlendState(
                filepath=state.filepath,
                page_id=state.page_id,
                is_work_blend=state.is_work_blend,
                is_dirty=True,
            )

        migration_op._capture_open_blend_state = _capture_dirty
        assert _execute(migration_op, probe) == {"CANCELLED"}
        assert conversion_count == 0, "未保存状態で変換が始まりました"
        assert _read_json(work / "work.json")["detailDataVersion"] == 0
        assert not plan.transaction_dir.exists()

        migration_op._capture_open_blend_state = original_capture
        plan = _build_plan(project, work)
        probe = _OperatorProbe(migration_op, plan)
        probe._plan = plan
        assert _execute(migration_op, probe) == {"FINISHED"}
    finally:
        migration_op._capture_open_blend_state = original_capture
        migration_op._migration_callbacks = original_callbacks

    _assert_current(page, "new")
    _assert_gate(False)
    assert _read_json(work / "work.json")["detailDataVersion"] == 1
    assert _read_json(plan.journal_path)["status"] == "verified_after_restart"
    assert any("再読込検証済み" in message for _levels, message in probe.messages)


def _rollback_case(project, migration_op, root: Path, template: Path) -> None:
    work = _create_work(root, "OpenPageRollback")
    page = work / "p0001" / "page.blend"
    bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    _assert_current(page, "old")
    _assert_gate(True)
    assert not bpy.data.is_dirty
    plan = _build_plan(project, work)
    probe = _OperatorProbe(migration_op, plan)
    original_callbacks = migration_op._migration_callbacks
    migration_op._migration_callbacks = lambda: _callbacks(
        project,
        template,
        reject_installed=page,
    )
    try:
        assert _execute(migration_op, probe) == {"CANCELLED"}
    finally:
        migration_op._migration_callbacks = original_callbacks

    _assert_current(page, "old")
    _assert_gate(True)
    assert _read_json(work / "work.json")["detailDataVersion"] == 0
    assert _read_json(plan.journal_path)["status"] == "rolled_back"
    assert any("元の状態へ戻して再読込" in message for _levels, message in probe.messages)


def _work_blend_case(project, migration_op, root: Path, template: Path) -> None:
    work = _create_work(root, "OpenWorkSuccess", with_work_blend=True)
    work_blend = work / "work.blend"
    bpy.ops.wm.open_mainfile(filepath=str(work_blend), load_ui=False)
    _assert_current(work_blend, "work")
    _assert_gate(True)
    assert not bpy.data.is_dirty
    plan = _build_plan(project, work)
    probe = _OperatorProbe(migration_op, plan)
    original_callbacks = migration_op._migration_callbacks
    migration_op._migration_callbacks = lambda: _callbacks(project, template)
    try:
        assert _execute(migration_op, probe) == {"FINISHED"}
    finally:
        migration_op._migration_callbacks = original_callbacks

    _assert_current(work_blend, "work")
    _assert_gate(False)
    work_state = bpy.context.scene.bmanga_work
    assert int(work_state.detail_data_version) == 1
    assert len(work_state.pages) == 1
    assert work_state.pages[0].id == "p0001"


class _WorkOpenProbe:
    def __init__(self, filepath: Path) -> None:
        self.filepath = str(filepath)
        self.messages: list[tuple[frozenset[str], str]] = []

    def report(self, levels, message: str) -> None:
        self.messages.append((frozenset(levels), str(message)))


def _work_open_gate_case(work_op, root: Path) -> None:
    work = _create_work(root, "WorkOpenGate", with_work_blend=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    probe = _WorkOpenProbe(work)
    result = work_op.BMANGA_OT_work_open.execute(probe, bpy.context)
    assert result == {"FINISHED"}
    _assert_current(work / "work.blend", "work")
    _assert_gate(True)
    assert any("作品を開きました" in message for _levels, message in probe.messages)


def _restart_recovery_gate_case(project, migration_op, root: Path, template: Path) -> None:
    work = _create_work(root, "RestartRecoveryGate")
    page = work / "p0001" / "page.blend"
    original_bytes = page.read_bytes()
    plan = _build_plan(project, work)
    _inspector, converter, validator = _callbacks(project, template)

    def _interrupt_after_swap(event: str, _page_id: str, _index: int) -> None:
        if event == "after_swap":
            raise RuntimeError("再起動復旧テスト用中断")

    try:
        project.execute_migration(
            plan,
            confirmed=True,
            converter=converter,
            validator=validator,
            fault_hook=_interrupt_after_swap,
            auto_rollback_on_error=False,
        )
    except project.MigrationExecutionError as exc:
        assert exc.rollback is None
    else:
        raise AssertionError("中断状態を作成できませんでした")
    assert _read_json(plan.journal_path)["status"] == "interrupted"
    assert page.read_bytes() == template.read_bytes()

    # Blender再起動後の直接読込に相当。load_postが中断処理を復旧し、
    # 再読込が完了するまで通常操作を閉じたままにする。
    bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    assert _read_json(plan.journal_path)["status"] == "rolled_back"
    assert page.read_bytes() == original_bytes
    _assert_gate(True)
    if bpy.context.scene.get("bmanga_open_file_reload_probe") != "old":
        migration_op._reload_blend_from_disk(page)
    _assert_current(page, "old")
    _assert_gate(True)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_open_file_reload_"))
    succeeded = False
    try:
        project = importlib.import_module(
            f"{MODULE_NAME}.io.project_content_migration"
        )
        migration_op = importlib.import_module(
            f"{MODULE_NAME}.operators.detail_data_migration_op"
        )
        work_op = importlib.import_module(f"{MODULE_NAME}.operators.work_op")
        template = temp_root / "migrated_template.blend"
        # 実変換ワーカーはページ用blendにも現行版を記録する。ここで使う
        # 簡易コピー変換も同じ成果物契約に合わせ、再読込ゲートを検証する。
        _save_probe_blend(template, "new", detail_data_version=1)
        _work_open_gate_case(work_op, temp_root)
        _unsaved_and_success_case(project, migration_op, temp_root, template)
        _rollback_case(project, migration_op, temp_root, template)
        _work_blend_case(project, migration_op, temp_root, template)
        _restart_recovery_gate_case(project, migration_op, temp_root, template)
        succeeded = True
        print("DETAIL_MIGRATION_OPEN_FILE_RELOAD_CHECK_OK")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon.unregister()
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}")


if __name__ == "__main__":
    main()
