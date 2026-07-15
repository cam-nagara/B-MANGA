"""用紙プリセット管理.

2 層で保持:
- 同梱: アドオン同梱の ``presets/paper/`` (B-MANGA/presets/paper/)
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット

既定プリセット「商業誌B4マンガ原稿用紙」は同梱 JSON として配布する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import shared_presets
from . import schema

_logger = log.get_logger(__name__)

# アドオンルート直下の presets/paper/
_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_PRESETS_DIR = _ADDON_ROOT / "presets" / "paper"

PRESET_SUFFIX = ".json"


@dataclass(frozen=True)
class PaperPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "user"
    data: dict[str, Any]


# ---------- 列挙 ----------


def list_global_presets() -> list[PaperPreset]:
    return _list_presets_in_dir(GLOBAL_PRESETS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[PaperPreset]:
    _migrate_work_presets(work_dir)
    return list_user_presets()


def list_user_presets() -> list[PaperPreset]:
    return _list_presets_in_dir(shared_presets.preset_dir("paper"), source="user")


def list_all_presets(work_dir: Path | None) -> list[PaperPreset]:
    """同梱 → 共通の順で返す (同名があれば共通優先で上書き)."""
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        _migrate_work_presets(work_dir)
    for p in list_user_presets():
        presets[p.name] = p
    return list(presets.values())


def _list_presets_in_dir(base: Path, *, source: str) -> list[PaperPreset]:
    if not base.is_dir():
        return []
    out: list[PaperPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "paper":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            PaperPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source=source,
                data=data,
            )
        )
    return out


# ---------- 適用・保存 ----------


def _apply_display_on_canvas(data: dict[str, Any], work_info) -> None:
    if not data or work_info is None:
        return
    schema.display_item_from_dict(work_info.display_work_name, data.get("workName", {}))
    schema.display_item_from_dict(work_info.display_episode, data.get("episode", {}))
    schema.display_item_from_dict(work_info.display_subtitle, data.get("subtitle", {}))
    schema.display_item_from_dict(work_info.display_author, data.get("author", {}))
    schema.display_item_from_dict(work_info.display_page_number, data.get("pageNumber", {}))
    if hasattr(work_info, "font"):
        work_info.font = data.get("font", "")


def _display_on_canvas_to_dict(work_info) -> dict[str, Any]:
    if work_info is None:
        return {}
    d: dict[str, Any] = {
        "workName": schema.display_item_to_dict(work_info.display_work_name),
        "episode": schema.display_item_to_dict(work_info.display_episode),
        "subtitle": schema.display_item_to_dict(work_info.display_subtitle),
        "author": schema.display_item_to_dict(work_info.display_author),
        "pageNumber": schema.display_item_to_dict(work_info.display_page_number),
    }
    font = str(getattr(work_info, "font", "") or "")
    if font:
        d["font"] = font
    return d


def apply_preset_to_paper(preset: PaperPreset, paper) -> None:
    schema.paper_from_dict(paper, preset.data.get("paper", {}))
    paper.preset_name = preset.name


def apply_preset_to_work(preset: PaperPreset, work) -> None:
    apply_preset_to_paper(preset, work.paper)
    _apply_display_on_canvas(preset.data.get("displayOnCanvas", {}), work.work_info)
    # 初期同梱プリセットは旧キー ``panelGap`` で配布されていたため、
    # 現行の ``comaGap`` を優先しつつ旧ファイルも欠落なく読み込む。
    gap_data = preset.data.get("comaGap")
    if gap_data is None:
        gap_data = preset.data.get("panelGap")
    if isinstance(gap_data, dict):
        schema.coma_gap_from_dict(work.coma_gap, gap_data)


def save_local_preset(work_dir: Path, work, name: str, description: str = "") -> Path:
    """現在の用紙関連設定を全作品共通プリセットとして保存."""
    del work_dir
    templates = shared_presets.preset_dir("paper")
    templates.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(name)
    out = templates / f"{safe_name}{PRESET_SUFFIX}"
    paper_data = schema.paper_to_dict(work.paper)
    paper_data["presetName"] = name
    data = {
        "schemaVersion": 1,
        "presetType": "paper",
        "presetName": name,
        "description": description,
        "paper": paper_data,
        "displayOnCanvas": _display_on_canvas_to_dict(work.work_info),
        "comaGap": schema.coma_gap_to_dict(work.coma_gap),
    }
    json_io.write_json(out, data)
    _logger.info("shared paper preset saved: %s", out)
    return out


def load_preset_by_name(name: str, work_dir: Path | None) -> PaperPreset | None:
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def load_default_preset(paper) -> PaperPreset | None:
    """既定の「商業誌B4マンガ原稿用紙」を PaperSettings に適用."""
    for preset in list_global_presets():
        if preset.name == "商業誌B4マンガ原稿用紙":
            apply_preset_to_paper(preset, paper)
            return preset
    _logger.warning("default preset '商業誌B4マンガ原稿用紙' not found under %s", GLOBAL_PRESETS_DIR)
    return None


def load_default_preset_for_work(work) -> PaperPreset | None:
    """既定の「商業誌B4マンガ原稿用紙」を用紙関連設定全体に適用."""
    for preset in list_global_presets():
        if preset.name == "商業誌B4マンガ原稿用紙":
            apply_preset_to_work(preset, work)
            return preset
    _logger.warning("default preset '商業誌B4マンガ原稿用紙' not found under %s", GLOBAL_PRESETS_DIR)
    return None


# ---------- util ----------

_FORBIDDEN_CHARS = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN_CHARS else ch for ch in name.strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned or "preset"


def _migrate_work_presets(work_dir: Path | None) -> None:
    if work_dir is None:
        return
    legacy_dir = paths.assets_dir(Path(work_dir)) / paths.ASSETS_TEMPLATES_DIR
    shared_presets.copy_json_presets_once(legacy_dir, shared_presets.preset_dir("paper"))
