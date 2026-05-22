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

_logger = log.get_logger(__name__)

PER_LAYER_EFFECT_DATA_PREFIX = "BName_EffectGP_"
EFFECT_DISPLAY_DATA_PREFIX = "BName_EffectDisplay_"
EFFECT_DISPLAY_ID_PREFIX = "effect_display_"
EFFECT_DISPLAY_KIND = "effect_display"
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


def delete_effect_display_object(controller_obj: bpy.types.Object | None) -> None:
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


def _ensure_display_material(
    display: bpy.types.Object,
    color=(0.0, 0.0, 0.0, 1.0),
    *,
    opacity: float = 1.0,
) -> None:
    display_id = str(display.get(on.PROP_ID, "") or display.name)
    mat_name = f"BName_Effect_Display_Line_{display_id}"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
    try:
        alpha = max(0.0, min(1.0, float(color[3]) * float(opacity)))
        rgba = (float(color[0]), float(color[1]), float(color[2]), alpha)
    except Exception:  # noqa: BLE001
        rgba = (0.0, 0.0, 0.0, max(0.0, min(1.0, float(opacity or 0.0))))
    mat.diffuse_color = rgba
    try:
        mat.use_nodes = False
        mat.blend_method = "BLEND" if rgba[3] < 1.0 else "OPAQUE"
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
    _ensure_display_material(display, line_color, opacity=line_opacity)
    try:
        from . import geometry_nodes_bridge as _gn

        _gn.ensure_modifier(display, "effect_line", values or {})
    except Exception:  # noqa: BLE001
        _logger.exception("effect display Geometry Nodes sync failed")
    return display


def sync_effect_display_transform(controller_obj: bpy.types.Object | None) -> None:
    display = find_effect_display_object(controller_obj)
    if display is None or controller_obj is None:
        return
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
