"""テキストプリセット管理.

フォント・サイズ・行間・色・縦横・白フチなどのテキスト設定をプリセットとして保存/読込する。
- 同梱: <addon>/presets/text/
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import shared_presets

_logger = log.get_logger(__name__)

_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_PRESETS_DIR = _ADDON_ROOT / "presets" / "text"

PRESET_SUFFIX = ".json"

_TEXT_KEYS = (
    "font",
    "font_size_unit",
    "font_size_value",
    "font_bold",
    "font_italic",
    "color",
    "writing_mode",
    "line_height",
    "letter_spacing",
    "ruby_line_height",
    "ruby_gap_mm",
    "ruby_letter_spacing",
    "ruby_size_percent",
    "ruby_font",
    "ruby_align",
    "ruby_small_kana",
    "stroke_enabled",
    "stroke_width_mm",
    "stroke_color",
    "speaker_type",
)


@dataclass(frozen=True)
class TextPreset:
    name: str
    description: str
    path: Path
    source: str
    data: dict[str, Any]


def _list_in_dir(base: Path, *, source: str) -> list[TextPreset]:
    if not base.is_dir():
        return []
    out: list[TextPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read text preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "text":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            TextPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source=source,
                data=data,
            )
        )
    return out


def list_global_presets() -> list[TextPreset]:
    return _list_in_dir(GLOBAL_PRESETS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[TextPreset]:
    _migrate_work_presets(work_dir)
    return list_user_presets()


def list_user_presets() -> list[TextPreset]:
    return _list_in_dir(shared_presets.preset_dir("text"), source="user")


def list_all_presets(work_dir: Path | None) -> list[TextPreset]:
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        _migrate_work_presets(work_dir)
    for p in list_user_presets():
        presets[p.name] = p
    return list(presets.values())


def snapshot_from_entry(entry) -> dict[str, Any]:
    """TextEntry から保存用の辞書を作成."""
    snap: dict[str, Any] = {}
    for key in _TEXT_KEYS:
        val = getattr(entry, key, None)
        if val is None:
            continue
        if key in {"color", "stroke_color"}:
            snap[key] = [round(float(c), 4) for c in val[:4]]
        else:
            try:
                snap[key] = round(float(val), 4) if isinstance(val, float) else val
            except (TypeError, ValueError):
                snap[key] = str(val)
    return snap


def apply_to_entry(entry, data: dict[str, Any]) -> None:
    """プリセットデータを TextEntry に適用."""
    for key in _TEXT_KEYS:
        if key not in data:
            continue
        val = data[key]
        if key in {"color", "stroke_color"}:
            try:
                prop = getattr(entry, key)
                for i, c in enumerate(val[:4]):
                    prop[i] = float(c)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                setattr(entry, key, val)
            except Exception:  # noqa: BLE001
                pass


def reset_entry_to_defaults(entry) -> None:
    """Restore only preset-controlled properties without touching body or placement."""
    properties = getattr(getattr(entry, "bl_rna", None), "properties", None)
    if properties is None:
        return
    for key in _TEXT_KEYS:
        prop = properties.get(key)
        if prop is None or bool(getattr(prop, "is_readonly", False)):
            continue
        default = getattr(prop, "default_array", None) if bool(getattr(prop, "is_array", False)) else getattr(prop, "default", None)
        try:
            setattr(entry, key, default)
        except (AttributeError, TypeError, ValueError):
            continue


def save_preset(
    out_path: Path,
    name: str,
    description: str,
    entry_data: dict[str, Any],
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "schemaVersion": 1,
        "presetType": "text",
        "presetName": name,
        "description": description,
    }
    data.update(entry_data)
    json_io.write_json(out_path, data)
    return out_path


def save_local_preset(
    work_dir: Path,
    name: str,
    description: str,
    entry_data: dict[str, Any],
) -> Path:
    del work_dir
    target = shared_presets.preset_dir("text")
    filename = name.replace("/", "_").replace("\\", "_") + PRESET_SUFFIX
    return save_preset(target / filename, name, description, entry_data)


def _migrate_work_presets(work_dir: Path | None) -> None:
    if work_dir is None:
        return
    legacy_dir = paths.assets_dir(Path(work_dir)) / "text_presets"
    shared_presets.copy_json_presets_once(legacy_dir, shared_presets.preset_dir("text"))
