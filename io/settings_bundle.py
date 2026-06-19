"""B-MANGA 設定と共通プリセットのエクスポート / インポート."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ..utils import json_io, log
from . import shared_presets

_logger = log.get_logger(__name__)

BUNDLE_META = "b_manga_settings_bundle.json"
PREFERENCES_JSON = "preferences.json"
PRESETS_PREFIX = "presets/"


def export_bundle(context, filepath: str | Path) -> Path:
    """B-MANGA のプリファレンスと共通プリセットを zip に書き出す."""
    out = Path(filepath)
    if out.suffix.lower() != ".zip":
        out = out.with_suffix(".zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(BUNDLE_META, json.dumps(_bundle_meta(), ensure_ascii=False, indent=2) + "\n")
        zf.writestr(
            PREFERENCES_JSON,
            json.dumps(_preferences_to_dict(context), ensure_ascii=False, indent=2) + "\n",
        )
        root = shared_presets.presets_root(create=True)
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                zf.write(path, f"{PRESETS_PREFIX}{rel}")
    _logger.info("B-MANGA settings exported: %s", out)
    return out


def import_bundle(context, filepath: str | Path) -> dict[str, int]:
    """zip から B-MANGA のプリファレンスと共通プリセットを取り込む."""
    src = Path(filepath)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    imported = {"preset_files": 0, "preferences": 0}
    with zipfile.ZipFile(src, "r") as zf, tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name in {BUNDLE_META, PREFERENCES_JSON}:
                continue
            if not name.startswith(PRESETS_PREFIX):
                continue
            rel = _safe_relative(name[len(PRESETS_PREFIX):])
            if rel is None:
                continue
            target = temp_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src_fh, target.open("wb") as dst_fh:
                shutil.copyfileobj(src_fh, dst_fh)

        dst_root = shared_presets.presets_root(create=True)
        for path in sorted(temp_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(temp_root)
            target = dst_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            imported["preset_files"] += 1

        try:
            prefs_data = json.loads(zf.read(PREFERENCES_JSON).decode("utf-8-sig"))
        except KeyError:
            prefs_data = {}
        if isinstance(prefs_data, dict) and _apply_preferences_from_dict(context, prefs_data):
            imported["preferences"] = 1
    _logger.info("B-MANGA settings imported: %s", src)
    return imported


def _bundle_meta() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "app": "B-MANGA",
        "contents": ["preferences", "presets"],
    }


def _preferences_to_dict(context) -> dict[str, Any]:
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
    except Exception:  # noqa: BLE001
        prefs = None
    if prefs is None:
        return {}
    data: dict[str, Any] = {}
    bl_rna = getattr(prefs, "bl_rna", None)
    if bl_rna is None:
        return {
            key: value
            for key, value in vars(prefs).items()
            if _is_json_scalar(value) or isinstance(value, list)
        }
    for prop in bl_rna.properties:
        identifier = str(getattr(prop, "identifier", "") or "")
        if not identifier or identifier == "rna_type" or bool(getattr(prop, "is_readonly", False)):
            continue
        if identifier == "ruby_dictionaries":
            data[identifier] = [
                {
                    "path": str(getattr(item, "path", "") or ""),
                    "enabled": bool(getattr(item, "enabled", True)),
                }
                for item in getattr(prefs, "ruby_dictionaries", [])
            ]
            continue
        value = getattr(prefs, identifier, None)
        if _is_json_scalar(value):
            data[identifier] = value
        elif hasattr(value, "__len__") and not isinstance(value, (str, bytes, dict)):
            try:
                data[identifier] = [float(v) for v in value]
            except Exception:  # noqa: BLE001
                pass
    return data


def _apply_preferences_from_dict(context, data: dict[str, Any]) -> bool:
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
    except Exception:  # noqa: BLE001
        prefs = None
    if prefs is None:
        return False
    for key, value in data.items():
        if key == "ruby_dictionaries":
            _apply_ruby_dictionaries(prefs, value)
            continue
        if not hasattr(prefs, key):
            continue
        try:
            current = getattr(prefs, key)
            if hasattr(current, "__len__") and not isinstance(current, (str, bytes)):
                for i, item in enumerate(value):
                    current[i] = item
            else:
                setattr(prefs, key, value)
        except Exception:  # noqa: BLE001
            _logger.warning("failed to import preference: %s", key, exc_info=True)
    try:
        addon_preferences.request_user_preferences_save()
    except Exception:  # noqa: BLE001
        pass
    return True


def _apply_ruby_dictionaries(prefs, value: Any) -> None:
    if not isinstance(value, list) or not hasattr(prefs, "ruby_dictionaries"):
        return
    try:
        prefs.ruby_dictionaries.clear()
    except AttributeError:
        while len(prefs.ruby_dictionaries):
            prefs.ruby_dictionaries.remove(len(prefs.ruby_dictionaries) - 1)
    for item in value:
        if not isinstance(item, dict):
            continue
        entry = prefs.ruby_dictionaries.add()
        entry.path = str(item.get("path", "") or "")
        entry.enabled = bool(item.get("enabled", True))
    prefs.ruby_dict_active_index = 0 if len(prefs.ruby_dictionaries) else -1


def _safe_relative(name: str) -> Path | None:
    rel = Path(name)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        return None
    return rel


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))
