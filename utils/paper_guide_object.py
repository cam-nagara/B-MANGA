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
PAPER_GUIDE_GP_DATA_PREFIX = "paper_guide_gp_"
PAPER_SAFE_FILL_MESH_PREFIX = "paper_safe_area_mesh_"
PAPER_GUIDE_MATERIAL_PREFIX = "BName_PaperGuide_"
_OLD_SAFE_FILL_MATERIAL = "BName_SafeAreaFill"

PROP_GUIDE_KIND = "bname_paper_guide_kind"
PROP_GUIDE_OWNER_ID = "bname_paper_guide_page_id"
GUIDE_KIND_LINES = "guides"
_OLD_LINE_KINDS = {"dim", "light", "inner", "safe"}

# 実体ガイド線をビュー上で一定の太さ (おおよそこのピクセル幅) に保つ。
GUIDE_SCREEN_PX = 1.6
_GUIDE_THICKNESS_INTERVAL = 0.12
_last_mpp: float = -1.0


def _gp_material(name: str, rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    try:
        mat.diffuse_color = rgba
    except Exception:  # noqa: BLE001
        pass
    if getattr(mat, "grease_pencil", None) is None:
        try:
            bpy.data.materials.create_gpencil_data(mat)
        except (AttributeError, RuntimeError):
            pass
    gp_style = getattr(mat, "grease_pencil", None)
    if gp_style is not None:
        try:
            gp_style.show_stroke = True
            gp_style.show_fill = False
            gp_style.color = rgba
        except Exception:  # noqa: BLE001
            pass
    return mat


def _material_slot_index(obj: bpy.types.Object, mat: bpy.types.Material) -> int:
    mats = getattr(getattr(obj, "data", None), "materials", None)
    if mats is None:
        return -1
    for i, existing in enumerate(mats):
        if existing is mat or getattr(existing, "name", None) == mat.name:
            return i
    try:
        mats.append(mat)
        return len(mats) - 1
    except Exception:  # noqa: BLE001
        _logger.exception("paper guide GP material slot append failed")
        return -1


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


def _guide_radius_m() -> float:
    if _last_mpp > 0.0:
        half = GUIDE_SCREEN_PX * _last_mpp * 0.5
        return max(mm_to_m(0.005) * 0.5, min(half, mm_to_m(3.0) * 0.5))
    return mm_to_m(0.12) * 0.5


def _gp_data_blocks():
    blocks = getattr(bpy.data, "grease_pencils_v3", None)
    if blocks is not None:
        return blocks
    blocks = getattr(bpy.data, "grease_pencils", None)
    if blocks is not None:
        return blocks
    return None


def _remove_data_block(data) -> None:
    if data is None or getattr(data, "users", 0) > 0:
        return
    try:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
            return
        if isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
            return
    except Exception:  # noqa: BLE001
        return
    blocks = _gp_data_blocks()
    if blocks is None:
        return
    try:
        blocks.remove(data)
    except Exception:  # noqa: BLE001
        pass


def _remove_object(obj: bpy.types.Object) -> None:
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        pass
    _remove_data_block(data)


def _remove_old_line_objects(page_id: str, keep_name: str) -> None:
    for obj in list(bpy.data.objects):
        owner = str(obj.get(PROP_GUIDE_OWNER_ID, "") or "")
        if owner != page_id or obj.name == keep_name:
            continue
        kind = str(obj.get(PROP_GUIDE_KIND, "") or "")
        if kind in _OLD_LINE_KINDS or obj.name.startswith(f"{PAPER_GUIDE_PREFIX}{page_id}_"):
            _remove_object(obj)


def _new_gp_data(data_name: str):
    blocks = _gp_data_blocks()
    if blocks is None:
        raise RuntimeError("Grease Pencil data-blocks are not available")
    old = blocks.get(data_name)
    if old is not None and getattr(old, "users", 0) == 0:
        try:
            blocks.remove(old)
        except Exception:  # noqa: BLE001
            pass
    return blocks.new(data_name)


def _append_gp_stroke(drawing, points: Iterable[tuple[float, float]], *, radius: float, cyclic: bool, material_index: int) -> bool:
    from . import gpencil as gp_utils

    pts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in points]
    if len(pts) < 2:
        return False
    return gp_utils.add_stroke_to_drawing(
        drawing,
        pts,
        radius=radius,
        cyclic=cyclic,
        material_index=material_index,
    )


def _ensure_gp_guide_object(
    scene,
    page,
    page_coll,
    guide_sets: list[tuple[str, list[list[tuple[float, float]]], list[tuple[tuple[float, float], tuple[float, float]]], bpy.types.Material]],
    *,
    z_m: float,
    visible: bool,
) -> bpy.types.Object:
    from . import gpencil as gp_utils

    page_id = str(getattr(page, "id", "") or "")
    obj_name = f"{PAPER_GUIDE_PREFIX}{page_id}"
    data_name = f"{PAPER_GUIDE_GP_DATA_PREFIX}{page_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        _remove_object(obj)
    gp_data = _new_gp_data(data_name)
    obj = bpy.data.objects.new(obj_name, gp_data)
    obj[PROP_GUIDE_KIND] = GUIDE_KIND_LINES
    obj[PROP_GUIDE_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.hide_render = True
    try:
        obj.show_in_front = True
    except Exception:  # noqa: BLE001
        pass
    layer = gp_utils.ensure_layer(gp_data, "ガイド")
    frame = gp_utils.ensure_active_frame(layer, frame_number=1)
    drawing = getattr(frame, "drawing", None) if frame is not None else None
    has_stroke = False
    radius = _guide_radius_m()
    if drawing is not None:
        for _label, loops, segments, mat in guide_sets:
            material_index = _material_slot_index(obj, mat)
            for loop in loops:
                if _append_gp_stroke(drawing, loop, radius=radius, cyclic=len(loop) >= 3, material_index=material_index):
                    has_stroke = True
            for start, end in segments:
                if _append_gp_stroke(drawing, [start, end], radius=radius, cyclic=False, material_index=material_index):
                    has_stroke = True
    obj.hide_viewport = not (visible and has_stroke)
    obj.location.z = z_m
    _set_page_location(scene, obj, page)
    _link_to_page_collection(obj, page_coll)
    _remove_old_line_objects(page_id, obj.name)
    return obj


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
    try:
        obj.show_transparent = True
    except Exception:  # noqa: BLE001
        pass
    obj.hide_viewport = not visible
    obj.hide_render = True
    obj.location.z = z_m
    _set_page_location(scene, obj, page)
    _link_to_page_collection(obj, page_coll)
    return obj


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _safe_fill_view_color(work) -> tuple[float, float, float, float]:
    overlay = getattr(work, "safe_area_overlay", None)
    color = getattr(overlay, "color", (0.0, 0.0, 0.0)) if overlay is not None else (0.0, 0.0, 0.0)
    opacity = getattr(overlay, "opacity", 0.30) if overlay is not None else 0.30
    try:
        r, g, b = float(color[0]), float(color[1]), float(color[2])
    except Exception:  # noqa: BLE001
        r, g, b = 0.0, 0.0, 0.0
    return (_clamp01(r), _clamp01(g), _clamp01(b), _clamp01(float(opacity or 0.0)))


def _clear_material_slots(data) -> None:
    mats = getattr(data, "materials", None)
    if mats is None or len(mats) == 0:
        return
    try:
        mats.clear()
        return
    except Exception:  # noqa: BLE001
        pass
    while len(mats) > 0:
        try:
            mats.pop(index=len(mats) - 1)
        except Exception:  # noqa: BLE001
            break


def _remove_old_safe_fill_material_if_unused() -> None:
    mat = bpy.data.materials.get(_OLD_SAFE_FILL_MATERIAL)
    if mat is None or getattr(mat, "users", 0) > 0:
        return
    try:
        bpy.data.materials.remove(mat)
    except Exception:  # noqa: BLE001
        pass


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
    _clear_material_slots(mesh)
    _remove_old_safe_fill_material_if_unused()

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    obj[PROP_GUIDE_KIND] = "safe_fill"
    obj[PROP_GUIDE_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.color = _safe_fill_view_color(work)
    try:
        obj.show_in_front = True
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.show_transparent = True
    except Exception:  # noqa: BLE001
        pass
    obj.hide_render = True
    obj.hide_viewport = not (
        bool(getattr(page, "in_page_range", True))
        and bool(getattr(work.safe_area_overlay, "enabled", True))
        and obj.color[3] > 0.0
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


def _object_page_id(obj: bpy.types.Object, work) -> str:
    parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
    if ":" in parent_key:
        return parent_key.split(":", 1)[0]
    for page in getattr(work, "pages", []) or []:
        if str(getattr(page, "id", "") or "") == parent_key:
            return parent_key
    if parent_key:
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, parent_key)
            if semantic and semantic != OUTSIDE_STACK_KEY:
                return semantic.split(":", 1)[0]
        except Exception:  # noqa: BLE001
            pass
    return ""


def _page_z_levels(work, page_id: str) -> tuple[float, float, float]:
    max_all = 0.02
    for obj in bpy.data.objects:
        if obj.get(PROP_GUIDE_OWNER_ID):
            continue
        if _object_page_id(obj, work) != page_id:
            continue
        z = float(getattr(obj.location, "z", 0.0) or 0.0)
        max_all = max(max_all, z)
    safe_z = max_all + 0.003
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
    safe_z, guide_z, _ = _page_z_levels(work, page_id)

    mat_dim = _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Dim", viewport_colors.PAPER_GUIDE_DIM)
    mat_light = _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Light", viewport_colors.PAPER_GUIDE_LIGHT)
    mat_guide = _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Guide", viewport_colors.PAPER_GUIDE)
    mat_safe = _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Safe", viewport_colors.SAFE_LINE)
    guide_sets = [
        (
            "dim",
            [
                _rect_loop(rects.canvas) if getattr(paper, "show_canvas_frame", True) else [],
                _rect_loop(rects.bleed) if float(getattr(paper, "bleed_mm", 0.0) or 0.0) > 0.0 and getattr(paper, "show_bleed_frame", True) else [],
            ],
            [],
            mat_dim,
        ),
        (
            "light",
            [_rect_loop(rects.finish) if getattr(paper, "show_finish_frame", True) else []],
            _trim_segments(rects.finish, rects.bleed)
            if float(getattr(paper, "bleed_mm", 0.0) or 0.0) > 0.0 and getattr(paper, "show_trim_marks", True)
            else [],
            mat_light,
        ),
        (
            "inner",
            [_rect_loop(rects.inner_frame) if getattr(paper, "show_inner_frame", True) else []],
            [],
            mat_guide,
        ),
        (
            "safe",
            [_rect_loop(rects.safe) if getattr(paper, "show_safe_line", True) else []],
            [],
            mat_safe,
        ),
    ]
    objects = [
        _ensure_gp_guide_object(scene, page, page_coll, guide_sets, z_m=guide_z, visible=in_range),
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
        _remove_data_block(data)
    return count


# ---------- ビュー上で一定太さに保つ ----------


def _active_view3d_region():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return None
    for win in wm.windows:
        scr = getattr(win, "screen", None)
        if scr is None:
            continue
        for area in scr.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            for region in area.regions:
                if region.type == "WINDOW" and region.width > 0 and region.height > 0:
                    return region, rv3d
    return None


def _meters_per_pixel(region, rv3d) -> Optional[float]:
    try:
        from bpy_extras import view3d_utils
    except Exception:  # noqa: BLE001
        return None
    cx = region.width * 0.5
    cy = region.height * 0.5
    p0 = view3d_utils.region_2d_to_location_3d(region, rv3d, (cx, cy), (0.0, 0.0, 0.0))
    p1 = view3d_utils.region_2d_to_location_3d(region, rv3d, (cx + 1.0, cy), (0.0, 0.0, 0.0))
    if p0 is None or p1 is None:
        return None
    d = (p1 - p0).length
    return d if d > 1.0e-9 else None


def apply_view_constant_thickness() -> None:
    """全用紙ガイド線の太さをビュー倍率に合わせ、画面上で一定太さに保つ."""
    global _last_mpp
    found = _active_view3d_region()
    if found is None:
        return
    region, rv3d = found
    mpp = _meters_per_pixel(region, rv3d)
    if mpp is None:
        return
    if _last_mpp > 0.0 and abs(mpp - _last_mpp) <= _last_mpp * 0.03:
        return
    _last_mpp = mpp
    # 異常な視点 (極端なズーム/パース) で bevel が暴れないようクランプ。
    # 0.005mm 〜 3mm 相当の線幅に収める。
    half = GUIDE_SCREEN_PX * mpp * 0.5
    half = max(mm_to_m(0.005) * 0.5, min(half, mm_to_m(3.0) * 0.5))
    for curve in bpy.data.curves:
        if not curve.name.startswith(PAPER_GUIDE_CURVE_PREFIX):
            continue
        try:
            if abs(float(curve.bevel_depth) - half) > half * 0.02:
                curve.bevel_depth = half
        except Exception:  # noqa: BLE001
            continue
    for obj in bpy.data.objects:
        if obj.get(PROP_GUIDE_KIND) != GUIDE_KIND_LINES or getattr(obj, "type", "") != "GREASEPENCIL":
            continue
        _set_gp_stroke_radius(obj, half)


def _set_gp_stroke_radius(obj: bpy.types.Object, radius: float) -> None:
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return
    for layer in layers:
        for frame in getattr(layer, "frames", []) or []:
            drawing = getattr(frame, "drawing", None)
            strokes = getattr(drawing, "strokes", None)
            if strokes is None:
                continue
            for stroke in strokes:
                points = getattr(stroke, "points", None)
                if points is None:
                    continue
                for point in points:
                    try:
                        point.radius = radius
                    except Exception:  # noqa: BLE001
                        pass


def _thickness_timer():
    try:
        apply_view_constant_thickness()
    except Exception:  # noqa: BLE001
        _logger.exception("paper guide thickness update failed")
    return _GUIDE_THICKNESS_INTERVAL


def register() -> None:
    if not bpy.app.timers.is_registered(_thickness_timer):
        bpy.app.timers.register(_thickness_timer, first_interval=_GUIDE_THICKNESS_INTERVAL)


def unregister() -> None:
    global _last_mpp
    _last_mpp = -1.0
    try:
        if bpy.app.timers.is_registered(_thickness_timer):
            bpy.app.timers.unregister(_thickness_timer)
    except Exception:  # noqa: BLE001
        pass
