"""Shared viewport overlay colors for B-MANGA."""

from __future__ import annotations

from . import color_space

PAPER_GUIDE = (0.0, 0.82, 1.0, 0.5)
PAPER_GUIDE_LIGHT = (0.0, 0.92, 1.0, 0.5)
PAPER_GUIDE_DIM = (0.0, 0.72, 1.0, 0.5)

SAFE_LINE = (0.25, 1.0, 0.35, 0.5)

BLENDER_BACKGROUND_DEFAULT_SRGB = (0x40 / 255.0, 0x40 / 255.0, 0x40 / 255.0)
BLENDER_BACKGROUND_DEFAULT_LINEAR = color_space.srgb_to_linear_rgb(
    BLENDER_BACKGROUND_DEFAULT_SRGB
)

SELECTION = (0.95, 0.0, 0.62, 0.95)
SELECTION_STRONG = (1.0, 0.0, 0.68, 1.0)
SELECTION_FILL = (1.0, 0.0, 0.68, 0.32)
HANDLE_FILL = (1.0, 0.0, 0.68, 0.95)
HANDLE_OUTLINE = (0.95, 0.0, 0.62, 1.0)
