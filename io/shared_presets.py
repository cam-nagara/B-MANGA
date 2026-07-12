"""全作品共通プリセットの保存先ヘルパ."""

from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path
from typing import Any

from ..utils import json_io, log

_logger = log.get_logger(__name__)

ENV_CONFIG_DIR = "BMANGA_USER_CONFIG_DIR"
CONFIG_DIR_NAME = "b_manga"
PRESETS_DIR_NAME = "presets"

PRESET_CATEGORIES = {
    "paper": "paper",
    "borders": "borders",
    "balloons": "balloons",
    "tails": "tails",
    "text": "text",
    "image_paths": "image_paths",
    "fills": "fills",
    "gradients": "gradients",
}


def config_root(*, create: bool = True) -> Path:
    override = os.environ.get(ENV_CONFIG_DIR, "").strip()
    if override:
        root = Path(override)
    else:
        try:
            import bpy  # type: ignore

            root = Path(bpy.utils.user_resource("CONFIG", path=CONFIG_DIR_NAME, create=create))
        except Exception:  # noqa: BLE001
            root = Path.home() / f".{CONFIG_DIR_NAME}"
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def presets_root(*, create: bool = True) -> Path:
    root = config_root(create=create) / PRESETS_DIR_NAME
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def preset_dir(category: str, *, create: bool = True) -> Path:
    rel = PRESET_CATEGORIES.get(category, category)
    target = presets_root(create=create) / rel
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def copy_json_presets_once(src_dir: Path, dst_dir: Path) -> int:
    """既存作品内プリセットを共通保存先へ一度だけコピーする."""
    src = Path(src_dir)
    if not src.is_dir():
        return 0
    dst = Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    existing = _list_existing_presets(dst)
    existing_names = {name for name, _data in existing}
    copied = 0
    for src_file in sorted(src.glob("*.json")):
        if src_file.name.startswith("_"):
            continue
        src_data = _read_preset_data(src_file)
        if src_data is None:
            target = dst / src_file.name
            if target.exists():
                continue
            try:
                shutil.copy2(src_file, target)
            except OSError:
                _logger.warning("failed to migrate preset: %s", src_file, exc_info=True)
                continue
            copied += 1
            continue
        src_name = _preset_name(src_data, src_file)
        if _has_equivalent_preset(existing, src_name, src_data):
            continue
        out_data = copy.deepcopy(src_data)
        out_name = src_name
        if src_name in existing_names:
            out_name = _unique_preset_name(existing_names, src_name)
            out_data["presetName"] = out_name
        target = _unique_file_path(dst, _sanitize_filename(out_name), ".json")
        try:
            json_io.write_json(target, out_data)
        except OSError:
            _logger.warning("failed to migrate preset: %s", src_file, exc_info=True)
            continue
        copied += 1
        existing.append((out_name, out_data))
        existing_names.add(out_name)
    return copied


def _list_existing_presets(dst: Path) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(dst.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = _read_preset_data(path)
        if data is None:
            continue
        out.append((_preset_name(data, path), data))
    return out


def _read_preset_data(path: Path) -> dict[str, Any] | None:
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _preset_name(data: dict[str, Any], path: Path) -> str:
    name = str(data.get("presetName") or "").strip()
    return name or path.stem


def _has_equivalent_preset(
    existing: list[tuple[str, dict[str, Any]]],
    src_name: str,
    src_data: dict[str, Any],
) -> bool:
    for existing_name, existing_data in existing:
        if existing_data == src_data:
            return True
        if _is_disambiguated_name(existing_name, src_name) and _same_payload_except_name(
            existing_data, src_data
        ):
            return True
    return False


def _is_disambiguated_name(existing_name: str, src_name: str) -> bool:
    return existing_name == src_name or existing_name.startswith(f"{src_name} ")


def _same_payload_except_name(a: dict[str, Any], b: dict[str, Any]) -> bool:
    aa = dict(a)
    bb = dict(b)
    aa.pop("presetName", None)
    bb.pop("presetName", None)
    return aa == bb


def _unique_preset_name(existing_names: set[str], base: str) -> str:
    base = base.strip() or "プリセット"
    for i in range(2, 1000):
        candidate = f"{base} {i}"
        if candidate not in existing_names:
            return candidate
    return f"{base} 999"


def _unique_file_path(dst: Path, stem: str, suffix: str) -> Path:
    target = dst / f"{stem}{suffix}"
    if not target.exists():
        return target
    for i in range(2, 1000):
        candidate = dst / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return dst / f"{stem}_999{suffix}"


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"
