from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "blender_manifest.toml"

REQUIRED_EXCLUSIONS = {
    "__pycache__/",
    ".*",
    "/*.zip",
    "*.blend",
    "*.blend[1-9]",
    "/_verify/",
    "/addons/",
    "/docs/",
    "/test/",
    "/tools/",
    "/wheels/_installed/",
    "/AGENT_INBOX.md",
    "/project.json",
}


def test_distribution_manifest_excludes_non_product_content() -> None:
    data = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    exclusions = set(data.get("build", {}).get("paths_exclude_pattern", ()))
    missing = sorted(REQUIRED_EXCLUSIONS - exclusions)
    assert not missing, f"配布除外規則が不足しています: {missing}"


def test_distribution_manifest_keeps_required_product_roots() -> None:
    data = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    exclusions = set(data.get("build", {}).get("paths_exclude_pattern", ()))
    required_roots = {
        "/assets/",
        "/bmanga_core/",
        "/core/",
        "/io/",
        "/keymap/",
        "/operators/",
        "/panels/",
        "/presets/",
        "/typography/",
        "/ui/",
        "/utils/",
        "/wheels/",
    }
    wrongly_excluded = sorted(required_roots & exclusions)
    assert not wrongly_excluded, f"製品ディレクトリが配布対象外です: {wrongly_excluded}"
