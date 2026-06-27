"""画像パスレイヤーの保存復元."""

from __future__ import annotations

from typing import Any

from ..utils import color_space, percentage


def image_path_layer_to_dict(entry) -> dict[str, Any]:
    color = color_space.linear_to_srgb_rgb(tuple(float(c) for c in getattr(entry, "color", (1.0, 1.0, 1.0, 1.0))[:3]))
    inout_start = color_space.linear_to_srgb_rgb(
        tuple(float(c) for c in getattr(entry, "inout_start_color", (1.0, 1.0, 1.0, 1.0))[:3])
    )
    inout_end = color_space.linear_to_srgb_rgb(
        tuple(float(c) for c in getattr(entry, "inout_end_color", (1.0, 1.0, 1.0, 1.0))[:3])
    )
    return {
        "id": str(getattr(entry, "id", "") or ""),
        "title": str(getattr(entry, "title", "") or ""),
        "contentSource": str(getattr(entry, "content_source", "image") or "image"),
        "filepath": str(getattr(entry, "filepath", "") or ""),
        "shapeKind": str(getattr(entry, "shape_kind", "circle") or "circle"),
        "shapeSides": int(getattr(entry, "shape_sides", 6) or 6),
        "pathPointsJson": str(getattr(entry, "path_points_json", "") or ""),
        "drawMode": str(getattr(entry, "draw_mode", "stamp") or "stamp"),
        "brushSizeMm": round(float(getattr(entry, "brush_size_mm", 10.0) or 10.0), 4),
        "aspectRatio": round(float(getattr(entry, "aspect_ratio", 1.0) or 1.0), 4),
        "imageAngleDeg": round(float(getattr(entry, "image_angle_deg", 0.0) or 0.0), 4),
        "spacingPercent": round(float(getattr(entry, "spacing_percent", 100.0) or 100.0), 4),
        "stampAngleMode": str(getattr(entry, "stamp_angle_mode", "line") or "line"),
        "stampAngleObjectName": str(getattr(entry, "stamp_angle_object_name", "") or ""),
        "ribbonRepeatMode": str(getattr(entry, "ribbon_repeat_mode", "repeat") or "repeat"),
        "color": _color_to_hex((*color, 1.0)),
        "colorAlpha": round(float(getattr(entry, "color", (1.0, 1.0, 1.0, 1.0))[3]), 4),
        "inoutSizeEnabled": bool(getattr(entry, "inout_size_enabled", False)),
        "inoutOpacityEnabled": bool(getattr(entry, "inout_opacity_enabled", False)),
        "inoutColorEnabled": bool(getattr(entry, "inout_color_enabled", False)),
        "inPercent": round(float(getattr(entry, "in_percent", 100.0) or 100.0), 4),
        "outPercent": round(float(getattr(entry, "out_percent", 100.0) or 100.0), 4),
        "inStartPercent": round(float(getattr(entry, "in_start_percent", 0.0) or 0.0), 4),
        "outStartPercent": round(float(getattr(entry, "out_start_percent", 0.0) or 0.0), 4),
        "inEasingCurve": str(getattr(entry, "in_easing_curve", "") or ""),
        "outEasingCurve": str(getattr(entry, "out_easing_curve", "") or ""),
        "inoutStartColor": _color_to_hex((*inout_start, 1.0)),
        "inoutStartColorAlpha": round(float(getattr(entry, "inout_start_color", (1.0, 1.0, 1.0, 1.0))[3]), 4),
        "inoutEndColor": _color_to_hex((*inout_end, 1.0)),
        "inoutEndColorAlpha": round(float(getattr(entry, "inout_end_color", (1.0, 1.0, 1.0, 1.0))[3]), 4),
        "visible": bool(getattr(entry, "visible", True)),
        "locked": bool(getattr(entry, "locked", False)),
        "opacity": _opacity_to_data(getattr(entry, "opacity", 100.0)),
        "opacityUnit": "percent",
        "parentKind": str(getattr(entry, "parent_kind", "page") or "page"),
        "parentKey": str(getattr(entry, "parent_key", "") or ""),
        "folderKey": str(getattr(entry, "folder_key", "") or ""),
    }


def image_path_layer_from_dict(entry, data: dict[str, Any], *, opacity_percent: bool = False) -> None:
    data = data or {}
    entry.id = str(data.get("id", "") or "")
    entry.title = str(data.get("title", "") or "")
    if hasattr(entry, "content_source"):
        entry.content_source = str(data.get("contentSource", data.get("content_source", "image")) or "image")
    entry.filepath = str(data.get("filepath", data.get("imagePath", "")) or "")
    if hasattr(entry, "shape_kind"):
        entry.shape_kind = str(data.get("shapeKind", data.get("shape_kind", "circle")) or "circle")
    if hasattr(entry, "shape_sides"):
        entry.shape_sides = int(data.get("shapeSides", data.get("shape_sides", 6)) or 6)
    entry.path_points_json = str(data.get("pathPointsJson", data.get("path_points_json", "")) or "")
    entry.parent_kind = str(data.get("parentKind", data.get("parent_kind", "page")) or "page")
    entry.parent_key = str(data.get("parentKey", data.get("parent_key", "")) or "")
    if hasattr(entry, "folder_key"):
        entry.folder_key = str(data.get("folderKey", data.get("folder_key", "")) or "")
    entry.draw_mode = str(data.get("drawMode", data.get("draw_mode", "stamp")) or "stamp")
    entry.brush_size_mm = float(data.get("brushSizeMm", data.get("brush_size_mm", 10.0)) or 10.0)
    entry.aspect_ratio = float(data.get("aspectRatio", data.get("aspect_ratio", 1.0)) or 1.0)
    entry.image_angle_deg = float(data.get("imageAngleDeg", data.get("image_angle_deg", 0.0)) or 0.0)
    entry.spacing_percent = float(data.get("spacingPercent", data.get("spacing_percent", 100.0)) or 100.0)
    entry.stamp_angle_mode = str(
        data.get("stampAngleMode", data.get("stamp_angle_mode", "line")) or "line"
    )
    entry.stamp_angle_object_name = str(
        data.get("stampAngleObjectName", data.get("stamp_angle_object_name", "")) or ""
    )
    entry.ribbon_repeat_mode = str(
        data.get("ribbonRepeatMode", data.get("ribbon_repeat_mode", "repeat")) or "repeat"
    )
    if hasattr(entry, "color"):
        alpha = float(data.get("colorAlpha", data.get("color_alpha", 1.0)) or 1.0)
        rgba = _hex_to_rgba(str(data.get("color", "#FFFFFF")), alpha)
        entry.color = (*color_space.srgb_to_linear_rgb(rgba[:3]), rgba[3])
    for attr, key, default in (
        ("inout_size_enabled", "inoutSizeEnabled", False),
        ("inout_opacity_enabled", "inoutOpacityEnabled", False),
        ("inout_color_enabled", "inoutColorEnabled", False),
    ):
        if hasattr(entry, attr):
            setattr(entry, attr, bool(data.get(key, data.get(attr, default))))
    for attr, key, default in (
        ("in_percent", "inPercent", 100.0),
        ("out_percent", "outPercent", 100.0),
        ("in_start_percent", "inStartPercent", 0.0),
        ("out_start_percent", "outStartPercent", 0.0),
    ):
        if hasattr(entry, attr):
            setattr(entry, attr, float(data.get(key, data.get(attr, default)) or default))
    for attr, key in (("in_easing_curve", "inEasingCurve"), ("out_easing_curve", "outEasingCurve")):
        if hasattr(entry, attr):
            setattr(entry, attr, str(data.get(key, data.get(attr, "")) or ""))
    if hasattr(entry, "inout_start_color"):
        alpha = float(data.get("inoutStartColorAlpha", data.get("inout_start_color_alpha", 1.0)) or 1.0)
        rgba = _hex_to_rgba(str(data.get("inoutStartColor", "#FFFFFF")), alpha)
        entry.inout_start_color = (*color_space.srgb_to_linear_rgb(rgba[:3]), rgba[3])
    if hasattr(entry, "inout_end_color"):
        alpha = float(data.get("inoutEndColorAlpha", data.get("inout_end_color_alpha", 1.0)) or 1.0)
        rgba = _hex_to_rgba(str(data.get("inoutEndColor", "#FFFFFF")), alpha)
        entry.inout_end_color = (*color_space.srgb_to_linear_rgb(rgba[:3]), rgba[3])
    entry.visible = bool(data.get("visible", True))
    entry.locked = bool(data.get("locked", False))
    entry.opacity = _opacity_from_data(data, "opacity", 100.0, percent_schema=opacity_percent)


def _color_to_hex(rgba: tuple[float, float, float, float]) -> str:
    r, g, b = rgba[0], rgba[1], rgba[2]
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def _hex_to_rgba(code: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    code = code.strip()
    if code.startswith("#"):
        code = code[1:]
    if len(code) == 6:
        r = int(code[0:2], 16) / 255.0
        g = int(code[2:4], 16) / 255.0
        b = int(code[4:6], 16) / 255.0
        return (r, g, b, alpha)
    if len(code) == 8:
        r = int(code[0:2], 16) / 255.0
        g = int(code[2:4], 16) / 255.0
        b = int(code[4:6], 16) / 255.0
        a = int(code[6:8], 16) / 255.0
        return (r, g, b, a)
    raise ValueError(f"invalid color hex: {code}")


def _opacity_to_data(value: object, default: float = 100.0) -> float:
    return round(percentage.clamp_percent(value, default), 4)


def _opacity_from_data(
    data: dict[str, Any],
    key: str,
    default: float = 100.0,
    *,
    percent_schema: bool = False,
) -> float:
    if key not in data:
        return percentage.clamp_percent(default)
    if percent_schema or str(data.get("opacityUnit", "") or "").lower() == "percent":
        return percentage.clamp_percent(data.get(key), default)
    return percentage.legacy_factor_to_percent(data.get(key), default)
