"""コマ枠線の実オブジェクト同期."""

from __future__ import annotations

import json
import math
from typing import Optional, Sequence

import bpy

from . import border_geom
from . import coma_z_order
from . import log
from . import object_naming as on
from . import outliner_model as om
from . import spread_merge_geometry
from .geom import mm_to_m

_logger = log.get_logger(__name__)

COMA_BORDER_NAME_PREFIX = "coma_border_"
COMA_BORDER_CURVE_PREFIX = "coma_border_curve_"
COMA_BORDER_MESH_PREFIX = "coma_border_mesh_"
COMA_BORDER_MATERIAL_PREFIX = "BManga_ComaBorder_"
COMA_WHITE_MARGIN_NAME_PREFIX = "coma_white_margin_"
COMA_WHITE_MARGIN_MESH_PREFIX = "coma_white_margin_mesh_"
COMA_WHITE_MARGIN_MATERIAL_PREFIX = "BManga_ComaWhiteMargin_"
COMA_WHITE_MARGIN_Z_M = (
    coma_z_order.COMA_PLANE_BASE_Z_M + coma_z_order.COMA_WHITE_MARGIN_OFFSET_Z_M
)
COMA_BORDER_Z_M = coma_z_order.COMA_PLANE_BASE_Z_M + coma_z_order.COMA_BORDER_OFFSET_Z_M
OUTSIDE_PAGE_ID = "outside"
_CURVE_PROFILE_RADIUS_FROM_WIDTH_MM = 0.0007071067811865476

PROP_COMA_BORDER_KIND = "bmanga_coma_border_kind"
PROP_COMA_BORDER_OWNER_ID = "bmanga_coma_border_owner_id"
PROP_COMA_WHITE_MARGIN_KIND = "bmanga_coma_white_margin_kind"
PROP_COMA_WHITE_MARGIN_OWNER_ID = "bmanga_coma_white_margin_owner_id"


def _owner_id(page_id: str, coma_id: str) -> str:
    return f"{page_id}:{coma_id}"


def _page_id_for_coma(page) -> str:
    page_id = str(getattr(page, "id", "") or "") if page is not None else ""
    return page_id or OUTSIDE_PAGE_ID


def _coma_collection(scene, page, page_id: str, coma_id: str, title: str):
    if page is None or page_id == OUTSIDE_PAGE_ID:
        return om.ensure_outside_collection(scene)
    return om.ensure_coma_collection(scene, page_id, coma_id, title)


def _curve_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_BORDER_CURVE_PREFIX}{page_id}_{coma_id}"


def _mesh_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_BORDER_MESH_PREFIX}{page_id}_{coma_id}"


def _object_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_BORDER_NAME_PREFIX}{page_id}_{coma_id}"


def _material_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_BORDER_MATERIAL_PREFIX}{page_id}_{coma_id}"


def _rgba_from_border(coma) -> tuple[float, float, float, float]:
    border = getattr(coma, "border", None)
    color = getattr(border, "color", (0.0, 0.0, 0.0, 1.0)) if border is not None else (0.0, 0.0, 0.0, 1.0)
    try:
        return (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]),
        )
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 0.0, 1.0)


def _ensure_material(page_id: str, coma_id: str, coma) -> bpy.types.Material:
    mat = bpy.data.materials.get(_material_name(page_id, coma_id))
    if mat is None:
        mat = bpy.data.materials.new(_material_name(page_id, coma_id))
    color = _rgba_from_border(coma)
    mat.diffuse_color = color
    mat.use_nodes = True
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (180, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-60, 0)
    try:
        emission.inputs["Color"].default_value = color
        emission.inputs["Strength"].default_value = 1.0
        nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("coma border material setup failed")
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _ensure_color_material(name: str, rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (180, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-60, 0)
    try:
        emission.inputs["Color"].default_value = rgba
        emission.inputs["Strength"].default_value = 1.0
        nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("coma color material setup failed")
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _ensure_soft_material(
    name: str,
    rgba: tuple[float, float, float, float],
    *,
    dither: bool = False,
) -> bpy.types.Material:
    """半透明のソフトマテリアル (旧方式の輪郭ぼかしハロー用).

    Emission を Transparent と Mix し、Solid 表示でもレンダーでも alpha が
    効くようにする。``rgba[3]`` を不透明度として扱う。 ``dither=True`` で
    半透明を網点状のディザ (ハッシュ) で解決する。
    """
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    r, g, b, a = (float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3]))
    mat.diffuse_color = (r, g, b, a)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = False
        # EEVEE Next: 半透明の解決方法。 DITHERED = 網点状ハッシュ、
        # BLENDED = 通常のアルファ合成。
        mat.surface_render_method = "DITHERED" if dither else "BLENDED"
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (0, 120)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (0, -120)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (150, 0)
    try:
        emission.inputs["Color"].default_value = (r, g, b, 1.0)
        emission.inputs["Strength"].default_value = 1.0
        # fac=0 → 1番目(Transparent)、fac=1 → 2番目(Emission)。alpha をそのまま使う。
        mix.inputs["Fac"].default_value = max(0.0, min(1.0, a))
        nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
        nt.links.new(emission.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("coma border soft material setup failed")
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _brush_halo_groups(
    path: list[tuple[float, float]],
    base_width_mm: float,
    color: tuple[float, float, float, float],
    blur_amount: float,
) -> list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]]:
    """旧方式の輪郭ぼかし: 芯 + 外側に広がる半透明ハローのグループ列を返す."""
    blur = max(0.0, min(1.0, float(blur_amount)))
    base_w = max(0.0, float(base_width_mm))
    r, g, b, a = (float(color[0]), float(color[1]), float(color[2]), float(color[3]))
    groups: list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]] = []
    # 芯 (不透明・通常幅)
    groups.append(([list(path)], base_w, (r, g, b, a), "brush_core"))
    if base_w <= 0.0 or blur <= 0.0:
        return groups
    # 外周の最後の帯が濃いと、ボケ終端に細いグレー線として見える。
    # 段階数を増やし、外側ほど急速に薄くして終端の段差を目視上消す。
    base_layers = 8 + int(round(blur * 12.0))  # 8..20
    width_layers = int(round(max(0.0, base_w - 0.5) * 6.0))
    layers = min(48, base_layers + width_layers)
    max_extra = base_w * (0.6 + 4.0 * blur)
    for i in range(1, layers + 1):
        f = i / float(layers)
        width_i = base_w + max_extra * f
        alpha_i = a * ((1.0 - f) ** 2.2) * 0.50
        if alpha_i <= 0.00035:
            continue
        groups.append(([list(path)], width_i, (r, g, b, alpha_i), "brush_halo"))
    # 外側 (幅広・薄い) を先に描き、芯を最後に描く
    groups.reverse()
    return groups


def _white_margin_ring(
    inner_pts: list[tuple[float, float]],
    outer_pts: list[tuple[float, float]],
) -> Optional[tuple[list[tuple[float, float, float]], list[tuple[int, int, int, int]]]]:
    """内側/外側の閉ループから一定幅のリング Mesh (頂点/面) を作る.

    内外を同じ角分割数の角処理輪郭で生成すれば点数が 1:1 対応するため、
    対応点同士を四角形で帯状に繋いで丸角のフチを作る。
    """
    n = len(inner_pts)
    if n < 3 or len(outer_pts) != n:
        return None
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in inner_pts]
    verts += [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in outer_pts]
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    return verts, faces


def _offset_loop(
    outline: list[tuple[float, float]],
    offset_mm: float,
) -> Optional[list[tuple[float, float]]]:
    offset = float(offset_mm)
    if abs(offset) <= 1.0e-6:
        return list(outline)
    loops = border_geom.stroke_loops_mm(outline, abs(offset) * 2.0)
    if loops is None:
        return None
    return loops[0] if offset > 0.0 else loops[1]


def _append_white_margin_band(
    verts: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int, int]],
    material_indices: list[int],
    inner_pts: list[tuple[float, float]],
    outer_pts: list[tuple[float, float]],
    material_index: int,
) -> bool:
    ring = _white_margin_ring(inner_pts, outer_pts)
    if ring is None:
        return False
    ring_verts, ring_faces = ring
    base = len(verts)
    verts.extend(ring_verts)
    faces.extend(tuple(base + index for index in face) for face in ring_faces)
    material_indices.extend([int(material_index)] * len(ring_faces))
    return True


def _rect_points(coma) -> list[tuple[float, float]]:
    w = max(0.001, float(getattr(coma, "rect_width_mm", 50.0) or 50.0))
    h = max(0.001, float(getattr(coma, "rect_height_mm", 50.0) or 50.0))
    return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]


def _polygon_points(coma) -> list[tuple[float, float]]:
    vertices = list(getattr(coma, "vertices", []) or [])
    if len(vertices) < 3:
        return _rect_points(coma)
    return [(float(v.x_mm), float(v.y_mm)) for v in vertices]


def _outline_points(coma) -> list[tuple[float, float]]:
    if str(getattr(coma, "shape_type", "rect") or "rect") == "rect":
        base = _rect_points(coma)
    else:
        base = _polygon_points(coma)
    border = getattr(coma, "border", None)
    try:
        return border_geom.styled_closed_path_mm(
            base,
            getattr(border, "corner_type", "square"),
            float(getattr(border, "corner_radius_mm", 0.0) or 0.0),
        )
    except Exception:  # noqa: BLE001
        return base


def _merged_border_polys(coma) -> list[list[tuple[float, float]]]:
    if str(getattr(coma, "merged_border_mode", "shape") or "shape") != "separate":
        return []
    raw = str(getattr(coma, "merged_border_polygons_json", "") or "")
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(loaded, list):
        return []
    polys: list[list[tuple[float, float]]] = []
    for item in loaded:
        if not isinstance(item, list):
            continue
        poly: list[tuple[float, float]] = []
        for pair in item:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            try:
                poly.append((float(pair[0]), float(pair[1])))
            except (TypeError, ValueError):
                continue
        if len(poly) >= 3:
            polys.append(poly)
    return polys


def _styled_paths_for_polys(coma, polys: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    border = getattr(coma, "border", None)
    paths: list[list[tuple[float, float]]] = []
    for poly in polys:
        try:
            paths.append(
                border_geom.styled_closed_path_mm(
                    poly,
                    getattr(border, "corner_type", "square"),
                    float(getattr(border, "corner_radius_mm", 0.0) or 0.0),
                )
            )
        except Exception:  # noqa: BLE001
            paths.append(list(poly))
    return [path for path in paths if len(path) >= 3]


def _separate_border_groups(
    coma,
    polys: list[list[tuple[float, float]]],
) -> list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]]:
    border = getattr(coma, "border", None)
    base_style = str(getattr(border, "style", "solid") or "solid")
    base_width = max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0))
    color = _rgba_from_border(coma)
    if base_width <= 0.0:
        return []
    if base_style == "solid":
        paths = _styled_paths_for_polys(coma, polys)
        return [(paths, base_width, color, "solid_closed")] if paths else []
    if base_style == "brush":
        groups: list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]] = []
        blur = float(getattr(border, "blur_amount", 0.5) or 0.0)
        for path in _styled_paths_for_polys(coma, polys):
            groups.extend(_brush_halo_groups(path, base_width, color, blur))
        return groups
    grouped: list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]] = []
    for poly in polys:
        for i in range(len(poly)):
            paths = _styled_segment_paths(poly[i], poly[(i + 1) % len(poly)], style=base_style, width_mm=base_width)
            if paths:
                grouped.append((paths, base_width, color, base_style))
    return grouped


def _rebuild_curve(
    curve: bpy.types.Curve,
    paths_mm: Sequence[Sequence[tuple[float, float]]],
    width_mm: float,
    *,
    cyclic: bool = True,
) -> None:
    curve.dimensions = "3D"
    while len(curve.splines):
        try:
            curve.splines.remove(curve.splines[0])
        except Exception:  # noqa: BLE001
            break
    for points_mm in paths_mm:
        if len(points_mm) < 2:
            continue
        spline = curve.splines.new(type="POLY")
        spline.points.add(len(points_mm) - 1)
        for point, (x_mm, y_mm) in zip(spline.points, points_mm, strict=False):
            point.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0, 1.0)
        spline.use_cyclic_u = cyclic and len(points_mm) >= 3
    curve.bevel_depth = max(0.0, float(width_mm)) * _CURVE_PROFILE_RADIUS_FROM_WIDTH_MM
    curve.bevel_resolution = 1
    curve.resolution_u = 1


def _rebuild_mesh_band(
    mesh: bpy.types.Mesh,
    paths_mm: Sequence[Sequence[tuple[float, float]]],
    width_mm: float,
) -> None:
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    for points_mm in paths_mm:
        loops = border_geom.stroke_loops_mm(points_mm, width_mm)
        if loops is None:
            continue
        outer, inner = loops
        if len(outer) < 3 or len(inner) != len(outer):
            continue
        base = len(verts)
        verts.extend((mm_to_m(x), mm_to_m(y), 0.0) for x, y in outer)
        verts.extend((mm_to_m(x), mm_to_m(y), 0.0) for x, y in inner)
        count = len(outer)
        faces.extend((base + i, base + (i + 1) % count, base + count + (i + 1) % count, base + count + i) for i in range(count))
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()


def _page_index(work, page) -> int:
    page_id = str(getattr(page, "id", "") or "")
    for i, candidate in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(candidate, "id", "") or "") == page_id:
            return i
    return -1


def _page_index_full_work(work, page) -> tuple[int, object]:
    """完全な work からページインデックスを解決する."""
    from ..core.work import get_work as _get_work
    full_work = _get_work(bpy.context)
    if full_work is not None and getattr(full_work, "loaded", False):
        idx = _page_index(full_work, page)
        if idx >= 0:
            return idx, full_work
    idx = _page_index(work, page)
    return idx, work


def _set_location(obj: bpy.types.Object, scene, work, page, coma) -> None:
    page_ox = 0.0
    page_oy = 0.0
    page_i, offset_work = _page_index_full_work(work, page)
    if page_i >= 0 and scene is not None:
        try:
            from . import page_grid

            page_ox, page_oy = page_grid.page_total_offset_mm(offset_work, scene, page_i)
        except Exception:  # noqa: BLE001
            _logger.exception("coma border page offset failed")
    local_x = 0.0
    local_y = 0.0
    if str(getattr(coma, "shape_type", "rect") or "rect") == "rect":
        local_x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        local_y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
    obj.location.x = mm_to_m(page_ox + local_x)
    obj.location.y = mm_to_m(page_oy + local_y)
    obj.location.z = coma_z_order.border_z(coma)


def _base_poly(coma) -> list[tuple[float, float]]:
    if str(getattr(coma, "shape_type", "rect") or "rect") == "rect":
        return _rect_points(coma)
    return _polygon_points(coma)


def _edge_settings(coma, edge_index: int, point_count: int):
    border = getattr(coma, "border", None)
    color = _rgba_from_border(coma)
    width = max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0))
    style = str(getattr(border, "style", "solid") or "solid")
    visible = bool(getattr(border, "visible", True))
    return visible, style, width, color


def _styled_segment_paths(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    style: str,
    width_mm: float,
) -> list[list[tuple[float, float]]]:
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length <= 1.0e-6:
        return []
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    if style == "double":
        offset = max(width_mm * 1.5, 0.6)
        return [
            [(x1 + nx * offset, y1 + ny * offset), (x2 + nx * offset, y2 + ny * offset)],
            [(x1 - nx * offset, y1 - ny * offset), (x2 - nx * offset, y2 - ny * offset)],
        ]
    if style not in {"dashed", "dotted"}:
        return [[start, end]]
    dash = max(width_mm * (1.2 if style == "dotted" else 6.0), 0.4 if style == "dotted" else 3.0)
    gap = max(width_mm * (2.4 if style == "dotted" else 3.0), 0.8 if style == "dotted" else 1.5)
    paths: list[list[tuple[float, float]]] = []
    pos = 0.0
    while pos < length:
        end_pos = min(length, pos + dash)
        if end_pos > pos:
            paths.append([
                (x1 + ux * pos, y1 + uy * pos),
                (x1 + ux * end_pos, y1 + uy * end_pos),
            ])
        pos = end_pos + gap
    return paths


def _spread_basic_frame_info(work, page, coma):
    try:
        return spread_merge_geometry.basic_frame_info(work, page, coma)
    except Exception:  # noqa: BLE001
        return "", None


def _spread_basic_frame_side(work, page, coma) -> str:
    side, _combined_rect = _spread_basic_frame_info(work, page, coma)
    return side


def _spread_basic_frame_groups(
    coma,
    side: str,
    combined_rect=None,
    *,
    style: str,
    width_mm: float,
    color: tuple[float, float, float, float],
) -> list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]]:
    if side not in {"left", "right"} or width_mm <= 0.0:
        return []
    if side == "right" or combined_rect is None:
        return []
    local_x = float(combined_rect.x) - float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
    local_y = float(combined_rect.y) - float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
    local_w = max(0.001, float(combined_rect.width))
    local_h = max(0.001, float(combined_rect.height))
    base = [
        (local_x, local_y),
        (local_x + local_w, local_y),
        (local_x + local_w, local_y + local_h),
        (local_x, local_y + local_h),
    ]
    draw_style = "solid" if style == "brush" else style
    paths: list[list[tuple[float, float]]] = []
    for edge_index, start in enumerate(base):
        end = base[(edge_index + 1) % len(base)]
        paths.extend(_styled_segment_paths(start, end, style=draw_style, width_mm=width_mm))
    if not paths:
        return []
    return [(paths, width_mm, color, "spread_basic_frame")]


def _border_paths_by_material(
    coma,
    *,
    work=None,
    page=None,
    spread_basic_frame_side: str = "",
) -> list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]]:
    border = getattr(coma, "border", None)
    if border is None or not bool(getattr(border, "visible", True)):
        return []
    separate_polys = _merged_border_polys(coma)
    if separate_polys:
        return _separate_border_groups(coma, separate_polys)
    base = _base_poly(coma)
    if len(base) < 2:
        return []
    base_style = str(getattr(border, "style", "solid") or "solid")
    base_width = max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0))
    if spread_basic_frame_side:
        side, combined_rect = spread_basic_frame_side, _spread_basic_frame_info(work, page, coma)[1]
    else:
        side, combined_rect = _spread_basic_frame_info(work, page, coma)
    if side:
        return _spread_basic_frame_groups(
            coma,
            side,
            combined_rect,
            style=base_style,
            width_mm=base_width,
            color=_rgba_from_border(coma),
        )
    if base_style == "solid":
        path = _outline_points(coma)
        return [([path], base_width, _rgba_from_border(coma), "solid_closed")]
    if base_style == "brush":
        path = _outline_points(coma)
        blur = float(getattr(border, "blur_amount", 0.5) or 0.0)
        return _brush_halo_groups(path, base_width, _rgba_from_border(coma), blur)
    grouped: list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]] = []
    for i in range(len(base)):
        visible, style, width, color = _edge_settings(coma, i, len(base))
        if not visible or width <= 0.0:
            continue
        paths = _styled_segment_paths(base[i], base[(i + 1) % len(base)], style=style, width_mm=width)
        if paths:
            grouped.append((paths, width, color, style))
    return grouped


def _has_edge_override(coma) -> bool:
    return False


def _uses_brush_texture(coma) -> bool:
    border = getattr(coma, "border", None)
    if border is None or _has_edge_override(coma):
        return False
    return (
        bool(getattr(border, "visible", True))
        and str(getattr(border, "style", "solid") or "solid") == "brush"
        and max(0.0, float(getattr(border, "width_mm", 0.0) or 0.0)) > 0.0
    )


def _remove_related_border_objects(page_id: str, coma_id: str, keep_names: set[str]) -> None:
    prefix = _object_name(page_id, coma_id)
    for obj in list(bpy.data.objects):
        if not obj.name.startswith(prefix):
            continue
        if obj.name in keep_names:
            continue
        data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Curve):
                    bpy.data.curves.remove(data)
            except Exception:  # noqa: BLE001
                pass


def _ensure_brush_texture_border(scene, work, page, coma, page_id: str, coma_id: str) -> Optional[bpy.types.Object]:
    plane_obj = None
    try:
        from . import coma_plane as _cp

        plane_obj = _cp.ensure_coma_plane(scene, work, page, coma)
        if plane_obj is None:
            _cp.update_coma_plane_geometry(scene, work, page, coma)
            plane_obj = _cp.find_coma_plane_object(page_id, coma_id)
    except Exception:  # noqa: BLE001
        _logger.exception("brush border: coma plane alpha update failed")
    _remove_related_border_objects(page_id, coma_id, set())
    try:
        from . import coma_border_texture

        coma_border_texture.cleanup_orphan_assets(page_id, coma_id)
    except Exception:  # noqa: BLE001
        pass
    _ensure_white_margin_object(scene, work, page, coma, page_id, coma_id)
    return plane_obj


def _ensure_white_margin_object(scene, work, page, coma, page_id: str, coma_id: str) -> Optional[bpy.types.Object]:
    wm = getattr(coma, "white_margin", None)
    if wm is None:
        return None
    enabled_global = bool(getattr(wm, "enabled", False))
    base_width = max(0.0, float(getattr(wm, "width_mm", 0.0) or 0.0))
    visible = bool(getattr(coma, "visible", True)) and enabled_global and base_width > 0.0
    border = getattr(coma, "border", None)
    if str(getattr(border, "style", "solid") or "solid") == "brush":
        visible = False
    obj_name = f"{COMA_WHITE_MARGIN_NAME_PREFIX}{page_id}_{coma_id}"
    mesh_name = f"{COMA_WHITE_MARGIN_MESH_PREFIX}{page_id}_{coma_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    material_indices: list[int] = []
    if visible and enabled_global and base_width > 0.0:
        try:
            outline = border_geom._dedupe_closed(_outline_points(coma))
            border_half = 0.0
            border_style = str(getattr(border, "style", "solid") or "solid")
            if (
                border is not None
                and bool(getattr(border, "visible", True))
                and border_style != "brush"
            ):
                border_half = max(0.0, float(getattr(border, "width_mm", 0.0) or 0.0)) * 0.5
            placement = str(getattr(wm, "placement", "outside") or "outside")
            if placement not in {"outside", "inside", "both"}:
                placement = "outside"
            edge_outer = _offset_loop(outline, border_half)
            edge_inner = _offset_loop(outline, -border_half)
            far_outer = _offset_loop(outline, border_half + base_width)
            far_inner = _offset_loop(outline, -(border_half + base_width))
            if placement in {"outside", "both"} and edge_outer and far_outer:
                _append_white_margin_band(verts, faces, material_indices, edge_outer, far_outer, 0)
            if placement in {"inside", "both"} and far_inner and edge_inner:
                _append_white_margin_band(verts, faces, material_indices, far_inner, edge_inner, 1)
        except Exception:  # noqa: BLE001
            _logger.exception("white margin shape ring failed")
            visible = False
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
        for poly, material_index in zip(mesh.polygons, material_indices, strict=False):
            poly.material_index = int(material_index)
    mesh.update()
    if not verts or not faces:
        visible = False
    base_color = getattr(wm, "color", (1.0, 1.0, 1.0, 1.0))
    outer_color = tuple(float(c) for c in getattr(wm, "outer_color", base_color)[:4])
    inner_color = tuple(float(c) for c in getattr(wm, "inner_color", base_color)[:4])
    outer_mat = _ensure_color_material(f"{COMA_WHITE_MARGIN_MATERIAL_PREFIX}{page_id}_{coma_id}_outer", outer_color)
    inner_mat = _ensure_color_material(f"{COMA_WHITE_MARGIN_MATERIAL_PREFIX}{page_id}_{coma_id}_inner", inner_color)
    while len(mesh.materials) < 2:
        mesh.materials.append(outer_mat)
    if mesh.materials[0] is not outer_mat:
        mesh.materials[0] = outer_mat
    if mesh.materials[1] is not inner_mat:
        mesh.materials[1] = inner_mat
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    obj[PROP_COMA_WHITE_MARGIN_KIND] = "coma_white_margin"
    obj[PROP_COMA_WHITE_MARGIN_OWNER_ID] = _owner_id(page_id, coma_id)
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.hide_viewport = not visible
    obj.hide_render = not visible
    _set_location(obj, scene, work, page, coma)
    obj.location.z = coma_z_order.white_margin_z(coma)
    coma_coll = _coma_collection(scene, page, page_id, coma_id, str(getattr(coma, "title", "") or coma_id))
    if coma_coll is not None and not any(existing is obj for existing in coma_coll.objects):
        coma_coll.objects.link(obj)
    for coll in tuple(obj.users_collection):
        if coll is coma_coll:
            continue
        coll.objects.unlink(obj)
    return obj


def ensure_coma_border_object(scene, work, page, coma) -> Optional[bpy.types.Object]:
    if scene is None or work is None or coma is None:
        return None
    page_id = _page_id_for_coma(page)
    coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
    if not page_id or not coma_id:
        return None
    border = getattr(coma, "border", None)
    spread_basic_frame_side = _spread_basic_frame_side(work, page, coma)
    if not spread_basic_frame_side and _uses_brush_texture(coma):
        return _ensure_brush_texture_border(scene, work, page, coma, page_id, coma_id)
    groups = _border_paths_by_material(
        coma,
        work=work,
        page=page,
        spread_basic_frame_side=spread_basic_frame_side,
    )
    if not groups:
        groups = [([], max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0)), _rgba_from_border(coma), "solid")]
    keep_names: set[str] = set()
    primary_obj: Optional[bpy.types.Object] = None
    coma_coll = _coma_collection(scene, page, page_id, coma_id, str(getattr(coma, "title", "") or coma_id))
    for group_index, (paths, width_mm, color, style_name) in enumerate(groups):
        suffix = "" if group_index == 0 else f"_{group_index:02d}"
        curve_name = _curve_name(page_id, coma_id) if group_index == 0 else f"{_curve_name(page_id, coma_id)}_{group_index:02d}"
        mesh_name = _mesh_name(page_id, coma_id) if group_index == 0 else f"{_mesh_name(page_id, coma_id)}_{group_index:02d}"
        object_name = _object_name(page_id, coma_id) if group_index == 0 else f"{_object_name(page_id, coma_id)}_{group_index:02d}"
        is_brush = style_name in {"brush_core", "brush_halo"}
        if style_name == "brush_core":
            mat = _ensure_color_material(f"{_material_name(page_id, coma_id)}_brushcore", color)
        elif style_name == "brush_halo":
            dither = bool(getattr(border, "blur_dither", False))
            mat = _ensure_soft_material(
                f"{_material_name(page_id, coma_id)}_brushhalo_{group_index:02d}",
                color,
                dither=dither,
            )
        elif group_index == 0:
            mat = _ensure_material(page_id, coma_id, coma)
            if mat.diffuse_color != color:
                mat = _ensure_color_material(_material_name(page_id, coma_id), color)
        else:
            mat = _ensure_color_material(f"{_material_name(page_id, coma_id)}_{group_index:02d}", color)
        if style_name == "solid_closed":
            mesh = bpy.data.meshes.get(mesh_name)
            if mesh is None:
                mesh = bpy.data.meshes.new(mesh_name)
            _rebuild_mesh_band(mesh, paths, width_mm)
            if not mesh.materials:
                mesh.materials.append(mat)
            elif mesh.materials[0] is not mat:
                mesh.materials[0] = mat
            obj = bpy.data.objects.get(object_name)
            if obj is not None and obj.type != "MESH":
                old_data = obj.data
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:  # noqa: BLE001
                    pass
                if old_data is not None and getattr(old_data, "users", 0) == 0:
                    try:
                        if isinstance(old_data, bpy.types.Mesh):
                            bpy.data.meshes.remove(old_data)
                        elif isinstance(old_data, bpy.types.Curve):
                            bpy.data.curves.remove(old_data)
                    except Exception:  # noqa: BLE001
                        pass
                obj = None
            if obj is None:
                obj = bpy.data.objects.new(object_name, mesh)
            elif obj.data is not mesh:
                obj.data = mesh
        else:
            curve = bpy.data.curves.get(curve_name)
            if curve is None:
                curve = bpy.data.curves.new(curve_name, type="CURVE")
            _rebuild_curve(
                curve,
                paths,
                width_mm,
                cyclic=(style_name in {"brush_core", "brush_halo"}),
            )
            if not curve.materials:
                curve.materials.append(mat)
            elif curve.materials[0] is not mat:
                curve.materials[0] = mat
            obj = bpy.data.objects.get(object_name)
            if obj is not None and obj.type != "CURVE":
                # 旧版 (ボカシ平面メッシュ) で保存されたファイルでは枠線
                # オブジェクトが MESH 型で残っている。Object のデータ型は
                # 変更できないため作り直す (放置すると obj.data=curve で
                # 例外になり枠線が壊れる/古いメッシュが残る)。
                old_data = obj.data
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:  # noqa: BLE001
                    pass
                if old_data is not None and getattr(old_data, "users", 0) == 0:
                    try:
                        if isinstance(old_data, bpy.types.Mesh):
                            bpy.data.meshes.remove(old_data)
                        elif isinstance(old_data, bpy.types.Curve):
                            bpy.data.curves.remove(old_data)
                    except Exception:  # noqa: BLE001
                        pass
                obj = None
            if obj is None:
                obj = bpy.data.objects.new(object_name, curve)
            elif obj.data is not curve:
                obj.data = curve
        keep_names.add(obj.name)
        if primary_obj is None:
            primary_obj = obj
        obj[PROP_COMA_BORDER_KIND] = "coma_border"
        obj[PROP_COMA_BORDER_OWNER_ID] = _owner_id(page_id, coma_id)
        obj[on.PROP_MANAGED] = False
        obj.hide_select = True
        visible = bool(getattr(coma, "visible", True)) and bool(getattr(border, "visible", True)) and width_mm > 0.0 and bool(paths)
        obj.hide_viewport = not visible
        obj.hide_render = not visible
        _set_location(obj, scene, work, page, coma)
        if is_brush:
            # 芯 (group_index 最大) を最前面、外側ハローを背面に並べて
            # z-fight を避けつつ輪郭が外へボケて見えるようにする。
            obj.location.z = coma_z_order.border_z(coma) + group_index * 1.0e-5
        if coma_coll is not None and not any(existing is obj for existing in coma_coll.objects):
            try:
                coma_coll.objects.link(obj)
            except Exception:  # noqa: BLE001
                _logger.exception("link coma border failed")
        for coll in tuple(obj.users_collection):
            if coll is coma_coll:
                continue
            try:
                coll.objects.unlink(obj)
            except Exception:  # noqa: BLE001
                pass
    _remove_related_border_objects(page_id, coma_id, keep_names)
    try:
        from . import coma_border_texture

        coma_border_texture.cleanup_orphan_assets(page_id, coma_id)
    except Exception:  # noqa: BLE001
        pass
    _ensure_white_margin_object(scene, work, page, coma, page_id, coma_id)
    obj = primary_obj
    if obj is None:
        return None
    return obj


def update_coma_border_geometry(scene, work, page, coma) -> bool:
    return ensure_coma_border_object(scene, work, page, coma) is not None


def update_coma_border_locations(scene, work) -> int:
    if scene is None or work is None:
        return 0
    count = 0
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            page_id = str(getattr(page, "id", "") or "")
            coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
            prefix = _object_name(page_id, coma_id)
            for candidate in list(bpy.data.objects):
                if candidate.name.startswith(prefix):
                    _set_location(candidate, scene, work, page, coma)
                    count += 1
            wm_obj = bpy.data.objects.get(f"{COMA_WHITE_MARGIN_NAME_PREFIX}{page_id}_{coma_id}")
            if wm_obj is not None:
                _set_location(wm_obj, scene, work, page, coma)
                wm_obj.location.z = coma_z_order.white_margin_z(coma)
                count += 1
    for coma in getattr(work, "shared_comas", []) or []:
        page_id = OUTSIDE_PAGE_ID
        coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
        prefix = _object_name(page_id, coma_id)
        for candidate in list(bpy.data.objects):
            if candidate.name.startswith(prefix):
                _set_location(candidate, scene, work, None, coma)
                count += 1
        wm_obj = bpy.data.objects.get(f"{COMA_WHITE_MARGIN_NAME_PREFIX}{page_id}_{coma_id}")
        if wm_obj is not None:
            _set_location(wm_obj, scene, work, None, coma)
            wm_obj.location.z = coma_z_order.white_margin_z(coma)
            count += 1
    return count


def regenerate_all_coma_borders(scene, work) -> int:
    if scene is None or work is None:
        return 0
    valid: set[str] = set()
    count = 0
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            page_id = str(getattr(page, "id", "") or "")
            coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
            if not page_id or not coma_id:
                continue
            valid.add(_owner_id(page_id, coma_id))
            if ensure_coma_border_object(scene, work, page, coma) is not None:
                count += 1
    for coma in getattr(work, "shared_comas", []) or []:
        page_id = OUTSIDE_PAGE_ID
        coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
        if not coma_id:
            continue
        valid.add(_owner_id(page_id, coma_id))
        if ensure_coma_border_object(scene, work, None, coma) is not None:
            count += 1
    for obj in list(bpy.data.objects):
        if obj.get(PROP_COMA_BORDER_KIND) != "coma_border":
            continue
        if str(obj.get(PROP_COMA_BORDER_OWNER_ID, "") or "") in valid:
            continue
        data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Curve):
                    bpy.data.curves.remove(data)
            except Exception:  # noqa: BLE001
                pass
    for obj in list(bpy.data.objects):
        if obj.get(PROP_COMA_WHITE_MARGIN_KIND) != "coma_white_margin":
            continue
        if str(obj.get(PROP_COMA_WHITE_MARGIN_OWNER_ID, "") or "") in valid:
            continue
        data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(data)
            except Exception:  # noqa: BLE001
                pass
    return count


def remove_coma_border(page_id: str, coma_id: str) -> bool:
    removed = False
    prefix = _object_name(page_id, coma_id)
    for obj in list(bpy.data.objects):
        if not obj.name.startswith(prefix):
            continue
        data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed = True
        except Exception:  # noqa: BLE001
            continue
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Curve):
                    bpy.data.curves.remove(data)
            except Exception:  # noqa: BLE001
                pass
    try:
        from . import coma_border_texture

        coma_border_texture.cleanup_orphan_assets(page_id, coma_id)
    except Exception:  # noqa: BLE001
        pass
    wm_obj = bpy.data.objects.get(f"{COMA_WHITE_MARGIN_NAME_PREFIX}{page_id}_{coma_id}")
    if wm_obj is not None:
        data = wm_obj.data
        try:
            bpy.data.objects.remove(wm_obj, do_unlink=True)
            removed = True
        except Exception:  # noqa: BLE001
            pass
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(data)
            except Exception:  # noqa: BLE001
                pass
    return removed


def on_coma_border_changed(border) -> None:
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if scene is None or work is None or border is None:
        return

    def _refresh_shading() -> None:
        try:
            from ..ui import overlay as _overlay

            _overlay.apply_bmanga_shading_mode(bpy.context)
        except Exception:  # noqa: BLE001
            pass

    try:
        target_ptr = int(border.as_pointer())
    except Exception:  # noqa: BLE001
        return
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            if not _coma_owns_border_pointer(coma, target_ptr):
                continue
            update_coma_border_geometry(scene, work, page, coma)
            # 角処理 (丸角/面取り) 変更時はコマ平面 Mesh も枠線形状へ追従させ、
            # 四隅でコマ内容が枠線からはみ出さないようにする。
            try:
                from . import coma_plane as _cp

                _cp.update_coma_plane_geometry(scene, work, page, coma)
            except Exception:  # noqa: BLE001
                _logger.exception("coma plane geometry update on border change failed")
            _refresh_shading()
            return
    for coma in getattr(work, "shared_comas", []) or []:
        if not _coma_owns_border_pointer(coma, target_ptr):
            continue
        update_coma_border_geometry(scene, work, None, coma)
        try:
            from . import coma_plane as _cp

            _cp.update_coma_plane_geometry(scene, work, None, coma)
        except Exception:  # noqa: BLE001
            _logger.exception("shared coma plane geometry update on border change failed")
        _refresh_shading()
        return


def _coma_owns_border_pointer(coma, target_ptr: int) -> bool:
    candidates = []
    try:
        b = getattr(coma, "border")
        candidates.append(b)
    except Exception:  # noqa: BLE001
        pass
    try:
        wm = getattr(coma, "white_margin")
        candidates.append(wm)
    except Exception:  # noqa: BLE001
        pass
    for candidate in candidates:
        try:
            if int(candidate.as_pointer()) == target_ptr:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
