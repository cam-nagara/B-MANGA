"""フキダシ描画の共通契約.

フキダシは、塗り、外側フチ、内側フチ、多重線、主線を同じ基準形状
から作る。素材スロット、役割番号、前後関係はこのモジュールだけを
正にして、個別モジュールで重複定義しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import balloon_shapes

MATERIAL_SLOT_FILL = 0
MATERIAL_SLOT_OUTER_EDGE = 1
MATERIAL_SLOT_INNER_EDGE = 2
MATERIAL_SLOT_LINE = 3

MULTI_LINE_ROLE_RADIUS_OFFSET = 100.0
OUTER_EDGE_ROLE_RADIUS = 200.0
INNER_EDGE_ROLE_RADIUS = 300.0
CLIPPED_FILL_ROLE_RADIUS = 400.0
MAIN_LINE_FILL_ROLE_RADIUS = 500.0

LINE_AND_EDGE_MASK_POWER = 4.0

FILL_Z_M = 0.0
OUTER_EDGE_Z_M = 0.000020
INNER_EDGE_Z_M = 0.000040
MULTI_LINE_Z_M = 0.000080
LINE_Z_M = 0.000100


@dataclass(frozen=True)
class BalloonRenderSettings:
    shape_name: str
    line_style: str
    line_width_mm: float
    filled_line_enabled: bool
    multi_line_enabled: bool
    multi_line_count: int
    multi_line_width_mm: float
    multi_line_spacing_mm: float
    multi_line_width_scale_percent: float
    multi_line_spacing_scale_percent: float
    multi_line_direction: str
    thorn_multi_line_valley_width_mm: float
    thorn_multi_line_peak_width_mm: float
    thorn_multi_line_length_scale_percent: float
    thorn_multi_line_cross_enabled: bool
    outer_edge_enabled: bool
    outer_edge_width_mm: float
    inner_edge_enabled: bool
    inner_edge_width_mm: float
    fill_blur_amount: float
    fill_blur_dither: bool

    @property
    def native_multi_line_rings_enabled(self) -> bool:
        return self.shape_name != "thorn"

    def as_modifier_kwargs(self, *, mask_object: Any = None) -> dict[str, Any]:
        return {
            "line_width_mm": self.line_width_mm,
            "filled_line_enabled": self.filled_line_enabled,
            "multi_line_enabled": self.multi_line_enabled,
            "multi_line_count": self.multi_line_count,
            "multi_line_width_mm": self.multi_line_width_mm,
            "multi_line_spacing_mm": self.multi_line_spacing_mm,
            "multi_line_width_scale_percent": self.multi_line_width_scale_percent,
            "multi_line_spacing_scale_percent": self.multi_line_spacing_scale_percent,
            "multi_line_direction": self.multi_line_direction,
            "native_multi_line_rings_enabled": self.native_multi_line_rings_enabled,
            "thorn_multi_line_valley_width_mm": self.thorn_multi_line_valley_width_mm,
            "thorn_multi_line_peak_width_mm": self.thorn_multi_line_peak_width_mm,
            "thorn_multi_line_length_scale_percent": self.thorn_multi_line_length_scale_percent,
            "thorn_multi_line_cross_enabled": self.thorn_multi_line_cross_enabled,
            "outer_edge_enabled": self.outer_edge_enabled,
            "outer_edge_width_mm": self.outer_edge_width_mm,
            "inner_edge_enabled": self.inner_edge_enabled,
            "inner_edge_width_mm": self.inner_edge_width_mm,
            "fill_blur_amount": self.fill_blur_amount,
            "fill_blur_dither": self.fill_blur_dither,
            "mask_object": mask_object,
            "clip_needed": False,
            "fill_clip_needed": False,
        }


def settings_from_entry(entry, *, filled_line_enabled: bool = False) -> BalloonRenderSettings:
    line_style = str(getattr(entry, "line_style", "") or "")
    return BalloonRenderSettings(
        shape_name=balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect")),
        line_style=line_style,
        line_width_mm=0.0 if line_style == "none" else float(getattr(entry, "line_width_mm", 0.3) or 0.3),
        filled_line_enabled=bool(filled_line_enabled),
        multi_line_enabled=line_style == "double",
        multi_line_count=int(getattr(entry, "multi_line_count", 3) or 3),
        multi_line_width_mm=float(getattr(entry, "multi_line_width_mm", 0.3) or 0.0),
        multi_line_spacing_mm=float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0),
        multi_line_width_scale_percent=float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0),
        multi_line_spacing_scale_percent=float(getattr(entry, "multi_line_spacing_scale_percent", 100.0) or 0.0),
        multi_line_direction=str(getattr(entry, "multi_line_direction", "outside") or "outside"),
        thorn_multi_line_valley_width_mm=float(getattr(entry, "thorn_multi_line_valley_width_mm", 0.3) or 0.0),
        thorn_multi_line_peak_width_mm=float(getattr(entry, "thorn_multi_line_peak_width_mm", 0.3) or 0.0),
        thorn_multi_line_length_scale_percent=float(
            getattr(entry, "thorn_multi_line_length_scale_percent", 100.0) or 0.0
        ),
        thorn_multi_line_cross_enabled=bool(getattr(entry, "thorn_multi_line_cross_enabled", False)),
        outer_edge_enabled=bool(getattr(entry, "outer_white_margin_enabled", False)),
        outer_edge_width_mm=float(getattr(entry, "outer_white_margin_width_mm", 1.0) or 0.0),
        inner_edge_enabled=bool(getattr(entry, "inner_white_margin_enabled", False)),
        inner_edge_width_mm=float(getattr(entry, "inner_white_margin_width_mm", 1.0) or 0.0),
        fill_blur_amount=float(getattr(entry, "fill_blur_amount", 0.0) or 0.0),
        fill_blur_dither=bool(getattr(entry, "fill_blur_dither", False)),
    )
