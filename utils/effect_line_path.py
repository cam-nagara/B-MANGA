"""効果線の基準パスと画像線表示."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import bpy
from mathutils import Vector

from . import layer_object_sync as los
from . import log
from . import material_opacity_mask
from . import object_naming as on
from .geom import m_to_mm, mm_to_m

_logger = log.get_logger(__name__)

BASE_PATH_KIND = "effect_base_path"
LINE_IMAGE_KIND = "effect_line_image"
BASE_PATH_ID_PREFIX = "effect_base_path_"
LINE_IMAGE_ID_PREFIX = "effect_line_image_"
BASE_PATH_DATA_PREFIX = "BManga_EffectBasePath_"
LINE_IMAGE_DATA_PREFIX = "BManga_EffectLineImage_"
LINE_IMAGE_MATERIAL_PREFIX = "BManga_EffectLineImage_"
PROP_EFFECT_CONTROLLER_ID = "bmanga_effect_controller_id"
PROP_EFFECT_LAYER_KEY = "bmanga_effect_layer_key"

_BASE_PATH_ROLES = {"line", "underlay", "white_outline_white", "white_outline_black"}
_IMAGE_LINE_ROLES = {"line", "white_outline_white", "white_outline_black"}
_MAX_IMAGE_LINE_FACES = 60000
_BASE_PATH_SYNCING = False


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))


def _controller_key(controller_obj: bpy.types.Object | None) -> str:
    if controller_obj is None:
        return ""
    return str(controller_obj.get(on.PROP_ID, "") or getattr(controller_obj, "name", "") or "")


def _base_path_id(controller_obj: bpy.types.Object | None) -> str:
    key = _controller_key(controller_obj)
    return f"{BASE_PATH_ID_PREFIX}{key}" if key else ""


def _line_image_id(controller_obj: bpy.types.Object | None) -> str:
    key = _controller_key(controller_obj)
    return f"{LINE_IMAGE_ID_PREFIX}{key}" if key else ""


def find_effect_base_path_object(controller_obj: bpy.types.Object | None) -> Optional[bpy.types.Object]:
    source_id = _base_path_id(controller_obj)
    return on.find_object_by_bmanga_id(source_id, kind=BASE_PATH_KIND) if source_id else None


def find_effect_line_image_object(controller_obj: bpy.types.Object | None) -> Optional[bpy.types.Object]:
    image_id = _line_image_id(controller_obj)
    return on.find_object_by_bmanga_id(image_id, kind=LINE_IMAGE_KIND) if image_id else None


def _parse_points_json(raw: object) -> list[tuple[float, float]]:
    try:
        data = json.loads(str(raw or ""))
    except Exception:  # noqa: BLE001
        return []
    points: list[tuple[float, float]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            point = (float(item[0]), float(item[1]))
        except Exception:  # noqa: BLE001
            continue
        if points and math.hypot(points[-1][0] - point[0], points[-1][1] - point[1]) <= 1.0e-6:
            continue
        points.append(point)
    return points


def _points_json(points: list[tuple[float, float]]) -> str:
    payload = [[round(float(x), 5), round(float(y), 5)] for x, y in points]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _line_role(stroke) -> str:
    return str(getattr(stroke, "role", "") or "line")


def _stroke_points_m(stroke) -> list[tuple[float, float, float]]:
    points = []
    for raw in getattr(stroke, "points_xyz", None) or []:
        try:
            points.append((float(raw[0]), float(raw[1]), float(raw[2])))
        except Exception:  # noqa: BLE001
            continue
    return points


def _first_line_points_mm(strokes) -> list[tuple[float, float]]:
    for stroke in strokes or ():
        if _line_role(stroke) not in _IMAGE_LINE_ROLES:
            continue
        points = _stroke_points_m(stroke)
        if len(points) >= 2:
            return [(m_to_mm(x), m_to_mm(y)) for x, y, _z in points]
    return []


def _link_to_controller_collections(obj: bpy.types.Object, controller_obj: bpy.types.Object) -> None:
    target_collections = list(getattr(controller_obj, "users_collection", []) or [])
    if not target_collections:
        target_collections = [bpy.context.scene.collection]
    for coll in target_collections:
        try:
            if obj.name not in coll.objects.keys():
                coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            pass
    for coll in list(getattr(obj, "users_collection", []) or []):
        if coll not in target_collections:
            try:
                coll.objects.unlink(obj)
            except Exception:  # noqa: BLE001
                pass


def _ensure_base_path_curve(controller_obj, layer, points_mm: list[tuple[float, float]]) -> Optional[bpy.types.Object]:
    source_id = _base_path_id(controller_obj)
    if not source_id or len(points_mm) < 2:
        return None
    obj = find_effect_base_path_object(controller_obj)
    curve = getattr(obj, "data", None) if obj is not None and getattr(obj, "type", "") == "CURVE" else None
    if curve is None:
        curve = bpy.data.curves.new(f"{BASE_PATH_DATA_PREFIX}{_safe_token(source_id)}", "CURVE")
    if obj is None or not _points_close(_read_curve_points_mm(controller_obj, obj), points_mm):
        _write_curve_points(controller_obj, curve, points_mm)
    if obj is None or getattr(obj, "type", "") != "CURVE":
        title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
        obj = bpy.data.objects.new(f"{title}_基準パス", curve)
    elif obj.data is not curve:
        obj.data = curve
    _stamp_base_path_identity(obj, controller_obj, layer)
    _link_to_controller_collections(obj, controller_obj)
    _copy_controller_transform(obj, controller_obj)
    obj.hide_viewport = False
    obj.hide_render = True
    obj.hide_select = False
    return obj


def _write_curve_points(controller_obj, curve: bpy.types.Curve, points_mm: list[tuple[float, float]]) -> None:
    curve.dimensions = "3D"
    curve.resolution_u = 12
    with los.suppress_sync():
        while len(curve.splines):
            curve.splines.remove(curve.splines[0])
        spline = curve.splines.new("POLY")
        spline.points.add(len(points_mm) - 1)
        for point, (x_mm, y_mm) in zip(spline.points, points_mm, strict=False):
            point.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0, 1.0)


def _stamp_base_path_identity(obj, controller_obj, layer) -> None:
    title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
    source_id = _base_path_id(controller_obj)
    on.stamp_identity(
        obj,
        kind=BASE_PATH_KIND,
        bmanga_id=source_id,
        title=f"{title}_基準パス",
        z_index=int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0),
        parent_key=str(controller_obj.get(on.PROP_PARENT_KEY, "") or ""),
        folder_id=str(controller_obj.get(on.PROP_FOLDER_ID, "") or ""),
        managed=False,
    )
    obj[PROP_EFFECT_CONTROLLER_ID] = _controller_key(controller_obj)
    obj[PROP_EFFECT_LAYER_KEY] = str(getattr(layer, "name", "") or "")
    try:
        on.assign_canonical_name(
            obj,
            "effect",
            int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0),
            "effect_base_path",
            f"{title}_基準パス",
        )
    except Exception:  # noqa: BLE001
        pass


def _copy_controller_transform(obj: bpy.types.Object, controller_obj: bpy.types.Object) -> None:
    with los.suppress_sync():
        try:
            if tuple(obj.location) != tuple(controller_obj.location):
                obj.location = tuple(controller_obj.location)
            if tuple(obj.rotation_euler) != tuple(controller_obj.rotation_euler):
                obj.rotation_euler = tuple(controller_obj.rotation_euler)
            if tuple(obj.scale) != tuple(controller_obj.scale):
                obj.scale = tuple(controller_obj.scale)
        except Exception:  # noqa: BLE001
            pass


def _points_close(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    if len(a) != len(b):
        return False
    for pa, pb in zip(a, b, strict=False):
        if math.hypot(float(pa[0]) - float(pb[0]), float(pa[1]) - float(pb[1])) > 1.0e-5:
            return False
    return True


def _read_curve_points_mm(controller_obj, obj: bpy.types.Object | None) -> list[tuple[float, float]]:
    if obj is None or getattr(obj, "type", "") != "CURVE":
        return []
    try:
        inv = controller_obj.matrix_world.inverted()
    except Exception:  # noqa: BLE001
        inv = None
    for spline in getattr(getattr(obj, "data", None), "splines", []) or []:
        points = _read_spline_points_mm(obj, inv, spline)
        if len(points) >= 2:
            return points
    return []


def _read_spline_points_mm(obj: bpy.types.Object, inv, spline) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    src_points = getattr(spline, "bezier_points", None) or getattr(spline, "points", None) or []
    for point in src_points:
        co = getattr(point, "co", None)
        if co is None:
            continue
        if len(co) >= 4:
            vec = Vector((float(co[0]) / max(float(co[3]), 1.0e-9), float(co[1]) / max(float(co[3]), 1.0e-9), float(co[2])))
        else:
            vec = Vector((float(co[0]), float(co[1]), float(co[2]) if len(co) > 2 else 0.0))
        try:
            local = (inv @ (obj.matrix_world @ vec)) if inv is not None else vec
            out.append((m_to_mm(float(local.x)), m_to_mm(float(local.y))))
        except Exception:  # noqa: BLE001
            continue
    return out


def sync_base_path_source(scene, controller_obj, layer, params_data: dict, strokes) -> bool:
    if not bool(params_data.get("base_path_enabled", False)):
        obj = find_effect_base_path_object(controller_obj)
        if obj is not None:
            obj.hide_viewport = True
            obj.hide_render = True
            obj.hide_select = True
        return False
    points = _read_curve_points_mm(controller_obj, find_effect_base_path_object(controller_obj))
    if len(points) < 2:
        points = _parse_points_json(params_data.get("base_path_points_json", ""))
    if len(points) < 2:
        points = _first_line_points_mm(strokes)
    if len(points) < 2:
        return False
    params_data["base_path_points_json"] = _points_json(points)
    obj = _ensure_base_path_curve(controller_obj, layer, points)
    if obj is not None:
        hidden = bool(getattr(layer, "hide", False))
        obj.hide_viewport = hidden
        obj.hide_select = hidden
    return True


def _path_profile(points_m: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points_m) < 2:
        return []
    sx, sy = points_m[0]
    ex, ey = points_m[-1]
    dx = ex - sx
    dy = ey - sy
    length = math.hypot(dx, dy)
    if length <= 1.0e-9:
        return []
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    profile: list[tuple[float, float]] = []
    for x, y in points_m:
        rel_x, rel_y = x - sx, y - sy
        t = max(0.0, min(1.0, (rel_x * ux + rel_y * uy) / length))
        offset_ratio = (rel_x * nx + rel_y * ny) / length
        profile.append((t, offset_ratio))
    return profile


def _cumulative(points: list[tuple[float, float, float]]) -> list[float]:
    total = [0.0]
    for index in range(1, len(points)):
        ax, ay, az = points[index - 1]
        bx, by, bz = points[index]
        total.append(total[-1] + math.sqrt((bx - ax) ** 2 + (by - ay) ** 2 + (bz - az) ** 2))
    return total


def _interp_values(values, fractions: list[float], original_fractions: list[float]) -> list[float] | None:
    if values is None:
        return None
    vals = [float(v) for v in values]
    if not vals:
        return None
    if len(vals) == 1 or len(original_fractions) <= 1:
        return [vals[0] for _t in fractions]
    out: list[float] = []
    for t in fractions:
        for i in range(1, len(original_fractions)):
            if t <= original_fractions[i] or i == len(original_fractions) - 1:
                span = max(1.0e-9, original_fractions[i] - original_fractions[i - 1])
                k = max(0.0, min(1.0, (t - original_fractions[i - 1]) / span))
                out.append(vals[i - 1] + (vals[i] - vals[i - 1]) * k)
                break
    return out


def _profile_at(profile: list[tuple[float, float]], t: float) -> float:
    if t <= profile[0][0]:
        return profile[0][1]
    for i in range(1, len(profile)):
        if t <= profile[i][0] or i == len(profile) - 1:
            t0, o0 = profile[i - 1]
            t1, o1 = profile[i]
            k = 0.0 if abs(t1 - t0) <= 1.0e-9 else (t - t0) / (t1 - t0)
            return o0 + (o1 - o0) * max(0.0, min(1.0, k))
    return profile[-1][1]


def _map_stroke_to_profile(stroke, profile: list[tuple[float, float]]):
    points = _stroke_points_m(stroke)
    if len(points) < 2:
        return stroke
    cum = _cumulative(points)
    total = cum[-1]
    if total <= 1.0e-9:
        return stroke
    original_fractions = [value / total for value in cum]
    fractions = sorted({round(t, 8) for t, _offset in profile} | {round(t, 8) for t in original_fractions})
    sx, sy, sz = points[0]
    ex, ey, ez = points[-1]
    ux, uy = (ex - sx) / total, (ey - sy) / total
    nx, ny = -uy, ux
    mapped = [
        (sx + ux * total * t + nx * total * _profile_at(profile, t), sy + uy * total * t + ny * total * _profile_at(profile, t), sz + (ez - sz) * t)
        for t in fractions
    ]
    return stroke.__class__(
        points_xyz=mapped,
        radius=getattr(stroke, "radius", 0.0),
        cyclic=bool(getattr(stroke, "cyclic", False)),
        radii=_interp_values(getattr(stroke, "radii", None), fractions, original_fractions),
        opacities=_interp_values(getattr(stroke, "opacities", None), fractions, original_fractions),
        role=getattr(stroke, "role", "line"),
        curve_type=getattr(stroke, "curve_type", "POLY"),
        bezier_smooth=bool(getattr(stroke, "bezier_smooth", False)),
        density_end=float(getattr(stroke, "density_end", 1.0) or 1.0),
        side=float(getattr(stroke, "side", 0.0) or 0.0),
    )


def apply_base_path_to_strokes(strokes, params_data: dict):
    if not bool(params_data.get("base_path_enabled", False)):
        return strokes
    points_mm = _parse_points_json(params_data.get("base_path_points_json", ""))
    profile = _path_profile([(mm_to_m(x), mm_to_m(y)) for x, y in points_mm])
    if not profile:
        return strokes
    return [
        _map_stroke_to_profile(stroke, profile)
        if _line_role(stroke) in _BASE_PATH_ROLES and not bool(getattr(stroke, "cyclic", False))
        else stroke
        for stroke in strokes or ()
    ]


def _image_path(params_data: dict) -> Path | None:
    raw = str(params_data.get("line_image_path", "") or "").strip()
    if not raw:
        return None
    path = Path(bpy.path.abspath(raw))
    return path if path.is_file() else None


def line_image_active(params_data: dict) -> bool:
    return _image_path(params_data) is not None


def solid_strokes_for_display(params_data: dict, strokes):
    if not line_image_active(params_data):
        return strokes
    return [stroke for stroke in strokes or () if _line_role(stroke) not in _IMAGE_LINE_ROLES]


def _load_image(params_data: dict) -> bpy.types.Image | None:
    path = _image_path(params_data)
    if path is None:
        return None
    try:
        image = bpy.data.images.load(str(path), check_existing=True)
        image.colorspace_settings.name = "sRGB"
        return image
    except Exception:  # noqa: BLE001
        _logger.warning("effect line image load failed: %s", path)
        return None


def _ensure_image_material(name: str, image: bpy.types.Image, opacity_percent: float, *, mask_info=None) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    mat.show_transparent_back = False
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    mix = nt.nodes.new("ShaderNodeMixShader")
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    emission = nt.nodes.new("ShaderNodeEmission")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.extension = "REPEAT"
    tex.interpolation = "Linear"
    alpha_socket = tex.outputs["Alpha"]
    if mask_info is not None:
        masked = material_opacity_mask.multiply_alpha_by_mask(
            nt,
            alpha_socket,
            mask_object=getattr(mask_info, "space_object", None),
            mask_image=getattr(mask_info, "image", None),
        )
        alpha_socket = masked if masked is not None else alpha_socket
    opacity = nt.nodes.new("ShaderNodeValue")
    opacity.outputs[0].default_value = max(0.0, min(1.0, float(opacity_percent) / 100.0))
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    nt.links.new(tex.outputs["Color"], emission.inputs["Color"])
    nt.links.new(alpha_socket, mul.inputs[0])
    nt.links.new(opacity.outputs[0], mul.inputs[1])
    nt.links.new(mul.outputs[0], mix.inputs["Fac"])
    nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    mat.diffuse_color = (1.0, 1.0, 1.0, max(0.0, min(1.0, float(opacity_percent) / 100.0)))
    return mat


def _path_lengths(points: list[tuple[float, float, float]]) -> tuple[list[float], float]:
    cum = _cumulative(points)
    return cum, cum[-1] if cum else 0.0


def _point_at_distance(points, cumulative, distance: float) -> tuple[float, float, float, float]:
    if len(points) < 2:
        return 0.0, 0.0, 0.0, 0.0
    if distance <= 0.0:
        a, b = points[0], points[1]
        return a[0], a[1], a[2], math.atan2(b[1] - a[1], b[0] - a[0])
    for i in range(1, len(points)):
        if distance <= cumulative[i] or i == len(points) - 1:
            span = max(1.0e-9, cumulative[i] - cumulative[i - 1])
            k = max(0.0, min(1.0, (distance - cumulative[i - 1]) / span))
            a, b = points[i - 1], points[i]
            return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k, math.atan2(b[1] - a[1], b[0] - a[0])
    a, b = points[-2], points[-1]
    return b[0], b[1], b[2], math.atan2(b[1] - a[1], b[0] - a[0])


def _stamp_angle(params_data: dict, path_angle: float) -> float:
    base = math.radians(float(params_data.get("line_image_angle_deg", 0.0) or 0.0))
    mode = str(params_data.get("line_image_stamp_angle_mode", "line") or "line")
    if mode == "line":
        return path_angle + base
    if mode == "object":
        obj = bpy.data.objects.get(str(params_data.get("line_image_stamp_angle_object_name", "") or ""))
        if obj is not None:
            return float(getattr(obj.rotation_euler, "z", 0.0) or 0.0) + base
    return base


def _append_stamp_mesh(verts, faces, uvs, stroke, params_data: dict, *, max_faces: int) -> None:
    points = _stroke_points_m(stroke)
    cumulative, total = _path_lengths(points)
    brush = mm_to_m(max(0.1, float(params_data.get("line_image_brush_size_mm", 3.0) or 3.0)))
    aspect = max(0.01, float(params_data.get("line_image_aspect_ratio", 1.0) or 1.0))
    spacing = max(mm_to_m(0.1), brush * max(1.0, float(params_data.get("line_image_spacing_percent", 100.0) or 100.0)) / 100.0)
    half_w, half_h = brush * aspect * 0.5, brush * 0.5
    corners = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
    face_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    distance = 0.0
    while True:
        if len(faces) >= max_faces:
            break
        x, y, z, path_angle = _point_at_distance(points, cumulative, distance)
        angle = _stamp_angle(params_data, path_angle)
        ca, sa = math.cos(angle), math.sin(angle)
        base = len(verts)
        for lx, ly in corners:
            verts.append((x + lx * ca - ly * sa, y + lx * sa + ly * ca, z))
        faces.append((base, base + 1, base + 2, base + 3))
        uvs.extend(face_uvs)
        next_distance = distance + spacing
        if next_distance < total:
            distance = next_distance
            continue
        if total > 0.0 and abs(distance - total) > spacing * 0.35:
            distance = total
            continue
        break


def _uv_rotated(u: float, v: float, angle: float, *, repeat: bool) -> tuple[float, float]:
    if abs(angle) <= 1.0e-7:
        return u, v
    tile = math.floor(u) if repeat else 0.0
    local_u = u - tile if repeat else u
    du, dv = local_u - 0.5, v - 0.5
    ca, sa = math.cos(angle), math.sin(angle)
    return du * ca - dv * sa + 0.5 + tile, du * sa + dv * ca + 0.5


def _append_ribbon_mesh(verts, faces, uvs, stroke, params_data: dict) -> None:
    points = _stroke_points_m(stroke)
    cumulative, total = _path_lengths(points)
    brush = mm_to_m(max(0.1, float(params_data.get("line_image_brush_size_mm", 3.0) or 3.0)))
    aspect = max(0.01, float(params_data.get("line_image_aspect_ratio", 1.0) or 1.0))
    spacing = max(mm_to_m(0.1), brush * aspect * max(1.0, float(params_data.get("line_image_spacing_percent", 100.0) or 100.0)) / 100.0)
    stretch = str(params_data.get("line_image_ribbon_repeat_mode", "repeat") or "repeat") == "stretch"
    angle = math.radians(float(params_data.get("line_image_angle_deg", 0.0) or 0.0))
    base = len(verts)
    for i, (x, y, z) in enumerate(points):
        tx, ty = _ribbon_tangent(points, i)
        width = brush * _width_factor(stroke, i)
        nx, ny = -ty, tx
        verts.append((x + nx * width * 0.5, y + ny * width * 0.5, z))
        verts.append((x - nx * width * 0.5, y - ny * width * 0.5, z))
        u = cumulative[i] / total if stretch and total > 1.0e-9 else cumulative[i] / spacing
        uvs.append(_uv_rotated(u, 1.0, angle, repeat=not stretch))
        uvs.append(_uv_rotated(u, 0.0, angle, repeat=not stretch))
    for i in range(len(points) - 1):
        start = base + i * 2
        faces.append((start, start + 1, start + 3, start + 2))


def _ribbon_tangent(points: list[tuple[float, float, float]], index: int) -> tuple[float, float]:
    if index <= 0:
        a, b = points[0], points[1]
    elif index >= len(points) - 1:
        a, b = points[-2], points[-1]
    else:
        a, b = points[index - 1], points[index + 1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return (1.0, 0.0) if length <= 1.0e-9 else (dx / length, dy / length)


def _width_factor(stroke, index: int) -> float:
    base = max(1.0e-9, float(getattr(stroke, "radius", 0.0) or 0.0))
    radii = getattr(stroke, "radii", None)
    if radii is not None and 0 <= index < len(radii):
        return max(0.0, float(radii[index]) / base)
    return 1.0


def _assign_mesh(mesh: bpy.types.Mesh, verts, faces, uvs) -> None:
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    if len(uvs) == len(verts):
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                vertex_index = mesh.loops[loop_index].vertex_index
                if 0 <= vertex_index < len(uvs):
                    uv_layer.data[loop_index].uv = uvs[vertex_index]
        mesh.update()
        return
    uv_index = 0
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            if uv_index < len(uvs):
                uv_layer.data[loop_index].uv = uvs[uv_index]
            uv_index += 1
    mesh.update()


def _build_image_mesh(mesh: bpy.types.Mesh, params_data: dict, strokes) -> bool:
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    uvs: list[tuple[float, float]] = []
    mode = str(params_data.get("line_image_draw_mode", "ribbon") or "ribbon")
    for stroke in strokes or ():
        if _line_role(stroke) not in _IMAGE_LINE_ROLES or bool(getattr(stroke, "cyclic", False)):
            continue
        if len(_stroke_points_m(stroke)) < 2:
            continue
        if mode == "stamp":
            _append_stamp_mesh(verts, faces, uvs, stroke, params_data, max_faces=_MAX_IMAGE_LINE_FACES)
        else:
            _append_ribbon_mesh(verts, faces, uvs, stroke, params_data)
        if len(faces) >= _MAX_IMAGE_LINE_FACES:
            break
    _assign_mesh(mesh, verts, faces, uvs)
    return bool(faces)


def sync_effect_line_image_object(
    *,
    scene: bpy.types.Scene,
    controller_obj: bpy.types.Object,
    params_data: dict,
    strokes,
    visible: bool = True,
) -> Optional[bpy.types.Object]:
    image = _load_image(params_data)
    if image is None:
        delete_effect_line_image_object(controller_obj)
        return None
    image_id = _line_image_id(controller_obj)
    mesh = bpy.data.meshes.get(f"{LINE_IMAGE_DATA_PREFIX}{_safe_token(image_id)}")
    if mesh is None:
        mesh = bpy.data.meshes.new(f"{LINE_IMAGE_DATA_PREFIX}{_safe_token(image_id)}")
    if not _build_image_mesh(mesh, params_data, strokes):
        delete_effect_line_image_object(controller_obj)
        return None
    obj = find_effect_line_image_object(controller_obj)
    if obj is None or getattr(obj, "type", "") != "MESH":
        obj = bpy.data.objects.new(f"{controller_obj.name}_画像線", mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    _stamp_line_image_identity(obj, controller_obj)
    _link_to_controller_collections(obj, controller_obj)
    _copy_controller_transform(obj, controller_obj)
    _apply_image_material(scene, obj, controller_obj, image, params_data)
    obj.hide_viewport = not bool(visible)
    obj.hide_render = not bool(visible)
    obj.hide_select = True
    return obj


def _stamp_line_image_identity(obj: bpy.types.Object, controller_obj: bpy.types.Object) -> None:
    title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
    image_id = _line_image_id(controller_obj)
    on.stamp_identity(
        obj,
        kind=LINE_IMAGE_KIND,
        bmanga_id=image_id,
        title=f"{title}_画像線",
        z_index=int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0),
        parent_key=str(controller_obj.get(on.PROP_PARENT_KEY, "") or ""),
        folder_id=str(controller_obj.get(on.PROP_FOLDER_ID, "") or ""),
        managed=False,
    )
    obj[PROP_EFFECT_CONTROLLER_ID] = _controller_key(controller_obj)
    try:
        on.assign_canonical_name(
            obj,
            "effect",
            int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0),
            "effect_line_image",
            f"{title}_画像線",
        )
    except Exception:  # noqa: BLE001
        pass


def _apply_image_material(scene, obj, controller_obj, image, params_data: dict) -> None:
    mask_info = None
    parent_key = str(controller_obj.get(on.PROP_PARENT_KEY, "") or "")
    try:
        from . import coma_content_mask

        mask_info = coma_content_mask.ensure_viewport_mask_for_parent(scene, getattr(scene, "bmanga_work", None), parent_key)
    except Exception:  # noqa: BLE001
        pass
    mat = _ensure_image_material(
        f"{LINE_IMAGE_MATERIAL_PREFIX}{_safe_token(_line_image_id(controller_obj))}",
        image,
        float(params_data.get("opacity", 100.0) or 100.0),
        mask_info=mask_info,
    )
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    elif obj.data.materials[0] is not mat:
        obj.data.materials[0] = mat


def delete_effect_line_image_object(controller_obj: bpy.types.Object | None) -> None:
    _delete_object(find_effect_line_image_object(controller_obj))


def delete_effect_base_path_object(controller_obj: bpy.types.Object | None) -> None:
    _delete_object(find_effect_base_path_object(controller_obj))


def delete_effect_line_helpers(controller_obj: bpy.types.Object | None) -> None:
    delete_effect_line_image_object(controller_obj)
    delete_effect_base_path_object(controller_obj)


def _delete_object(obj: bpy.types.Object | None) -> None:
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return
    try:
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
    except Exception:  # noqa: BLE001
        pass


def sync_effect_line_helpers_transform(controller_obj: bpy.types.Object | None) -> None:
    if controller_obj is None:
        return
    for obj in (find_effect_line_image_object(controller_obj), find_effect_base_path_object(controller_obj)):
        if obj is None:
            continue
        _link_to_controller_collections(obj, controller_obj)
        obj[on.PROP_PARENT_KEY] = str(controller_obj.get(on.PROP_PARENT_KEY, "") or "")
        obj[on.PROP_FOLDER_ID] = str(controller_obj.get(on.PROP_FOLDER_ID, "") or "")
        obj[on.PROP_Z_INDEX] = int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0)
        _copy_controller_transform(obj, controller_obj)


def sync_from_base_path_object(scene: bpy.types.Scene, obj: bpy.types.Object | None) -> bool:
    global _BASE_PATH_SYNCING
    if _BASE_PATH_SYNCING or scene is None or obj is None:
        return False
    if str(obj.get(on.PROP_KIND, "") or "") != BASE_PATH_KIND:
        return False
    controller = on.find_object_by_bmanga_id(str(obj.get(PROP_EFFECT_CONTROLLER_ID, "") or ""), kind="effect")
    if controller is None:
        return False
    points = _read_curve_points_mm(controller, obj)
    if len(points) < 2:
        return False
    try:
        from ..operators import effect_line_op
        from ..utils import layer_stack as layer_stack_utils

        layer_key = str(obj.get(PROP_EFFECT_LAYER_KEY, "") or "")
        layer = layer_stack_utils._find_gp_layer_by_key(getattr(controller.data, "layers", None), layer_key)
        if layer is None:
            return False
        bounds = effect_line_op.effect_layer_bounds(controller, layer)
        if bounds is None:
            return False
        meta = effect_line_op._effect_meta(controller)
        entry = dict(meta.get(effect_line_op._layer_meta_key(layer), {}) or {})
        params_data = dict(entry.get("params", {}) or {})
        points_json = _points_json(points)
        if (
            bool(params_data.get("base_path_enabled", False))
            and str(params_data.get("base_path_points_json", "") or "") == points_json
        ):
            return False
        params_data["base_path_enabled"] = True
        params_data["base_path_points_json"] = points_json
        entry["params"] = params_data
        meta[effect_line_op._layer_meta_key(layer)] = entry
        _BASE_PATH_SYNCING = True
        with los.suppress_sync():
            effect_line_op._write_effect_meta(controller, meta)
            effect_line_op._write_effect_strokes(
                bpy.context,
                controller,
                layer,
                bounds,
                center_xy_mm=effect_line_op.effect_layer_center(controller, layer, bounds),
            )
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("effect base path sync failed")
        return False
    finally:
        _BASE_PATH_SYNCING = False
