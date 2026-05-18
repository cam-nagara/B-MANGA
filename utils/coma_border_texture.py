"""コマ枠線のボカシブラシ用アルファテクスチャ Mesh."""

from __future__ import annotations

import math
from typing import Sequence

import bpy

from . import border_geom
from . import log
from . import object_naming as on
from .geom import mm_to_m

_logger = log.get_logger(__name__)

COMA_BORDER_TEXTURE_MESH_PREFIX = "coma_border_texture_mesh_"
COMA_BORDER_TEXTURE_MATERIAL_PREFIX = "BName_ComaBorderTexture_"
COMA_BORDER_TEXTURE_IMAGE_PREFIX = "BName_ComaBorderAlpha_"
COMA_PLANE_ALPHA_IMAGE_PREFIX = "BName_ComaPlaneAlpha_"
COMA_BORDER_TEXTURE_UV_NAME = "BNameComaBorderTextureUV"

PROP_COMA_BORDER_KIND = "bname_coma_border_kind"
PROP_COMA_BORDER_OWNER_ID = "bname_coma_border_owner_id"

_PX_PER_MM = 3.0
_MIN_IMAGE_SIZE = 64
_MAX_IMAGE_SIZE = 1024
_SIGNATURE_PROP = "bname_border_alpha_signature"


def object_name(page_id: str, coma_id: str) -> str:
    return f"coma_border_{page_id}_{coma_id}"


def mesh_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_BORDER_TEXTURE_MESH_PREFIX}{page_id}_{coma_id}"


def material_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_BORDER_TEXTURE_MATERIAL_PREFIX}{page_id}_{coma_id}"


def image_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_BORDER_TEXTURE_IMAGE_PREFIX}{page_id}_{coma_id}"


def plane_alpha_image_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_PLANE_ALPHA_IMAGE_PREFIX}{page_id}_{coma_id}"


def ensure_coma_plane_alpha_image(
    page_id: str,
    coma_id: str,
    outline_mm: Sequence[tuple[float, float]],
    width_mm: float,
    blur_amount: float,
    *,
    dither: bool = False,
) -> bpy.types.Image | None:
    pts = [(float(x), float(y)) for x, y in outline_mm]
    if len(pts) < 3 or width_mm <= 0.0:
        return None
    bounds = _bounds(pts)
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    width_px = _texture_size(max_x - min_x)
    height_px = _texture_size(max_y - min_y)
    name = plane_alpha_image_name(page_id, coma_id)
    image = bpy.data.images.get(name)
    if image is not None and str(getattr(image, "source", "") or "") != "GENERATED":
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass
        image = None
    if image is None or image.size[0] != width_px or image.size[1] != height_px:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:  # noqa: BLE001
                pass
        image = bpy.data.images.new(name, width=width_px, height=height_px, alpha=True, float_buffer=False)
    try:
        image.colorspace_settings.name = "Non-Color"
        image.generated_color = (1.0, 1.0, 1.0, 0.0)
    except Exception:  # noqa: BLE001
        pass
    signature = _plane_alpha_signature(
        pts,
        bounds,
        width_px,
        height_px,
        width_mm,
        blur_amount,
        dither=dither,
    )
    if str(image.get(_SIGNATURE_PROP, "") or "") == signature and _plane_cache_sample_ok(
        image,
        pts,
        bounds,
        width_mm,
        blur_amount,
        dither=dither,
    ):
        return image
    pixels = _plane_alpha_pixels(
        pts,
        bounds,
        width_px,
        height_px,
        width_mm,
        blur_amount,
        dither=dither,
    )
    try:
        image.pixels.foreach_set(pixels)
        image.update()
        image[_SIGNATURE_PROP] = signature
    except Exception:  # noqa: BLE001
        _logger.exception("coma plane alpha image update failed")
    return image


def cleanup_plane_alpha_assets(page_id: str, coma_id: str) -> None:
    image = bpy.data.images.get(plane_alpha_image_name(page_id, coma_id))
    if image is not None and image.users == 0:
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass


def ensure_brush_border_mesh(
    page_id: str,
    coma_id: str,
    owner_id: str,
    outline_mm: Sequence[tuple[float, float]],
    width_mm: float,
    color: tuple[float, float, float, float],
    blur_amount: float,
    *,
    dither: bool = False,
    background: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> bpy.types.Object | None:
    pts = [(float(x), float(y)) for x, y in outline_mm]
    if len(pts) < 3 or width_mm <= 0.0:
        return None
    bounds = _bounds(pts)
    if bounds is None:
        return None
    total_mm = _brush_total_width_mm(width_mm, blur_amount)
    mesh = _ensure_mesh(page_id, coma_id, pts, bounds, total_mm)
    image = _ensure_alpha_image(
        page_id,
        coma_id,
        pts,
        bounds,
        width_mm,
        color,
        blur_amount,
        dither=dither,
        background=background,
    )
    mat = _ensure_material(page_id, coma_id, color, image, dither=dither)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj_name = object_name(page_id, coma_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and obj.type != "MESH":
        old_data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        _remove_orphan_data(old_data)
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    obj[PROP_COMA_BORDER_KIND] = "coma_border"
    obj[PROP_COMA_BORDER_OWNER_ID] = owner_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    try:
        obj.display_type = "TEXTURED"
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.show_transparent = True
    except Exception:  # noqa: BLE001
        pass
    return obj


def cleanup_orphan_assets(page_id: str, coma_id: str) -> None:
    names = {
        "mesh": mesh_name(page_id, coma_id),
        "mat": material_name(page_id, coma_id),
        "image": image_name(page_id, coma_id),
    }
    mesh = bpy.data.meshes.get(names["mesh"])
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            pass
    mat = bpy.data.materials.get(names["mat"])
    if mat is not None and mat.users == 0:
        try:
            bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            pass
    image = bpy.data.images.get(names["image"])
    if image is not None and image.users == 0:
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass


def _bounds(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x - min_x <= 1.0e-6 or max_y - min_y <= 1.0e-6:
        return None
    return min_x, min_y, max_x, max_y


def _ensure_mesh(
    page_id: str,
    coma_id: str,
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    total_width_mm: float,
) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.get(mesh_name(page_id, coma_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name(page_id, coma_id))
    mesh_points, faces = _ring_mesh_points(points, total_width_mm)
    if not mesh_points or not faces:
        mesh_points = [(float(x), float(y)) for x, y in points]
        faces = [tuple(range(len(mesh_points)))]
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in mesh_points]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    _ensure_uv(mesh, mesh_points, bounds)
    return mesh


def _ring_mesh_points(
    points: Sequence[tuple[float, float]],
    total_width_mm: float,
) -> tuple[list[tuple[float, float]], list[tuple[int, int, int, int]]]:
    """輪郭から内側へ消える画像帯だけをメッシュ化する."""
    width = max(0.0, float(total_width_mm))
    if width <= 1.0e-6 or len(points) < 3:
        return [], []
    try:
        loops = border_geom.stroke_loops_mm(points, width * 2.0)
    except Exception:  # noqa: BLE001
        _logger.exception("coma border texture ring loop failed")
        loops = None
    if loops is None:
        return [], []
    _outer_unused, inner = loops
    outer = [(float(x), float(y)) for x, y in points]
    if len(inner) != len(outer) or len(outer) < 3:
        return [], []
    n = len(outer)
    mesh_points = outer + [(float(x), float(y)) for x, y in inner]
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    return mesh_points, faces


def _ensure_uv(
    mesh: bpy.types.Mesh,
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
) -> None:
    min_x, min_y, max_x, max_y = bounds
    width = max(max_x - min_x, 1.0e-6)
    height = max(max_y - min_y, 1.0e-6)
    uv_layer = mesh.uv_layers.get(COMA_BORDER_TEXTURE_UV_NAME)
    if uv_layer is None:
        uv_layer = mesh.uv_layers.new(name=COMA_BORDER_TEXTURE_UV_NAME)
    try:
        for loop in mesh.loops:
            x, y = points[loop.vertex_index]
            uv_layer.data[loop.index].uv = ((x - min_x) / width, (y - min_y) / height)
    except Exception:  # noqa: BLE001
        _logger.exception("coma border texture UV assign failed")


def _ensure_alpha_image(
    page_id: str,
    coma_id: str,
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    width_mm: float,
    color: tuple[float, float, float, float],
    blur_amount: float,
    *,
    dither: bool,
    background: tuple[float, float, float],
) -> bpy.types.Image:
    min_x, min_y, max_x, max_y = bounds
    box_w = max_x - min_x
    box_h = max_y - min_y
    width_px = _texture_size(box_w)
    height_px = _texture_size(box_h)
    name = image_name(page_id, coma_id)
    image = bpy.data.images.get(name)
    if image is None or image.size[0] != width_px or image.size[1] != height_px:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:  # noqa: BLE001
                pass
        image = bpy.data.images.new(name, width=width_px, height=height_px, alpha=True, float_buffer=False)
    try:
        image.colorspace_settings.name = "sRGB"
    except Exception:  # noqa: BLE001
        pass
    signature = _image_signature(
        points,
        bounds,
        width_px,
        height_px,
        width_mm,
        color,
        blur_amount,
        dither=dither,
        background=background,
    )
    if str(image.get(_SIGNATURE_PROP, "") or "") == signature and _cache_sample_ok(
        image,
        points,
        bounds,
        width_mm,
        color,
        blur_amount,
        dither=dither,
    ):
        return image
    pixels = _alpha_pixels(
        points,
        bounds,
        width_px,
        height_px,
        width_mm,
        color,
        blur_amount,
        dither=dither,
        background=background,
    )
    try:
        image.pixels.foreach_set(pixels)
        image.update()
        image[_SIGNATURE_PROP] = signature
    except Exception:  # noqa: BLE001
        _logger.exception("coma border alpha image update failed")
    return image


def _texture_size(size_mm: float) -> int:
    raw = int(math.ceil(max(1.0, size_mm) * _PX_PER_MM))
    return max(_MIN_IMAGE_SIZE, min(_MAX_IMAGE_SIZE, raw))


def _alpha_pixels(
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    width_mm: float,
    color: tuple[float, float, float, float],
    blur_amount: float,
    *,
    dither: bool,
    background: tuple[float, float, float],
) -> list[float]:
    min_x, min_y, max_x, max_y = bounds
    box_w = max(max_x - min_x, 1.0e-6)
    box_h = max(max_y - min_y, 1.0e-6)
    blur = max(0.0, min(1.0, float(blur_amount)))
    line_w = max(0.0, float(width_mm))
    color_alpha = max(0.0, min(1.0, float(color[3])))
    core_mm, fade_mm, total_mm = _brush_width_parts(line_w, blur)
    r, g, b = float(color[0]), float(color[1]), float(color[2])
    br, bg, bb = _background_rgb(background)
    alpha_values: list[float] = [0.0] * (width_px * height_px)
    pixels: list[float] = [0.0] * (width_px * height_px * 4)
    for y_px in range(height_px):
        y = min_y + ((y_px + 0.5) / height_px) * box_h
        for x_px in range(width_px):
            x = min_x + ((x_px + 0.5) / width_px) * box_w
            alpha_values[y_px * width_px + x_px] = _alpha_at_point(
                x,
                y,
                points,
                core_mm,
                fade_mm,
                total_mm,
                color_alpha,
            )
    if dither:
        alpha_values = _error_diffuse_alpha(alpha_values, width_px, height_px, color_alpha)
    for y_px in range(height_px):
        for x_px in range(width_px):
            alpha = alpha_values[y_px * width_px + x_px]
            offset = (y_px * width_px + x_px) * 4
            display_alpha = alpha / max(color_alpha, 1.0e-6)
            pixels[offset] = br + (r - br) * display_alpha
            pixels[offset + 1] = bg + (g - bg) * display_alpha
            pixels[offset + 2] = bb + (b - bb) * display_alpha
            pixels[offset + 3] = alpha
    return pixels


def _plane_alpha_pixels(
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    width_mm: float,
    blur_amount: float,
    *,
    dither: bool,
) -> list[float]:
    min_x, min_y, max_x, max_y = bounds
    box_w = max(max_x - min_x, 1.0e-6)
    box_h = max(max_y - min_y, 1.0e-6)
    core_mm, fade_mm, total_mm = _brush_width_parts(max(0.0, float(width_mm)), max(0.0, min(1.0, float(blur_amount))))
    fade_total = max(1.0e-6, core_mm + fade_mm if total_mm > 0.0 else float(width_mm))
    alpha_values: list[float] = [0.0] * (width_px * height_px)
    pixels: list[float] = [0.0] * (width_px * height_px * 4)
    for y_px in range(height_px):
        y = min_y + (y_px / max(1, height_px - 1)) * box_h
        for x_px in range(width_px):
            x = min_x + (x_px / max(1, width_px - 1)) * box_w
            alpha_values[y_px * width_px + x_px] = _plane_alpha_at_point(
                x,
                y,
                points,
                fade_total,
            )
    if dither:
        alpha_values = _error_diffuse_alpha(alpha_values, width_px, height_px, 1.0)
    for y_px in range(height_px):
        for x_px in range(width_px):
            alpha = alpha_values[y_px * width_px + x_px]
            offset = (y_px * width_px + x_px) * 4
            pixels[offset] = 1.0
            pixels[offset + 1] = 1.0
            pixels[offset + 2] = 1.0
            pixels[offset + 3] = alpha
    return pixels


def _cache_sample_ok(
    image: bpy.types.Image,
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    width_mm: float,
    color: tuple[float, float, float, float],
    blur_amount: float,
    *,
    dither: bool,
) -> bool:
    try:
        width_px = int(image.size[0])
        height_px = int(image.size[1])
        if width_px <= 0 or height_px <= 0:
            return False
        x_px = width_px // 2
        y_px = height_px // 2
        min_x, min_y, max_x, max_y = bounds
        x = min_x + (x_px / max(1, width_px - 1)) * max(max_x - min_x, 1.0e-6)
        y = min_y + (y_px / max(1, height_px - 1)) * max(max_y - min_y, 1.0e-6)
        blur = max(0.0, min(1.0, float(blur_amount)))
        core_mm, fade_mm, total_mm = _brush_width_parts(max(0.0, float(width_mm)), blur)
        color_alpha = max(0.0, min(1.0, float(color[3])))
        expected = _alpha_at_point(
            x,
            y,
            points,
            core_mm,
            fade_mm,
            total_mm,
            color_alpha,
        )
        if dither:
            expected = color_alpha if expected >= color_alpha * 0.5 else 0.0
        actual = float(image.pixels[(y_px * width_px + x_px) * 4 + 3])
    except Exception:  # noqa: BLE001
        return False
    return abs(actual - expected) <= 1.0e-4


def _plane_cache_sample_ok(
    image: bpy.types.Image,
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    width_mm: float,
    blur_amount: float,
    *,
    dither: bool,
) -> bool:
    try:
        width_px = int(image.size[0])
        height_px = int(image.size[1])
        if width_px <= 0 or height_px <= 0:
            return False
        samples = ((width_px // 2, height_px // 2), (0, height_px // 2))
        min_x, min_y, max_x, max_y = bounds
        core_mm, fade_mm, total_mm = _brush_width_parts(max(0.0, float(width_mm)), max(0.0, min(1.0, float(blur_amount))))
        fade_total = max(1.0e-6, core_mm + fade_mm if total_mm > 0.0 else float(width_mm))
        for x_px, y_px in samples:
            x = min_x + (x_px / max(1, width_px - 1)) * max(max_x - min_x, 1.0e-6)
            y = min_y + (y_px / max(1, height_px - 1)) * max(max_y - min_y, 1.0e-6)
            expected = _plane_alpha_at_point(
                x,
                y,
                points,
                fade_total,
            )
            if dither:
                expected = 1.0 if expected >= 0.5 else 0.0
            actual = float(image.pixels[(y_px * width_px + x_px) * 4 + 3])
            base = (y_px * width_px + x_px) * 4
            if (
                float(image.pixels[base]) < 0.95
                or float(image.pixels[base + 1]) < 0.95
                or float(image.pixels[base + 2]) < 0.95
            ):
                return False
            if abs(actual - expected) > 1.0e-4:
                return False
    except Exception:  # noqa: BLE001
        return False
    return True


def _alpha_at_point(
    x: float,
    y: float,
    points: Sequence[tuple[float, float]],
    core_mm: float,
    fade_mm: float,
    total_mm: float,
    color_alpha: float,
) -> float:
    if not _point_in_polygon(x, y, points):
        return 0.0
    dist = _distance_to_edges(x, y, points)
    return _edge_alpha(dist, core_mm, fade_mm, total_mm, color_alpha)


def _plane_alpha_at_point(
    x: float,
    y: float,
    points: Sequence[tuple[float, float]],
    fade_total_mm: float,
) -> float:
    if not _point_in_polygon(x, y, points):
        return 0.0
    dist = _distance_to_edges(x, y, points)
    t = max(0.0, min(1.0, dist / max(1.0e-6, float(fade_total_mm))))
    return t * t * (3.0 - 2.0 * t)


def _error_diffuse_alpha(
    values: Sequence[float],
    width_px: int,
    height_px: int,
    max_alpha: float,
) -> list[float]:
    """Floyd-Steinberg 誤差拡散で中間アルファを二値化する."""
    width = max(0, int(width_px))
    height = max(0, int(height_px))
    upper = max(0.0, min(1.0, float(max_alpha)))
    if width <= 0 or height <= 0 or upper <= 0.0:
        return [0.0] * (width * height)
    data = [max(0.0, min(upper, float(v))) for v in values]
    if len(data) < width * height:
        data.extend([0.0] * (width * height - len(data)))
    threshold = upper * 0.5

    def add_error(x: int, y: int, error: float, weight: float) -> None:
        if x < 0 or x >= width or y < 0 or y >= height:
            return
        idx = y * width + x
        data[idx] = max(0.0, min(upper, data[idx] + error * weight))

    for y in range(height):
        if y & 1:
            x_iter = range(width - 1, -1, -1)
            forward = -1
        else:
            x_iter = range(width)
            forward = 1
        for x in x_iter:
            idx = y * width + x
            old = max(0.0, min(upper, data[idx]))
            new = upper if old >= threshold else 0.0
            error = old - new
            data[idx] = new
            add_error(x + forward, y, error, 7.0 / 16.0)
            add_error(x - forward, y + 1, error, 3.0 / 16.0)
            add_error(x, y + 1, error, 5.0 / 16.0)
            add_error(x + forward, y + 1, error, 1.0 / 16.0)
    return data[: width * height]


def _brush_width_parts(line_w: float, blur: float) -> tuple[float, float, float]:
    if blur <= 0.0:
        core_mm = line_w
        fade_mm = 0.0
    else:
        core_mm = line_w * 0.35
        fade_mm = max(0.15, line_w * (0.65 + 3.35 * blur))
    return core_mm, fade_mm, core_mm + fade_mm


def _brush_total_width_mm(line_w: float, blur_amount: float) -> float:
    blur = max(0.0, min(1.0, float(blur_amount)))
    _core, _fade, total = _brush_width_parts(max(0.0, float(line_w)), blur)
    return total


def _background_rgb(background: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        return (
            max(0.0, min(1.0, float(background[0]))),
            max(0.0, min(1.0, float(background[1]))),
            max(0.0, min(1.0, float(background[2]))),
        )
    except Exception:  # noqa: BLE001
        return (1.0, 1.0, 1.0)


def _image_signature(
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    width_mm: float,
    color: tuple[float, float, float, float],
    blur_amount: float,
    *,
    dither: bool,
    background: tuple[float, float, float],
) -> str:
    rounded_points = tuple((round(float(x), 4), round(float(y), 4)) for x, y in points)
    rounded_bounds = tuple(round(float(v), 4) for v in bounds)
    rounded_color = tuple(round(float(v), 5) for v in color)
    rounded_bg = tuple(round(float(v), 5) for v in _background_rgb(background))
    return repr(
        (
            "border-alpha-v2",
            rounded_points,
            rounded_bounds,
            int(width_px),
            int(height_px),
            round(float(width_mm), 4),
            rounded_color,
            round(float(blur_amount), 5),
            bool(dither),
            rounded_bg,
        )
    )


def _plane_alpha_signature(
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    width_mm: float,
    blur_amount: float,
    *,
    dither: bool,
) -> str:
    rounded_points = tuple((round(float(x), 4), round(float(y), 4)) for x, y in points)
    rounded_bounds = tuple(round(float(v), 4) for v in bounds)
    return repr(
        (
            "plane-alpha-v4",
            rounded_points,
            rounded_bounds,
            int(width_px),
            int(height_px),
            round(float(width_mm), 4),
            round(float(blur_amount), 5),
            bool(dither),
        )
    )


def _edge_alpha(dist: float, core_mm: float, fade_mm: float, total_mm: float, color_alpha: float) -> float:
    if total_mm <= 0.0 or dist > total_mm:
        return 0.0
    if fade_mm <= 1.0e-6 or dist <= core_mm:
        return color_alpha
    t = max(0.0, min(1.0, (dist - core_mm) / fade_mm))
    smooth = t * t * (3.0 - 2.0 * t)
    return color_alpha * (1.0 - smooth)


def _point_in_polygon(x: float, y: float, points: Sequence[tuple[float, float]]) -> bool:
    inside = False
    count = len(points)
    j = count - 1
    for i in range(count):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > y) != (yj > y):
            denom = yj - yi
            if abs(denom) <= 1.0e-12:
                j = i
                continue
            hit_x = (xj - xi) * (y - yi) / denom + xi
            if x < hit_x:
                inside = not inside
        j = i
    return inside


def _distance_to_edges(x: float, y: float, points: Sequence[tuple[float, float]]) -> float:
    best = float("inf")
    count = len(points)
    for i in range(count):
        ax, ay = points[i]
        bx, by = points[(i + 1) % count]
        best = min(best, _distance_to_segment(x, y, ax, ay, bx, by))
    return best


def _distance_to_segment(x: float, y: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1.0e-12:
        return math.hypot(x - ax, y - ay)
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
    px = ax + dx * t
    py = ay + dy * t
    return math.hypot(x - px, y - py)


def _ensure_material(
    page_id: str,
    coma_id: str,
    color: tuple[float, float, float, float],
    image: bpy.types.Image,
    *,
    dither: bool,
) -> bpy.types.Material:
    mat = bpy.data.materials.get(material_name(page_id, coma_id))
    if mat is None:
        mat = bpy.data.materials.new(material_name(page_id, coma_id))
    r, g, b, a = float(color[0]), float(color[1]), float(color[2]), float(color[3])
    mat.diffuse_color = (r, g, b, a)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = False
        mat.surface_render_method = "DITHERED" if dither else "BLENDED"
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (380, 0)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (-360, 20)
    tex.image = image
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-60, 120)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-60, -120)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (160, 0)
    try:
        emission.inputs["Color"].default_value = (r, g, b, 1.0)
        emission.inputs["Strength"].default_value = 1.0
        nt.links.new(tex.outputs["Alpha"], mix.inputs[0])
        nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
        nt.links.new(emission.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("coma border texture material setup failed")
    return mat


def _remove_orphan_data(data) -> None:
    if data is None or getattr(data, "users", 0) > 0:
        return
    try:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
    except Exception:  # noqa: BLE001
        pass
