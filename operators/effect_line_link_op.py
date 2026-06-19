"""リンク効果線の作成と連動伝播."""

from __future__ import annotations

import copy
import uuid

import bpy
from bpy.types import Operator

from ..utils import (
    free_transform,
    gp_layer_parenting as gp_parent,
    layer_hierarchy,
    layer_links,
    layer_stack as layer_stack_utils,
    log,
    object_naming as on,
    object_selection,
)

_logger = log.get_logger(__name__)

LINK_ID_PROP = "link_id"

_LINKED_SHAPE_FIELDS = {
    "rotation_deg",
    "start_shape",
    "start_to_coma_frame",
    "start_rounded_corner_enabled",
    "start_rounded_corner_radius_mm",
    "start_rounded_corner_radius_unit",
    "start_rounded_corner_radius_percent",
    "start_cloud_bump_width_mm",
    "start_cloud_bump_width_jitter",
    "start_cloud_bump_height_mm",
    "start_cloud_bump_height_jitter",
    "start_cloud_offset_percent",
    "start_cloud_sub_width_ratio",
    "start_cloud_sub_width_jitter",
    "start_cloud_sub_height_ratio",
    "start_cloud_sub_height_jitter",
    "end_shape",
    "end_rounded_corner_enabled",
    "end_rounded_corner_radius_mm",
    "end_rounded_corner_radius_unit",
    "end_rounded_corner_radius_percent",
    "end_cloud_bump_width_mm",
    "end_cloud_bump_width_jitter",
    "end_cloud_bump_height_mm",
    "end_cloud_bump_height_jitter",
    "end_cloud_offset_percent",
    "end_cloud_sub_width_ratio",
    "end_cloud_sub_width_jitter",
    "end_cloud_sub_height_ratio",
    "end_cloud_sub_height_jitter",
    "spacing_density_compensation",
    "speed_angle_deg",
    "white_outline_count",
    "white_outline_spacing_mm",
    "white_outline_white_line_count_auto",
    "white_outline_white_line_count",
    "white_outline_width_mm",
    "white_outline_width_jitter_enabled",
    "white_outline_width_min_percent",
    "white_outline_length_jitter_enabled",
    "white_outline_length_min_percent",
    "white_outline_white_ratio_percent",
    "white_outline_white_brush_mm",
    "white_outline_white_attenuation",
    "white_outline_white_in_percent",
    "white_outline_white_out_percent",
    "white_outline_white_inout_range_mode",
    "white_outline_white_in_range_percent",
    "white_outline_white_out_range_percent",
    "white_outline_white_in_range_mm",
    "white_outline_white_out_range_mm",
    "white_outline_black_line_count_auto",
    "white_outline_black_line_count",
    "white_outline_black_direction",
    "white_outline_black_brush_mm",
    "white_outline_black_spacing_mm",
    "white_outline_black_width_scale_percent",
    "white_outline_black_length_scale_near_percent",
    "white_outline_black_length_scale_far_percent",
    "white_outline_black_attenuation",
}


def _effect_link_id(effect_op, obj, layer) -> str:
    entry = effect_op._effect_meta(obj).get(effect_op._layer_meta_key(layer), {})
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(LINK_ID_PROP, "") or "")


def _set_effect_link_id(effect_op, obj, layer, link_id: str) -> None:
    if obj is None or layer is None:
        return
    key = effect_op._layer_meta_key(layer)
    if not key:
        return
    meta = effect_op._effect_meta(obj)
    entry = meta.get(key, {}) if isinstance(meta.get(key, {}), dict) else {}
    entry = dict(entry)
    if link_id:
        entry[LINK_ID_PROP] = str(link_id)
    else:
        entry.pop(LINK_ID_PROP, None)
    meta[key] = entry
    effect_op._write_effect_meta(obj, meta)


def _ensure_effect_link_pair(effect_op, source_obj, source_layer, dest_obj, dest_layer) -> str:
    link_id = _effect_link_id(effect_op, source_obj, source_layer)
    if not link_id:
        link_id = uuid.uuid4().hex
        _set_effect_link_id(effect_op, source_obj, source_layer, link_id)
    _set_effect_link_id(effect_op, dest_obj, dest_layer, link_id)
    return link_id


def _copy_linked_shape_params(source_params: dict, dest_params: dict) -> dict:
    out = dict(dest_params or {})
    for field in _LINKED_SHAPE_FIELDS:
        if field in source_params:
            out[field] = source_params[field]
    # 「ズラし量」はリンクで連動させない。保存データに値が無い旧レイヤーでも、
    # 共有の作業用パラメータ (編集元の値) へフォールバックしないよう既定値で埋める
    out.setdefault("uni_flash_offset_percent", 50.0)
    return out


def _copy_linked_free_transform(source_entry: dict, dest_entry: dict) -> dict:
    out = dict(dest_entry or {})
    key = free_transform.EFFECT_META_KEY
    if key in source_entry:
        out[key] = copy.deepcopy(source_entry.get(key))
    else:
        out.pop(key, None)
    return out


def _params_proxy_from_data(effect_op, context, data: dict):
    scene_params = getattr(context.scene, "bmanga_effect_line_params", None)
    if scene_params is None:
        return None
    return effect_op._EffectParamProxy(scene_params, data)


def _iter_effect_objects():
    for obj in bpy.data.objects:
        if getattr(obj, "type", "") != "GREASEPENCIL":
            continue
        if str(obj.get(on.PROP_KIND, "") or "") == "effect":
            yield obj


def _linked_effect_targets(effect_op, source_obj, source_layer, link_id: str):
    source_key = effect_op._layer_meta_key(source_layer)
    for obj in _iter_effect_objects():
        meta = effect_op._effect_meta(obj)
        layers = getattr(getattr(obj, "data", None), "layers", None)
        for key, entry in list(meta.items()):
            if obj == source_obj and key == source_key:
                continue
            if not isinstance(entry, dict):
                continue
            if str(entry.get(LINK_ID_PROP, "") or "") != link_id:
                continue
            peer_layer = layer_stack_utils._find_gp_layer_by_key(layers, key)
            if peer_layer is not None:
                yield obj, peer_layer, entry


def propagate_linked_effect_strokes(
    context,
    obj,
    source_layer,
    bounds: tuple[float, float, float, float],
    source_params_data: dict,
    center_xy_mm: tuple[float, float] | None = None,
) -> None:
    from . import effect_line_op

    link_id = _effect_link_id(effect_line_op, obj, source_layer)
    if not link_id:
        return
    meta = effect_line_op._effect_meta(obj)
    source_key = effect_line_op._layer_meta_key(source_layer)
    source_entry = meta.get(source_key, {}) if isinstance(meta.get(source_key, {}), dict) else {}
    for peer_obj, peer_layer, entry in _linked_effect_targets(effect_line_op, obj, source_layer, link_id):
        peer_params = _copy_linked_shape_params(
            source_params_data,
            effect_line_op._layer_params_data(peer_obj, peer_layer),
        )
        peer_meta = effect_line_op._effect_meta(peer_obj)
        peer_key = effect_line_op._layer_meta_key(peer_layer)
        peer_meta[peer_key] = _copy_linked_free_transform(source_entry, entry)
        effect_line_op._write_effect_meta(peer_obj, peer_meta)
        params_proxy = _params_proxy_from_data(effect_line_op, context, peer_params)
        if params_proxy is None:
            continue
        effect_line_op._write_effect_strokes(
            context,
            peer_obj,
            peer_layer,
            bounds,
            seed=effect_line_op._seed_for_layer(peer_obj, peer_layer),
            params_override=params_proxy,
            propagate_link=False,
            center_xy_mm=center_xy_mm,
        )


def _copy_effect_meta_between_objects(effect_op, source_obj, source_layer, dest_obj, dest_layer, *, include_link: bool) -> dict:
    source_meta = effect_op._effect_meta(source_obj)
    source_key = effect_op._layer_meta_key(source_layer)
    source_entry = source_meta.get(source_key)
    if not isinstance(source_entry, dict):
        return {}
    copied = copy.deepcopy(source_entry)
    if not include_link:
        copied.pop(LINK_ID_PROP, None)
    dest_meta = effect_op._effect_meta(dest_obj)
    dest_meta[effect_op._layer_meta_key(dest_layer)] = copied
    effect_op._write_effect_meta(dest_obj, dest_meta)
    return copied


def _effect_layer_uid(layer) -> str:
    return layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(layer))


def _duplicate_parent_key(source_obj, source_layer, ui_parent_key: str = "") -> str:
    parent_key = gp_parent.parent_key(source_layer)
    if not parent_key and source_obj is not None:
        parent_key = str(source_obj.get(on.PROP_PARENT_KEY, "") or "")
    if not parent_key and str(ui_parent_key or "") == layer_hierarchy.OUTSIDE_STACK_KEY:
        return layer_hierarchy.OUTSIDE_STACK_KEY
    return str(parent_key or "")


def duplicate_effect_entry(
    context,
    source_obj,
    source_layer,
    *,
    linked: bool = False,
    ui_parent_key: str = "",
):
    from . import effect_line_op

    bounds = effect_line_op.effect_layer_bounds(source_obj, source_layer)
    if source_obj is None or source_layer is None or bounds is None:
        return None, None
    dest_obj, dest_layer = effect_line_op._create_effect_layer(
        context,
        bounds,
        parent_key=_duplicate_parent_key(source_obj, source_layer, ui_parent_key),
    )
    if dest_obj is None or dest_layer is None:
        return None, None
    copied = _copy_effect_meta_between_objects(
        effect_line_op,
        source_obj,
        source_layer,
        dest_obj,
        dest_layer,
        include_link=False,
    )
    if linked:
        _ensure_effect_link_pair(effect_line_op, source_obj, source_layer, dest_obj, dest_layer)
        layer_links.link_uids(context, [_effect_layer_uid(source_layer), _effect_layer_uid(dest_layer)])
        copied = effect_line_op._effect_meta(dest_obj).get(effect_line_op._layer_meta_key(dest_layer), copied)
        if not isinstance(copied, dict):
            copied = {}
    try:
        center_xy = (float(copied["center_x"]), float(copied["center_y"]))
    except Exception:  # noqa: BLE001
        center_xy = effect_line_op.effect_layer_center(source_obj, source_layer, bounds)
    params_data = copied.get("params", {}) if isinstance(copied, dict) else {}
    params_proxy = _params_proxy_from_data(effect_line_op, context, params_data)
    effect_line_op._write_effect_strokes(
        context,
        dest_obj,
        dest_layer,
        bounds,
        params_override=params_proxy,
        center_xy_mm=center_xy,
    )
    effect_line_op._select_effect_layer(context, dest_obj, dest_layer)
    object_selection.select_key(
        context,
        object_selection.effect_key(dest_layer),
        mode="single",
    )
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return dest_obj, dest_layer


def link_existing_effect_layers(context, effect_layers: list[tuple[object, object]]) -> int:
    from . import effect_line_op

    valid = [
        (obj, layer)
        for obj, layer in effect_layers
        if obj is not None and layer is not None
    ]
    if len(valid) < 2:
        return 0
    source_obj, source_layer = valid[0]
    for dest_obj, dest_layer in valid[1:]:
        _ensure_effect_link_pair(effect_line_op, source_obj, source_layer, dest_obj, dest_layer)
    bounds = effect_line_op.effect_layer_bounds(source_obj, source_layer)
    if bounds is not None:
        effect_line_op._write_effect_strokes(
            context,
            source_obj,
            source_layer,
            bounds,
            center_xy_mm=effect_line_op.effect_layer_center(source_obj, source_layer, bounds),
        )
    return len(valid)


class BMANGA_OT_effect_line_create_linked(Operator):
    bl_idname = "bmanga.effect_line_create_linked"
    bl_label = "リンク効果線を作成"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if getattr(getattr(context, "scene", None), "bmanga_active_layer_kind", "") != "effect":
            return False
        from . import effect_line_op

        obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
        return obj is not None and layer is not None and bounds is not None

    def execute(self, context):
        from . import effect_line_op

        obj, source_layer, bounds = effect_line_op.active_effect_layer_bounds(context)
        if obj is None or source_layer is None or bounds is None:
            self.report({"ERROR"}, "リンク元の効果線が選択されていません")
            return {"CANCELLED"}
        _dest_obj, linked_layer = duplicate_effect_entry(
            context,
            obj,
            source_layer,
            linked=True,
        )
        if linked_layer is None:
            self.report({"ERROR"}, "複製された効果線を取得できません")
            return {"CANCELLED"}
        self.report({"INFO"}, "リンク効果線を作成しました")
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_effect_line_create_linked,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
