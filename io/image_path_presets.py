"""パターンカーブプリセット管理."""

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
        "presetType": "image_path",
        "presetName": "標準スタンプ",
        "description": "線の向きに沿って画像を連続表示",
        "contentSource": "image",
        "drawMode": "stamp",
        "shapeKind": "circle",
        "shapeSides": 6,
        "color": [1.0, 1.0, 1.0, 1.0],
        "brushSizeMm": 10.0,
        "aspectRatio": 1.0,
        "imageAngleDeg": 0.0,
        "spacingPercent": 100.0,
        "stampAngleMode": "line",
        "stampAngleObjectName": "",
        "ribbonRepeatMode": "repeat",
        "imagePath": "",
        "opacity": 100.0,
        "inoutSizeEnabled": False,
        "inoutOpacityEnabled": False,
        "inoutColorEnabled": False,
        "inPercent": 100.0,
        "outPercent": 100.0,
        "inStartPercent": 0.0,
        "outStartPercent": 0.0,
        "inEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "outEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "inoutStartColor": [1.0, 1.0, 1.0, 1.0],
        "inoutEndColor": [1.0, 1.0, 1.0, 1.0],
    },
    {
        "schemaVersion": 1,
        "presetType": "image_path",
        "presetName": "標準リボン",
        "description": "ブラシサイズの画像をリボン状に連続表示",
        "contentSource": "image",
        "drawMode": "ribbon",
        "shapeKind": "circle",
        "shapeSides": 6,
        "color": [1.0, 1.0, 1.0, 1.0],
        "brushSizeMm": 10.0,
        "aspectRatio": 1.0,
        "imageAngleDeg": 0.0,
        "spacingPercent": 100.0,
        "stampAngleMode": "line",
        "stampAngleObjectName": "",
        "ribbonRepeatMode": "repeat",
        "imagePath": "",
        "opacity": 100.0,
        "inoutSizeEnabled": False,
        "inoutOpacityEnabled": False,
        "inoutColorEnabled": False,
        "inPercent": 100.0,
        "outPercent": 100.0,
        "inStartPercent": 0.0,
        "outStartPercent": 0.0,
        "inEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "outEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "inoutStartColor": [1.0, 1.0, 1.0, 1.0],
        "inoutEndColor": [1.0, 1.0, 1.0, 1.0],
    },
    {
        "schemaVersion": 1,
        "presetType": "image_path",
        "presetName": "一枚リボン",
        "description": "始点から終点まで画像ひとつを伸ばして表示",
        "contentSource": "image",
        "drawMode": "ribbon",
        "shapeKind": "circle",
        "shapeSides": 6,
        "color": [1.0, 1.0, 1.0, 1.0],
        "brushSizeMm": 10.0,
        "aspectRatio": 1.0,
        "imageAngleDeg": 0.0,
        "spacingPercent": 100.0,
        "stampAngleMode": "line",
        "stampAngleObjectName": "",
        "ribbonRepeatMode": "stretch",
        "imagePath": "",
        "opacity": 100.0,
        "inoutSizeEnabled": False,
        "inoutOpacityEnabled": False,
        "inoutColorEnabled": False,
        "inPercent": 100.0,
        "outPercent": 100.0,
        "inStartPercent": 0.0,
        "outStartPercent": 0.0,
        "inEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "outEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "inoutStartColor": [1.0, 1.0, 1.0, 1.0],
        "inoutEndColor": [1.0, 1.0, 1.0, 1.0],
    },
    {
        "schemaVersion": 1,
        "presetType": "image_path",
        "presetName": "円形スタンプ",
        "description": "円形の生成形状を連続表示",
        "contentSource": "shape",
        "drawMode": "stamp",
        "shapeKind": "circle",
        "shapeSides": 6,
        "color": [0.0, 0.0, 0.0, 1.0],
        "brushSizeMm": 10.0,
        "aspectRatio": 1.0,
        "imageAngleDeg": 0.0,
        "spacingPercent": 100.0,
        "stampAngleMode": "line",
        "stampAngleObjectName": "",
        "ribbonRepeatMode": "repeat",
        "imagePath": "",
        "opacity": 100.0,
        "inoutSizeEnabled": False,
        "inoutOpacityEnabled": False,
        "inoutColorEnabled": False,
        "inPercent": 100.0,
        "outPercent": 100.0,
        "inStartPercent": 0.0,
        "outStartPercent": 0.0,
        "inEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "outEasingCurve": "0.0000,0.0000;1.0000,1.0000",
        "inoutStartColor": [0.0, 0.0, 0.0, 1.0],
        "inoutEndColor": [0.0, 0.0, 0.0, 1.0],
    },
)


@dataclass(frozen=True)
class ImagePathPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "user"
    data: dict[str, Any]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _local_dir() -> Path:
    return shared_presets.preset_dir("image_paths")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read image path preset index %s: %s", path, exc)
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


def _preset_from_data(data: dict[str, Any], path: Path, source: str) -> ImagePathPreset | None:
    if data.get("presetType") != "image_path":
        return None
    name = str(data.get("presetName") or path.stem).strip()
    if not name:
        return None
    return ImagePathPreset(
        name=name,
        description=str(data.get("description", "") or ""),
        path=path,
        source=source,
        data=data,
    )


def _list_user_presets_raw() -> list[ImagePathPreset]:
    base = _local_dir()
    if not base.is_dir():
        return []
    out: list[ImagePathPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        if path.name.startswith("_"):
            continue
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read image path preset %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        preset = _preset_from_data(data, path, "user")
        if preset is not None:
            out.append(preset)
    return out


def _builtin_presets() -> list[ImagePathPreset]:
    return [
        ImagePathPreset(
            name=str(data["presetName"]),
            description=str(data.get("description", "") or ""),
            path=Path(""),
            source="global",
            data=copy.deepcopy(data),
        )
        for data in _BUILTIN_PRESETS
    ]


def _default_order_key(preset: ImagePathPreset) -> tuple[int, int, str]:
    return (
        0 if preset.name == "標準スタンプ" else 1,
        0 if preset.source == "global" else 1,
        preset.name,
    )


def _order_presets(presets: list[ImagePathPreset], order: list[str]) -> list[ImagePathPreset]:
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


def list_user_presets() -> list[ImagePathPreset]:
    index = _read_local_index()
    return _order_presets(_list_user_presets_raw(), index.get("order", []))


def list_all_presets(_work_dir: Path | None = None) -> list[ImagePathPreset]:
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    by_name = {p.name: p for p in _builtin_presets()}
    for preset in _list_user_presets_raw():
        by_name[preset.name] = preset
    visible = [p for p in by_name.values() if p.name not in hidden]
    return _order_presets(visible, index.get("order", []))


def load_preset_by_name(name: str, work_dir: Path | None = None) -> ImagePathPreset | None:
    name = str(name or "").strip()
    if not name:
        return None
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def apply_preset_to_entry(preset: ImagePathPreset, entry) -> None:
    data = preset.data
    if hasattr(entry, "content_source"):
        entry.content_source = str(data.get("contentSource", getattr(entry, "content_source", "image")) or "image")
    if hasattr(entry, "filepath"):
        entry.filepath = str(data.get("imagePath", getattr(entry, "filepath", "")) or "")
    if hasattr(entry, "shape_kind"):
        entry.shape_kind = str(data.get("shapeKind", getattr(entry, "shape_kind", "circle")) or "circle")
    if hasattr(entry, "shape_sides"):
        entry.shape_sides = int(data.get("shapeSides", getattr(entry, "shape_sides", 6)) or 6)
    if hasattr(entry, "color"):
        entry.color = _color_tuple(data.get("color", getattr(entry, "color", (1.0, 1.0, 1.0, 1.0))))
    entry.draw_mode = str(data.get("drawMode", getattr(entry, "draw_mode", "stamp")) or "stamp")
    entry.brush_size_mm = float(data.get("brushSizeMm", getattr(entry, "brush_size_mm", 10.0)) or 10.0)
    entry.aspect_ratio = float(data.get("aspectRatio", getattr(entry, "aspect_ratio", 1.0)) or 1.0)
    entry.image_angle_deg = float(data.get("imageAngleDeg", getattr(entry, "image_angle_deg", 0.0)) or 0.0)
    entry.spacing_percent = float(data.get("spacingPercent", getattr(entry, "spacing_percent", 100.0)) or 100.0)
    entry.stamp_angle_mode = str(
        data.get("stampAngleMode", getattr(entry, "stamp_angle_mode", "line")) or "line"
    )
    entry.stamp_angle_object_name = str(data.get("stampAngleObjectName", "") or "")
    entry.ribbon_repeat_mode = str(
        data.get("ribbonRepeatMode", getattr(entry, "ribbon_repeat_mode", "repeat")) or "repeat"
    )
    entry.opacity = float(data.get("opacity", getattr(entry, "opacity", 100.0)) or 100.0)
    for attr, key, default in (
        ("inout_size_enabled", "inoutSizeEnabled", False),
        ("inout_opacity_enabled", "inoutOpacityEnabled", False),
        ("inout_color_enabled", "inoutColorEnabled", False),
    ):
        if hasattr(entry, attr):
            setattr(entry, attr, bool(data.get(key, getattr(entry, attr, default))))
    for attr, key, default in (
        ("in_percent", "inPercent", 100.0),
        ("out_percent", "outPercent", 100.0),
        ("in_start_percent", "inStartPercent", 0.0),
        ("out_start_percent", "outStartPercent", 0.0),
    ):
        if hasattr(entry, attr):
            setattr(entry, attr, float(data.get(key, getattr(entry, attr, default)) or default))
    for attr, key in (("in_easing_curve", "inEasingCurve"), ("out_easing_curve", "outEasingCurve")):
        if hasattr(entry, attr):
            setattr(entry, attr, str(data.get(key, getattr(entry, attr, "")) or ""))
    if hasattr(entry, "inout_start_color"):
        entry.inout_start_color = _color_tuple(
            data.get("inoutStartColor", getattr(entry, "inout_start_color", (1.0, 1.0, 1.0, 1.0)))
        )
    if hasattr(entry, "inout_end_color"):
        entry.inout_end_color = _color_tuple(
            data.get("inoutEndColor", getattr(entry, "inout_end_color", (1.0, 1.0, 1.0, 1.0)))
        )


def preset_dict_from_entry(entry, name: str, description: str = "") -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "presetType": "image_path",
        "presetName": name,
        "description": description,
        "contentSource": str(getattr(entry, "content_source", "image") or "image"),
        "shapeKind": str(getattr(entry, "shape_kind", "circle") or "circle"),
        "shapeSides": int(getattr(entry, "shape_sides", 6) or 6),
        "color": _rounded_color(getattr(entry, "color", (1.0, 1.0, 1.0, 1.0))),
        "drawMode": str(getattr(entry, "draw_mode", "stamp") or "stamp"),
        "brushSizeMm": round(float(getattr(entry, "brush_size_mm", 10.0) or 10.0), 4),
        "aspectRatio": round(float(getattr(entry, "aspect_ratio", 1.0) or 1.0), 4),
        "imageAngleDeg": round(float(getattr(entry, "image_angle_deg", 0.0) or 0.0), 4),
        "spacingPercent": round(float(getattr(entry, "spacing_percent", 100.0) or 100.0), 4),
        "stampAngleMode": str(getattr(entry, "stamp_angle_mode", "line") or "line"),
        "stampAngleObjectName": str(getattr(entry, "stamp_angle_object_name", "") or ""),
        "ribbonRepeatMode": str(getattr(entry, "ribbon_repeat_mode", "repeat") or "repeat"),
        "imagePath": str(getattr(entry, "filepath", "") or ""),
        "opacity": round(float(getattr(entry, "opacity", 100.0) or 100.0), 4),
        "inoutSizeEnabled": bool(getattr(entry, "inout_size_enabled", False)),
        "inoutOpacityEnabled": bool(getattr(entry, "inout_opacity_enabled", False)),
        "inoutColorEnabled": bool(getattr(entry, "inout_color_enabled", False)),
        "inPercent": round(float(getattr(entry, "in_percent", 100.0) or 100.0), 4),
        "outPercent": round(float(getattr(entry, "out_percent", 100.0) or 100.0), 4),
        "inStartPercent": round(float(getattr(entry, "in_start_percent", 0.0) or 0.0), 4),
        "outStartPercent": round(float(getattr(entry, "out_start_percent", 0.0) or 0.0), 4),
        "inEasingCurve": str(getattr(entry, "in_easing_curve", "") or ""),
        "outEasingCurve": str(getattr(entry, "out_easing_curve", "") or ""),
        "inoutStartColor": _rounded_color(getattr(entry, "inout_start_color", (1.0, 1.0, 1.0, 1.0))),
        "inoutEndColor": _rounded_color(getattr(entry, "inout_end_color", (1.0, 1.0, 1.0, 1.0))),
    }


def _rounded_color(value) -> list[float]:
    return [round(float(v), 4) for v in _color_tuple(value)]


def _color_tuple(value) -> tuple[float, float, float, float]:
    try:
        vals = [float(value[i]) for i in range(min(4, len(value)))]
    except Exception:  # noqa: BLE001
        vals = [1.0, 1.0, 1.0, 1.0]
    while len(vals) < 4:
        vals.append(1.0)
    return tuple(max(0.0, min(1.0, v)) for v in vals[:4])


def _local_preset_by_name(name: str) -> ImagePathPreset | None:
    for preset in _list_user_presets_raw():
        if preset.name == name:
            return preset
    return None


def _global_preset_by_name(name: str) -> ImagePathPreset | None:
    for preset in _builtin_presets():
        if preset.name == name:
            return preset
    return None


def preset_name_exists(work_dir: Path | None, name: str) -> bool:
    name = str(name or "").strip()
    if not name:
        return False
    return any(p.name == name for p in list_all_presets(work_dir))


def unique_preset_name(work_dir: Path | None, base: str) -> str:
    base = (base or "新規パターンカーブプリセット").strip() or "新規パターンカーブプリセット"
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
    payload["presetType"] = "image_path"
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
    entry,
    name: str,
    description: str = "",
    *,
    insert_after: str = "",
) -> Path:
    is_new = _local_preset_by_name(name) is None
    out = _write_local_preset_data(
        preset_dict_from_entry(entry, name, description),
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
    _logger.info("shared image path preset saved: %s", out)
    return out


def rename_preset(work_dir: Path | None, old_name: str, new_name: str) -> ImagePathPreset:
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


def duplicate_preset(work_dir: Path | None, source_name: str, new_name: str) -> ImagePathPreset:
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


def move_preset(work_dir: Path | None, name: str, direction: str) -> list[str]:
    name = name.strip()
    order = _visible_order_names(work_dir)
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
