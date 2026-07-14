"""共通ルビ表示設定の相対値・論理フォント解決."""

from __future__ import annotations

from pathlib import Path

from ..utils import text_style


def gap_em_from_entry(entry) -> float:
    try:
        return max(-2.0, min(4.0, float(getattr(entry, "ruby_gap_em", 0.0))))
    except Exception:  # noqa: BLE001
        return 0.0


def gap_mm_from_entry(entry) -> float:
    base_em = max(0.001, float(getattr(entry, "font_size_q", 20.0) or 20.0) * 0.25)
    return gap_em_from_entry(entry) * base_em


def resolve_font_path(entry) -> str:
    legacy = str(getattr(entry, "ruby_font", "") or "")
    if legacy:
        try:
            resolved = str(Path(legacy).expanduser().resolve())
            if Path(resolved).is_file():
                return resolved
        except OSError:
            pass
    preset = str(getattr(entry, "ruby_font_preset", "inherit") or "inherit")
    if preset == "inherit":
        return text_style.resolve_font_path(str(getattr(entry, "font", "") or ""))
    if preset == "sans-jp":
        return text_style.resolve_font_path("")
    candidates = _logical_candidates(preset)
    for candidate in candidates:
        if Path(candidate).is_file():
            return str(candidate)
    return text_style.resolve_font_path(str(getattr(entry, "font", "") or ""))


def _logical_candidates(preset: str) -> tuple[str, ...]:
    if preset == "serif-jp":
        return (
            r"C:\Windows\Fonts\YuMincho.ttc",
            r"C:\Windows\Fonts\msmincho.ttc",
            "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
        )
    return tuple(text_style.font_candidates())
