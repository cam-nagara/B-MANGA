"""コマ枠線の実オブジェクト同期."""

from __future__ import annotations

from typing import Optional, Sequence

import bpy

from . import border_geom
from . import log
from . import object_naming as on
from . import outliner_model as om
from .geom import mm_to_m

_logger = log.get_logger(__name__)

COMA_BORDER_NAME_PREFIX = "coma_border_"
COMA_BORDER_CURVE_PREFIX = "coma_border_curve_"
COMA_BORDER_MATERIAL_PREFIX = "BName_ComaBorder_"
COMA_BORDER_Z_M = 0.012

PROP_COMA_BORDER_KIND = "bname_coma_border_kind"
PROP_COMA_BORDER_OWNER_ID = "bname_coma_border_owner_id"


def _owner_id(page_id: str, coma_id: str) -> str:
    return f"{page_id}:{coma_id}"


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
    points_mm: Sequence[tuple[float, float]],
    width_mm: float,
) -> None:
    curve.dimensions = "3D"
    while len(curve.splines):
        try:
            curve.splines.remove(curve.splines[0])
        except Exception:  # noqa: BLE001
            break
    if len(points_mm) < 2:
        return
    spline = curve.splines.new(type="POLY")
    spline.points.add(len(points_mm) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, points_mm, strict=False):
        point.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0, 1.0)
    spline.use_cyclic_u = True
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


def ensure_coma_border_object(scene, work, page, coma) -> Optional[bpy.types.Object]:
    if scene is None or work is None or page is None or coma is None:
        return None
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
    if not page_id or not coma_id:
        return None
    border = getattr(coma, "border", None)
    width_mm = max(0.0, float(getattr(border, "width_mm", 0.5) or 0.0))
    curve = bpy.data.curves.get(_curve_name(page_id, coma_id))
    if curve is None:
        curve = bpy.data.curves.new(_curve_name(page_id, coma_id), type="CURVE")
    _rebuild_curve(curve, _outline_points(coma), width_mm)
    mat = _ensure_material(page_id, coma_id, coma)
    if not curve.materials:
        curve.materials.append(mat)
    elif curve.materials[0] is not mat:
        curve.materials[0] = mat

    obj = bpy.data.objects.get(_object_name(page_id, coma_id))
    if obj is None:
        obj = bpy.data.objects.new(_object_name(page_id, coma_id), curve)
    elif obj.data is not curve:
        obj.data = curve
    owner = _owner_id(page_id, coma_id)
    obj[PROP_COMA_BORDER_KIND] = "coma_border"
    obj[PROP_COMA_BORDER_OWNER_ID] = owner
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    visible = bool(getattr(coma, "visible", True)) and bool(getattr(border, "visible", True)) and width_mm > 0.0
    obj.hide_viewport = not visible
    obj.hide_render = not visible
    _set_location(obj, scene, work, page, coma)

    coma_coll = on.find_collection_by_bname_id(owner, kind="coma")
    if coma_coll is None:
        coma_coll = om.ensure_coma_collection(scene, page_id, coma_id, str(getattr(coma, "title", "") or coma_id))
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
            obj = bpy.data.objects.get(_object_name(page_id, coma_id))
            if obj is None:
                continue
            _set_location(obj, scene, work, page, coma)
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
                bpy.data.curves.remove(data)
            except Exception:  # noqa: BLE001
                pass
    return count


def remove_coma_border(page_id: str, coma_id: str) -> bool:
    obj = bpy.data.objects.get(_object_name(page_id, coma_id))
    if obj is None:
        return False
    data = obj.data
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return False
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            bpy.data.curves.remove(data)
        except Exception:  # noqa: BLE001
            pass
    return True


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
            try:
                if int(getattr(coma, "border").as_pointer()) != target_ptr:
                    continue
            except Exception:  # noqa: BLE001
                continue
            update_coma_border_geometry(scene, work, page, coma)
            return
