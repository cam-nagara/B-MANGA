"""用紙ガイド線群とセーフライン外塗りの実オブジェクト同期."""

from __future__ import annotations

import time
from typing import Iterable, Optional

import bpy

from ..ui import overlay_shared
from . import log, runtime_activity
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
PAPER_SAFE_FILL_VIEW_MATERIAL = "BName_SafeAreaFill_View"

PROP_GUIDE_KIND = "bname_paper_guide_kind"
PROP_GUIDE_OWNER_ID = "bname_paper_guide_page_id"
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
    for _label, loops, segments, _mat in guide_sets:
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


def _safe_fill_view_material(rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.get(PAPER_SAFE_FILL_VIEW_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(PAPER_SAFE_FILL_VIEW_MATERIAL)
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
        mat.show_transparent_back = True
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.surface_render_method = "BLENDED"
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
    return mat


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
        # B-Name のページ一覧ビューはコマプレビュー表示のためテクスチャ表示にする。
        # この面だけはビュー表示カラーを使うので、オブジェクト側をソリッド表示へ固定する。
        obj.display_type = "SOLID"
    except Exception:  # noqa: BLE001
        pass
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
    safe_z = max_all + (_GUIDE_Z_CLEARANCE_M * 0.5)
    guide_z = safe_z + _GUIDE_Z_CLEARANCE_M
    return safe_z, guide_z, max_all


def _is_left_page(paper, page_index: int) -> bool:
    try:
        from . import page_grid

        return page_grid.is_left_half_page(
            page_index,
            getattr(paper, "start_side", "right"),
            getattr(paper, "read_direction", "left"),
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


def _paper_guide_sets(paper, rects) -> list[tuple[str, list, list, bpy.types.Material]]:
    mat_dim, mat_light, mat_guide, mat_safe = _paper_guide_materials()
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
            mat_dim,
        ),
        (
            "light",
            [_rect_loop(rects.finish) if guides_visible and getattr(paper, "show_finish_frame", True) else []],
            _trim_segments(rects.finish, rects.bleed)
            if guides_visible and bleed_enabled and getattr(paper, "show_trim_marks", True)
            else [],
            mat_light,
        ),
        (
            "inner",
            [_rect_loop(rects.inner_frame) if guides_visible and getattr(paper, "show_inner_frame", True) else []],
            [],
            mat_guide,
        ),
        (
            "safe",
            [_rect_loop(rects.safe) if guides_visible and getattr(paper, "show_safe_line", True) else []],
            [],
            mat_safe,
        ),
    ]


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
    is_left = _is_left_page(paper, page_index)
    rects = overlay_shared.compute_paper_rects(paper, is_left_half=is_left)
    page_coll = on.find_collection_by_bname_id(page_id, kind="page")
    if page_coll is None:
        page_coll = om.ensure_page_collection(scene, page_id, str(getattr(page, "title", "") or page_id))
    in_range = bool(getattr(page, "in_page_range", True))
    safe_z, guide_z, _ = _page_z_levels(work, page_id)

    guide_sets = _paper_guide_sets(paper, rects)
    objects = _ensure_curve_guides(scene, page, page_coll, guide_sets, guide_z=guide_z, visible=in_range)
    objects.append(_ensure_safe_fill_object(scene, work, page, page_coll, rects.canvas, rects.safe, safe_z))
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
    curve_half = GUIDE_SCREEN_PX * mpp * 0.5 * _GUIDE_CURVE_RADIUS_SCALE
    curve_half = max(mm_to_m(0.005) * 0.5, min(curve_half, mm_to_m(3.0) * 0.5))
    gp_half = GUIDE_SCREEN_PX * mpp * 0.5 * _GUIDE_GP_RADIUS_SCALE
    gp_half = max(mm_to_m(0.005) * 0.5, min(gp_half, mm_to_m(3.0) * 0.5))
    for curve in bpy.data.curves:
        if not curve.name.startswith(PAPER_GUIDE_CURVE_PREFIX):
            continue
        try:
            if abs(float(curve.bevel_depth) - curve_half) > curve_half * 0.02:
                curve.bevel_depth = curve_half
        except Exception:  # noqa: BLE001
            continue
    for obj in bpy.data.objects:
        if obj.get(PROP_GUIDE_KIND) != GUIDE_KIND_LINES or getattr(obj, "type", "") != "GREASEPENCIL":
            continue
        _set_gp_stroke_radius(obj, gp_half)


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
    rects = overlay_shared.compute_paper_rects(paper, is_left_half=_is_left_page(paper, page_index))
    return _guide_sets_have_geometry(_paper_guide_sets(paper, rects))


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
    should_have_geometry = _line_guide_should_have_geometry(work, page)
    if curve_objects:
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
    return False


def repair_loaded_work_paper_guides(scene=None, work=None) -> bool:
    """既存ファイルに残った古い用紙ガイド線を、現在仕様へ軽量修復する."""
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    work = work or getattr(scene, "bname_work", None)
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
    global _last_mpp, _last_repair_time
    try:
        if not _live_guide_updates_allowed():
            _last_mpp = -1.0
            return _GUIDE_IDLE_INTERVAL
        now = time.monotonic()
        if now - _last_repair_time >= _GUIDE_REPAIR_INTERVAL:
            _last_repair_time = now
            repair_loaded_work_paper_guides()
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
