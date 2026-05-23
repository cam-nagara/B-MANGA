"""フキダシ表示オブジェクトの同期ヘルパ."""

from __future__ import annotations

import json
import math
from contextlib import contextmanager
from typing import Optional, Sequence

import bpy
from mathutils import Vector

from . import balloon_curve_render_nodes
from . import balloon_curve_source_state
from . import balloon_shapes
from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import percentage
from .geom import Rect, mm_to_m

_logger = log.get_logger(__name__)

BALLOON_CURVE_NAME_PREFIX = "balloon_"
BALLOON_FILL_NAME_PREFIX = "balloon_fill_"
BALLOON_SOURCE_NAME_PREFIX = "balloon_source_"
BALLOON_CLIP_MASK_NAME_PREFIX = "balloon_clip_mask_"
BALLOON_CURVE_MATERIAL_PREFIX = "BName_Balloon_Curve_"
BALLOON_FILL_MATERIAL_PREFIX = "BName_Balloon_Fill_"
PROP_BALLOON_FILL_KIND = "bname_balloon_fill_kind"
PROP_BALLOON_FILL_OWNER_ID = "bname_balloon_fill_owner_id"
PROP_BALLOON_FILL_SOURCE_MATERIAL = "bname_balloon_fill_source_material"
PROP_BALLOON_SOURCE_KIND = "bname_balloon_source_kind"
PROP_BALLOON_SOURCE_OWNER_ID = "bname_balloon_source_owner_id"
PROP_BALLOON_CLIP_MASK_KIND = "bname_balloon_clip_mask_kind"
PROP_BALLOON_CLIP_MASK_OWNER_ID = "bname_balloon_clip_mask_owner_id"
PROP_BALLOON_GEOMETRY_KEY = "bname_balloon_geometry_key"
CURVE_GEOMETRY_VERSION = 2
_AUTO_SYNC_SUSPEND_COUNT = 0
_AUTO_SYNC_DEFER_COUNT = 0


@contextmanager
def suspend_auto_sync():
    """Temporarily skip expensive mesh rebuilds from property update callbacks."""
    global _AUTO_SYNC_SUSPEND_COUNT
    _AUTO_SYNC_SUSPEND_COUNT += 1
    try:
        yield
    finally:
        _AUTO_SYNC_SUSPEND_COUNT = max(0, _AUTO_SYNC_SUSPEND_COUNT - 1)


def _auto_sync_suspended() -> bool:
    return _AUTO_SYNC_SUSPEND_COUNT > 0


@contextmanager
def defer_auto_sync():
    """Temporarily batch balloon entry updates before one explicit sync."""
    global _AUTO_SYNC_DEFER_COUNT
    _AUTO_SYNC_DEFER_COUNT += 1
    try:
        yield
    finally:
        _AUTO_SYNC_DEFER_COUNT = max(0, _AUTO_SYNC_DEFER_COUNT - 1)


def _auto_sync_deferred() -> bool:
    return _AUTO_SYNC_DEFER_COUNT > 0


def _remove_unused_data_block(data) -> None:
    if data is None or getattr(data, "users", 0) != 0:
        return
    try:
        if isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
        elif isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    except Exception:  # noqa: BLE001
        pass


def _replace_object_with_curve(
    *,
    obj: Optional[bpy.types.Object],
    obj_name: str,
    curve: bpy.types.Curve,
) -> bpy.types.Object:
    if obj is not None and obj.type != "CURVE":
        _remove_balloon_object(obj)
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, curve)
    else:
        if getattr(obj, "data", None) is None:
            obj.data = curve
    return obj


def _entry_line_rgba(entry) -> tuple[float, float, float, float]:
    color = getattr(entry, "line_color", (0.0, 0.0, 0.0, 1.0))
    opacity = percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)
    try:
        return (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]) * opacity,
        )
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 0.0, opacity)


def _entry_fill_rgba(entry) -> tuple[float, float, float, float]:
    color = getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0))
    opacity = percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)
    fill_opacity = percentage.percent_to_factor(getattr(entry, "fill_opacity", 100.0), 100.0)
    try:
        return (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]) * opacity * fill_opacity,
        )
    except Exception:  # noqa: BLE001
        return (1.0, 1.0, 1.0, opacity)


def _ensure_balloon_curve_material(
    curve: Optional[bpy.types.Curve],
    *,
    material_name: str,
    entry=None,
) -> bpy.types.Material:
    """フキダシ輪郭用の material を ensure."""
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(name=material_name)
    line = _entry_line_rgba(entry)
    try:
        mat.diffuse_color = line
    except Exception:  # noqa: BLE001
        pass
    try:
        _setup_emission_alpha_material(mat, line)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve material setup failed")
    if curve is not None:
        if not curve.materials:
            curve.materials.append(mat)
        elif curve.materials[0] is not mat:
            curve.materials[0] = mat
    return mat


def _fill_material_for_entry(material_name: str, entry=None) -> tuple[bpy.types.Material, bool]:
    chosen_name = str(getattr(entry, "fill_material_name", "") or "").strip() if entry is not None else ""
    source = bpy.data.materials.get(chosen_name) if chosen_name else None
    if source is not None and not bool(source.get(PROP_BALLOON_FILL_KIND, False)):
        copy_name = f"{material_name}__{chosen_name}"
        mat = bpy.data.materials.get(copy_name)
        if mat is None:
            mat = source.copy()
            mat.name = copy_name
        mat[PROP_BALLOON_FILL_KIND] = "copy"
        mat[PROP_BALLOON_FILL_OWNER_ID] = str(getattr(entry, "id", "") or "")
        mat[PROP_BALLOON_FILL_SOURCE_MATERIAL] = chosen_name
        return mat, True

    mat = bpy.data.materials.get(chosen_name or material_name)
    if mat is None:
        mat = bpy.data.materials.new(name=chosen_name or material_name)
    mat[PROP_BALLOON_FILL_KIND] = "generated"
    mat[PROP_BALLOON_FILL_OWNER_ID] = str(getattr(entry, "id", "") or "")
    return mat, False


def _apply_fill_material_basics(mat: bpy.types.Material, fill: tuple[float, float, float, float], entry=None) -> None:
    try:
        mat.diffuse_color = fill
        mat.blend_method = "OPAQUE" if float(fill[3]) >= 0.999 else "BLEND"
        if bool(getattr(entry, "fill_blur_dither", False)):
            mat.surface_render_method = "DITHERED"
        mat.show_transparent_back = True
    except Exception:  # noqa: BLE001
        pass
    if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return
    try:
        for node in mat.node_tree.nodes:
            if node.bl_idname == "ShaderNodeBsdfPrincipled":
                if "Alpha" in node.inputs:
                    node.inputs["Alpha"].default_value = fill[3]
    except Exception:  # noqa: BLE001
        pass


def _clear_material_nodes(mat: bpy.types.Material):
    mat.use_nodes = True
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    return nt


def _setup_emission_alpha_material(
    mat: bpy.types.Material,
    color: tuple[float, float, float, float],
    *,
    gradient: tuple[tuple[float, float, float, float], tuple[float, float, float, float], float] | None = None,
) -> None:
    nt = _clear_material_nodes(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (360, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-180, -120)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-180, 80)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (120, 0)
    emission.inputs["Strength"].default_value = 1.0
    if gradient is None:
        emission.inputs["Color"].default_value = color
    else:
        start, end, angle_deg = gradient
        coord = nt.nodes.new("ShaderNodeTexCoord")
        coord.location = (-980, 80)
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.location = (-780, 80)
        gradient_node = nt.nodes.new("ShaderNodeTexGradient")
        gradient_node.location = (-560, 80)
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.location = (-360, 80)
        ramp.color_ramp.elements[0].position = 0.0
        ramp.color_ramp.elements[0].color = start
        ramp.color_ramp.elements[1].position = 1.0
        ramp.color_ramp.elements[1].color = end
        try:
            mapping.inputs["Rotation"].default_value[2] = math.radians(float(angle_deg))
        except Exception:  # noqa: BLE001
            pass
        nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], gradient_node.inputs["Vector"])
        nt.links.new(gradient_node.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], emission.inputs["Color"])
    mix.inputs["Fac"].default_value = max(0.0, min(1.0, float(color[3])))
    nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    try:
        mat.blend_method = "OPAQUE" if float(color[3]) >= 0.999 else "BLEND"
        mat.show_transparent_back = True
    except (AttributeError, TypeError):
        pass


def _ensure_fill_material(material_name: str, entry=None) -> bpy.types.Material:
    mat, copied_user_material = _fill_material_for_entry(material_name, entry)
    fill = _entry_fill_rgba(entry)
    _apply_fill_material_basics(mat, fill, entry)
    if copied_user_material and not bool(getattr(entry, "fill_gradient_enabled", False)):
        return mat
    try:
        if bool(getattr(entry, "fill_gradient_enabled", False)):
            start = tuple(float(v) for v in getattr(entry, "fill_gradient_start_color", fill))
            end = tuple(float(v) for v in getattr(entry, "fill_gradient_end_color", fill))
            _setup_emission_alpha_material(
                mat,
                fill,
                gradient=(start, end, float(getattr(entry, "fill_gradient_angle_deg", 90.0) or 90.0)),
            )
        else:
            _setup_emission_alpha_material(mat, fill)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon fill material setup failed")
    return mat


def _remove_balloon_object(obj: bpy.types.Object) -> None:
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve object removal failed")
        return
    _remove_unused_data_block(data)


def _remove_duplicate_balloon_objects(
    balloon_id: str,
    keep_obj: Optional[bpy.types.Object],
) -> None:
    if not balloon_id:
        return
    for obj in list(bpy.data.objects):
        if obj is keep_obj:
            continue
        if obj.get(on.PROP_KIND) != "balloon":
            continue
        if str(obj.get(on.PROP_ID, "") or "") != balloon_id:
            continue
        _remove_balloon_object(obj)


def _remove_legacy_balloon_fill_objects(balloon_id: str) -> None:
    if not balloon_id:
        return
    legacy_name = f"{BALLOON_FILL_NAME_PREFIX}{balloon_id}"
    for obj in list(bpy.data.objects):
        if obj.name != legacy_name and not (
            obj.get(PROP_BALLOON_FILL_KIND) == "balloon_fill"
            and str(obj.get(PROP_BALLOON_FILL_OWNER_ID, "") or "") == balloon_id
        ):
            continue
        _remove_balloon_object(obj)


def _remove_balloon_source_object(balloon_id: str) -> None:
    if not balloon_id:
        return
    source_name = f"{BALLOON_SOURCE_NAME_PREFIX}{balloon_id}"
    for obj in list(bpy.data.objects):
        if obj.name != source_name and not (
            obj.get(PROP_BALLOON_SOURCE_KIND) == "geometry_source"
            and str(obj.get(PROP_BALLOON_SOURCE_OWNER_ID, "") or "") == balloon_id
        ):
            continue
        _remove_balloon_object(obj)


def _tag_curve_object_updated(obj: bpy.types.Object | None) -> None:
    if obj is None:
        return
    try:
        obj.data.update_tag()
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.update_tag()
    except Exception:  # noqa: BLE001
        pass


def _ensure_curve_object_for_entry(
    balloon_id: str,
    line_mat: bpy.types.Material,
    fill_mat: Optional[bpy.types.Material],
) -> bpy.types.Object:
    obj_name = f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}"
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    curve_data = _ensure_balloon_curve_data(balloon_id, line_mat, fill_mat)
    obj = _replace_object_with_curve(obj=obj, obj_name=obj_name, curve=curve_data)
    _prepare_balloon_curve_data(obj.data, line_mat, fill_mat)
    if obj.data is not curve_data:
        _remove_unused_data_block(curve_data)
    _remove_duplicate_balloon_objects(balloon_id, obj)
    _remove_legacy_balloon_fill_objects(balloon_id)
    _remove_balloon_source_object(balloon_id)
    return obj


def _sync_generated_shape_if_needed(
    obj: bpy.types.Object,
    entry,
    *,
    force_regenerate: bool,
    preserve_manual_delta: bool,
) -> None:
    geometry_key = _geometry_key_for_entry(entry)
    previous_key = str(obj.get(PROP_BALLOON_GEOMETRY_KEY, "") or "")
    source_state = balloon_curve_source_state.detect_state(obj)
    should_rebuild = force_regenerate or previous_key != geometry_key or not obj.data.splines
    if not should_rebuild:
        return
    can_rebuild = (
        force_regenerate
        or source_state == balloon_curve_source_state.STATE_GENERATED
        or (source_state == balloon_curve_source_state.STATE_MANUAL and preserve_manual_delta)
    )
    if not can_rebuild:
        return
    delta = None
    if preserve_manual_delta and source_state == balloon_curve_source_state.STATE_MANUAL:
        delta = balloon_curve_source_state.manual_delta(obj)
    _sync_curve_geometry(obj, entry)
    if delta is not None:
        balloon_curve_source_state.apply_delta(obj, delta)
    obj[PROP_BALLOON_GEOMETRY_KEY] = geometry_key
    balloon_curve_source_state.mark_generated(obj)
    _tag_curve_object_updated(obj)


def _apply_entry_transform(entry, obj: bpy.types.Object) -> None:
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))
    obj.rotation_euler[2] = math.radians(float(getattr(entry, "rotation_deg", 0.0) or 0.0))
    obj.scale.x = -1.0 if bool(getattr(entry, "flip_h", False)) else 1.0
    obj.scale.y = -1.0 if bool(getattr(entry, "flip_v", False)) else 1.0
    obj.scale.z = 1.0


def _stamp_values_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    default_parent_kind = "outside" if page is None else "page"
    entry_parent_kind = str(getattr(entry, "parent_kind", "") or default_parent_kind)
    entry_parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder_id = folder_id or str(getattr(entry, "folder_key", "") or "")
    if entry_parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if entry_parent_kind == "coma" and entry_parent_key:
        return "coma", entry_parent_key, entry_folder_id
    if entry_parent_kind == "folder" and entry_folder_id:
        return "folder", entry_folder_id, entry_folder_id
    return "page", entry_parent_key or str(getattr(page, "id", "") or ""), entry_folder_id


def _balloon_z_index(scene: bpy.types.Scene, page, balloon_id: str) -> int:
    z_base = 1000
    work = getattr(scene, "bname_work", None)
    balloons = getattr(page, "balloons", None) if page is not None else getattr(work, "shared_balloons", None)
    if balloons is None:
        return z_base
    for i, entry in enumerate(balloons):
        if str(getattr(entry, "id", "") or "") == balloon_id:
            return z_base + (i + 1) * 10
    return z_base


def _apply_page_world_offset(scene: bpy.types.Scene, work, page, entry, obj: bpy.types.Object) -> None:
    try:
        from . import page_grid as _pg
        from .geom import mm_to_m as _mm_to_m

        page_idx = -1
        if work is not None and page is not None:
            target_id = str(getattr(page, "id", "") or "")
            for i, page_entry in enumerate(getattr(work, "pages", [])):
                if str(getattr(page_entry, "id", "") or "") == target_id:
                    page_idx = i
                    break
        if page_idx < 0:
            return
        ox_mm, oy_mm = _pg.page_total_offset_mm(work, scene, page_idx)
        obj.location.x = _mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0) + ox_mm)
        obj.location.y = _mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0) + oy_mm)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: page world offset 加算失敗")


def _sync_visibility_and_modifier(scene: bpy.types.Scene, work, page, entry, obj: bpy.types.Object) -> None:
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    try:
        if work is not None:
            los.assign_per_page_z_ranks(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: z order sync failed")
    try:
        mask_obj = _coma_mask_object_for_entry(scene, work, page, entry, obj)
        line_width_mm = float(getattr(entry, "line_width_mm", 0.3) or 0.3)
        balloon_curve_render_nodes.ensure_modifier(
            obj,
            line_width_mm=line_width_mm,
            mask_object=mask_obj,
            clip_needed=_balloon_curve_needs_coma_clip(
                scene,
                work,
                page,
                entry,
                obj,
                mask_obj,
                margin_mm=line_width_mm * 0.5,
            ),
            fill_clip_needed=_balloon_curve_needs_coma_clip(
                scene,
                work,
                page,
                entry,
                obj,
                mask_obj,
                margin_mm=0.0,
            ),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: lightweight render node sync failed")


def ensure_balloon_curve_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
    force_regenerate: bool = False,
    preserve_manual_delta: bool = False,
) -> Optional[bpy.types.Object]:
    """``BNameBalloonEntry`` から balloon Curve Object を生成・更新する.

    rect/ellipse/cloud/fluffy/thorn 等の Meldex 共通形状と尻尾を Curve として
    描画する。
    """
    if scene is None or entry is None:
        return None
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None

    line_mat = _ensure_balloon_curve_material(
        None,
        material_name=f"{BALLOON_CURVE_MATERIAL_PREFIX}{balloon_id}",
        entry=entry,
    )
    fill_mat = _ensure_fill_material(f"{BALLOON_FILL_MATERIAL_PREFIX}{balloon_id}", entry)

    work = getattr(scene, "bname_work", None)
    obj = _ensure_curve_object_for_entry(balloon_id, line_mat, fill_mat)
    _sync_generated_shape_if_needed(
        obj,
        entry,
        force_regenerate=force_regenerate,
        preserve_manual_delta=preserve_manual_delta,
    )
    _apply_entry_transform(entry, obj)
    stamp_kind, stamp_key, stamp_folder = _stamp_values_for_entry(entry, page, folder_id)

    los.stamp_layer_object(
        obj,
        kind="balloon",
        bname_id=balloon_id,
        title=str(getattr(entry, "title", "") or balloon_id),
        z_index=_balloon_z_index(scene, page, balloon_id),
        parent_kind=stamp_kind,
        parent_key=stamp_key,
        folder_id=stamp_folder,
        scene=scene,
        # entry.x_mm/y_mm をページローカル座標として独自管理し、その値に
        # page_grid のオフセットを加算して world 座標とする。
        apply_page_offset=False,
    )
    _apply_page_world_offset(scene, work, page, entry, obj)
    _sync_visibility_and_modifier(scene, work, page, entry, obj)
    return obj


def _split_coma_parent_key(page, parent_key: str) -> tuple[str, str]:
    key = str(parent_key or "")
    if ":" in key:
        page_id, coma_id = key.split(":", 1)
        return page_id, coma_id
    return str(getattr(page, "id", "") or ""), key


def _find_page_by_id(work, page_id: str):
    if work is None or not page_id:
        return None
    for page in getattr(work, "pages", []) or []:
        if str(getattr(page, "id", "") or "") == page_id:
            return page
    return None


def _find_coma_by_id(page, coma_id: str):
    if page is None or not coma_id:
        return None
    for coma in getattr(page, "comas", []) or []:
        if str(getattr(coma, "id", "") or "") == coma_id:
            return coma
    return None


def _point_in_polygon_xy(point: tuple[float, float], polygon: Sequence[tuple[float, float]], *, tolerance: float = 1.0e-9) -> bool:
    x, y = float(point[0]), float(point[1])
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    prev_x, prev_y = polygon[-1]
    for cur_x, cur_y in polygon:
        dx = cur_x - prev_x
        dy = cur_y - prev_y
        cross = (x - prev_x) * dy - (y - prev_y) * dx
        if abs(cross) <= tolerance:
            dot = (x - prev_x) * (x - cur_x) + (y - prev_y) * (y - cur_y)
            if dot <= tolerance:
                return True
        if (prev_y > y) != (cur_y > y):
            ix = prev_x + (y - prev_y) * (cur_x - prev_x) / max(1.0e-12, (cur_y - prev_y))
            if ix >= x - tolerance:
                inside = not inside
        prev_x, prev_y = cur_x, cur_y
    return inside


def _sample_cubic_vec(p0: Vector, h0: Vector, h1: Vector, p1: Vector, t: float) -> Vector:
    mt = 1.0 - t
    return (mt**3) * p0 + (3.0 * mt * mt * t) * h0 + (3.0 * mt * t * t) * h1 + (t**3) * p1


def _iter_curve_local_samples(obj: bpy.types.Object, *, steps: int = 8):
    curve = getattr(obj, "data", None)
    if curve is None:
        return
    for spline in getattr(curve, "splines", []) or []:
        if spline.type == "BEZIER":
            points = list(getattr(spline, "bezier_points", []) or [])
            count = len(points)
            if count == 0:
                continue
            segment_count = count if getattr(spline, "use_cyclic_u", False) else max(0, count - 1)
            for index in range(segment_count):
                p0 = points[index]
                p1 = points[(index + 1) % count]
                for step in range(steps + 1):
                    pos = _sample_cubic_vec(
                        Vector(p0.co),
                        Vector(p0.handle_right),
                        Vector(p1.handle_left),
                        Vector(p1.co),
                        step / max(1, steps),
                    )
                    yield pos
        else:
            for point in getattr(spline, "points", []) or []:
                co = getattr(point, "co", None)
                if co is None:
                    continue
                yield Vector((float(co.x), float(co.y), float(co.z)))


def _balloon_curve_needs_coma_clip(
    scene,
    work,
    page,
    entry,
    obj: bpy.types.Object,
    mask_obj: bpy.types.Object | None,
    *,
    margin_mm: float | None = None,
) -> bool:
    if mask_obj is None:
        return False
    parent_key = str(getattr(entry, "parent_key", "") or "")
    page_id, coma_id = _split_coma_parent_key(page, parent_key)
    target_page = page if str(getattr(page, "id", "") or "") == page_id else _find_page_by_id(work, page_id)
    target_coma = _find_coma_by_id(target_page, coma_id)
    if scene is None or work is None or target_page is None or target_coma is None:
        return True
    try:
        from . import layer_hierarchy

        polygon = [(float(x_mm), float(y_mm)) for x_mm, y_mm in layer_hierarchy.coma_polygon(target_coma)]
        if len(polygon) < 3:
            return True
        line_margin = (
            max(0.0, float(getattr(entry, "line_width_mm", 0.0) or 0.0)) * 0.5
            if margin_mm is None
            else max(0.0, float(margin_mm))
        )
        x0 = float(getattr(entry, "x_mm", 0.0) or 0.0) + float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0) - line_margin
        y0 = float(getattr(entry, "y_mm", 0.0) or 0.0) + float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0) - line_margin
        x1 = (
            float(getattr(entry, "x_mm", 0.0) or 0.0)
            + float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
            + max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0))
            + line_margin
        )
        y1 = (
            float(getattr(entry, "y_mm", 0.0) or 0.0)
            + float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
            + max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0))
            + line_margin
        )
        corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        for xy in corners:
            if not _point_in_polygon_xy(xy, polygon, tolerance=0.1):
                return True
        return False
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: clip need check failed")
        return True


def _coma_mask_object_for_entry(scene, work, page, entry, obj: bpy.types.Object | None = None):
    if scene is None or work is None or entry is None:
        return None
    parent_key = str(getattr(entry, "parent_key", "") or "")
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    if parent_kind != "coma" and ":" not in parent_key:
        return None
    page_id, coma_id = _split_coma_parent_key(page, parent_key)
    if not page_id or not coma_id:
        return None
    target_page = page if str(getattr(page, "id", "") or "") == page_id else _find_page_by_id(work, page_id)
    target_coma = _find_coma_by_id(target_page, coma_id)
    if obj is not None and target_page is not None and target_coma is not None:
        return _ensure_balloon_clip_mask(scene, work, target_page, target_coma, entry, obj)
    try:
        from . import coma_plane

        if target_page is not None and target_coma is not None:
            return coma_plane.ensure_coma_mask(scene, work, target_page, target_coma)
        return coma_plane.find_coma_mask_object(page_id, coma_id)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: coma mask resolve failed")
    return None


def _hide_balloon_clip_mask_object(mask_obj: bpy.types.Object) -> None:
    mask_obj.hide_viewport = True
    mask_obj.hide_render = True
    mask_obj.hide_select = True
    try:
        mask_obj.hide_set(True)
    except Exception:  # noqa: BLE001
        pass
    try:
        mask_obj.display_type = "WIRE"
        mask_obj.show_name = False
    except Exception:  # noqa: BLE001
        pass


def _remove_balloon_clip_mask(balloon_id: str) -> None:
    if not balloon_id:
        return
    mask_name = f"{BALLOON_CLIP_MASK_NAME_PREFIX}{balloon_id}"
    for obj in list(bpy.data.objects):
        if obj.name != mask_name and not (
            obj.get(PROP_BALLOON_CLIP_MASK_KIND) == "coma_clip"
            and str(obj.get(PROP_BALLOON_CLIP_MASK_OWNER_ID, "") or "") == balloon_id
        ):
            continue
        data = getattr(obj, "data", None)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        _remove_unused_data_block(data)


def _ensure_balloon_clip_mask(scene, work, page, coma, entry, owner_obj: bpy.types.Object):
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    try:
        from . import layer_hierarchy
        from . import page_grid

        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            return None
        page_idx = -1
        for i, page_entry in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(page_entry, "id", "") or "") == page_id:
                page_idx = i
                break
        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, page_idx if page_idx >= 0 else 0)
        inv = owner_obj.matrix_world.inverted_safe()
        verts = []
        for x_mm, y_mm in layer_hierarchy.coma_polygon(coma):
            world = Vector((mm_to_m(float(x_mm) + ox_mm), mm_to_m(float(y_mm) + oy_mm), 0.0))
            verts.append(tuple(inv @ world))
        if len(verts) < 3:
            _remove_balloon_clip_mask(balloon_id)
            return None
        mesh_name = f"{BALLOON_CLIP_MASK_NAME_PREFIX}{balloon_id}_mesh"
        obj_name = f"{BALLOON_CLIP_MASK_NAME_PREFIX}{balloon_id}"
        mesh = bpy.data.meshes.get(mesh_name)
        if mesh is None:
            mesh = bpy.data.meshes.new(mesh_name)
        mesh.clear_geometry()
        mesh.from_pydata(verts, [], [tuple(range(len(verts)))])
        mesh.update()
        mask_obj = bpy.data.objects.get(obj_name)
        if mask_obj is None:
            mask_obj = bpy.data.objects.new(obj_name, mesh)
        elif mask_obj.data is not mesh:
            mask_obj.data = mesh
        mask_obj.matrix_world = owner_obj.matrix_world.copy()
        mask_obj[PROP_BALLOON_CLIP_MASK_KIND] = "coma_clip"
        mask_obj[PROP_BALLOON_CLIP_MASK_OWNER_ID] = balloon_id
        mask_obj[on.PROP_MANAGED] = False
        _hide_balloon_clip_mask_object(mask_obj)
        target_collection = owner_obj.users_collection[0] if owner_obj.users_collection else bpy.context.collection
        if target_collection is not None and not any(o is mask_obj for o in target_collection.objects):
            target_collection.objects.link(mask_obj)
        for collection in tuple(mask_obj.users_collection):
            if collection is target_collection:
                continue
            try:
                collection.objects.unlink(mask_obj)
            except Exception:  # noqa: BLE001
                pass
        return mask_obj
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: clip mask create failed")
        return None


def _set_data_materials(data, materials: Sequence[bpy.types.Material | None]) -> None:
    try:
        data.materials.clear()
    except Exception:  # noqa: BLE001
        while len(data.materials) > 0:
            data.materials.pop(index=len(data.materials) - 1)
    for mat in materials:
        if mat is not None:
            data.materials.append(mat)


def _ensure_balloon_curve_data(
    balloon_id: str,
    line_material: bpy.types.Material,
    fill_material: Optional[bpy.types.Material],
) -> bpy.types.Curve:
    curve_name = f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}_curve"
    curve = bpy.data.curves.get(curve_name)
    if curve is None:
        curve = bpy.data.curves.new(curve_name, "CURVE")
    _prepare_balloon_curve_data(curve, line_material, fill_material)
    return curve


def _prepare_balloon_curve_data(
    curve: bpy.types.Curve,
    line_material: bpy.types.Material,
    fill_material: Optional[bpy.types.Material],
) -> None:
    curve.dimensions = "2D"
    curve.resolution_u = 16
    curve.bevel_depth = 0.0
    curve.bevel_resolution = 0
    try:
        curve.fill_mode = "NONE"
        curve.use_fill_caps = False
    except Exception:  # noqa: BLE001
        pass
    _set_data_materials(curve, (line_material, fill_material))


def _clear_curve_splines(curve: bpy.types.Curve) -> None:
    while len(curve.splines) > 0:
        curve.splines.remove(curve.splines[0])


def _entry_center_offset(entry) -> tuple[float, float]:
    return (
        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
    )


def _point_to_curve_xyz(point: tuple[float, float], offset: tuple[float, float]) -> tuple[float, float, float]:
    return (
        mm_to_m(float(point[0]) + offset[0]),
        mm_to_m(float(point[1]) + offset[1]),
        0.0,
    )


def _add_bezier_loop(
    curve: bpy.types.Curve,
    points: Sequence[tuple[float, float]],
    *,
    sharp_indices: set[int],
    offset: tuple[float, float],
) -> None:
    if len(points) < 3:
        return
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    spline.use_cyclic_u = True
    spline.material_index = 0
    for index, point in enumerate(points):
        bp = spline.bezier_points[index]
        bp.co = _point_to_curve_xyz(point, offset)
        is_sharp = index in sharp_indices
        handle_type = "VECTOR" if is_sharp else "AUTO"
        bp.handle_left_type = handle_type
        bp.handle_right_type = handle_type
        bp.radius = 1.0


def _add_bezier_anchor_loop(
    curve: bpy.types.Curve,
    anchors: Sequence[balloon_shapes.BezierAnchor],
    *,
    offset: tuple[float, float],
) -> None:
    if len(anchors) < 3:
        return
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(anchors) - 1)
    spline.use_cyclic_u = True
    spline.material_index = 0
    for index, anchor in enumerate(anchors):
        bp = spline.bezier_points[index]
        bp.co = _point_to_curve_xyz(anchor.co, offset)
        left = anchor.handle_left if anchor.handle_left is not None else anchor.co
        right = anchor.handle_right if anchor.handle_right is not None else anchor.co
        bp.handle_left = _point_to_curve_xyz(left, offset)
        bp.handle_right = _point_to_curve_xyz(right, offset)
        bp.handle_left_type = str(anchor.handle_left_type or "FREE")
        bp.handle_right_type = str(anchor.handle_right_type or "FREE")
        bp.radius = 1.0


def _body_outline_for_entry(entry) -> tuple[list[tuple[float, float]], list[int]]:
    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    return balloon_shapes.outline_with_corners_for_entry(entry, rect)


def _body_bezier_for_entry(entry) -> list[balloon_shapes.BezierAnchor] | None:
    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    return balloon_shapes.bezier_loop_for_entry(entry, rect)


def _geometry_key_for_entry(entry) -> str:
    sp = getattr(entry, "shape_params", None)
    shape_params = {
        "cloud_bump_width_mm": float(getattr(sp, "cloud_bump_width_mm", 10.0) or 10.0),
        "cloud_bump_width_jitter": float(getattr(sp, "cloud_bump_width_jitter", 0.0) or 0.0),
        "cloud_bump_height_mm": float(getattr(sp, "cloud_bump_height_mm", 4.0) or 4.0),
        "cloud_bump_height_jitter": float(getattr(sp, "cloud_bump_height_jitter", 0.0) or 0.0),
        "cloud_offset_percent": float(getattr(sp, "cloud_offset_percent", 50.0) or 50.0),
        "cloud_sub_width_ratio": float(getattr(sp, "cloud_sub_width_ratio", 0.0) or 0.0),
        "cloud_sub_width_jitter": float(getattr(sp, "cloud_sub_width_jitter", 0.0) or 0.0),
        "cloud_sub_height_ratio": float(getattr(sp, "cloud_sub_height_ratio", 0.0) or 0.0),
        "cloud_sub_height_jitter": float(getattr(sp, "cloud_sub_height_jitter", 0.0) or 0.0),
    }
    tails = []
    for tail in getattr(entry, "tails", []) or []:
        points = []
        for point in getattr(tail, "points", []) or []:
            points.append(
                {
                    "x": float(getattr(point, "x_mm", 0.0) or 0.0),
                    "y": float(getattr(point, "y_mm", 0.0) or 0.0),
                    "corner": str(getattr(point, "corner_type", "line") or "line"),
                }
            )
        tails.append(
            {
                "type": str(getattr(tail, "type", "straight") or "straight"),
                "direction": float(getattr(tail, "direction_deg", 270.0) or 270.0),
                "length": float(getattr(tail, "length_mm", 0.0) or 0.0),
                "root_width": float(getattr(tail, "root_width_mm", 0.0) or 0.0),
                "tip_width": float(getattr(tail, "tip_width_mm", 0.0) or 0.0),
                "bend": float(getattr(tail, "curve_bend", 0.0) or 0.0),
                "custom": bool(getattr(tail, "custom_points_enabled", False)),
                "start_x": float(getattr(tail, "start_x_mm", 0.0) or 0.0),
                "start_y": float(getattr(tail, "start_y_mm", 0.0) or 0.0),
                "end_x": float(getattr(tail, "end_x_mm", 0.0) or 0.0),
                "end_y": float(getattr(tail, "end_y_mm", 0.0) or 0.0),
                "points": points,
            }
        )
    payload = {
        "curve_geometry_version": CURVE_GEOMETRY_VERSION,
        "shape": balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect")),
        "custom": str(getattr(entry, "custom_preset_name", "") or ""),
        "width": float(getattr(entry, "width_mm", 0.0) or 0.0),
        "height": float(getattr(entry, "height_mm", 0.0) or 0.0),
        "center": _entry_center_offset(entry),
        "rounded": bool(getattr(entry, "rounded_corner_enabled", False)),
        "rounded_radius": float(getattr(entry, "rounded_corner_radius_mm", 0.0) or 0.0),
        "shape_params": shape_params,
        "tails": tails,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sync_curve_geometry(obj: bpy.types.Object, entry) -> None:
    curve = obj.data
    _clear_curve_splines(curve)
    if balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect")) == "none":
        return
    offset = _entry_center_offset(entry)
    body_anchors = _body_bezier_for_entry(entry)
    if body_anchors is not None:
        _add_bezier_anchor_loop(curve, body_anchors, offset=offset)
    else:
        body_points, sharp = _body_outline_for_entry(entry)
        _add_bezier_loop(curve, body_points, sharp_indices=set(sharp), offset=offset)
    for tail in getattr(entry, "tails", []) or []:
        tail_points = _tail_polygon_for_entry(entry, tail)
        _add_bezier_loop(
            curve,
            tail_points,
            sharp_indices=set(range(len(tail_points))),
            offset=offset,
        )


def _tail_polygon_for_entry(entry, tail) -> list[tuple[float, float]]:
    """フキダシ内ローカル座標で、しっぽの輪郭点列を返す。"""
    from . import balloon_tail_geom
    from .geom import Rect

    rect = Rect(
        0.0,
        0.0,
        float(getattr(entry, "width_mm", 0.0) or 0.0),
        float(getattr(entry, "height_mm", 0.0) or 0.0),
    )
    return balloon_tail_geom.polygon_for_tail(rect, tail)


def _apply_balloon_object_transform(scene, work, page, entry, obj) -> None:
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))
    obj.rotation_euler[2] = math.radians(float(getattr(entry, "rotation_deg", 0.0) or 0.0))
    obj.scale.x = -1.0 if bool(getattr(entry, "flip_h", False)) else 1.0
    obj.scale.y = -1.0 if bool(getattr(entry, "flip_v", False)) else 1.0
    obj.scale.z = 1.0
    try:
        from . import page_grid as _pg
        from .geom import mm_to_m as _mm_to_m

        page_idx = -1
        if work is not None and page is not None:
            target_id = str(getattr(page, "id", "") or "")
            for i, p in enumerate(getattr(work, "pages", [])):
                if str(getattr(p, "id", "") or "") == target_id:
                    page_idx = i
                    break
        if page_idx >= 0:
            ox_mm, oy_mm = _pg.page_total_offset_mm(work, scene, page_idx)
            obj.location.x = _mm_to_m(
                float(getattr(entry, "x_mm", 0.0) or 0.0) + ox_mm
            )
            obj.location.y = _mm_to_m(
                float(getattr(entry, "y_mm", 0.0) or 0.0) + oy_mm
            )
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: page world offset 加算失敗")


def _sync_existing_balloon_object_lightweight(scene, work, page, entry) -> bool:
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return False
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is None:
        obj = bpy.data.objects.get(f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}")
    if obj is None:
        return ensure_balloon_curve_object(scene=scene, entry=entry, page=page) is not None
    if getattr(obj, "type", "") != "CURVE":
        return ensure_balloon_curve_object(scene=scene, entry=entry, page=page) is not None
    geometry_key = _geometry_key_for_entry(entry)
    if str(obj.get(PROP_BALLOON_GEOMETRY_KEY, "") or "") != geometry_key:
        state = balloon_curve_source_state.detect_state(obj)
        if state == balloon_curve_source_state.STATE_GENERATED:
            _sync_curve_geometry(obj, entry)
            obj[PROP_BALLOON_GEOMETRY_KEY] = geometry_key
            balloon_curve_source_state.mark_generated(obj)
            _tag_curve_object_updated(obj)
    _apply_balloon_object_transform(scene, work, page, entry, obj)
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    try:
        _remove_balloon_source_object(balloon_id)
        mask_obj = _coma_mask_object_for_entry(scene, work, page, entry, obj)
        line_width_mm = float(getattr(entry, "line_width_mm", 0.3) or 0.3)
        balloon_curve_render_nodes.ensure_modifier(
            obj,
            line_width_mm=line_width_mm,
            mask_object=mask_obj,
            clip_needed=_balloon_curve_needs_coma_clip(
                scene,
                work,
                page,
                entry,
                obj,
                mask_obj,
                margin_mm=line_width_mm * 0.5,
            ),
            fill_clip_needed=_balloon_curve_needs_coma_clip(
                scene,
                work,
                page,
                entry,
                obj,
                mask_obj,
                margin_mm=0.0,
            ),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: lightweight render node sync failed")
    return True


def find_balloon_entry(scene, balloon_id: str):
    """全 page の balloons から id で逆引き."""
    work = getattr(scene, "bname_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "balloons", []):
            if str(getattr(entry, "id", "") or "") == balloon_id:
                return page, entry
    for entry in getattr(work, "shared_balloons", []):
        if str(getattr(entry, "id", "") or "") == balloon_id:
            return None, entry
    return None, None


def find_balloon_object(balloon_id: str) -> Optional[bpy.types.Object]:
    if not balloon_id:
        return None
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is None:
        obj = bpy.data.objects.get(f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}")
    return obj


def source_state_for_entry(entry) -> str:
    balloon_id = str(getattr(entry, "id", "") or "")
    obj = find_balloon_object(balloon_id)
    if obj is None:
        return balloon_curve_source_state.STATE_GENERATED
    return balloon_curve_source_state.detect_state(obj)


def cleanup_orphan_balloon_objects(scene) -> int:
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None:
        return 0
    valid: set[str] = set()
    for page in getattr(work, "pages", []) or []:
        for entry in getattr(page, "balloons", []) or []:
            entry_id = str(getattr(entry, "id", "") or "")
            if entry_id:
                valid.add(entry_id)
    for entry in getattr(work, "shared_balloons", []) or []:
        entry_id = str(getattr(entry, "id", "") or "")
        if entry_id:
            valid.add(entry_id)
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get(on.PROP_KIND) == "balloon":
            balloon_id = str(obj.get(on.PROP_ID, "") or "")
            if balloon_id and balloon_id not in valid:
                _remove_balloon_object(obj)
                removed += 1
            continue
        if obj.get(PROP_BALLOON_FILL_KIND) == "balloon_fill":
            owner_id = str(obj.get(PROP_BALLOON_FILL_OWNER_ID, "") or "")
            if owner_id and owner_id not in valid:
                _remove_balloon_object(obj)
                removed += 1
            continue
        if obj.get(PROP_BALLOON_SOURCE_KIND) == "geometry_source":
            owner_id = str(obj.get(PROP_BALLOON_SOURCE_OWNER_ID, "") or "")
            if owner_id and owner_id not in valid:
                _remove_balloon_object(obj)
                removed += 1
            continue
        if obj.get(PROP_BALLOON_CLIP_MASK_KIND) == "coma_clip":
            owner_id = str(obj.get(PROP_BALLOON_CLIP_MASK_OWNER_ID, "") or "")
            if owner_id and owner_id not in valid:
                data = getattr(obj, "data", None)
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:  # noqa: BLE001
                    pass
                _remove_unused_data_block(data)
                removed += 1
    return removed


def on_balloon_entry_changed(entry) -> bool:
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if scene is None or work is None or entry is None:
        return False
    try:
        target_ptr = int(entry.as_pointer())
    except Exception:  # noqa: BLE001
        target_ptr = 0
    target_id = str(getattr(entry, "id", "") or "")
    for page in getattr(work, "pages", []) or []:
        for candidate in getattr(page, "balloons", []) or []:
            candidate_id = str(getattr(candidate, "id", "") or "")
            try:
                same_pointer = bool(target_ptr) and int(candidate.as_pointer()) == target_ptr
            except Exception:  # noqa: BLE001
                same_pointer = False
            same_id = bool(target_id) and candidate_id == target_id
            if not same_pointer and not same_id:
                continue
            if _auto_sync_deferred():
                return True
            if _auto_sync_suspended():
                return _sync_existing_balloon_object_lightweight(scene, work, page, candidate)
            return ensure_balloon_curve_object(
                scene=scene,
                entry=candidate,
                page=page,
            ) is not None
    for candidate in getattr(work, "shared_balloons", []) or []:
        candidate_id = str(getattr(candidate, "id", "") or "")
        try:
            same_pointer = bool(target_ptr) and int(candidate.as_pointer()) == target_ptr
        except Exception:  # noqa: BLE001
            same_pointer = False
        same_id = bool(target_id) and candidate_id == target_id
        if not same_pointer and not same_id:
            continue
        if _auto_sync_deferred():
            return True
        if _auto_sync_suspended():
            return _sync_existing_balloon_object_lightweight(scene, work, None, candidate)
        return ensure_balloon_curve_object(
            scene=scene,
            entry=candidate,
            page=None,
        ) is not None
    return False
