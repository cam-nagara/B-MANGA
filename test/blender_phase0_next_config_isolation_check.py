"""Phase 0: Next 3製品の実package IDとユーザー設定保存先を検査する。"""

from __future__ import annotations

import builtins
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    ("b_manga_next", ROOT),
    ("b_manga_render_next", ROOT / "addons" / "b_manga_render"),
    ("b_manga_line_next", ROOT / "addons" / "b_manga_line"),
)


def _load_package(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(
        name, path / "__init__.py", submodule_search_locations=[str(path)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"パッケージを読み込めません: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _assert_default_paths(packages: dict[str, object]) -> None:
    main_store = packages["b_manga_next"].io.shared_presets
    overlay_text = packages["b_manga_next"].ui.overlay_text
    render_store = packages["b_manga_render_next"].defaults_store
    line_store = packages["b_manga_line_next"].presets
    assert main_store.CONFIG_DIR_NAME == "b_manga_next"
    assert overlay_text._addon_package_id() == "b_manga_next"
    context = SimpleNamespace(
        preferences=SimpleNamespace(
            addons={
                "b_manga_next": SimpleNamespace(
                    preferences=SimpleNamespace(text_selection_color=(0.1, 0.2, 0.3, 0.4))
                )
            }
        ),
        scene=None,
    )
    assert overlay_text._text_selection_color(context) == (0.1, 0.2, 0.3, 0.4)
    assert Path(main_store.config_root(create=False)).name == "b_manga_next"
    assert render_store._manifest_id() == "b_manga_render_next"
    assert "b_manga_render_next" in render_store._store_path().parts
    assert line_store._manifest_id() == "b_manga_line_next"
    assert "b_manga_line_next" in line_store._store_path().parts


def _assert_override_isolation(packages: dict[str, object], root: Path) -> None:
    main_store = packages["b_manga_next"].io.shared_presets
    render_store = packages["b_manga_render_next"].defaults_store
    line_store = packages["b_manga_line_next"].presets
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(root / "main_next")
    os.environ["BMANGA_RENDER_PRESET_STORE_DIR"] = str(root / "render_next")
    os.environ["BMANGA_LINE_PRESET_STORE_DIR"] = str(root / "line_next")
    normal_files = _create_normal_sentinels(root)
    source_dir = root / "source_presets"
    source_dir.mkdir()
    (source_dir / "next.json").write_text(
        '{"presetName": "Nextのみ"}', encoding="utf-8"
    )
    restore_access = _deny_file_access(set(normal_files))
    try:
        assert main_store.copy_json_presets_once(
            source_dir, main_store.preset_dir("paper")
        ) == 1
        render_store.save_preset_default("next", _EmptyPreset())
        line_store._write_store(bpy.context.scene)
    finally:
        restore_access()
    assert all(path.read_text(encoding="utf-8") == "NORMAL" for path in normal_files)
    assert render_store._store_path().name == "b_manga_render_next_preset_defaults.json"
    assert line_store._store_path().name == "b_manga_line_next_presets.json"


def _create_normal_sentinels(root: Path) -> tuple[Path, ...]:
    paths = (
        root / "b_manga" / "normal.json",
        root / "render_next" / "b_manga_render_preset_defaults.json",
        root / "line_next" / "b_manga_line_presets.json",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NORMAL", encoding="utf-8")
    return paths


def _deny_file_access(forbidden: set[Path]):
    denied = {path.resolve() for path in forbidden}
    original_builtin_open = builtins.open
    original_path_open = Path.open

    def check(file) -> None:
        if not isinstance(file, int) and Path(file).resolve() in denied:
            raise AssertionError(f"通常版の設定ファイルへアクセスしました: {file}")

    def guarded_builtin_open(file, *args, **kwargs):
        check(file)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_path_open(path, *args, **kwargs):
        check(path)
        return original_path_open(path, *args, **kwargs)

    def restore() -> None:
        builtins.open = original_builtin_open
        Path.open = original_path_open

    builtins.open = guarded_builtin_open
    Path.open = guarded_path_open
    return restore


class _EmptyCommands:
    def __iter__(self):
        return iter(())


class _EmptyPreset:
    commands = _EmptyCommands()


def main() -> None:
    packages = {name: _load_package(name, path) for name, path in PACKAGES}
    old_env = {
        name: os.environ.get(name)
        for name in (
            "BMANGA_USER_CONFIG_DIR",
            "BMANGA_RENDER_PRESET_STORE_DIR",
            "BMANGA_LINE_PRESET_STORE_DIR",
        )
    }
    try:
        for name in old_env:
            os.environ.pop(name, None)
        _assert_default_paths(packages)
        with tempfile.TemporaryDirectory(prefix="bmanga_next_config_") as temp:
            _assert_override_isolation(packages, Path(temp))
        print(json.dumps({"status": "pass", "packages": sorted(packages)}))
        print("BMANGA_PHASE0_NEXT_CONFIG_ISOLATION_OK")
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
