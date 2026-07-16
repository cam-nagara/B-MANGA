"""Meldexの本文・ルビ表示設定をB-MANGAのTextEntryへ安全に適用する."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from ..utils import color_space, log, text_style
from . import meldex_scenario_file
from .meldex_contract import ScenarioDocument, ScenarioPage, validate_payload

_logger = log.get_logger(__name__)
_CSS_PX_TO_Q = 127.0 / 120.0  # CSS 96 dpi: 1px = 0.264583mm = 1.058333Q
_CSS_PX_TO_MM = 25.4 / 96.0
_GENERIC_FONT_FAMILIES = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
    "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded",
}


def is_enabled(context=None) -> bool:
    """保存済み旧ルビ専用値を参照せず、新しい設定だけを判定する."""
    try:
        from ..preferences import get_preferences

        prefs = get_preferences(context)
    except Exception:  # noqa: BLE001 - プリファレンス未登録中は安全側のオフ
        return False
    return bool(getattr(prefs, "meldex_apply_text_presentation", False)) if prefs else False


def enrich_from_source_file(document: ScenarioDocument) -> ScenarioDocument:
    """旧Meldex直接送信payloadへ、保存元ファイルの本文設定を補完する.

    直接送信の本文・ルビ内容は常に受信payloadを正とし、表示設定だけを保存元から
    補う。Meldex側は送信前保存を必須にしているため、ファイル取込と同じ設定になる。
    """
    if document.version < 2 or (document.presentation and "text" in document.presentation):
        return document
    path = _local_source_path(document.document_id)
    if path is None or not path.is_file():
        return document
    try:
        source = validate_payload(meldex_scenario_file.load_contract_payload(path))
    except Exception as exc:  # noqa: BLE001 - 受信payloadのルビだけでも取込は継続できる
        _logger.warning("Meldex text settings could not be read from %s: %s", path, exc)
        return document

    source_rows = {
        row.row_id: row
        for page in source.pages
        for row in page.rows
    }
    pages = tuple(
        ScenarioPage(tuple(
            replace(
                row,
                presentation=merge_presentations(
                    getattr(source_rows.get(row.row_id), "presentation", None),
                    row.presentation,
                ),
            )
            for row in page.rows
        ))
        for page in document.pages
    )
    return replace(
        document,
        pages=pages,
        presentation=merge_presentations(source.presentation, document.presentation),
    )


def _local_source_path(document_id: str) -> Path | None:
    """認証済み受信でも相対パスやUNC共有への暗黙アクセスは許可しない."""
    raw = str(document_id or "").strip()
    path = Path(raw)
    if (
        not raw
        or raw.startswith(("\\\\", "//"))
        or not path.is_absolute()
        or not path.name.lower().endswith(meldex_scenario_file.SUPPORTED_SUFFIXES)
    ):
        return None
    return path


def merge_presentations(*sources: dict[str, Any] | None) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for section in ("text", "ruby"):
            value = source.get(section)
            if isinstance(value, dict):
                merged[section] = {**merged.get(section, {}), **value}
    return merged or None


def apply_to_entry(entry, presentation: dict[str, Any] | None) -> None:
    if not isinstance(presentation, dict):
        return
    text = presentation.get("text") if isinstance(presentation.get("text"), dict) else {}
    ruby = presentation.get("ruby") if isinstance(presentation.get("ruby"), dict) else {}
    from ..core.text_entry import prime_writing_mode_tracking

    prime_writing_mode_tracking(entry)
    writing_mode = text.get("writingMode", ruby.get("writingMode"))
    if writing_mode in {"horizontal", "vertical"}:
        entry.writing_mode = writing_mode
    if "fontSizePx" in text:
        entry.font_size_unit = "q"
        entry.font_size_q = float(text["fontSizePx"]) * _CSS_PX_TO_Q
    if "lineHeight" in text:
        entry.line_height = float(text["lineHeight"])
    if "letterSpacingEm" in text:
        entry.letter_spacing = float(text["letterSpacingEm"])
    if "bold" in text:
        entry.font_bold = bool(text["bold"])
    if "italic" in text:
        entry.font_italic = bool(text["italic"])
    if "color" in text:
        _assign_color(entry.color, _linear_rgba(text["color"]))
    if "fontFamily" in text:
        resolved_font = resolve_installed_font_family(str(text["fontFamily"]))
        if resolved_font:
            entry.font = resolved_font
    if "strokeWidthPx" in text:
        width_px = float(text["strokeWidthPx"])
        entry.stroke_enabled = width_px > 0.0
        entry.stroke_width_mm = width_px * _CSS_PX_TO_MM
    if "strokeColor" in text:
        _assign_color(entry.stroke_color, _linear_rgba(text["strokeColor"]))

    ruby_mapping = {
        "sizePercent": "ruby_size_percent",
        "gapEm": "ruby_gap_em",
        "letterSpacingEm": "ruby_letter_spacing",
        "lineHeight": "ruby_line_height",
        "align": "ruby_align",
        "smallKana": "ruby_small_kana",
        "fontPreset": "ruby_font_preset",
        "defaultStyle": "ruby_default_style",
    }
    for source_key, target_key in ruby_mapping.items():
        if source_key in ruby:
            setattr(entry, target_key, ruby[source_key])


@lru_cache(maxsize=64)
def resolve_installed_font_family(css_family: str) -> str:
    """CSS論理フォント名をこのPCのフォントへ解決し、パス自体は転送しない."""
    requested = [
        token.strip().strip("'\"")
        for token in str(css_family or "").split(",")
        if token.strip()
    ]
    requested = [item for item in requested if item.casefold() not in _GENERIC_FONT_FAMILIES]
    if not requested:
        return ""
    fonts: dict[str, str] = {}
    for path in text_style.available_font_paths():
        family = text_style._parse_font_family_name(path) or Path(path).stem  # noqa: SLF001
        fonts.setdefault(_font_key(family), path)
        fonts.setdefault(_font_key(Path(path).stem), path)
    for family in requested:
        resolved = fonts.get(_font_key(family))
        if resolved:
            return resolved
    return ""


def _font_key(value: str) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "")).casefold()


def _linear_rgba(value: str) -> tuple[float, float, float, float]:
    raw = str(value).lstrip("#")
    channels = [int(raw[index:index + 2], 16) / 255.0 for index in range(0, len(raw), 2)]
    rgb = color_space.srgb_to_linear_rgb(channels[:3])
    alpha = channels[3] if len(channels) == 4 else 1.0
    return rgb[0], rgb[1], rgb[2], alpha


def _assign_color(target, value: tuple[float, float, float, float]) -> None:
    for index, channel in enumerate(value):
        target[index] = float(channel)
