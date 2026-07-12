"""カスタムフキダシ形状プリセット管理 (計画書 3.1.4.2b).

2 層:
- 同梱: <addon>/presets/balloons/
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット

パスツールで作成した閉じた頂点列を JSON として保存。Phase 3 段階では
「選択中の BMangaBalloonEntry (shape 任意) の 4 頂点 + 形状パラメータ」
を単純保存する。ベジェ曲線登録は Phase 3 後半。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import shared_presets

_logger = log.get_logger(__name__)

_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_BALLOONS_DIR = _ADDON_ROOT / "presets" / "balloons"

PRESET_SUFFIX = ".json"
PRESET_INDEX_FILENAME = "_preset_index.json"


@dataclass(frozen=True)
class BalloonPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "user"
    data: dict[str, Any]


def _list_in_dir(base: Path, *, source: str) -> list[BalloonPreset]:
    if not base.is_dir():
        return []
    out: list[BalloonPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read balloon preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "balloon":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            BalloonPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source=source,
                data=data,
            )
        )
    return out


def list_global_presets() -> list[BalloonPreset]:
    return _list_in_dir(GLOBAL_BALLOONS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[BalloonPreset]:
    _migrate_work_presets(work_dir)
    return list_user_presets()


def list_user_presets() -> list[BalloonPreset]:
    return _list_in_dir(shared_presets.preset_dir("balloons"), source="user")


def list_all_presets(work_dir: Path | None) -> list[BalloonPreset]:
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        _migrate_work_presets(work_dir)
    index = _read_local_index()
    order = list(index.get("order", []))
    hidden = set(index.get("hidden", []))
    for p in list_user_presets():
        presets[p.name] = p
    visible = [p for p in presets.values() if p.name not in hidden]
    return _order_presets(visible, order)


def save_preset(
    out_path: Path,
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    *,
    absolute_coords: bool = False,
    extras: dict | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "schemaVersion": 1,
        "presetType": "balloon",
        "presetName": name,
        "description": description,
        "coordMode": "absolute" if absolute_coords else "relative",
        "vertices": [[round(x, 3), round(y, 3)] for x, y in vertices_mm],
    }
    if extras:
        data.update(extras)
    json_io.write_json(out_path, data)
    return out_path


def save_local_preset(
    work_dir: Path,
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    absolute_coords: bool = False,
) -> Path:
    del work_dir
    target_dir = shared_presets.preset_dir("balloons")
    safe = _sanitize_filename(name)
    out = target_dir / f"{safe}{PRESET_SUFFIX}"
    is_new = not out.exists()
    result = save_preset(out, name, description, vertices_mm, absolute_coords=absolute_coords)
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if name in hidden:
        hidden.discard(name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    if is_new:
        _insert_order_name(name)
    return result


def save_global_preset(
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    absolute_coords: bool = False,
) -> Path:
    """全作品共通プリセットとして保存する."""
    safe = _sanitize_filename(name)
    out = shared_presets.preset_dir("balloons") / f"{safe}{PRESET_SUFFIX}"
    is_new = not out.exists()
    result = save_preset(out, name, description, vertices_mm, absolute_coords=absolute_coords)
    if is_new:
        _insert_order_name(name)
    return result


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"


# ---------- インデックス基盤 (並び順 / 非表示) ----------


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


def _local_dir() -> Path:
    return shared_presets.preset_dir("balloons")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read balloon preset index %s: %s", path, exc)
        return {"order": [], "hidden": []}
    if not isinstance(data, dict):
        return {"order": [], "hidden": []}
    order = _string_list(data.get("order", []))
    hidden = _string_list(data.get("hidden", []))
    return {"order": order, "hidden": hidden}


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


def _default_order_key(preset: BalloonPreset) -> tuple[int, str]:
    return (
        0 if preset.source == "global" else 1,
        preset.path.name,
    )


def _order_presets(preset_list: list[BalloonPreset], order: list[str]) -> list[BalloonPreset]:
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


def _safe_local_path(name: str) -> Path:
    return _local_dir() / f"{_sanitize_filename(name)}{PRESET_SUFFIX}"


def _global_preset_by_name(name: str) -> BalloonPreset | None:
    for preset in list_global_presets():
        if preset.name == name:
            return preset
    return None


def _local_preset_by_name(name: str) -> BalloonPreset | None:
    for preset in _list_in_dir(_local_dir(), source="user"):
        if preset.name == name:
            return preset
    return None


def _visible_order_names() -> list[str]:
    return [preset.name for preset in list_all_presets(None)]


def _insert_order_name(name: str, *, after_name: str = "") -> None:
    index = _read_local_index()
    order = [item for item in _visible_order_names() if item != name]
    if after_name and after_name in order:
        order.insert(order.index(after_name) + 1, name)
    else:
        order.append(name)
    index["order"] = order
    _write_local_index(index)


def _write_local_preset_data(
    data: dict[str, Any],
    name: str,
    *,
    description: str = "",
) -> Path:
    target_dir = _local_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = _local_preset_by_name(name)
    out = existing.path if existing is not None else _safe_local_path(name)
    if existing is None and out.exists():
        raise ValueError(f"同じファイル名のプリセットが既にあります: {out.name}")
    payload = copy.deepcopy(data)
    payload["presetType"] = "balloon"
    payload["presetName"] = name
    if description:
        payload["description"] = description
    json_io.write_json(out, payload)
    return out


# ---------- CRUD ----------


def preset_name_exists(name: str) -> bool:
    return any(p.name == name for p in list_all_presets(None))


def unique_preset_name(base: str = "新規フキダシプリセット") -> str:
    base = (base or "新規フキダシプリセット").strip() or "新規フキダシプリセット"
    if not preset_name_exists(base):
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if not preset_name_exists(candidate):
            return candidate
    return base


def load_preset_by_name(name: str) -> BalloonPreset | None:
    for preset in list_all_presets(None):
        if preset.name == name:
            return preset
    return None


def rename_preset(old_name: str, new_name: str) -> BalloonPreset:
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
    if preset.source == "global":
        hidden.add(old_name)
    hidden.discard(new_name)
    out = _write_local_preset_data(preset.data, new_name)
    if preset.source == "user" and preset.path != out:
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


def duplicate_preset(source_name: str, new_name: str) -> BalloonPreset:
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
    if preset.source == "global" or _global_preset_by_name(name) is not None:
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


def _migrate_work_presets(work_dir: Path | None) -> None:
    if work_dir is None:
        return
    legacy_dir = paths.assets_dir(Path(work_dir)) / paths.ASSETS_BALLOONS_DIR
    shared_presets.copy_json_presets_once(legacy_dir, shared_presets.preset_dir("balloons"))
