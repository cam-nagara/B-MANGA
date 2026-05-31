"""Asset Browser thumbnail generation for B-Name assets."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

from .geom import m_to_mm
from . import log

_logger = log.get_logger(__name__)

ASSET_PREVIEW_SIZE = 128


def set_collection_asset_preview(
    coll: bpy.types.Collection,
    *,
    payload: dict | None = None,
) -> None:
    try:
        pixels = _asset_preview_pixels(
            payload=payload,
            objects=list(getattr(coll, "objects", []) or []),
        )
        preview = coll.preview_ensure()
        preview.image_size = (ASSET_PREVIEW_SIZE, ASSET_PREVIEW_SIZE)
        preview.image_pixels_float = pixels
        preview.icon_size = (ASSET_PREVIEW_SIZE, ASSET_PREVIEW_SIZE)
        preview.icon_pixels_float = pixels
        _load_custom_preview_image(coll, pixels)
    except Exception:  # noqa: BLE001
        _logger.exception("asset preview generation failed")


def _load_custom_preview_image(coll: bpy.types.Collection, pixels: list[float]) -> None:
    image = None
    path = ""
    try:
        path, image = _write_preview_png(pixels)
        with bpy.context.temp_override(id=coll):
            bpy.ops.ed.lib_id_load_custom_preview(filepath=path)
    except Exception:  # noqa: BLE001
        _logger.exception("asset custom preview load failed")
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:  # noqa: BLE001
                pass
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def patch_external_library_preview(
    blend_path: str | Path,
    collection_name: str,
    *,
    payload: dict | None = None,
    objects: list[bpy.types.Object] | None = None,
) -> None:
    png_path = ""
    script_path = ""
    image = None
    try:
        pixels = _asset_preview_pixels(payload=payload, objects=list(objects or []))
        png_path, image = _write_preview_png(pixels)
        script_path = _write_preview_patch_script()
        binary = str(getattr(bpy.app, "binary_path", "") or "")
        if not binary:
            return
        result = subprocess.run(
            [
                binary,
                "--background",
                "--factory-startup",
                "--python",
                script_path,
                "--",
                str(blend_path),
                str(collection_name),
                png_path,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            _logger.warning(
                "external asset preview patch failed: %s",
                (result.stderr or result.stdout or "").strip(),
            )
    except Exception:  # noqa: BLE001
        _logger.exception("external asset preview patch failed")
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:  # noqa: BLE001
                pass
        for path in (png_path, script_path):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass


def _write_preview_png(pixels: list[float]) -> tuple[str, bpy.types.Image]:
    handle = tempfile.NamedTemporaryFile(prefix="bname_asset_preview_", suffix=".png", delete=False)
    path = handle.name
    handle.close()
    image = bpy.data.images.new("BNameAssetPreview", ASSET_PREVIEW_SIZE, ASSET_PREVIEW_SIZE, alpha=True)
    image.pixels = pixels
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    return path, image


def _write_preview_patch_script() -> str:
    code = r'''
from __future__ import annotations

import sys
import bpy


def main() -> None:
    args = sys.argv
    if "--" not in args:
        raise SystemExit(2)
    blend_path, collection_name, png_path = args[args.index("--") + 1:args.index("--") + 4]
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    coll = bpy.data.collections.get(collection_name)
    if coll is None:
        raise SystemExit(3)
    if coll.asset_data is None:
        coll.asset_mark()
    with bpy.context.temp_override(id=coll):
        bpy.ops.ed.lib_id_load_custom_preview(filepath=png_path)
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)


if __name__ == "__main__":
    main()
'''
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="bname_asset_preview_patch_",
        suffix=".py",
        delete=False,
    )
    try:
        handle.write(code)
        return handle.name
    finally:
        handle.close()


def _asset_preview_pixels(
    *,
    payload: dict | None,
    objects: list[bpy.types.Object],
) -> list[float]:
    captured = _capture_objects_preview_pixels(objects)
    if captured is not None:
        return captured
    size = ASSET_PREVIEW_SIZE
    canvas = _preview_canvas(size)
    entries = [
        entry
        for entry in (payload or {}).get("entries", []) or []
        if isinstance(entry, dict)
    ]
    boxes = [_preview_bounds_for_entry(entry) for entry in entries]
    if not any(boxes):
        boxes = [_preview_bounds_for_object(obj) for obj in objects]
    transform = _preview_transform([box for box in boxes if box is not None], size)
    _draw_preview_background(canvas, size)
    if entries:
        for entry in entries:
            box = _preview_bounds_for_entry(entry)
            if box is None:
                continue
            rect = _map_preview_rect(box, transform)
            kind = str(entry.get("kind", "") or "")
            if kind == "balloon":
                _draw_preview_balloon(canvas, size, rect, entry)
            elif kind == "text":
                _draw_preview_text(canvas, size, rect)
            elif kind == "effect":
                _draw_preview_effect(canvas, size, rect)
            else:
                _draw_preview_rect(
                    canvas,
                    size,
                    rect,
                    (0.35, 0.38, 0.42, 1.0),
                    fill=False,
                )
    else:
        for box in boxes:
            if box is not None:
                _draw_preview_rect(
                    canvas,
                    size,
                    _map_preview_rect(box, transform),
                    (0.15, 0.16, 0.18, 1.0),
                    fill=False,
                )
    return canvas


def _capture_objects_preview_pixels(objects: list[bpy.types.Object]) -> list[float] | None:
    preview_objects = [obj for obj in objects if obj is not None]
    if not preview_objects:
        return None
    scene = None
    camera = None
    camera_data = None
    try:
        scene = bpy.data.scenes.new("BNameAssetPreviewScene")
        _setup_preview_render_scene(scene)
        linked: list[bpy.types.Object] = []
        for obj in preview_objects:
            try:
                scene.collection.objects.link(obj)
                linked.append(obj)
            except RuntimeError:
                linked.append(obj)
            except Exception:  # noqa: BLE001
                continue
        if not linked:
            return None
        _refresh_scene(scene)
        bounds = _world_bounds_for_objects(linked)
        if bounds is None:
            return None
        camera_data = bpy.data.cameras.new("BNameAssetPreviewCamera")
        camera = bpy.data.objects.new("BNameAssetPreviewCamera", camera_data)
        scene.collection.objects.link(camera)
        _position_preview_camera(camera, camera_data, bounds)
        scene.camera = camera
        _refresh_scene(scene)
        try:
            bpy.ops.render.render(write_still=False, scene=scene.name)
        except TypeError:
            _render_with_temporary_scene(scene)
        image = bpy.data.images.get("Render Result")
        if image is None:
            return None
        pixels = list(image.pixels)
        expected = ASSET_PREVIEW_SIZE * ASSET_PREVIEW_SIZE * 4
        if len(pixels) != expected or not _captured_pixels_have_content(pixels):
            return None
        return pixels
    except Exception:  # noqa: BLE001
        _logger.exception("asset preview capture failed")
        return None
    finally:
        if camera is not None:
            try:
                bpy.data.objects.remove(camera, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
        if camera_data is not None and camera_data.users == 0:
            try:
                bpy.data.cameras.remove(camera_data)
            except Exception:  # noqa: BLE001
                pass
        if scene is not None:
            try:
                bpy.data.scenes.remove(scene)
            except Exception:  # noqa: BLE001
                pass


def _setup_preview_render_scene(scene: bpy.types.Scene) -> None:
    scene.render.resolution_x = ASSET_PREVIEW_SIZE
    scene.render.resolution_y = ASSET_PREVIEW_SIZE
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    for engine in ("BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except Exception:  # noqa: BLE001
            continue
    display = getattr(scene, "display", None)
    shading = getattr(display, "shading", None)
    if shading is not None:
        for attr, value in (
            ("background_type", "VIEWPORT"),
            ("background_color", (0.90, 0.90, 0.90)),
            ("color_type", "MATERIAL"),
            ("light", "STUDIO"),
        ):
            try:
                setattr(shading, attr, value)
            except Exception:  # noqa: BLE001
                pass
    if scene.world is None:
        try:
            scene.world = bpy.data.worlds.new("BNameAssetPreviewWorld")
        except Exception:  # noqa: BLE001
            scene.world = None
    if scene.world is not None:
        try:
            scene.world.color = (0.90, 0.90, 0.90)
        except Exception:  # noqa: BLE001
            pass
    for attr, value in (
        ("view_transform", "Standard"),
        ("look", "None"),
        ("exposure", 0.0),
        ("gamma", 1.0),
    ):
        try:
            setattr(scene.view_settings, attr, value)
        except Exception:  # noqa: BLE001
            pass


def _refresh_scene(scene: bpy.types.Scene) -> None:
    try:
        scene.frame_set(scene.frame_current)
    except Exception:  # noqa: BLE001
        pass
    try:
        scene.view_layers[0].update()
    except Exception:  # noqa: BLE001
        pass


def _render_with_temporary_scene(scene: bpy.types.Scene) -> None:
    window = getattr(bpy.context, "window", None)
    previous_scene = getattr(window, "scene", None) if window is not None else None
    try:
        if window is not None:
            window.scene = scene
        bpy.ops.render.render(write_still=False)
    finally:
        if window is not None and previous_scene is not None:
            try:
                window.scene = previous_scene
            except Exception:  # noqa: BLE001
                pass


def _world_bounds_for_objects(
    objects: list[bpy.types.Object],
) -> tuple[float, float, float, float, float, float] | None:
    points: list[Vector] = []
    for obj in objects:
        try:
            corners = list(getattr(obj, "bound_box", []) or [])
        except Exception:  # noqa: BLE001
            corners = []
        valid_corners = [
            corner
            for corner in corners
            if any(abs(float(component)) > 1.0e-8 for component in corner)
        ]
        if valid_corners:
            points.extend(obj.matrix_world @ Vector(corner) for corner in valid_corners)
            continue
        try:
            points.append(obj.matrix_world.translation.copy())
        except Exception:  # noqa: BLE001
            pass
    if not points:
        return None
    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_y = min(point.y for point in points)
    max_y = max(point.y for point in points)
    min_z = min(point.z for point in points)
    max_z = max(point.z for point in points)
    pad = max(max_x - min_x, max_y - min_y, 0.01) * 0.08
    return min_x - pad, max_x + pad, min_y - pad, max_y + pad, min_z, max_z


def _position_preview_camera(
    camera: bpy.types.Object,
    camera_data: bpy.types.Camera,
    bounds: tuple[float, float, float, float, float, float],
) -> None:
    min_x, max_x, min_y, max_y, min_z, max_z = bounds
    span_x = max(0.01, max_x - min_x)
    span_y = max(0.01, max_y - min_y)
    span_z = max(0.01, max_z - min_z)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    camera.location = (center_x, center_y, max_z + max(span_x, span_y, span_z) * 2.5 + 1.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = max(span_x, span_y) * 1.08
    camera_data.clip_start = 0.001
    camera_data.clip_end = max(10.0, camera.location.z - min_z + 10.0)


def _captured_pixels_have_content(pixels: list[float]) -> bool:
    if not pixels:
        return False
    values = []
    dark_pixels = 0
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i:i + 4]
        if a > 0.5:
            values.extend((r, g, b))
            if max(r, g, b) < 0.45:
                dark_pixels += 1
    if dark_pixels >= 8:
        return True
    return bool(values) and max(values) - min(values) > 0.18


def _preview_canvas(size: int) -> list[float]:
    return [1.0, 1.0, 1.0, 1.0] * (size * size)


def _draw_preview_background(canvas: list[float], size: int) -> None:
    for y in range(size):
        for x in range(size):
            shade = 0.94 if ((x // 12) + (y // 12)) % 2 == 0 else 0.88
            _set_preview_pixel(canvas, size, x, y, (shade, shade, shade, 1.0))
    _draw_preview_rect(
        canvas,
        size,
        (6, 6, size - 7, size - 7),
        (0.78, 0.82, 0.85, 1.0),
        fill=False,
    )


def _preview_bounds_for_entry(entry: dict) -> tuple[float, float, float, float] | None:
    bounds = entry.get("bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
        data = entry.get("data")
        if not isinstance(data, dict):
            return None
        bounds = (
            data.get("x_mm", 0.0),
            data.get("y_mm", 0.0),
            data.get("width_mm", 30.0),
            data.get("height_mm", 20.0),
        )
    try:
        x, y, w, h = (
            float(bounds[0]),
            float(bounds[1]),
            float(bounds[2]),
            float(bounds[3]),
        )
    except Exception:  # noqa: BLE001
        return None
    if w <= 0.0 or h <= 0.0:
        return None
    return x, y, w, h


def _preview_bounds_for_object(obj: bpy.types.Object) -> tuple[float, float, float, float] | None:
    try:
        x = m_to_mm(float(obj.location.x))
        y = m_to_mm(float(obj.location.y))
    except Exception:  # noqa: BLE001
        return None
    return x - 15.0, y - 15.0, 30.0, 30.0


def _preview_transform(
    boxes: list[tuple[float, float, float, float]],
    size: int,
) -> tuple[float, float, float]:
    if not boxes:
        return 1.0, 0.0, 0.0
    min_x = min(x for x, _y, _w, _h in boxes)
    min_y = min(y for _x, y, _w, _h in boxes)
    max_x = max(x + w for x, _y, w, _h in boxes)
    max_y = max(y + h for _x, y, _w, h in boxes)
    span_x = max(1.0, max_x - min_x)
    span_y = max(1.0, max_y - min_y)
    margin = 18.0
    scale = min((size - margin * 2.0) / span_x, (size - margin * 2.0) / span_y)
    offset_x = (size - span_x * scale) * 0.5 - min_x * scale
    offset_y = (size - span_y * scale) * 0.5 - min_y * scale
    return scale, offset_x, offset_y


def _map_preview_rect(
    box: tuple[float, float, float, float],
    transform: tuple[float, float, float],
) -> tuple[int, int, int, int]:
    scale, offset_x, offset_y = transform
    x, y, w, h = box
    left = int(round(x * scale + offset_x))
    right = int(round((x + w) * scale + offset_x))
    bottom = int(round(y * scale + offset_y))
    top = int(round((y + h) * scale + offset_y))
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def _draw_preview_balloon(
    canvas: list[float],
    size: int,
    rect: tuple[int, int, int, int],
    entry: dict,
) -> None:
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    shape = str(data.get("shape", "ellipse") or "ellipse")
    if shape in {"rect", "octagon"}:
        _draw_preview_rect(canvas, size, rect, (1.0, 1.0, 1.0, 1.0), fill=True)
        _draw_preview_rect(canvas, size, rect, (0.05, 0.05, 0.05, 1.0), fill=False)
    else:
        _draw_preview_ellipse(canvas, size, rect, (1.0, 1.0, 1.0, 1.0), fill=True)
        _draw_preview_ellipse(canvas, size, rect, (0.05, 0.05, 0.05, 1.0), fill=False)


def _draw_preview_text(canvas: list[float], size: int, rect: tuple[int, int, int, int]) -> None:
    left, bottom, right, top = rect
    height = max(1, top - bottom)
    count = max(2, min(5, height // 7))
    for i in range(count):
        y = bottom + int(round((i + 1) * height / (count + 1)))
        _draw_preview_line(canvas, size, left + 2, y, right - 2, y, (0.08, 0.08, 0.08, 1.0))


def _draw_preview_effect(canvas: list[float], size: int, rect: tuple[int, int, int, int]) -> None:
    left, bottom, right, top = rect
    cx = (left + right) // 2
    cy = (bottom + top) // 2
    for i in range(18):
        t = i / 18.0
        if i % 4 == 0:
            x = left + int((right - left) * t)
            y = top
        elif i % 4 == 1:
            x = right
            y = bottom + int((top - bottom) * t)
        elif i % 4 == 2:
            x = right - int((right - left) * t)
            y = bottom
        else:
            x = left
            y = top - int((top - bottom) * t)
        _draw_preview_line(canvas, size, cx, cy, x, y, (0.1, 0.1, 0.1, 1.0))


def _draw_preview_rect(
    canvas: list[float],
    size: int,
    rect: tuple[int, int, int, int],
    color: tuple[float, float, float, float],
    *,
    fill: bool,
) -> None:
    left, bottom, right, top = _clamp_preview_rect(rect, size)
    if fill:
        for y in range(bottom, top + 1):
            for x in range(left, right + 1):
                _set_preview_pixel(canvas, size, x, y, color)
        return
    _draw_preview_line(canvas, size, left, bottom, right, bottom, color)
    _draw_preview_line(canvas, size, right, bottom, right, top, color)
    _draw_preview_line(canvas, size, right, top, left, top, color)
    _draw_preview_line(canvas, size, left, top, left, bottom, color)


def _draw_preview_ellipse(
    canvas: list[float],
    size: int,
    rect: tuple[int, int, int, int],
    color: tuple[float, float, float, float],
    *,
    fill: bool,
) -> None:
    left, bottom, right, top = _clamp_preview_rect(rect, size)
    cx = (left + right) * 0.5
    cy = (bottom + top) * 0.5
    rx = max(1.0, (right - left) * 0.5)
    ry = max(1.0, (top - bottom) * 0.5)
    for y in range(bottom, top + 1):
        for x in range(left, right + 1):
            value = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            if (fill and value <= 1.0) or (not fill and 0.86 <= value <= 1.16):
                _set_preview_pixel(canvas, size, x, y, color)


def _draw_preview_line(
    canvas: list[float],
    size: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[float, float, float, float],
) -> None:
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        _set_preview_pixel(canvas, size, x, y, color)
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def _clamp_preview_rect(rect: tuple[int, int, int, int], size: int) -> tuple[int, int, int, int]:
    left, bottom, right, top = rect
    return (
        max(0, min(size - 1, left)),
        max(0, min(size - 1, bottom)),
        max(0, min(size - 1, right)),
        max(0, min(size - 1, top)),
    )


def _set_preview_pixel(
    canvas: list[float],
    size: int,
    x: int,
    y: int,
    color: tuple[float, float, float, float],
) -> None:
    if not (0 <= x < size and 0 <= y < size):
        return
    idx = (y * size + x) * 4
    canvas[idx:idx + 4] = color
