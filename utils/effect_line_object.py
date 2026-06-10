"""効果線 GP Object ヘルパ.

新規効果線 GP Object を生成し、Outliner mirror に登録する。
"""

from __future__ import annotations

import math
from typing import Optional

import bpy

from . import gpencil as gp_utils
from . import coma_content_mask
from . import layer_object_sync as los
from . import log
from . import material_opacity_mask
from . import object_naming as on
from . import object_preserve
from . import percentage
from .geom import mm_to_m

_logger = log.get_logger(__name__)

PER_LAYER_EFFECT_DATA_PREFIX = "BName_EffectGP_"
EFFECT_DISPLAY_DATA_PREFIX = "BName_EffectDisplay_"
EFFECT_DISPLAY_ID_PREFIX = "effect_display_"
EFFECT_DISPLAY_KIND = "effect_display"
EFFECT_FRAME_SOURCE_DATA_PREFIX = "BName_EffectFrameSource_"
EFFECT_FRAME_SOURCE_ID_PREFIX = "effect_frame_source_"
EFFECT_FRAME_SOURCE_KIND = "effect_frame_source"
EFFECT_SHAPE_SOURCE_DATA_PREFIX = "BName_EffectShapeSource_"
EFFECT_SHAPE_SOURCE_ID_PREFIX = "effect_shape_source_"
EFFECT_SHAPE_SOURCE_KIND = "effect_shape_source"
EFFECT_DENSITY_SOURCE_DATA_PREFIX = "BName_EffectDensitySource_"
EFFECT_DENSITY_SOURCE_ID_PREFIX = "effect_density_source_"
EFFECT_DENSITY_SOURCE_KIND = "effect_density_source"
PROP_EFFECT_TARGET = "bname_effect_target"
PROP_EFFECT_CONTROLLER_ID = "bname_effect_controller_id"
PROP_EFFECT_DISPLAY_MASK_PARENT = "bname_effect_display_mask_parent"
PROP_EFFECT_SOURCE_ROLE = "bname_effect_source_role"


def _configure_line_material_nodes(
    mat: bpy.types.Material,
    rgba: tuple[float, float, float, float],
    *,
    mask_info=None,
) -> None:
    from . import geometry_nodes_bridge

    mat.diffuse_color = rgba
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    mat.show_transparent_back = False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (520, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (260, 0)
    attr = nodes.new("ShaderNodeAttribute")
    attr.location = (-260, -180)
    attr.attribute_name = geometry_nodes_bridge.EFFECT_ALPHA_ATTR
    mul = nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.location = (0, -140)
    try:
        bsdf.inputs["Base Color"].default_value = (rgba[0], rgba[1], rgba[2], 1.0)
        mul.inputs[1].default_value = rgba[3]
        links.new(attr.outputs["Fac"], mul.inputs[0])
        alpha = material_opacity_mask.multiply_alpha_by_mask(
            mat.node_tree,
            mul.outputs["Value"],
            mask_object=getattr(mask_info, "space_object", None),
            mask_image=getattr(mask_info, "image", None),
            location=(-560, -500),
            label="コマ内容マスク不透明度",
        )
        _mat_alpha = alpha if alpha is not None else mul.outputs["Value"]
        links.new(_mat_alpha, bsdf.inputs["Alpha"])
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    except Exception:  # noqa: BLE001
        mat.use_nodes = False
        mat.blend_method = "BLEND" if rgba[3] < 1.0 else "OPAQUE"


def _resolve_unique_data_name(base: str) -> str:
    coll = gp_utils._gp_data_blocks()
    if base not in coll:
        return base
    for i in range(1, 10000):
        candidate = f"{base}.{i:03d}"
        if candidate not in coll:
            return candidate
    return base


def _new_effect_gp_object_for_layer(
    *, bname_id: str, title: str
) -> bpy.types.Object:
    base_data_name = f"{PER_LAYER_EFFECT_DATA_PREFIX}{bname_id}"
    data_name = _resolve_unique_data_name(base_data_name)
    gp_data = gp_utils.ensure_gpencil(data_name)
    obj_name = title or bname_id
    obj = bpy.data.objects.new(obj_name, gp_data)
    if len(gp_data.layers) == 0:
        try:
            gp_utils.ensure_layer(gp_data, "content")
        except Exception:  # noqa: BLE001
            _logger.exception("new effect GP: default layer create failed")
    return obj


def _display_bname_id(controller_obj: bpy.types.Object | None) -> str:
    if controller_obj is None:
        return ""
    base = str(controller_obj.get(on.PROP_ID, "") or "")
    if not base:
        base = str(getattr(controller_obj, "name", "") or "")
    return f"{EFFECT_DISPLAY_ID_PREFIX}{base}" if base else ""


def _frame_source_bname_id(controller_obj: bpy.types.Object | None) -> str:
    if controller_obj is None:
        return ""
    base = str(controller_obj.get(on.PROP_ID, "") or "")
    if not base:
        base = str(getattr(controller_obj, "name", "") or "")
    return f"{EFFECT_FRAME_SOURCE_ID_PREFIX}{base}" if base else ""


def _shape_source_bname_id(controller_obj: bpy.types.Object | None, role: str) -> str:
    if controller_obj is None:
        return ""
    base = str(controller_obj.get(on.PROP_ID, "") or "")
    if not base:
        base = str(getattr(controller_obj, "name", "") or "")
    role = str(role or "").strip()
    return f"{EFFECT_SHAPE_SOURCE_ID_PREFIX}{role}_{base}" if role and base else ""


def _density_source_bname_id(controller_obj: bpy.types.Object | None) -> str:
    if controller_obj is None:
        return ""
    base = str(controller_obj.get(on.PROP_ID, "") or "")
    if not base:
        base = str(getattr(controller_obj, "name", "") or "")
    return f"{EFFECT_DENSITY_SOURCE_ID_PREFIX}{base}" if base else ""


def find_effect_display_object(controller_obj: bpy.types.Object | None) -> Optional[bpy.types.Object]:
    """効果線の実表示用 Mesh Object を返す。"""
    display_id = _display_bname_id(controller_obj)
    if display_id:
        obj = on.find_object_by_bname_id(display_id, kind=EFFECT_DISPLAY_KIND)
        if obj is not None:
            return obj
    controller_id = str(controller_obj.get(on.PROP_ID, "") or "") if controller_obj is not None else ""
    if not controller_id:
        return None
    for obj in bpy.data.objects:
        if object_preserve.is_preserved(obj):
            continue
        if str(obj.get(PROP_EFFECT_CONTROLLER_ID, "") or "") == controller_id:
            return obj
    return None


def find_effect_frame_source_object(controller_obj: bpy.types.Object | None) -> Optional[bpy.types.Object]:
    source_id = _frame_source_bname_id(controller_obj)
    if source_id:
        obj = on.find_object_by_bname_id(source_id, kind=EFFECT_FRAME_SOURCE_KIND)
        if obj is not None:
            return obj
    controller_id = str(controller_obj.get(on.PROP_ID, "") or "") if controller_obj is not None else ""
    if not controller_id:
        return None
    for obj in bpy.data.objects:
        if object_preserve.is_preserved(obj):
            continue
        if str(obj.get(PROP_EFFECT_CONTROLLER_ID, "") or "") == controller_id and str(obj.get(on.PROP_KIND, "") or "") == EFFECT_FRAME_SOURCE_KIND:
            return obj
    return None


def find_effect_shape_source_object(
    controller_obj: bpy.types.Object | None,
    role: str,
) -> Optional[bpy.types.Object]:
    source_id = _shape_source_bname_id(controller_obj, role)
    if source_id:
        obj = on.find_object_by_bname_id(source_id, kind=EFFECT_SHAPE_SOURCE_KIND)
        if obj is not None:
            return obj
    controller_id = str(controller_obj.get(on.PROP_ID, "") or "") if controller_obj is not None else ""
    if not controller_id:
        return None
    role = str(role or "")
    for obj in bpy.data.objects:
        if object_preserve.is_preserved(obj):
            continue
        if (
            str(obj.get(PROP_EFFECT_CONTROLLER_ID, "") or "") == controller_id
            and str(obj.get(on.PROP_KIND, "") or "") == EFFECT_SHAPE_SOURCE_KIND
            and str(obj.get(PROP_EFFECT_SOURCE_ROLE, "") or "") == role
        ):
            return obj
    return None


def find_effect_density_source_object(controller_obj: bpy.types.Object | None) -> Optional[bpy.types.Object]:
    source_id = _density_source_bname_id(controller_obj)
    if source_id:
        obj = on.find_object_by_bname_id(source_id, kind=EFFECT_DENSITY_SOURCE_KIND)
        if obj is not None:
            return obj
    controller_id = str(controller_obj.get(on.PROP_ID, "") or "") if controller_obj is not None else ""
    if not controller_id:
        return None
    for obj in bpy.data.objects:
        if object_preserve.is_preserved(obj):
            continue
        if str(obj.get(PROP_EFFECT_CONTROLLER_ID, "") or "") == controller_id and str(obj.get(on.PROP_KIND, "") or "") == EFFECT_DENSITY_SOURCE_KIND:
            return obj
    return None


def _delete_source_object(obj: bpy.types.Object | None) -> None:
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return
    try:
        if data is not None and data.users == 0:
            if getattr(data, "bl_rna", None) is not None and data.bl_rna.identifier == "Mesh":
                bpy.data.meshes.remove(data)
            elif getattr(data, "bl_rna", None) is not None and data.bl_rna.identifier == "Curve":
                bpy.data.curves.remove(data)
    except Exception:  # noqa: BLE001
        pass


def delete_effect_frame_source_object(controller_obj: bpy.types.Object | None) -> None:
    _delete_source_object(find_effect_frame_source_object(controller_obj))


def delete_effect_shape_source_object(controller_obj: bpy.types.Object | None, role: str) -> None:
    _delete_source_object(find_effect_shape_source_object(controller_obj, role))


def delete_effect_density_source_object(controller_obj: bpy.types.Object | None) -> None:
    _delete_source_object(find_effect_density_source_object(controller_obj))


def preserve_effect_frame_source_object(controller_obj: bpy.types.Object | None) -> bool:
    return object_preserve.preserve_object(
        find_effect_frame_source_object(controller_obj),
        "効果線の古い始点コマ枠実体を保持",
    )


def preserve_effect_shape_source_object(controller_obj: bpy.types.Object | None, role: str) -> bool:
    return object_preserve.preserve_object(
        find_effect_shape_source_object(controller_obj, role),
        "効果線の古い始終点形状実体を保持",
    )


def preserve_effect_density_source_object(controller_obj: bpy.types.Object | None) -> bool:
    return object_preserve.preserve_object(
        find_effect_density_source_object(controller_obj),
        "効果線の古い距離密度実体を保持",
    )


def delete_effect_display_object(controller_obj: bpy.types.Object | None) -> None:
    delete_effect_frame_source_object(controller_obj)
    delete_effect_shape_source_object(controller_obj, "start")
    delete_effect_shape_source_object(controller_obj, "end")
    delete_effect_density_source_object(controller_obj)
    obj = find_effect_display_object(controller_obj)
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return
    try:
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)
    except Exception:  # noqa: BLE001
        pass


def _link_display_to_controller_collections(display: bpy.types.Object, controller: bpy.types.Object) -> None:
    target_collections = list(getattr(controller, "users_collection", []) or [])
    if not target_collections:
        target_collections = [bpy.context.scene.collection]
    for coll in target_collections:
        try:
            if display.name not in coll.objects.keys():
                coll.objects.link(display)
        except Exception:  # noqa: BLE001
            pass
    for coll in list(getattr(display, "users_collection", []) or []):
        if coll not in target_collections:
            try:
                coll.objects.unlink(display)
            except Exception:  # noqa: BLE001
                pass


def _rebuild_frame_source_mesh(mesh: bpy.types.Mesh, outline_mm) -> None:
    points: list[tuple[float, float]] = []
    for raw in outline_mm or ():
        try:
            x, y = raw
            pt = (mm_to_m(float(x)), mm_to_m(float(y)))
        except Exception:  # noqa: BLE001
            continue
        if points and abs(points[-1][0] - pt[0]) < 1.0e-9 and abs(points[-1][1] - pt[1]) < 1.0e-9:
            continue
        points.append(pt)
    if len(points) > 2 and abs(points[0][0] - points[-1][0]) < 1.0e-9 and abs(points[0][1] - points[-1][1]) < 1.0e-9:
        points.pop()
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    half_z = 0.05
    for x, y in points:
        verts.append((x, y, -half_z))
        verts.append((x, y, half_z))
    count = len(points)
    if count >= 2:
        for i in range(count):
            j = (i + 1) % count
            faces.append((i * 2, j * 2, j * 2 + 1, i * 2 + 1))
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()


def _rebuild_density_source_mesh(mesh: bpy.types.Mesh, points_mm) -> None:
    verts: list[tuple[float, float, float]] = []
    for raw in points_mm or ():
        try:
            x, y = raw
            verts.append((mm_to_m(float(x)), mm_to_m(float(y)), 0.0))
        except Exception:  # noqa: BLE001
            continue
    mesh.clear_geometry()
    if verts:
        mesh.from_pydata(verts, [], [])
    mesh.update()


def _remove_effect_display_gn_modifier(display: bpy.types.Object) -> None:
    try:
        from . import geometry_nodes_bridge as _gn

        modifier = display.modifiers.get(_gn.MODIFIER_NAME)
        if modifier is not None:
            display.modifiers.remove(modifier)
    except Exception:  # noqa: BLE001
        pass


def _effect_alpha_attribute_name() -> str:
    try:
        from . import geometry_nodes_bridge as _gn

        return str(_gn.EFFECT_ALPHA_ATTR)
    except Exception:  # noqa: BLE001
        return "bname_effect_alpha"


def _stroke_role(stroke) -> str:
    return str(getattr(stroke, "role", "") or "line")


def _stroke_material_index(stroke) -> int:
    role = _stroke_role(stroke)
    if role == "underlay":
        return 2
    if role in {"end_fill", "white_outline_white"}:
        return 1
    return 0


def _stroke_z_offset(stroke) -> float:
    role = _stroke_role(stroke)
    if role == "end_fill":
        return -1.2e-4
    if role == "underlay":
        return -8.0e-5
    if role == "white_outline_white":
        return -4.0e-5
    return 0.0


def _stroke_radius_at(stroke, index: int) -> float:
    radii = getattr(stroke, "radii", None)
    if radii is not None and 0 <= index < len(radii):
        return max(0.0, float(radii[index]))
    return max(0.0, float(getattr(stroke, "radius", 0.0) or 0.0))


def _stroke_alpha_at(stroke, index: int) -> float:
    opacities = getattr(stroke, "opacities", None)
    if opacities is not None and 0 <= index < len(opacities):
        return max(0.0, min(1.0, float(opacities[index])))
    return 1.0


def _append_line_segment_mesh(
    *,
    verts: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int, int]],
    face_materials: list[int],
    point_alphas: list[float],
    stroke,
    start_index: int,
    end_index: int,
) -> None:
    points = getattr(stroke, "points_xyz", None) or []
    try:
        sx, sy, sz = points[start_index]
        ex, ey, ez = points[end_index]
    except Exception:  # noqa: BLE001
        return
    dx = float(ex) - float(sx)
    dy = float(ey) - float(sy)
    length = math.hypot(dx, dy)
    if length <= 1.0e-12:
        return
    r0 = _stroke_radius_at(stroke, start_index)
    r1 = _stroke_radius_at(stroke, end_index)
    if r0 <= 1.0e-12 and r1 <= 1.0e-12:
        return
    nx = -dy / length
    ny = dx / length
    z0 = float(sz) + _stroke_z_offset(stroke)
    z1 = float(ez) + _stroke_z_offset(stroke)
    base = len(verts)
    side = 0.0
    try:
        side = float(getattr(stroke, "side", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        side = 0.0
    if _stroke_role(stroke) == "underlay" and abs(side) > 1.0e-9:
        sign = 1.0 if side >= 0.0 else -1.0
        verts.extend(
            [
                (float(sx), float(sy), z0),
                (float(sx) + nx * sign * r0, float(sy) + ny * sign * r0, z0),
                (float(ex) + nx * sign * r1, float(ey) + ny * sign * r1, z1),
                (float(ex), float(ey), z1),
            ]
        )
    else:
        verts.extend(
            [
                (float(sx) + nx * r0, float(sy) + ny * r0, z0),
                (float(sx) - nx * r0, float(sy) - ny * r0, z0),
                (float(ex) - nx * r1, float(ey) - ny * r1, z1),
                (float(ex) + nx * r1, float(ey) + ny * r1, z1),
            ]
        )
    faces.append((base, base + 1, base + 2, base + 3))
    face_materials.append(_stroke_material_index(stroke))
    point_alphas.extend([_stroke_alpha_at(stroke, start_index), _stroke_alpha_at(stroke, start_index), _stroke_alpha_at(stroke, end_index), _stroke_alpha_at(stroke, end_index)])


def _append_stroke_mesh(
    *,
    verts: list[tuple[float, float, float]],
    faces: list[tuple[int, ...]],
    face_materials: list[int],
    point_alphas: list[float],
    stroke,
) -> None:
    points = list(getattr(stroke, "points_xyz", None) or [])
    if not points:
        return
    role = _stroke_role(stroke)
    if (
        role == "end_fill"
        or (role == "white_outline_white" and bool(getattr(stroke, "cyclic", False)))
    ) and len(points) >= 3:
        base = len(verts)
        z_offset = _stroke_z_offset(stroke)
        for index, point in enumerate(points):
            try:
                x, y, z = point
                verts.append((float(x), float(y), float(z) + z_offset))
                point_alphas.append(_stroke_alpha_at(stroke, index))
            except Exception:  # noqa: BLE001
                verts.append((0.0, 0.0, z_offset))
                point_alphas.append(1.0)
        faces.append(tuple(range(base, base + len(points))))
        face_materials.append(_stroke_material_index(stroke))
        return
    count = len(points)
    if count < 2:
        return
    last = count if bool(getattr(stroke, "cyclic", False)) else count - 1
    for index in range(last):
        _append_line_segment_mesh(
            verts=verts,
            faces=faces,  # type: ignore[arg-type]
            face_materials=face_materials,
            point_alphas=point_alphas,
            stroke=stroke,
            start_index=index,
            end_index=(index + 1) % count,
        )


def _rebuild_effect_display_mesh(mesh: bpy.types.Mesh, strokes) -> None:
    materials = list(getattr(mesh, "materials", []) or [])
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    face_materials: list[int] = []
    point_alphas: list[float] = []
    for stroke in strokes or ():
        _append_stroke_mesh(
            verts=verts,
            faces=faces,
            face_materials=face_materials,
            point_alphas=point_alphas,
            stroke=stroke,
        )
    try:
        attr = mesh.attributes.get(_effect_alpha_attribute_name())
        if attr is not None:
            mesh.attributes.remove(attr)
    except Exception:  # noqa: BLE001
        pass
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()
    if materials and len(mesh.materials) == 0:
        for mat in materials:
            if mat is not None:
                mesh.materials.append(mat)
    for index, material_index in enumerate(face_materials):
        if index < len(mesh.polygons):
            mesh.polygons[index].material_index = int(material_index)
    try:
        attr = mesh.attributes.new(_effect_alpha_attribute_name(), "FLOAT", "POINT")
        for index, value in enumerate(point_alphas):
            if index < len(attr.data):
                attr.data[index].value = max(0.0, min(1.0, float(value)))
    except Exception:  # noqa: BLE001
        pass
    mesh.update()


def ensure_effect_frame_source_object(
    *,
    scene: bpy.types.Scene,
    controller_obj: bpy.types.Object,
    outline_mm,
) -> Optional[bpy.types.Object]:
    if scene is None or controller_obj is None or not outline_mm:
        preserve_effect_frame_source_object(controller_obj)
        return None
    source_id = _frame_source_bname_id(controller_obj)
    if not source_id:
        return None
    obj = find_effect_frame_source_object(controller_obj)
    mesh = getattr(obj, "data", None) if obj is not None and getattr(obj, "type", "") == "MESH" else None
    if mesh is None:
        mesh = bpy.data.meshes.new(f"{EFFECT_FRAME_SOURCE_DATA_PREFIX}{source_id}")
    _rebuild_frame_source_mesh(mesh, outline_mm)
    if obj is None or getattr(obj, "type", "") != "MESH":
        obj = bpy.data.objects.new(f"{controller_obj.name}_始点コマ枠", mesh)
    elif obj.data is not mesh:
        old_data = obj.data
        obj.data = mesh
        try:
            if old_data is not None and old_data.users == 0:
                bpy.data.meshes.remove(old_data)
        except Exception:  # noqa: BLE001
            pass
    try:
        title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
        on.stamp_identity(
            obj,
            kind=EFFECT_FRAME_SOURCE_KIND,
            bname_id=source_id,
            title=f"{title}_始点コマ枠",
            z_index=int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0),
            parent_key=str(controller_obj.get(on.PROP_PARENT_KEY, "") or ""),
            folder_id=str(controller_obj.get(on.PROP_FOLDER_ID, "") or ""),
            managed=False,
        )
        obj[PROP_EFFECT_CONTROLLER_ID] = str(controller_obj.get(on.PROP_ID, "") or "")
    except Exception:  # noqa: BLE001
        pass
    _link_display_to_controller_collections(obj, controller_obj)
    try:
        obj.location = tuple(controller_obj.location)
        obj.rotation_euler = tuple(controller_obj.rotation_euler)
        obj.scale = tuple(controller_obj.scale)
    except Exception:  # noqa: BLE001
        pass
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True
    return obj


def ensure_effect_shape_source_object(
    *,
    scene: bpy.types.Scene,
    controller_obj: bpy.types.Object,
    role: str,
    outline_mm,
) -> Optional[bpy.types.Object]:
    if scene is None or controller_obj is None or not outline_mm:
        preserve_effect_shape_source_object(controller_obj, role)
        return None
    source_id = _shape_source_bname_id(controller_obj, role)
    if not source_id:
        return None
    obj = find_effect_shape_source_object(controller_obj, role)
    mesh = getattr(obj, "data", None) if obj is not None and getattr(obj, "type", "") == "MESH" else None
    if mesh is None:
        mesh = bpy.data.meshes.new(f"{EFFECT_SHAPE_SOURCE_DATA_PREFIX}{source_id}")
    _rebuild_frame_source_mesh(mesh, outline_mm)
    if obj is None or getattr(obj, "type", "") != "MESH":
        suffix = "始点形状" if str(role) == "start" else "終点形状"
        obj = bpy.data.objects.new(f"{controller_obj.name}_{suffix}", mesh)
    elif obj.data is not mesh:
        old_data = obj.data
        obj.data = mesh
        try:
            if old_data is not None and old_data.users == 0:
                bpy.data.meshes.remove(old_data)
        except Exception:  # noqa: BLE001
            pass
    try:
        title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
        suffix = "始点形状" if str(role) == "start" else "終点形状"
        on.stamp_identity(
            obj,
            kind=EFFECT_SHAPE_SOURCE_KIND,
            bname_id=source_id,
            title=f"{title}_{suffix}",
            z_index=int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0),
            parent_key=str(controller_obj.get(on.PROP_PARENT_KEY, "") or ""),
            folder_id=str(controller_obj.get(on.PROP_FOLDER_ID, "") or ""),
            managed=False,
        )
        obj[PROP_EFFECT_CONTROLLER_ID] = str(controller_obj.get(on.PROP_ID, "") or "")
        obj[PROP_EFFECT_SOURCE_ROLE] = str(role or "")
    except Exception:  # noqa: BLE001
        pass
    _link_display_to_controller_collections(obj, controller_obj)
    try:
        obj.location = tuple(controller_obj.location)
        obj.rotation_euler = tuple(controller_obj.rotation_euler)
        obj.scale = tuple(controller_obj.scale)
    except Exception:  # noqa: BLE001
        pass
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True
    return obj


def ensure_effect_density_source_object(
    *,
    scene: bpy.types.Scene,
    controller_obj: bpy.types.Object,
    points_mm,
) -> Optional[bpy.types.Object]:
    if scene is None or controller_obj is None or not points_mm:
        preserve_effect_density_source_object(controller_obj)
        return None
    source_id = _density_source_bname_id(controller_obj)
    if not source_id:
        return None
    obj = find_effect_density_source_object(controller_obj)
    mesh = getattr(obj, "data", None) if obj is not None and getattr(obj, "type", "") == "MESH" else None
    if mesh is None:
        mesh = bpy.data.meshes.new(f"{EFFECT_DENSITY_SOURCE_DATA_PREFIX}{source_id}")
    _rebuild_density_source_mesh(mesh, points_mm)
    if obj is None or getattr(obj, "type", "") != "MESH":
        obj = bpy.data.objects.new(f"{controller_obj.name}_距離密度", mesh)
    elif obj.data is not mesh:
        old_data = obj.data
        obj.data = mesh
        try:
            if old_data is not None and old_data.users == 0:
                bpy.data.meshes.remove(old_data)
        except Exception:  # noqa: BLE001
            pass
    try:
        title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
        on.stamp_identity(
            obj,
            kind=EFFECT_DENSITY_SOURCE_KIND,
            bname_id=source_id,
            title=f"{title}_距離密度",
            z_index=int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0),
            parent_key=str(controller_obj.get(on.PROP_PARENT_KEY, "") or ""),
            folder_id=str(controller_obj.get(on.PROP_FOLDER_ID, "") or ""),
            managed=False,
        )
        obj[PROP_EFFECT_CONTROLLER_ID] = str(controller_obj.get(on.PROP_ID, "") or "")
    except Exception:  # noqa: BLE001
        pass
    _link_display_to_controller_collections(obj, controller_obj)
    try:
        obj.location = tuple(controller_obj.location)
        obj.rotation_euler = tuple(controller_obj.rotation_euler)
        obj.scale = tuple(controller_obj.scale)
    except Exception:  # noqa: BLE001
        pass
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True
    return obj


def _ensure_display_material(
    display: bpy.types.Object,
    color=(0.0, 0.0, 0.0, 1.0),
    *,
    opacity: float = 1.0,
    fill_color=(1.0, 1.0, 1.0, 1.0),
    fill_opacity: float = 1.0,
    underlay_color=(1.0, 1.0, 1.0, 1.0),
    mask_info=None,
) -> None:
    display_id = str(display.get(on.PROP_ID, "") or display.name)
    mat_name = f"BName_Effect_Display_Line_{display_id}"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
    fill_mat_name = f"BName_Effect_Display_Fill_{display_id}"
    fill_mat = bpy.data.materials.get(fill_mat_name)
    if fill_mat is None:
        fill_mat = bpy.data.materials.new(fill_mat_name)
    underlay_mat_name = f"BName_Effect_Display_Underlay_{display_id}"
    underlay_mat = bpy.data.materials.get(underlay_mat_name)
    if underlay_mat is None:
        underlay_mat = bpy.data.materials.new(underlay_mat_name)
    try:
        alpha = max(0.0, min(1.0, float(color[3]) * float(opacity)))
        rgba = (float(color[0]), float(color[1]), float(color[2]), alpha)
    except Exception:  # noqa: BLE001
        rgba = (0.0, 0.0, 0.0, max(0.0, min(1.0, float(opacity or 0.0))))
    try:
        fill_alpha = max(0.0, min(1.0, float(fill_color[3]) * float(fill_opacity) * float(opacity)))
        fill_rgba = (float(fill_color[0]), float(fill_color[1]), float(fill_color[2]), fill_alpha)
    except Exception:  # noqa: BLE001
        fill_rgba = (1.0, 1.0, 1.0, max(0.0, min(1.0, float(fill_opacity or 0.0) * float(opacity or 0.0))))
    try:
        underlay_alpha = max(0.0, min(1.0, float(underlay_color[3]) * float(opacity)))
        underlay_rgba = (float(underlay_color[0]), float(underlay_color[1]), float(underlay_color[2]), underlay_alpha)
    except Exception:  # noqa: BLE001
        underlay_rgba = (1.0, 1.0, 1.0, max(0.0, min(1.0, float(opacity or 0.0))))
    fill_mat.diffuse_color = fill_rgba
    underlay_mat.diffuse_color = underlay_rgba
    try:
        _configure_line_material_nodes(mat, rgba, mask_info=mask_info)
        material_opacity_mask.setup_flat_emission_material(
            fill_mat,
            fill_rgba,
            mask_object=getattr(mask_info, "space_object", None),
            mask_image=getattr(mask_info, "image", None),
        )
        material_opacity_mask.setup_flat_emission_material(
            underlay_mat,
            underlay_rgba,
            mask_object=getattr(mask_info, "space_object", None),
            mask_image=getattr(mask_info, "image", None),
        )
    except Exception:  # noqa: BLE001
        pass
    mats = getattr(getattr(display, "data", None), "materials", None)
    if mats is None:
        return
    if len(mats) == 0:
        try:
            mats.append(mat)
        except Exception:  # noqa: BLE001
            pass
    elif mats[0] is not mat:
        try:
            mats[0] = mat
        except Exception:  # noqa: BLE001
            pass
    if len(mats) < 2:
        try:
            mats.append(fill_mat)
        except Exception:  # noqa: BLE001
            pass
    elif mats[1] is not fill_mat:
        try:
            mats[1] = fill_mat
        except Exception:  # noqa: BLE001
            pass
    if len(mats) < 3:
        try:
            mats.append(underlay_mat)
        except Exception:  # noqa: BLE001
            pass
    elif mats[2] is not underlay_mat:
        try:
            mats[2] = underlay_mat
        except Exception:  # noqa: BLE001
            pass


def _move_display_mask_after_geometry_nodes(display: bpy.types.Object) -> None:
    try:
        from . import geometry_nodes_bridge as _gn
        from . import mask_apply

        names = [mod.name for mod in display.modifiers]
        gn_index = names.index(_gn.MODIFIER_NAME)
        mask_names = {mask_apply.MOD_NAME_COMA_MASK, mask_apply.MOD_NAME_PAGE_MASK}
        for mask_name in mask_names:
            if mask_name not in names:
                continue
            mask_index = names.index(mask_name)
            if mask_index < gn_index:
                display.modifiers.move(mask_index, gn_index)
                names = [mod.name for mod in display.modifiers]
                gn_index = names.index(_gn.MODIFIER_NAME)
    except Exception:  # noqa: BLE001
        pass


def _display_mask_is_current(display: bpy.types.Object, parent_key: str) -> bool:
    try:
        from . import mask_apply

        current_parent = str(display.get(PROP_EFFECT_DISPLAY_MASK_PARENT, "") or "")
        if current_parent != str(parent_key or ""):
            return False
        coma_mod = display.modifiers.get(mask_apply.MOD_NAME_COMA_MASK)
        page_mod = display.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK)
        return coma_mod is None and page_mod is None
    except Exception:  # noqa: BLE001
        return False


def _sync_display_mask(display: bpy.types.Object, parent_key: str) -> None:
    parent_key = str(parent_key or "")
    if _display_mask_is_current(display, parent_key):
        try:
            display[PROP_EFFECT_DISPLAY_MASK_PARENT] = parent_key
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        from . import mask_apply

        mask_apply.remove_mask_from_object(display)
        display[PROP_EFFECT_DISPLAY_MASK_PARENT] = parent_key
    except Exception:  # noqa: BLE001
        _logger.exception("effect display mask sync failed")


def _prefer_exact_boolean_for_effect_mask(display: bpy.types.Object) -> None:
    try:
        from . import mask_apply

        for name in (mask_apply.MOD_NAME_COMA_MASK, mask_apply.MOD_NAME_PAGE_MASK):
            mod = display.modifiers.get(name)
            if mod is None or getattr(mod, "type", "") != "BOOLEAN":
                continue
            try:
                mod.solver = "EXACT"
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def ensure_effect_display_object(
    *,
    scene: bpy.types.Scene,
    controller_obj: bpy.types.Object,
    values: dict | None = None,
    strokes=None,
) -> Optional[bpy.types.Object]:
    """効果線を実際に表示する Mesh Object を同期する。

    選択・メタデータは既存レイヤーに残し、画面表示はこの Mesh Object に任せる。
    strokes が渡された場合は、重いノード評価を使わず表示用メッシュへ焼き込む。
    """
    if scene is None or controller_obj is None:
        return None
    display_id = _display_bname_id(controller_obj)
    if not display_id:
        return None
    display = on.find_object_by_bname_id(display_id, kind=EFFECT_DISPLAY_KIND)
    if display is None:
        mesh = bpy.data.meshes.new(f"{EFFECT_DISPLAY_DATA_PREFIX}{display_id}")
        title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
        display = bpy.data.objects.new(f"{title}_表示", mesh)
    title = str(controller_obj.get(on.PROP_TITLE, "") or controller_obj.name)
    z_index = int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0)
    parent_key = str(controller_obj.get(on.PROP_PARENT_KEY, "") or "")
    folder_id = str(controller_obj.get(on.PROP_FOLDER_ID, "") or "")
    on.stamp_identity(
        display,
        kind=EFFECT_DISPLAY_KIND,
        bname_id=display_id,
        title=title,
        z_index=z_index,
        parent_key=parent_key,
        folder_id=folder_id,
        managed=False,
    )
    display[PROP_EFFECT_CONTROLLER_ID] = str(controller_obj.get(on.PROP_ID, "") or "")
    try:
        on.assign_canonical_name(display, "effect", z_index, "effect_display", f"{title}_表示")
    except Exception:  # noqa: BLE001
        pass
    _link_display_to_controller_collections(display, controller_obj)
    try:
        display.location = tuple(controller_obj.location)
        display.rotation_euler = tuple(controller_obj.rotation_euler)
        display.scale = tuple(controller_obj.scale)
    except Exception:  # noqa: BLE001
        pass
    display.hide_viewport = False
    display.hide_render = False
    display.hide_select = False
    # 効果線の編集用 GP (controller) は表示状態のままだと、Blender が表示中の
    # グリースペンシルのためにビューポートを毎フレーム再描画し続け、用紙ガイド線・
    # 効果線などの細線がずっと点滅する (TAA が settle しない)。画面表示はこの
    # 表示用 Mesh が担うので、編集用 GP は必ずビューポート非表示にする。
    try:
        if str(getattr(controller_obj, "type", "") or "") == "GREASEPENCIL":
            controller_obj.hide_viewport = True
            controller_obj.hide_render = True
    except Exception:  # noqa: BLE001
        pass
    line_color = (values or {}).get("線色", (0.0, 0.0, 0.0, 1.0))
    line_opacity = percentage.percent_to_factor((values or {}).get("不透明度", 100.0), 100.0)
    fill_color = (values or {}).get("塗り色", (1.0, 1.0, 1.0, 1.0))
    fill_opacity = percentage.percent_to_factor((values or {}).get("塗り不透明度", 100.0), 100.0)
    underlay_color = (values or {}).get("白抜き線色", (1.0, 1.0, 1.0, 1.0))
    if int((values or {}).get("種類", 0) or 0) == 5:
        fill_color = (1.0, 1.0, 1.0, 1.0)
        fill_opacity = 1.0
    _ensure_display_material(
        display,
        line_color,
        opacity=line_opacity,
        fill_color=fill_color,
        fill_opacity=fill_opacity,
        underlay_color=underlay_color,
        mask_info=coma_content_mask.ensure_viewport_mask_for_parent(
            scene,
            getattr(scene, "bname_work", None),
            parent_key,
        ),
    )
    if strokes is not None:
        _remove_effect_display_gn_modifier(display)
        try:
            _rebuild_effect_display_mesh(display.data, strokes)
            _sync_display_mask(display, parent_key)
        except Exception:  # noqa: BLE001
            _logger.exception("effect display mesh sync failed")
        return display
    try:
        from . import geometry_nodes_bridge as _gn

        _gn.ensure_modifier(display, "effect_line", values or {})
        _sync_display_mask(display, parent_key)
    except Exception:  # noqa: BLE001
        _logger.exception("effect display Geometry Nodes sync failed")
    return display


def sync_effect_display_transform(controller_obj: bpy.types.Object | None) -> None:
    if controller_obj is None:
        return
    # 読み込み/同期のたびに編集用 GP (controller) を非表示へ戻す。表示状態の
    # グリースペンシルは Blender がビューポートを毎フレーム再描画させ続け、用紙
    # ガイド線・効果線などの細線がずっと点滅する。既存ファイルで表示状態のまま
    # 残っていた編集用 GP も、これで開いた時点で確実に隠れる。
    try:
        if str(getattr(controller_obj, "type", "") or "") == "GREASEPENCIL" and not controller_obj.hide_viewport:
            controller_obj.hide_viewport = True
            controller_obj.hide_render = True
    except Exception:  # noqa: BLE001
        pass
    display = find_effect_display_object(controller_obj)
    source = find_effect_frame_source_object(controller_obj)
    density_source = find_effect_density_source_object(controller_obj)
    if display is not None:
        _link_display_to_controller_collections(display, controller_obj)
        try:
            display[on.PROP_PARENT_KEY] = str(controller_obj.get(on.PROP_PARENT_KEY, "") or "")
            display[on.PROP_FOLDER_ID] = str(controller_obj.get(on.PROP_FOLDER_ID, "") or "")
            display[on.PROP_Z_INDEX] = int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0)
            _sync_display_mask(display, str(controller_obj.get(on.PROP_PARENT_KEY, "") or ""))
        except Exception:  # noqa: BLE001
            pass
        try:
            display.location = tuple(controller_obj.location)
            display.rotation_euler = tuple(controller_obj.rotation_euler)
            display.scale = tuple(controller_obj.scale)
        except Exception:  # noqa: BLE001
            pass
    if source is not None:
        _link_display_to_controller_collections(source, controller_obj)
        try:
            source[on.PROP_PARENT_KEY] = str(controller_obj.get(on.PROP_PARENT_KEY, "") or "")
            source[on.PROP_FOLDER_ID] = str(controller_obj.get(on.PROP_FOLDER_ID, "") or "")
            source[on.PROP_Z_INDEX] = int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0)
            source.location = tuple(controller_obj.location)
            source.rotation_euler = tuple(controller_obj.rotation_euler)
            source.scale = tuple(controller_obj.scale)
        except Exception:  # noqa: BLE001
            pass
    if density_source is not None:
        _link_display_to_controller_collections(density_source, controller_obj)
        try:
            density_source[on.PROP_PARENT_KEY] = str(controller_obj.get(on.PROP_PARENT_KEY, "") or "")
            density_source[on.PROP_FOLDER_ID] = str(controller_obj.get(on.PROP_FOLDER_ID, "") or "")
            density_source[on.PROP_Z_INDEX] = int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0)
            density_source.location = tuple(controller_obj.location)
            density_source.rotation_euler = tuple(controller_obj.rotation_euler)
            density_source.scale = tuple(controller_obj.scale)
        except Exception:  # noqa: BLE001
            pass


def sync_controller_transform_from_display(display_obj: bpy.types.Object | None) -> bool:
    """Reflect standard Blender transform edits on effect helper objects to controller."""
    if display_obj is None:
        return False
    kind = str(display_obj.get(on.PROP_KIND, "") or "")
    if kind not in {
        EFFECT_DISPLAY_KIND,
        EFFECT_FRAME_SOURCE_KIND,
        EFFECT_SHAPE_SOURCE_KIND,
        EFFECT_DENSITY_SOURCE_KIND,
    }:
        return False
    controller_id = str(display_obj.get(PROP_EFFECT_CONTROLLER_ID, "") or "")
    if not controller_id:
        return False
    controller = on.find_object_by_bname_id(controller_id, kind="effect")
    if controller is None:
        return False
    changed = (
        tuple(controller.location) != tuple(display_obj.location)
        or tuple(controller.rotation_euler) != tuple(display_obj.rotation_euler)
        or tuple(controller.scale) != tuple(display_obj.scale)
    )
    if not changed:
        return False
    with los.suppress_sync():
        controller.location = tuple(display_obj.location)
        controller.rotation_euler = tuple(display_obj.rotation_euler)
        controller.scale = tuple(display_obj.scale)
    sync_effect_display_transform(controller)
    return True


def create_effect_line_object(
    *,
    scene: bpy.types.Scene,
    bname_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
    target_ref: str = "",
) -> Optional[bpy.types.Object]:
    """新規効果線 GP Object を生成し、Outliner mirror に登録."""
    if scene is None or not bname_id:
        return None
    obj = on.find_object_by_bname_id(bname_id, kind="effect")
    if obj is None:
        obj = _new_effect_gp_object_for_layer(bname_id=bname_id, title=title)
    # 編集用 GP は作成直後から非表示にする。表示状態のグリースペンシルは Blender が
    # ビューポートを連続再描画させ、細線が点滅し続ける。画面表示は表示用 Mesh が担う。
    try:
        obj.hide_viewport = True
        obj.hide_render = True
    except Exception:  # noqa: BLE001
        pass
    los.stamp_layer_object(
        obj,
        kind="effect",
        bname_id=bname_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
        scene=scene,
    )
    if target_ref:
        obj[PROP_EFFECT_TARGET] = target_ref
    try:
        work = getattr(scene, "bname_work", None)
        if work is not None:
            los.assign_per_page_z_ranks(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("create_effect_line_object: z order sync failed")
    try:
        gp_utils.ensure_default_stroke_material(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("create_effect_line_object: default material failed")
    try:
        from . import mask_apply

        mask_apply.apply_mask_to_layer_object(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("create_effect_line_object: mask_apply failed")
    return obj
