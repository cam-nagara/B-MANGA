"""Shared text layout bounds for editor preview and rendered text."""

from __future__ import annotations

from .geom import Rect

TEXT_CONTENT_PADDING_MM = 2.5


def text_inner_rect(rect: Rect, padding_mm: float = TEXT_CONTENT_PADDING_MM) -> Rect:
    """Return the content area inset from the visible text handle."""
    padded = rect.inset(float(padding_mm))
    return padded if padded.width > 0.0 and padded.height > 0.0 else rect
