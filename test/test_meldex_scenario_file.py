from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCENARIO_FILE = _load_module("meldex_scenario_file_test", "io/meldex_scenario_file.py")
CONTRACT = _load_module("meldex_contract_file_test", "io/meldex_contract.py")


class MeldexScenarioFileTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bmanga_meldex_file_")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, name: str, document) -> Path:
        path = self.root / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def test_current_file_matches_meldex_default_export_semantics(self):
        path = self._write("第1話.mel-scenario", {
            "fileType": "meldex-scriptnote",
            "title": "第1話",
            "layoutMode": "manga",
            "rubyPresentation": {
                "writingMode": "vertical",
                "sizePercent": 65,
                "gapEm": 0.2,
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
            "rubyRules": [
                {"text": "前", "ruby": "まえ", "style": "mono"},
                {"text": "大阪", "ruby": "規則の読み"},
                {"text": "次", "ruby": "つぎ"},
            ],
            "rows": [
                {
                    "id": "r1",
                    "role": "セリフ",
                    "text": "前{東京|とうきょう}[{大阪|おおさか}](ml:大阪)後",
                },
                {"id": "break", "role": "めくり", "text": "区切り本文"},
                {
                    "id": "r2",
                    "role": "ナレーション",
                    "text": "次",
                    "rubyPresentation": {"sizePercent": 80},
                },
                {"id": "summary", "role": "プロット", "text": "出力しない"},
            ],
        })
        payload = SCENARIO_FILE.load_contract_payload(path)
        document = CONTRACT.validate_payload(payload)
        self.assertEqual(2, document.version)
        self.assertEqual(2, len(payload["pages"]))
        self.assertEqual(["r1"], [row["rowId"] for row in payload["pages"][0]["rows"]])
        self.assertEqual(["r2"], [row["rowId"] for row in payload["pages"][1]["rows"]])
        first = payload["pages"][0]["rows"][0]
        self.assertEqual("前東京大阪後", first["body"])
        self.assertEqual(
            [(0, "まえ", "document-rule"), (1, "とうきょう", "manual"), (3, "おおさか", "manual")],
            [(ruby["start"], ruby["rubyText"], ruby["origin"]) for ruby in first["rubies"]],
        )
        self.assertNotIn("規則の読み", [ruby["rubyText"] for ruby in first["rubies"]])
        self.assertEqual("jukugo", first["rubies"][1]["style"])
        self.assertEqual(65.0, payload["presentation"]["ruby"]["sizePercent"])
        self.assertEqual("vertical", payload["presentation"]["ruby"]["writingMode"])
        self.assertEqual(80.0, payload["pages"][1]["rows"][0]["presentation"]["ruby"]["sizePercent"])
        self.assertEqual(str(path.resolve()), payload["source"]["documentId"])

    def test_legacy_scriptnote_uses_stable_defaults_and_page_break_migration(self):
        path = self._write("旧形式.scriptnote.json", {
            "title": "旧形式",
            "layoutMode": "manga",
            "editor": {"viewMode": "vertical"},
            "characters": [{"name": "改ページ", "kind": "break"}],
            "rows": [
                {"id": "a", "role": "", "text": "最初"},
                {"id": "b", "role": "改ページ", "text": ""},
                {"id": "c", "role": "", "text": "次"},
            ],
        })
        payload = SCENARIO_FILE.load_contract_payload(path)
        CONTRACT.validate_payload(payload)
        ruby = payload["presentation"]["ruby"]
        self.assertEqual("vertical", ruby["writingMode"])
        self.assertAlmostEqual(55.0, ruby["sizePercent"])
        self.assertAlmostEqual(-3.0 / 28.0, ruby["gapEm"])
        self.assertEqual(1.0, ruby["lineHeight"])
        self.assertEqual(2, len(payload["pages"]))

    def test_saved_legacy_compatibility_is_converted_from_renderer_coordinates(self):
        presentation = {
            "version": 2,
            "writingMode": "vertical",
            "sizePercent": 75,
            "gapEm": 0.4,
            "letterSpacingEm": 0,
            "lineHeight": 1,
            "align": "center",
            "smallKana": "keep",
            "fontPreset": "inherit",
            "defaultStyle": "group",
            "compatibility": {
                "legacySizeEm": 0.55,
                "legacyOffsetPx": 3.5,
                "useLegacySize": True,
                "useLegacyGap": True,
            },
        }
        document = {
            "fileType": "meldex-scriptnote",
            "layoutMode": "manga",
            "editor": {"viewMode": "vertical"},
            "rubyPresentation": presentation,
            "rows": [{"id": "r1", "role": "", "text": "幽奈"}],
            "rubyRules": [{"text": "幽奈", "ruby": "ゆうな"}],
        }
        path = self._write("legacy-compatible.scriptnote.json", document)
        ruby = SCENARIO_FILE.load_contract_payload(path)["presentation"]["ruby"]
        self.assertAlmostEqual(55.0, ruby["sizePercent"])
        self.assertAlmostEqual(-3.0 / 28.0, ruby["gapEm"])

        presentation["writingMode"] = "horizontal"
        document["editor"]["viewMode"] = "horizontal"
        path = self._write("legacy-horizontal.scriptnote.json", document)
        ruby = SCENARIO_FILE.load_contract_payload(path)["presentation"]["ruby"]
        self.assertAlmostEqual(-0.25, ruby["gapEm"])

        presentation["compatibility"]["legacyGapEm"] = -0.2
        path = self._write("legacy-explicit.scriptnote.json", document)
        ruby = SCENARIO_FILE.load_contract_payload(path)["presentation"]["ruby"]
        self.assertAlmostEqual(-0.2, ruby["gapEm"])

    def test_invalid_files_are_rejected_before_import(self):
        wrong_extension = self._write("scenario.json", {"rows": []})
        with self.assertRaisesRegex(SCENARIO_FILE.ScenarioFileError, "mel-scenario"):
            SCENARIO_FILE.load_contract_payload(wrong_extension)
        wrong_type = self._write("board.mel-scenario", {"fileType": "meldex-board", "rows": []})
        with self.assertRaisesRegex(SCENARIO_FILE.ScenarioFileError, "シナリオファイルではありません"):
            SCENARIO_FILE.load_contract_payload(wrong_type)
        broken = self._write("broken.mel-scenario", "{broken")
        with self.assertRaisesRegex(SCENARIO_FILE.ScenarioFileError, "JSONが壊れています"):
            SCENARIO_FILE.load_contract_payload(broken)


if __name__ == "__main__":
    unittest.main()
