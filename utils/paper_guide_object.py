"""用紙ガイド線群とセーフライン外塗りの実オブジェクト同期."""

from __future__ import annotations

import math
import time
from typing import Iterable, Optional

import bpy

from ..ui import overlay_shared
from . import log, runtime_activity
from . import object_naming as on
from . import outliner_model as om
from . import percentage
from . import spread_merge_geometry
from . import viewport_colors
from .geom import Rect, mm_to_m

_logger = log.get_logger(__name__)

PAPER_GUIDE_PREFIX = "page_paper_guide_"
PAPER_SAFE_FILL_PREFIX = "page_safe_area_fill_"
PAPER_BLEED_OUTER_FILL_PREFIX = "page_bleed_outer_fill_"
PAPER_GUIDE_CURVE_PREFIX = "paper_guide_curve_"
PAPER_GUIDE_GP_DATA_PREFIX = "paper_guide_gp_"
PAPER_SAFE_FILL_MESH_PREFIX = "paper_safe_area_mesh_"
PAPER_BLEED_OUTER_FILL_MESH_PREFIX = "paper_bleed_outer_mesh_"
PAPER_GUIDE_MATERIAL_PREFIX = "BManga_PaperGuide_"
_OLD_SAFE_FILL_MATERIAL = "BManga_SafeAreaFill"
PAPER_SAFE_FILL_VIEW_MATERIAL = "BManga_SafeAreaFill_View"
PAPER_BLEED_OUTER_FILL_VIEW_MATERIAL = "BManga_BleedOuterFill_View"

PROP_GUIDE_KIND = "bmanga_paper_guide_kind"
PROP_GUIDE_OWNER_ID = "bmanga_paper_guide_page_id"
PROP_GUIDE_SIGNATURE = "bmanga_paper_guide_signature"
GUIDE_KIND_LINES = "guides"
_OLD_LINE_KINDS = {"dim", "light", "inner", "safe"}

# 実体ガイド線をビュー上で一定の太さ (おおよそこのピクセル幅) に保つ。
GUIDE_SCREEN_PX = 1.0
# Grease Pencil v3 の point.radius はビューポート上で指定 world 半径より太く出るため、
# 実機スクリーンショットで 1px に見える係数へ補正する。
_GUIDE_GP_RADIUS_SCALE = 0.1
_GUIDE_CURVE_RADIUS_SCALE = 1.0
_GUIDE_THICKNESS_INTERVAL = runtime_activity.GUIDE_ACTIVE_INTERVAL
_GUIDE_IDLE_INTERVAL = runtime_activity.GUIDE_IDLE_INTERVAL
_GUIDE_REPAIR_INTERVAL = 1.0
_GUIDE_Z_CLEARANCE_M = 0.012
_last_mpp: float = -1.0
_last_repair_time: float = 0.0


def _live_guide_updates_allowed() -> bool:
    return runtime_activity.work_loaded(bpy.context) and runtime_activity.live_view_updates_allowed(bpy.context)


def _opaque_rgba(rgba: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    try:
        return (
            max(0.0, min(1.0, float(rgba[0]))),
            max(0.0, min(1.0, float(rgba[1]))),
            max(0.0, min(1.0, float(rgba[2]))),
            1.0,
        )
    except Exception:  # noqa: BLE001
        return (0.0, 0.82, 1.0, 1.0)


def _gp_material(name: str, rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    rgba = _opaque_rgba(rgba)
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
    _setup_guide_view_material(mat, rgba)
    return mat


def _setup_guide_view_material(mat: bpy.types.Material, rgba: tuple[float, float, float, float]) -> None:
    _set_material_opaque(mat, rgba)
    try:
        mat.use_nodes = True
    except Exception:  # noqa: BLE001
        return
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (220, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (0, 0)
    emission.inputs["Color"].default_value = rgba
    emission.inputs["Strength"].default_value = 1.0
    nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    _set_material_opaque(mat, rgba)


def _set_material_opaque(mat: bpy.types.Material, rgba: tuple[float, float, float, float]) -> None:
    try:
        mat.blend_method = "OPAQUE"
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.surface_render_method = "DITHERED"
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        mat.show_transparent_back = False
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.diffuse_color = rgba
    except Exception:  # noqa: BLE001
        pass


def _guide_material_is_translucent(mat: bpy.types.Material) -> bool:
    try:
        if float(mat.diffuse_color[3]) < 0.999:
            return True
    except Exception:  # noqa: BLE001
        return True
    try:
        if str(getattr(mat, "blend_method", "")) == "BLEND":
            return True
    except Exception:  # noqa: BLE001
        return True
    try:
        if str(getattr(mat, "surface_render_method", "")) == "BLENDED":
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


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


def _append_loop(
    curve: bpy.types.Curve,
    points: Iterable[tuple[float, float]],
    *,
    material_index: int = 0,
) -> None:
    pts = list(points)
    if len(pts) < 2:
        return
    spline = curve.splines.new(type="POLY")
    spline.points.add(len(pts) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, pts, strict=False):
        point.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0, 1.0)
    spline.use_cyclic_u = len(pts) >= 3
    try:
        spline.material_index = max(0, int(material_index))
    except Exception:  # noqa: BLE001
        pass


def _append_segment(
    curve: bpy.types.Curve,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    material_index: int = 0,
) -> None:
    spline = curve.splines.new(type="POLY")
    spline.points.add(1)
    spline.points[0].co = (mm_to_m(start[0]), mm_to_m(start[1]), 0.0, 1.0)
    spline.points[1].co = (mm_to_m(end[0]), mm_to_m(end[1]), 0.0, 1.0)
    spline.use_cyclic_u = False
    try:
        spline.material_index = max(0, int(material_index))
    except Exception:  # noqa: BLE001
        pass


def _guide_curve_radius_m() -> float:
    if _last_mpp > 0.0:
        half = GUIDE_SCREEN_PX * _last_mpp * 0.5 * _GUIDE_CURVE_RADIUS_SCALE
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


def _remove_old_line_objects(page_id: str, keep_names: set[str]) -> None:
    for obj in list(bpy.data.objects):
        owner = str(obj.get(PROP_GUIDE_OWNER_ID, "") or "")
        if owner != page_id or obj.name in keep_names:
            continue
        kind = str(obj.get(PROP_GUIDE_KIND, "") or "")
        if kind == GUIDE_KIND_LINES or kind in _OLD_LINE_KINDS or obj.name.startswith(f"{PAPER_GUIDE_PREFIX}{page_id}_"):
            _remove_object(obj)


def _remove_stale_guide_objects(valid_ids: set[str]) -> None:
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


def _ensure_curve_object_data(obj_name: str, curve: bpy.types.Curve) -> bpy.types.Object:
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and getattr(obj, "type", "") != "CURVE":
        _remove_object(obj)
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, curve)
    elif obj.data is not curve:
        obj.data = curve
    return obj


def _curve_material_index(curve: bpy.types.Curve, material: bpy.types.Material) -> int:
    mats = getattr(curve, "materials", None)
    if mats is None:
        return 0
    for i, existing in enumerate(mats):
        if existing is material or getattr(existing, "name", None) == material.name:
            return i
    try:
        mats.append(material)
        return len(mats) - 1
    except Exception:  # noqa: BLE001
        _logger.exception("paper guide curve material append failed")
        return 0


def _guide_sets_have_geometry(guide_sets) -> bool:
    for _label, loops, segments, *_rest in guide_sets:
        if any(bool(loop) for loop in loops):
            return True
        if bool(segments):
            return True
    return False


def _ensure_single_curve_guide_object(
    scene,
    page,
    page_coll,
    guide_sets,
    *,
    z_m: float,
    visible: bool,
) -> bpy.types.Object:
    page_id = str(getattr(page, "id", "") or "")
    obj_name = f"{PAPER_GUIDE_PREFIX}{page_id}"
    curve_name = f"{PAPER_GUIDE_CURVE_PREFIX}{page_id}_lines"

    curve = bpy.data.curves.get(curve_name)
    if curve is None:
        curve = bpy.data.curves.new(curve_name, type="CURVE")
    curve.dimensions = "3D"
    try:
        curve.fill_mode = "FULL"
    except Exception:  # noqa: BLE001
        pass
    curve.bevel_depth = _guide_curve_radius_m()
    curve.bevel_resolution = 0
    curve.resolution_u = 1
    while len(curve.splines):
        curve.splines.remove(curve.splines[0])
    _clear_material_slots(curve)

    has_geometry = False
    for _label, loops, segments, mat in guide_sets:
        material_index = _curve_material_index(curve, mat)
        for loop in loops:
            if not loop:
                continue
            _append_loop(curve, loop, material_index=material_index)
            has_geometry = True
        for start, end in segments:
            _append_segment(curve, start, end, material_index=material_index)
            has_geometry = True

    obj = _ensure_curve_object_data(obj_name, curve)
    obj[PROP_GUIDE_KIND] = GUIDE_KIND_LINES
    obj[PROP_GUIDE_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.hide_render = True
    try:
        obj.show_in_front = False
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.show_transparent = False
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.display_type = "TEXTURED"
    except Exception:  # noqa: BLE001
        pass
    obj.hide_viewport = not (visible and has_geometry)
    obj.location.z = z_m
    _set_page_location(scene, obj, page)
    _link_to_page_collection(obj, page_coll)
    _remove_old_line_objects(page_id, {obj.name})
    return obj


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _safe_fill_view_color(work) -> tuple[float, float, float, float]:
    overlay = getattr(work, "safe_area_overlay", None)
    color = getattr(overlay, "color", (0.0, 0.0, 0.0)) if overlay is not None else (0.0, 0.0, 0.0)
    opacity = percentage.percent_to_factor(
        getattr(overlay, "opacity", 30.0) if overlay is not None else 30.0,
        30.0,
    )
    try:
        r, g, b = float(color[0]), float(color[1]), float(color[2])
    except Exception:  # noqa: BLE001
        r, g, b = 0.0, 0.0, 0.0
    return (_clamp01(r), _clamp01(g), _clamp01(b), _clamp01(float(opacity or 0.0)))


def _bleed_outer_fill_view_color(work) -> tuple[float, float, float, float]:
    overlay = getattr(work, "safe_area_overlay", None)
    color = (
        getattr(overlay, "bleed_outer_color", (0.0, 0.0, 0.0))
        if overlay is not None
        else (0.0, 0.0, 0.0)
    )
    opacity = percentage.percent_to_factor(
        getattr(overlay, "bleed_outer_opacity", 100.0) if overlay is not None else 100.0,
        100.0,
    )
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


def _transparent_fill_view_material(
    name: str,
    rgba: tuple[float, float, float, float],
) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    r, g, b, a = (
        _clamp01(float(rgba[0])),
        _clamp01(float(rgba[1])),
        _clamp01(float(rgba[2])),
        _clamp01(float(rgba[3])),
    )
    try:
        mat.diffuse_color = (r, g, b, a)
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.use_nodes = True
        mat.blend_method = "BLEND"
        mat.show_transparent_back = False
        mat.use_backface_culling = True
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    try:
        mat.use_transparency_overlap = False
    except (AttributeError, TypeError):
        pass
    nt = mat.node_tree
    if nt is not None:
        for node in list(nt.nodes):
            nt.nodes.remove(node)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (320, 0)
        emission = nt.nodes.new("ShaderNodeEmission")
        emission.location = (0, 110)
        transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
        transparent.location = (0, -110)
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.location = (160, 0)
        try:
            emission.inputs["Color"].default_value = (r, g, b, 1.0)
            emission.inputs["Strength"].default_value = 1.0
            mix.inputs["Fac"].default_value = a
            nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
            nt.links.new(emission.outputs["Emission"], mix.inputs[2])
            nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        except Exception:  # noqa: BLE001
            _logger.exception("safe area fill material setup failed")
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _tag_fill_object_updated(obj: bpy.types.Object) -> None:
    mesh = getattr(obj, "data", None)
    try:
        if mesh is not None:
            mesh.update()
    except Exception:  # noqa: BLE001
        pass
    try:
        if mesh is not None:
            mesh.update_tag()
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.update_tag(refresh={"OBJECT", "DATA"})
    except Exception:  # noqa: BLE001
        pass


def _safe_fill_view_material(rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    return _transparent_fill_view_material(PAPER_SAFE_FILL_VIEW_MATERIAL, rgba)


def _bleed_outer_fill_view_material(rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    return _transparent_fill_view_material(PAPER_BLEED_OUTER_FILL_VIEW_MATERIAL, rgba)


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


def _fill_faces_from_rect_pairs(
    rect_pairs: Iterable[tuple[Rect, Rect]],
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int, int]]]:
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    for canvas, inner in rect_pairs:
        part_verts, part_faces = _safe_fill_faces(canvas, inner)
        base = len(verts)
        verts.extend(part_verts)
        faces.extend(tuple(base + index for index in face) for face in part_faces)
    return verts, faces


def _bleed_outer_fill_is_visible(work) -> bool:
    overlay = getattr(work, "safe_area_overlay", None)
    if overlay is None or not bool(getattr(overlay, "bleed_outer_enabled", True)):
        return False
    return _bleed_outer_fill_view_color(work)[3] > 0.0


def _fill_rect_pairs_for_page(work, page_index: int, page, rects):
    if not bool(getattr(page, "spread", False)):
        safe_outer = rects.bleed if _bleed_outer_fill_is_visible(work) else rects.canvas
        return [(safe_outer, rects.safe)], [(rects.canvas, rects.bleed)]
    paper = getattr(work, "paper", None)
    if paper is None:
        safe_outer = rects.bleed if _bleed_outer_fill_is_visible(work) else rects.canvas
        return [(safe_outer, rects.safe)], [(rects.canvas, rects.bleed)]
    try:
        combined = spread_merge_geometry.combined_spread_rects(paper, page)
    except Exception:  # noqa: BLE001
        _logger.exception("spread fill rect calculation failed")
        safe_outer = rects.bleed if _bleed_outer_fill_is_visible(work) else rects.canvas
        return [(safe_outer, rects.safe)], [(rects.canvas, rects.bleed)]
    safe_outer = combined.bleed if _bleed_outer_fill_is_visible(work) else combined.canvas
    return [(safe_outer, combined.safe)], [(combined.canvas, combined.bleed)]


def _ensure_safe_fill_object(
    scene,
    work,
    page,
    page_coll,
    rect_pairs: Iterable[tuple[Rect, Rect]],
    z_m: float,
) -> bpy.types.Object:
    page_id = str(getattr(page, "id", "") or "")
    mesh_name = f"{PAPER_SAFE_FILL_MESH_PREFIX}{page_id}"
    obj_name = f"{PAPER_SAFE_FILL_PREFIX}{page_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    verts, faces = _fill_faces_from_rect_pairs(rect_pairs)
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()
    _clear_material_slots(mesh)

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
    mesh.materials.append(_safe_fill_view_material(tuple(obj.color)))
    _remove_old_safe_fill_material_if_unused()
    try:
        # B-MANGA のページ一覧ビューはコマプレビュー表示のためテクスチャ表示にする。
        # この面だけはビュー表示カラーを使うので、オブジェクト側をソリッド表示へ固定する。
        obj.display_type = "SOLID"
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.show_in_front = False
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
    _tag_fill_object_updated(obj)
    return obj


def _ensure_bleed_outer_fill_object(
    scene,
    work,
    page,
    page_coll,
    rect_pairs: Iterable[tuple[Rect, Rect]],
    z_m: float,
) -> bpy.types.Object:
    page_id = str(getattr(page, "id", "") or "")
    mesh_name = f"{PAPER_BLEED_OUTER_FILL_MESH_PREFIX}{page_id}"
    obj_name = f"{PAPER_BLEED_OUTER_FILL_PREFIX}{page_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    verts, faces = _fill_faces_from_rect_pairs(rect_pairs)
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()
    _clear_material_slots(mesh)

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    obj[PROP_GUIDE_KIND] = "bleed_outer_fill"
    obj[PROP_GUIDE_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.color = _bleed_outer_fill_view_color(work)
    mesh.materials.append(_bleed_outer_fill_view_material(tuple(obj.color)))
    try:
        obj.display_type = "SOLID"
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.show_in_front = False
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.show_transparent = True
    except Exception:  # noqa: BLE001
        pass
    overlay = getattr(work, "safe_area_overlay", None)
    obj.hide_render = True
    obj.hide_viewport = not (
        bool(getattr(page, "in_page_range", True))
        and bool(getattr(overlay, "bleed_outer_enabled", True))
        and obj.color[3] > 0.0
    )
    obj.location.z = z_m
    _set_page_location(scene, obj, page)
    _link_to_page_collection(obj, page_coll)
    _tag_fill_object_updated(obj)
    return obj


def _is_coma_mode_guide(scene) -> bool:
    try:
        from . import page_file_scene
        role, _pid, _cid = page_file_scene.current_role(bpy.context)
        return role == page_file_scene.ROLE_COMA
    except Exception:  # noqa: BLE001
        return False


def _coma_origin_mm_guide(scene, work):
    """コマモード時の現在ページの中心座標 (mm) を返す."""
    try:
        from . import page_file_scene, page_grid as _pg

        _role, page_id, _cid = page_file_scene.current_role(bpy.context)
        cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
        ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
        cols = max(1, int(getattr(scene, "bmanga_overview_cols", 4) or 4))
        gap_x, gap_y = _pg.resolve_gap_mm(scene)
        start_side = getattr(work.paper, "start_side", "right")
        read_direction = getattr(work.paper, "read_direction", "left")
        for i, p in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(p, "id", "") or "") == page_id:
                ox, oy = _pg.page_grid_offset_mm(
                    i, cols, gap_x, cw, ch, start_side, read_direction,
                    work=work, gap_y_mm=gap_y,
                )
                add_x, add_y = _pg.page_manual_offset_mm(p)
                pw = _pg.page_content_width_mm(work, i, cw)
                return (ox + add_x + pw * 0.5, oy + add_y + ch * 0.5)
    except Exception:  # noqa: BLE001
        pass
    return None


def _set_page_location_by_index(scene, work, obj: bpy.types.Object, page_index: int) -> None:
    if scene is None or work is None or obj is None:
        return
    try:
        from . import page_grid

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, page_index)

        if _is_coma_mode_guide(scene):
            origin = _coma_origin_mm_guide(scene, work)
            if origin is not None:
                org_x, org_y = origin
                x_m = mm_to_m(ox_mm - org_x)
                z_m = mm_to_m(oy_mm - org_y)
                depth = float(obj.location.y)
                obj.location = (x_m, depth, z_m)
                obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
                return

        x_m = mm_to_m(ox_mm)
        y_m = mm_to_m(oy_mm)
        loc = obj.location
        needs_rot_reset = (abs(float(obj.rotation_euler.x)) > 1.0e-6)
        if abs(float(loc.x) - x_m) <= 1.0e-9 and abs(float(loc.y) - y_m) <= 1.0e-9 and not needs_rot_reset:
            return
        obj.location = (x_m, y_m, loc.z)
        if needs_rot_reset:
            obj.rotation_euler = (0.0, 0.0, 0.0)
    except Exception:  # noqa: BLE001
        _logger.exception("paper guide page offset failed")


def _set_page_location(scene, obj: bpy.types.Object, page) -> None:
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
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

        if _is_coma_mode_guide(scene):
            origin = _coma_origin_mm_guide(scene, work)
            if origin is not None:
                org_x, org_y = origin
                depth = float(obj.location.z)
                obj.location.x = mm_to_m(ox_mm - org_x)
                obj.location.y = depth
                obj.location.z = mm_to_m(oy_mm - org_y)
                obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
                return

        obj.location.x = mm_to_m(ox_mm)
        obj.location.y = mm_to_m(oy_mm)
        if abs(float(obj.rotation_euler.x)) > 1.0e-6:
            obj.rotation_euler = (0.0, 0.0, 0.0)
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
    max_all = 0.0
    # コマの重なり順 (z_order) に追従させるため、ページ上で最も手前に来るコマ要素の z を求める。
    # コマ枠線・白フチ・コマ面の実体は parent_key を持たず、下の max_all 走査では拾えない。
    # そのためコマデータから直接 z を算出して上限に含める。これを怠ると重なり順の深いコマで
    # ガイド線が枠線・白フチと同一深度に並び、点滅 (Z 競合) や枠線の裏に隠れる不具合が起きる。
    max_coma_z = 0.0
    try:
        from . import coma_z_order

        for page in getattr(work, "pages", []) or []:
            if str(getattr(page, "id", "") or "") != str(page_id):
                continue
            for coma in getattr(page, "comas", []) or []:
                max_coma_z = max(
                    max_coma_z,
                    float(coma_z_order.border_z(coma)),
                    float(coma_z_order.white_margin_z(coma)),
                    float(coma_z_order.plane_z(coma)),
                )
            break
    except Exception:  # noqa: BLE001
        pass
    for obj in bpy.data.objects:
        if obj.get(PROP_GUIDE_OWNER_ID):
            continue
        if _object_page_id(obj, work) != page_id:
            continue
        z = float(getattr(obj.location, "z", 0.0) or 0.0)
        max_all = max(max_all, z)
    top_z = max(max_all, max_coma_z, 0.0)
    safe_z = top_z + _GUIDE_Z_CLEARANCE_M
    guide_z = safe_z + 0.001
    return safe_z, guide_z, max_all


def _is_left_page(paper, page_index: int, work=None) -> bool:
    try:
        from . import page_grid

        return page_grid.is_left_half_page(
            page_index,
            getattr(paper, "start_side", "right"),
            getattr(paper, "read_direction", "left"),
            work=work,
        )
    except Exception:  # noqa: BLE001
        return False


def _paper_guide_materials() -> tuple[bpy.types.Material, bpy.types.Material, bpy.types.Material, bpy.types.Material]:
    return (
        _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Dim", viewport_colors.PAPER_GUIDE_DIM),
        _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Light", viewport_colors.PAPER_GUIDE_LIGHT),
        _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Guide", viewport_colors.PAPER_GUIDE),
        _gp_material(f"{PAPER_GUIDE_MATERIAL_PREFIX}Safe", viewport_colors.SAFE_LINE),
    )


def _paper_guide_geometry_sets(paper, rects) -> list[tuple[str, list, list]]:
    guides_visible = bool(getattr(paper, "show_guides", True))
    bleed_enabled = float(getattr(paper, "bleed_mm", 0.0) or 0.0) > 0.0
    return [
        (
            "dim",
            [
                _rect_loop(rects.canvas) if guides_visible and getattr(paper, "show_canvas_frame", True) else [],
                _rect_loop(rects.bleed)
                if guides_visible and bleed_enabled and getattr(paper, "show_bleed_frame", True)
                else [],
            ],
            [],
        ),
        (
            "light",
            [_rect_loop(rects.finish) if guides_visible and getattr(paper, "show_finish_frame", True) else []],
            _trim_segments(rects.finish, rects.bleed)
            if guides_visible and bleed_enabled and getattr(paper, "show_trim_marks", True)
            else [],
        ),
        (
            "inner",
            [_rect_loop(rects.inner_frame) if guides_visible and getattr(paper, "show_inner_frame", True) else []],
            [],
        ),
        (
            "safe",
            [_rect_loop(rects.safe) if guides_visible and getattr(paper, "show_safe_line", True) else []],
            [],
        ),
    ]


def _paper_guide_sets(paper, rects) -> list[tuple[str, list, list, bpy.types.Material]]:
    return [
        (label, loops, segments, material)
        for (label, loops, segments), material in zip(
            _paper_guide_geometry_sets(paper, rects),
            _paper_guide_materials(),
        )
    ]


def _shift_loop(loop, dx_mm: float) -> list[tuple[float, float]]:
    return [(float(x) + dx_mm, float(y)) for x, y in loop]


def _shift_segments(segments, dx_mm: float):
    return [
        ((float(a[0]) + dx_mm, float(a[1])), (float(b[0]) + dx_mm, float(b[1])))
        for a, b in segments
    ]


def _shift_guide_sets(guide_sets, dx_mm: float):
    shifted = []
    for label, loops, segments, material in guide_sets:
        shifted.append((
            label,
            [_shift_loop(loop, dx_mm) for loop in loops],
            _shift_segments(segments, dx_mm),
            material,
        ))
    return shifted


def _merge_guide_sets(*sets):
    merged: dict[str, list] = {}
    order: list[str] = []
    for guide_sets in sets:
        for label, loops, segments, material in guide_sets:
            if label not in merged:
                merged[label] = [[], [], material]
                order.append(label)
            merged[label][0].extend(loops)
            merged[label][1].extend(segments)
    return [(label, merged[label][0], merged[label][1], merged[label][2]) for label in order]


def _spread_page_guide_sets(paper, page):
    from . import page_grid

    canvas_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    right_offset = page_grid.spread_right_page_offset_mm(page, canvas_width)
    left_rects = overlay_shared.compute_paper_rects(paper, is_left_half=True)
    right_rects = overlay_shared.compute_paper_rects(paper, is_left_half=False)
    page_pair_sets = _merge_guide_sets(
        _paper_guide_sets(paper, left_rects),
        _shift_guide_sets(_paper_guide_sets(paper, right_rects), right_offset),
    )
    combined_rects = spread_merge_geometry.combined_spread_rects(paper, page)
    combined_sets = _paper_guide_sets(paper, combined_rects)
    page_pair_labels = {"dim", "light"}
    combined_by_label = {label: (label, loops, segments, material) for label, loops, segments, material in combined_sets}
    guide_sets = []
    for label, loops, segments, material in page_pair_sets:
        if label in page_pair_labels:
            guide_sets.append((label, loops, segments, material))
        else:
            guide_sets.append(combined_by_label.get(label, (label, loops, segments, material)))
    return guide_sets, combined_rects


def _paper_guide_sets_for_page(work, page_index: int, page):
    paper = getattr(work, "paper", None)
    try:
        from . import page_grid

        grid_page_index = page_grid.original_page_index(work, page_index)
    except Exception:  # noqa: BLE001
        page_grid = None
        grid_page_index = page_index
    if bool(getattr(page, "spread", False)) and page_grid is not None:
        guide_sets, combined_rects = _spread_page_guide_sets(paper, page)
        return guide_sets, combined_rects, grid_page_index

    rects = overlay_shared.compute_paper_rects(
        paper,
        is_left_half=_is_left_page(paper, grid_page_index, work=work),
    )
    return _paper_guide_sets(paper, rects), rects, grid_page_index


def _paper_guide_signature(work, page_index: int, page, rects) -> str:
    paper = getattr(work, "paper", None)
    overlay = getattr(work, "safe_area_overlay", None)
    try:
        from . import page_grid

        tombo_aligned = page_grid.page_spread_tombo_aligned(page)
        tombo_gap_mm = page_grid.page_spread_tombo_gap_mm(page)
    except Exception:  # noqa: BLE001
        tombo_aligned = bool(getattr(page, "tombo_aligned", True))
        tombo_gap_mm = -9.6
    attrs = (
        "canvas_width_mm",
        "canvas_height_mm",
        "finish_width_mm",
        "finish_height_mm",
        "bleed_mm",
        "inner_frame_width_mm",
        "inner_frame_height_mm",
        "inner_frame_offset_x_mm",
        "inner_frame_offset_y_mm",
        "safe_top_mm",
        "safe_bottom_mm",
        "safe_gutter_mm",
        "safe_fore_edge_mm",
        "show_canvas_frame",
        "show_guides",
        "show_bleed_frame",
        "show_finish_frame",
        "show_inner_frame",
        "show_safe_line",
        "show_trim_marks",
        "start_side",
        "read_direction",
    )
    paper_values = []
    for attr in attrs:
        value = getattr(paper, attr, None)
        if isinstance(value, float):
            value = round(value, 6)
        paper_values.append(value)
    safe_color = _safe_fill_view_color(work)
    bleed_outer_color = _bleed_outer_fill_view_color(work)
    return repr((
        "paper_guide_spread_fill_v5",
        int(page_index),
        str(getattr(page, "id", "") or ""),
        bool(getattr(page, "spread", False)),
        bool(tombo_aligned),
        round(float(tombo_gap_mm), 6),
        bool(getattr(page, "in_page_range", True)),
        tuple(paper_values),
        tuple(round(float(c), 6) for c in safe_color),
        bool(getattr(overlay, "enabled", True)) if overlay is not None else True,
        tuple(round(float(c), 6) for c in bleed_outer_color),
        bool(getattr(overlay, "bleed_outer_enabled", True)) if overlay is not None else True,
        round(float(rects.canvas.x), 6),
        round(float(rects.canvas.y), 6),
        round(float(rects.canvas.width), 6),
        round(float(rects.canvas.height), 6),
        round(float(rects.bleed.x), 6),
        round(float(rects.bleed.y), 6),
        round(float(rects.bleed.width), 6),
        round(float(rects.bleed.height), 6),
        round(float(rects.safe.x), 6),
        round(float(rects.safe.y), 6),
        round(float(rects.safe.width), 6),
        round(float(rects.safe.height), 6),
    ))


def _ensure_curve_guides(
    scene,
    page,
    page_coll,
    guide_sets,
    *,
    guide_z: float,
    visible: bool,
) -> list[bpy.types.Object]:
    obj = _ensure_single_curve_guide_object(
        scene,
        page,
        page_coll,
        guide_sets,
        z_m=guide_z,
        visible=visible,
    )
    return [obj]


def ensure_paper_guides_for_page(scene, work, page_index: int) -> list[bpy.types.Object]:
    if scene is None or work is None or not (0 <= page_index < len(getattr(work, "pages", []) or [])):
        return []
    page = work.pages[page_index]
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return []
    paper = work.paper
    guide_sets, rects, grid_page_index = _paper_guide_sets_for_page(work, page_index, page)
    page_coll = on.find_collection_by_bmanga_id(page_id, kind="page")
    if page_coll is None:
        page_coll = om.ensure_page_collection(scene, page_id, str(getattr(page, "title", "") or page_id))
    in_range = bool(getattr(page, "in_page_range", True))
    safe_z, guide_z, _ = _page_z_levels(work, page_id)
    signature = _paper_guide_signature(work, grid_page_index, page, rects)
    safe_fill_rect_pairs, bleed_outer_fill_rect_pairs = _fill_rect_pairs_for_page(work, page_index, page, rects)

    objects = _ensure_curve_guides(scene, page, page_coll, guide_sets, guide_z=guide_z, visible=in_range)
    objects.append(_ensure_safe_fill_object(scene, work, page, page_coll, safe_fill_rect_pairs, safe_z))
    objects.append(
        _ensure_bleed_outer_fill_object(
            scene,
            work,
            page,
            page_coll,
            bleed_outer_fill_rect_pairs,
            safe_z + 0.0005,
        )
    )
    for obj in objects:
        try:
            obj[PROP_GUIDE_SIGNATURE] = signature
        except Exception:  # noqa: BLE001
            pass
    return objects


def regenerate_all_paper_guides(scene, work) -> int:
    if scene is None or work is None or not getattr(work, "loaded", False):
        return 0
    from ..core.mode import MODE_COMA, get_mode
    if get_mode() == MODE_COMA:
        return 0
    valid_ids: set[str] = set()
    count = 0
    for i, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        if page_id:
            valid_ids.add(page_id)
        count += len(ensure_paper_guides_for_page(scene, work, i))
    _remove_stale_guide_objects(valid_ids)
    return count


def sync_paper_guides_after_page_transform(scene, work) -> int:
    """ページ位置変更後、既存ガイドは位置だけ更新し、必要なページだけ再生成する."""
    if scene is None or work is None or not getattr(work, "loaded", False):
        return 0
    valid_ids: set[str] = set()
    changed = 0
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            continue
        valid_ids.add(page_id)
        paper = work.paper
        _guide_sets, rects, grid_page_index = _paper_guide_sets_for_page(work, page_index, page)
        signature = _paper_guide_signature(work, grid_page_index, page, rects)
        line_obj = _line_guide_object(page_id)
        safe_obj = _safe_fill_object(page_id)
        bleed_outer_obj = _bleed_outer_fill_object(page_id)
        if (
            line_obj is None
            or safe_obj is None
            or bleed_outer_obj is None
            or str(line_obj.get(PROP_GUIDE_SIGNATURE, "") or "") != signature
            or str(safe_obj.get(PROP_GUIDE_SIGNATURE, "") or "") != signature
            or str(bleed_outer_obj.get(PROP_GUIDE_SIGNATURE, "") or "") != signature
            or _guide_curve_objects(page_id)
        ):
            changed += len(ensure_paper_guides_for_page(scene, work, page_index))
            continue
        in_range = bool(getattr(page, "in_page_range", True))
        try:
            if line_obj.hide_viewport == in_range:
                line_obj.hide_viewport = not in_range
        except Exception:  # noqa: BLE001
            pass
        try:
            visible_safe = (
                in_range
                and bool(getattr(paper, "show_guides", True))
                and bool(getattr(work.safe_area_overlay, "enabled", True))
                and float(safe_obj.color[3]) > 0.0
            )
            if safe_obj.hide_viewport == visible_safe:
                safe_obj.hide_viewport = not visible_safe
        except Exception:  # noqa: BLE001
            pass
        try:
            overlay = getattr(work, "safe_area_overlay", None)
            visible_bleed_outer = (
                in_range
                and bool(getattr(paper, "show_guides", True))
                and bool(getattr(overlay, "bleed_outer_enabled", True))
                and float(bleed_outer_obj.color[3]) > 0.0
            )
            if bleed_outer_obj.hide_viewport == visible_bleed_outer:
                bleed_outer_obj.hide_viewport = not visible_bleed_outer
        except Exception:  # noqa: BLE001
            pass
        _set_page_location_by_index(scene, work, line_obj, page_index)
        _set_page_location_by_index(scene, work, safe_obj, page_index)
        _set_page_location_by_index(scene, work, bleed_outer_obj, page_index)
        changed += 3
    _remove_stale_guide_objects(valid_ids)
    return changed


# ---------- ビュー上で一定太さに保つ ----------


def _active_view3d_region():
    """最も大きい 3D ビューポートを基準にする (ページ一覧の小窓と併存するため)."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return None
    best = None
    best_size = 0
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
                    size = int(region.width) * int(region.height)
                    if size > best_size:
                        best = (region, rv3d)
                        best_size = size
    return best


def _meters_per_pixel(region, rv3d) -> Optional[float]:
    try:
        from bpy_extras import view3d_utils
    except Exception:  # noqa: BLE001
        return None
    sample_m = mm_to_m(10.0)
    try:
        p0 = view3d_utils.location_3d_to_region_2d(region, rv3d, (0.0, 0.0, 0.0))
        px = view3d_utils.location_3d_to_region_2d(region, rv3d, (sample_m, 0.0, 0.0))
        py = view3d_utils.location_3d_to_region_2d(region, rv3d, (0.0, sample_m, 0.0))
        distances = []
        if p0 is not None and px is not None:
            distances.append((px - p0).length)
        if p0 is not None and py is not None:
            distances.append((py - p0).length)
        valid = [dist for dist in distances if dist > 1.0e-6]
        if valid:
            return sample_m / (sum(valid) / len(valid))
    except Exception:  # noqa: BLE001
        pass
    cx = region.width * 0.5
    cy = region.height * 0.5
    p0 = view3d_utils.region_2d_to_location_3d(region, rv3d, (cx, cy), (0.0, 0.0, 0.0))
    p1 = view3d_utils.region_2d_to_location_3d(region, rv3d, (cx + 1.0, cy), (0.0, 0.0, 0.0))
    if p0 is None or p1 is None:
        return None
    d = (p1 - p0).length
    return d if d > 1.0e-9 else None


def apply_view_constant_thickness() -> bool:
    """全用紙ガイド線の太さをビュー倍率に合わせ、画面上で一定太さに保つ."""
    global _last_mpp
    found = _active_view3d_region()
    if found is None:
        return False
    region, rv3d = found
    mpp = _meters_per_pixel(region, rv3d)
    if mpp is None:
        return False
    if _last_mpp > 0.0 and abs(mpp - _last_mpp) <= _last_mpp * 0.03:
        return False
    _last_mpp = mpp
    # 異常な視点 (極端なズーム/パース) で bevel が暴れないようクランプ。
    # 0.005mm 〜 3mm 相当の線幅に収める。
    curve_half = GUIDE_SCREEN_PX * mpp * 0.5 * _GUIDE_CURVE_RADIUS_SCALE
    curve_half = max(mm_to_m(0.005) * 0.5, min(curve_half, mm_to_m(3.0) * 0.5))
    gp_half = GUIDE_SCREEN_PX * mpp * 0.5 * _GUIDE_GP_RADIUS_SCALE
    gp_half = max(mm_to_m(0.005) * 0.5, min(gp_half, mm_to_m(3.0) * 0.5))
    changed = False
    for curve in bpy.data.curves:
        if not curve.name.startswith(PAPER_GUIDE_CURVE_PREFIX):
            continue
        try:
            if abs(float(curve.bevel_depth) - curve_half) > curve_half * 0.02:
                curve.bevel_depth = curve_half
                changed = True
        except Exception:  # noqa: BLE001
            continue
    for obj in bpy.data.objects:
        if obj.get(PROP_GUIDE_KIND) != GUIDE_KIND_LINES or getattr(obj, "type", "") != "GREASEPENCIL":
            continue
        if _set_gp_stroke_radius(obj, gp_half):
            changed = True
    return changed


def _guide_strokes(obj: bpy.types.Object):
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return []
    strokes = []
    for layer in layers:
        for frame in getattr(layer, "frames", []) or []:
            drawing = getattr(frame, "drawing", None)
            for stroke in getattr(drawing, "strokes", []) or []:
                strokes.append(stroke)
    return strokes


def _materials_are_current(obj: bpy.types.Object) -> bool:
    expected = {
        f"{PAPER_GUIDE_MATERIAL_PREFIX}Dim": _opaque_rgba(viewport_colors.PAPER_GUIDE_DIM),
        f"{PAPER_GUIDE_MATERIAL_PREFIX}Light": _opaque_rgba(viewport_colors.PAPER_GUIDE_LIGHT),
        f"{PAPER_GUIDE_MATERIAL_PREFIX}Guide": _opaque_rgba(viewport_colors.PAPER_GUIDE),
        f"{PAPER_GUIDE_MATERIAL_PREFIX}Safe": _opaque_rgba(viewport_colors.SAFE_LINE),
    }
    materials = list(getattr(getattr(obj, "data", None), "materials", []) or [])
    if len(materials) < len(expected):
        return False
    material_names = {str(getattr(mat, "name", "") or "") for mat in materials}
    for name, rgba in expected.items():
        if name not in material_names:
            return False
        mat = bpy.data.materials.get(name)
        if mat is None:
            return False
        try:
            diffuse = tuple(float(c) for c in mat.diffuse_color[:4])
            if any(abs(diffuse[i] - rgba[i]) > 1.0e-4 for i in range(4)):
                return False
        except Exception:  # noqa: BLE001
            return False
        gp_style = getattr(mat, "grease_pencil", None)
        if gp_style is None:
            return False
        try:
            stroke_color = tuple(float(c) for c in gp_style.color[:4])
            if any(abs(stroke_color[i] - rgba[i]) > 1.0e-4 for i in range(4)):
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


def _curve_has_visible_geometry(obj: bpy.types.Object) -> bool:
    if getattr(obj, "type", "") != "CURVE":
        return False
    curve = getattr(obj, "data", None)
    if curve is None:
        return False
    try:
        if float(getattr(curve, "bevel_depth", 0.0) or 0.0) <= 0.0:
            return False
    except Exception:  # noqa: BLE001
        return False
    return len(getattr(curve, "splines", []) or []) > 0


def _curve_display_needs_rebuild(obj: bpy.types.Object) -> bool:
    if bool(getattr(obj, "show_in_front", False)) or bool(getattr(obj, "show_transparent", False)):
        return True
    for mat in list(getattr(getattr(obj, "data", None), "materials", []) or []):
        if _guide_material_is_translucent(mat):
            return True
    return False


def _line_guide_object(page_id: str) -> Optional[bpy.types.Object]:
    return bpy.data.objects.get(f"{PAPER_GUIDE_PREFIX}{page_id}")


def _safe_fill_object(page_id: str) -> Optional[bpy.types.Object]:
    return bpy.data.objects.get(f"{PAPER_SAFE_FILL_PREFIX}{page_id}")


def _bleed_outer_fill_object(page_id: str) -> Optional[bpy.types.Object]:
    return bpy.data.objects.get(f"{PAPER_BLEED_OUTER_FILL_PREFIX}{page_id}")


def _guide_front_order_needs_repair(work, page) -> bool:
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return False
    safe_z, guide_z, _ = _page_z_levels(work, page_id)
    line_obj = _line_guide_object(page_id)
    safe_obj = _safe_fill_object(page_id)
    bleed_outer_obj = _bleed_outer_fill_object(page_id)
    if line_obj is not None and abs(float(getattr(line_obj.location, "z", 0.0) or 0.0) - guide_z) > 1.0e-6:
        return True
    if safe_obj is not None:
        if abs(float(getattr(safe_obj.location, "z", 0.0) or 0.0) - safe_z) > 1.0e-6:
            return True
        if bool(getattr(safe_obj, "show_in_front", False)):
            return True
    if bleed_outer_obj is not None:
        if abs(float(getattr(bleed_outer_obj.location, "z", 0.0) or 0.0) - (safe_z + 0.0005)) > 1.0e-6:
            return True
        if bool(getattr(bleed_outer_obj, "show_in_front", False)):
            return True
    return False


def _line_guide_should_have_geometry(work, page) -> bool:
    paper = getattr(work, "paper", None)
    if paper is None:
        return False
    if not bool(getattr(paper, "show_guides", True)):
        return False
    if not bool(getattr(page, "in_page_range", True)):
        return False
    target_id = str(getattr(page, "id", "") or "")
    page_index = -1
    for i, candidate in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(candidate, "id", "") or "") == target_id:
            page_index = i
            break
    if page_index < 0:
        return False
    rects = overlay_shared.compute_paper_rects(paper, is_left_half=_is_left_page(paper, page_index, work=work))
    return _guide_sets_have_geometry(_paper_guide_geometry_sets(paper, rects))


def _guide_curve_objects(page_id: str) -> list[bpy.types.Object]:
    objects = []
    for obj in bpy.data.objects:
        if str(obj.get(PROP_GUIDE_OWNER_ID, "") or "") != page_id:
            continue
        if str(obj.get(PROP_GUIDE_KIND, "") or "") in _OLD_LINE_KINDS and getattr(obj, "type", "") == "CURVE":
            objects.append(obj)
    return objects


def _paper_guide_needs_repair(work, page) -> bool:
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return False
    curve_objects = _guide_curve_objects(page_id)
    line_obj = _line_guide_object(page_id)
    safe_obj = _safe_fill_object(page_id)
    bleed_outer_obj = _bleed_outer_fill_object(page_id)
    should_have_geometry = _line_guide_should_have_geometry(work, page)
    if curve_objects:
        return True
    if safe_obj is None or bleed_outer_obj is None:
        return True
    if line_obj is not None and getattr(line_obj, "type", "") != "CURVE":
        return True
    if should_have_geometry:
        if line_obj is None:
            return True
        if bool(getattr(line_obj, "hide_viewport", False)) or not _curve_has_visible_geometry(line_obj):
            return True
        if _curve_display_needs_rebuild(line_obj):
            return True
        if not _materials_are_current(line_obj):
            return True
    elif line_obj is not None:
        if _curve_has_visible_geometry(line_obj) or not bool(getattr(line_obj, "hide_viewport", False)):
            return True
    if _guide_front_order_needs_repair(work, page):
        return True
    return False


def repair_loaded_work_paper_guides(scene=None, work=None) -> bool:
    """既存ファイルに残った古い用紙ガイド線を、現在仕様へ軽量修復する."""
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    work = work or getattr(scene, "bmanga_work", None)
    if work is None or not bool(getattr(work, "loaded", False)):
        return False
    needs_rebuild = False
    for page in getattr(work, "pages", []) or []:
        if _paper_guide_needs_repair(work, page):
            needs_rebuild = True
            break
    if not needs_rebuild:
        return False
    regenerate_all_paper_guides(scene, work)
    apply_view_constant_thickness()
    return True


def _set_gp_stroke_radius(obj: bpy.types.Object, radius: float) -> bool:
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return False
    changed = False
    threshold = max(abs(float(radius)) * 0.02, 1.0e-9)
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
                        if abs(float(getattr(point, "radius", 0.0) or 0.0) - radius) > threshold:
                            point.radius = radius
                            changed = True
                    except Exception:  # noqa: BLE001
                        pass
    return changed


def _thickness_timer():
    global _last_mpp, _last_repair_time
    try:
        if not _live_guide_updates_allowed():
            _last_mpp = -1.0
            return _GUIDE_IDLE_INTERVAL
        changed = False
        now = time.monotonic()
        if now - _last_repair_time >= _GUIDE_REPAIR_INTERVAL:
            _last_repair_time = now
            changed = bool(repair_loaded_work_paper_guides())
        changed = bool(apply_view_constant_thickness()) or changed
    except Exception:  # noqa: BLE001
        _logger.exception("paper guide thickness update failed")
        return _GUIDE_IDLE_INTERVAL
    return _GUIDE_THICKNESS_INTERVAL if changed else _GUIDE_IDLE_INTERVAL


def register() -> None:
    if not bpy.app.timers.is_registered(_thickness_timer):
        # persistent=True が無いと最初のファイル切替 (ページ一覧⇄ページ⇄コマ) で
        # タイマーが消え、以後ズームしてもガイド線の太さが更新されなくなる。
        bpy.app.timers.register(
            _thickness_timer,
            first_interval=_GUIDE_THICKNESS_INTERVAL,
            persistent=True,
        )


def unregister() -> None:
    global _last_mpp
    _last_mpp = -1.0
    try:
        if bpy.app.timers.is_registered(_thickness_timer):
            bpy.app.timers.unregister(_thickness_timer)
    except Exception:  # noqa: BLE001
        pass
