"""Phase 0のB-MANGA Next隔離識別子が3アドオンで一致することを検査する。"""

from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDONS = (
    (ROOT, "b_manga_next", "B-MANGA Next"),
    (
        ROOT / "addons" / "b_manga_render",
        "b_manga_render_next",
        "B-MANGA Render Next",
    ),
    (
        ROOT / "addons" / "b_manga_line",
        "b_manga_line_next",
        "B-MANGA Liner Next",
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
    def test_all_extension_ids_and_names_are_isolated(self) -> None:
        for root, expected_id, expected_name in ADDONS:
            with self.subTest(addon=root.name):
                manifest = tomllib.loads(
                    (root / "blender_manifest.toml").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["id"], expected_id)
                self.assertEqual(manifest["name"], expected_name)
                self.assertEqual(_bl_info_name(root / "__init__.py"), expected_name)

    def test_next_ids_are_unique(self) -> None:
        ids = [expected_id for _root, expected_id, _name in ADDONS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(item.endswith("_next") for item in ids))

    def test_next_config_identities_do_not_reuse_normal_names(self) -> None:
        source_paths = (
            ROOT / "io" / "shared_presets.py",
            ROOT / "addons" / "b_manga_render" / "defaults_store.py",
            ROOT / "addons" / "b_manga_line" / "presets.py",
        )
        sources = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
        assigned_strings = set(
            re.findall(r"^(?:CONFIG_DIR_NAME|_STORE_FILE_NAME|_FILE_NAME)\s*=\s*\"([^\"]+)\"", sources, re.MULTILINE)
        )
        self.assertTrue(assigned_strings.isdisjoint(NORMAL_STORE_NAMES))
        self.assertIn("b_manga_line_next_presets.json", assigned_strings)
        self.assertIn("b_manga_render_next_preset_defaults.json", assigned_strings)
        self.assertIn("tomllib.load(stream).get(\"id\")", sources)

    def test_preferences_lookup_is_not_fixed_to_normal_package(self) -> None:
        source = (ROOT / "ui" / "overlay_text.py").read_text(encoding="utf-8")
        self.assertNotIn('addons.get("b_manga")', source)
        self.assertIn("_addon_package_id()", source)


if __name__ == "__main__":
    unittest.main()
