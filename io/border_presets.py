"""枠線プリセット管理 (枠線セクション + フチセクション).

2 層で保持:
- 同梱: アドオン同梱の ``presets/borders/``
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット

プリセットには枠線とフチの全体設定を ``io/schema.py`` の dict 変換を介して保存する。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import shared_presets
from . import schema

_logger = log.get_logger(__name__)

_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_BORDERS_DIR = _ADDON_ROOT / "presets" / "borders"

PRESET_SUFFIX = ".json"
PRESET_INDEX_FILENAME = "_preset_index.json"
LEGACY_BORDER_PRESET_ALIASES = {
    "ボカシブラシ": "輪郭ぼかし",
}


@dataclass(frozen=True)
class BorderPreset:
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
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _list_in_dir(base: Path, *, source: str) -> list[BorderPreset]:
    if not base.is_dir():
        return []
    out: list[BorderPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read border preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "border":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            BorderPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source=source,
                data=data,
            )
        )
    return out


def _local_dir(_work_dir: Path | None = None) -> Path:
    return shared_presets.preset_dir("borders")


def _legacy_dir(work_dir: Path) -> Path:
    return paths.assets_dir(Path(work_dir)) / paths.ASSETS_BORDERS_DIR


def _local_index_path(_work_dir: Path | None = None) -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index(_work_dir: Path | None = None) -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read border preset index %s: %s", path, exc)
        return {"order": [], "hidden": []}
    if not isinstance(data, dict):
        return {"order": [], "hidden": []}
    order = _string_list(data.get("order", []))
    hidden = _string_list(data.get("hidden", []))
    return {"order": order, "hidden": hidden}


def _write_local_index(_work_dir: Path | None, index: dict[str, Any]) -> None:
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


def _default_order_key(preset: BorderPreset) -> tuple[int, int, str]:
    return (
        0 if preset.name == "標準" else 1,
        0 if preset.source == "global" else 1,
        preset.path.name,
    )


def _order_presets(preset_list: list[BorderPreset], order: list[str]) -> list[BorderPreset]:
    if not order:
        return sorted(preset_list, key=_default_order_key)
    order_pos = {name: i for i, name in enumerate(order)}
    return sorted(
        preset_list,
        key=lambda p: (
            0 if p.name in order_pos else 1,
            order_pos.get(p.name, 0),
            *_default_order_key(p),
        ),
    )


def list_global_presets() -> list[BorderPreset]:
    return _order_presets(_list_in_dir(GLOBAL_BORDERS_DIR, source="global"), [])


def list_local_presets(work_dir: Path) -> list[BorderPreset]:
    _migrate_work_presets(work_dir)
    return list_user_presets()


def list_user_presets() -> list[BorderPreset]:
    index = _read_local_index()
    return _order_presets(_list_in_dir(_local_dir(), source="user"), index.get("order", []))


def list_all_presets(work_dir: Path | None) -> list[BorderPreset]:
    presets = {p.name: p for p in list_global_presets()}
    order: list[str] = []
    hidden: set[str] = set()
    if work_dir is not None:
        _migrate_work_presets(work_dir)
    index = _read_local_index()
    order = list(index.get("order", []))
    hidden = set(index.get("hidden", []))
    for p in list_user_presets():
        presets[p.name] = p
    visible = [p for p in presets.values() if p.name not in hidden]
    return _order_presets(visible, order)


def load_preset_by_name(name: str, work_dir: Path | None) -> BorderPreset | None:
    alias = LEGACY_BORDER_PRESET_ALIASES.get(name, name)
    for preset in list_all_presets(work_dir):
        if preset.name == name or preset.name == alias:
            return preset
    return None


def apply_preset_to_coma(preset: BorderPreset, coma) -> None:
    """プリセットの枠線・フチ・背景設定を 1 つのコマへ適用."""
    schema.coma_border_from_dict(coma.border, preset.data.get("border", {}))
    schema.coma_white_margin_from_dict(coma.white_margin, preset.data.get("whiteMargin", {}))
    # 背景 (表示有無 + 背景色)。古いプリセットには無いキーなので、存在する
    # 場合のみ適用して既存プリセットの挙動を変えない。
    if "paperVisible" in preset.data and hasattr(coma, "paper_visible"):
        coma.paper_visible = bool(preset.data.get("paperVisible", True))
    if "backgroundColor" in preset.data and hasattr(coma, "background_color"):
        try:
            alpha = float(preset.data.get("backgroundColorAlpha", 1.0))
        except (TypeError, ValueError):
            alpha = 1.0
        try:
            coma.background_color = schema.hex_to_rgba(
                str(preset.data.get("backgroundColor", "#FFFFFF")), alpha
            )
        except ValueError:
            _logger.warning("invalid backgroundColor in preset %s", preset.name)
    # セレクタ表示をコマの実状態へ追従させるため、適用プリセット名を記録する。
    try:
        coma.border.preset_name = preset.name
    except Exception:  # noqa: BLE001
        pass


def preset_dict_from_coma(coma, name: str, description: str = "") -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "presetType": "border",
        "presetName": name,
        "description": description,
        "border": schema.coma_border_to_dict(coma.border),
        "whiteMargin": schema.coma_white_margin_to_dict(coma.white_margin),
        "paperVisible": bool(getattr(coma, "paper_visible", True)),
        "backgroundColor": schema.color_to_hex(tuple(coma.background_color)),
        "backgroundColorAlpha": round(float(coma.background_color[3]), 3),
    }


def _local_preset_by_name(_work_dir: Path | None, name: str) -> BorderPreset | None:
    for preset in _list_in_dir(_local_dir(), source="user"):
        if preset.name == name:
            return preset
    return None


def _global_preset_by_name(name: str) -> BorderPreset | None:
    alias = LEGACY_BORDER_PRESET_ALIASES.get(name, name)
    for preset in list_global_presets():
        if preset.name == name or preset.name == alias:
            return preset
    return None


def _safe_local_path(_work_dir: Path | None, name: str) -> Path:
    return _local_dir() / f"{_sanitize_filename(name)}{PRESET_SUFFIX}"


def _write_local_preset_data(
    work_dir: Path,
    data: dict[str, Any],
    name: str,
    *,
    description: str | None = None,
) -> Path:
    target_dir = _local_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = _local_preset_by_name(work_dir, name)
    out = existing.path if existing is not None else _safe_local_path(work_dir, name)
    if existing is None and out.exists():
        raise ValueError(f"同じファイル名のプリセットが既にあります: {out.name}")
    payload = copy.deepcopy(data)
    payload["presetType"] = "border"
    payload["presetName"] = name
    if description is not None:
        payload["description"] = description
    json_io.write_json(out, payload)
    return out


def _visible_order_names(work_dir: Path) -> list[str]:
    return [preset.name for preset in list_all_presets(work_dir)]


def _insert_order_name(work_dir: Path, name: str, *, after_name: str = "") -> None:
    index = _read_local_index()
    order = [item for item in _visible_order_names(work_dir) if item != name]
    if after_name and after_name in order:
        order.insert(order.index(after_name) + 1, name)
    else:
        order.append(name)
    index["order"] = order
    _write_local_index(work_dir, index)


def save_local_preset(
    work_dir: Path,
    coma,
    name: str,
    description: str = "",
    *,
    insert_after: str = "",
) -> Path:
    is_new = _local_preset_by_name(work_dir, name) is None
    out = _write_local_preset_data(
        work_dir,
        preset_dict_from_coma(coma, name, description),
        name,
        description=description,
    )
    index = _read_local_index(work_dir)
    hidden = set(index.get("hidden", []))
    if name in hidden:
        hidden.discard(name)
        index["hidden"] = list(hidden)
        _write_local_index(work_dir, index)
    if is_new:
        _insert_order_name(work_dir, name, after_name=insert_after)
    _logger.info("shared border preset saved: %s", out)
    return out


def preset_name_exists(work_dir: Path, name: str) -> bool:
    return any(p.name == name for p in list_all_presets(work_dir))


def unique_preset_name(work_dir: Path, base: str) -> str:
    base = (base or "新規枠線プリセット").strip() or "新規枠線プリセット"
    if not preset_name_exists(work_dir, base):
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if not preset_name_exists(work_dir, candidate):
            return candidate
    return base


def rename_preset(work_dir: Path, old_name: str, new_name: str) -> BorderPreset:
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

    index = _read_local_index(work_dir)
    order = _visible_order_names(work_dir)
    hidden = set(index.get("hidden", []))
    if preset.source == "global":
        hidden.add(old_name)
    hidden.discard(new_name)
    out = _write_local_preset_data(work_dir, preset.data, new_name)
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
    _write_local_index(work_dir, index)
    result = _local_preset_by_name(work_dir, new_name)
    if result is None:
        raise ValueError(f"プリセットの改名に失敗しました: {new_name}")
    return result


def duplicate_preset(work_dir: Path, source_name: str, new_name: str) -> BorderPreset:
    source_name = source_name.strip()
    new_name = new_name.strip()
    if not source_name or not new_name:
        raise ValueError("プリセット名が空です")
    if preset_name_exists(work_dir, new_name):
        raise ValueError(f"同名のプリセットが既にあります: {new_name}")
    preset = load_preset_by_name(source_name, work_dir)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {source_name}")
    _write_local_preset_data(work_dir, preset.data, new_name)
    index = _read_local_index(work_dir)
    hidden = set(index.get("hidden", []))
    if new_name in hidden:
        hidden.discard(new_name)
        index["hidden"] = list(hidden)
        _write_local_index(work_dir, index)
    _insert_order_name(work_dir, new_name, after_name=source_name)
    result = _local_preset_by_name(work_dir, new_name)
    if result is None:
        raise ValueError(f"プリセットの複製に失敗しました: {new_name}")
    return result


def delete_preset(work_dir: Path, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("プリセット名が空です")
    preset = load_preset_by_name(name, work_dir)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {name}")
    index = _read_local_index(work_dir)
    hidden = set(index.get("hidden", []))
    local = _local_preset_by_name(work_dir, name)
    if local is not None:
        try:
            local.path.unlink()
        except FileNotFoundError:
            pass
    if preset.source == "global" or _global_preset_by_name(name) is not None:
        hidden.add(name)
    index["hidden"] = list(hidden)
    index["order"] = [item for item in _visible_order_names(work_dir) if item != name]
    _write_local_index(work_dir, index)


def move_preset(work_dir: Path, name: str, direction: str) -> list[str]:
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
    preset_index = _read_local_index(work_dir)
    preset_index["order"] = order
    _write_local_index(work_dir, preset_index)
    return order


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"


def _migrate_work_presets(work_dir: Path | None) -> None:
    if work_dir is None:
        return
    legacy_dir = _legacy_dir(Path(work_dir))
    shared_presets.copy_json_presets_once(legacy_dir, _local_dir())
    legacy_index_path = legacy_dir / PRESET_INDEX_FILENAME
    if not legacy_index_path.is_file():
        return
    try:
        legacy_index = json_io.read_json(legacy_index_path)
    except (OSError, ValueError):
        return
    if not isinstance(legacy_index, dict):
        return
    current = _read_local_index()
    order = _string_list(current.get("order", []))
    for name in _string_list(legacy_index.get("order", [])):
        if name not in order:
            order.append(name)
    current["order"] = order
    _write_local_index(None, current)
