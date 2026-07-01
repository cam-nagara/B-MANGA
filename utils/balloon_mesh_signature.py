"""Generated balloon mesh cache signatures."""

from __future__ import annotations

import json
from typing import Any

from . import free_transform

_SHAPE_PARAM_FIELDS = (
    "cloud_bump_width_mm",
    "cloud_bump_width_jitter",
    "cloud_bump_height_mm",
    "cloud_bump_height_jitter",
    "cloud_offset_percent",
    "shape_seed",
    "cloud_sub_width_ratio",
    "cloud_sub_width_jitter",
    "cloud_sub_height_ratio",
    "cloud_sub_height_jitter",
    "cloud_valley_sharp",
    "dynamic_shape_base_kind",
    "dynamic_base_rounded_corner_enabled",
    "dynamic_base_rounded_corner_radius_mm",
    "dynamic_base_rounded_corner_radius_unit",
    "dynamic_base_rounded_corner_radius_percent",
    "cloud_wave_count",
    "cloud_wave_amplitude_mm",
    "spike_count",
    "spike_depth_mm",
    "spike_jitter",
)

_TAIL_FIELDS = (
    "type",
    "direction_deg",
    "length_mm",
    "root_width_mm",
    "tip_width_mm",
    "curve_bend",
    "custom_points_enabled",
    "start_x_mm",
    "start_y_mm",
    "end_x_mm",
    "end_y_mm",
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return float(default)


def _value(value: Any) -> Any:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return str(value)


def shape_params(entry) -> dict[str, Any]:
    params = getattr(entry, "shape_params", None)
    if params is None:
        return {}
    data: dict[str, Any] = {}
    for field in _SHAPE_PARAM_FIELDS:
        if hasattr(params, field):
            data[field] = _value(getattr(params, field))
    return data


def tails(entry) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tail in getattr(entry, "tails", []) or []:
        item: dict[str, Any] = {}
        for field in _TAIL_FIELDS:
            if hasattr(tail, field):
                item[field] = _value(getattr(tail, field))
        points = []
        for point in getattr(tail, "points", []) or []:
            points.append(
                {
                    "x_mm": _number(getattr(point, "x_mm", 0.0)),
                    "y_mm": _number(getattr(point, "y_mm", 0.0)),
                    "corner_type": str(getattr(point, "corner_type", "") or ""),
                }
            )
        item["points"] = points
        out.append(item)
    return out


def entry_shape(entry) -> dict[str, Any]:
    return {
        "shape": str(getattr(entry, "shape", "") or ""),
        "custom_preset_name": str(getattr(entry, "custom_preset_name", "") or ""),
        "width_mm": _number(getattr(entry, "width_mm", 0.0)),
        "height_mm": _number(getattr(entry, "height_mm", 0.0)),
        "center_offset_x_mm": _number(getattr(entry, "center_offset_x_mm", 0.0)),
        "center_offset_y_mm": _number(getattr(entry, "center_offset_y_mm", 0.0)),
        "corner_type": str(getattr(entry, "corner_type", "") or ""),
        "rounded_corner_enabled": bool(getattr(entry, "rounded_corner_enabled", False)),
        "rounded_corner_radius_mm": _number(getattr(entry, "rounded_corner_radius_mm", 0.0)),
        "rounded_corner_radius_unit": str(getattr(entry, "rounded_corner_radius_unit", "") or ""),
        "rounded_corner_radius_percent": _number(getattr(entry, "rounded_corner_radius_percent", 0.0)),
        "free_transform": free_transform.entry_snapshot(entry),
        "free_transform_line_width_scale": _number(
            getattr(entry, "free_transform_line_width_scale", 1.0),
            1.0,
        ),
        "shape_params": shape_params(entry),
        "tails": tails(entry),
    }


def stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
