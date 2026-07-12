"""効果線プリセット管理."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log
from . import shared_presets

_logger = log.get_logger(__name__)

PRESET_SUFFIX = ".json"
PRESET_INDEX_FILENAME = "_preset_index.json"

_BUILTIN_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "schemaVersion": 1,
        "presetType": "effect_line",
        "presetName": "集中線",
        "description": "中心へ向かう標準の効果線",
        "effect_type": "focus",
    },
    {
        "schemaVersion": 1,
        "presetType": "effect_line",
        "presetName": "ウニフラ",
        "description": "ギザギザ形状の集中線",
        "effect_type": "uni_flash",
    },
    {
        "schemaVersion": 1,
        "presetType": "effect_line",
        "presetName": "ベタフラ",
        "description": "塗りつぶしを含むウニフラ",
        "effect_type": "beta_flash",
    },
    {
        "schemaVersion": 1,
        "presetType": "effect_line",
        "presetName": "流線",
        "description": "動きや速度を表す平行線",
        "effect_type": "speed",
    },
    {
        "schemaVersion": 1,
        "presetType": "effect_line",
        "presetName": "白抜き線",
        "description": "白線の両側に黒線を重ねる効果線",
        "schema_version": 20,
        "effect_type": "white_outline",
        "white_outline_white_ratio_percent": 50.0,
        "white_outline_black_ratio_percent": 50.0,
    },
)


@dataclass(frozen=True)
class EffectLinePreset:
    name: str
    description: str
    path: Path
    source: str
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
    return shared_presets.preset_dir("effect_lines")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read effect line preset index %s: %s", path, exc)
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


def _preset_from_data(data: dict[str, Any], path: Path, source: str) -> EffectLinePreset | None:
    if data.get("presetType") != "effect_line":
        return None
    name = str(data.get("presetName") or path.stem).strip()
    if not name:
        return None
    return EffectLinePreset(
        name=name,
        description=str(data.get("description", "") or ""),
        path=path,
        source=source,
        data=data,
    )


def _list_user_presets_raw() -> list[EffectLinePreset]:
    base = _local_dir()
    if not base.is_dir():
        return []
    out: list[EffectLinePreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        if path.name.startswith("_"):
            continue
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read effect line preset %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            preset = _preset_from_data(data, path, "user")
            if preset is not None:
                out.append(preset)
    return out


def _builtin_presets() -> list[EffectLinePreset]:
    return [
        EffectLinePreset(
            name=str(data["presetName"]),
            description=str(data.get("description", "") or ""),
            path=Path(""),
            source="global",
            data=copy.deepcopy(data),
        )
        for data in _BUILTIN_PRESETS
    ]


def _default_order_key(preset: EffectLinePreset) -> tuple[int, int, str]:
    builtin_order = {str(p["presetName"]): i for i, p in enumerate(_BUILTIN_PRESETS)}
    return (builtin_order.get(preset.name, 999), 0 if preset.source == "global" else 1, preset.name)


def _order_presets(presets: list[EffectLinePreset], order: list[str]) -> list[EffectLinePreset]:
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


def list_all_presets(_work_dir: Path | None = None) -> list[EffectLinePreset]:
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    by_name = {preset.name: preset for preset in _builtin_presets()}
    for preset in _list_user_presets_raw():
        by_name[preset.name] = preset
    visible = [preset for preset in by_name.values() if preset.name not in hidden]
    return _order_presets(visible, index.get("order", []))


def load_preset_by_name(name: str, work_dir: Path | None = None) -> EffectLinePreset | None:
    name = str(name or "").strip()
    if not name:
        return None
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def apply_preset_to_params(preset: EffectLinePreset, params) -> None:
    from ..core import effect_line

    data = {
        key: copy.deepcopy(value)
        for key, value in dict(preset.data).items()
        if key not in {"schemaVersion", "presetType", "presetName", "description"}
    }
    effect_line.effect_params_from_dict(params, data)


def preset_dict_from_params(params, name: str, description: str = "") -> dict[str, Any]:
    from ..core import effect_line

    data = effect_line.effect_params_to_dict(params)
    data.update(
        {
            "schemaVersion": 1,
            "presetType": "effect_line",
            "presetName": name,
            "description": description,
        }
    )
    return data


def _local_preset_by_name(name: str) -> EffectLinePreset | None:
    for preset in _list_user_presets_raw():
        if preset.name == name:
            return preset
    return None


def _global_preset_by_name(name: str) -> EffectLinePreset | None:
    for preset in _builtin_presets():
        if preset.name == name:
            return preset
    return None


def preset_name_exists(work_dir: Path | None, name: str) -> bool:
    name = str(name or "").strip()
    return bool(name) and any(p.name == name for p in list_all_presets(work_dir))


def unique_preset_name(work_dir: Path | None, base: str) -> str:
    base = (base or "新規効果線プリセット").strip() or "新規効果線プリセット"
    if not preset_name_exists(work_dir, base):
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if not preset_name_exists(work_dir, candidate):
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
    payload = copy.deepcopy(data)
    payload["presetType"] = "effect_line"
    payload["presetName"] = name
    if description is not None:
        payload["description"] = description
    json_io.write_json(out, payload)
    return out


def _visible_order_names(work_dir: Path | None) -> list[str]:
    return [preset.name for preset in list_all_presets(work_dir)]


def _insert_order_name(work_dir: Path | None, name: str, *, after_name: str = "") -> None:
    index = _read_local_index()
    order = [item for item in _visible_order_names(work_dir) if item != name]
    if after_name and after_name in order:
        order.insert(order.index(after_name) + 1, name)
    else:
        order.append(name)
    index["order"] = order
    _write_local_index(index)


def save_local_preset(
    work_dir: Path | None,
    params,
    name: str,
    description: str = "",
    *,
    insert_after: str = "",
) -> Path:
    is_new = _local_preset_by_name(name) is None
    out = _write_local_preset_data(
        preset_dict_from_params(params, name, description),
        name,
        description=description,
    )
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if name in hidden:
        hidden.discard(name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    if is_new:
        _insert_order_name(work_dir, name, after_name=insert_after)
    _logger.info("shared effect line preset saved: %s", out)
    return out


def rename_preset(work_dir: Path | None, old_name: str, new_name: str) -> EffectLinePreset:
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name:
        raise ValueError("プリセット名が空です")
    if old_name == new_name:
        preset = load_preset_by_name(old_name, work_dir)
        if preset is None:
            raise ValueError(f"プリセットが見つかりません: {old_name}")
        return preset
    if preset_name_exists(work_dir, new_name):
        raise ValueError(f"同名のプリセットが既にあります: {new_name}")
    preset = load_preset_by_name(old_name, work_dir)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {old_name}")

    index = _read_local_index()
    order = _visible_order_names(work_dir)
    hidden = set(index.get("hidden", []))
    if preset.source == "global":
        hidden.add(old_name)
    hidden.discard(new_name)
    out = _write_local_preset_data(preset.data, new_name)
    if preset.source == "user" and preset.path != out:
        try:
            preset.path.unlink()
        except FileNotFoundError:
            pass
    order = [new_name if name == old_name else name for name in order]
    if new_name not in order:
        order.append(new_name)
    index["hidden"] = list(hidden)
    index["order"] = order
    _write_local_index(index)
    result = _local_preset_by_name(new_name)
    if result is None:
        raise ValueError(f"プリセットの改名に失敗しました: {new_name}")
    return result


def duplicate_preset(work_dir: Path | None, source_name: str, new_name: str) -> EffectLinePreset:
    source_name = source_name.strip()
    new_name = new_name.strip()
    if not source_name or not new_name:
        raise ValueError("プリセット名が空です")
    if preset_name_exists(work_dir, new_name):
        raise ValueError(f"同名のプリセットが既にあります: {new_name}")
    preset = load_preset_by_name(source_name, work_dir)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {source_name}")
    _write_local_preset_data(preset.data, new_name)
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if new_name in hidden:
        hidden.discard(new_name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    _insert_order_name(work_dir, new_name, after_name=source_name)
    result = _local_preset_by_name(new_name)
    if result is None:
        raise ValueError(f"プリセットの複製に失敗しました: {new_name}")
    return result


def delete_preset(work_dir: Path | None, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("プリセット名が空です")
    preset = load_preset_by_name(name, work_dir)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {name}")
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    local = _local_preset_by_name(name)
    if local is not None:
        try:
            local.path.unlink()
        except FileNotFoundError:
            pass
    if preset.source == "global" or _global_preset_by_name(name) is not None:
        hidden.add(name)
    index["hidden"] = list(hidden)
    index["order"] = [item for item in _visible_order_names(work_dir) if item != name]
    _write_local_index(index)


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"
