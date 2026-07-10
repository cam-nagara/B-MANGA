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
        from bmanga_dev_meldex_import.io import balloon_presets, meldex_scenario_import, page_io, text_presets

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
        text_presets.list_all_presets = lambda _path: [SimpleNamespace(name="会話", data={"font_bold": True, "writing_mode": "horizontal"})]

        first = meldex_scenario_import.import_payload(bpy.context, work, _payload())
        assert first == {"pagesAdded": 1, "created": 4, "updated": 0, "ignored": 0}, first
        assert (temp_root / "scenario" / "imported.json").is_file()
        assert len(work.pages) == 3
        assert not work.pages[2].detail_loaded, "追加ページの詳細は保存後に解放する"
        from bmanga_dev_meldex_import.utils import page_detail
        page_detail.ensure_page_detail(work, work.pages[2])
        assert [len(page.comas) for page in work.pages] == [1, 0, 0]
        assert any(item.id == "manual-balloon" for item in work.pages[0].balloons)
        imported = [item for item in work.pages[0].balloons if item.meldex_source_row_id]
        assert imported[0].shape == "custom" and imported[0].custom_preset_name == "会話"
        assert imported[1].shape == "ellipse", "空白を除去した曖昧一致は禁止"
        text = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert text.body == "東京\nです"
        assert text.font_bold and text.writing_mode == "horizontal"
        assert len(text.ruby_spans) == 1 and text.ruby_spans[0].ruby_text == "とうきょう"
        balloon = next(item for item in imported if item.meldex_source_row_id == "r1")
        assert balloon.width_mm > text.width_mm and balloon.height_mm > text.height_mm
        all_imported_ids = [item.id for page in work.pages for item in page.balloons if item.meldex_source_row_id]
        assert len(all_imported_ids) == len(set(all_imported_ids)), "フキダシIDは作品内で一意"

        counts = [(len(page.balloons), len(page.texts), len(page.comas)) for page in work.pages]
        balloon.line_width_mm = 4.0
        balloon.fill_color = (0.2, 0.3, 0.4, 1.0)
        changed = _payload()
        changed["pages"][0]["rows"][0]["type"] = "未登録"
        second = meldex_scenario_import.import_payload(bpy.context, work, changed)
        assert second["created"] == 0 and second["updated"] == 4
        assert counts == [(len(page.balloons), len(page.texts), len(page.comas)) for page in work.pages]
        text = next(item for item in work.pages[0].texts if item.meldex_source_row_id == "r1")
        assert not text.font_bold and text.writing_mode == "vertical", "未登録タイプは標準テキストへ戻す"
        balloon = next(item for item in work.pages[0].balloons if item.meldex_source_row_id == "r1")
        assert balloon.shape == "ellipse" and not balloon.custom_preset_name
        assert abs(balloon.line_width_mm - 0.3) < 1.0e-6 and tuple(balloon.fill_color) == (1.0, 1.0, 1.0, 1.0)
        missing_index = next(i for i, item in enumerate(work.pages[0].balloons) if item.meldex_source_row_id == "r1")
        work.pages[0].balloons.remove(missing_index)
        repaired = meldex_scenario_import.import_payload(bpy.context, work, _payload())
        assert repaired["created"] == 1
        repaired_balloon = next(item for item in work.pages[0].balloons if item.meldex_source_row_id == "r1")
        assert repaired_balloon.shape == "custom" and repaired_balloon.custom_preset_name == "会話"
        print("BMANGA_MELDEX_SCENARIO_IMPORT_OK")
    finally:
        try:
            addon.unregister()
        finally:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
