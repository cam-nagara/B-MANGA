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
PRESET_INDEX_FILENAME = "_preset_index.json"

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
    "ruby_gap_em",
    "ruby_letter_spacing",
    "ruby_size_percent",
    "ruby_font",
    "ruby_font_preset",
    "ruby_align",
    "ruby_small_kana",
    "ruby_default_style",
    "stroke_enabled",
    "stroke_width_mm",
    "stroke_color",
    "linked_balloon_preset",
)


@dataclass(frozen=True)
class TextPreset:
    name: str
    description: str
    path: Path
    source: str
    data: dict[str, Any]


def normalize_font_size_unit(value: Any) -> str:
    """サイズ単位を正規形 ("q" / "pt") へ揃える.

    エントリ側 EnumProperty (core/text_entry.py の _FONT_SIZE_UNIT_ITEMS) の
    識別子は小文字だが、旧同梱プリセット等に大文字 "Q" が残っており、
    そのまま代入すると TypeError になる (v0.6.497 実機で発生)。
    """
    unit = str(value or "q").strip().lower()
    return unit if unit in {"q", "pt"} else "q"


def _normalize_preset_data(data: dict[str, Any]) -> dict[str, Any]:
    if "font_size_unit" in data:
        data["font_size_unit"] = normalize_font_size_unit(data.get("font_size_unit"))
    return data


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
                data=_normalize_preset_data(data),
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
    if work_dir is not None:
        _migrate_work_presets(work_dir)
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    presets = {}
    for p in list_global_presets():
        if p.name not in hidden:
            presets[p.name] = p
    for p in list_user_presets():
        if p.name not in hidden:
            presets[p.name] = p
    order = list(index.get("order", []))
    ordered = []
    for name in order:
        if name in presets:
            ordered.append(presets.pop(name))
    for p in presets.values():
        ordered.append(p)
    return ordered


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
    from ..core.text_entry import prime_writing_mode_tracking

    prime_writing_mode_tracking(entry)
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
    is_new = _local_preset_by_name(name) is None
    target = shared_presets.preset_dir("text")
    filename = name.replace("/", "_").replace("\\", "_") + PRESET_SUFFIX
    result = save_preset(target / filename, name, description, entry_data)
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if name in hidden:
        hidden.discard(name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    if is_new:
        _insert_order_name(name)
    return result


def _migrate_work_presets(work_dir: Path | None) -> None:
    if work_dir is None:
        return
    legacy_dir = paths.assets_dir(Path(work_dir)) / "text_presets"
    shared_presets.copy_json_presets_once(legacy_dir, shared_presets.preset_dir("text"))


# ---------- 共通プリセット CRUD (改名・複製・削除・並べ替え) ----------


def _local_dir() -> Path:
    return shared_presets.preset_dir("text")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict:
    path = _local_index_path()
    if not path.is_file():
        return {"schemaVersion": 1, "order": [], "hidden": []}
    try:
        return json_io.read_json(path)
    except (OSError, ValueError):
        return {"schemaVersion": 1, "order": [], "hidden": []}


def _write_local_index(index: dict) -> None:
    path = _local_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_io.write_json(path, index)


def _safe_local_path(name: str) -> Path:
    safe = _sanitize_filename(name)
    return _local_dir() / f"{safe}{PRESET_SUFFIX}"


def _sanitize_filename(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in forbidden else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"


def _global_preset_by_name(name: str) -> TextPreset | None:
    for p in list_global_presets():
        if p.name == name:
            return p
    return None


def _local_preset_by_name(name: str) -> TextPreset | None:
    for p in list_user_presets():
        if p.name == name:
            return p
    return None


def _visible_order_names() -> list[str]:
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    order = list(index.get("order", []))
    # Add any presets not yet in order
    all_names = set()
    for p in list_global_presets():
        if p.name not in hidden:
            all_names.add(p.name)
    for p in list_user_presets():
        if p.name not in hidden:
            all_names.add(p.name)
    for name in all_names:
        if name not in order:
            order.append(name)
    return [n for n in order if n in all_names]


def _insert_order_name(name: str, *, after_name: str = "") -> None:
    index = _read_local_index()
    order = list(index.get("order", []))
    if name in order:
        return
    if after_name and after_name in order:
        idx = order.index(after_name) + 1
        order.insert(idx, name)
    else:
        order.append(name)
    index["order"] = order
    _write_local_index(index)


def _write_local_preset_data(data: dict, name: str, *, description: str = "") -> Path:
    data = dict(data)
    data["presetName"] = name
    if description:
        data["description"] = description
    if "schemaVersion" not in data:
        data["schemaVersion"] = 1
    if "presetType" not in data:
        data["presetType"] = "text"
    out = _safe_local_path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    json_io.write_json(out, data)
    return out


def preset_name_exists(name: str) -> bool:
    return any(p.name == name for p in list_all_presets(None))


def unique_preset_name(base: str) -> str:
    base = (base or "新規テキストプリセット").strip() or "新規テキストプリセット"
    if not preset_name_exists(base):
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if not preset_name_exists(candidate):
            return candidate
    return base


def load_preset_by_name(name: str) -> TextPreset | None:
    for preset in list_all_presets(None):
        if preset.name == name:
            return preset
    return None


def rename_preset(old_name: str, new_name: str) -> TextPreset:
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


def duplicate_preset(source_name: str, new_name: str) -> TextPreset:
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
    idx = order.index(name)
    if direction == "UP":
        new_idx = max(0, idx - 1)
    elif direction == "DOWN":
        new_idx = min(len(order) - 1, idx + 1)
    else:
        raise ValueError(f"不明な移動方向です: {direction}")
    if new_idx != idx:
        order.insert(new_idx, order.pop(idx))
    index = _read_local_index()
    index["order"] = order
    _write_local_index(index)
    return order
