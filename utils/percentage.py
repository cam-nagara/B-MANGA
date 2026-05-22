"""UI percentage value helpers."""

from __future__ import annotations


def clamp_percent(value, default: float = 100.0) -> float:
    try:
        result = float(value)
    except Exception:  # noqa: BLE001
        result = float(default)
    return max(0.0, min(100.0, result))


def percent_to_factor(value, default: float = 100.0) -> float:
    return clamp_percent(value, default) / 100.0


def legacy_factor_to_percent(value, default: float = 100.0) -> float:
    try:
        result = float(value)
    except Exception:  # noqa: BLE001
        return clamp_percent(default)
    if 0.0 <= result <= 1.0:
        return clamp_percent(result * 100.0)
    return clamp_percent(result)
