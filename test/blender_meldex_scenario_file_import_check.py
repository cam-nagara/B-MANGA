"""Blender 5.1: 作品ファイルパネル経由のMeldexシナリオ読込を確認する."""

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
        "editor": {
            "viewMode": "vertical",
            "baseTextFontSize": 16,
            "baseTextLineHeightV": 1.7,
            "baseTextLetterSpacingV": 0.15,
            "baseTextBold": "bold",
            "baseTextColor": "#808080",
        },
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
            {
                "name": "セリフ",
                "textStyle": {
                    "fontSize": 18,
                    "fontStyle": "italic",
                    "textColor": "#404040",
                },
            },
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
        from bmanga_dev_meldex_scenario_file import preferences
        from bmanga_dev_meldex_scenario_file.io import text_presets

        preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=False
        )

        text_presets.list_all_presets = lambda _path: [
            SimpleNamespace(
                name="セリフ",
                data={
                    "writing_mode": "horizontal",
                    "ruby_size_percent": 58.0,
                    "ruby_gap_em": -0.05,
                    "ruby_letter_spacing": 0.03,
                    "ruby_line_height": 1.6,
                    "ruby_align": "center",
                    "ruby_small_kana": "keep",
                    "ruby_font_preset": "gothic-jp",
                    "ruby_default_style": "group",
                    "linked_balloon_preset": "",
                },
            ),
            SimpleNamespace(
                name="ナレーション",
                data={"writing_mode": "vertical", "ruby_size_percent": 62.0},
            ),
        ]

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

        # 取込は作品ファイル上で行われ、成功後はページ詳細がメモリへ残らない
        # (残すと一覧でそのページのテキストだけ選択できてしまう)。検証は
        # JSON から読み戻してから行う。
        def _ensure_all_details():
            for page in work.pages:
                page_detail.ensure_page_detail(work, page)

        def _loaded_counts():
            _ensure_all_details()
            return [(len(page.balloons), len(page.texts)) for page in work.pages]

        assert all(
            not bool(page.detail_loaded) for page in work.pages
        ), "取込後にページ詳細がメモリへ残っています"
        _ensure_all_details()
        first = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        second = next(item for item in work.pages[1].texts if item.meldex_source_row_id == "r2")
        assert first.body == "東京です"
        assert first.meldex_source_document_id == str(scenario_path.resolve())
        assert first.writing_mode == "horizontal"
        assert abs(first.ruby_size_percent - 58.0) < 1.0e-6
        assert abs(first.ruby_gap_em - (-0.05)) < 1.0e-6
        assert abs(first.ruby_letter_spacing - 0.03) < 1.0e-6
        assert abs(first.ruby_line_height - 1.6) < 1.0e-6
        assert first.ruby_align == "center" and first.ruby_small_kana == "keep"
        assert first.ruby_font_preset == "gothic-jp"
        assert first.ruby_default_style == "group"
        assert len(first.ruby_spans) == 1
        assert first.ruby_spans[0].ruby_text == "とうきょう"
        assert first.ruby_spans[0].style == "jukugo"
        assert second.body == "大阪" and second.ruby_spans[0].ruby_text == "おおさか"
        assert not any(
            item.meldex_source_row_id == "plot"
            for page in work.pages for item in page.texts
        )

        # 同じ保存ファイルをオンで再読込すると、本文・ルビの表示設定だけを
        # Meldex側へ切り替え、本文内容や取込IDはそのまま更新する。
        preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=True
        )
        result = bpy.ops.bmanga.meldex_scenario_file_import(
            "EXEC_DEFAULT", filepath=str(scenario_path)
        )
        assert result == {"FINISHED"}, result
        _ensure_all_details()
        first = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert first.writing_mode == "vertical"
        assert abs(first.font_size_q - 18.0 * 127.0 / 120.0) < 1.0e-5
        assert abs(first.line_height - 1.7) < 1.0e-6
        assert abs(first.letter_spacing - 0.15) < 1.0e-6
        assert first.font_bold and first.font_italic
        assert abs(first.ruby_size_percent - 65.0) < 1.0e-6
        assert abs(first.ruby_gap_em - 0.25) < 1.0e-6
        preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=False
        )

        counts = _loaded_counts()
        scenario_path.write_text(
            json.dumps(_document("{東京|とうきょう}を更新"), ensure_ascii=False),
            encoding="utf-8",
        )
        result = bpy.ops.bmanga.meldex_scenario_file_import(
            "EXEC_DEFAULT", filepath=str(scenario_path)
        )
        assert result == {"FINISHED"}, result
        assert counts == _loaded_counts()
        first = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert first.body == "東京を更新"

        legacy_document = _document("幽奈")
        legacy_document["rubyPresentation"] = {
            **legacy_document["rubyPresentation"],
            "sizePercent": 75,
            "gapEm": 0.4,
            "compatibility": {
                "legacySizeEm": 0.55,
                "legacyOffsetPx": 3.5,
                "useLegacySize": True,
                "useLegacyGap": True,
            },
        }
        legacy_path = temp_root / "旧互換.scriptnote.json"
        legacy_path.write_text(
            json.dumps(legacy_document, ensure_ascii=False), encoding="utf-8"
        )
        result = bpy.ops.bmanga.meldex_scenario_file_import(
            "EXEC_DEFAULT", filepath=str(legacy_path)
        )
        assert result == {"FINISHED"}, result
        _ensure_all_details()
        legacy = next(
            item
            for item in work.pages[0].texts
            if item.meldex_source_document_id == str(legacy_path.resolve())
            and item.meldex_source_row_id == "r1"
        )
        assert legacy.writing_mode == "horizontal"
        assert abs(legacy.ruby_size_percent - 58.0) < 1.0e-6
        assert abs(legacy.ruby_gap_em - (-0.05)) < 1.0e-6

        horizontal_document = json.loads(json.dumps(legacy_document, ensure_ascii=False))
        horizontal_document["rubyPresentation"]["writingMode"] = "horizontal"
        horizontal_path = temp_root / "旧互換_横書き.scriptnote.json"
        horizontal_path.write_text(
            json.dumps(horizontal_document, ensure_ascii=False), encoding="utf-8"
        )
        result = bpy.ops.bmanga.meldex_scenario_file_import(
            "EXEC_DEFAULT", filepath=str(horizontal_path)
        )
        assert result == {"FINISHED"}, result
        _ensure_all_details()
        horizontal = next(
            item
            for item in work.pages[0].texts
            if item.meldex_source_document_id == str(horizontal_path.resolve())
            and item.meldex_source_row_id == "r1"
        )
        assert horizontal.writing_mode == "horizontal"
        assert abs(horizontal.ruby_size_percent - 58.0) < 1.0e-6
        assert abs(horizontal.ruby_gap_em - (-0.05)) < 1.0e-6

        bad_path = temp_root / "broken.mel-scenario"
        bad_path.write_text("{broken", encoding="utf-8")
        counts_before = _loaded_counts()
        try:
            result = bpy.ops.bmanga.meldex_scenario_file_import(
                "EXEC_DEFAULT", filepath=str(bad_path)
            )
        except RuntimeError as exc:
            assert "JSONが壊れています" in str(exc)
        else:
            assert result == {"CANCELLED"}, result
        assert counts_before == _loaded_counts()
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
