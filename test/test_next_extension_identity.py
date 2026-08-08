"""本体Nextだけを隔離し、Render/Linerを通常版のまま保つ契約を検査する。"""

from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDONS = (
    (ROOT, "b_manga_next", "B-MANGA Next", True),
    (
        ROOT / "addons" / "b_manga_render",
        "b_manga_render",
        "B-MANGA Render",
        False,
    ),
    (
        ROOT / "addons" / "b_manga_line",
        "b_manga_line",
        "B-MANGA Liner",
        False,
    ),
)
NORMAL_STORE_NAMES = {
    "b_manga",
    "b_manga_line_presets.json",
    "b_manga_render_preset_defaults.json",
}


def _bl_info_name(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        return str(value["name"])
    raise AssertionError(f"bl_infoが見つかりません: {path}")


class NextExtensionIdentityTest(unittest.TestCase):
    def test_only_core_extension_id_and_name_are_isolated(self) -> None:
        for root, expected_id, expected_name, _is_next in ADDONS:
            with self.subTest(addon=root.name):
                manifest = tomllib.loads(
                    (root / "blender_manifest.toml").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["id"], expected_id)
                self.assertEqual(manifest["name"], expected_name)
                self.assertEqual(_bl_info_name(root / "__init__.py"), expected_name)

    def test_next_ids_are_unique(self) -> None:
        ids = [expected_id for _root, expected_id, _name, _is_next in ADDONS]
        self.assertEqual(len(ids), len(set(ids)))
        next_flags = {
            expected_id: is_next
            for _root, expected_id, _name, is_next in ADDONS
        }
        self.assertEqual(next_flags["b_manga_next"], True)
        self.assertEqual(next_flags["b_manga_render"], False)
        self.assertEqual(next_flags["b_manga_line"], False)

    def test_only_core_config_identity_avoids_normal_store(self) -> None:
        core_source = (ROOT / "io" / "shared_presets.py").read_text(
            encoding="utf-8"
        )
        assigned_strings = set(
            re.findall(
                r"^(?:CONFIG_DIR_NAME|_STORE_FILE_NAME|_FILE_NAME)\s*=\s*\"([^\"]+)\"",
                core_source,
                re.MULTILINE,
            )
        )
        self.assertTrue(assigned_strings.isdisjoint(NORMAL_STORE_NAMES))

        render_source = (
            ROOT / "addons" / "b_manga_render" / "defaults_store.py"
        ).read_text(encoding="utf-8")
        line_source = (
            ROOT / "addons" / "b_manga_line" / "presets.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"b_manga_render_preset_defaults.json"', render_source)
        self.assertIn('"b_manga_line_presets.json"', line_source)

    def test_preferences_lookup_is_not_fixed_to_normal_package(self) -> None:
        source = (ROOT / "ui" / "overlay_text.py").read_text(encoding="utf-8")
        self.assertNotIn('addons.get("b_manga")', source)
        self.assertIn("_addon_package_id()", source)


if __name__ == "__main__":
    unittest.main()
