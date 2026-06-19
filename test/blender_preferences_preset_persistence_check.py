"""Blender 実機用: プリファレンスとツールプリセット選択の保持確認."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_pref_persistence"
_VERBOSE = False


def _mark(message: str) -> None:
    if _VERBOSE:
        print(message, flush=True)


def _load_addon():
    _mark("load_addon: spec")
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    _mark("load_addon: module_from_spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    _mark("load_addon: exec_module")
    spec.loader.exec_module(mod)
    _mark("load_addon: register")
    mod.register()
    _mark("load_addon: registered")
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _first_valid(items, *, skip: set[str] | None = None) -> str:
    skip = skip or set()
    for item in items:
        value = str(item[0])
        if value and value not in skip:
            return value
    raise AssertionError(f"有効なプリセット項目がありません: {items!r}")


class _FakeRubyDictionaries(list):
    def add(self):
        entry = SimpleNamespace(path="", enabled=True)
        self.append(entry)
        return entry

    def remove(self, index: int) -> None:
        del self[index]


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_pref_persistence_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    try:
        _main_impl(temp_root)
    finally:
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)


def _main_impl(temp_root: Path) -> None:
    _mark("main: factory")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _mark("main: load")
    _load_addon()
    _mark("main: imports")

    preferences = _sub("preferences")
    preset_op = _sub("operators.preset_op")
    tail_detail = _sub("operators.balloon_tail_detail_op")
    text_auto_ruby_op = _sub("operators.text_auto_ruby_op")
    settings_bundle = _sub("io.settings_bundle")
    shared_presets = _sub("io.shared_presets")
    json_io = _sub("utils.json_io")
    _mark("main: monkeypatch")

    prefs = SimpleNamespace(
        last_balloon_tool_preset="",
        last_tail_preset="",
        last_text_tool_preset="",
        last_fill_tool_preset="",
        last_gradient_tool_preset="",
        ruby_dictionaries=_FakeRubyDictionaries(),
        ruby_dict_active_index=0,
    )
    save_calls: list[str] = []
    original_get_preferences = preferences.get_preferences
    original_request_save = preferences.request_user_preferences_save
    original_ruby_get_preferences = text_auto_ruby_op.get_preferences
    original_ruby_request_save = text_auto_ruby_op.request_user_preferences_save
    try:
        preferences.get_preferences = lambda _context=None: prefs
        preferences.request_user_preferences_save = lambda: save_calls.append("save")
        text_auto_ruby_op.get_preferences = lambda _context=None: prefs
        text_auto_ruby_op.request_user_preferences_save = lambda: save_calls.append("save")

        wm = bpy.context.window_manager
        _mark("main: enum values")
        balloon_value = _first_valid(
            preset_op._balloon_tool_preset_enum_items(None, bpy.context),
            skip={"DEFAULT"},
        )
        text_value = _first_valid(
            preset_op._text_preset_enum_items(None, bpy.context),
            skip={"NONE"},
        )
        fill_value = "black50"
        gradient_value = "bw50"
        tail_value = _first_valid(
            tail_detail._tail_preset_enum_items(None, bpy.context),
            skip={"NONE"},
        )

        _mark("main: set selectors")
        wm.bmanga_balloon_tool_preset_selector = balloon_value
        wm.bmanga_text_tool_preset_selector = text_value
        wm.bmanga_fill_tool_preset_selector = fill_value
        wm.bmanga_gradient_tool_preset_selector = gradient_value
        wm.bmanga_tail_preset_selector = tail_value

        _mark("main: assert remembered")
        assert prefs.last_balloon_tool_preset == balloon_value
        assert prefs.last_text_tool_preset == text_value
        assert prefs.last_fill_tool_preset == fill_value
        assert prefs.last_gradient_tool_preset == gradient_value
        assert prefs.last_tail_preset == tail_value
        assert len(save_calls) >= 5, f"保存予約が呼ばれていません: {save_calls!r}"

        wm.bmanga_balloon_tool_preset_selector = "DEFAULT"
        wm.bmanga_fill_tool_preset_selector = "black"
        wm.bmanga_gradient_tool_preset_selector = "bw_linear"
        prefs.last_balloon_tool_preset = balloon_value
        prefs.last_text_tool_preset = text_value
        prefs.last_fill_tool_preset = fill_value
        prefs.last_gradient_tool_preset = gradient_value
        prefs.last_tail_preset = tail_value

        _mark("main: restore selectors")
        preset_op.restore_tool_preset_selectors(bpy.context)
        tail_detail.restore_tail_preset_selector(bpy.context)

        _mark("main: assert restored")
        _mark(
            "restored: "
            f"balloon={wm.bmanga_balloon_tool_preset_selector!r}/{balloon_value!r}, "
            f"text={wm.bmanga_text_tool_preset_selector!r}/{text_value!r}, "
            f"fill={wm.bmanga_fill_tool_preset_selector!r}/{fill_value!r}, "
            f"gradient={wm.bmanga_gradient_tool_preset_selector!r}/{gradient_value!r}, "
            f"tail={wm.bmanga_tail_preset_selector!r}/{tail_value!r}"
        )
        assert wm.bmanga_balloon_tool_preset_selector == balloon_value
        assert wm.bmanga_text_tool_preset_selector == text_value
        assert wm.bmanga_fill_tool_preset_selector == fill_value
        assert wm.bmanga_gradient_tool_preset_selector == gradient_value
        assert wm.bmanga_tail_preset_selector == tail_value

        before = len(save_calls)
        _mark("main: ruby add")
        assert bpy.ops.bmanga.ruby_dict_add() == {"FINISHED"}
        assert len(save_calls) == before + 1
        _mark("main: ruby remove")
        assert bpy.ops.bmanga.ruby_dict_remove() == {"FINISHED"}
        assert len(save_calls) == before + 2

        assert hasattr(bpy.ops.bmanga, "preferences_export")
        assert hasattr(bpy.ops.bmanga, "preferences_import")

        preset_file = shared_presets.preset_dir("paper") / "共有用紙.json"
        json_io.write_json(
            preset_file,
            {
                "schemaVersion": 1,
                "presetType": "paper",
                "presetName": "共有用紙",
                "description": "テスト",
                "paper": {},
            },
        )
        prefs.last_fill_tool_preset = "black50"
        bundle_path = temp_root / "bmanga_settings.zip"
        out = settings_bundle.export_bundle(bpy.context, bundle_path)
        assert Path(out).is_file()
        preset_file.unlink()
        prefs.last_fill_tool_preset = ""
        result = settings_bundle.import_bundle(bpy.context, bundle_path)
        assert preset_file.is_file(), "共通プリセットがインポートされていません"
        assert prefs.last_fill_tool_preset == "black50"
        assert int(result.get("preset_files", 0)) >= 1

        legacy_a = temp_root / "work_a" / "assets" / "templates"
        legacy_b = temp_root / "work_b" / "assets" / "templates"
        legacy_a.mkdir(parents=True, exist_ok=True)
        legacy_b.mkdir(parents=True, exist_ok=True)
        json_io.write_json(
            legacy_a / "same_file.json",
            {
                "schemaVersion": 1,
                "presetType": "paper",
                "presetName": "移行プリセット",
                "description": "A",
                "paper": {"widthMm": 100},
            },
        )
        json_io.write_json(
            legacy_b / "same_file.json",
            {
                "schemaVersion": 1,
                "presetType": "paper",
                "presetName": "移行プリセット",
                "description": "B",
                "paper": {"widthMm": 200},
            },
        )
        migrated_dir = temp_root / "migrated_presets"
        assert shared_presets.copy_json_presets_once(legacy_a, migrated_dir) == 1
        assert shared_presets.copy_json_presets_once(legacy_b, migrated_dir) == 1
        assert shared_presets.copy_json_presets_once(legacy_b, migrated_dir) == 0
        migrated_names = sorted(
            json_io.read_json(path).get("presetName")
            for path in migrated_dir.glob("*.json")
        )
        assert migrated_names == ["移行プリセット", "移行プリセット 2"], migrated_names
    finally:
        _mark("main: restore monkeypatch")
        preferences.get_preferences = original_get_preferences
        preferences.request_user_preferences_save = original_request_save
        text_auto_ruby_op.get_preferences = original_ruby_get_preferences
        text_auto_ruby_op.request_user_preferences_save = original_ruby_request_save

    _mark("main: done")
    print("[ok] preferences and tool preset selections persist")


if __name__ == "__main__":
    ok = False
    try:
        main()
        ok = True
    except Exception:  # noqa: BLE001
        print("exception:", flush=True)
        print(traceback.format_exc(), flush=True)
        raise
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0 if ok else 1)
