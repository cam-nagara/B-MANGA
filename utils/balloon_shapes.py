"""Meldex-compatible balloon/card shape outlines."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .geom import Rect

MELDEX_CARD_SHAPES = ("rect", "ellipse", "cloud", "fluffy", "thorn", "thorn-curve", "octagon")
DYNAMIC_MELDEX_SHAPES = ("cloud", "fluffy", "thorn", "thorn-curve")

_LEGACY_SHAPE_ALIASES = {
    "polygon": "octagon",
    "pill": "ellipse",
    "hexagon": "octagon",
    "diamond": "octagon",
    "star": "thorn",
    "spike_straight": "thorn",
    "spike_curve": "thorn-curve",
    "thorn_curve": "thorn-curve",
    "uni_flash": "ellipse",
}


@dataclass(frozen=True)
class BezierAnchor:
    co: tuple[float, float]
    handle_left: tuple[float, float] | None = None
    handle_right: tuple[float, float] | None = None
    handle_left_type: str = "FREE"
    handle_right_type: str = "FREE"


def normalize_shape(shape: str | None) -> str:
    value = str(shape or "rect")
    return _LEGACY_SHAPE_ALIASES.get(value, value)


def is_dynamic_meldex_shape(shape: str | None) -> bool:
    return normalize_shape(shape) in DYNAMIC_MELDEX_SHAPES


def outline_for_entry(entry, rect: Rect) -> list[tuple[float, float]]:
    sp = getattr(entry, "shape_params", None)
    shape = normalize_shape(getattr(entry, "shape", "rect"))
    if shape == "custom":
        custom = _custom_outline_for_entry(entry, rect)
        if custom is not None:
            return custom
    return outline_for_shape(
        shape,
        rect,
        rounded_corner_enabled=bool(getattr(entry, "rounded_corner_enabled", False)),
        rounded_corner_radius_mm=float(getattr(entry, "rounded_corner_radius_mm", 0.0)),
        cloud_bump_width_mm=float(getattr(sp, "cloud_bump_width_mm", 10.0)),
        cloud_bump_width_jitter=float(getattr(sp, "cloud_bump_width_jitter", 0.0)),
        cloud_bump_height_mm=float(getattr(sp, "cloud_bump_height_mm", 4.0)),
        cloud_bump_height_jitter=float(getattr(sp, "cloud_bump_height_jitter", 0.0)),
        cloud_offset=float(getattr(sp, "cloud_offset_percent", 50.0)) / 100.0,
        cloud_sub_width_ratio=float(getattr(sp, "cloud_sub_width_ratio", 0.0)),
        cloud_sub_width_jitter=float(getattr(sp, "cloud_sub_width_jitter", 0.0)),
        cloud_sub_height_ratio=float(getattr(sp, "cloud_sub_height_ratio", 0.0)),
        cloud_sub_height_jitter=float(getattr(sp, "cloud_sub_height_jitter", 0.0)),
        jitter_seed=_entry_jitter_seed(entry, sp),
    )


def outline_with_corners_for_entry(
    entry, rect: Rect
) -> tuple[list[tuple[float, float]], list[int]]:
    """輪郭点列と「鋭角に残す頂点 index」のセットを返す.

    雲・トゲ・トゲ(曲線) のように本来角ばっている頂点を、曲線化したときに
    丸めず鋭角のまま描くために使う。それ以外の形状は corners 空。
    """
    sp = getattr(entry, "shape_params", None)
    shape = normalize_shape(getattr(entry, "shape", "rect"))
    if shape == "custom":
        custom = _custom_outline_for_entry(entry, rect)
        if custom is not None:
            return custom, []
    return outline_with_corners_for_shape(
        shape,
        rect,
        rounded_corner_enabled=bool(getattr(entry, "rounded_corner_enabled", False)),
        rounded_corner_radius_mm=float(getattr(entry, "rounded_corner_radius_mm", 0.0)),
        cloud_bump_width_mm=float(getattr(sp, "cloud_bump_width_mm", 10.0)),
        cloud_bump_width_jitter=float(getattr(sp, "cloud_bump_width_jitter", 0.0)),
        cloud_bump_height_mm=float(getattr(sp, "cloud_bump_height_mm", 4.0)),
        cloud_bump_height_jitter=float(getattr(sp, "cloud_bump_height_jitter", 0.0)),
        cloud_offset=float(getattr(sp, "cloud_offset_percent", 50.0)) / 100.0,
        cloud_sub_width_ratio=float(getattr(sp, "cloud_sub_width_ratio", 0.0)),
        cloud_sub_width_jitter=float(getattr(sp, "cloud_sub_width_jitter", 0.0)),
        cloud_sub_height_ratio=float(getattr(sp, "cloud_sub_height_ratio", 0.0)),
        cloud_sub_height_jitter=float(getattr(sp, "cloud_sub_height_jitter", 0.0)),
        jitter_seed=_entry_jitter_seed(entry, sp),
    )


def bezier_loop_for_entry(entry, rect: Rect) -> list[BezierAnchor] | None:
    sp = getattr(entry, "shape_params", None)
    shape = normalize_shape(getattr(entry, "shape", "rect"))
    if shape == "custom":
        return None
    return bezier_loop_for_shape(
        shape,
        rect,
        rounded_corner_enabled=bool(getattr(entry, "rounded_corner_enabled", False)),
        rounded_corner_radius_mm=float(getattr(entry, "rounded_corner_radius_mm", 0.0)),
        cloud_bump_width_mm=float(getattr(sp, "cloud_bump_width_mm", 10.0)),
        cloud_bump_width_jitter=float(getattr(sp, "cloud_bump_width_jitter", 0.0)),
        cloud_bump_height_mm=float(getattr(sp, "cloud_bump_height_mm", 4.0)),
        cloud_bump_height_jitter=float(getattr(sp, "cloud_bump_height_jitter", 0.0)),
        cloud_offset=float(getattr(sp, "cloud_offset_percent", 50.0)) / 100.0,
        cloud_sub_width_ratio=float(getattr(sp, "cloud_sub_width_ratio", 0.0)),
        cloud_sub_width_jitter=float(getattr(sp, "cloud_sub_width_jitter", 0.0)),
        cloud_sub_height_ratio=float(getattr(sp, "cloud_sub_height_ratio", 0.0)),
        cloud_sub_height_jitter=float(getattr(sp, "cloud_sub_height_jitter", 0.0)),
        jitter_seed=_entry_jitter_seed(entry, sp),
    )


def _custom_outline_for_entry(entry, rect: Rect) -> list[tuple[float, float]] | None:
    preset_name = str(getattr(entry, "custom_preset_name", "") or "").strip()
    if not preset_name:
        return None
    preset = _find_custom_preset(preset_name)
    if preset is None:
        return None
    vertices = preset.data.get("vertices", [])
    pts: list[tuple[float, float]] = []
    for item in vertices:
        try:
            pts.append((float(item[0]), float(item[1])))
        except Exception:  # noqa: BLE001
            continue
    if len(pts) < 3:
        return None
    min_x = min(x for x, _y in pts)
    max_x = max(x for x, _y in pts)
    min_y = min(y for _x, y in pts)
    max_y = max(y for _x, y in pts)
    src_w = max_x - min_x
    src_h = max_y - min_y
    if src_w <= 1.0e-6 or src_h <= 1.0e-6:
        return None
    return [
        (
            rect.x + ((x - min_x) / src_w) * rect.width,
            rect.y + ((y - min_y) / src_h) * rect.height,
        )
        for x, y in pts
    ]


def _find_custom_preset(preset_name: str):
    try:
        from ..io import balloon_presets

        work_dir = _active_work_dir()
        for preset in balloon_presets.list_all_presets(work_dir):
            if preset.name == preset_name or Path(preset.path).stem == preset_name:
                return preset
    except Exception:  # noqa: BLE001
        return None
    return None


def _active_work_dir() -> Path | None:
    try:
        import bpy
        from ..core.work import get_work

        work = get_work(bpy.context)
        path = str(getattr(work, "work_dir", "") or "") if work is not None else ""
        return Path(path) if path else None
    except Exception:  # noqa: BLE001
        return None


def outline_for_shape(
    shape: str | None,
    rect: Rect,
    *,
    rounded_corner_enabled: bool = False,
    rounded_corner_radius_mm: float = 0.0,
    cloud_bump_width_mm: float = 10.0,
    cloud_bump_width_jitter: float = 0.0,
    cloud_bump_height_mm: float = 4.0,
    cloud_bump_height_jitter: float = 0.0,
    cloud_offset: float = 0.5,
    cloud_sub_width_ratio: float = 0.0,
    cloud_sub_width_jitter: float = 0.0,
    cloud_sub_height_ratio: float = 0.0,
    cloud_sub_height_jitter: float = 0.0,
    jitter_seed: int = 0,
) -> list[tuple[float, float]]:
    return outline_with_corners_for_shape(
        shape,
        rect,
        rounded_corner_enabled=rounded_corner_enabled,
        rounded_corner_radius_mm=rounded_corner_radius_mm,
        cloud_bump_width_mm=cloud_bump_width_mm,
        cloud_bump_width_jitter=cloud_bump_width_jitter,
        cloud_bump_height_mm=cloud_bump_height_mm,
        cloud_bump_height_jitter=cloud_bump_height_jitter,
        cloud_offset=cloud_offset,
        cloud_sub_width_ratio=cloud_sub_width_ratio,
        cloud_sub_width_jitter=cloud_sub_width_jitter,
        cloud_sub_height_ratio=cloud_sub_height_ratio,
        cloud_sub_height_jitter=cloud_sub_height_jitter,
        jitter_seed=jitter_seed,
    )[0]


def outline_with_corners_for_shape(
    shape: str | None,
    rect: Rect,
    *,
    rounded_corner_enabled: bool = False,
    rounded_corner_radius_mm: float = 0.0,
    cloud_bump_width_mm: float = 10.0,
    cloud_bump_width_jitter: float = 0.0,
    cloud_bump_height_mm: float = 4.0,
    cloud_bump_height_jitter: float = 0.0,
    cloud_offset: float = 0.5,
    cloud_sub_width_ratio: float = 0.0,
    cloud_sub_width_jitter: float = 0.0,
    cloud_sub_height_ratio: float = 0.0,
    cloud_sub_height_jitter: float = 0.0,
    jitter_seed: int = 0,
) -> tuple[list[tuple[float, float]], list[int]]:
    s = normalize_shape(shape)
    opts = _DynamicOpts(
        bump_w=max(2.0, float(cloud_bump_width_mm)),
        bump_w_jitter=_clamp01(float(cloud_bump_width_jitter)),
        bump_h=max(0.5, float(cloud_bump_height_mm)),
        bump_h_jitter=_clamp01(float(cloud_bump_height_jitter)),
        offset=max(0.0, min(1.0, float(cloud_offset))),
        sub_w=max(0.0, min(100.0, float(cloud_sub_width_ratio))),
        sub_w_jitter=_clamp01(float(cloud_sub_width_jitter)),
        sub_h=max(0.0, min(100.0, float(cloud_sub_height_ratio))),
        sub_h_jitter=_clamp01(float(cloud_sub_height_jitter)),
        rng=random.Random(int(jitter_seed) & 0xFFFFFFFF),
    )
    if s == "rect":
        if rounded_corner_enabled and rounded_corner_radius_mm > 0.0:
            return _outline_rounded_rect(rect, rounded_corner_radius_mm), []
        return _outline_rect(rect), [0, 1, 2, 3]
    if s == "ellipse":
        return _outline_ellipse(rect), []
    if s == "cloud":
        return _outline_cloud_with_corners(rect, opts)
    if s == "fluffy":
        return _outline_fluffy(rect, opts), []
    if s == "thorn":
        return _outline_thorn_with_corners(rect, opts)
    if s == "thorn-curve":
        return _outline_thorn_curve_with_corners(rect, opts)
    if s == "octagon":
        return _outline_octagon(rect), list(range(8))
    # Legacy B-Name shapes kept for existing files.
    if s == "pill":
        return _outline_pill(rect), []
    if s == "diamond":
        return _outline_diamond(rect), [0, 1, 2, 3]
    if s == "hexagon":
        return _outline_hexagon(rect), list(range(6))
    if s == "star":
        return _outline_star(rect), list(range(10))
    return _outline_rect(rect), [0, 1, 2, 3]


def bezier_loop_for_shape(
    shape: str | None,
    rect: Rect,
    *,
    rounded_corner_enabled: bool = False,
    rounded_corner_radius_mm: float = 0.0,
    cloud_bump_width_mm: float = 10.0,
    cloud_bump_width_jitter: float = 0.0,
    cloud_bump_height_mm: float = 4.0,
    cloud_bump_height_jitter: float = 0.0,
    cloud_offset: float = 0.5,
    cloud_sub_width_ratio: float = 0.0,
    cloud_sub_width_jitter: float = 0.0,
    cloud_sub_height_ratio: float = 0.0,
    cloud_sub_height_jitter: float = 0.0,
    jitter_seed: int = 0,
) -> list[BezierAnchor] | None:
    s = normalize_shape(shape)
    opts = _DynamicOpts(
        bump_w=max(2.0, float(cloud_bump_width_mm)),
        bump_w_jitter=_clamp01(float(cloud_bump_width_jitter)),
        bump_h=max(0.5, float(cloud_bump_height_mm)),
        bump_h_jitter=_clamp01(float(cloud_bump_height_jitter)),
        offset=max(0.0, min(1.0, float(cloud_offset))),
        sub_w=max(0.0, min(100.0, float(cloud_sub_width_ratio))),
        sub_w_jitter=_clamp01(float(cloud_sub_width_jitter)),
        sub_h=max(0.0, min(100.0, float(cloud_sub_height_ratio))),
        sub_h_jitter=_clamp01(float(cloud_sub_height_jitter)),
        rng=random.Random(int(jitter_seed) & 0xFFFFFFFF),
    )
    if s == "ellipse":
        return _bezier_ellipse(rect)
    if s == "rect" and rounded_corner_enabled and rounded_corner_radius_mm > 0.0:
        return _bezier_rounded_rect(rect, rounded_corner_radius_mm)
    if s == "cloud":
        return _bezier_cloud(rect, opts)
    if s == "fluffy":
        return _bezier_fluffy(rect, opts)
    if s == "thorn-curve":
        return _bezier_thorn_curve(rect, opts)
    if s == "pill":
        return _bezier_pill(rect)
    return None


class _DynamicOpts:
    def __init__(
        self,
        *,
        bump_w: float,
        bump_w_jitter: float,
        bump_h: float,
        bump_h_jitter: float,
        offset: float,
        sub_w: float,
        sub_w_jitter: float,
        sub_h: float,
        sub_h_jitter: float,
        rng: random.Random,
    ) -> None:
        self.bump_w = bump_w
        self.bump_w_jitter = bump_w_jitter
        self.bump_h = bump_h
        self.bump_h_jitter = bump_h_jitter
        self.offset = offset
        self.sub_w = sub_w
        self.sub_w_jitter = sub_w_jitter
        self.sub_h = sub_h
        self.sub_h_jitter = sub_h_jitter
        self.rng = rng


def _stable_seed(value: str) -> int:
    seed = 2166136261
    for char in str(value or ""):
        seed ^= ord(char)
        seed = (seed * 16777619) & 0xFFFFFFFF
    return seed


def _entry_jitter_seed(entry, shape_params) -> int:
    base = _stable_seed(str(getattr(entry, "id", "") or getattr(entry, "shape", "") or ""))
    seed = int(getattr(shape_params, "shape_seed", 0) or 0) & 0xFFFFFFFF
    return (base ^ ((seed + 0x9E3779B9) * 0x85EBCA6B)) & 0xFFFFFFFF


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _jitter_factor(amount: float, rng: random.Random, *, min_factor: float = 0.15) -> float:
    amount = _clamp01(amount)
    if amount <= 0.0:
        return 1.0
    return max(float(min_factor), 1.0 + (rng.random() * 2.0 - 1.0) * amount)


def _height_factor_for_width(width_factor: float) -> float:
    return max(0.35, min(1.8, 1.0 + (float(width_factor) - 1.0) * 0.35))


def _outline_rect(rect: Rect) -> list[tuple[float, float]]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _outline_rounded_rect(rect: Rect, radius_mm: float, segments: int = 24) -> list[tuple[float, float]]:
    radius = max(0.0, min(float(radius_mm), rect.width * 0.5, rect.height * 0.5))
    if radius <= 0.0:
        return _outline_rect(rect)
    corners = (
        (rect.x2 - radius, rect.y2 - radius, 0.0),
        (rect.x + radius, rect.y2 - radius, math.pi * 0.5),
        (rect.x + radius, rect.y + radius, math.pi),
        (rect.x2 - radius, rect.y + radius, math.pi * 1.5),
    )
    pts: list[tuple[float, float]] = []
    for cx, cy, start in corners:
        for step in range(segments + 1):
            angle = start + (math.pi * 0.5) * (step / segments)
            pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _outline_ellipse(rect: Rect, segments: int = 64) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    return [
        (cx + rx * math.cos(2.0 * math.pi * i / segments), cy + ry * math.sin(2.0 * math.pi * i / segments))
        for i in range(segments)
    ]


def _bezier_ellipse(rect: Rect) -> list[BezierAnchor]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    k = 0.5522847498307936
    return [
        BezierAnchor((cx + rx, cy), (cx + rx, cy - k * ry), (cx + rx, cy + k * ry)),
        BezierAnchor((cx, cy + ry), (cx + k * rx, cy + ry), (cx - k * rx, cy + ry)),
        BezierAnchor((cx - rx, cy), (cx - rx, cy + k * ry), (cx - rx, cy - k * ry)),
        BezierAnchor((cx, cy - ry), (cx - k * rx, cy - ry), (cx + k * rx, cy - ry)),
    ]


def _bezier_rounded_rect(rect: Rect, radius_mm: float) -> list[BezierAnchor] | None:
    radius = max(0.0, min(float(radius_mm), rect.width * 0.5, rect.height * 0.5))
    if radius <= 0.0:
        return None
    k = 0.5522847498307936 * radius
    x0, x1, x2, x3 = rect.x, rect.x + radius, rect.x2 - radius, rect.x2
    y0, y1, y2, y3 = rect.y, rect.y + radius, rect.y2 - radius, rect.y2
    return [
        BezierAnchor((x2, y3), (x2 - k, y3), (x2 + k, y3)),
        BezierAnchor((x3, y2), (x3, y2 + k), (x3, y2 - k)),
        BezierAnchor((x3, y1), (x3, y1 + k), (x3, y1 - k)),
        BezierAnchor((x2, y0), (x2 + k, y0), (x2 - k, y0)),
        BezierAnchor((x1, y0), (x1 + k, y0), (x1 - k, y0)),
        BezierAnchor((x0, y1), (x0, y1 - k), (x0, y1 + k)),
        BezierAnchor((x0, y2), (x0, y2 - k), (x0, y2 + k)),
        BezierAnchor((x1, y3), (x1 - k, y3), (x1 + k, y3)),
    ]


def _bezier_pill(rect: Rect) -> list[BezierAnchor]:
    radius = min(rect.width, rect.height) * 0.5
    if radius <= 0.0:
        return []
    return _bezier_rounded_rect(rect, radius) or []


def _outline_polygon_pct(rect: Rect, pct_pts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(rect.x + rect.width * (px / 100.0), rect.y + rect.height * ((100.0 - py) / 100.0)) for px, py in pct_pts]


def _outline_octagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(12, 0), (88, 0), (100, 12), (100, 88), (88, 100), (12, 100), (0, 88), (0, 12)])


def _outline_pill(rect: Rect, segments: int = 16) -> list[tuple[float, float]]:
    radius = min(rect.width, rect.height) * 0.5
    if radius <= 0.0:
        return _outline_rect(rect)
    cx_left = rect.x + radius
    cx_right = rect.x2 - radius
    cy = (rect.y + rect.y2) * 0.5
    pts: list[tuple[float, float]] = []
    for step in range(segments + 1):
        angle = -math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_right + radius * math.cos(angle), cy + radius * math.sin(angle)))
    for step in range(segments + 1):
        angle = math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_left + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _outline_diamond(rect: Rect) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    return [(cx, rect.y2), (rect.x2, cy), (cx, rect.y), (rect.x, cy)]


def _outline_hexagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(25, 0), (75, 0), (100, 50), (75, 100), (25, 100), (0, 50)])


def _outline_star(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(
        rect,
        [(50, 0), (61, 35), (98, 35), (68, 57), (79, 91), (50, 70), (21, 91), (32, 57), (2, 35), (39, 35)],
    )


def _local_to_rect(rect: Rect, pts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(rect.x + x, rect.y2 - y) for x, y in pts]


def _local_point_to_rect(rect: Rect, point: tuple[float, float]) -> tuple[float, float]:
    return (rect.x + point[0], rect.y2 - point[1])


def _local_anchor_to_rect(rect: Rect, anchor: BezierAnchor) -> BezierAnchor:
    return BezierAnchor(
        _local_point_to_rect(rect, anchor.co),
        _local_point_to_rect(rect, anchor.handle_left or anchor.co),
        _local_point_to_rect(rect, anchor.handle_right or anchor.co),
        anchor.handle_left_type,
        anchor.handle_right_type,
    )


def _ellipse_perimeter(rx: float, ry: float) -> float:
    if rx <= 0.0 or ry <= 0.0:
        return 0.0
    h_val = ((rx - ry) / (rx + ry)) ** 2
    return math.pi * (rx + ry) * (1.0 + (3.0 * h_val) / (10.0 + math.sqrt(max(0.0, 4.0 - 3.0 * h_val))))


def _dynamic_base(width: float, height: float, opts: _DynamicOpts, *, fluffy: bool = False):
    if not (width > 4.0 and height > 4.0):
        return None
    cx = width * 0.5
    cy = height * 0.5
    eff_h = float(opts.bump_h)
    if eff_h < 0.5:
        return None
    base_margin = min(max(1.0, min(width, height) * (0.05 if fluffy else 0.08)), max(1.0, min(cx, cy) - 1.0))
    rx = cx - base_margin
    ry = cy - base_margin
    if rx <= 1.0 or ry <= 1.0:
        return None
    return cx, cy, rx, ry, eff_h


def _bump_sequence(rx: float, ry: float, opts: _DynamicOpts, *, min_slots: int):
    perimeter = _ellipse_perimeter(rx, ry)
    sub_enabled = opts.sub_w > 0.0 or opts.sub_h > 0.0
    sub_w_ratio = (opts.sub_w if opts.sub_w > 0.0 else 50.0) / 100.0
    sub_h_ratio = (opts.sub_h if opts.sub_h > 0.0 else 50.0) / 100.0
    slot_width = opts.bump_w * (1.0 + sub_w_ratio) if sub_enabled else opts.bump_w
    slots = max(3 if sub_enabled else min_slots, round(perimeter / max(0.001, slot_width)))
    bumps = slots * 2 if sub_enabled else slots
    period = (2.0 * math.pi) / slots
    main_angle = period / (1.0 + sub_w_ratio) if sub_enabled else period
    sub_angle = main_angle * sub_w_ratio if sub_enabled else 0.0
    base_angle = -math.pi * 0.5 + opts.offset * period
    return sub_enabled, sub_h_ratio, bumps, main_angle, sub_angle, base_angle


def _bump_segments(rx: float, ry: float, opts: _DynamicOpts, *, min_slots: int):
    sub_enabled, sub_h_ratio, bumps, main_angle, sub_angle, base_angle = _bump_sequence(
        rx,
        ry,
        opts,
        min_slots=min_slots,
    )
    segments: list[tuple[bool, float, float]] = []
    total_span = 0.0
    for i in range(bumps):
        is_sub = sub_enabled and (i % 2 == 1)
        base_span = sub_angle if is_sub else main_angle
        width_jitter = opts.sub_w_jitter if is_sub else opts.bump_w_jitter
        height_jitter = opts.sub_h_jitter if is_sub else opts.bump_h_jitter
        width_factor = _jitter_factor(width_jitter, opts.rng)
        span = max(0.001, base_span * width_factor)
        h_base = sub_h_ratio if is_sub else 1.0
        h_mul = h_base * _jitter_factor(height_jitter, opts.rng) * _height_factor_for_width(width_factor)
        segments.append((is_sub, span, h_mul))
        total_span += span
    if total_span <= 1.0e-9:
        return base_angle, []
    scale = (2.0 * math.pi) / total_span
    return base_angle, [(is_sub, span * scale, h_mul) for is_sub, span, h_mul in segments]


def _sample_cubic(
    p0: tuple[float, float],
    c1: tuple[float, float],
    c2: tuple[float, float],
    p1: tuple[float, float],
    *,
    steps: int = 12,
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for step in range(1, steps + 1):
        t = step / steps
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3.0 * mt**2 * t * c1[0] + 3.0 * mt * t**2 * c2[0] + t**3 * p1[0]
        y = mt**3 * p0[1] + 3.0 * mt**2 * t * c1[1] + 3.0 * mt * t**2 * c2[1] + t**3 * p1[1]
        out.append((x, y))
    return out


def _outline_cloud(rect: Rect, opts: _DynamicOpts) -> list[tuple[float, float]]:
    return _outline_cloud_with_corners(rect, opts)[0]


def _outline_cloud_with_corners(
    rect: Rect, opts: _DynamicOpts
) -> tuple[list[tuple[float, float]], list[int]]:
    base = _dynamic_base(rect.width, rect.height, opts)
    if base is None:
        return _outline_ellipse(rect), []
    cx, cy, rx, ry, eff_h = base
    angle, segments = _bump_segments(rx, ry, opts, min_slots=6)
    if not segments:
        return _outline_ellipse(rect), []

    def valley_point(t: float) -> tuple[float, float]:
        # 谷を基準楕円より内側へ少し入れ、丸いこぶ同士の境目を
        # 見た目にも鋭いV字にする。
        cos_t = math.cos(t)
        sin_t = math.sin(t)
        notch = min(max(0.2, min(rect.width, rect.height) * 0.02), max(0.0, min(rx, ry) - 0.1))
        return (cx + (rx - notch) * cos_t, cy + (ry - notch) * sin_t)

    pts = [valley_point(angle)]
    corners: list[int] = []
    for _is_sub, bump_angle, h_mul in segments:
        start_angle = angle
        end_angle = angle + bump_angle
        angle = end_angle
        v_start = valley_point(start_angle)
        v_end = valley_point(end_angle)
        mx = (v_start[0] + v_end[0]) * 0.5
        my = (v_start[1] + v_end[1]) * 0.5
        chord_x = v_end[0] - v_start[0]
        chord_y = v_end[1] - v_start[1]
        chord_len = math.hypot(chord_x, chord_y)
        if chord_len < 0.001:
            continue
        perp_x = -chord_y / chord_len
        perp_y = chord_x / chord_len
        if perp_x * (mx - cx) + perp_y * (my - cy) < 0.0:
            perp_x = -perp_x
            perp_y = -perp_y
        m_len = (4.0 / 3.0) * eff_h * h_mul
        off_x = m_len * perp_x
        off_y = m_len * perp_y
        pts.extend(_sample_cubic(v_start, (v_start[0] + off_x, v_start[1] + off_y), (v_end[0] + off_x, v_end[1] + off_y), v_end))
    return _local_to_rect(rect, pts), corners


def _bezier_cloud(rect: Rect, opts: _DynamicOpts) -> list[BezierAnchor] | None:
    base = _dynamic_base(rect.width, rect.height, opts)
    if base is None:
        return _bezier_ellipse(rect)
    cx, cy, rx, ry, eff_h = base
    angle, segments = _bump_segments(rx, ry, opts, min_slots=6)
    if not segments:
        return _bezier_ellipse(rect)

    def valley_point(t: float) -> tuple[float, float]:
        notch = min(max(0.2, min(rect.width, rect.height) * 0.02), max(0.0, min(rx, ry) - 0.1))
        return (cx + (rx - notch) * math.cos(t), cy + (ry - notch) * math.sin(t))

    cubics: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    for _is_sub, bump_angle, h_mul in segments:
        start_angle = angle
        end_angle = angle + bump_angle
        angle = end_angle
        v_start = valley_point(start_angle)
        v_end = valley_point(end_angle)
        mx = (v_start[0] + v_end[0]) * 0.5
        my = (v_start[1] + v_end[1]) * 0.5
        chord_x = v_end[0] - v_start[0]
        chord_y = v_end[1] - v_start[1]
        chord_len = math.hypot(chord_x, chord_y)
        if chord_len < 0.001:
            continue
        perp_x = -chord_y / chord_len
        perp_y = chord_x / chord_len
        if perp_x * (mx - cx) + perp_y * (my - cy) < 0.0:
            perp_x = -perp_x
            perp_y = -perp_y
        m_len = (4.0 / 3.0) * eff_h * h_mul
        c1 = (v_start[0] + m_len * perp_x, v_start[1] + m_len * perp_y)
        c2 = (v_end[0] + m_len * perp_x, v_end[1] + m_len * perp_y)
        cubics.append((v_start, c1, c2))
    if len(cubics) < 3:
        return _bezier_ellipse(rect)
    anchors: list[BezierAnchor] = []
    for i, (co, c1, _c2) in enumerate(cubics):
        incoming_c2 = cubics[(i - 1) % len(cubics)][2]
        anchors.append(_local_anchor_to_rect(rect, BezierAnchor(co, incoming_c2, c1)))
    return anchors


def _outline_thorn(rect: Rect, opts: _DynamicOpts) -> list[tuple[float, float]]:
    return _outline_thorn_with_corners(rect, opts)[0]


def _outline_thorn_with_corners(
    rect: Rect, opts: _DynamicOpts
) -> tuple[list[tuple[float, float]], list[int]]:
    base = _dynamic_base(rect.width, rect.height, opts)
    if base is None:
        return _outline_ellipse(rect), []
    cx, cy, rx, ry, eff_h = base
    angle, segments = _bump_segments(rx, ry, opts, min_slots=6)
    if not segments:
        return _outline_ellipse(rect), []

    def ellipse_point(t: float) -> tuple[float, float]:
        return (cx + rx * math.cos(t), cy + ry * math.sin(t))

    def peak_at(t: float, h_mul: float) -> tuple[float, float]:
        return (cx + (rx + eff_h * h_mul) * math.cos(t), cy + (ry + eff_h * h_mul) * math.sin(t))

    pts = [ellipse_point(angle)]
    for _is_sub, bump_angle, h_mul in segments:
        mid_angle = angle + bump_angle * 0.5
        angle += bump_angle
        pts.append(peak_at(mid_angle, h_mul))
        pts.append(ellipse_point(angle))
    # トゲは全頂点 (先端・谷) が鋭角の多角形。
    corners = list(range(len(pts)))
    return _local_to_rect(rect, pts), corners


def _outline_thorn_curve(rect: Rect, opts: _DynamicOpts) -> list[tuple[float, float]]:
    return _outline_thorn_curve_with_corners(rect, opts)[0]


def _outline_thorn_curve_with_corners(
    rect: Rect, opts: _DynamicOpts
) -> tuple[list[tuple[float, float]], list[int]]:
    base = _dynamic_base(rect.width, rect.height, opts)
    if base is None:
        return _outline_ellipse(rect), []
    cx, cy, rx, ry, eff_h = base
    angle, segments = _bump_segments(rx, ry, opts, min_slots=6)
    if not segments:
        return _outline_ellipse(rect), []
    tpull = 0.18
    depth_ratio = 1.12

    def peak_at(t: float, h_mul: float) -> tuple[float, float]:
        return (cx + (rx + eff_h * h_mul) * math.cos(t), cy + (ry + eff_h * h_mul) * math.sin(t))

    peaks: list[tuple[float, float]] = []
    for _is_sub, bump_angle, h_mul in segments:
        mid_angle = angle + bump_angle * 0.5
        angle += bump_angle
        peaks.append(peak_at(mid_angle, h_mul))
    if not peaks:
        return _outline_ellipse(rect), []

    pts = [peaks[0]]
    corners: list[int] = []
    for i, p0 in enumerate(peaks):
        p1 = peaks[(i + 1) % len(peaks)]
        mx = (p0[0] + p1[0]) * 0.5
        my = (p0[1] + p1[1]) * 0.5
        dcx = cx - mx
        dcy = cy - my
        length = math.hypot(dcx, dcy)
        in_x = dcx / length if length > 0.001 else 0.0
        in_y = dcy / length if length > 0.001 else 0.0
        depth = min(eff_h * depth_ratio, max(0.3, min(rect.width, rect.height) * 0.08))
        c1 = (p0[0] + (p1[0] - p0[0]) * tpull + in_x * depth, p0[1] + (p1[1] - p0[1]) * tpull + in_y * depth)
        c2 = (p1[0] + (p0[0] - p1[0]) * tpull + in_x * depth, p1[1] + (p0[1] - p1[1]) * tpull + in_y * depth)
        pts.extend(_sample_cubic(p0, c1, c2, p1))
    return _local_to_rect(rect, pts), corners


def _bezier_thorn_curve(rect: Rect, opts: _DynamicOpts) -> list[BezierAnchor] | None:
    base = _dynamic_base(rect.width, rect.height, opts)
    if base is None:
        return _bezier_ellipse(rect)
    cx, cy, rx, ry, eff_h = base
    angle, segments = _bump_segments(rx, ry, opts, min_slots=6)
    if not segments:
        return _bezier_ellipse(rect)
    tpull = 0.18
    depth_ratio = 1.12

    def peak_at(t: float, h_mul: float) -> tuple[float, float]:
        return (cx + (rx + eff_h * h_mul) * math.cos(t), cy + (ry + eff_h * h_mul) * math.sin(t))

    peaks: list[tuple[float, float]] = []
    for _is_sub, bump_angle, h_mul in segments:
        mid_angle = angle + bump_angle * 0.5
        angle += bump_angle
        peaks.append(peak_at(mid_angle, h_mul))
    if len(peaks) < 3:
        return _bezier_ellipse(rect)

    cubics: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    for i, p0 in enumerate(peaks):
        p1 = peaks[(i + 1) % len(peaks)]
        mx = (p0[0] + p1[0]) * 0.5
        my = (p0[1] + p1[1]) * 0.5
        dcx = cx - mx
        dcy = cy - my
        length = math.hypot(dcx, dcy)
        in_x = dcx / length if length > 0.001 else 0.0
        in_y = dcy / length if length > 0.001 else 0.0
        depth = min(eff_h * depth_ratio, max(0.3, min(rect.width, rect.height) * 0.08))
        c1 = (p0[0] + (p1[0] - p0[0]) * tpull + in_x * depth, p0[1] + (p1[1] - p0[1]) * tpull + in_y * depth)
        c2 = (p1[0] + (p0[0] - p1[0]) * tpull + in_x * depth, p1[1] + (p0[1] - p1[1]) * tpull + in_y * depth)
        cubics.append((p0, c1, c2))
    return [
        _local_anchor_to_rect(rect, BezierAnchor(co, cubics[(i - 1) % len(cubics)][2], c1))
        for i, (co, c1, _c2) in enumerate(cubics)
    ]


def _outline_fluffy(rect: Rect, opts: _DynamicOpts) -> list[tuple[float, float]]:
    base = _dynamic_base(rect.width, rect.height, opts, fluffy=True)
    if base is None:
        return _outline_ellipse(rect)
    cx, cy, rx_base, ry_base, eff_h = base
    amp = eff_h * 0.5
    r_min = min(rx_base, ry_base)
    perimeter = _ellipse_perimeter(rx_base, ry_base)
    width_factor = _jitter_factor(opts.bump_w_jitter, opts.rng, min_factor=0.5)
    num_bumps = max(6, round(perimeter / max(0.001, opts.bump_w * width_factor)))
    period = (2.0 * math.pi) / num_bumps
    base_angle = -math.pi * 0.5 + opts.offset * period
    steps = num_bumps * 10
    sub_enabled = opts.sub_w > 0.0 or opts.sub_h > 0.0
    sub_width_factor = _jitter_factor(opts.sub_w_jitter, opts.rng, min_factor=0.5)
    sub_freq = max(1, round(num_bumps * 2.0 / sub_width_factor)) if sub_enabled else 0
    sub_amp_ratio = ((opts.sub_h if opts.sub_h > 0.0 else 50.0) / 100.0) * 0.4 if sub_enabled else 0.0
    main_width = [_jitter_factor(opts.bump_w_jitter, opts.rng, min_factor=0.5) for _i in range(num_bumps)]
    main_height = [
        _jitter_factor(opts.bump_h_jitter, opts.rng, min_factor=0.2) * _height_factor_for_width(main_width[i])
        for i in range(num_bumps)
    ]
    sub_width = [_jitter_factor(opts.sub_w_jitter, opts.rng, min_factor=0.5) for _i in range(max(1, sub_freq))]
    sub_height = [
        _jitter_factor(opts.sub_h_jitter, opts.rng, min_factor=0.2) * _height_factor_for_width(sub_width[i])
        for i in range(max(1, sub_freq))
    ]

    raw: list[tuple[float, float]] = []
    for i in range(steps):
        t = base_angle + (i / steps) * 2.0 * math.pi
        phase = t - base_angle
        main_idx = int(((phase % (2.0 * math.pi)) / (2.0 * math.pi)) * num_bumps) % num_bumps
        wave = math.cos(num_bumps * phase) * main_height[main_idx]
        if sub_freq > 0:
            sub_idx = int(((phase % (2.0 * math.pi)) / (2.0 * math.pi)) * sub_freq) % len(sub_height)
            wave += sub_amp_ratio * math.cos(sub_freq * phase) * sub_height[sub_idx]
        r_mul = 1.0 + (amp / r_min) * wave
        raw.append((cx + rx_base * r_mul * math.cos(t), cy + ry_base * r_mul * math.sin(t)))

    pts: list[tuple[float, float]] = [raw[0]]
    n = len(raw)
    for i in range(n):
        p0 = raw[(i - 1 + n) % n]
        p1 = raw[i]
        p2 = raw[(i + 1) % n]
        p3 = raw[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        pts.extend(_sample_cubic(p1, c1, c2, p2, steps=4))
    return _local_to_rect(rect, pts)


def _bezier_fluffy(rect: Rect, opts: _DynamicOpts) -> list[BezierAnchor] | None:
    base = _dynamic_base(rect.width, rect.height, opts, fluffy=True)
    if base is None:
        return _bezier_ellipse(rect)
    cx, cy, rx_base, ry_base, eff_h = base
    amp = eff_h * 0.5
    r_min = min(rx_base, ry_base)
    perimeter = _ellipse_perimeter(rx_base, ry_base)
    width_factor = _jitter_factor(opts.bump_w_jitter, opts.rng, min_factor=0.5)
    num_bumps = max(6, round(perimeter / max(0.001, opts.bump_w * width_factor)))
    steps = max(8, num_bumps * 2)
    period = (2.0 * math.pi) / num_bumps
    base_angle = -math.pi * 0.5 + opts.offset * period
    main_width = [_jitter_factor(opts.bump_w_jitter, opts.rng, min_factor=0.5) for _i in range(num_bumps)]
    main_height = [
        _jitter_factor(opts.bump_h_jitter, opts.rng, min_factor=0.2) * _height_factor_for_width(main_width[i])
        for i in range(num_bumps)
    ]
    raw: list[tuple[float, float]] = []
    for i in range(steps):
        t = base_angle + (i / steps) * 2.0 * math.pi
        phase = t - base_angle
        main_idx = int(((phase % (2.0 * math.pi)) / (2.0 * math.pi)) * num_bumps) % num_bumps
        wave = math.cos(num_bumps * phase) * main_height[main_idx]
        r_mul = 1.0 + (amp / r_min) * wave
        raw.append((cx + rx_base * r_mul * math.cos(t), cy + ry_base * r_mul * math.sin(t)))
    if len(raw) < 3:
        return _bezier_ellipse(rect)
    anchors: list[BezierAnchor] = []
    n = len(raw)
    for i, co in enumerate(raw):
        prev_pt = raw[(i - 1) % n]
        next_pt = raw[(i + 1) % n]
        tangent = ((next_pt[0] - prev_pt[0]) / 6.0, (next_pt[1] - prev_pt[1]) / 6.0)
        handle_left = (co[0] - tangent[0], co[1] - tangent[1])
        handle_right = (co[0] + tangent[0], co[1] + tangent[1])
        anchors.append(_local_anchor_to_rect(rect, BezierAnchor(co, handle_left, handle_right)))
    return anchors
