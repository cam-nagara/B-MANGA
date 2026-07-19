"""書き出しプリセット管理.

書き出し設定（形式・カラーモード・範囲・DPI・含めるレイヤー種類等）をプリセットとして
保存/読込する。共通: Blender ユーザー設定配下の B-MANGA 共通プリセット (JSON)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log
from . import shared_presets

_logger = log.get_logger(__name__)

PRESET_SUFFIX = ".json"
PRESET_INDEX_FILENAME = "_preset_index.json"

_EXPORT_KEYS = (
    "format",
    "output_mode",
    "color_mode",
    "area",
    "scale_percent",
    "dpi_override",
    "include_border",
    "include_white_margin",
    "include_nombre",
    "include_work_info",
    "include_tombo",
    "include_paper_color",
    "split_spreads",
    "filename_template",
)

_DEFAULTS: dict[str, Any] = {
    "format": "png",
    "output_mode": "flat",
    "color_mode": "rgb",
    "area": "finish",
    "scale_percent": 100.0,
    "dpi_override": 0,
    "include_border": True,
    "include_white_margin": True,
    "include_nombre": True,
    "include_work_info": True,
    "include_tombo": False,
    "include_paper_color": True,
    "split_spreads": False,
    "filename_template": "{workName}_{episode}_{page}",
}


@dataclass(frozen=True)
class ExportPreset:
    name: str
    description: str
    path: Path | None
    source: str  # "user"
    data: dict[str, Any]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _local_dir() -> Path:
    return shared_presets.preset_dir("exports")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read export preset index: %s", exc)
        return {"order": [], "hidden": []}
    if not isinstance(data, dict):
        return {"order": [], "hidden": []}
    return {
        "order": _string_list(data.get("order", [])),
        "hidden": _string_list(data.get("hidden", [])),
    }


def _write_local_index(index: dict[str, Any]) -> None:
    target = _local_index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    order = _string_list(index.get("order", []))
    hidden = _string_list(index.get("hidden", []))
    json_io.write_json(
        target,
        {
            "schemaVersion": 1,
            "order": list(dict.fromkeys(order)),
            "hidden": list(dict.fromkeys(hidden)),
        },
    )


def _list_in_dir(base: Path) -> list[ExportPreset]:
    if not base.is_dir():
        return []
    out: list[ExportPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        if path.name.startswith("_"):
            continue
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read export preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "export":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            ExportPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source="user",
                data=data,
            )
        )
    return out


def list_all_presets(_work_dir: Path | None = None) -> list[ExportPreset]:
    local = _list_in_dir(_local_dir())
    by_name: dict[str, ExportPreset] = {p.name: p for p in local}
    index = _read_local_index()
    order = index.get("order", [])
    hidden = set(index.get("hidden", []))
    ordered: list[ExportPreset] = []
    seen: set[str] = set()
    for name in order:
        if name in by_name and name not in hidden:
            ordered.append(by_name[name])
            seen.add(name)
    for p in local:
        if p.name not in seen and p.name not in hidden:
            ordered.append(p)
    return ordered


def load_preset_by_name(name: str, _work_dir: Path | None = None) -> ExportPreset | None:
    for p in list_all_presets():
        if p.name == name:
            return p
    return None


def unique_preset_name(_work_dir: Path | None, base: str) -> str:
    existing = {p.name for p in list_all_presets()}
    if base not in existing:
        return base
    for i in range(2, 10000):
        candidate = f"{base} {i}"
        if candidate not in existing:
            return candidate
    return f"{base}_new"


def _safe_filename(name: str) -> str:
    import re
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name).strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned or "preset"


def save_preset(name: str, settings: dict[str, Any], *, description: str = "") -> ExportPreset:
    target_dir = _local_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "schemaVersion": 1,
        "presetType": "export",
        "presetName": name,
        "description": description,
    }
    for key in _EXPORT_KEYS:
        if key in settings:
            data[key] = settings[key]
    filename = _safe_filename(name) + PRESET_SUFFIX
    path = target_dir / filename
    json_io.write_json(path, data)
    index = _read_local_index()
    order = index.get("order", [])
    if name not in order:
        order.append(name)
        index["order"] = order
        _write_local_index(index)
    return ExportPreset(name=name, description=description, path=path, source="user", data=data)


def rename_preset(old_name: str, new_name: str) -> ExportPreset | None:
    preset = load_preset_by_name(old_name)
    if preset is None or preset.path is None:
        return None
    data = dict(preset.data)
    data["presetName"] = new_name
    new_filename = _safe_filename(new_name) + PRESET_SUFFIX
    new_path = preset.path.parent / new_filename
    json_io.write_json(new_path, data)
    if new_path != preset.path:
        try:
            preset.path.unlink()
        except OSError:
            pass
    index = _read_local_index()
    order = index.get("order", [])
    index["order"] = [new_name if n == old_name else n for n in order]
    _write_local_index(index)
    return ExportPreset(name=new_name, description=data.get("description", ""), path=new_path, source="user", data=data)


def duplicate_preset(source_name: str, new_name: str) -> ExportPreset | None:
    preset = load_preset_by_name(source_name)
    if preset is None:
        return None
    data = dict(preset.data)
    data["presetName"] = new_name
    return save_preset(new_name, {k: data[k] for k in _EXPORT_KEYS if k in data}, description=data.get("description", ""))


def delete_preset(name: str) -> None:
    preset = load_preset_by_name(name)
    if preset is not None and preset.path is not None:
        try:
            preset.path.unlink()
        except OSError:
            pass
    index = _read_local_index()
    index["order"] = [n for n in index.get("order", []) if n != name]
    _write_local_index(index)


def move_preset(name: str, direction: str) -> list[str]:
    index = _read_local_index()
    order = index.get("order", [])
    if name not in order:
        all_presets = list_all_presets()
        order = [p.name for p in all_presets]
    try:
        idx = order.index(name)
    except ValueError:
        return order
    if direction == "UP" and idx > 0:
        order[idx], order[idx - 1] = order[idx - 1], order[idx]
    elif direction == "DOWN" and idx < len(order) - 1:
        order[idx], order[idx + 1] = order[idx + 1], order[idx]
    index["order"] = order
    _write_local_index(index)
    return order


def preset_to_settings(preset: ExportPreset) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _EXPORT_KEYS:
        if key in preset.data:
            result[key] = preset.data[key]
        elif key in _DEFAULTS:
            result[key] = _DEFAULTS[key]
    return result


def get_defaults() -> dict[str, Any]:
    return dict(_DEFAULTS)
