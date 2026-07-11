"""フキダシ白抜き線の旧保存値を現行のUI単位へ移行する。"""

from __future__ import annotations

from typing import Any


SETTINGS_VERSION = 2


def _number_or(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def settings_version(data: dict[str, Any]) -> int:
    try:
        return int(data.get("whiteOutlineSettingsVersion", 1) or 1)
    except (TypeError, ValueError):
        return 1


def legacy_white_brush_mm(data: dict[str, Any]) -> float:
    """旧・非表示倍率を含む白線太さを、自由変形前の基準mmで返す。"""
    line_width = max(0.0, _number_or(data.get("lineWidthMm"), 0.3))
    # 旧生成は 0 を `or 100` で扱っていたため、その境界挙動も再現する。
    line_peak_value = _number_or(data.get("linePeakWidthPct"), 100.0) or 100.0
    white_peak_value = _number_or(data.get("flashWhiteLinePeakWidthPct"), 100.0) or 100.0
    line_peak = max(0.0, line_peak_value) / 100.0
    white_peak = max(0.0, white_peak_value) / 100.0
    white_scale = max(0.0, _number_or(data.get("flashWhiteLineWidthPercent"), 100.0)) / 100.0
    return max(0.01, line_width * line_peak * white_peak * white_scale)


def prepare_legacy_values(entry, params: dict[str, Any]) -> None:
    """旧保存値で欠けている項目へ、当時の初期値を設定する。"""
    defaults = {
        "white_outline_width_min_percent": 100.0,
        "white_outline_length_min_percent": 100.0,
        "white_outline_white_line_count_auto": False,
        "white_outline_black_line_count_auto": False,
        "white_outline_white_ratio_percent": 70.0,
        "white_outline_black_ratio_percent": 30.0,
        "white_outline_white_in_percent": 100.0,
        "white_outline_white_out_percent": 100.0,
    }
    for field, value in defaults.items():
        if field not in params and hasattr(entry, field):
            setattr(entry, field, value)


def finish_legacy_migration(entry, data: dict[str, Any]) -> None:
    """旧隠し係数と0～1のUI値を、画面に表示する実効値へ一度だけ変換する。"""
    if abs(_number_or(data.get("linePeakWidthPct"), 100.0)) <= 1.0e-9:
        # 旧生成は0を100として扱っていたため、実効値をUIにも明示する。
        entry.line_peak_width_pct = 100.0
    peak_value = _number_or(data.get("flashWhiteLinePeakWidthPct"), 100.0) or 100.0
    peak = max(0.0, peak_value)
    valley = max(0.0, _number_or(data.get("flashWhiteLineValleyWidthPct"), 0.0))
    endpoint = 0.0 if peak <= 1.0e-6 else max(0.0, min(100.0, valley / peak * 100.0))
    for field in ("white_outline_white_in_percent", "white_outline_white_out_percent"):
        if hasattr(entry, field):
            setattr(entry, field, _number_or(getattr(entry, field), 0.0) * endpoint / 100.0)
    # 旧UIの0～1はFACTOR（0～100%）だったため、新しい百分率へ変換する。
    for field in ("white_outline_white_attenuation", "white_outline_black_attenuation"):
        if hasattr(entry, field):
            converted = _number_or(getattr(entry, field), 0.0) * 100.0
            setattr(entry, field, max(-100.0, min(100.0, converted)))
