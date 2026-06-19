"""フキダシしっぽプリセット管理.

2 層で保持:
- 同梱: アドオン同梱の ``presets/tails/``
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット

しっぽの形状 (直線/曲線/付箋・折れ線/曲線つなぎ)・線種 (三角/楕円)・
太さ・長さなど、しっぽの設定一式を保存する。
色や線幅は親フキダシの設定に従うため、プリセットには含めない。
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
GLOBAL_TAILS_DIR = _ADDON_ROOT / "presets" / "tails"

PRESET_SUFFIX = ".json"

# プリセットに保存する Tail プロパティ ⇔ JSON キーの対応
_TAIL_FIELDS: tuple[tuple[str, str, str], ...] = (
    # (tail attr, json key, kind)
    ("type", "type", "str"),
    ("curve_mode", "curveMode", "str"),
    ("line_type", "lineType", "str"),
    ("ellipse_gap_mm", "ellipseGapMm", "float"),
    ("ellipse_angle_deg", "ellipseAngleDeg", "float"),
    ("ellipse_orient", "ellipseOrient", "str"),
    ("sharp_corners", "sharpCorners", "bool"),
    ("taper_in_percent", "taperInPercent", "float"),
    ("taper_out_percent", "taperOutPercent", "float"),
    ("direction_deg", "directionDeg", "float"),
    ("length_mm", "lengthMm", "float"),
    ("root_width_mm", "rootWidthMm", "float"),
    ("tip_width_mm", "tipWidthMm", "float"),
    ("curve_bend", "curveBend", "float"),
)


@dataclass(frozen=True)
class TailPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "user"
    data: dict[str, Any]


def _list_in_dir(base: Path, *, source: str) -> list[TailPreset]:
    if not base.is_dir():
        return []
    out: list[TailPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read tail preset %s: %s", path, exc)
            continue
        if not isinstance(data, dict) or data.get("presetType") != "balloon_tail":
            continue
        name = str(data.get("presetName") or path.stem)
        description = str(data.get("description", "") or "")
        out.append(TailPreset(name=name, description=description, path=path, source=source, data=data))
    return out


def list_global_presets() -> list[TailPreset]:
    return _list_in_dir(GLOBAL_TAILS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[TailPreset]:
    _migrate_work_presets(work_dir)
    return list_user_presets()


def list_user_presets() -> list[TailPreset]:
    return _list_in_dir(shared_presets.preset_dir("tails"), source="user")


def list_all_presets(work_dir: Path | None) -> list[TailPreset]:
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        _migrate_work_presets(work_dir)
    for p in list_user_presets():
        presets[p.name] = p
    return sorted(presets.values(), key=lambda p: (0 if p.source == "global" else 1, p.name))


def load_preset_by_name(name: str, work_dir: Path | None) -> TailPreset | None:
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def apply_preset_to_tail(preset: TailPreset, tail) -> None:
    """プリセットのしっぽ設定を 1 つのしっぽへ適用 (位置・ポイント列は保持)."""
    data = preset.data.get("tail", {})
    for attr, key, kind in _TAIL_FIELDS:
        if key not in data or not hasattr(tail, attr):
            continue
        try:
            value = data[key]
            if kind == "float":
                setattr(tail, attr, float(value))
            elif kind == "bool":
                setattr(tail, attr, bool(value))
            else:
                setattr(tail, attr, str(value))
        except Exception:  # noqa: BLE001
            _logger.warning("tail preset field apply failed: %s", attr)


def preset_dict_from_tail(tail, name: str, description: str = "") -> dict[str, Any]:
    data: dict[str, Any] = {}
    for attr, key, kind in _TAIL_FIELDS:
        value = getattr(tail, attr, None)
        if value is None:
            continue
        if kind == "float":
            data[key] = round(float(value), 3)
        elif kind == "bool":
            data[key] = bool(value)
        else:
            data[key] = str(value)
    return {
        "schemaVersion": 1,
        "presetType": "balloon_tail",
        "presetName": name,
        "description": description,
        "tail": data,
    }


def _local_preset_by_name(_work_dir: Path | None, name: str) -> TailPreset | None:
    for preset in list_user_presets():
        if preset.name == name:
            return preset
    return None


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"


def preset_name_exists(work_dir: Path, name: str) -> bool:
    return load_preset_by_name(name, work_dir) is not None


def unique_preset_name(work_dir: Path, base: str) -> str:
    base = (base or "新規しっぽプリセット").strip() or "新規しっぽプリセット"
    if not preset_name_exists(work_dir, base):
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if not preset_name_exists(work_dir, candidate):
            return candidate
    return base


def save_local_preset(work_dir: Path, tail, name: str, description: str = "") -> Path:
    del work_dir
    target_dir = shared_presets.preset_dir("tails")
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = _local_preset_by_name(None, name)
    out = existing.path if existing is not None else target_dir / f"{_sanitize_filename(name)}{PRESET_SUFFIX}"
    payload = copy.deepcopy(preset_dict_from_tail(tail, name, description))
    json_io.write_json(out, payload)
    _logger.info("shared tail preset saved: %s", out)
    return out


def delete_local_preset(work_dir: Path, name: str) -> bool:
    del work_dir
    preset = _local_preset_by_name(None, name)
    if preset is None:
        return False
    try:
        preset.path.unlink()
        return True
    except FileNotFoundError:
        return False


def _migrate_work_presets(work_dir: Path | None) -> None:
    if work_dir is None:
        return
    legacy_dir = paths.assets_dir(Path(work_dir)) / paths.ASSETS_TAILS_DIR
    shared_presets.copy_json_presets_once(legacy_dir, shared_presets.preset_dir("tails"))
