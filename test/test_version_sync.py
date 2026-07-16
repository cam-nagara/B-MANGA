"""The extension manifest and legacy ``bl_info`` must publish one version."""

from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _legacy_version() -> tuple[int, int, int]:
    module = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets):
            continue
        mapping = ast.literal_eval(node.value)
        return tuple(mapping["version"])
    raise AssertionError("__init__.py に bl_info がありません")


def test_manifest_and_legacy_versions_match() -> None:
    manifest = tomllib.loads((ROOT / "blender_manifest.toml").read_text(encoding="utf-8"))
    manifest_version = tuple(int(part) for part in manifest["version"].split("."))
    assert manifest_version == _legacy_version()
