"""枠線プリセット管理 (枠線セクション + 白フチセクション).

2 層で保持 (`io/presets.py` / `io/balloon_presets.py` を踏襲):
- グローバル: アドオン同梱の ``presets/borders/``
- 作品ローカル: ``MyWork.bname/assets/borders/``

プリセットには枠線と白フチの全体設定を ``io/schema.py`` の dict 変換を介して保存する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import schema

_logger = log.get_logger(__name__)

_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_BORDERS_DIR = _ADDON_ROOT / "presets" / "borders"

PRESET_SUFFIX = ".json"


@dataclass(frozen=True)
class BorderPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "local"
    data: dict[str, Any]


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


def list_global_presets() -> list[BorderPreset]:
    return _list_in_dir(GLOBAL_BORDERS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[BorderPreset]:
    target = paths.assets_dir(Path(work_dir)) / paths.ASSETS_BORDERS_DIR
    return _list_in_dir(target, source="local")


def list_all_presets(work_dir: Path | None) -> list[BorderPreset]:
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        for p in list_local_presets(work_dir):
            presets[p.name] = p
    return list(presets.values())


def load_preset_by_name(name: str, work_dir: Path | None) -> BorderPreset | None:
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def apply_preset_to_coma(preset: BorderPreset, coma) -> None:
    """プリセットの枠線・白フチ設定を 1 つのコマへ適用."""
    schema.coma_border_from_dict(coma.border, preset.data.get("border", {}))
    schema.coma_white_margin_from_dict(coma.white_margin, preset.data.get("whiteMargin", {}))


def preset_dict_from_coma(coma, name: str, description: str = "") -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "presetType": "border",
        "presetName": name,
        "description": description,
        "border": schema.coma_border_to_dict(coma.border),
        "whiteMargin": schema.coma_white_margin_to_dict(coma.white_margin),
    }


def save_local_preset(work_dir: Path, coma, name: str, description: str = "") -> Path:
    target_dir = paths.assets_dir(Path(work_dir)) / paths.ASSETS_BORDERS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = _sanitize_filename(name)
    out = target_dir / f"{safe}{PRESET_SUFFIX}"
    json_io.write_json(out, preset_dict_from_coma(coma, name, description))
    _logger.info("local border preset saved: %s", out)
    return out


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"
