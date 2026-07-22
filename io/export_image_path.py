"""パターンカーブ (image_path) レイヤーの書き出しラスタライズ.

ビューポート実体 (utils/image_path_object.py の _build_stamp_mesh /
_build_ribbon_mesh) と同じ幾何ヘルパー・同じ配色ロジック
(utils/path_content.py) を再利用し、Pillow で ExportLayer を生成する。
コマ形状のクリップは export_group_masks のコマグループマスクが担うため、
ここではマスクを扱わない。
"""

from __future__ import annotations

import math
from pathlib import Path

from ..utils import color_space, log, path_content
from ..utils.geom import mm_to_px

_logger = log.get_logger(__name__)

# ビューポートのメッシュにはピクセル解像度の概念が無いため、書き出しは
# 2倍で描いて縮小するスーパーサンプリングで輪郭を滑らかにする
# (utils/page_preview_object.py の PREVIEW_RENDER_SUPERSAMPLE と同じ発想)。
_SUPERSAMPLE = 2


def _srgb_byte(linear_rgba) -> tuple[int, int, int, int]:
    r, g, b = color_space.linear_to_srgb_rgb(tuple(float(c) for c in linear_rgba[:3]))
    a = float(linear_rgba[3]) if len(linear_rgba) > 3 else 1.0
    return (
        max(0, min(255, int(round(r * 255)))),
        max(0, min(255, int(round(g * 255)))),
        max(0, min(255, int(round(b * 255)))),
        max(0, min(255, int(round(a * 255)))),
    )


def _load_source_image(Image, entry):
    filepath = str(getattr(entry, "filepath", "") or "")
    if not filepath:
        return None
    try:
        import bpy

        abs_path = Path(bpy.path.abspath(filepath))
    except Exception:  # noqa: BLE001
        abs_path = Path(filepath)
    if not abs_path.is_file():
        return None
    try:
        with Image.open(abs_path) as opened:
            return opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        _logger.exception("image path export: source image load failed: %s", abs_path)
        return None


def _tinted(Image, source, rgba255: tuple[int, int, int, int]):
    """画像色 × ポイント色の乗算tint (path_content.ensure_material と同じ式)."""
    r, g, b, a = rgba255
    if (r, g, b, a) == (255, 255, 255, 255):
        return source
    ch = source.split()
    out = [
        ch[0].point(lambda v, m=r: (v * m) // 255),
        ch[1].point(lambda v, m=g: (v * m) // 255),
        ch[2].point(lambda v, m=b: (v * m) // 255),
        ch[3].point(lambda v, m=a: (v * m) // 255),
    ]
    return Image.merge("RGBA", out)


def _affine_from_points(dest_pts, src_pts) -> tuple[float, ...] | None:
    """dest 3点 → src 3点 の対応から PIL AFFINE 係数 (src = A·dest) を解く."""
    (dx0, dy0), (dx1, dy1), (dx2, dy2) = dest_pts
    (sx0, sy0), (sx1, sy1), (sx2, sy2) = src_pts
    e1x, e1y = dx1 - dx0, dy1 - dy0
    e2x, e2y = dx2 - dx0, dy2 - dy0
    det = e1x * e2y - e1y * e2x
    if abs(det) < 1.0e-9:
        return None
    f1x, f1y = sx1 - sx0, sy1 - sy0
    f2x, f2y = sx2 - sx0, sy2 - sy0
    a = (f1x * e2y - f2x * e1y) / det
    b = (f2x * e1x - f1x * e2x) / det
    d = (f1y * e2y - f2y * e1y) / det
    e = (f2y * e1x - f1y * e2x) / det
    c = sx0 - a * dx0 - b * dy0
    f = sy0 - d * dx0 - e * dy0
    return (a, b, c, d, e, f)


def _composite_quad(Image, hires, source, dest_quad, src_quad) -> None:
    """source の src_quad (px) を hires 上の dest_quad (px) へ貼り込む."""
    xs = [p[0] for p in dest_quad]
    ys = [p[1] for p in dest_quad]
    bx0 = max(0, int(math.floor(min(xs))))
    by0 = max(0, int(math.floor(min(ys))))
    bx1 = min(hires.width, int(math.ceil(max(xs))))
    by1 = min(hires.height, int(math.ceil(max(ys))))
    if bx1 <= bx0 or by1 <= by0:
        return
    local_dest = [(x - bx0, y - by0) for x, y in dest_quad[:3]]
    coeffs = _affine_from_points(local_dest, src_quad[:3])
    if coeffs is None:
        return
    patch = source.transform(
        (bx1 - bx0, by1 - by0),
        Image.AFFINE,
        coeffs,
        resample=Image.BILINEAR,
    )
    hires.alpha_composite(patch, (bx0, by0))


def _render_stamps(Image, ImageDraw, hires, to_px, entry, points, source) -> None:
    from ..utils import image_path_object as ipo

    cumulative, total = ipo._path_lengths(points)
    brush = max(0.1, float(getattr(entry, "brush_size_mm", 10.0) or 10.0))
    aspect = max(0.01, float(getattr(entry, "aspect_ratio", 1.0) or 1.0))
    spacing = max(0.1, brush * max(1.0, float(getattr(entry, "spacing_percent", 100.0) or 100.0)) / 100.0)
    distances = [0.0]
    d = spacing
    while d < total:
        distances.append(d)
        d += spacing
    if total > 0.0 and (not distances or abs(distances[-1] - total) > spacing * 0.35):
        distances.append(total)

    is_shape = str(getattr(entry, "content_source", "image") or "image") == "shape"
    base_shape = path_content.unit_shape_points(
        getattr(entry, "shape_kind", "circle"),
        sides=int(getattr(entry, "shape_sides", 6) or 6),
    )
    base_corners = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
    for distance in distances:
        x, y, path_angle = ipo._interpolate_at(points, cumulative, distance)
        profile = path_content.inout_profile_value(entry, distance, total)
        scale = path_content.size_factor(entry, profile)
        if scale <= 1.0e-6:
            continue
        rgba = path_content.color_for_path_distance(entry, distance, total)
        rgba255 = _srgb_byte(rgba)
        if rgba255[3] <= 0:
            continue
        angle = ipo._stamp_angle(entry, path_angle)
        ca = math.cos(angle)
        sa = math.sin(angle)

        def world(ux: float, uy: float) -> tuple[float, float]:
            lx = ux * brush * aspect * scale
            ly = uy * brush * scale
            return (x + lx * ca - ly * sa, y + lx * sa + ly * ca)

        if is_shape:
            poly = [to_px(*world(ux, uy)) for ux, uy in base_shape]
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            bx0 = max(0, int(math.floor(min(xs))))
            by0 = max(0, int(math.floor(min(ys))))
            bx1 = min(hires.width, int(math.ceil(max(xs))))
            by1 = min(hires.height, int(math.ceil(max(ys))))
            if bx1 <= bx0 or by1 <= by0:
                continue
            patch = Image.new("RGBA", (bx1 - bx0, by1 - by0), (0, 0, 0, 0))
            draw = ImageDraw.Draw(patch)
            draw.polygon([(px - bx0, py - by0) for px, py in poly], fill=rgba255)
            hires.alpha_composite(patch, (bx0, by0))
        elif source is not None:
            tinted = _tinted(Image, source, rgba255)
            # メッシュのUV (0,0)=左下 → PIL 画像は (0,H)=左下
            w_src, h_src = tinted.size
            dest_quad = [to_px(*world(ux, uy)) for ux, uy in base_corners]
            src_quad = [(0.0, float(h_src)), (float(w_src), float(h_src)), (float(w_src), 0.0), (0.0, 0.0)]
            _composite_quad(Image, hires, tinted, dest_quad, src_quad)


def _render_ribbon(Image, hires, to_px, entry, points, source) -> None:
    from ..utils import image_path_object as ipo

    cumulative, total = ipo._path_lengths(points)
    brush = max(0.1, float(getattr(entry, "brush_size_mm", 10.0) or 10.0))
    aspect = max(0.01, float(getattr(entry, "aspect_ratio", 1.0) or 1.0))
    spacing = max(0.1, brush * aspect * max(1.0, float(getattr(entry, "spacing_percent", 100.0) or 100.0)) / 100.0)
    half = brush * 0.5
    stretch = str(getattr(entry, "ribbon_repeat_mode", "repeat") or "repeat") == "stretch"
    angle = math.radians(float(getattr(entry, "image_angle_deg", 0.0) or 0.0))
    w_src, h_src = source.size

    lefts: list[tuple[float, float]] = []
    rights: list[tuple[float, float]] = []
    uvs_l: list[tuple[float, float]] = []
    uvs_r: list[tuple[float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    for i, (x, y) in enumerate(points):
        profile = path_content.inout_profile_value(entry, cumulative[i], total)
        local_half = half * path_content.size_factor(entry, profile)
        colors.append(path_content.color_for_path_distance(entry, cumulative[i], total))
        tx, ty = ipo._ribbon_tangent(points, i)
        nx, ny = -ty, tx
        lefts.append((x + nx * local_half, y + ny * local_half))
        rights.append((x - nx * local_half, y - ny * local_half))
        if stretch:
            u = cumulative[i] / total if total > 1.0e-9 else 0.0
        else:
            u = cumulative[i] / spacing
        uvs_l.append(ipo._uv_rotated(u, 1.0, angle, repeat=not stretch))
        uvs_r.append(ipo._uv_rotated(u, 0.0, angle, repeat=not stretch))

    # REPEAT 用に横タイルを繋いだストリップ (縦も回転はみ出し用に3タイル)。
    # 区間ごとの u 範囲は狭いのでストリップは都度作らず全区間で1枚共有する。
    all_u = [uv[0] for uv in uvs_l + uvs_r]
    all_v = [uv[1] for uv in uvs_l + uvs_r]
    tile_u0 = int(math.floor(min(all_u)))
    tiles_u = max(1, int(math.ceil(max(all_u))) - tile_u0 + 1)
    tile_v0 = int(math.floor(min(all_v)))
    tiles_v = max(1, int(math.ceil(max(all_v))) - tile_v0 + 1)
    if tiles_u * tiles_v > 4096:
        # UVが異常に発散している場合の保険 (巨大ストリップでのメモリ暴走防止)
        return
    strip = Image.new("RGBA", (w_src * tiles_u, h_src * tiles_v), (0, 0, 0, 0))
    for ty_i in range(tiles_v):
        for tx_i in range(tiles_u):
            strip.paste(source, (tx_i * w_src, ty_i * h_src))

    def src_px(u: float, v: float) -> tuple[float, float]:
        return (
            (u - tile_u0) * w_src,
            (tiles_v + tile_v0 - v) * h_src,
        )

    for i in range(len(points) - 1):
        rgba255 = _srgb_byte(colors[i])
        if rgba255[3] <= 0:
            continue
        tinted = _tinted(Image, strip, rgba255)
        dest_quad = [
            to_px(*lefts[i]),
            to_px(*rights[i]),
            to_px(*lefts[i + 1]),
            to_px(*rights[i + 1]),
        ]
        src_quad = [
            src_px(*uvs_l[i]),
            src_px(*uvs_r[i]),
            src_px(*uvs_l[i + 1]),
            src_px(*uvs_r[i + 1]),
        ]
        _composite_quad(Image, hires, tinted, dest_quad, src_quad)


def render_image_path_layer(entry, canvas_height_px: int, dpi: int, *, group_path=("image_path_layers",)):
    """パターンカーブ1件を bbox 限定の ExportLayer にする。描けない時は None."""
    from . import export_pipeline as ep
    from ..utils import image_path_object as ipo

    Image = ep.Image
    ImageDraw = ep.ImageDraw
    if Image is None:
        return None

    points = ipo._parse_points(entry)
    if len(points) < 2:
        return None
    display_points = ipo._smooth_path_points(entry, points)
    if len(display_points) < 2:
        return None

    is_shape = str(getattr(entry, "content_source", "image") or "image") == "shape"
    source = None
    if not is_shape:
        source = _load_source_image(Image, entry)
        if source is None:
            # ビューポートも画像未指定の image ソースは透明 (fallback_alpha=0)
            return None

    brush = max(0.1, float(getattr(entry, "brush_size_mm", 10.0) or 10.0))
    aspect = max(0.01, float(getattr(entry, "aspect_ratio", 1.0) or 1.0))
    pad_mm = 0.5 * brush * math.hypot(aspect, 1.0) + 1.0
    bbox = ep._points_bbox(display_points)
    if bbox is None:
        return None
    canvas = ep._canvas_for_bbox(bbox, canvas_height_px, dpi, pad_mm=pad_mm)
    if canvas is None:
        return None

    ss = _SUPERSAMPLE
    hires = Image.new("RGBA", (canvas.image.width * ss, canvas.image.height * ss), (0, 0, 0, 0))

    def to_px(x_mm: float, y_mm: float) -> tuple[float, float]:
        return (
            (mm_to_px(x_mm, dpi) - canvas.left) * ss,
            ((canvas_height_px - mm_to_px(y_mm, dpi)) - canvas.top) * ss,
        )

    use_ribbon = (
        not is_shape
        and str(getattr(entry, "draw_mode", "stamp") or "stamp") == "ribbon"
    )
    if use_ribbon:
        _render_ribbon(Image, hires, to_px, entry, display_points, source)
    else:
        _render_stamps(Image, ImageDraw, hires, to_px, entry, display_points, source)

    rendered = hires.resize(canvas.image.size, Image.LANCZOS)
    opacity_pct = float(getattr(entry, "opacity", 100.0) or 100.0)
    return ep.ExportLayer(
        str(getattr(entry, "title", "") or getattr(entry, "id", "") or "image_path"),
        rendered,
        canvas.left,
        canvas.top,
        group_path=tuple(group_path),
        visible=bool(getattr(entry, "visible", True)),
        opacity=max(0, min(255, int(round(opacity_pct * 2.55)))),
        blend_mode="normal",
        stack_parent_key=str(getattr(entry, "folder_key", "") or getattr(entry, "parent_key", "") or ""),
    )
