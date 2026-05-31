"""Line pattern settings shared by balloon drawing/export paths."""

from __future__ import annotations


def dashed_segment_mm(owner, line_width_mm: float = 0.3) -> float:
    try:
        value = float(getattr(owner, "dashed_segment_length_mm"))
        if value > 0.0:
            return value
    except Exception:  # noqa: BLE001
        pass
    return max(float(line_width_mm) * 12.0, 3.6)


def dashed_gap_mm(owner, line_width_mm: float = 0.3) -> float:
    try:
        value = float(getattr(owner, "dashed_gap_mm"))
        if value >= 0.0:
            return value
    except Exception:  # noqa: BLE001
        pass
    return max(float(line_width_mm) * 8.0, 2.4)


def dotted_gap_mm(owner, line_width_mm: float = 0.3) -> float:
    try:
        value = float(getattr(owner, "dotted_gap_mm"))
        if value >= 0.0:
            return value
    except Exception:  # noqa: BLE001
        pass
    return max(float(line_width_mm) * 1.5, 0.45)


def dotted_center_spacing_mm(owner, line_width_mm: float = 0.3) -> float:
    diameter = max(0.001, float(line_width_mm))
    return diameter + dotted_gap_mm(owner, line_width_mm)
