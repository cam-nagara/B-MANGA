"""コマ枠線の実オブジェクト同期."""

from __future__ import annotations

import math
from typing import Optional, Sequence

import bpy

from . import border_geom
from . import log
from . import object_naming as on
from . import outliner_model as om
from .geom import Rect, mm_to_m

_logger = log.get_logger(__name__)

_BRUSH_OVERRIDE_NORMALIZE_DEPTH = 0

COMA_BORDER_NAME_PREFIX = "coma_border_"
COMA_BORDER_CURVE_PREFIX = "coma_border_curve_"
COMA_BORDER_MATERIAL_PREFIX = "BName_ComaBorder_"
COMA_WHITE_MARGIN_NAME_PREFIX = "coma_white_margin_"
COMA_WHITE_MARGIN_MESH_PREFIX = "coma_white_margin_mesh_"
COMA_WHITE_MARGIN_MATERIAL_PREFIX = "BName_ComaWhiteMargin_"
COMA_WHITE_MARGIN_Z_M = 0.018
COMA_BORDER_Z_M = 0.024
OUTSIDE_PAGE_ID = "outside"

PROP_COMA_BORDER_KIND = "bname_coma_border_kind"
PROP_COMA_BORDER_OWNER_ID = "bname_coma_border_owner_id"
PROP_COMA_WHITE_MARGIN_KIND = "bname_coma_white_margin_kind"
PROP_COMA_WHITE_MARGIN_OWNER_ID = "bname_coma_white_margin_owner_id"


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
    return mat


def _ensure_soft_material(
    name: str,
    rgba: tuple[float, float, float, float],
    *,
    dither: bool = False,
) -> bpy.types.Material:
    """半透明のソフトマテリアル (ボカシブラシのハロー用).

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
    return mat


def _brush_halo_groups(
    path: list[tuple[float, float]],
    base_width_mm: float,
    color: tuple[float, float, float, float],
    blur_amount: float,
) -> list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]]:
    """ボカシブラシ: 芯 + 外側に広がる半透明ハローのグループ列を返す."""
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
    対応点同士を四角形で帯状に繋いで丸角の白フチを作る。
    """
    n = len(inner_pts)
    if n < 3 or len(outer_pts) != n:
        return None
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in inner_pts]
    verts += [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in outer_pts]
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    return verts, faces


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
    curve.bevel_depth = mm_to_m(max(0.0, width_mm)) * 0.5
    curve.bevel_resolution = 1
    curve.resolution_u = 1


def _page_index(work, page) -> int:
    page_id = str(getattr(page, "id", "") or "")
    for i, candidate in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(candidate, "id", "") or "") == page_id:
            return i
    return -1


def _set_location(obj: bpy.types.Object, scene, work, page, coma) -> None:
    page_ox = 0.0
    page_oy = 0.0
    page_i = _page_index(work, page)
    if page_i >= 0 and scene is not None:
        try:
            from . import page_grid

            page_ox, page_oy = page_grid.page_total_offset_mm(work, scene, page_i)
        except Exception:  # noqa: BLE001
            _logger.exception("coma border page offset failed")
    local_x = 0.0
    local_y = 0.0
    if str(getattr(coma, "shape_type", "rect") or "rect") == "rect":
        local_x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        local_y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
    obj.location.x = mm_to_m(page_ox + local_x)
    obj.location.y = mm_to_m(page_oy + local_y)
    obj.location.z = COMA_BORDER_Z_M


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
    override_map = {int(getattr(s, "edge_index", -1)): s for s in getattr(coma, "edge_styles", []) or []}
    edge_style = override_map.get(edge_index)
    if edge_style is not None:
        try:
            color = tuple(float(c) for c in edge_style.color[:4])
        except Exception:  # noqa: BLE001
            pass
        width = max(0.0, float(getattr(edge_style, "width_mm", width) or width))
    if str(getattr(coma, "shape_type", "rect") or "rect") == "rect" and point_count == 4:
        rect_edges = [border.edge_bottom, border.edge_right, border.edge_top, border.edge_left]
        if edge_index < len(rect_edges):
            edge = rect_edges[edge_index]
            if getattr(edge, "use_override", False):
                visible = bool(getattr(edge, "visible", True))
                style = str(getattr(edge, "style", style) or style)
                width = max(0.0, float(getattr(edge, "width_mm", width) or width))
                try:
                    color = tuple(float(c) for c in edge.color[:4])
                except Exception:  # noqa: BLE001
                    pass
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


def _border_paths_by_material(coma) -> list[tuple[list[list[tuple[float, float]]], float, tuple[float, float, float, float], str]]:
    border = getattr(coma, "border", None)
    if border is None or not bool(getattr(border, "visible", True)):
        return []
    base = _base_poly(coma)
    if len(base) < 2:
        return []
    no_edge_override = (
        len(getattr(coma, "edge_styles", []) or []) == 0
        and not any(
            getattr(edge, "use_override", False)
            for edge in (border.edge_bottom, border.edge_right, border.edge_top, border.edge_left)
        )
    )
    base_style = str(getattr(border, "style", "solid") or "solid")
    base_width = max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0))
    if no_edge_override and base_style == "solid":
        path = _outline_points(coma)
        return [([path], base_width, _rgba_from_border(coma), "solid_closed")]
    if no_edge_override and base_style == "brush":
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
    border = getattr(coma, "border", None)
    if border is None:
        return False
    return (
        len(getattr(coma, "edge_styles", []) or []) > 0
        or any(
            getattr(edge, "use_override", False)
            for edge in (border.edge_bottom, border.edge_right, border.edge_top, border.edge_left)
        )
    )


def _normalize_brush_edge_overrides(coma) -> bool:
    """ボカシブラシは全周テクスチャで描くため、辺ごとの設定を解除する."""
    global _BRUSH_OVERRIDE_NORMALIZE_DEPTH
    border = getattr(coma, "border", None)
    if border is None or str(getattr(border, "style", "solid") or "solid") != "brush":
        return False
    if _BRUSH_OVERRIDE_NORMALIZE_DEPTH > 0:
        return False
    changed = False
    _BRUSH_OVERRIDE_NORMALIZE_DEPTH += 1
    try:
        edge_styles = getattr(coma, "edge_styles", None)
        if edge_styles is not None and len(edge_styles) > 0:
            edge_styles.clear()
            changed = True
        for edge in (border.edge_bottom, border.edge_right, border.edge_top, border.edge_left):
            if bool(getattr(edge, "use_override", False)):
                edge.use_override = False
                changed = True
    finally:
        _BRUSH_OVERRIDE_NORMALIZE_DEPTH = max(0, _BRUSH_OVERRIDE_NORMALIZE_DEPTH - 1)
    return changed


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
    from . import coma_border_texture

    border = getattr(coma, "border", None)
    outline = _outline_points(coma)
    owner_id = _owner_id(page_id, coma_id)
    obj = coma_border_texture.ensure_brush_border_mesh(
        page_id,
        coma_id,
        owner_id,
        outline,
        max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0)),
        _rgba_from_border(coma),
        float(getattr(border, "blur_amount", 0.5) or 0.0),
        dither=bool(getattr(border, "blur_dither", False)),
    )
    keep_names = {obj.name} if obj is not None else set()
    if obj is not None:
        visible = bool(getattr(coma, "visible", True)) and bool(getattr(border, "visible", True))
        obj.hide_viewport = not visible
        obj.hide_render = not visible
        _set_location(obj, scene, work, page, coma)
        obj.location.z = COMA_BORDER_Z_M
        coma_coll = _coma_collection(scene, page, page_id, coma_id, str(getattr(coma, "title", "") or coma_id))
        if coma_coll is not None and not any(existing is obj for existing in coma_coll.objects):
            try:
                coma_coll.objects.link(obj)
            except Exception:  # noqa: BLE001
                _logger.exception("link coma border texture failed")
        for coll in tuple(obj.users_collection):
            if coll is coma_coll:
                continue
            coll.objects.unlink(obj)
    _remove_related_border_objects(page_id, coma_id, keep_names)
    coma_border_texture.cleanup_orphan_assets(page_id, coma_id)
    _ensure_white_margin_object(scene, work, page, coma, page_id, coma_id)
    return obj


def _ensure_white_margin_object(scene, work, page, coma, page_id: str, coma_id: str) -> Optional[bpy.types.Object]:
    wm = getattr(coma, "white_margin", None)
    if wm is None:
        return None
    enabled_global = bool(getattr(wm, "enabled", False))
    base_width = max(0.0, float(getattr(wm, "width_mm", 0.0) or 0.0))
    any_override = any(
        getattr(edge, "use_override", False) and getattr(edge, "enabled", False) and float(getattr(edge, "width_mm", 0.0) or 0.0) > 0.0
        for edge in (wm.edge_bottom, wm.edge_right, wm.edge_top, wm.edge_left)
    )
    visible = bool(getattr(coma, "visible", True)) and (enabled_global and base_width > 0.0 or any_override)
    obj_name = f"{COMA_WHITE_MARGIN_NAME_PREFIX}{page_id}_{coma_id}"
    mesh_name = f"{COMA_WHITE_MARGIN_MESH_PREFIX}{page_id}_{coma_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    border = getattr(coma, "border", None)
    corner_type = str(getattr(border, "corner_type", "square") or "square")
    corner_r = float(getattr(border, "corner_radius_mm", 0.0) or 0.0)
    is_rect = str(getattr(coma, "shape_type", "rect") or "rect") == "rect"
    # 等幅 (辺ごとの個別設定なし) かつ丸角/面取りのときは、枠線と同心の
    # 丸角リングとして白フチを生成し、四隅のはみ出しをなくす。
    use_round_ring = (
        visible
        and is_rect
        and corner_type in ("rounded", "bevel")
        and corner_r > 0.0
        and not any_override
        and enabled_global
        and base_width > 0.0
    )
    if use_round_ring:
        w_mm = float(getattr(coma, "rect_width_mm", 0.0) or 0.0)
        h_mm = float(getattr(coma, "rect_height_mm", 0.0) or 0.0)
        mw = base_width
        try:
            inner = border_geom.styled_closed_path_mm(
                [(0.0, 0.0), (w_mm, 0.0), (w_mm, h_mm), (0.0, h_mm)],
                corner_type,
                corner_r,
            )
            outer = border_geom.styled_closed_path_mm(
                [(-mw, -mw), (w_mm + mw, -mw), (w_mm + mw, h_mm + mw), (-mw, h_mm + mw)],
                corner_type,
                corner_r + mw,
            )
            ring = _white_margin_ring(inner, outer)
        except Exception:  # noqa: BLE001
            _logger.exception("white margin rounded ring failed")
            ring = None
        if ring is not None:
            verts, faces = ring
        else:
            use_round_ring = False
    if visible and not use_round_ring:
        poly = _base_poly(coma)
        if is_rect:
            rect = Rect(0.0, 0.0, float(getattr(coma, "rect_width_mm", 0.0) or 0.0), float(getattr(coma, "rect_height_mm", 0.0) or 0.0))
            widths = [base_width] * 4
            enabled = [enabled_global] * 4
            for idx, edge in enumerate((wm.edge_bottom, wm.edge_right, wm.edge_top, wm.edge_left)):
                if getattr(edge, "use_override", False):
                    widths[idx] = max(0.0, float(getattr(edge, "width_mm", 0.0) or 0.0))
                    enabled[idx] = bool(getattr(edge, "enabled", False))
            bottom_w = widths[0] if enabled[0] else 0.0
            right_w = widths[1] if enabled[1] else 0.0
            top_w = widths[2] if enabled[2] else 0.0
            left_w = widths[3] if enabled[3] else 0.0
            rects = [
                Rect(rect.x - left_w, rect.y - bottom_w, rect.width + left_w + right_w, bottom_w),
                Rect(rect.x2, rect.y, right_w, rect.height),
                Rect(rect.x - left_w, rect.y2, rect.width + left_w + right_w, top_w),
                Rect(rect.x - left_w, rect.y, left_w, rect.height),
            ]
        else:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            outer = Rect(min(xs) - base_width, min(ys) - base_width, max(xs) - min(xs) + 2 * base_width, max(ys) - min(ys) + 2 * base_width)
            inner = Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            rects = [
                Rect(outer.x, inner.y2, outer.width, outer.y2 - inner.y2),
                Rect(outer.x, outer.y, outer.width, inner.y - outer.y),
                Rect(outer.x, inner.y, inner.x - outer.x, inner.height),
                Rect(inner.x2, inner.y, outer.x2 - inner.x2, inner.height),
            ]
        for rect in rects:
            if rect.width <= 0.0 or rect.height <= 0.0:
                continue
            start = len(verts)
            verts.extend([
                (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
                (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
                (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
                (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
            ])
            faces.append((start, start + 1, start + 2, start + 3))
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()
    color = tuple(float(c) for c in getattr(wm, "color", (1.0, 1.0, 1.0, 1.0))[:4])
    mat = _ensure_color_material(f"{COMA_WHITE_MARGIN_MATERIAL_PREFIX}{page_id}_{coma_id}", color)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat
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
    obj.location.z = COMA_WHITE_MARGIN_Z_M
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
    _normalize_brush_edge_overrides(coma)
    if _uses_brush_texture(coma):
        return _ensure_brush_texture_border(scene, work, page, coma, page_id, coma_id)
    groups = _border_paths_by_material(coma)
    if not groups:
        groups = [([], max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0)), _rgba_from_border(coma), "solid")]
    keep_names: set[str] = set()
    primary_obj: Optional[bpy.types.Object] = None
    coma_coll = _coma_collection(scene, page, page_id, coma_id, str(getattr(coma, "title", "") or coma_id))
    for group_index, (paths, width_mm, color, style_name) in enumerate(groups):
        suffix = "" if group_index == 0 else f"_{group_index:02d}"
        curve_name = _curve_name(page_id, coma_id) if group_index == 0 else f"{_curve_name(page_id, coma_id)}_{group_index:02d}"
        object_name = _object_name(page_id, coma_id) if group_index == 0 else f"{_object_name(page_id, coma_id)}_{group_index:02d}"
        curve = bpy.data.curves.get(curve_name)
        if curve is None:
            curve = bpy.data.curves.new(curve_name, type="CURVE")
        is_brush = style_name in {"brush_core", "brush_halo"}
        _rebuild_curve(
            curve,
            paths,
            width_mm,
            cyclic=(style_name in {"solid_closed", "brush_core", "brush_halo"}),
        )
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
            obj.location.z = COMA_BORDER_Z_M + group_index * 1.0e-5
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
                wm_obj.location.z = COMA_WHITE_MARGIN_Z_M
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
            wm_obj.location.z = COMA_WHITE_MARGIN_Z_M
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
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if scene is None or work is None or border is None:
        return
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
        return


def _coma_owns_border_pointer(coma, target_ptr: int) -> bool:
    candidates = []
    try:
        b = getattr(coma, "border")
        candidates.extend([b, b.edge_top, b.edge_right, b.edge_bottom, b.edge_left])
    except Exception:  # noqa: BLE001
        pass
    try:
        wm = getattr(coma, "white_margin")
        candidates.extend([wm, wm.edge_top, wm.edge_right, wm.edge_bottom, wm.edge_left])
    except Exception:  # noqa: BLE001
        pass
    for candidate in candidates:
        try:
            if int(candidate.as_pointer()) == target_ptr:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
