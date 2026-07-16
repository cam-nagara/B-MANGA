"""Blender 5.1: 作品ファイルパネル経由のMeldexシナリオ読込を確認する."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_meldex_scenario_file"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _document(body: str = "{東京|とうきょう}です") -> dict:
    return {
        "fileType": "meldex-scriptnote",
        "schema_version": 2,
        "version": 2,
        "title": "読込確認",
        "layoutMode": "manga",
        "rubyPresentation": {
            "version": 2,
            "writingMode": "vertical",
            "sizePercent": 65,
            "gapEm": 0.25,
            "letterSpacingEm": 0.1,
            "lineHeight": 2.0,
            "align": "start",
            "smallKana": "fullsize",
            "fontPreset": "serif-jp",
            "defaultStyle": "jukugo",
        },
        "characters": [
            {"name": "めくり", "isBreak": True},
            {"name": "プロット", "isSummary": True},
        ],
        "rubyRules": [{"text": "大阪", "ruby": "おおさか", "style": "group"}],
        "rows": [
            {"id": "r1", "role": "セリフ", "text": body},
            {"id": "break", "role": "めくり", "text": ""},
            {"id": "r2", "role": "ナレーション", "text": "大阪"},
            {"id": "plot", "role": "プロット", "text": "取込対象外"},
        ],
        "source": {},
    }


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_meldex_file_import_"))
    addon = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon = _load_addon()
        work_path = temp_root / "FileImport.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(work_path)) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work

        scenario_path = temp_root / "第1話.mel-scenario"
        scenario_path.write_text(
            json.dumps(_document(), ensure_ascii=False), encoding="utf-8"
        )
        assert bpy.ops.bmanga.meldex_scenario_file_import.poll()
        result = bpy.ops.bmanga.meldex_scenario_file_import(
            "EXEC_DEFAULT", filepath=str(scenario_path)
        )
        assert result == {"FINISHED"}, result
        assert len(work.pages) == 2

        from bmanga_dev_meldex_scenario_file.utils import page_detail

        page_detail.ensure_page_detail(work, work.pages[0])
        page_detail.ensure_page_detail(work, work.pages[1])
        first = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        second = next(item for item in work.pages[1].texts if item.meldex_source_row_id == "r2")
        assert first.body == "東京です"
        assert first.meldex_source_document_id == str(scenario_path.resolve())
        assert first.writing_mode == "vertical"
        assert abs(first.ruby_size_percent - 65.0) < 1.0e-6
        assert abs(first.ruby_gap_em - 0.25) < 1.0e-6
        assert first.ruby_default_style == "jukugo"
        assert len(first.ruby_spans) == 1
        assert first.ruby_spans[0].ruby_text == "とうきょう"
        assert first.ruby_spans[0].style == "jukugo"
        assert second.body == "大阪" and second.ruby_spans[0].ruby_text == "おおさか"
        assert not any(
            item.meldex_source_row_id == "plot"
            for page in work.pages for item in page.texts
        )

        counts = [(len(page.balloons), len(page.texts)) for page in work.pages]
        scenario_path.write_text(
            json.dumps(_document("{東京|とうきょう}を更新"), ensure_ascii=False),
            encoding="utf-8",
        )
        result = bpy.ops.bmanga.meldex_scenario_file_import(
            "EXEC_DEFAULT", filepath=str(scenario_path)
        )
        assert result == {"FINISHED"}, result
        assert counts == [(len(page.balloons), len(page.texts)) for page in work.pages]
        first = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert first.body == "東京を更新"

        bad_path = temp_root / "broken.mel-scenario"
        bad_path.write_text("{broken", encoding="utf-8")
        counts_before = [(len(page.balloons), len(page.texts)) for page in work.pages]
        try:
            result = bpy.ops.bmanga.meldex_scenario_file_import(
                "EXEC_DEFAULT", filepath=str(bad_path)
            )
        except RuntimeError as exc:
            assert "JSONが壊れています" in str(exc)
        else:
            assert result == {"CANCELLED"}, result
        assert counts_before == [(len(page.balloons), len(page.texts)) for page in work.pages]
        print("BMANGA_MELDEX_SCENARIO_FILE_IMPORT_OK")
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
