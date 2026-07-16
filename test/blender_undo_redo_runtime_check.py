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
_stage = "setup"


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


def _assert_page_state(expected_delta: float) -> None:
    from bmanga_dev_undo_runtime.utils import page_file_scene

    work, _page, balloon, text = _find_entries()
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


def _commit_micro_move() -> None:
    from bmanga_dev_undo_runtime.operators.object_tool_op import BMANGA_OT_object_tool
    from bmanga_dev_undo_runtime.utils import object_selection

    _work, page, balloon, text = _find_entries()
    balloon_key = object_selection.balloon_key(page, balloon)
    text_key = object_selection.text_key(page, text)
    object_selection.select_key(bpy.context, balloon_key, mode="single")
    object_selection.select_key(bpy.context, text_key, mode="add")
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
        bpy.context,
        {"kind": "balloon", "part": "move", "key": balloon_key},
        float(balloon.x_mm),
        float(balloon.y_mm),
    )
    harness._apply_snapshots(bpy.context, DELTA_X, 0.0)
    harness._finish_drag(bpy.context)
    assert abs(float(balloon.x_mm) - (ORIGINAL_X + DELTA_X)) < 1.0e-5
    assert abs(float(text.x_mm) - (TEXT_ORIGINAL_X + DELTA_X)) < 1.0e-5, (
        float(text.x_mm),
        TEXT_ORIGINAL_X + DELTA_X,
    )


def _commit_return_to_origin() -> None:
    from bmanga_dev_undo_runtime.operators.object_tool_op import BMANGA_OT_object_tool
    from bmanga_dev_undo_runtime.utils import object_selection

    _work, page, balloon, text = _find_entries()
    balloon_key = object_selection.balloon_key(page, balloon)
    text_key = object_selection.text_key(page, text)
    object_selection.select_key(bpy.context, balloon_key, mode="single")
    object_selection.select_key(bpy.context, text_key, mode="add")
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
        bpy.context,
        {"kind": "balloon", "part": "move", "key": balloon_key},
        float(balloon.x_mm),
        float(balloon.y_mm),
    )
    harness._apply_snapshots(bpy.context, 0.2, 0.0)
    harness._apply_snapshots(bpy.context, 0.0, 0.0)
    harness._finish_drag(bpy.context)


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
            assert bpy.ops.ed.undo() == {"FINISHED"}
            _stage = "check_undo"
            return 0.35
        if _stage == "check_undo":
            _assert_page_state(0.0)
            assert bpy.ops.ed.redo() == {"FINISHED"}
            _stage = "check_redo"
            return 0.35
        if _stage == "check_redo":
            _assert_page_state(DELTA_X)
            _commit_return_to_origin()
            _stage = "check_noop_undo"
            assert bpy.ops.ed.undo() == {"FINISHED"}
            return 0.35
        if _stage == "check_noop_undo":
            # 元へ戻したドラッグが空履歴を作っていれば、1回のUndoではここが
            # 24.01mmのままになる。24.00mmなら最終状態比較が機能している。
            _assert_page_state(0.0)
            work, _page, _balloon, _text = _find_entries()
            assert _artifact_mtimes(Path(work.work_dir)) == _artifact_baseline
            _stage = "done"
            _write_status(
                True,
                micro_delta_mm=DELTA_X,
                filepath=bpy.data.filepath,
                artifacts_checked=sorted(_artifact_baseline),
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
