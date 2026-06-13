"""フィルレイヤーの実体平面同期.

ベタ塗り・グラデーションをマテリアル付き Mesh 平面として表示する。
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import object_preserve
from .geom import mm_to_m

_logger = log.get_logger(__name__)

FILL_OBJECT_NAME_PREFIX = "fill_"
FILL_MESH_NAME_PREFIX = "fill_mesh_"
FILL_MATERIAL_NAME_PREFIX = "BName_Fill_"
FILL_Z_BASE = 250
_AUTO_SYNC_SUSPEND_DEPTH = 0


@contextmanager
def suspend_auto_sync():
    global _AUTO_SYNC_SUSPEND_DEPTH
    _AUTO_SYNC_SUSPEND_DEPTH += 1
    try:
        yield
    finally:
        _AUTO_SYNC_SUSPEND_DEPTH = max(0, _AUTO_SYNC_SUSPEND_DEPTH - 1)


def auto_sync_suspended() -> bool:
    return _AUTO_SYNC_SUSPEND_DEPTH > 0


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))


def _object_name(fill_id: str) -> str:
    return f"{FILL_OBJECT_NAME_PREFIX}{_safe_token(fill_id)}"


def _mesh_name(fill_id: str) -> str:
    return f"{FILL_MESH_NAME_PREFIX}{_safe_token(fill_id)}"


def _material_name(fill_id: str) -> str:
    return f"{FILL_MATERIAL_NAME_PREFIX}{_safe_token(fill_id)}"


def _resolve_parent_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder = folder_id or str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if parent_kind == "coma" and parent_key:
        return "coma", parent_key, entry_folder
    if parent_kind == "folder":
        folder_key = entry_folder or parent_key
        if folder_key:
            return "folder", folder_key, folder_key
    return "page", parent_key or str(getattr(page, "id", "") or ""), entry_folder


def _page_by_id(work, page_id: str):
    if work is None or not page_id:
        return None
    for candidate in getattr(work, "pages", []) or []:
        if str(getattr(candidate, "id", "") or "") == page_id:
            return candidate
    return None


def _semantic_parent_key_for_entry(work, entry, fallback_page=None) -> str:
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    folder_key = str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return ""
    if parent_kind == "folder":
        folder_key = folder_key or parent_key
        if folder_key:
            try:
                from . import layer_folder
                from .layer_hierarchy import OUTSIDE_STACK_KEY

                semantic = layer_folder.semantic_parent_key_for_folder(work, folder_key)
                return "" if semantic == OUTSIDE_STACK_KEY else semantic
            except Exception:  # noqa: BLE001
                return ""
    if parent_key:
        return parent_key
    if folder_key:
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, folder_key)
            if semantic != OUTSIDE_STACK_KEY:
                return semantic
        except Exception:  # noqa: BLE001
            pass
    return str(getattr(fallback_page, "id", "") or "")


def page_for_entry(scene, work, entry, fallback_page=None):
    key = _semantic_parent_key_for_entry(work, entry, fallback_page)
    page_id = key.split(":", 1)[0] if key else ""
    page = _page_by_id(work, page_id)
    if (
        page is None
        and str(getattr(entry, "parent_kind", "") or "page") == "page"
        and not page_id
    ):
        pages = getattr(work, "pages", None)
        if pages and len(pages):
            page = pages[0]
    return page


def entry_page_offset_mm(scene, work, entry, page):
    try:
        from . import page_grid
    except ImportError:
        return 0.0, 0.0
    if page is None or work is None:
        return 0.0, 0.0
    page_id = str(getattr(page, "id", "") or "")
    for i, p in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(p, "id", "") or "") == page_id:
            return page_grid.page_total_offset_mm(work, scene, i)
    return 0.0, 0.0


def _fill_z_index(scene, fill_id: str) -> int:
    coll = getattr(scene, "bname_fill_layers", None) if scene is not None else None
    if coll is None:
        return FILL_Z_BASE
    for i, entry in enumerate(coll):
        if str(getattr(entry, "id", "") or "") == fill_id:
            return FILL_Z_BASE + (i + 1) * 10
    return FILL_Z_BASE


def _rebuild_mesh(mesh: bpy.types.Mesh, width_m: float, height_m: float) -> None:
    half_w = width_m * 0.5
    half_h = height_m * 0.5
    verts = [
        (-half_w, -half_h, 0.0),
        (half_w, -half_h, 0.0),
        (half_w, half_h, 0.0),
        (-half_w, half_h, 0.0),
    ]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for loop_index, uv in zip(mesh.polygons[0].loop_indices, uvs, strict=False):
        uv_layer.data[loop_index].uv = uv


def _rebuild_lasso_mesh(
    mesh: bpy.types.Mesh,
    points_mm: list,
    center_x_mm: float,
    center_y_mm: float,
    canvas_w_mm: float,
    canvas_h_mm: float,
) -> bool:
    import bmesh

    if len(points_mm) < 3:
        return False
    bm = bmesh.new()
    try:
        for x, y in points_mm:
            bm.verts.new((mm_to_m(x - center_x_mm), mm_to_m(y - center_y_mm), 0.0))
        bm.verts.ensure_lookup_table()
        try:
            face = bm.faces.new(bm.verts)
        except ValueError:
            return False
        bmesh.ops.triangulate(bm, faces=[face])
        mesh.clear_geometry()
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    cw = canvas_w_mm if canvas_w_mm > 1e-6 else 1.0
    ch = canvas_h_mm if canvas_h_mm > 1e-6 else 1.0
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            v = mesh.vertices[mesh.loops[li].vertex_index]
            px = v.co.x * 1000.0 + center_x_mm
            py = v.co.y * 1000.0 + center_y_mm
            uv_layer.data[li].uv = (px / cw, py / ch)
    return True


def _ensure_solid_material(name: str, color: tuple, opacity: float) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
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
    out.location = (360, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-60, -140)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-60, 60)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (140, 0)

    r, g, b = float(color[0]), float(color[1]), float(color[2])
    a = float(color[3]) if len(color) > 3 else 1.0
    fac = a * (opacity / 100.0)

    emission.inputs["Color"].default_value = (r, g, b, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    mix.inputs["Fac"].default_value = fac
    nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])

    try:
        mat.diffuse_color = (r, g, b, fac)
    except Exception:  # noqa: BLE001
        pass
    return mat


def _ensure_gradient_material(
    name: str,
    color1: tuple,
    color2: tuple,
    gradient_type: str,
    angle_rad: float,
    opacity: float,
    *,
    endpoint_uv: tuple | None = None,
) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
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
    out.location = (600, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (160, -140)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (160, 60)
    mix_shader = nt.nodes.new("ShaderNodeMixShader")
    mix_shader.location = (380, 0)

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-600, 0)
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-400, 0)
    mapping.vector_type = "TEXTURE"
    gradient = nt.nodes.new("ShaderNodeTexGradient")
    gradient.location = (-200, 0)
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (0, 0)

    if gradient_type == "radial":
        gradient.gradient_type = "SPHERICAL"
        if endpoint_uv is not None:
            su, sv, eu, ev = endpoint_uv
            dx, dy = eu - su, ev - sv
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1e-6:
                dist = 1.0
            mapping.inputs["Location"].default_value = (su, sv, 0.0)
            mapping.inputs["Scale"].default_value = (1.0 / dist, 1.0 / dist, 1.0)
        else:
            mapping.inputs["Location"].default_value = (0.5, 0.5, 0.0)
    else:
        gradient.gradient_type = "LINEAR"
        if endpoint_uv is not None:
            su, sv, eu, ev = endpoint_uv
            dx, dy = eu - su, ev - sv
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1e-6:
                dist = 1.0
            ep_angle = math.atan2(dy, dx)
            mapping.inputs["Location"].default_value = (su, sv, 0.0)
            mapping.inputs["Rotation"].default_value = (0.0, 0.0, -ep_angle)
            mapping.inputs["Scale"].default_value = (1.0 / dist, 1.0, 1.0)
        else:
            mapping.inputs["Rotation"].default_value = (0.0, 0.0, angle_rad)
            mapping.inputs["Location"].default_value = (0.5, 0.5, 0.0)

    cr = ramp.color_ramp
    cr.elements[0].color = (float(color1[0]), float(color1[1]), float(color1[2]), 1.0)
    cr.elements[1].color = (float(color2[0]), float(color2[1]), float(color2[2]), 1.0)

    alpha = opacity / 100.0
    emission.inputs["Strength"].default_value = 1.0
    mix_shader.inputs["Fac"].default_value = alpha

    nt.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], gradient.inputs["Vector"])
    nt.links.new(gradient.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], emission.inputs["Color"])
    nt.links.new(transparent.outputs["BSDF"], mix_shader.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix_shader.inputs[2])
    nt.links.new(mix_shader.outputs["Shader"], out.inputs["Surface"])

    r1, g1, b1 = float(color1[0]), float(color1[1]), float(color1[2])
    try:
        mat.diffuse_color = (r1, g1, b1, alpha)
    except Exception:  # noqa: BLE001
        pass
    return mat


def _endpoint_uv_for_entry(entry, width_mm: float, height_mm: float):
    if not getattr(entry, "use_gradient_endpoints", False):
        return None
    if width_mm < 1e-6 or height_mm < 1e-6:
        return None
    su = float(getattr(entry, "gradient_start_x_mm", 0.0) or 0.0) / width_mm
    sv = float(getattr(entry, "gradient_start_y_mm", 0.0) or 0.0) / height_mm
    eu = float(getattr(entry, "gradient_end_x_mm", 0.0) or 0.0) / width_mm
    ev = float(getattr(entry, "gradient_end_y_mm", 0.0) or 0.0) / height_mm
    return (su, sv, eu, ev)


def _ensure_material(entry, width_mm: float = 182.0, height_mm: float = 257.0) -> bpy.types.Material:
    fill_id = str(getattr(entry, "id", "") or "")
    name = _material_name(fill_id)
    fill_type = str(getattr(entry, "fill_type", "solid") or "solid")
    opacity = float(getattr(entry, "opacity", 100.0) or 100.0)
    color = tuple(entry.color)

    if fill_type == "gradient":
        color2 = tuple(entry.color2)
        grad_type = str(getattr(entry, "gradient_type", "linear") or "linear")
        angle = float(getattr(entry, "gradient_angle", 0.0) or 0.0)
        ep_uv = _endpoint_uv_for_entry(entry, width_mm, height_mm)
        return _ensure_gradient_material(
            name, color, color2, grad_type, angle, opacity, endpoint_uv=ep_uv,
        )
    return _ensure_solid_material(name, color, opacity)


def ensure_fill_real_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    if scene is None or entry is None:
        return None
    fill_id = str(getattr(entry, "id", "") or "")
    if not fill_id:
        return None

    work = getattr(scene, "bname_work", None)
    paper = getattr(work, "paper", None) if work is not None else None
    canvas_w_mm = float(getattr(paper, "canvas_width_mm", 182.0) or 182.0)
    canvas_h_mm = float(getattr(paper, "canvas_height_mm", 257.0) or 257.0)

    use_region = bool(getattr(entry, "use_region", False))
    lasso_json = str(getattr(entry, "lasso_points_json", "") or "")
    lasso_points = None
    if lasso_json:
        import json
        try:
            lasso_points = json.loads(lasso_json)
        except (json.JSONDecodeError, TypeError):
            lasso_points = None
        if lasso_points is not None and len(lasso_points) < 3:
            lasso_points = None

    if use_region:
        rw = float(getattr(entry, "region_width_mm", 0.0) or 0.0)
        rh = float(getattr(entry, "region_height_mm", 0.0) or 0.0)
        if rw < 0.1 or rh < 0.1:
            use_region = False

    if use_region:
        mesh_w_mm = rw
        mesh_h_mm = rh
        rx = float(getattr(entry, "region_x_mm", 0.0) or 0.0)
        ry = float(getattr(entry, "region_y_mm", 0.0) or 0.0)
    else:
        mesh_w_mm = canvas_w_mm
        mesh_h_mm = canvas_h_mm

    mat = _ensure_material(entry, canvas_w_mm, canvas_h_mm)

    mesh = bpy.data.meshes.get(_mesh_name(fill_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(_mesh_name(fill_id))

    if lasso_points is not None and use_region:
        center_x = rx + mesh_w_mm * 0.5
        center_y = ry + mesh_h_mm * 0.5
        if not _rebuild_lasso_mesh(mesh, lasso_points, center_x, center_y, canvas_w_mm, canvas_h_mm):
            _rebuild_mesh(mesh, mm_to_m(mesh_w_mm), mm_to_m(mesh_h_mm))
    else:
        _rebuild_mesh(mesh, mm_to_m(mesh_w_mm), mm_to_m(mesh_h_mm))
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj_name = _object_name(fill_id)
    obj = on.find_object_by_bname_id(fill_id, kind="fill")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    if object_preserve.is_preserved(obj):
        obj = None
    if obj is not None and obj.type != "MESH":
        object_preserve.preserve_object(obj, "古いフィル実体を保持")
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
    if use_region:
        obj.location.x = mm_to_m(rx + mesh_w_mm * 0.5 + ox_mm)
        obj.location.y = mm_to_m(ry + mesh_h_mm * 0.5 + oy_mm)
    else:
        obj.location.x = mm_to_m(canvas_w_mm * 0.5 + ox_mm)
        obj.location.y = mm_to_m(canvas_h_mm * 0.5 + oy_mm)

    parent_kind, parent_key, stamp_folder = _resolve_parent_for_entry(entry, page, folder_id)
    los.stamp_layer_object(
        obj,
        kind="fill",
        bname_id=fill_id,
        title=str(getattr(entry, "title", "") or fill_id),
        z_index=_fill_z_index(scene, fill_id),
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=stamp_folder,
        scene=scene,
        apply_page_offset=False,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    obj.hide_select = False
    return obj


def find_fill_entry(scene, fill_id: str):
    coll = getattr(scene, "bname_fill_layers", None) if scene is not None else None
    if coll is None:
        return None
    for entry in coll:
        if str(getattr(entry, "id", "") or "") == fill_id:
            return entry
    return None


def cleanup_orphan_fill_objects(scene: bpy.types.Scene) -> int:
    coll = getattr(scene, "bname_fill_layers", None) if scene is not None else None
    valid = {str(getattr(entry, "id", "") or "") for entry in coll or []}
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "fill":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid in valid:
            continue
        object_preserve.preserve_object(obj, "作品データにないフィル実体を保持")
        removed += 1
    return removed


def remove_fill_real_object(fill_id: str) -> bool:
    if not fill_id:
        return False
    removed = False
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "fill":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid != fill_id:
            continue
        data = getattr(obj, "data", None)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("fill real object: removal failed")
            continue
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
            except Exception:  # noqa: BLE001
                pass
        removed = True
    mat_name = _material_name(fill_id)
    mat = bpy.data.materials.get(mat_name)
    if mat is not None and getattr(mat, "users", 0) == 0:
        try:
            bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            pass
    return removed


def sync_all_fill_real_objects(scene: bpy.types.Scene, work) -> int:
    if scene is None or work is None:
        return 0
    coll = getattr(scene, "bname_fill_layers", None)
    if coll is None:
        return 0
    count = 0
    for entry in coll:
        page = page_for_entry(scene, work, entry)
        if ensure_fill_real_object(scene=scene, entry=entry, page=page) is not None:
            count += 1
    cleanup_orphan_fill_objects(scene)
    return count


def on_fill_entry_changed(entry) -> bool:
    if auto_sync_suspended():
        return False
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if scene is None or work is None or entry is None:
        return False
    fill_id = str(getattr(entry, "id", "") or "")
    if not fill_id:
        return False
    page = page_for_entry(scene, work, entry)
    ensure_fill_real_object(scene=scene, entry=entry, page=page)
    return True
