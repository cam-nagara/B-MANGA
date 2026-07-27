"""効果線の表示用MeshをPNG/2D合成用のPillowレイヤーへ変換する."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Callable

import bpy

from ..utils import color_space, log
from ..utils.geom import m_to_mm


_logger = log.get_logger(__name__)
_EFFECT_ALPHA_ATTRIBUTE = "bmanga_effect_alpha"
_PATH_COLOR_ATTRIBUTE = "bmanga_path_content_color"
_PATH_ALPHA_ATTRIBUTE = "bmanga_path_content_alpha"
_MAX_TILED_TEXTURE_PIXELS = 16 * 1024 * 1024


@dataclass(frozen=True)
class _Canvas:
    image: Any
    left: int
    top: int
    canvas_height_px: int
    dpi: int

    def point_px(self, point: tuple[float, float]) -> tuple[int, int]:
        x_mm, y_mm = point
        x_px = int(round(x_mm * self.dpi / 25.4)) - self.left
        y_px = (
            self.canvas_height_px
            - int(round(y_mm * self.dpi / 25.4))
            - self.top
        )
        return (x_px, y_px)


@dataclass(frozen=True)
class _Polygon:
    points: tuple[tuple[float, float], ...]
    color: tuple[int, int, int, int]
    texture_path: Path | None = None
    uvs: tuple[tuple[float, float], ...] = ()


def _rgba255(value) -> tuple[int, int, int, int]:
    try:
        rgba = tuple(float(component) for component in value)
    except Exception:  # noqa: BLE001
        rgba = (0.0, 0.0, 0.0, 1.0)
    while len(rgba) < 4:
        rgba += (1.0,)
    red, green, blue = color_space.linear_to_srgb_rgb(rgba[:3])
    return (
        int(round(max(0.0, min(1.0, red)) * 255)),
        int(round(max(0.0, min(1.0, green)) * 255)),
        int(round(max(0.0, min(1.0, blue)) * 255)),
        int(round(max(0.0, min(1.0, rgba[3])) * 255)),
    )


def _attribute_value(mesh, name: str, index: int, default: float) -> float:
    attribute = getattr(mesh, "attributes", None)
    attribute = attribute.get(name) if attribute is not None else None
    data = getattr(attribute, "data", None)
    if data is None or index < 0 or index >= len(data):
        return default
    try:
        return max(0.0, min(1.0, float(data[index].value)))
    except Exception:  # noqa: BLE001
        return default


def _attribute_color(mesh, index: int) -> tuple[float, float, float, float] | None:
    attributes = getattr(mesh, "attributes", None)
    attribute = attributes.get(_PATH_COLOR_ATTRIBUTE) if attributes is not None else None
    data = getattr(attribute, "data", None)
    if data is None or index < 0 or index >= len(data):
        return None
    try:
        color = tuple(float(component) for component in data[index].color)
    except Exception:  # noqa: BLE001
        return None
    if len(color) < 4:
        return None
    return color[:4]


def _material_color(mesh, material_index: int) -> tuple[int, int, int, int]:
    materials = getattr(mesh, "materials", None)
    if materials is None or material_index < 0 or material_index >= len(materials):
        return (0, 0, 0, 255)
    material = materials[material_index]
    return _rgba255(
        getattr(material, "diffuse_color", (0.0, 0.0, 0.0, 1.0))
        if material is not None
        else (0.0, 0.0, 0.0, 1.0)
    )


def _material_texture_path(mesh, material_index: int) -> Path | None:
    materials = getattr(mesh, "materials", None)
    if materials is None or material_index < 0 or material_index >= len(materials):
        return None
    material = materials[material_index]
    nodes = getattr(getattr(material, "node_tree", None), "nodes", ())
    for node in nodes:
        if str(getattr(node, "type", "") or "") != "TEX_IMAGE":
            continue
        image = getattr(node, "image", None)
        filepath = str(getattr(image, "filepath", "") or "")
        if not filepath:
            continue
        try:
            path = Path(bpy.path.abspath(filepath))
        except Exception:  # noqa: BLE001
            path = Path(filepath)
        if path.is_file():
            return path
    return None


def _controller_opacity(controller) -> float:
    data = getattr(controller, "data", None)
    try:
        meta = json.loads(str(data.get("bmanga_effect_line_meta", "{}") or "{}"))
    except Exception:  # noqa: BLE001
        return 1.0
    if not isinstance(meta, dict):
        return 1.0
    for entry in meta.values():
        params = entry.get("params", {}) if isinstance(entry, dict) else {}
        if not isinstance(params, dict):
            continue
        try:
            return max(0.0, min(1.0, float(params.get("opacity", 100.0)) / 100.0))
        except Exception:  # noqa: BLE001
            return 1.0
    return 1.0


def _canvas_for_points(
    Image,
    points: list[tuple[float, float]],
    canvas_size: tuple[int, int],
    dpi: int,
) -> _Canvas | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    canvas_width_px, canvas_height_px = canvas_size
    left_px = max(0, int(math.floor(min(xs) * dpi / 25.4)))
    right_px = min(canvas_width_px, int(math.ceil(max(xs) * dpi / 25.4)))
    top_px = max(
        0,
        canvas_height_px - int(math.ceil(max(ys) * dpi / 25.4)),
    )
    bottom_px = min(
        canvas_height_px,
        canvas_height_px - int(math.floor(min(ys) * dpi / 25.4)),
    )
    if right_px <= left_px or bottom_px <= top_px:
        return None
    return _Canvas(
        Image.new(
            "RGBA",
            (right_px - left_px, bottom_px - top_px),
            (0, 0, 0, 0),
        ),
        left_px,
        top_px,
        canvas_height_px,
        dpi,
    )


def _world_points(evaluated, mesh, page_offset_mm):
    result = []
    for vertex in mesh.vertices:
        point = evaluated.matrix_world @ vertex.co
        result.append(
            (
                m_to_mm(float(point.x)) - page_offset_mm[0],
                m_to_mm(float(point.y)) - page_offset_mm[1],
                float(point.z),
            )
        )
    return result


def _polygon_uvs(mesh, polygon, expected_count: int):
    uv_layer = getattr(getattr(mesh, "uv_layers", None), "active", None)
    uv_data = getattr(uv_layer, "data", None)
    if uv_data is None:
        return ()
    try:
        result = tuple(
            (
                float(uv_data[loop_index].uv.x),
                float(uv_data[loop_index].uv.y),
            )
            for loop_index in polygon.loop_indices
        )
    except Exception:  # noqa: BLE001
        return ()
    return result if len(result) == expected_count else ()


def _polygon_color(
    mesh,
    indexes: list[int],
    material_index: int,
    *,
    image_display: bool,
    opacity: float,
) -> tuple[int, int, int, int]:
    base_color = (
        (255, 255, 255, 255)
        if image_display
        else _material_color(mesh, material_index)
    )
    colors = [
        color
        for color in (_attribute_color(mesh, index) for index in indexes)
        if color is not None
    ]
    if colors:
        average = tuple(
            sum(color[channel] for color in colors) / len(colors)
            for channel in range(4)
        )
        tint = _rgba255(average)
        base_color = (
            int(round(base_color[0] * tint[0] / 255)),
            int(round(base_color[1] * tint[1] / 255)),
            int(round(base_color[2] * tint[2] / 255)),
            base_color[3],
        )
    alphas = [
        _attribute_value(
            mesh,
            _EFFECT_ALPHA_ATTRIBUTE,
            index,
            _attribute_value(mesh, _PATH_ALPHA_ATTRIBUTE, index, 1.0),
        )
        for index in indexes
    ]
    alpha = sum(alphas) / len(alphas)
    return (*base_color[:3], int(round(base_color[3] * alpha * opacity)))


def _polygon_record(
    mesh,
    polygon,
    world_points,
    *,
    image_display: bool,
    opacity: float,
):
    indexes = list(polygon.vertices)
    if len(indexes) < 3:
        return None
    points = tuple(
        (world_points[index][0], world_points[index][1])
        for index in indexes
    )
    z_value = sum(world_points[index][2] for index in indexes) / len(indexes)
    material_index = int(getattr(polygon, "material_index", 0))
    texture_path = (
        _material_texture_path(mesh, material_index)
        if image_display
        else None
    )
    uvs = _polygon_uvs(mesh, polygon, len(points)) if texture_path else ()
    return (
        (round(z_value, 7), material_index),
        _Polygon(
            points,
            _polygon_color(
                mesh,
                indexes,
                material_index,
                image_display=image_display,
                opacity=opacity,
            ),
            texture_path if uvs else None,
            uvs,
        ),
    )


def _mesh_polygons(
    obj,
    page_offset_mm: tuple[float, float],
    *,
    image_display: bool,
    opacity: float,
):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )
    if mesh is None:
        return [], {}
    try:
        world_points = _world_points(evaluated, mesh, page_offset_mm)
        groups = defaultdict(list)
        for polygon in mesh.polygons:
            record = _polygon_record(
                mesh,
                polygon,
                world_points,
                image_display=image_display,
                opacity=opacity,
            )
            if record is not None:
                key, exported = record
                groups[key].append(exported)
        return world_points, groups
    finally:
        evaluated.to_mesh_clear()


def _controller_hidden(controller) -> bool:
    from ..utils import gpencil

    layers = getattr(getattr(controller, "data", None), "layers", None)
    if layers is None:
        return False
    visible = []
    for layer in layers:
        try:
            visible.append(not gpencil.layer_effectively_hidden(layer))
        except Exception:  # noqa: BLE001
            visible.append(not bool(getattr(layer, "hide", False)))
    return bool(visible) and not any(visible)


def _display_objects(controller) -> list[bpy.types.Object]:
    from ..utils import effect_line_object, effect_line_path

    result = []
    display = effect_line_object.find_effect_display_object(controller)
    if display is not None:
        result.append(display)
    image_display = effect_line_path.find_effect_line_image_object(controller)
    if image_display is not None:
        result.append(image_display)
    return result


def _load_texture(Image, path: Path, cache: dict[Path, Any]):
    if path in cache:
        return cache[path]
    try:
        with Image.open(path) as opened:
            texture = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        texture = None
    cache[path] = texture
    return texture


def _tiled_texture(Image, source, uvs):
    min_u = math.floor(min(uv[0] for uv in uvs))
    max_u = math.ceil(max(uv[0] for uv in uvs))
    min_v = math.floor(min(uv[1] for uv in uvs))
    max_v = math.ceil(max(uv[1] for uv in uvs))
    tiles_u = max(1, max_u - min_u)
    tiles_v = max(1, max_v - min_v)
    if tiles_u * tiles_v > 4096:
        return None
    total_pixels = source.width * tiles_u * source.height * tiles_v
    if total_pixels > _MAX_TILED_TEXTURE_PIXELS:
        scale = math.sqrt(_MAX_TILED_TEXTURE_PIXELS / total_pixels)
        source = source.resize(
            (
                max(1, int(source.width * scale)),
                max(1, int(source.height * scale)),
            ),
            resample=Image.Resampling.LANCZOS,
        )
    tiled = Image.new(
        "RGBA",
        (source.width * tiles_u, source.height * tiles_v),
        (0, 0, 0, 0),
    )
    for row in range(tiles_v):
        for column in range(tiles_u):
            tiled.paste(source, (column * source.width, row * source.height))
    source_points = tuple(
        (
            (u - min_u) * source.width,
            (max_v - v) * source.height,
        )
        for u, v in uvs
    )
    return tiled, source_points


def _composite_triangle(
    Image,
    ImageDraw,
    target,
    source,
    dest_points,
    source_points,
) -> None:
    from . import export_image_path

    xs = [point[0] for point in dest_points]
    ys = [point[1] for point in dest_points]
    left = max(0, int(math.floor(min(xs))))
    top = max(0, int(math.floor(min(ys))))
    right = min(target.width, int(math.ceil(max(xs))))
    bottom = min(target.height, int(math.ceil(max(ys))))
    if right <= left or bottom <= top:
        return
    local_dest = [(x - left, y - top) for x, y in dest_points]
    coefficients = export_image_path._affine_from_points(
        local_dest,
        source_points,
    )
    if coefficients is None:
        return
    patch = source.transform(
        (right - left, bottom - top),
        Image.AFFINE,
        coefficients,
        resample=Image.BILINEAR,
    )
    mask = Image.new("L", patch.size, 0)
    ImageDraw.Draw(mask).polygon(local_dest, fill=255)
    patch.putalpha(Image.composite(patch.getchannel("A"), mask, mask))
    target.alpha_composite(patch, (left, top))


def _draw_textured_polygon(
    Image,
    ImageDraw,
    target,
    canvas,
    polygon,
    texture,
) -> bool:
    from . import export_image_path

    tiled = _tiled_texture(Image, texture, polygon.uvs)
    if tiled is None:
        return False
    source, source_points = tiled
    source = export_image_path._tinted(Image, source, polygon.color)
    dest_points = tuple(canvas.point_px(point) for point in polygon.points)
    for index in range(1, len(dest_points) - 1):
        triangle = (0, index, index + 1)
        _composite_triangle(
            Image,
            ImageDraw,
            target,
            source,
            [dest_points[item] for item in triangle],
            [source_points[item] for item in triangle],
        )
    return True


def _controller_geometry(
    controller,
    page_offset_mm: tuple[float, float],
):
    all_points: list[tuple[float, float]] = []
    grouped = defaultdict(list)
    opacity = _controller_opacity(controller)
    for object_index, display in enumerate(_display_objects(controller)):
        if getattr(display, "type", "") != "MESH":
            continue
        points, groups = _mesh_polygons(
            display,
            page_offset_mm,
            image_display=bool(
                str(display.get("bmanga_kind", "") or "") == "effect_line_image"
            ),
            opacity=(
                opacity
                if str(display.get("bmanga_kind", "") or "")
                == "effect_line_image"
                else 1.0
            ),
        )
        all_points.extend((point[0], point[1]) for point in points)
        for (z_value, material_index), polygons in groups.items():
            grouped[(object_index, z_value, material_index)].extend(polygons)
    return all_points, grouped


def _rasterize_controller(
    Image,
    ImageDraw,
    controller,
    page_offset_mm: tuple[float, float],
    canvas_size: tuple[int, int],
    dpi: int,
    texture_cache: dict[Path, Any],
):
    all_points, grouped = _controller_geometry(controller, page_offset_mm)
    canvas = _canvas_for_points(Image, all_points, canvas_size, dpi)
    if canvas is None:
        return None
    for key in sorted(grouped):
        group_image = Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(group_image)
        for polygon in grouped[key]:
            if polygon.color[3] <= 0:
                continue
            texture = (
                _load_texture(Image, polygon.texture_path, texture_cache)
                if polygon.texture_path is not None
                else None
            )
            if texture is not None and _draw_textured_polygon(
                Image,
                ImageDraw,
                group_image,
                canvas,
                polygon,
                texture,
            ):
                continue
            draw.polygon(
                [canvas.point_px(point) for point in polygon.points],
                fill=polygon.color,
            )
        canvas = _Canvas(
            Image.alpha_composite(canvas.image, group_image),
            canvas.left,
            canvas.top,
            canvas.canvas_height_px,
            canvas.dpi,
        )
    return canvas


def _effect_export_layer(
    *,
    controller,
    parent_key: str,
    page_offset_mm,
    canvas_size,
    dpi,
    texture_cache,
    ExportLayer,
    Image,
    ImageDraw,
    group_path_for_parent,
    stack_uid_for_object,
):
    canvas = _rasterize_controller(
        Image,
        ImageDraw,
        controller,
        page_offset_mm,
        canvas_size,
        dpi,
        texture_cache,
    )
    if canvas is None or canvas.image.getbbox() is None:
        return None
    return ExportLayer(
        str(controller.get("bmanga_title", "") or controller.name),
        canvas.image,
        canvas.left,
        canvas.top,
        group_path=group_path_for_parent(
            "coma" if ":" in parent_key else "page",
            parent_key,
            ("gp", "effects"),
        ),
        visible=True,
        opacity=255,
        blend_mode="normal",
        stack_uid=stack_uid_for_object(controller),
        stack_parent_key=str(
            controller.get("bmanga_folder_id", "") or parent_key
        ),
    )


def page_effect_mesh_layers(
    *,
    work,
    page,
    canvas_size: tuple[int, int],
    dpi: int,
    ExportLayer,
    Image,
    ImageDraw,
    page_offset_mm: tuple[float, float],
    group_path_for_parent: Callable,
    stack_uid_for_object: Callable,
) -> list:
    """現在ページに属する効果線表示Meshをレイヤー順付きで返す."""
    from ..utils import layer_object_model

    page_id = str(getattr(page, "id", "") or "")
    layers = []
    texture_cache: dict[Path, Any] = {}
    for controller in layer_object_model.iter_layer_objects():
        if layer_object_model.layer_kind(controller) != "effect":
            continue
        parent_key = layer_object_model.parent_key(controller)
        if (parent_key.split(":", 1)[0] if parent_key else "") != page_id:
            continue
        if _controller_hidden(controller):
            continue
        try:
            layer = _effect_export_layer(
                controller=controller,
                parent_key=parent_key,
                page_offset_mm=page_offset_mm,
                canvas_size=canvas_size,
                dpi=dpi,
                texture_cache=texture_cache,
                ExportLayer=ExportLayer,
                Image=Image,
                ImageDraw=ImageDraw,
                group_path_for_parent=group_path_for_parent,
                stack_uid_for_object=stack_uid_for_object,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "effect mesh export failed: %s",
                getattr(controller, "name", ""),
            )
            continue
        if layer is not None:
            layers.append(layer)
    return layers
