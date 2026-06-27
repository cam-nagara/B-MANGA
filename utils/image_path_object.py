"""パターンカーブレイヤーの実体同期."""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import object_preserve
from . import path_content
from .geom import m_to_mm, mm_to_m
from .image_real_object import entry_page_offset_mm, page_for_entry

_logger = log.get_logger(__name__)

IMAGE_PATH_OBJECT_NAME_PREFIX = "image_path_"
IMAGE_PATH_MESH_NAME_PREFIX = "image_path_mesh_"
IMAGE_PATH_MATERIAL_NAME_PREFIX = "BManga_ImagePath_"
IMAGE_PATH_Z_BASE = 330
_AUTO_SYNC_SUSPEND_DEPTH = 0


@contextmanager
def suspend_auto_sync():
    global _AUTO_SYNC_SUSPEND_DEPTH
    _AUTO_SYNC_SUSPEND_DEPTH += 1
    try:
        yield
    finally:
        _AUTO_SYNC_SUSPEND_DEPTH = max(0, _AUTO_SYNC_SUSPEND_DEPTH - 1)


def auto_sync_suspended() -> bool:
    return _AUTO_SYNC_SUSPEND_DEPTH > 0


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))


def _object_name(image_path_id: str) -> str:
    return f"{IMAGE_PATH_OBJECT_NAME_PREFIX}{_safe_token(image_path_id)}"


def _mesh_name(image_path_id: str) -> str:
    return f"{IMAGE_PATH_MESH_NAME_PREFIX}{_safe_token(image_path_id)}"


def _material_name(image_path_id: str) -> str:
    return f"{IMAGE_PATH_MATERIAL_NAME_PREFIX}{_safe_token(image_path_id)}"


def _ensure_parent_collection(scene: bpy.types.Scene, parent_kind: str, parent_key: str) -> None:
    from . import outliner_model as _om

    if parent_kind == "coma" and ":" in parent_key:
        page_id, coma_id = parent_key.split(":", 1)
        _om.ensure_coma_collection(scene, page_id, coma_id)
    elif parent_kind == "page" and parent_key:
        _om.ensure_page_collection(scene, parent_key)


def _resolve_parent_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder = folder_id or str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if parent_kind == "coma" and parent_key:
        return "coma", parent_key, entry_folder
    if parent_kind == "folder":
        folder_key = entry_folder or parent_key
        if folder_key:
            return "folder", folder_key, folder_key
    return "page", parent_key or str(getattr(page, "id", "") or ""), entry_folder


def _image_path_z_index(scene, image_path_id: str) -> int:
    coll = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    if coll is not None:
        for i, entry in enumerate(coll):
            if str(getattr(entry, "id", "") or "") == image_path_id:
                return IMAGE_PATH_Z_BASE + (i + 1) * 10
    return IMAGE_PATH_Z_BASE


def _parse_points(entry) -> list[tuple[float, float]]:
    raw = str(getattr(entry, "path_points_json", "") or "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    points: list[tuple[float, float]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            x = float(item[0])
            y = float(item[1])
        except (TypeError, ValueError):
            continue
        if points and (abs(points[-1][0] - x) < 1e-6 and abs(points[-1][1] - y) < 1e-6):
            continue
        points.append((x, y))
    return points


def _path_lengths(points: list[tuple[float, float]]) -> tuple[list[float], float]:
    cumulative = [0.0]
    total = 0.0
    for i in range(1, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        total += math.hypot(dx, dy)
        cumulative.append(total)
    return cumulative, total


def _points_center(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5


def translate_entry_points(entry, dx_mm: float, dy_mm: float) -> bool:
    points = _parse_points(entry)
    if not points:
        return False
    moved = [[float(x) + float(dx_mm), float(y) + float(dy_mm)] for x, y in points]
    entry.path_points_json = json.dumps(moved, ensure_ascii=False, separators=(",", ":"))
    return True


def _interpolate_at(
    points: list[tuple[float, float]],
    cumulative: list[float],
    distance: float,
) -> tuple[float, float, float]:
    if len(points) < 2:
        return 0.0, 0.0, 0.0
    if distance <= 0.0:
        p0, p1 = points[0], points[1]
        return p0[0], p0[1], math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    for i in range(1, len(points)):
        if distance <= cumulative[i] or i == len(points) - 1:
            seg_len = max(1e-9, cumulative[i] - cumulative[i - 1])
            t = max(0.0, min(1.0, (distance - cumulative[i - 1]) / seg_len))
            x = points[i - 1][0] + (points[i][0] - points[i - 1][0]) * t
            y = points[i - 1][1] + (points[i][1] - points[i - 1][1]) * t
            angle = math.atan2(points[i][1] - points[i - 1][1], points[i][0] - points[i - 1][0])
            return x, y, angle
    p0, p1 = points[-2], points[-1]
    return p1[0], p1[1], math.atan2(p1[1] - p0[1], p1[0] - p0[0])


def _uv_rotated(u: float, v: float, angle_rad: float, *, repeat: bool) -> tuple[float, float]:
    if abs(angle_rad) < 1e-7:
        return u, v
    tile = math.floor(u) if repeat else 0.0
    local_u = u - tile if repeat else u
    du = local_u - 0.5
    dv = v - 0.5
    ca = math.cos(angle_rad)
    sa = math.sin(angle_rad)
    ru = du * ca - dv * sa + 0.5
    rv = du * sa + dv * ca + 0.5
    return ru + tile if repeat else ru, rv


def _stamp_angle(entry, path_angle: float) -> float:
    base = math.radians(float(getattr(entry, "image_angle_deg", 0.0) or 0.0))
    mode = str(getattr(entry, "stamp_angle_mode", "line") or "line")
    if mode == "line":
        return path_angle + base
    if mode == "object":
        obj_name = str(getattr(entry, "stamp_angle_object_name", "") or "")
        obj = bpy.data.objects.get(obj_name) if obj_name else None
        if obj is not None:
            return float(getattr(obj.rotation_euler, "z", 0.0) or 0.0) + base
    return base


def _build_stamp_mesh(
    mesh: bpy.types.Mesh,
    entry,
    points: list[tuple[float, float]],
    center: tuple[float, float],
) -> None:
    cumulative, total = _path_lengths(points)
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

    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    uvs: list[tuple[float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    cx, cy = center
    source = str(getattr(entry, "content_source", "image") or "image")
    base_shape = path_content.unit_shape_points(
        getattr(entry, "shape_kind", "circle"),
        sides=int(getattr(entry, "shape_sides", 6) or 6),
    )
    base_corners = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
    face_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for distance in distances:
        x, y, path_angle = _interpolate_at(points, cumulative, distance)
        profile = path_content.inout_profile_value(entry, distance, total)
        scale = path_content.size_factor(entry, profile)
        rgba = path_content.color_for_path_distance(entry, distance, total)
        angle = _stamp_angle(entry, path_angle)
        ca = math.cos(angle)
        sa = math.sin(angle)
        start = len(verts)
        shape = base_shape if source == "shape" else base_corners
        for ux, uy in shape:
            lx = ux * brush * aspect * scale
            ly = uy * brush * scale
            vx = x + lx * ca - ly * sa - cx
            vy = y + lx * sa + ly * ca - cy
            verts.append((mm_to_m(vx), mm_to_m(vy), 0.0))
            colors.append(rgba)
        faces.append(tuple(range(start, start + len(shape))))
        uvs.extend(face_uvs if source != "shape" else [(0.0, 0.0)] * len(shape))
    _assign_mesh(mesh, verts, faces, uvs, colors)


def _ribbon_tangent(points: list[tuple[float, float]], index: int) -> tuple[float, float]:
    if index <= 0:
        dx = points[1][0] - points[0][0]
        dy = points[1][1] - points[0][1]
    elif index >= len(points) - 1:
        dx = points[-1][0] - points[-2][0]
        dy = points[-1][1] - points[-2][1]
    else:
        dx = points[index + 1][0] - points[index - 1][0]
        dy = points[index + 1][1] - points[index - 1][1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 1.0, 0.0
    return dx / length, dy / length


def _build_ribbon_mesh(
    mesh: bpy.types.Mesh,
    entry,
    points: list[tuple[float, float]],
    center: tuple[float, float],
) -> None:
    cumulative, total = _path_lengths(points)
    brush = max(0.1, float(getattr(entry, "brush_size_mm", 10.0) or 10.0))
    aspect = max(0.01, float(getattr(entry, "aspect_ratio", 1.0) or 1.0))
    spacing = max(0.1, brush * aspect * max(1.0, float(getattr(entry, "spacing_percent", 100.0) or 100.0)) / 100.0)
    half = brush * 0.5
    stretch = str(getattr(entry, "ribbon_repeat_mode", "repeat") or "repeat") == "stretch"
    angle = math.radians(float(getattr(entry, "image_angle_deg", 0.0) or 0.0))

    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    uvs: list[tuple[float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    cx, cy = center
    for i, (x, y) in enumerate(points):
        profile = path_content.inout_profile_value(entry, cumulative[i], total)
        local_half = half * path_content.size_factor(entry, profile)
        rgba = path_content.color_for_path_distance(entry, cumulative[i], total)
        tx, ty = _ribbon_tangent(points, i)
        nx, ny = -ty, tx
        left = (x + nx * local_half - cx, y + ny * local_half - cy)
        right = (x - nx * local_half - cx, y - ny * local_half - cy)
        verts.append((mm_to_m(left[0]), mm_to_m(left[1]), 0.0))
        verts.append((mm_to_m(right[0]), mm_to_m(right[1]), 0.0))
        colors.extend([rgba, rgba])
        if stretch:
            u = cumulative[i] / total if total > 1e-9 else 0.0
        else:
            u = cumulative[i] / spacing
        uvs.append(_uv_rotated(u, 1.0, angle, repeat=not stretch))
        uvs.append(_uv_rotated(u, 0.0, angle, repeat=not stretch))
    for i in range(len(points) - 1):
        start = i * 2
        faces.append((start, start + 1, start + 3, start + 2))
    _assign_mesh(mesh, verts, faces, uvs, colors)


def _assign_mesh(
    mesh: bpy.types.Mesh,
    verts: list[tuple[float, float, float]],
    faces: list[tuple[int, ...]],
    uvs: list[tuple[float, float]],
    colors: list[tuple[float, float, float, float]] | None = None,
) -> None:
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    if len(uvs) == len(verts):
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                vertex_index = mesh.loops[loop_index].vertex_index
                if 0 <= vertex_index < len(uvs):
                    uv_layer.data[loop_index].uv = uvs[vertex_index]
    else:
        uv_index = 0
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                if uv_index < len(uvs):
                    uv_layer.data[loop_index].uv = uvs[uv_index]
                uv_index += 1
    path_content.write_color_attribute(mesh, colors)
    mesh.update()


def _load_image(entry) -> Optional[bpy.types.Image]:
    if str(getattr(entry, "content_source", "image") or "image") != "image":
        return None
    filepath = str(getattr(entry, "filepath", "") or "")
    if not filepath:
        return None
    abs_path = Path(bpy.path.abspath(filepath))
    if not abs_path.is_file():
        return None
    try:
        image = bpy.data.images.load(str(abs_path), check_existing=True)
        try:
            image.colorspace_settings.name = "sRGB"
        except Exception:  # noqa: BLE001
            pass
        return image
    except Exception:  # noqa: BLE001
        _logger.debug("image path image load failed: %s", abs_path, exc_info=True)
        return None


def _ensure_material(
    name: str,
    image: Optional[bpy.types.Image],
    opacity: float,
    *,
    mask_info=None,
    fallback_alpha: float = 1.0,
) -> bpy.types.Material:
    return path_content.ensure_material(
        name,
        image,
        opacity,
        mask_info=mask_info,
        fallback_alpha=fallback_alpha,
    )


def _remove_object(obj: bpy.types.Object) -> None:
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("image path object removal failed")
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass


def ensure_image_path_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    if scene is None or entry is None:
        return None
    image_path_id = str(getattr(entry, "id", "") or "")
    if not image_path_id:
        return None

    points = _parse_points(entry)
    mesh = bpy.data.meshes.get(_mesh_name(image_path_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(_mesh_name(image_path_id))

    if len(points) >= 2:
        center = _points_center(points)
        source = str(getattr(entry, "content_source", "image") or "image")
        if source == "image" and str(getattr(entry, "draw_mode", "stamp") or "stamp") == "ribbon":
            _build_ribbon_mesh(mesh, entry, points, center)
        else:
            _build_stamp_mesh(mesh, entry, points, center)
    else:
        center = (0.0, 0.0)
        _assign_mesh(mesh, [], [], [])

    parent_kind, parent_key, stamp_folder = _resolve_parent_for_entry(entry, page, folder_id)
    mask_info = None
    if parent_kind == "coma" and parent_key and ":" in parent_key:
        try:
            from . import coma_content_mask
            work = getattr(scene, "bmanga_work", None)
            mask_info = coma_content_mask.ensure_viewport_mask_for_parent(scene, work, parent_key)
        except Exception:  # noqa: BLE001
            pass

    mat = _ensure_material(
        _material_name(image_path_id),
        _load_image(entry),
        float(getattr(entry, "opacity", 100.0) or 100.0),
        mask_info=mask_info,
        fallback_alpha=1.0 if str(getattr(entry, "content_source", "image") or "image") == "shape" else 0.0,
    )
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj_name = _object_name(image_path_id)
    obj = on.find_object_by_bmanga_id(image_path_id, kind="image_path")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    if object_preserve.is_preserved(obj):
        obj = None
    if obj is not None and obj.type != "MESH":
        object_preserve.preserve_object(obj, "古いパターンカーブ実体を保持")
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    work = getattr(scene, "bmanga_work", None)
    ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
    obj.location.x = mm_to_m(center[0] + ox_mm)
    obj.location.y = mm_to_m(center[1] + oy_mm)
    obj.rotation_euler[2] = 0.0

    _ensure_parent_collection(scene, parent_kind, parent_key)
    los.stamp_layer_object(
        obj,
        kind="image_path",
        bmanga_id=image_path_id,
        title=str(getattr(entry, "title", "") or image_path_id),
        z_index=_image_path_z_index(scene, image_path_id),
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=stamp_folder,
        scene=scene,
        apply_page_offset=False,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    obj.hide_select = False
    return obj


def find_image_path_entry(scene, image_path_id: str):
    coll = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    if coll is None:
        return None
    for entry in coll:
        if str(getattr(entry, "id", "") or "") == image_path_id:
            return entry
    return None


def cleanup_orphan_image_path_objects(scene: bpy.types.Scene) -> int:
    coll = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    valid = {str(getattr(entry, "id", "") or "") for entry in coll or []}
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "image_path":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid in valid:
            continue
        object_preserve.preserve_object(obj, "作品データにないパターンカーブ実体を保持")
        removed += 1
    return removed


def remove_image_path_object(image_path_id: str) -> bool:
    if not image_path_id:
        return False
    removed = False
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "image_path":
            continue
        if str(obj.get(on.PROP_ID, "") or "") != image_path_id:
            continue
        _remove_object(obj)
        removed = True
    return removed


def sync_entry_points_from_object(scene: bpy.types.Scene, obj: bpy.types.Object | None) -> bool:
    if scene is None or obj is None or object_preserve.is_preserved(obj):
        return False
    if str(obj.get(on.PROP_KIND, "") or "") != "image_path":
        return False
    image_path_id = str(obj.get(on.PROP_ID, "") or "")
    entry = find_image_path_entry(scene, image_path_id)
    if entry is None:
        return False
    points = _parse_points(entry)
    if len(points) < 2:
        return False
    work = getattr(scene, "bmanga_work", None)
    page = page_for_entry(scene, work, entry)
    ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
    cx, cy = _points_center(points)
    dx = m_to_mm(obj.location.x) - (cx + ox_mm)
    dy = m_to_mm(obj.location.y) - (cy + oy_mm)
    if abs(dx) <= 1e-5 and abs(dy) <= 1e-5:
        return False
    with suspend_auto_sync():
        if not translate_entry_points(entry, dx, dy):
            return False
    on_image_path_entry_changed(entry)
    return True


def sync_all_image_path_objects(scene: bpy.types.Scene, work) -> int:
    if scene is None or work is None:
        return 0
    coll = getattr(scene, "bmanga_image_path_layers", None)
    if coll is None:
        return 0
    count = 0
    for entry in coll:
        page = page_for_entry(scene, work, entry)
        if ensure_image_path_object(scene=scene, entry=entry, page=page) is not None:
            count += 1
    cleanup_orphan_image_path_objects(scene)
    return count


def on_image_path_entry_changed(entry) -> bool:
    if auto_sync_suspended():
        return False
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if scene is None or work is None or entry is None:
        return False
    image_path_id = str(getattr(entry, "id", "") or "")
    target_ptr = 0
    try:
        target_ptr = int(entry.as_pointer())
    except Exception:  # noqa: BLE001
        pass
    coll = getattr(scene, "bmanga_image_path_layers", None) or []
    for candidate in coll:
        same_id = bool(image_path_id) and str(getattr(candidate, "id", "") or "") == image_path_id
        try:
            same_ptr = bool(target_ptr) and int(candidate.as_pointer()) == target_ptr
        except Exception:  # noqa: BLE001
            same_ptr = False
        if same_id or same_ptr:
            return sync_all_image_path_objects(scene, work) > 0
    return False
