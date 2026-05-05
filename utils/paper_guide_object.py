"""用紙ガイド線群とセーフライン外塗りの実オブジェクト同期."""

from __future__ import annotations

from typing import Iterable, Optional

import bpy

from ..ui import overlay_shared
from . import log
from . import object_naming as on
from . import outliner_model as om
from . import viewport_colors
from .geom import Rect, mm_to_m

_logger = log.get_logger(__name__)

PAPER_GUIDE_PREFIX = "page_paper_guide_"
PAPER_SAFE_FILL_PREFIX = "page_safe_area_fill_"
PAPER_GUIDE_CURVE_PREFIX = "paper_guide_curve_"
PAPER_SAFE_FILL_MESH_PREFIX = "paper_safe_area_mesh_"
PAPER_GUIDE_MATERIAL_PREFIX = "BName_PaperGuide_"
PAPER_SAFE_FILL_MATERIAL = "BName_SafeAreaFill"

PROP_GUIDE_KIND = "bname_paper_guide_kind"
PROP_GUIDE_OWNER_ID = "bname_paper_guide_page_id"


def _material(name: str, rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    try:
        mat.diffuse_color = rgba
    except Exception:  # noqa: BLE001
        pass
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = True
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (180, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-80, 0)
    try:
        emission.inputs["Color"].default_value = rgba
        emission.inputs["Strength"].default_value = 1.0
        nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("paper guide material setup failed")
    return mat


def _rect_loop(rect: Rect) -> list[tuple[float, float]]:
    return [
        (rect.x, rect.y),
        (rect.x2, rect.y),
        (rect.x2, rect.y2),
        (rect.x, rect.y2),
    ]


def _trim_segments(
    finish: Rect,
    bleed: Rect,
    *,
    corner_arm_mm: float = 10.0,
    center_size_mm: float = 10.0,
    center_gap_mm: float = 5.0,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    fr, br = finish, bleed
    arm = corner_arm_mm
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    segs.extend([
        ((br.x - arm, fr.y), (br.x, fr.y)),
        ((fr.x, br.y - arm), (fr.x, br.y)),
        ((br.x - arm, br.y), (br.x, br.y)),
        ((br.x, br.y - arm), (br.x, br.y)),
        ((br.x2, fr.y), (br.x2 + arm, fr.y)),
        ((fr.x2, br.y - arm), (fr.x2, br.y)),
        ((br.x2, br.y), (br.x2 + arm, br.y)),
        ((br.x2, br.y - arm), (br.x2, br.y)),
        ((br.x - arm, fr.y2), (br.x, fr.y2)),
        ((fr.x, br.y2), (fr.x, br.y2 + arm)),
        ((br.x - arm, br.y2), (br.x, br.y2)),
        ((br.x, br.y2), (br.x, br.y2 + arm)),
        ((br.x2, fr.y2), (br.x2 + arm, fr.y2)),
        ((fr.x2, br.y2), (fr.x2, br.y2 + arm)),
        ((br.x2, br.y2), (br.x2 + arm, br.y2)),
        ((br.x2, br.y2), (br.x2, br.y2 + arm)),
    ])
    cx_mid = (fr.x + fr.x2) * 0.5
    cy_mid = (fr.y + fr.y2) * 0.5
    half = center_size_mm * 0.5
    gap = center_gap_mm
    cy_top = br.y2 + gap + half
    cy_bot = br.y - gap - half
    cx_left = br.x - gap - half
    cx_right = br.x2 + gap + half
    segs.extend([
        ((cx_mid, cy_top - half), (cx_mid, cy_top + half)),
        ((cx_mid - half, cy_top), (cx_mid + half, cy_top)),
        ((cx_mid, cy_bot - half), (cx_mid, cy_bot + half)),
        ((cx_mid - half, cy_bot), (cx_mid + half, cy_bot)),
        ((cx_left, cy_mid - half), (cx_left, cy_mid + half)),
        ((cx_left - half, cy_mid), (cx_left + half, cy_mid)),
        ((cx_right, cy_mid - half), (cx_right, cy_mid + half)),
        ((cx_right - half, cy_mid), (cx_right + half, cy_mid)),
    ])
    return segs


def _append_loop(curve: bpy.types.Curve, points: Iterable[tuple[float, float]]) -> None:
    pts = list(points)
    if len(pts) < 2:
        return
    spline = curve.splines.new(type="POLY")
    spline.points.add(len(pts) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, pts, strict=False):
        point.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0, 1.0)
    spline.use_cyclic_u = len(pts) >= 3


def _append_segment(curve: bpy.types.Curve, start: tuple[float, float], end: tuple[float, float]) -> None:
    spline = curve.splines.new(type="POLY")
    spline.points.add(1)
    spline.points[0].co = (mm_to_m(start[0]), mm_to_m(start[1]), 0.0, 1.0)
    spline.points[1].co = (mm_to_m(end[0]), mm_to_m(end[1]), 0.0, 1.0)
    spline.use_cyclic_u = False


def _ensure_curve_object(
    scene,
    page,
    page_coll,
    *,
    suffix: str,
    points: list[list[tuple[float, float]]],
    segments: list[tuple[tuple[float, float], tuple[float, float]]] | None,
    material: bpy.types.Material,
    z_m: float,
    visible: bool,
) -> bpy.types.Object:
    page_id = str(getattr(page, "id", "") or "")
    curve_name = f"{PAPER_GUIDE_CURVE_PREFIX}{page_id}_{suffix}"
    obj_name = f"{PAPER_GUIDE_PREFIX}{page_id}_{suffix}"
    curve = bpy.data.curves.get(curve_name)
    if curve is None:
        curve = bpy.data.curves.new(curve_name, type="CURVE")
    curve.dimensions = "3D"
    while len(curve.splines):
        curve.splines.remove(curve.splines[0])
    for loop in points:
        _append_loop(curve, loop)
    for start, end in segments or []:
        _append_segment(curve, start, end)
    curve.bevel_depth = mm_to_m(0.12) * 0.5
    curve.bevel_resolution = 0
    if not curve.materials:
        curve.materials.append(material)
    elif curve.materials[0] is not material:
        curve.materials[0] = material

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, curve)
    elif obj.data is not curve:
        obj.data = curve
    obj[PROP_GUIDE_KIND] = suffix
    obj[PROP_GUIDE_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.hide_viewport = not visible
    obj.hide_render = True
    obj.location.z = z_m
    _set_page_location(scene, obj, page)
    _link_to_page_collection(obj, page_coll)
    return obj


def _safe_fill_material(opacity: float) -> bpy.types.Material:
    alpha = max(0.0, min(1.0, float(opacity)))
    return _material(PAPER_SAFE_FILL_MATERIAL, (0.0, 0.0, 0.0, alpha))


def _safe_fill_faces(canvas: Rect, safe: Rect) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int, int]]]:
    rects = [
        Rect(canvas.x, safe.y2, canvas.width, canvas.y2 - safe.y2),
        Rect(canvas.x, canvas.y, canvas.width, safe.y - canvas.y),
        Rect(canvas.x, safe.y, safe.x - canvas.x, safe.height),
        Rect(safe.x2, safe.y, canvas.x2 - safe.x2, safe.height),
    ]
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
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
    return verts, faces


def _ensure_safe_fill_object(scene, work, page, page_coll, canvas: Rect, safe: Rect, z_m: float) -> bpy.types.Object:
    page_id = str(getattr(page, "id", "") or "")
    mesh_name = f"{PAPER_SAFE_FILL_MESH_PREFIX}{page_id}"
    obj_name = f"{PAPER_SAFE_FILL_PREFIX}{page_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    verts, faces = _safe_fill_faces(canvas, safe)
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()
    mat = _safe_fill_material(float(getattr(work.safe_area_overlay, "opacity", 0.30) or 0.30))
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    obj[PROP_GUIDE_KIND] = "safe_fill"
    obj[PROP_GUIDE_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.hide_render = True
    obj.hide_viewport = not (
        bool(getattr(page, "in_page_range", True))
        and bool(getattr(work.safe_area_overlay, "enabled", True))
        and float(getattr(work.safe_area_overlay, "opacity", 0.30) or 0.30) > 0.0
    )
    obj.location.z = z_m
    _set_page_location(scene, obj, page)
    _link_to_page_collection(obj, page_coll)
    return obj


def _set_page_location(scene, obj: bpy.types.Object, page) -> None:
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None:
        return
    target_id = str(getattr(page, "id", "") or "")
    page_index = -1
    for i, candidate in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(candidate, "id", "") or "") == target_id:
            page_index = i
            break
    if page_index < 0:
        return
    try:
        from . import page_grid

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, page_index)
        obj.location.x = mm_to_m(ox_mm)
        obj.location.y = mm_to_m(oy_mm)
    except Exception:  # noqa: BLE001
        _logger.exception("paper guide page offset failed")


def _link_to_page_collection(obj: bpy.types.Object, page_coll) -> None:
    if page_coll is not None and not any(existing is obj for existing in page_coll.objects):
        try:
            page_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("paper guide link failed")
    for coll in tuple(obj.users_collection):
        if coll is page_coll:
            continue
        try:
            coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass


def _object_page_id(obj: bpy.types.Object) -> str:
    parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
    if ":" in parent_key:
        return parent_key.split(":", 1)[0]
    if parent_key.startswith(("p", "P")):
        return parent_key
    return ""


def _page_z_levels(page_id: str) -> tuple[float, float, float]:
    max_all = 0.02
    max_non_text = 0.02
    min_text = 0.0
    for obj in bpy.data.objects:
        if obj.get(PROP_GUIDE_OWNER_ID):
            continue
        if _object_page_id(obj) != page_id:
            continue
        z = float(getattr(obj.location, "z", 0.0) or 0.0)
        max_all = max(max_all, z)
        if str(obj.get(on.PROP_KIND, "") or "") == "text":
            min_text = z if min_text <= 0.0 else min(min_text, z)
        else:
            max_non_text = max(max_non_text, z)
    safe_z = max_non_text + 0.003
    if min_text > 0.0:
        safe_z = min(safe_z, max(0.001, min_text - 0.002))
    guide_z = max(max_all, safe_z) + 0.01
    return safe_z, guide_z, max_all


def ensure_paper_guides_for_page(scene, work, page_index: int) -> list[bpy.types.Object]:
    if scene is None or work is None or not (0 <= page_index < len(getattr(work, "pages", []) or [])):
        return []
    page = work.pages[page_index]
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return []
    paper = work.paper
    try:
        from . import page_grid

        is_left = page_grid.is_left_half_page(
            page_index,
            getattr(paper, "start_side", "right"),
            getattr(paper, "read_direction", "left"),
        )
    except Exception:  # noqa: BLE001
        is_left = False
    rects = overlay_shared.compute_paper_rects(paper, is_left_half=is_left)
    page_coll = on.find_collection_by_bname_id(page_id, kind="page")
    if page_coll is None:
        page_coll = om.ensure_page_collection(scene, page_id, str(getattr(page, "title", "") or page_id))
    in_range = bool(getattr(page, "in_page_range", True))
    safe_z, guide_z, _ = _page_z_levels(page_id)

    mat_dim = _material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Dim", viewport_colors.PAPER_GUIDE_DIM)
    mat_light = _material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Light", viewport_colors.PAPER_GUIDE_LIGHT)
    mat_guide = _material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Guide", viewport_colors.PAPER_GUIDE)
    mat_safe = _material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Safe", viewport_colors.SAFE_LINE)
    objects = [
        _ensure_curve_object(
            scene,
            page,
            page_coll,
            suffix="dim",
            points=[
                _rect_loop(rects.canvas) if getattr(paper, "show_canvas_frame", True) else [],
                _rect_loop(rects.bleed) if float(getattr(paper, "bleed_mm", 0.0) or 0.0) > 0.0 and getattr(paper, "show_bleed_frame", True) else [],
            ],
            segments=[],
            material=mat_dim,
            z_m=guide_z,
            visible=in_range,
        ),
        _ensure_curve_object(
            scene,
            page,
            page_coll,
            suffix="light",
            points=[_rect_loop(rects.finish) if getattr(paper, "show_finish_frame", True) else []],
            segments=_trim_segments(rects.finish, rects.bleed)
            if float(getattr(paper, "bleed_mm", 0.0) or 0.0) > 0.0 and getattr(paper, "show_trim_marks", True)
            else [],
            material=mat_light,
            z_m=guide_z + 0.001,
            visible=in_range,
        ),
        _ensure_curve_object(
            scene,
            page,
            page_coll,
            suffix="inner",
            points=[_rect_loop(rects.inner_frame) if getattr(paper, "show_inner_frame", True) else []],
            segments=[],
            material=mat_guide,
            z_m=guide_z + 0.002,
            visible=in_range,
        ),
        _ensure_curve_object(
            scene,
            page,
            page_coll,
            suffix="safe",
            points=[_rect_loop(rects.safe) if getattr(paper, "show_safe_line", True) else []],
            segments=[],
            material=mat_safe,
            z_m=guide_z + 0.003,
            visible=in_range,
        ),
        _ensure_safe_fill_object(scene, work, page, page_coll, rects.canvas, rects.safe, safe_z),
    ]
    return objects


def regenerate_all_paper_guides(scene, work) -> int:
    if scene is None or work is None or not getattr(work, "loaded", False):
        return 0
    valid_ids: set[str] = set()
    count = 0
    for i, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        if page_id:
            valid_ids.add(page_id)
        count += len(ensure_paper_guides_for_page(scene, work, i))
    for obj in list(bpy.data.objects):
        owner = str(obj.get(PROP_GUIDE_OWNER_ID, "") or "")
        if not owner or owner in valid_ids:
            continue
        data = getattr(obj, "data", None)
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
    return count
