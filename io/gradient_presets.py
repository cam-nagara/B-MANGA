"""グラデーションプリセット管理.

囲い塗り/グラデーションツールで使うグラデーション設定 (種別・開始色・終了色・
不透明度) をプリセットとして保存/読込する。

- 組み込み: 本モジュール内蔵の既定プリセット (JSON ファイル不要)
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット
  (``shared_presets.preset_dir("gradients")``)
"""

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

# 組み込みプリセット。旧 operators/preset_op.py の _GRADIENT_PRESETS と同一の
# 内容を保持する (id/label は据え置き、JSON ファイルとしては保存しない)。
_BUILTIN_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "bw_linear",
        "label": "黒→白",
        "gradient_type": "linear",
        "color": (0.0, 0.0, 0.0, 1.0),
        "color2": (1.0, 1.0, 1.0, 1.0),
        "opacity": 100,
    },
    {
        "id": "wb_linear",
        "label": "白→黒",
        "gradient_type": "linear",
        "color": (1.0, 1.0, 1.0, 1.0),
        "color2": (0.0, 0.0, 0.0, 1.0),
        "opacity": 100,
    },
    {
        "id": "bw_radial",
        "label": "黒→白 (円形)",
        "gradient_type": "radial",
        "color": (0.0, 0.0, 0.0, 1.0),
        "color2": (1.0, 1.0, 1.0, 1.0),
        "opacity": 100,
    },
    {
        "id": "bw50",
        "label": "黒→白 (半透明)",
        "gradient_type": "linear",
        "color": (0.0, 0.0, 0.0, 1.0),
        "color2": (1.0, 1.0, 1.0, 1.0),
        "opacity": 50,
    },
)


@dataclass(frozen=True)
class GradientPreset:
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


def _local_dir() -> Path:
    return shared_presets.preset_dir("gradients")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read gradient preset index %s: %s", path, exc)
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


def _preset_from_data(data: dict[str, Any], path: Path, source: str) -> GradientPreset | None:
    if data.get("presetType") != "gradient":
        return None
    name = str(data.get("presetName") or path.stem).strip()
    if not name:
        return None
    return GradientPreset(
        name=name,
        description=str(data.get("description", "") or ""),
        path=path,
        source=source,
        data=data,
    )


def _list_user_presets_raw() -> list[GradientPreset]:
    base = _local_dir()
    if not base.is_dir():
        return []
    out: list[GradientPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        if path.name.startswith("_"):
            continue
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read gradient preset %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        preset = _preset_from_data(data, path, "user")
        if preset is not None:
            out.append(preset)
    return out


def _builtin_presets() -> list[GradientPreset]:
    return [
        GradientPreset(
            name=str(data["label"]),
            description=str(data.get("description", "") or ""),
            path=None,
            source="builtin",
            data=copy.deepcopy(data),
        )
        for data in _BUILTIN_PRESETS
    ]


def _default_order_key(preset: GradientPreset) -> tuple[int, int, str]:
    builtin_order = {str(p["label"]): i for i, p in enumerate(_BUILTIN_PRESETS)}
    return (
        builtin_order.get(preset.name, 999),
        0 if preset.source == "builtin" else 1,
        preset.name,
    )


def _order_presets(presets: list[GradientPreset], order: list[str]) -> list[GradientPreset]:
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


def list_user_presets() -> list[GradientPreset]:
    index = _read_local_index()
    return _order_presets(_list_user_presets_raw(), index.get("order", []))


def list_all_presets(_work_dir: Path | None = None) -> list[GradientPreset]:
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    by_name = {p.name: p for p in _builtin_presets()}
    for preset in _list_user_presets_raw():
        by_name[preset.name] = preset
    visible = [p for p in by_name.values() if p.name not in hidden]
    return _order_presets(visible, index.get("order", []))


def load_preset_by_name(name: str, work_dir: Path | None = None) -> GradientPreset | None:
    name = str(name or "").strip()
    if not name:
        return None
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def _color_tuple(value: Any) -> tuple[float, float, float, float]:
    try:
        vals = [float(value[i]) for i in range(min(4, len(value)))]
    except Exception:  # noqa: BLE001
        vals = [0.0, 0.0, 0.0, 1.0]
    while len(vals) < 4:
        vals.append(1.0)
    return tuple(max(0.0, min(1.0, v)) for v in vals[:4])


def _rounded_color(value: Any) -> tuple[float, float, float, float]:
    return tuple(round(v, 4) for v in _color_tuple(value))


def snapshot_from_entry(entry) -> dict[str, Any]:
    """フィルエントリ (グラデーション) から保存用の辞書を作成."""
    return {
        "color": _rounded_color(getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))),
        "color2": _rounded_color(getattr(entry, "color2", (1.0, 1.0, 1.0, 1.0))),
        "gradient_type": str(getattr(entry, "gradient_type", "linear") or "linear"),
        "opacity": int(round(float(getattr(entry, "opacity", 100.0) or 100.0))),
    }


def apply_to_entry(entry, data: dict[str, Any]) -> None:
    """プリセットデータをフィルエントリ (グラデーション) に適用."""
    if hasattr(entry, "color") and "color" in data:
        entry.color = _color_tuple(data.get("color"))
    if hasattr(entry, "color2") and "color2" in data:
        entry.color2 = _color_tuple(data.get("color2"))
    if hasattr(entry, "gradient_type") and "gradient_type" in data:
        gradient_type = str(data.get("gradient_type") or "linear")
        if gradient_type not in ("linear", "radial"):
            gradient_type = "linear"
        entry.gradient_type = gradient_type
    if hasattr(entry, "opacity") and "opacity" in data:
        try:
            entry.opacity = float(data.get("opacity"))
        except (TypeError, ValueError):
            pass


def preset_dict_from_entry(entry, name: str, description: str = "") -> dict[str, Any]:
    snap = snapshot_from_entry(entry)
    return {
        "schemaVersion": 1,
        "presetType": "gradient",
        "presetName": name,
        "description": description,
        "gradient_type": snap["gradient_type"],
        "color": snap["color"],
        "color2": snap["color2"],
        "opacity": snap["opacity"],
    }


def _local_preset_by_name(name: str) -> GradientPreset | None:
    for preset in _list_user_presets_raw():
        if preset.name == name:
            return preset
    return None


def _builtin_preset_by_name(name: str) -> GradientPreset | None:
    for preset in _builtin_presets():
        if preset.name == name:
            return preset
    return None


def preset_name_exists(name: str) -> bool:
    name = str(name or "").strip()
    if not name:
        return False
    return any(p.name == name for p in list_all_presets(None))


def unique_preset_name(base: str) -> str:
    base = (base or "新規グラデーションプリセット").strip() or "新規グラデーションプリセット"
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
    payload = copy.deepcopy(data)
    payload["presetType"] = "gradient"
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
        "presetType": "gradient",
        "presetName": name,
        "description": description,
        "gradient_type": entry_data.get("gradient_type", "linear"),
        "color": list(entry_data.get("color", [0, 0, 0, 1])),
        "color2": list(entry_data.get("color2", [1, 1, 1, 1])),
        "opacity": entry_data.get("opacity", 100),
    }
    out = _write_local_preset_data(data, name, description=description)
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if name in hidden:
        hidden.discard(name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    if is_new:
        _insert_order_name(name)
    _logger.info("shared gradient preset saved: %s", out)
    return out


def rename_preset(old_name: str, new_name: str) -> GradientPreset:
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
    hidden.discard(new_name)
    out = _write_local_preset_data(preset.data, new_name)
    if preset.source == "user" and preset.path != out:
        try:
            preset.path.unlink()
        except FileNotFoundError:
            pass
    order = [new_name if n == old_name else n for n in order]
    if new_name not in order:
        order.append(new_name)
    index["hidden"] = list(hidden)
    index["order"] = order
    _write_local_index(index)
    result = _local_preset_by_name(new_name)
    if result is None:
        raise ValueError(f"プリセットの改名に失敗しました: {new_name}")
    return result


def duplicate_preset(source_name: str, new_name: str) -> GradientPreset:
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
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if new_name in hidden:
        hidden.discard(new_name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
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
    if local is not None:
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
    index_pos = order.index(name)
    if direction == "UP":
        new_index = max(0, index_pos - 1)
    elif direction == "DOWN":
        new_index = min(len(order) - 1, index_pos + 1)
    else:
        raise ValueError(f"不明な移動方向です: {direction}")
    if new_index != index_pos:
        order.insert(new_index, order.pop(index_pos))
    preset_index = _read_local_index()
    preset_index["order"] = order
    _write_local_index(preset_index)
    return order


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"
