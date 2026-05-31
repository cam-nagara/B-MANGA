"""角丸半径の入力単位を実寸へ解決するヘルパ."""

from __future__ import annotations


RADIUS_UNIT_ITEMS = (
    ("mm", "mm", ""),
    ("percent", "%", ""),
)


def max_radius_mm(width_mm: float, height_mm: float) -> float:
    return max(0.0, min(abs(float(width_mm)), abs(float(height_mm))) * 0.5)


def radius_from_values(
    *,
    unit: str,
    radius_mm: float,
    radius_percent: float,
    width_mm: float,
    height_mm: float,
) -> float:
    if str(unit or "mm") == "percent":
        pct = max(0.0, min(100.0, float(radius_percent)))
        return max_radius_mm(width_mm, height_mm) * pct / 100.0
    return max(0.0, float(radius_mm))


def has_positive_value(owner, *, prefix: str = "rounded_corner") -> bool:
    unit = str(getattr(owner, f"{prefix}_radius_unit", "mm") or "mm")
    if unit == "percent":
        return float(getattr(owner, f"{prefix}_radius_percent", 0.0) or 0.0) > 0.0
    return float(getattr(owner, f"{prefix}_radius_mm", 0.0) or 0.0) > 0.0


def radius_for_owner(owner, width_mm: float, height_mm: float, *, prefix: str = "rounded_corner") -> float:
    return radius_from_values(
        unit=str(getattr(owner, f"{prefix}_radius_unit", "mm") or "mm"),
        radius_mm=float(getattr(owner, f"{prefix}_radius_mm", 0.0) or 0.0),
        radius_percent=float(getattr(owner, f"{prefix}_radius_percent", 0.0) or 0.0),
        width_mm=float(width_mm),
        height_mm=float(height_mm),
    )


def radius_for_balloon_entry(entry, rect=None) -> float:
    width = float(getattr(rect, "width", getattr(entry, "width_mm", 0.0)) or 0.0)
    height = float(getattr(rect, "height", getattr(entry, "height_mm", 0.0)) or 0.0)
    return radius_for_owner(entry, width, height)


def radius_for_effect_params(params, prefix: str, width_mm: float, height_mm: float) -> float:
    return radius_for_owner(params, width_mm, height_mm, prefix=f"{prefix}_rounded_corner")
