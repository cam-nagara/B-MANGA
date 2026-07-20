"""ラスター描画レイヤーの書き出し合成."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils import color_space, log, percentage

_logger = log.get_logger(__name__)


def _srgb255_from_linear_color(rgba) -> tuple[int, int, int, int]:
    rgb = color_space.linear_to_srgb_rgb(tuple(float(c) for c in rgba[:3]))
    alpha = float(rgba[3]) if len(rgba) > 3 else 1.0
    return (
        max(0, min(255, round(rgb[0] * 255))),
        max(0, min(255, round(rgb[1] * 255))),
        max(0, min(255, round(rgb[2] * 255))),
        max(0, min(255, round(alpha * 255))),
    )


def _entry_png_path(work, entry) -> Path:
    return Path(str(getattr(work, "work_dir", "") or "")) / str(
        getattr(entry, "filepath_rel", "") or f"raster/{entry.id}.png"
    )


def _painted_ink_mask(src: Any):
    gray = src.convert("L")
    alpha = src.getchannel("A")
    # Texture Paint は黒で描く運用が自然なので、黒=濃いインクとして扱う。
    return gray.point(lambda value: 255 - int(value)), alpha


def _render_raster_entry(Image, entry, work, canvas_size: tuple[int, int]):
    path = _entry_png_path(work, entry)
    if not path.is_file():
        return None
    try:
        with Image.open(path) as opened:
            src = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        _logger.exception("raster png open failed: %s", path)
        return None
    if src.size != canvas_size:
        src = src.resize(canvas_size, Image.Resampling.LANCZOS)
    ink, alpha = _painted_ink_mask(src)
    if str(getattr(entry, "bit_depth", "") or "gray8") == "gray1":
        ink = ink.point(lambda value: 255 if value >= 128 else 0)
    line_rgba = _srgb255_from_linear_color(getattr(entry, "line_color", (0, 0, 0, 1)))
    fill_rgba = _srgb255_from_linear_color(getattr(entry, "fill_color", (1, 1, 1, 1)))
    opacity = percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)
    mask = Image.eval(alpha, lambda value: int(value * opacity * (line_rgba[3] / 255.0)))
    # グレー値で カラー(濃い側) と セカンダリカラー(薄い側) を混ぜる。
    # ビューポート側の MixRGB (Fac=グレー値) と同じ式にして表示と出力を一致させる。
    # ink = 255 - gray なので、ink=255 (黒) でカラー、ink=0 (白) でセカンダリカラー。
    density_rgb = Image.merge(
        "RGB",
        tuple(
            ink.point(lambda value, c=c, f=f: int(c * (value / 255.0) + f * (1.0 - value / 255.0)))
            for c, f in zip(line_rgba[:3], fill_rgba[:3])
        ),
    )
    color = Image.merge("RGBA", (*density_rgb.split(), mask))
    return color


def page_raster_layers(
    scene,
    work,
    page,
    canvas_size,
    _dpi,
    export_layer_cls,
    Image,
    *,
    group_path_for_parent=None,
    stack_uid_for_entry=None,
) -> list:
    coll = getattr(scene, "bmanga_raster_layers", None) if scene is not None else None
    if coll is None:
        return []
    layers = []
    page_id = str(getattr(page, "id", "") or "")
    for entry in coll:
        if not bool(getattr(entry, "visible", True)):
            continue
        if str(getattr(entry, "scope", "") or "page") != "page":
            continue
        parent_kind = str(getattr(entry, "parent_kind", "") or "page")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        if parent_kind not in {"page", "coma"}:
            continue
        if parent_kind == "page" and parent_key != page_id:
            continue
        if parent_kind == "coma" and parent_key.split(":", 1)[0] != page_id:
            continue
        image = _render_raster_entry(Image, entry, work, canvas_size)
        if image is None:
            continue
        layers.append(
            export_layer_cls(
                getattr(entry, "title", "") or f"raster_{entry.id}",
                image,
                0,
                0,
                group_path=(
                    group_path_for_parent(entry, ("raster",))
                    if group_path_for_parent is not None
                    else ("raster",)
                ),
                opacity=255,
                blend_mode="normal",
                stack_uid=(stack_uid_for_entry(entry) if stack_uid_for_entry is not None else ""),
                stack_parent_key=str(getattr(entry, "folder_key", "") or parent_key),
            )
        )
    return layers
