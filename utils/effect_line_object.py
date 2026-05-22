"""効果線 GP Object ヘルパ.

新規効果線 GP Object を生成し、Outliner mirror に登録する。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import gpencil as gp_utils
from . import layer_object_sync as los
from . import log
from . import object_naming as on
from .geom import mm_to_m

_logger = log.get_logger(__name__)

PER_LAYER_EFFECT_DATA_PREFIX = "BName_EffectGP_"
EFFECT_DISPLAY_DATA_PREFIX = "BName_EffectDisplay_"
EFFECT_DISPLAY_ID_PREFIX = "effect_display_"
EFFECT_DISPLAY_KIND = "effect_display"
EFFECT_FRAME_SOURCE_DATA_PREFIX = "BName_EffectFrameSource_"
EFFECT_FRAME_SOURCE_ID_PREFIX = "effect_frame_source_"
EFFECT_FRAME_SOURCE_KIND = "effect_frame_source"
PROP_EFFECT_TARGET = "bname_effect_target"
PROP_EFFECT_CONTROLLER_ID = "bname_effect_controller_id"


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
        if str(obj.get(PROP_EFFECT_CONTROLLER_ID, "") or "") == controller_id and str(obj.get(on.PROP_KIND, "") or "") == EFFECT_FRAME_SOURCE_KIND:
            return obj
    return None


def delete_effect_frame_source_object(controller_obj: bpy.types.Object | None) -> None:
    obj = find_effect_frame_source_object(controller_obj)
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


def delete_effect_display_object(controller_obj: bpy.types.Object | None) -> None:
    delete_effect_frame_source_object(controller_obj)
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


def ensure_effect_frame_source_object(
    *,
    scene: bpy.types.Scene,
    controller_obj: bpy.types.Object,
    outline_mm,
) -> Optional[bpy.types.Object]:
    if scene is None or controller_obj is None or not outline_mm:
        delete_effect_frame_source_object(controller_obj)
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


def _ensure_display_material(
    display: bpy.types.Object,
    color=(0.0, 0.0, 0.0, 1.0),
    *,
    opacity: float = 1.0,
    fill_color=(1.0, 1.0, 1.0, 1.0),
    fill_opacity: float = 1.0,
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
    mat.diffuse_color = rgba
    fill_mat.diffuse_color = fill_rgba
    try:
        mat.use_nodes = False
        mat.blend_method = "BLEND" if rgba[3] < 1.0 else "OPAQUE"
        fill_mat.use_nodes = False
        fill_mat.blend_method = "BLEND" if fill_rgba[3] < 1.0 else "OPAQUE"
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


def ensure_effect_display_object(
    *,
    scene: bpy.types.Scene,
    controller_obj: bpy.types.Object,
    values: dict | None = None,
) -> Optional[bpy.types.Object]:
    """効果線を実際に表示する Mesh Object を Geometry Nodes で同期する。

    制御用の Grease Pencil に直接 Geometry Nodes を載せると Blender 5.1 では
    表示結果が空になるため、選択・メタデータは既存レイヤーに残し、画面表示は
    この Mesh Object に任せる。
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
    line_color = (values or {}).get("線色", (0.0, 0.0, 0.0, 1.0))
    line_opacity = float((values or {}).get("不透明度", 1.0) or 0.0)
    fill_color = (values or {}).get("塗り色", (1.0, 1.0, 1.0, 1.0))
    fill_opacity = float((values or {}).get("塗り不透明度", 1.0) or 0.0)
    _ensure_display_material(
        display,
        line_color,
        opacity=line_opacity,
        fill_color=fill_color,
        fill_opacity=fill_opacity,
    )
    try:
        from . import geometry_nodes_bridge as _gn

        _gn.ensure_modifier(display, "effect_line", values or {})
    except Exception:  # noqa: BLE001
        _logger.exception("effect display Geometry Nodes sync failed")
    return display


def sync_effect_display_transform(controller_obj: bpy.types.Object | None) -> None:
    if controller_obj is None:
        return
    display = find_effect_display_object(controller_obj)
    source = find_effect_frame_source_object(controller_obj)
    if display is not None:
        _link_display_to_controller_collections(display, controller_obj)
        try:
            display[on.PROP_PARENT_KEY] = str(controller_obj.get(on.PROP_PARENT_KEY, "") or "")
            display[on.PROP_FOLDER_ID] = str(controller_obj.get(on.PROP_FOLDER_ID, "") or "")
            display[on.PROP_Z_INDEX] = int(controller_obj.get(on.PROP_Z_INDEX, 0) or 0)
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
