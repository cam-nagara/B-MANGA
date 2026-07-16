"""Blender 5.1実機: 復旧後の自動再読込とフォールバックの実機検証。

強制終了後の復旧が新規作成ページを削除した直後に同じファイルの再読込を
予約すると、対象が存在せずENOENTで行き止まりダイアログになっていた回帰
に対する修正 (``_reload_fallback_target`` / ``_reload_missing_target`` /
``_native_save_reload_tick``) を、実際のBlenderファイル開閉で検証する。
"""

from __future__ import annotations

import importlib
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
MODULE_NAME = "bmanga_native_save_reload_fallback_test"

_counters = {"checks": 0}


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


def _save_probe_blend(path: Path, value: str) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.scene["bmanga_reload_fallback_probe"] = value
    result = bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    assert "FINISHED" in result


def _create_work(root: Path) -> tuple[Path, Path, Path]:
    """work.blend だけを実体化した最小作品フォルダーを作る (page.blendは無し)."""

    work = root / "ReloadFallback.bmanga"
    _write_json(
        work / "work.json",
        {"schemaVersion": 9, "detailDataVersion": 0, "title": "再読込フォールバック"},
    )
    _write_json(
        work / "pages.json",
        {
            "schemaVersion": 2,
            "pages": [{"id": "p0001", "title": "1ページ", "dirRel": "p0001"}],
        },
    )
    _write_json(work / "p0001" / "page.json", {"pageId": "p0001", "sentinel": True})
    work_blend = work / "work.blend"
    page = work / "p0001" / "page.blend"
    _save_probe_blend(work_blend, "work")
    return work, work_blend, page


def _open(path: Path, value: str) -> None:
    result = bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    assert "FINISHED" in result
    assert Path(bpy.data.filepath).resolve() == path.resolve(), (
        f"{path} を開いたはずがbpy.data.filepathが一致しません: {bpy.data.filepath}"
    )
    assert bpy.context.scene.get("bmanga_reload_fallback_probe") == value


def _check(condition: bool, message: str) -> None:
    _counters["checks"] += 1
    assert condition, message


def _case_fallback_target(handlers, work_blend: Path, page: Path) -> None:
    """(A) _reload_fallback_target: page→work.blend、work.blend自身→None."""

    fallback_for_page = handlers._reload_fallback_target(page)
    _check(
        fallback_for_page is not None
        and fallback_for_page.resolve() == work_blend.resolve(),
        "存在しないpage.blendのフォールバックがwork.blendを指しません",
    )
    fallback_for_work = handlers._reload_fallback_target(work_blend)
    _check(
        fallback_for_work is None,
        "work.blend自身のフォールバックがNoneではありません",
    )


def _case_retry_before_limit(handlers, work_blend: Path, page: Path) -> None:
    """(B) 存在しないpage.blendは上限未満ならリトライ間隔を返し、何も開かない."""

    _open(work_blend, "work")
    generation = handlers._native_save_reload_generation
    state = {"attempts": 0}
    assert not page.is_file()
    result = handlers._native_save_reload_tick(page, generation, state)
    _check(
        result == handlers._NATIVE_SAVE_RELOAD_RETRY_INTERVAL,
        "リトライ間隔が返っていません",
    )
    _check(
        Path(bpy.data.filepath).resolve() == work_blend.resolve(),
        "リトライ中に別ファイルが開かれました",
    )
    _check(state["attempts"] == 1, "attemptsが加算されていません")


def _case_fallback_opens_work_blend_at_limit(handlers, work_blend: Path, page: Path) -> None:
    """(C) 上限到達でフォールバック(work.blend)を開く."""

    _open(work_blend, "work")
    generation = handlers._native_save_reload_generation
    state = {"attempts": handlers._NATIVE_SAVE_RELOAD_MAX_ATTEMPTS - 1}
    assert not page.is_file()
    result = handlers._native_save_reload_tick(page, generation, state)
    _check(result is None, "上限到達時の戻り値がNoneではありません")
    _check(
        Path(bpy.data.filepath).resolve() == work_blend.resolve(),
        "上限到達後にwork.blendへフォールバックしていません",
    )


def _case_generation_mismatch_does_nothing(handlers, work_blend: Path, page: Path) -> None:
    """(D) 世代不一致なら何も開かずNoneを返す."""

    _open(work_blend, "work")
    stale_generation = handlers._native_save_reload_generation + 1
    state = {"attempts": 0}
    result = handlers._native_save_reload_tick(page, stale_generation, state)
    _check(result is None, "世代不一致時の戻り値がNoneではありません")
    _check(
        Path(bpy.data.filepath).resolve() == work_blend.resolve(),
        "世代不一致にもかかわらずファイルが開かれました",
    )
    _check(state["attempts"] == 0, "世代不一致でもattemptsが加算されています")


def _case_existing_target_is_opened(handlers, work_blend: Path, page: Path) -> None:
    """(E) 再読込対象が実在すればそのファイルを開く."""

    page.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work_blend, page)
    _open(work_blend, "work")
    generation = handlers._native_save_reload_generation
    state = {"attempts": 0}
    assert page.is_file()
    result = handlers._native_save_reload_tick(page, generation, state)
    _check(result is None, "存在するファイルの再読込後の戻り値がNoneではありません")
    _check(
        Path(bpy.data.filepath).resolve() == page.resolve(),
        "存在するpage.blendへ再読込されていません",
    )


def _case_changed_file_cancels_stale_timer(handlers, work_blend: Path, page: Path) -> None:
    """(F) 待機中に別blendへ移動したら、古い予約は現在画面を奪わない."""

    page.unlink(missing_ok=True)
    other = work_blend.with_name("other.blend")
    shutil.copy2(work_blend, other)
    _open(other, "work")
    state = {"attempts": 0, "origin": str(work_blend)}
    result = handlers._native_save_reload_tick(
        page, handlers._native_save_reload_generation, state,
    )
    _check(result is None, "別ファイル移動後も古いタイマーが継続しました")
    _check(state["attempts"] == 0, "別ファイル移動後にattemptsが加算されました")
    _check(Path(bpy.data.filepath).resolve() == other.resolve(), "古いタイマーが別ファイルを開きました")


def _case_transient_open_failure_retries(handlers, work_blend: Path, page: Path) -> None:
    """(G) 実在ファイルの一時読込失敗も上限内では再試行する."""

    shutil.copy2(work_blend, page)
    _open(work_blend, "work")
    original = handlers._open_native_reload_target

    def _fail_once(_path):
        raise OSError("temporary sharing violation")

    handlers._open_native_reload_target = _fail_once
    try:
        state = {"attempts": 0, "origin": str(work_blend)}
        result = handlers._native_save_reload_tick(
            page, handlers._native_save_reload_generation, state,
        )
    finally:
        handlers._open_native_reload_target = original
    _check(result == handlers._NATIVE_SAVE_RELOAD_RETRY_INTERVAL, "一時読込失敗を再試行しません")
    _check(state["attempts"] == 1, "一時読込失敗でattemptsが加算されません")
    _check(Path(bpy.data.filepath).resolve() == work_blend.resolve(), "一時失敗中に画面が移動しました")
    result = handlers._native_save_reload_tick(
        page, handlers._native_save_reload_generation, state,
    )
    _check(result is None, "再試行成功後の戻り値がNoneではありません")
    _check(Path(bpy.data.filepath).resolve() == page.resolve(), "再試行で対象を開けません")


def _case_work_open_preflight_restores_missing_work_blend(work_op, native_guard, root: Path) -> None:
    """(H) work.blend退避直後の異常終了も「作品を開く」入口で復旧する."""

    work = root / "OpenPreflight.bmanga"
    _write_json(work / "work.json", {"detailDataVersion": 0})
    source = work / "work.blend"
    source.write_bytes(b"latest-work")
    token = native_guard.begin_native_save(source, 0)
    _check(token is not None and token.requires_restore, "中断保存の復旧トークンを作れません")
    _check(not source.exists(), "退避直後のwork.blend欠落状態を再現できません")
    native_guard._release(token)
    recovered, error = work_op._recover_selected_work_before_open(work)
    _check(recovered and not error and source.read_bytes() == b"latest-work", "作品を開く前にwork.blendを復旧できません")


EXPECTED_CHECK_COUNT = 23


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_native_reload_fallback_"))
    succeeded = False
    try:
        addon = _load_addon()
        handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
        work_op = importlib.import_module(f"{MODULE_NAME}.operators.work_op")
        native_guard = importlib.import_module(f"{MODULE_NAME}.io.project_content_native_save_guard")
        _work, work_blend, page = _create_work(temp_root)

        _case_fallback_target(handlers, work_blend, page)
        _case_retry_before_limit(handlers, work_blend, page)
        _case_fallback_opens_work_blend_at_limit(handlers, work_blend, page)
        _case_generation_mismatch_does_nothing(handlers, work_blend, page)
        _case_existing_target_is_opened(handlers, work_blend, page)
        _case_changed_file_cancels_stale_timer(handlers, work_blend, page)
        _case_transient_open_failure_retries(handlers, work_blend, page)
        _case_work_open_preflight_restores_missing_work_blend(work_op, native_guard, temp_root)

        assert _counters["checks"] == EXPECTED_CHECK_COUNT, (
            f"検証アサートの実行数が想定と異なります: {_counters['checks']}"
            f" (期待 {EXPECTED_CHECK_COUNT})"
        )
        succeeded = True
        print("BMANGA_NATIVE_SAVE_RELOAD_FALLBACK_CHECK_OK", flush=True)
    except Exception:
        traceback.print_exc()
        succeeded = False
    finally:
        try:
            bpy.ops.wm.read_factory_settings(use_empty=True)
        except Exception:
            pass
        if addon is not None:
            try:
                addon.unregister()
            except Exception:
                pass
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=True)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}")
    os._exit(0 if succeeded else 1)


if __name__ == "__main__":
    main()
