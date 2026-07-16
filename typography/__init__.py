"""typography — 日本語組版エンジン (blf + fontTools + Pillow の 3 層構成)."""

from __future__ import annotations

from . import export_renderer, kinsoku, layout, metrics, ruby, tatechuyoko, vertical_glyph, viewport_renderer  # noqa: F401


def register() -> None:
    pass


def unregister() -> None:
    pass
