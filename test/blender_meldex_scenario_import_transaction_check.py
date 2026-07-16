"""Blender 5.1: Meldex取込のファイル一括復元と競合拒否を検証する。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_meldex_import_transaction"


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


def _payload(page_count: int = 3, body: str = "初回本文") -> dict:
    return {
        "contract": "meldex-bmanga-scenario",
        "version": 1,
        "source": {"documentId": "transaction-scenario"},
        "pages": [
            {
                "rows": [
                    {
                        "rowId": f"row-{index + 1}",
                        "type": "会話",
                        "body": body if index == 0 else f"{index + 1}ページ",
                        "rubies": [],
                    }
                ]
            }
            for index in range(page_count)
        ],
    }


def _json_snapshot(work_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(work_dir)): path.read_bytes()
        for path in work_dir.rglob("*.json")
        if ".bmanga-save-recovery-v1" not in path.parts
    }


def _first_body(page_detail, work) -> str:
    page_detail.ensure_page_detail(work, work.pages[0])
    return next(
        text.body
        for text in work.pages[0].texts
        if text.meldex_source_row_id == "row-1"
    )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_meldex_transaction_"))
    addon = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon = _load_addon()
        work_path = temp_root / "Transaction.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(work_path)) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        work_dir = Path(str(work.work_dir))

        from bmanga_dev_meldex_import_transaction import preferences
        from bmanga_dev_meldex_import_transaction.io import (
            balloon_presets,
            meldex_scenario_import,
            page_io,
            project_content_save_baseline as baseline,
            text_presets,
        )
        from bmanga_dev_meldex_import_transaction.utils import json_io, page_detail, paths

        preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=False
        )
        balloon_presets.list_all_presets = lambda _path: []
        text_presets.list_all_presets = lambda _path: []
        assert len(work.pages) == 1

        # 既存page.jsonが壊れている場合は、空のページ詳細で上書きせず中止する。
        page = work.pages[0]
        page_json = paths.page_meta_path(work_dir, str(page.id))
        original_page = page_json.read_bytes()
        page_detail.clear_page_detail(page)
        page_json.write_bytes(b"{broken")
        baseline.record_successful_write(page_json)
        try:
            meldex_scenario_import.import_payload(bpy.context, work, _payload())
        except RuntimeError as exc:
            assert "ページ情報を読み込めない" in str(exc), exc
        else:
            raise AssertionError("壊れたpage.jsonの取込が中止されなかった")
        assert page_json.read_bytes() == b"{broken"
        assert len(work.pages) == 1 and not work.pages[0].detail_loaded
        assert not paths.scenario_file(work_dir).exists()
        page_json.write_bytes(original_page)
        baseline.record_successful_write(page_json)

        # 一覧に登録済みなのにpage.jsonだけ無いページも、新規ページとは
        # みなさず書込み前に中止する。
        page_json.unlink()
        baseline.record_successful_write(page_json)
        try:
            meldex_scenario_import.import_payload(bpy.context, work, _payload())
        except RuntimeError as exc:
            assert "ページ情報ファイルがない" in str(exc), exc
        else:
            raise AssertionError("欠損page.jsonの取込が中止されなかった")
        assert len(work.pages) == 1 and not work.pages[0].detail_loaded
        assert not paths.scenario_file(work_dir).exists()
        page_json.write_bytes(original_page)
        baseline.record_successful_write(page_json)

        # Dropbox等が読込後にpages.jsonを書き換えた場合は、その内容を保ったまま
        # 何も変更せず中止する。
        pages_json = paths.pages_meta_path(work_dir)
        original_pages = pages_json.read_bytes()
        external_pages = b'{"external":"dropbox"}'
        pages_json.write_bytes(external_pages)
        try:
            meldex_scenario_import.import_payload(bpy.context, work, _payload())
        except RuntimeError as exc:
            assert "変更" in str(exc) or "更新" in str(exc) or "競合" in str(exc), exc
        else:
            raise AssertionError("外部変更を伴う取込が中止されなかった")
        assert pages_json.read_bytes() == external_pages
        assert len(work.pages) == 1
        pages_json.write_bytes(original_pages)
        baseline.record_successful_write(pages_json)

        # 新規2ページと既存ページを書いた後の失敗でも、全ファイル・メモリ・
        # 新規ディレクトリを取込前へ戻す。
        before_failure = _json_snapshot(work_dir)
        before_counter = int(work.balloon_id_counter)
        original_save_page = page_io.save_page_json
        p0002_calls = 0

        def fail_second_p0002_save(target_work_dir, target_page):
            nonlocal p0002_calls
            if str(target_page.id) == "p0002":
                p0002_calls += 1
                if p0002_calls == 2:
                    raise OSError("injected page.json failure")
            return original_save_page(target_work_dir, target_page)

        page_io.save_page_json = fail_second_p0002_save
        try:
            meldex_scenario_import.import_payload(bpy.context, work, _payload())
        except OSError as exc:
            assert "injected" in str(exc)
        else:
            raise AssertionError("途中失敗が呼出元へ返らなかった")
        finally:
            page_io.save_page_json = original_save_page
        assert _json_snapshot(work_dir) == before_failure
        assert len(work.pages) == 1 and not work.pages[0].detail_loaded
        assert int(work.balloon_id_counter) == before_counter
        assert not (work_dir / "p0002").exists()
        assert not (work_dir / "p0003").exists()
        assert not (work_dir / ".bmanga-save-recovery-v1").exists()

        # 初回のscenario/imported.json書込みが完了した直後に失敗しても、
        # 保存コピーと今回新設した空のscenarioフォルダーを残さない。
        initial_scenario_dir = paths.scenario_dir(work_dir)
        if initial_scenario_dir.exists():
            initial_scenario_dir.rmdir()
        original_write_json = json_io.write_json
        scenario_copy = paths.scenario_file(work_dir).resolve()

        def fail_after_first_scenario_copy(path, data):
            result = original_write_json(path, data)
            if Path(path).resolve() == scenario_copy:
                raise OSError("injected first imported.json failure")
            return result

        json_io.write_json = fail_after_first_scenario_copy
        try:
            meldex_scenario_import.import_payload(bpy.context, work, _payload())
        except OSError as exc:
            assert "first imported.json" in str(exc)
        else:
            raise AssertionError("初回保存コピー直後の失敗が返らなかった")
        finally:
            json_io.write_json = original_write_json
        assert _json_snapshot(work_dir) == before_failure
        assert len(work.pages) == 1 and not work.pages[0].detail_loaded
        assert not (work_dir / "p0002").exists()
        assert not (work_dir / "p0003").exists()
        assert not paths.scenario_dir(work_dir).exists()

        # 同じ内容で再試行し、復元前の差分書込キャッシュがpage.json保存を
        # 誤って省略しないことも確認する。
        result = meldex_scenario_import.import_payload(bpy.context, work, _payload())
        assert result == {"pagesAdded": 2, "created": 3, "updated": 0, "ignored": 0}, result
        assert len(work.pages) == 3
        saved_page = json.loads(page_json.read_text(encoding="utf-8"))
        assert saved_page["texts"][0]["body"] == "初回本文"
        saved_work = json.loads(paths.work_meta_path(work_dir).read_text(encoding="utf-8"))
        assert saved_work["balloonIdCounter"] == int(work.balloon_id_counter) > 0
        assert paths.coma_json_path(work_dir, "p0002", "c01").is_file()
        assert paths.coma_json_path(work_dir, "p0003", "c01").is_file()
        assert not (work_dir / ".bmanga-save-recovery-v1").exists()

        # ページ群を保存し終え、最後のimported.jsonで失敗しても、取込前の
        # 本文・JSON全件へ戻す。
        old_body = _first_body(page_detail, work)
        before_copy_failure = _json_snapshot(work_dir)
        original_write_json = json_io.write_json

        def fail_scenario_copy(path, data):
            if Path(path).resolve() == scenario_copy:
                raise OSError("injected imported.json failure")
            return original_write_json(path, data)

        json_io.write_json = fail_scenario_copy
        try:
            meldex_scenario_import.import_payload(
                bpy.context,
                work,
                _payload(body="失敗時の本文"),
            )
        except OSError as exc:
            assert "imported.json" in str(exc)
        else:
            raise AssertionError("保存コピー失敗が呼出元へ返らなかった")
        finally:
            json_io.write_json = original_write_json
        assert _json_snapshot(work_dir) == before_copy_failure
        assert _first_body(page_detail, work) == old_body
        assert not (work_dir / ".bmanga-save-recovery-v1").exists()

        # 再起動相当でimported.jsonが競合基準に未登録でも、操作開始時に内容を
        # 観測してから安全に再取込できる。
        page_paths = tuple(
            paths.page_meta_path(work_dir, str(item.id)) for item in work.pages
        )
        baseline.capture_loaded_baseline(
            work_dir,
            paths.work_blend_path(work_dir),
            page_json_paths=page_paths,
        )
        result = meldex_scenario_import.import_payload(
            bpy.context,
            work,
            _payload(body="再起動後の本文"),
        )
        assert result["pagesAdded"] == 0 and result["updated"] == 3
        assert _first_body(page_detail, work) == "再起動後の本文"

        # pages.jsonに無いページフォルダーは既存データの可能性があるため再利用せず、
        # 中身を一切変更しない。
        orphan = work_dir / "p0004"
        orphan.mkdir()
        (orphan / "unknown.txt").write_text("keep", encoding="utf-8")
        try:
            meldex_scenario_import.import_payload(bpy.context, work, _payload(page_count=4))
        except RuntimeError as exc:
            assert "未登録のページフォルダー" in str(exc), exc
        else:
            raise AssertionError("未登録ページフォルダーの再利用が拒否されなかった")
        assert len(work.pages) == 3
        assert (orphan / "unknown.txt").read_text(encoding="utf-8") == "keep"
        print("BMANGA_MELDEX_SCENARIO_IMPORT_TRANSACTION_OK")
    finally:
        if addon is not None:
            try:
                addon.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
