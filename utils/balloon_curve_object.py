"""フキダシ Curve Object ヘルパ (Phase 4c).

`utils/balloon_shapes.outline_for_entry` から得られる輪郭点列を Bezier Curve
として生成し、Outliner mirror に登録する。

Curve は ``bevel_depth`` で線幅を持たせ、``fill_mode="BOTH"`` で内側塗り
潰しを行う。基本形状 (rect/ellipse/cloud/octagon 等) と尻尾を同じ Curve
Object 内の複数スプラインとして同期する。
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import bpy

from . import balloon_shapes as bs
from . import balloon_tail_geom
from . import balloon_uni_flash
from . import layer_object_sync as los
from . import log
from . import object_naming as on
from .geom import mm_to_m

_logger = log.get_logger(__name__)

BALLOON_CURVE_NAME_PREFIX = "balloon_"
BALLOON_FILL_NAME_PREFIX = "balloon_fill_"
BALLOON_CURVE_DATA_PREFIX = "balloon_curve_"
BALLOON_CARRIER_MESH_DATA_PREFIX = "balloon_carrier_mesh_"
BALLOON_SOURCE_NAME_PREFIX = "balloon_source_"
BALLOON_FILL_DATA_PREFIX = "balloon_fill_curve_"
BALLOON_MESH_DATA_PREFIX = "balloon_mesh_"
BALLOON_FILL_MESH_DATA_PREFIX = "balloon_fill_mesh_"
BALLOON_CURVE_MATERIAL_PREFIX = "BName_Balloon_Curve_"
BALLOON_FILL_MATERIAL_PREFIX = "BName_Balloon_Fill_"
PROP_BALLOON_FILL_KIND = "bname_balloon_fill_kind"
PROP_BALLOON_FILL_OWNER_ID = "bname_balloon_fill_owner_id"
PROP_BALLOON_FILL_SOURCE_MATERIAL = "bname_balloon_fill_source_material"
PROP_BALLOON_SOURCE_KIND = "bname_balloon_source_kind"
PROP_BALLOON_SOURCE_OWNER_ID = "bname_balloon_source_owner_id"
FILL_LOCAL_Z_OFFSET_M = -0.001


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


def _curve_to_mesh_data(
    *,
    scene: bpy.types.Scene,
    curve_data: bpy.types.Curve,
    mesh_name: str,
) -> Optional[bpy.types.Mesh]:
    if scene is None or curve_data is None:
        return None
    tmp = bpy.data.objects.new("__bname_balloon_curve_to_mesh__", curve_data)
    try:
        scene.collection.objects.link(tmp)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve mesh temp link failed")
        return None
    mesh = None
    try:
        view_layer = bpy.context.view_layer
        if view_layer is not None:
            view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = tmp.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
        mesh.name = mesh_name
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve to mesh failed")
        mesh = None
    finally:
        try:
            bpy.data.objects.remove(tmp, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    return mesh


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


def _ensure_balloon_curve_data(
    name: str,
    points_mm: Sequence[tuple[float, float]],
    tail_polygons_mm: Sequence[Sequence[tuple[float, float]]] | None = None,
    *,
    fill: bool = False,
    corner_indices: Sequence[int] | None = None,
) -> bpy.types.Curve:
    """点列 (mm) から Bezier Curve データブロックを ensure (再構築)."""
    curve = bpy.data.curves.get(name)
    if curve is None:
        curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "2D"
    # 既存スプライン全削除
    while len(curve.splines):
        try:
            curve.splines.remove(curve.splines[0])
        except Exception:  # noqa: BLE001
            break
    if not points_mm or len(points_mm) < 3:
        return curve
    _append_bezier_loop(curve, points_mm, corner_indices)
    for tail_points in tail_polygons_mm or ():
        if len(tail_points) >= 3:
            _append_poly_loop(curve, tail_points)
    try:
        curve.fill_mode = "BOTH" if fill else "NONE"
    except Exception:  # noqa: BLE001
        pass
    # 線幅 (ベベル) は呼出側で設定。data 側のデフォルトは 0。
    return curve


def _append_bezier_loop(
    curve: bpy.types.Curve,
    points_mm: Sequence[tuple[float, float]],
    corner_indices: Sequence[int] | None = None,
) -> None:
    spline = curve.splines.new(type="BEZIER")
    spline.bezier_points.add(len(points_mm) - 1)
    corners = {int(i) for i in (corner_indices or ())}
    for i, (x_mm, y_mm) in enumerate(points_mm):
        bp = spline.bezier_points[i]
        bp.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0)
        if i in corners:
            # 雲の谷・トゲ先端など本来鋭角の頂点は VECTOR で角を残す
            bp.handle_left_type = "VECTOR"
            bp.handle_right_type = "VECTOR"
        else:
            # それ以外は AUTO にして自然な曲線
            bp.handle_left_type = "AUTO"
            bp.handle_right_type = "AUTO"
    spline.use_cyclic_u = True


def _append_poly_loop(
    curve: bpy.types.Curve,
    points_mm: Sequence[tuple[float, float]],
) -> None:
    spline = curve.splines.new(type="POLY")
    spline.points.add(len(points_mm) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, points_mm, strict=False):
        point.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0, 1.0)
    spline.use_cyclic_u = True


def _append_polyline(
    curve: bpy.types.Curve,
    points_mm: Sequence[tuple[float, float]],
) -> None:
    if len(points_mm) < 2:
        return
    spline = curve.splines.new(type="POLY")
    spline.points.add(len(points_mm) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, points_mm, strict=False):
        point.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0, 1.0)
    spline.use_cyclic_u = False


def _ensure_balloon_line_curve_data(
    name: str,
    segments_mm: Sequence[tuple[tuple[float, float], tuple[float, float]]],
    tail_polygons_mm: Sequence[Sequence[tuple[float, float]]] | None = None,
) -> bpy.types.Curve:
    curve = bpy.data.curves.get(name)
    if curve is None:
        curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "2D"
    while len(curve.splines):
        try:
            curve.splines.remove(curve.splines[0])
        except Exception:  # noqa: BLE001
            break
    for start, end in segments_mm:
        _append_polyline(curve, (start, end))
    for tail_points in tail_polygons_mm or ():
        if len(tail_points) >= 3:
            _append_poly_loop(curve, tail_points)
    try:
        curve.fill_mode = "NONE"
    except Exception:  # noqa: BLE001
        pass
    return curve


def _ensure_uni_flash_line_mesh_data(
    name: str,
    segments_mm: Sequence[tuple[tuple[float, float], tuple[float, float]]],
    entry,
) -> bpy.types.Mesh:
    old = bpy.data.meshes.get(name)
    if old is not None and getattr(old, "users", 0) == 0:
        try:
            bpy.data.meshes.remove(old)
        except Exception:  # noqa: BLE001
            pass
    mesh = bpy.data.meshes.new(name)
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    start_factor, end_factor = balloon_uni_flash.line_width_factors(entry)
    start_half_m = mm_to_m(line_width_mm * start_factor * 0.5)
    end_half_m = mm_to_m(line_width_mm * end_factor * 0.5)
    eps = 1.0e-9
    verts: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    for start, end in segments_mm:
        sx, sy = start
        ex, ey = end
        dx = float(ex) - float(sx)
        dy = float(ey) - float(sy)
        length = math.hypot(dx, dy)
        if length <= 1.0e-9:
            continue
        nx = -dy / length
        ny = dx / length
        sx_m = mm_to_m(float(sx))
        sy_m = mm_to_m(float(sy))
        ex_m = mm_to_m(float(ex))
        ey_m = mm_to_m(float(ey))
        base = len(verts)
        if start_half_m <= eps and end_half_m <= eps:
            continue
        if start_half_m <= eps:
            verts.extend(
                [
                    (sx_m, sy_m, 0.0),
                    (ex_m + nx * end_half_m, ey_m + ny * end_half_m, 0.0),
                    (ex_m - nx * end_half_m, ey_m - ny * end_half_m, 0.0),
                ]
            )
            faces.append([base, base + 1, base + 2])
        elif end_half_m <= eps:
            verts.extend(
                [
                    (sx_m + nx * start_half_m, sy_m + ny * start_half_m, 0.0),
                    (sx_m - nx * start_half_m, sy_m - ny * start_half_m, 0.0),
                    (ex_m, ey_m, 0.0),
                ]
            )
            faces.append([base, base + 1, base + 2])
        else:
            verts.extend(
                [
                    (sx_m + nx * start_half_m, sy_m + ny * start_half_m, 0.0),
                    (sx_m - nx * start_half_m, sy_m - ny * start_half_m, 0.0),
                    (ex_m - nx * end_half_m, ey_m - ny * end_half_m, 0.0),
                    (ex_m + nx * end_half_m, ey_m + ny * end_half_m, 0.0),
                ]
            )
            faces.append([base, base + 1, base + 2, base + 3])
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _entry_line_rgba(entry) -> tuple[float, float, float, float]:
    color = getattr(entry, "line_color", (0.0, 0.0, 0.0, 1.0))
    opacity = max(0.0, min(1.0, float(getattr(entry, "opacity", 1.0) or 0.0)))
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
    opacity = max(0.0, min(1.0, float(getattr(entry, "opacity", 1.0) or 0.0)))
    fill_opacity = max(0.0, min(1.0, float(getattr(entry, "fill_opacity", 1.0) or 0.0)))
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


def _outline_points_for_entry(entry) -> tuple[list[tuple[float, float]], list[int]]:
    """entry から輪郭点列 (mm, ローカル左下 origin) と鋭角頂点 index を取得."""
    width = float(getattr(entry, "width_mm", 40.0) or 40.0)
    height = float(getattr(entry, "height_mm", 20.0) or 20.0)
    rect = bs.Rect(0.0, 0.0, width, height)
    corners: list[int] = []
    try:
        pts, corners = bs.outline_with_corners_for_entry(entry, rect)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon outline_for_entry failed")
        pts = []
    if not pts or len(pts) < 3:
        # フォールバック: 矩形
        pts = [
            (0.0, 0.0),
            (width, 0.0),
            (width, height),
            (0.0, height),
        ]
        corners = [0, 1, 2, 3]
    return pts, corners


def _uni_flash_geometry_for_entry(entry) -> balloon_uni_flash.UniFlashGeometry:
    width = float(getattr(entry, "width_mm", 40.0) or 40.0)
    height = float(getattr(entry, "height_mm", 20.0) or 20.0)
    return balloon_uni_flash.geometry_for_entry(
        entry,
        bs.Rect(0.0, 0.0, width, height),
    )


def _tail_polygon_for_entry(entry, tail) -> list[tuple[float, float]]:
    width = max(0.1, float(getattr(entry, "width_mm", 40.0) or 40.0))
    height = max(0.1, float(getattr(entry, "height_mm", 20.0) or 20.0))
    return balloon_tail_geom.polygon_for_tail(bs.Rect(0.0, 0.0, width, height), tail)


def _tail_polygons_for_entry(entry) -> list[list[tuple[float, float]]]:
    polygons: list[list[tuple[float, float]]] = []
    for tail in getattr(entry, "tails", []) or []:
        pts = _tail_polygon_for_entry(entry, tail)
        if len(pts) >= 3:
            polygons.append(pts)
    return polygons


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


def _ensure_balloon_fill_mesh_data(
    *,
    scene: bpy.types.Scene,
    entry,
    points_mm: Sequence[tuple[float, float]],
    tail_polygons_mm: Sequence[Sequence[tuple[float, float]]],
    corner_indices: Sequence[int] | None = None,
) -> tuple[Optional[bpy.types.Mesh], Optional[bpy.types.Material]]:
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None, None
    fill_data = _ensure_balloon_curve_data(
        f"{BALLOON_FILL_DATA_PREFIX}{balloon_id}",
        points_mm,
        tail_polygons_mm,
        fill=True,
        corner_indices=corner_indices,
    )
    try:
        fill_data.bevel_depth = 0.0
    except Exception:  # noqa: BLE001
        pass
    mat = _ensure_fill_material(f"{BALLOON_FILL_MATERIAL_PREFIX}{balloon_id}", entry)
    if not fill_data.materials:
        fill_data.materials.append(mat)
    elif fill_data.materials[0] is not mat:
        fill_data.materials[0] = mat
    fill_mesh = _curve_to_mesh_data(
        scene=scene,
        curve_data=fill_data,
        mesh_name=f"{BALLOON_FILL_MESH_DATA_PREFIX}{balloon_id}",
    )
    _remove_unused_data_block(fill_data)
    return fill_mesh, mat


def _append_mesh_faces(
    *,
    source: bpy.types.Mesh,
    verts: list[tuple[float, float, float]],
    faces: list[list[int]],
    material_indices: list[int],
    material_index: int,
    z_offset: float = 0.0,
) -> None:
    base = len(verts)
    for vertex in getattr(source, "vertices", []) or []:
        co = vertex.co
        verts.append((float(co.x), float(co.y), float(co.z) + z_offset))
    for poly in getattr(source, "polygons", []) or []:
        faces.append([base + int(i) for i in poly.vertices])
        material_indices.append(material_index)


def _combine_balloon_mesh_data(
    *,
    mesh_name: str,
    line_mesh: bpy.types.Mesh,
    fill_mesh: Optional[bpy.types.Mesh],
    line_material: bpy.types.Material,
    fill_material: Optional[bpy.types.Material],
) -> bpy.types.Mesh:
    verts: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    material_indices: list[int] = []
    if fill_mesh is not None:
        _append_mesh_faces(
            source=fill_mesh,
            verts=verts,
            faces=faces,
            material_indices=material_indices,
            material_index=1,
            z_offset=FILL_LOCAL_Z_OFFSET_M,
        )
    _append_mesh_faces(
        source=line_mesh,
        verts=verts,
        faces=faces,
        material_indices=material_indices,
        material_index=0,
    )
    mesh = bpy.data.meshes.new(mesh_name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.materials.append(line_material)
    if fill_material is not None:
        mesh.materials.append(fill_material)
    for poly, material_index in zip(mesh.polygons, material_indices, strict=False):
        poly.material_index = material_index
    return mesh


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

    # 1. Curve データ生成
    is_uni_flash = balloon_uni_flash.is_uni_flash_entry(entry)
    line_segments_mm: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if is_uni_flash:
        geometry = _uni_flash_geometry_for_entry(entry)
        points_mm = geometry.fill_outline_mm
        corner_indices = list(range(len(points_mm)))
        line_segments_mm = geometry.line_segments_mm
    else:
        points_mm, corner_indices = _outline_points_for_entry(entry)
    tail_polygons_mm = _tail_polygons_for_entry(entry)
    curve_data_name = f"{BALLOON_CURVE_DATA_PREFIX}{balloon_id}"
    if is_uni_flash:
        curve_data = None
    else:
        curve_data = _ensure_balloon_curve_data(
            curve_data_name,
            points_mm,
            tail_polygons_mm,
            corner_indices=corner_indices,
        )
    line_mat = _ensure_balloon_curve_material(
        curve_data,
        material_name=f"{BALLOON_CURVE_MATERIAL_PREFIX}{balloon_id}",
        entry=entry,
    )
    # ベベルでフキダシの線幅を再現 (entry.line_width_mm)
    line_width_mm = float(getattr(entry, "line_width_mm", 0.3) or 0.3)
    if is_uni_flash:
        mesh_data = _ensure_uni_flash_line_mesh_data(
            f"{BALLOON_MESH_DATA_PREFIX}{balloon_id}_lines",
            line_segments_mm,
            entry,
        )
    else:
        try:
            curve_data.bevel_depth = mm_to_m(line_width_mm) * 0.5
            curve_data.bevel_resolution = 0
        except Exception:  # noqa: BLE001
            pass
        mesh_data = _curve_to_mesh_data(
            scene=scene,
            curve_data=curve_data,
            mesh_name=f"{BALLOON_MESH_DATA_PREFIX}{balloon_id}",
        )
    if mesh_data is None:
        return None
    fill_mesh, fill_mat = _ensure_balloon_fill_mesh_data(
        scene=scene,
        entry=entry,
        points_mm=points_mm,
        tail_polygons_mm=tail_polygons_mm,
        corner_indices=corner_indices,
    )
    combined_mesh = _combine_balloon_mesh_data(
        mesh_name=f"{BALLOON_MESH_DATA_PREFIX}{balloon_id}",
        line_mesh=mesh_data,
        fill_mesh=fill_mesh,
        line_material=line_mat,
        fill_material=fill_mat,
    )
    _remove_unused_data_block(mesh_data)
    _remove_unused_data_block(fill_mesh)
    source_obj = _ensure_balloon_source_object(
        scene=scene,
        balloon_id=balloon_id,
        mesh=combined_mesh,
    )

    # 2. Mesh Object 生成 or 再利用
    obj_name = f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}"
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    carrier_mesh = _ensure_balloon_carrier_mesh(balloon_id, line_mat, fill_mat)
    obj = _replace_object_with_mesh(obj=obj, obj_name=obj_name, mesh=carrier_mesh)
    if curve_data is not None:
        _remove_unused_data_block(curve_data)
    _remove_duplicate_balloon_objects(balloon_id, obj)
    _remove_legacy_balloon_fill_objects(balloon_id)

    # 3. ページローカル座標 mm → m
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))

    # 4. parent 解決 (balloon_text_plane と同方針)
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

    # 5. z_index は page.balloons 配列 index に基づく (kind 別 base offset)
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
            "uni_flash" if is_uni_flash else "balloon",
            _gn.balloon_values(entry, uni_flash=is_uni_flash) | {"参照形状": source_obj},
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


def _ensure_balloon_source_object(
    *,
    scene: bpy.types.Scene,
    balloon_id: str,
    mesh: bpy.types.Mesh,
) -> bpy.types.Object:
    obj_name = f"{BALLOON_SOURCE_NAME_PREFIX}{balloon_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and obj.type != "MESH":
        _remove_balloon_object(obj)
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        old_data = getattr(obj, "data", None)
        obj.data = mesh
        _remove_unused_data_block(old_data)
    obj[PROP_BALLOON_SOURCE_KIND] = "geometry_source"
    obj[PROP_BALLOON_SOURCE_OWNER_ID] = balloon_id
    obj.hide_select = True
    obj.hide_viewport = True
    obj.hide_render = True
    obj.location = (0.0, 0.0, 0.0)
    if scene is not None and not obj.users_collection:
        scene.collection.objects.link(obj)
    try:
        obj.hide_set(True)
    except Exception:  # noqa: BLE001
        pass
    return obj


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
        return ensure_balloon_curve_object(
            scene=scene,
            entry=candidate,
            page=None,
        ) is not None
    return False
