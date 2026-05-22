"""フキダシ表示オブジェクトの同期ヘルパ."""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Optional, Sequence

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import percentage
from .geom import mm_to_m

_logger = log.get_logger(__name__)

BALLOON_CURVE_NAME_PREFIX = "balloon_"
BALLOON_FILL_NAME_PREFIX = "balloon_fill_"
BALLOON_CARRIER_MESH_DATA_PREFIX = "balloon_carrier_mesh_"
BALLOON_SOURCE_NAME_PREFIX = "balloon_source_"
BALLOON_CURVE_MATERIAL_PREFIX = "BName_Balloon_Curve_"
BALLOON_FILL_MATERIAL_PREFIX = "BName_Balloon_Fill_"
PROP_BALLOON_FILL_KIND = "bname_balloon_fill_kind"
PROP_BALLOON_FILL_OWNER_ID = "bname_balloon_fill_owner_id"
PROP_BALLOON_FILL_SOURCE_MATERIAL = "bname_balloon_fill_source_material"
PROP_BALLOON_SOURCE_KIND = "bname_balloon_source_kind"
PROP_BALLOON_SOURCE_OWNER_ID = "bname_balloon_source_owner_id"
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


def _replace_object_with_mesh(
    *,
    obj: Optional[bpy.types.Object],
    obj_name: str,
    mesh: bpy.types.Mesh,
) -> bpy.types.Object:
    if obj is not None and obj.type != "MESH":
        _remove_balloon_object(obj)
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        old_data = getattr(obj, "data", None)
        obj.data = mesh
        _remove_unused_data_block(old_data)
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
        mat.use_nodes = True
        nt = mat.node_tree
        # 既存ノード全削除して再構築
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (200, 0)
        emission = nt.nodes.new("ShaderNodeEmission")
        emission.location = (-100, 0)
        try:
            emission.inputs["Color"].default_value = line
        except Exception:  # noqa: BLE001
            pass
        try:
            emission.inputs["Strength"].default_value = 1.0
        except Exception:  # noqa: BLE001
            pass
        nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
        try:
            mat.blend_method = "BLEND"
        except (AttributeError, TypeError):
            pass
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
        mat.blend_method = "BLEND"
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


def _ensure_fill_material(material_name: str, entry=None) -> bpy.types.Material:
    mat, copied_user_material = _fill_material_for_entry(material_name, entry)
    fill = _entry_fill_rgba(entry)
    _apply_fill_material_basics(mat, fill, entry)
    if copied_user_material and not bool(getattr(entry, "fill_gradient_enabled", False)):
        return mat
    mat.use_nodes = True
    try:
        nt = mat.node_tree
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (200, 0)
        principled = nt.nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (-100, 0)
        if bool(getattr(entry, "fill_gradient_enabled", False)):
            start = tuple(float(v) for v in getattr(entry, "fill_gradient_start_color", fill))
            end = tuple(float(v) for v in getattr(entry, "fill_gradient_end_color", fill))
            coord = nt.nodes.new("ShaderNodeTexCoord")
            coord.location = (-760, 0)
            mapping = nt.nodes.new("ShaderNodeMapping")
            mapping.location = (-560, 0)
            gradient = nt.nodes.new("ShaderNodeTexGradient")
            gradient.location = (-360, 0)
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.location = (-160, 60)
            ramp.color_ramp.elements[0].position = 0.0
            ramp.color_ramp.elements[0].color = start
            ramp.color_ramp.elements[1].position = 1.0
            ramp.color_ramp.elements[1].color = end
            try:
                mapping.inputs["Rotation"].default_value[2] = math.radians(float(getattr(entry, "fill_gradient_angle_deg", 90.0) or 90.0))
            except Exception:  # noqa: BLE001
                pass
            nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
            nt.links.new(mapping.outputs["Vector"], gradient.inputs["Vector"])
            nt.links.new(gradient.outputs["Fac"], ramp.inputs["Fac"])
            nt.links.new(ramp.outputs["Color"], principled.inputs["Base Color"])
        else:
            principled.inputs["Base Color"].default_value = fill
        principled.inputs["Alpha"].default_value = fill[3]
        nt.links.new(principled.outputs["BSDF"], out.inputs["Surface"])
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


def ensure_balloon_curve_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
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

    # 1. Mesh Object 生成 or 再利用。表示形状は Geometry Nodes が入力値から生成する。
    obj_name = f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}"
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    carrier_mesh = _ensure_balloon_carrier_mesh(balloon_id, line_mat, fill_mat)
    obj = _replace_object_with_mesh(obj=obj, obj_name=obj_name, mesh=carrier_mesh)
    _remove_duplicate_balloon_objects(balloon_id, obj)
    _remove_legacy_balloon_fill_objects(balloon_id)
    _remove_balloon_source_object(balloon_id)

    # 2. ページローカル座標 mm → m
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))

    # 3. parent 解決 (balloon_text_plane と同方針)
    default_parent_kind = "outside" if page is None else "page"
    entry_parent_kind = str(getattr(entry, "parent_kind", "") or default_parent_kind)
    entry_parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder_id = folder_id or str(getattr(entry, "folder_key", "") or "")
    if entry_parent_kind in {"none", "outside"}:
        stamp_kind, stamp_key, stamp_folder = "outside", "", ""
    elif entry_parent_kind == "coma" and entry_parent_key:
        stamp_kind, stamp_key, stamp_folder = "coma", entry_parent_key, entry_folder_id
    elif entry_parent_kind == "folder" and entry_folder_id:
        stamp_kind, stamp_key, stamp_folder = "folder", entry_folder_id, entry_folder_id
    else:
        stamp_kind = "page"
        stamp_key = entry_parent_key or str(getattr(page, "id", "") or "")
        stamp_folder = entry_folder_id

    # 4. z_index は page.balloons 配列 index に基づく (kind 別 base offset)
    BALLOON_Z_BASE = 1000
    z_index = BALLOON_Z_BASE
    work = getattr(scene, "bname_work", None)
    balloons = getattr(page, "balloons", None) if page is not None else getattr(work, "shared_balloons", None)
    if balloons is not None:
        for i, e in enumerate(balloons):
            if str(getattr(e, "id", "") or "") == balloon_id:
                z_index = BALLOON_Z_BASE + (i + 1) * 10
                break

    los.stamp_layer_object(
        obj,
        kind="balloon",
        bname_id=balloon_id,
        title=str(getattr(entry, "title", "") or balloon_id),
        z_index=z_index,
        parent_kind=stamp_kind,
        parent_key=stamp_key,
        folder_id=stamp_folder,
        scene=scene,
        # entry.x_mm/y_mm をページローカル座標として独自管理し、その値に
        # page_grid のオフセットを加算して world 座標とする。
        apply_page_offset=False,
    )
    # ページオフセットを entry.x_mm/y_mm に加算して world 位置を決定
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
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    try:
        if work is not None:
            los.assign_per_page_z_ranks(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: z order sync failed")
    try:
        from . import geometry_nodes_bridge as _gn

        _gn.ensure_modifier(
            obj,
            "balloon",
            _gn.balloon_values(entry),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: Geometry Nodes 同期失敗")
    return obj


def _set_mesh_materials(mesh: bpy.types.Mesh, materials: Sequence[bpy.types.Material | None]) -> None:
    try:
        mesh.materials.clear()
    except Exception:  # noqa: BLE001
        while len(mesh.materials) > 0:
            mesh.materials.pop(index=len(mesh.materials) - 1)
    for mat in materials:
        if mat is not None:
            mesh.materials.append(mat)


def _ensure_balloon_carrier_mesh(
    balloon_id: str,
    line_material: bpy.types.Material,
    fill_material: Optional[bpy.types.Material],
) -> bpy.types.Mesh:
    mesh_name = f"{BALLOON_CARRIER_MESH_DATA_PREFIX}{balloon_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    mesh.clear_geometry()
    mesh.update()
    _set_mesh_materials(mesh, (line_material, fill_material))
    return mesh


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
    _apply_balloon_object_transform(scene, work, page, entry, obj)
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    try:
        from . import geometry_nodes_bridge as _gn

        _remove_balloon_source_object(balloon_id)
        _gn.ensure_modifier(
            obj,
            "balloon",
            _gn.balloon_values(entry),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: lightweight Geometry Nodes sync failed")
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
