"""グリースペンシルツールプリセット管理.

他ツールと違いレイヤー設定ではなく「ドローモードのツール設定」を保存する。
1プリセット = 1機能 (ブラシ / フィル / トリム / 消しゴム / グラブ) + 詳細設定。
2 層で保持:
- 同梱: アドオンに同梱される built-in プリセット (機能ごとの標準設定)
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

PRESET_TYPE = "gp_tool"

_TOOL_IDS = ("brush", "fill", "trim", "erase", "grab")

# 保存キー → (スクラッチPropertyGroupの属性名, 正規化関数名)
_ENUM_VALUES = {
    "tool": _TOOL_IDS,
    "brushAsset": (
        "Pencil",
        "Pencil Soft",
        "Pen",
        "Ink Pen",
        "Ink Pen Rough",
        "Marker Bold",
        "Marker Chisel",
        "Airbrush",
    ),
    "strokeType": ("STROKE", "FILL", "BOTH"),
    "capsType": ("ROUND", "FLAT"),
    "fillDirection": ("NORMAL", "INVERT"),
    "fillSolver": ("DELAUNAY", "PIXEL"),
    "fillExtendMode": ("EXTEND", "RADIUS"),
    "eraserMode": ("HARD", "SOFT", "STROKE"),
}

# JSONキー ↔ BMangaGpToolSettings 属性の対応表 (適用・保存の唯一の正)
_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("tool", "tool", "enum"),
    ("brushAsset", "brush_asset", "enum"),
    ("size", "size", "int"),
    ("useSizePressure", "use_size_pressure", "bool"),
    ("strength", "strength", "float"),
    ("useStrengthPressure", "use_strength_pressure", "bool"),
    ("strokeType", "stroke_type", "enum"),
    ("capsType", "caps_type", "enum"),
    ("hardness", "hardness", "float"),
    ("useSmoothStroke", "use_smooth_stroke", "bool"),
    ("smoothStrokeFactor", "smooth_stroke_factor", "float"),
    ("fillDirection", "fill_direction", "enum"),
    ("fillSolver", "fill_solver", "enum"),
    ("fillFactor", "fill_factor", "float"),
    ("fillDilate", "fill_dilate", "int"),
    ("fillExtendMode", "fill_extend_mode", "enum"),
    ("fillExtendFactor", "fill_extend_factor", "float"),
    ("eraserMode", "eraser_mode", "enum"),
    ("activeLayerOnly", "use_active_layer_only", "bool"),
    ("keepCaps", "use_keep_caps", "bool"),
)

_BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "label": "ブラシ（標準）",
        "description": "鉛筆ブラシで描く標準設定",
        "tool": "brush",
        "brushAsset": "Pencil",
        "size": 14,
        "useSizePressure": True,
        "strength": 1.0,
        "useStrengthPressure": True,
    },
    {
        "label": "フィル（標準）",
        "description": "囲まれた領域を塗りつぶす標準設定",
        "tool": "fill",
        "size": 10,
        "strength": 1.0,
    },
    {
        "label": "トリム（標準）",
        "description": "ストロークを切り取る標準設定",
        "tool": "trim",
    },
    {
        "label": "消しゴム（標準）",
        "description": "触れた点を削除する標準の消しゴム",
        "tool": "erase",
        "eraserMode": "HARD",
        "size": 60,
        "strength": 1.0,
    },
    {
        "label": "グラブ（標準）",
        "description": "スカルプトのグラブでストロークを動かす標準設定",
        "tool": "grab",
        "size": 50,
        "strength": 0.6,
    },
]


@dataclass(frozen=True)
class GpToolPreset:
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


def _enum_value(key: str, value: Any, default: str) -> str:
    allowed = _ENUM_VALUES.get(key, ())
    text = str(value or "").strip()
    return text if text in allowed else default


def enum_value(key: str, value: Any, default: str) -> str:
    """保存キーの値を許可リストで正規化して返す (適用側から使う公開版)."""
    return _enum_value(key, value, default)


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int_value(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)


def _local_dir() -> Path:
    return shared_presets.preset_dir("gp_tools")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read gp tool preset index %s: %s", path, exc)
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


def _preset_from_data(data: dict[str, Any], path: Path, source: str) -> GpToolPreset | None:
    if not isinstance(data, dict) or data.get("presetType") != PRESET_TYPE:
        return None
    name = str(data.get("presetName") or path.stem).strip()
    if not name:
        return None
    return GpToolPreset(
        name=name,
        description=str(data.get("description", "") or ""),
        path=path,
        source=source,
        data=data,
    )


def _list_user_presets_raw() -> list[GpToolPreset]:
    base = _local_dir()
    if not base.is_dir():
        return []
    out: list[GpToolPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        if path.name.startswith("_"):
            continue
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read gp tool preset %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            preset = _preset_from_data(data, path, "user")
            if preset is not None:
                out.append(preset)
    return out


def _builtin_presets() -> list[GpToolPreset]:
    out: list[GpToolPreset] = []
    for entry in _BUILTIN_PRESETS:
        name = str(entry.get("label") or "").strip()
        if not name:
            continue
        description = str(entry.get("description", "") or "")
        data: dict[str, Any] = {
            "schemaVersion": 1,
            "presetType": PRESET_TYPE,
            "presetName": name,
            "description": description,
        }
        for key, _attr, _kind in _FIELDS:
            if key in entry:
                data[key] = entry[key]
        out.append(
            GpToolPreset(name=name, description=description, path=None, source="builtin", data=data)
        )
    return out


def _default_order_key(preset: GpToolPreset) -> tuple[int, int, str]:
    builtin_order = {
        str(entry.get("label") or ""): i for i, entry in enumerate(_BUILTIN_PRESETS)
    }
    return (builtin_order.get(preset.name, 999), 0 if preset.source == "builtin" else 1, preset.name)


def _order_presets(presets: list[GpToolPreset], order: list[str]) -> list[GpToolPreset]:
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


def list_all_presets(_work_dir: Path | None = None) -> list[GpToolPreset]:
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    by_name = {preset.name: preset for preset in _builtin_presets()}
    for preset in _list_user_presets_raw():
        by_name[preset.name] = preset
    visible = [preset for preset in by_name.values() if preset.name not in hidden]
    return _order_presets(visible, index.get("order", []))


def load_preset_by_name(name: str, work_dir: Path | None = None) -> GpToolPreset | None:
    name = str(name or "").strip()
    if not name:
        return None
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def tool_id(data: dict[str, Any]) -> str:
    """プリセットデータの機能IDを正規化して返す."""
    return _enum_value("tool", data.get("tool"), "brush")


def snapshot_from_entry(entry) -> dict[str, Any]:
    """設定 PropertyGroup (BMangaGpToolSettings) から保存用の辞書を作成."""
    out: dict[str, Any] = {}
    for key, attr, kind in _FIELDS:
        value = getattr(entry, attr, None)
        if value is None:
            continue
        if kind == "float":
            out[key] = round(float(value), 4)
        elif kind == "int":
            out[key] = int(value)
        elif kind == "bool":
            out[key] = bool(value)
        else:
            out[key] = str(value)
    return out


def apply_to_entry(entry, data: dict[str, Any]) -> None:
    """プリセットデータを設定 PropertyGroup へ適用 (存在するキーのみ)."""
    for key, attr, kind in _FIELDS:
        if key not in data or not hasattr(entry, attr):
            continue
        current = getattr(entry, attr)
        if kind == "enum":
            setattr(entry, attr, _enum_value(key, data.get(key), str(current)))
        elif kind == "float":
            setattr(entry, attr, _float_value(data.get(key), float(current)))
        elif kind == "int":
            setattr(entry, attr, _int_value(data.get(key), int(current)))
        elif kind == "bool":
            setattr(entry, attr, bool(data.get(key)))


def _local_preset_by_name(name: str) -> GpToolPreset | None:
    for preset in _list_user_presets_raw():
        if preset.name == name:
            return preset
    return None


def _builtin_preset_by_name(name: str) -> GpToolPreset | None:
    for preset in _builtin_presets():
        if preset.name == name:
            return preset
    return None


def preset_name_exists(name: str) -> bool:
    name = str(name or "").strip()
    return bool(name) and any(p.name == name for p in list_all_presets(None))


def unique_preset_name(base: str) -> str:
    base = (base or "新規グリースペンシルツールプリセット").strip() or "新規グリースペンシルツールプリセット"
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
    payload["schemaVersion"] = 1
    payload["presetType"] = PRESET_TYPE
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
    data: dict[str, Any] = {
        "schemaVersion": 1,
        "presetType": PRESET_TYPE,
        "presetName": name,
        "description": description,
    }
    for key, _attr, _kind in _FIELDS:
        if key in entry_data:
            data[key] = entry_data[key]
    data["tool"] = tool_id(data)
    out = _write_local_preset_data(data, name, description=description)
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if name in hidden:
        hidden.discard(name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    if is_new:
        _insert_order_name(name)
    _logger.info("shared gp tool preset saved: %s", out)
    return out


def rename_preset(old_name: str, new_name: str) -> GpToolPreset:
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


def duplicate_preset(source_name: str, new_name: str) -> GpToolPreset:
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
