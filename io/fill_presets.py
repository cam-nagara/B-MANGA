"""囲い塗り (ベタ塗り) プリセット管理.

ベタ塗りの色・不透明度をプリセットとして保存/読込する。2 層で保持:
- 同梱: アドオンに同梱される built-in プリセット (JSON ファイルなし)
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット (JSON)
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

_BUILTIN_PRESETS: list[dict[str, Any]] = [
    {"id": "black", "label": "ベタ塗り (黒)", "color": (0, 0, 0, 1), "opacity": 100},
    {"id": "white", "label": "ベタ塗り (白)", "color": (1, 1, 1, 1), "opacity": 100},
    {"id": "gray50", "label": "ベタ塗り (50%)", "color": (0.214, 0.214, 0.214, 1), "opacity": 100},
    {"id": "black50", "label": "ベタ塗り (黒 半透明)", "color": (0, 0, 0, 1), "opacity": 50},
]


@dataclass(frozen=True)
class FillPreset:
    name: str
    description: str
    path: Path | None  # None for built-in
    source: str  # "builtin" | "user"
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


def _color_tuple(value: Any) -> tuple[float, float, float, float]:
    try:
        vals = [float(value[i]) for i in range(min(4, len(value)))]
    except (TypeError, IndexError, ValueError):
        vals = [0.0, 0.0, 0.0, 1.0]
    while len(vals) < 4:
        vals.append(1.0)
    return tuple(max(0.0, min(1.0, v)) for v in vals[:4])


def _opacity_int(value: Any) -> int:
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = 100.0
    return int(round(max(0.0, min(100.0, num))))


def _rounded_color(value: Any) -> tuple[float, float, float, float]:
    return tuple(round(v, 4) for v in _color_tuple(value))


def _local_dir() -> Path:
    return shared_presets.preset_dir("fills")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read fill preset index %s: %s", path, exc)
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
    json_io.write_json(
        target,
        {
            "schemaVersion": 1,
            "order": list(dict.fromkeys(_string_list(index.get("order", [])))),
            "hidden": list(dict.fromkeys(_string_list(index.get("hidden", [])))),
        },
    )


def _preset_from_data(data: dict[str, Any], path: Path, source: str) -> FillPreset | None:
    if not isinstance(data, dict) or data.get("presetType") != "fill":
        return None
    name = str(data.get("presetName") or path.stem).strip()
    if not name:
        return None
    return FillPreset(
        name=name,
        description=str(data.get("description", "") or ""),
        path=path,
        source=source,
        data=data,
    )


def _list_user_presets_raw() -> list[FillPreset]:
    base = _local_dir()
    if not base.is_dir():
        return []
    out: list[FillPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        if path.name.startswith("_"):
            continue
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read fill preset %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            preset = _preset_from_data(data, path, "user")
            if preset is not None:
                out.append(preset)
    return out


def _builtin_presets() -> list[FillPreset]:
    out: list[FillPreset] = []
    for entry in _BUILTIN_PRESETS:
        name = str(entry.get("label") or entry.get("id") or "").strip()
        if not name:
            continue
        description = str(entry.get("description", "") or "")
        data: dict[str, Any] = {
            "schemaVersion": 1,
            "presetType": "fill",
            "presetName": name,
            "description": description,
            "color": list(entry.get("color", (0, 0, 0, 1))),
            "opacity": entry.get("opacity", 100),
        }
        out.append(FillPreset(name=name, description=description, path=None, source="builtin", data=data))
    return out


def _default_order_key(preset: FillPreset) -> tuple[int, int, str]:
    builtin_order = {
        str(entry.get("label") or entry.get("id") or ""): i for i, entry in enumerate(_BUILTIN_PRESETS)
    }
    return (builtin_order.get(preset.name, 999), 0 if preset.source == "builtin" else 1, preset.name)


def _order_presets(presets: list[FillPreset], order: list[str]) -> list[FillPreset]:
    if not order:
        return sorted(presets, key=_default_order_key)
    order_pos = {name: i for i, name in enumerate(order)}
    return sorted(
        presets,
        key=lambda p: (
            0 if p.name in order_pos else 1,
            order_pos.get(p.name, 0),
            *_default_order_key(p),
        ),
    )


def list_all_presets(_work_dir: Path | None = None) -> list[FillPreset]:
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    by_name = {preset.name: preset for preset in _builtin_presets()}
    for preset in _list_user_presets_raw():
        by_name[preset.name] = preset
    visible = [preset for preset in by_name.values() if preset.name not in hidden]
    return _order_presets(visible, index.get("order", []))


def load_preset_by_name(name: str, work_dir: Path | None = None) -> FillPreset | None:
    name = str(name or "").strip()
    if not name:
        return None
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def snapshot_from_entry(entry) -> dict[str, Any]:
    """フィルエントリ (BMangaFillLayer 等) から保存用の辞書 (color, opacity) を作成."""
    return {
        "color": _rounded_color(getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))),
        "opacity": _opacity_int(getattr(entry, "opacity", 100)),
    }


def apply_to_entry(entry, data: dict[str, Any]) -> None:
    """プリセットデータ (color, opacity) をフィルエントリへ適用."""
    if "color" in data and hasattr(entry, "color"):
        entry.color = _color_tuple(data.get("color"))
    if "opacity" in data and hasattr(entry, "opacity"):
        entry.opacity = float(_opacity_int(data.get("opacity", 100)))


def preset_dict_from_entry(entry, name: str, description: str = "") -> dict[str, Any]:
    """フィルエントリの現在値から保存用プリセット辞書 (スキーマ付き) を作成."""
    snap = snapshot_from_entry(entry)
    return {
        "schemaVersion": 1,
        "presetType": "fill",
        "presetName": name,
        "description": description,
        "color": list(snap["color"]),
        "opacity": snap["opacity"],
    }


def _local_preset_by_name(name: str) -> FillPreset | None:
    for preset in _list_user_presets_raw():
        if preset.name == name:
            return preset
    return None


def _builtin_preset_by_name(name: str) -> FillPreset | None:
    for preset in _builtin_presets():
        if preset.name == name:
            return preset
    return None


def preset_name_exists(name: str) -> bool:
    name = str(name or "").strip()
    return bool(name and (_builtin_preset_by_name(name) is not None or _local_preset_by_name(name) is not None))


def unique_preset_name(base: str) -> str:
    base = (base or "新規囲い塗りプリセット").strip() or "新規囲い塗りプリセット"
    if not preset_name_exists(base):
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if not preset_name_exists(candidate):
            return candidate
    return base


def _safe_local_path(name: str) -> Path:
    return _local_dir() / f"{_sanitize_filename(name)}{PRESET_SUFFIX}"


def _write_local_preset_data(data: dict[str, Any], name: str, description: str | None = None) -> Path:
    target_dir = _local_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = _local_preset_by_name(name)
    out = existing.path if existing is not None else _safe_local_path(name)
    if existing is None and out.exists():
        raise ValueError(f"同じファイル名のプリセットが既にあります: {out.name}")
    payload = dict(data)
    payload["presetType"] = "fill"
    payload["presetName"] = name
    if description is not None:
        payload["description"] = description
    json_io.write_json(out, payload)
    return out


def _visible_order_names() -> list[str]:
    return [preset.name for preset in list_all_presets()]


def _insert_order_name(name: str, *, after_name: str = "") -> None:
    index = _read_local_index()
    order = [item for item in _visible_order_names() if item != name]
    if after_name and after_name in order:
        order.insert(order.index(after_name) + 1, name)
    else:
        order.append(name)
    index["order"] = order
    _write_local_index(index)


def save_local_preset(name: str, description: str, entry_data: dict[str, Any]) -> Path:
    is_new = _local_preset_by_name(name) is None
    data = {
        "schemaVersion": 1,
        "presetType": "fill",
        "presetName": name,
        "description": description,
        "color": list(entry_data.get("color", [0, 0, 0, 1])),
        "opacity": entry_data.get("opacity", 100),
    }
    out = _write_local_preset_data(data, name, description=description)
    if is_new:
        _insert_order_name(name)
    _logger.info("shared fill preset saved: %s", out)
    return out


def rename_preset(old_name: str, new_name: str) -> FillPreset:
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name:
        raise ValueError("プリセット名が空です")
    if old_name == new_name:
        preset = load_preset_by_name(old_name)
        if preset is None:
            raise ValueError(f"プリセットが見つかりません: {old_name}")
        return preset
    if preset_name_exists(new_name):
        raise ValueError(f"同名のプリセットが既にあります: {new_name}")
    preset = load_preset_by_name(old_name)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {old_name}")

    index = _read_local_index()
    order = _visible_order_names()
    hidden = set(index.get("hidden", []))
    if preset.source == "builtin":
        hidden.add(old_name)
    out = _write_local_preset_data(preset.data, new_name)
    if preset.source == "user" and preset.path is not None and preset.path != out:
        try:
            preset.path.unlink()
        except FileNotFoundError:
            pass
    order = [new_name if item == old_name else item for item in order]
    if new_name not in order:
        order.append(new_name)
    index["hidden"] = list(hidden)
    index["order"] = order
    _write_local_index(index)
    result = _local_preset_by_name(new_name)
    if result is None:
        raise ValueError(f"プリセットの改名に失敗しました: {new_name}")
    return result


def duplicate_preset(source_name: str, new_name: str) -> FillPreset:
    source_name = source_name.strip()
    new_name = new_name.strip()
    if not source_name or not new_name:
        raise ValueError("プリセット名が空です")
    if preset_name_exists(new_name):
        raise ValueError(f"同名のプリセットが既にあります: {new_name}")
    preset = load_preset_by_name(source_name)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {source_name}")
    _write_local_preset_data(preset.data, new_name)
    _insert_order_name(new_name, after_name=source_name)
    result = _local_preset_by_name(new_name)
    if result is None:
        raise ValueError(f"プリセットの複製に失敗しました: {new_name}")
    return result


def delete_preset(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("プリセット名が空です")
    preset = load_preset_by_name(name)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {name}")
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    local = _local_preset_by_name(name)
    if local is not None and local.path is not None:
        try:
            local.path.unlink()
        except FileNotFoundError:
            pass
    if preset.source == "builtin" or _builtin_preset_by_name(name) is not None:
        hidden.add(name)
    index["hidden"] = list(hidden)
    index["order"] = [item for item in _visible_order_names() if item != name]
    _write_local_index(index)


def move_preset(name: str, direction: str) -> list[str]:
    name = name.strip()
    order = _visible_order_names()
    if name not in order:
        raise ValueError(f"プリセットが見つかりません: {name}")
    index = order.index(name)
    if direction == "UP":
        new_index = max(0, index - 1)
    elif direction == "DOWN":
        new_index = min(len(order) - 1, index + 1)
    else:
        raise ValueError(f"不明な移動方向です: {direction}")
    if new_index != index:
        order.insert(new_index, order.pop(index))
    preset_index = _read_local_index()
    preset_index["order"] = order
    _write_local_index(preset_index)
    return order


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"
