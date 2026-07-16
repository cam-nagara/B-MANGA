"""Blender 5.1: Meldex scenario import persistence and idempotency."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_meldex_import"


def _load_addon():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _payload():
    return {
        "contract": "meldex-bmanga-scenario",
        "version": 1,
        "source": {"documentId": "scenario-1"},
        "pages": [
            {"rows": [
                {"rowId": "r1", "type": "会話", "body": "東京\nです", "rubies": [{"start": 0, "length": 2, "rubyText": "とうきょう", "style": "group"}]},
                {"rowId": "r2", "type": " 会話", "body": "完全一致のみ", "rubies": []},
            ]},
            {"rows": [{"rowId": "r3", "type": "", "body": "二頁", "rubies": []}]},
            {"rows": [{"rowId": "r4", "type": "", "body": "追加頁", "rubies": []}]},
        ],
    }


def main() -> None:
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_meldex_import_"))
    try:
        work = bpy.context.scene.bmanga_work
        work.loaded = True
        work.work_dir = str(temp_root)
        from bmanga_dev_meldex_import import preferences
        from bmanga_dev_meldex_import.io import balloon_presets, meldex_scenario_import, page_io, text_presets

        preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=False
        )

        for _index in range(2):
            page = page_io.register_new_page(work)
            page_io.ensure_page_dir(temp_root, page.id)
            page.detail_loaded = True
        work.active_page_index = 0
        coma = work.pages[0].comas.add()
        coma.id = "manual-coma"
        manual = work.pages[0].balloons.add()
        manual.id = "manual-balloon"
        balloon_presets.list_all_presets = lambda _path: [SimpleNamespace(name="会話", data={})]
        first_font = r"C:\Windows\Fonts\YuGothM.ttc"
        dialogue_font = r"C:\Windows\Fonts\msgothic.ttc"
        text_presets.list_all_presets = lambda _path: [
            SimpleNamespace(
                name="先頭既定",
                data={
                    "font": first_font,
                    "font_bold": False,
                    "writing_mode": "vertical",
                    "line_height": 1.5,
                    "linked_balloon_preset": "",
                },
            ),
            SimpleNamespace(
                name="会話",
                data={
                    "font": dialogue_font,
                    "font_bold": True,
                    "writing_mode": "horizontal",
                    "line_height": 1.8,
                    "ruby_size_percent": 72.0,
                    "ruby_gap_em": 0.12,
                    "ruby_letter_spacing": 0.07,
                    "ruby_line_height": 1.7,
                    "ruby_align": "center",
                    "ruby_small_kana": "keep",
                    "ruby_font_preset": "gothic-jp",
                    "ruby_default_style": "mono",
                    "linked_balloon_preset": "会話",
                },
            )
        ]

        first = meldex_scenario_import.import_payload(bpy.context, work, _payload())
        assert first == {"pagesAdded": 1, "created": 4, "updated": 0, "ignored": 0}, first
        assert (temp_root / "scenario" / "imported.json").is_file()
        assert len(work.pages) == 3
        assert not work.pages[2].detail_loaded, "追加ページの詳細は保存後に解放する"
        from bmanga_dev_meldex_import.utils import page_detail
        page_detail.ensure_page_detail(work, work.pages[2])
        # 2026-07-12: 取込で追加した不足ページには、通常のページ追加と同じ
        # 基本枠コマを1個自動生成する。既存ページ (0, 1) は変更しない。
        assert [len(page.comas) for page in work.pages] == [1, 0, 1]
        added_coma = work.pages[2].comas[0]
        assert added_coma.shape_type == "rect"
        p = work.paper
        assert abs(float(added_coma.rect_width_mm) - float(p.inner_frame_width_mm)) < 1.0e-6
        assert abs(float(added_coma.rect_height_mm) - float(p.inner_frame_height_mm)) < 1.0e-6
        assert any(item.id == "manual-balloon" for item in work.pages[0].balloons)
        imported = [item for item in work.pages[0].balloons if item.meldex_source_row_id]
        assert imported[0].shape == "custom" and imported[0].custom_preset_name == "会話"
        assert imported[1].shape == "ellipse", "空白を除去した曖昧一致は禁止"
        text = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert text.body == "東京\nです"
        assert text.font_bold and text.writing_mode == "horizontal"
        assert abs(float(text.line_height) - 1.8) < 1.0e-6, "完全一致プリセットの行間を適用する"
        assert text.font == dialogue_font, "完全一致プリセットのフォントを適用する"
        assert len(text.ruby_spans) == 1 and text.ruby_spans[0].ruby_text == "とうきょう"
        fallback_text = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r2")
        assert fallback_text.font == first_font and fallback_text.writing_mode == "vertical", (
            "完全一致しないタイプにはリスト先頭のテキストプリセットをフォント込みで適用する"
        )
        assert abs(float(fallback_text.line_height) - 1.5) < 1.0e-6, (
            "完全一致しないタイプにもリスト先頭の行間を適用する"
        )
        balloon = next(item for item in imported if item.meldex_source_row_id == "r1")
        assert balloon.width_mm > text.width_mm and balloon.height_mm > text.height_mm
        all_imported_ids = [item.id for page in work.pages for item in page.balloons if item.meldex_source_row_id]
        assert len(all_imported_ids) == len(set(all_imported_ids)), "フキダシIDは作品内で一意"

        # コマ指定のないシナリオ要素は、既存コマより前面のページ直下へ作る。
        from bmanga_dev_meldex_import.utils import layer_stack
        stack = layer_stack.sync_layer_stack(bpy.context)
        assert stack is not None
        coma_key = f"{work.pages[0].id}:{coma.id}"
        coma_index = next(
            i for i, item in enumerate(stack)
            if layer_stack.stack_item_uid(item) == layer_stack.target_uid("coma", coma_key)
        )
        imported_uids = []
        for imported_text in (item for item in work.pages[0].texts if item.meldex_source_row_id):
            imported_uids.append(layer_stack.target_uid("text", f"{work.pages[0].id}:{imported_text.id}"))
        for imported_balloon in imported:
            imported_uids.append(layer_stack.target_uid("balloon", f"{work.pages[0].id}:{imported_balloon.id}"))
        positions = {layer_stack.stack_item_uid(item): i for i, item in enumerate(stack)}
        assert imported_uids and all(positions[uid] < coma_index for uid in imported_uids), (
            "コマ指定のないシナリオ要素が既存コマより前面に作成されていません"
        )
        for imported_text in (item for item in work.pages[0].texts if item.meldex_source_row_id):
            assert imported_text.parent_kind == "page" and imported_text.parent_key == work.pages[0].id
        for imported_balloon in imported:
            assert imported_balloon.parent_kind == "page" and imported_balloon.parent_key == work.pages[0].id

        counts = [(len(page.balloons), len(page.texts), len(page.comas)) for page in work.pages]
        balloon.line_width_mm = 4.0
        balloon.fill_color = (0.2, 0.3, 0.4, 1.0)
        changed = _payload()
        changed["pages"][0]["rows"][0]["type"] = "未登録"
        changed["pages"][1]["rows"][0]["type"] = "会話"
        second = meldex_scenario_import.import_payload(bpy.context, work, changed)
        assert second["created"] == 0 and second["updated"] == 4
        assert counts == [(len(page.balloons), len(page.texts), len(page.comas)) for page in work.pages]
        text = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert not text.font_bold and text.writing_mode == "vertical", "未登録タイプは先頭プリセットへ切り替える"
        assert text.font == first_font, "タイプ変更時も先頭プリセットのフォントを適用する"
        formerly_blank = next(item for item in work.pages[1].texts if item.meldex_source_row_id == "r3")
        assert formerly_blank.font == dialogue_font and formerly_blank.font_bold, (
            "空タイプから完全一致タイプへ変えた時も一致プリセットを適用する"
        )
        balloon = next(item for item in work.pages[0].balloons if item.meldex_source_row_id == "r1")
        assert balloon.shape == "ellipse" and not balloon.custom_preset_name
        assert abs(balloon.line_width_mm - 0.3) < 1.0e-6 and tuple(balloon.fill_color) == (1.0, 1.0, 1.0, 1.0)
        missing_index = next(i for i, item in enumerate(work.pages[0].balloons) if item.meldex_source_row_id == "r1")
        work.pages[0].balloons.remove(missing_index)
        repaired = meldex_scenario_import.import_payload(bpy.context, work, _payload())
        assert repaired["created"] == 1
        repaired_balloon = next(item for item in work.pages[0].balloons if item.meldex_source_row_id == "r1")
        assert repaired_balloon.shape == "custom" and repaired_balloon.custom_preset_name == "会話"

        v2 = _payload()
        v2["version"] = 2
        v2["indexUnit"] = "unicode-code-point"
        v2["normalization"] = "none"
        v2["presentation"] = {"ruby": {
            "writingMode": "vertical", "sizePercent": 75.0, "gapEm": 0.25,
            "letterSpacingEm": 0.2, "lineHeight": 2.0, "align": "start",
            "smallKana": "fullsize", "fontPreset": "inherit",
        }}
        v2_row = v2["pages"][0]["rows"][0]
        v2_row["rubies"] = [
            {"start": 0, "length": 1, "rubyText": "とう", "style": "mono", "origin": "manual", "priority": 10},
            {"start": 0, "length": 2, "rubyText": "とうきょう", "style": "jukugo", "origin": "manual", "priority": 10,
             "segments": [{"start": 0, "length": 1, "rubyText": "とう"}, {"start": 1, "length": 1, "rubyText": "きょう"}]},
            {"start": 0, "length": 2, "rubyText": "低", "style": "group", "origin": "local-auto-dictionary", "priority": 1},
        ]
        meldex_scenario_import.import_payload(bpy.context, work, v2)
        text = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert text.writing_mode == "horizontal" and abs(text.ruby_size_percent - 72.0) < 1.0e-6
        assert abs(text.ruby_gap_em - 0.12) < 1.0e-6
        assert abs(text.ruby_letter_spacing - 0.07) < 1.0e-6
        assert abs(text.ruby_line_height - 1.7) < 1.0e-6 and text.ruby_align == "center"
        assert text.ruby_small_kana == "keep" and text.ruby_font_preset == "gothic-jp"
        assert text.ruby_default_style == "mono", "Meldex表示設定よりB-MANGAプリセットを優先する"
        assert len(text.ruby_spans) == 1 and text.ruby_spans[0].priority == 10
        assert text.ruby_spans[0].length == 2 and text.ruby_spans[0].ruby_text == "とうきょう"
        assert len(text.ruby_spans[0].segments) == 2 and text.ruby_spans[0].segments[1].ruby_text == "きょう"
        print("BMANGA_MELDEX_SCENARIO_IMPORT_OK")
    finally:
        try:
            addon.unregister()
        finally:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
