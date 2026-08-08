"""Blender 5.1 UI: ページ内0.01mm編集の Undo/Redo 境界を実機検証する.

``--background`` では ``bpy.ops.ed.undo`` の UI poll が成立しないため、通常画面で
起動し timer から実行する。合否は ``BMANGA_UNDO_TEST_STATUS`` の JSON に書く。
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_undo_runtime"
STATUS_PATH = Path(
    os.environ.get(
        "BMANGA_UNDO_TEST_STATUS",
        str(ROOT / "_verify" / "2026-07-17_undo_runtime" / "status.json"),
    )
)
WORK_PARENT = Path(tempfile.mkdtemp(prefix="bmanga_undo_runtime_"))
WORK_PATH = WORK_PARENT / "UndoRuntime.bmanga"
BALLOON_ID = "balloon_undo_runtime"
TEXT_ID = "text_undo_runtime"
ORIGINAL_X = 24.0
TEXT_ORIGINAL_X = 70.0
DELTA_X = 0.01
_addon = None
_artifact_baseline: dict[str, int] = {}
_transfer_state: dict[str, object] = {}
_stage = "setup"


def _run_in_view3d(callback):
    """mainfile再読込後も有効な3Dビュー文脈で処理する."""
    window = next(iter(getattr(bpy.context.window_manager, "windows", ()) or ()), None)
    if window is None:
        raise AssertionError("検証用のBlenderウィンドウがありません")
    area = next((candidate for candidate in window.screen.areas if candidate.type == "VIEW_3D"), None)
    if area is None:
        raise AssertionError("検証用の3Dビューがありません")
    region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)
    if region is None:
        raise AssertionError("検証用のWINDOWリージョンがありません")
    with bpy.context.temp_override(
        window=window,
        screen=window.screen,
        area=area,
        region=region,
    ):
        return callback(bpy.context)


def _run_history_operator(operator):
    """有効なウィンドウ文脈でUndo/Redoを実行する."""
    return _run_in_view3d(lambda _context: operator("EXEC_DEFAULT"))


def _write_status(ok: bool, **details) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps({"ok": ok, "stage": _stage, **details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _find_entries():
    work = bpy.context.scene.bmanga_work
    for page in work.pages:
        balloon = next(
            (entry for entry in page.balloons if str(entry.id) == BALLOON_ID),
            None,
        )
        text = next(
            (entry for entry in page.texts if str(entry.id) == TEXT_ID),
            None,
        )
        if balloon is not None and text is not None:
            return work, page, balloon, text
    raise AssertionError("検証用フキダシ／テキストが見つかりません")


def _artifact_mtimes(work_dir: Path) -> dict[str, int]:
    result = {}
    for suffix in ("*.json", "*.png"):
        for path in work_dir.rglob(suffix):
            if ".bmanga-save-recovery-v1" in path.parts:
                continue
            result[str(path.relative_to(work_dir))] = path.stat().st_mtime_ns
    return result


def _assert_page_state(
    expected_delta: float,
    *,
    check_domain: bool = False,
) -> None:
    from bmanga_dev_undo_runtime.io import domain_projection, domain_runtime
    from bmanga_dev_undo_runtime.utils import page_file_scene

    work, page, balloon, text = _find_entries()
    role, page_id, _coma_id = page_file_scene.current_role(bpy.context)
    assert work.loaded
    assert role == page_file_scene.ROLE_PAGE, role
    assert page_id == "p0001", page_id
    assert Path(bpy.data.filepath).name == "page.blend", bpy.data.filepath
    assert abs(float(balloon.x_mm) - (ORIGINAL_X + expected_delta)) < 1.0e-5, (
        float(balloon.x_mm),
        ORIGINAL_X + expected_delta,
    )
    assert abs(float(text.x_mm) - (TEXT_ORIGINAL_X + expected_delta)) < 1.0e-5, (
        float(text.x_mm),
        TEXT_ORIGINAL_X + expected_delta,
    )
    if check_domain:
        project_uid = domain_projection.ensure_project_uid(work)
        page_uid = domain_projection.ensure_page_uid(page, project_uid)
        document = domain_runtime.store_for(work.work_dir).pages[page_uid]
        nodes = {
            (node.kind, node.display_id): node
            for node in document.nodes.values()
        }
        assert abs(
            float(nodes[("balloon", BALLOON_ID)].settings["xMm"])
            - (ORIGINAL_X + expected_delta)
        ) < 1.0e-5
        assert abs(
            float(nodes[("text", TEXT_ID)].settings["xMm"])
            - (TEXT_ORIGINAL_X + expected_delta)
        ) < 1.0e-5


def _commit_micro_move() -> None:
    from bmanga_dev_undo_runtime.operators.object_tool_op import BMANGA_OT_object_tool
    from bmanga_dev_undo_runtime.utils import object_selection

    def _drag(context):
        _work, page, balloon, text = _find_entries()
        balloon_key = object_selection.balloon_key(page, balloon)
        text_key = object_selection.text_key(page, text)
        object_selection.select_key(context, balloon_key, mode="single")
        object_selection.select_key(context, text_key, mode="add")
        method_names = (
            "_clear_click_state",
            "_clear_drag_state",
            "_finish_drag",
            "_make_snapshots",
            "_setup_center_snap",
            "_start_object_drag",
            "_apply_snapshots",
        )
        harness = type(
            "ObjectToolHarness",
            (),
            {name: getattr(BMANGA_OT_object_tool, name) for name in method_names},
        )()
        harness._clear_drag_state()
        harness._clear_click_state()
        harness._start_object_drag(
            context,
            {"kind": "balloon", "part": "move", "key": balloon_key},
            float(balloon.x_mm),
            float(balloon.y_mm),
        )
        assert harness._object_move_drag is not None
        harness._object_move_drag.update_overlay(context, DELTA_X, 0.0)
        harness._finish_drag(context)

    _run_in_view3d(_drag)
    _work, _page, balloon, text = _find_entries()
    assert abs(float(balloon.x_mm) - (ORIGINAL_X + DELTA_X)) < 1.0e-5
    assert abs(float(text.x_mm) - (TEXT_ORIGINAL_X + DELTA_X)) < 1.0e-5, (
        float(text.x_mm),
        TEXT_ORIGINAL_X + DELTA_X,
    )


def _commit_return_to_origin() -> None:
    from bmanga_dev_undo_runtime.operators.object_tool_op import BMANGA_OT_object_tool
    from bmanga_dev_undo_runtime.utils import object_selection

    def _drag(context):
        _work, page, balloon, text = _find_entries()
        balloon_key = object_selection.balloon_key(page, balloon)
        text_key = object_selection.text_key(page, text)
        object_selection.select_key(context, balloon_key, mode="single")
        object_selection.select_key(context, text_key, mode="add")
        method_names = (
            "_clear_click_state",
            "_clear_drag_state",
            "_finish_drag",
            "_make_snapshots",
            "_setup_center_snap",
            "_start_object_drag",
            "_apply_snapshots",
        )
        harness = type(
            "ObjectToolNoopHarness",
            (),
            {name: getattr(BMANGA_OT_object_tool, name) for name in method_names},
        )()
        harness._clear_drag_state()
        harness._clear_click_state()
        harness._start_object_drag(
            context,
            {"kind": "balloon", "part": "move", "key": balloon_key},
            float(balloon.x_mm),
            float(balloon.y_mm),
        )
        assert harness._object_move_drag is not None
        harness._object_move_drag.update_overlay(context, 0.2, 0.0)
        harness._object_move_drag.update_overlay(context, 0.0, 0.0)
        harness._finish_drag(context)

    _run_in_view3d(_drag)


def _setup_cross_page_transfer() -> None:
    global _transfer_state

    assert bpy.ops.bmanga.page_add("EXEC_DEFAULT") == {"FINISHED"}

    def _transfer(context):
        from bmanga_dev_undo_runtime.utils import (
            layer_stack,
            layer_transfer_group,
            page_grid,
            paths,
            undo_transaction,
        )
        from bmanga_dev_undo_runtime.utils.layer_reparent import ClickTarget

        work, page, balloon, _text = _find_entries()
        assert len(work.pages) == 2
        target = work.pages[1]
        stack = layer_stack.sync_layer_stack(context, preserve_active_index=True)
        balloon_uid = layer_stack.target_uid(
            "balloon",
            f"{page.id}:{balloon.id}",
        )
        layer_stack.clear_all_selection(context)
        index = next(
            index
            for index, item in enumerate(stack)
            if layer_stack.stack_item_uid(item) == balloon_uid
        )
        assert layer_stack.select_stack_index(context, index)
        target_index = 1
        offset = page_grid.page_total_offset_mm(
            work,
            context.scene,
            target_index,
        )
        drop = (offset[0] + 105.0, offset[1] + 140.0)
        click_target = ClickTarget(
            "page",
            target,
            None,
            target_index,
            drop,
            (105.0, 140.0),
        )
        changed = layer_transfer_group.transfer_group_to_page(
            context,
            click_target,
            drop_world_xy_mm=drop,
        )
        assert changed == 2, changed
        assert undo_transaction.push_undo(
            "B-MANGA: Phase5 cross-page transfer",
        )
        _transfer_state.clear()
        _transfer_state.update({
            "work_dir": Path(work.work_dir),
            "source_page_id": str(page.id),
            "target_page_id": str(target.id),
            "source_page_path": paths.page_meta_path(
                Path(work.work_dir),
                str(page.id),
            ),
            "stage_path": layer_transfer_group.cross_page_stage.staged_path(
                Path(work.work_dir),
                str(target.id),
            ),
        })
        _assert_transfer_files(applied=True)

    _run_in_view3d(_transfer)


def _assert_transfer_files(*, applied: bool) -> None:
    from bmanga_dev_undo_runtime.utils import cross_page_stage, history_runtime

    page_data = json.loads(
        Path(_transfer_state["source_page_path"]).read_text(encoding="utf-8")
    )
    nodes = page_data["tree"]["nodes"].values()
    source_present = {
        (node.get("kind"), node.get("displayId"))
        for node in nodes
    }
    expected = {
        ("balloon", BALLOON_ID),
        ("text", TEXT_ID),
    }
    stage_path = Path(_transfer_state["stage_path"])
    stage = cross_page_stage._read(stage_path)
    ready = any(
        isinstance(entry, dict)
        and str(entry.get("state", "") or "") == "ready"
        for entry in stage.get(cross_page_stage.ASSET_ENTRIES_KEY, ())
    )
    if applied:
        assert not (source_present & expected), source_present
        assert ready
    else:
        assert expected <= source_present, source_present
        assert not ready
    assert not history_runtime.is_blocked(), history_runtime.blocked_error()


def _tick():
    global _stage, _artifact_baseline
    try:
        if _stage == "move":
            _assert_page_state(0.0)
            work, _page, _balloon, _text = _find_entries()
            _artifact_baseline = _artifact_mtimes(Path(work.work_dir))
            _commit_micro_move()
            _assert_page_state(DELTA_X)
            _stage = "undo"
            return 0.15
        if _stage == "undo":
            assert _run_history_operator(bpy.ops.ed.undo) == {"FINISHED"}
            _stage = "check_undo"
            return 0.35
        if _stage == "check_undo":
            _assert_page_state(0.0, check_domain=True)
            assert _run_history_operator(bpy.ops.ed.redo) == {"FINISHED"}
            _stage = "check_redo"
            return 0.35
        if _stage == "check_redo":
            _assert_page_state(DELTA_X, check_domain=True)
            _commit_return_to_origin()
            _stage = "check_noop_undo"
            assert _run_history_operator(bpy.ops.ed.undo) == {"FINISHED"}
            return 0.35
        if _stage == "check_noop_undo":
            # 元へ戻したドラッグが空履歴を作っていれば、1回のUndoではここが
            # 24.01mmのままになる。24.00mmなら最終状態比較が機能している。
            _assert_page_state(0.0, check_domain=True)
            work, _page, _balloon, _text = _find_entries()
            assert _artifact_mtimes(Path(work.work_dir)) == _artifact_baseline
            _setup_cross_page_transfer()
            _stage = "transfer_undo"
            return 0.15
        if _stage == "transfer_undo":
            assert _run_history_operator(bpy.ops.ed.undo) == {"FINISHED"}
            _stage = "check_transfer_undo"
            return 0.5
        if _stage == "check_transfer_undo":
            _assert_transfer_files(applied=False)
            _assert_page_state(0.0, check_domain=True)
            assert _run_history_operator(bpy.ops.ed.redo) == {"FINISHED"}
            _stage = "check_transfer_redo"
            return 0.5
        if _stage == "check_transfer_redo":
            _assert_transfer_files(applied=True)
            work = bpy.context.scene.bmanga_work
            source = next(
                page
                for page in work.pages
                if page.id == _transfer_state["source_page_id"]
            )
            assert not any(entry.id == BALLOON_ID for entry in source.balloons)
            assert not any(entry.id == TEXT_ID for entry in source.texts)
            _stage = "done"
            _write_status(
                True,
                micro_delta_mm=DELTA_X,
                filepath=bpy.data.filepath,
                artifacts_checked=sorted(_artifact_baseline),
                cross_page_undo_redo=True,
            )
            print("BMANGA_UNDO_REDO_RUNTIME_OK")
            bpy.ops.wm.quit_blender()
            return None
        raise AssertionError(f"unknown stage: {_stage}")
    except Exception as exc:  # noqa: BLE001
        _write_status(False, error=str(exc), traceback=traceback.format_exc())
        traceback.print_exc()
        bpy.ops.wm.quit_blender()
        return None


def main() -> None:
    global _addon, _stage
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _addon = _load_addon()
        assert bpy.ops.bmanga.work_new(filepath=str(WORK_PATH)) == {"FINISHED"}
        # 未作成page.blendを作成・開き直す境界自体も通す。
        assert bpy.ops.bmanga.open_page_file(index=0) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        page = work.pages[0]
        page.detail_loaded = True
        entry = page.balloons.add()
        entry.id = BALLOON_ID
        entry.title = "Undo Runtime"
        entry.x_mm = ORIGINAL_X
        entry.y_mm = 30.0
        entry.width_mm = 40.0
        entry.height_mm = 25.0
        entry.parent_kind = "page"
        entry.parent_key = str(page.id)
        text = page.texts.add()
        text.id = TEXT_ID
        text.title = "Undo Runtime Text"
        text.body = "Undo Runtime"
        text.x_mm = TEXT_ORIGINAL_X
        text.y_mm = 45.0
        text.width_mm = 30.0
        text.height_mm = 20.0
        text.parent_kind = "page"
        text.parent_key = str(page.id)
        text.parent_balloon_id = entry.id
        assert bpy.ops.bmanga.work_save() == {"FINISHED"}
        from bmanga_dev_undo_runtime.io import blend_io

        assert blend_io.open_page_blend(Path(work.work_dir), str(page.id))
        _stage = "move"
        bpy.app.timers.register(_tick, first_interval=0.8)
    except Exception as exc:  # noqa: BLE001
        _write_status(False, error=str(exc), traceback=traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
