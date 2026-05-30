"""フキダシ表示オブジェクトの同期ヘルパ."""

from __future__ import annotations

import json
import math
from contextlib import contextmanager
from typing import Optional, Sequence

import bpy

from . import balloon_curve_render_nodes
from . import balloon_curve_source_state
from . import balloon_fill_mesh
from . import balloon_line_mesh
from . import balloon_multiline_curve
from . import balloon_render_contract as render_contract
from . import balloon_shapes
from . import coma_content_mask
from . import free_transform
from . import layer_object_sync as los
from . import log
from . import material_opacity_mask
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
BALLOON_OUTER_EDGE_MATERIAL_PREFIX = "BName_Balloon_Outer_Edge_"
BALLOON_INNER_EDGE_MATERIAL_PREFIX = "BName_Balloon_Inner_Edge_"
PROP_BALLOON_FILL_KIND = "bname_balloon_fill_kind"
PROP_BALLOON_FILL_OWNER_ID = "bname_balloon_fill_owner_id"
PROP_BALLOON_FILL_SOURCE_MATERIAL = "bname_balloon_fill_source_material"
PROP_BALLOON_SOURCE_KIND = "bname_balloon_source_kind"
PROP_BALLOON_SOURCE_OWNER_ID = "bname_balloon_source_owner_id"
PROP_BALLOON_CLIP_MASK_KIND = "bname_balloon_clip_mask_kind"
PROP_BALLOON_CLIP_MASK_OWNER_ID = "bname_balloon_clip_mask_owner_id"
PROP_BALLOON_GEOMETRY_KEY = "bname_balloon_geometry_key"
PROP_BALLOON_CURVE_RESOLUTION_INITIALIZED = "bname_balloon_curve_resolution_initialized"
DEFAULT_BALLOON_CURVE_RESOLUTION_U = 64
CURVE_GEOMETRY_VERSION = 10
CLIPPED_FILL_ROLE_RADIUS = render_contract.CLIPPED_FILL_ROLE_RADIUS
_MATERIAL_SLOT_OUTER_EDGE = render_contract.MATERIAL_SLOT_OUTER_EDGE
_MATERIAL_SLOT_INNER_EDGE = render_contract.MATERIAL_SLOT_INNER_EDGE
_MATERIAL_SLOT_LINE = render_contract.MATERIAL_SLOT_LINE
_LINE_AND_EDGE_MASK_POWER = render_contract.LINE_AND_EDGE_MASK_POWER
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


def _entry_margin_rgba(entry, attr_name: str) -> tuple[float, float, float, float]:
    color = getattr(entry, attr_name, (1.0, 1.0, 1.0, 1.0))
    opacity = percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)
    try:
        return (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]) * opacity,
        )
    except Exception:  # noqa: BLE001
        return (1.0, 1.0, 1.0, opacity)


def _ensure_color_material(
    material_name: str,
    color: tuple[float, float, float, float],
    *,
    mask_info=None,
    mask_power: float = 1.0,
) -> bpy.types.Material:
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(name=material_name)
    try:
        mat.diffuse_color = color
        _setup_emission_alpha_material(mat, color, mask_info=mask_info, mask_power=mask_power)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon color material setup failed")
    return mat


def _ensure_balloon_curve_material(
    curve: Optional[bpy.types.Curve],
    *,
    material_name: str,
    entry=None,
    mask_info=None,
    mask_power: float = 1.0,
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
        _setup_emission_alpha_material(mat, line, mask_info=mask_info, mask_power=mask_power)
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


def _mat_socket_by_name(sockets, *names: str):
    for name in names:
        try:
            return sockets[name]
        except Exception:  # noqa: BLE001
            continue
    for socket in sockets:
        if getattr(socket, "name", "") in names:
            return socket
    return None


def _mat_link(nt, output_socket, input_socket) -> None:
    if output_socket is None or input_socket is None:
        return
    try:
        nt.links.new(output_socket, input_socket)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon material node link failed")


def _mat_value(nt, value: float, *, label: str, location: tuple[float, float]):
    node = nt.nodes.new("ShaderNodeValue")
    node.label = label
    node.location = location
    try:
        node.outputs[0].default_value = float(value)
    except Exception:  # noqa: BLE001
        pass
    return node.outputs[0]


def _mat_math(nt, operation: str, left_socket, right_socket_or_value, *, label: str, location: tuple[float, float]):
    node = nt.nodes.new("ShaderNodeMath")
    node.label = label
    node.location = location
    try:
        node.operation = operation
    except Exception:  # noqa: BLE001
        pass
    _mat_link(nt, left_socket, node.inputs[0])
    if hasattr(right_socket_or_value, "default_value"):
        _mat_link(nt, right_socket_or_value, node.inputs[1])
    else:
        try:
            node.inputs[1].default_value = float(right_socket_or_value)
        except Exception:  # noqa: BLE001
            pass
    return node.outputs["Value"]


def _mat_fill_blur_alpha_socket(nt, *, dither: bool, location: tuple[float, float]):
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.label = "塗り輪郭ぼかし"
    attr.location = location
    attr.attribute_name = balloon_curve_render_nodes.FILL_BLUR_ALPHA_ATTRIBUTE
    alpha = _mat_socket_by_name(attr.outputs, "Fac", "Alpha", "Value")
    if alpha is None:
        return None
    if not dither:
        return alpha
    try:
        noise = nt.nodes.new("ShaderNodeTexWhiteNoise")
    except Exception:  # noqa: BLE001
        return alpha
    noise.label = "塗りぼかしディザ"
    noise.location = (location[0], location[1] - 220)
    return _mat_math(
        nt,
        "GREATER_THAN",
        alpha,
        _mat_socket_by_name(noise.outputs, "Value"),
        label="塗りぼかしディザ判定",
        location=(location[0] + 230, location[1] - 120),
    )


def _setup_emission_alpha_material(
    mat: bpy.types.Material,
    color: tuple[float, float, float, float],
    *,
    gradient: tuple[tuple[float, float, float, float], tuple[float, float, float, float], float] | None = None,
    fill_blur_alpha: bool = False,
    fill_blur_dither: bool = False,
    mask_info=None,
    mask_power: float = 1.0,
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
    alpha_socket = _mat_value(nt, float(color[3]), label="塗り不透明度", location=(-580, -240))
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
        ramp_alpha = _mat_socket_by_name(ramp.outputs, "Alpha")
        if ramp_alpha is not None:
            alpha_socket = _mat_math(
                nt,
                "MULTIPLY",
                alpha_socket,
                ramp_alpha,
                label="グラデーション不透明度",
                location=(-340, -240),
            )
    if fill_blur_alpha:
        blur_alpha = _mat_fill_blur_alpha_socket(nt, dither=fill_blur_dither, location=(-580, -520))
        if blur_alpha is not None:
            alpha_socket = _mat_math(
                nt,
                "MULTIPLY",
                alpha_socket,
                blur_alpha,
                label="塗り輪郭ぼかし不透明度",
                location=(-120, -320),
            )
    if mask_info is not None:
        alpha_socket = material_opacity_mask.multiply_alpha_by_mask(
            nt,
            alpha_socket,
            mask_object=getattr(mask_info, "space_object", None),
            mask_image=getattr(mask_info, "image", None),
            location=(-820, -760),
            label="コマ内容マスク不透明度",
            power=mask_power,
        )
    nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    _mat_link(nt, alpha_socket, mix.inputs["Fac"])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    try:
        opaque_color = float(color[3]) >= 0.999 and not fill_blur_alpha
        mat.blend_method = "OPAQUE" if opaque_color and mask_info is None else "BLEND"
        if mask_info is not None:
            # 画像マスクがある場合は必ず BLENDED に統一する。
            # DITHERED はビューポート レンダー表示の低サンプリングだと
            # マスクのアルファ=0 領域でも斑模様で描画され「マスクが効かない」見え方
            # になるため、 画像マスクで切り抜くフキダシ系では使わない。
            mat.surface_render_method = "BLENDED"
        elif fill_blur_dither:
            mat.surface_render_method = "DITHERED"
        elif fill_blur_alpha or float(color[3]) < 0.999:
            mat.surface_render_method = "BLENDED"
        mat.show_transparent_back = True
    except (AttributeError, TypeError):
        pass


def _ensure_fill_material(material_name: str, entry=None, *, mask_info=None) -> bpy.types.Material:
    mat, copied_user_material = _fill_material_for_entry(material_name, entry)
    fill = _entry_fill_rgba(entry)
    _apply_fill_material_basics(mat, fill, entry)
    fill_blur_enabled = float(getattr(entry, "fill_blur_amount", 0.0) or 0.0) > 1.0e-6
    fill_blur_dither = bool(getattr(entry, "fill_blur_dither", False))
    if copied_user_material and mask_info is None and not bool(getattr(entry, "fill_gradient_enabled", False)) and not fill_blur_enabled:
        return mat
    try:
        if bool(getattr(entry, "fill_gradient_enabled", False)):
            start = tuple(float(v) for v in getattr(entry, "fill_gradient_start_color", fill))
            end = tuple(float(v) for v in getattr(entry, "fill_gradient_end_color", fill))
            _setup_emission_alpha_material(
                mat,
                fill,
                gradient=(start, end, float(getattr(entry, "fill_gradient_angle_deg", 90.0) or 90.0)),
                fill_blur_alpha=fill_blur_enabled,
                fill_blur_dither=fill_blur_dither,
                mask_info=mask_info,
            )
        else:
            _setup_emission_alpha_material(
                mat,
                fill,
                fill_blur_alpha=fill_blur_enabled,
                fill_blur_dither=fill_blur_dither,
                mask_info=mask_info,
            )
    except Exception:  # noqa: BLE001
        _logger.exception("balloon fill material setup failed")
    return mat


def _remove_balloon_object(obj: bpy.types.Object) -> None:
    # フキダシ本体の場合は、付随するメッシュバンド (主線・フチ・多重線) も同時に撤去する
    if obj.get(on.PROP_KIND) == "balloon":
        balloon_id = str(obj.get(on.PROP_ID, "") or "")
        if balloon_id:
            balloon_line_mesh.remove_all_balloon_band_meshes(balloon_id)
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


def _mm_to_curve_local(value_mm: float) -> float:
    return float(value_mm) * 0.001


def _curve_local_to_mm(value_m: float) -> float:
    return float(value_m) * 1000.0


def _transform_curve_vector_between_rects(
    vector,
    old_rect: tuple[float, float, float, float],
    new_rect: tuple[float, float, float, float],
):
    old_x, old_y, old_w, old_h = old_rect
    new_x, new_y, new_w, new_h = new_rect
    old_w = max(1.0e-9, float(old_w))
    old_h = max(1.0e-9, float(old_h))
    new_w = max(1.0e-9, float(new_w))
    new_h = max(1.0e-9, float(new_h))
    old_origin_x = float(old_x) + old_w * 0.5
    old_origin_y = float(old_y) + old_h * 0.5
    new_origin_x = float(new_x) + new_w * 0.5
    new_origin_y = float(new_y) + new_h * 0.5
    page_x = old_origin_x + _curve_local_to_mm(vector.x)
    page_y = old_origin_y + _curve_local_to_mm(vector.y)
    u = (page_x - float(old_x)) / old_w
    v = (page_y - float(old_y)) / old_h
    mapped_x = float(new_x) + u * new_w
    mapped_y = float(new_y) + v * new_h
    vector.x = _mm_to_curve_local(mapped_x - new_origin_x)
    vector.y = _mm_to_curve_local(mapped_y - new_origin_y)


def transform_manual_curve_to_rect(
    entry,
    old_rect: tuple[float, float, float, float],
    new_rect: tuple[float, float, float, float],
) -> bool:
    """手編集済み/自由形状のフキダシを、B-Nameのハンドル変形に追従させる."""
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return False
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is None or getattr(obj, "type", "") != "CURVE":
        return False
    state = balloon_curve_source_state.detect_state(obj)
    if state not in {balloon_curve_source_state.STATE_MANUAL, balloon_curve_source_state.STATE_FREEFORM}:
        return False
    try:
        for spline in getattr(obj.data, "splines", []) or []:
            if str(getattr(spline, "type", "") or "") == "BEZIER":
                for point in getattr(spline, "bezier_points", []) or []:
                    _transform_curve_vector_between_rects(point.co, old_rect, new_rect)
                    _transform_curve_vector_between_rects(point.handle_left, old_rect, new_rect)
                    _transform_curve_vector_between_rects(point.handle_right, old_rect, new_rect)
            else:
                for point in getattr(spline, "points", []) or []:
                    co = getattr(point, "co", None)
                    if co is None:
                        continue
                    _transform_curve_vector_between_rects(co, old_rect, new_rect)
        balloon_curve_source_state.mark_freeform(obj)
        _tag_curve_object_updated(obj)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: manual/freeform curve transform failed")
        return False


def _ensure_curve_object_for_entry(
    balloon_id: str,
    line_mat: bpy.types.Material,
    fill_mat: Optional[bpy.types.Material],
    outer_mat: Optional[bpy.types.Material] = None,
    inner_mat: Optional[bpy.types.Material] = None,
) -> bpy.types.Object:
    obj_name = f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}"
    obj = on.find_object_by_bname_id(balloon_id, kind="balloon")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    curve_data = _ensure_balloon_curve_data(balloon_id, line_mat, fill_mat, outer_mat, inner_mat)
    obj = _replace_object_with_curve(obj=obj, obj_name=obj_name, curve=curve_data)
    _prepare_balloon_curve_data(obj.data, line_mat, fill_mat, outer_mat, inner_mat)
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
    origin_x, origin_y = _entry_origin_xy(entry)
    obj.location.x = mm_to_m(origin_x)
    obj.location.y = mm_to_m(origin_y)
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
        origin_x, origin_y = _entry_origin_xy(entry)
        obj.location.x = _mm_to_m(origin_x + ox_mm)
        obj.location.y = _mm_to_m(origin_y + oy_mm)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: page world offset 加算失敗")


def _sync_balloon_band_meshes(scene, work, page, entry, obj: bpy.types.Object, mask_info) -> None:
    """フキダシ塗り面・主線・外側フチ・内側フチ・多重線の Mesh オブジェクトを ensure する."""
    materials = list(getattr(obj.data, "materials", []) or [])
    fill_mat = materials[render_contract.MATERIAL_SLOT_FILL] if len(materials) > render_contract.MATERIAL_SLOT_FILL else None
    line_mat = materials[_MATERIAL_SLOT_LINE] if len(materials) > _MATERIAL_SLOT_LINE else None
    outer_mat = materials[_MATERIAL_SLOT_OUTER_EDGE] if len(materials) > _MATERIAL_SLOT_OUTER_EDGE else None
    inner_mat = materials[_MATERIAL_SLOT_INNER_EDGE] if len(materials) > _MATERIAL_SLOT_INNER_EDGE else None
    balloon_id = str(getattr(entry, "id", "") or "")
    if fill_mat is not None:
        balloon_fill_mesh.ensure_balloon_fill_mesh(
            scene=scene,
            work=work,
            page=page,
            entry=entry,
            body_object=obj,
            fill_material=fill_mat,
        )
    else:
        balloon_fill_mesh.remove_balloon_fill_mesh(balloon_id)
    if line_mat is not None:
        balloon_line_mesh.ensure_balloon_line_mesh(
            scene=scene,
            work=work,
            page=page,
            entry=entry,
            body_object=obj,
            line_material=line_mat,
            mask_info=mask_info,
        )
    else:
        balloon_line_mesh.remove_balloon_line_mesh(balloon_id)
    if outer_mat is not None:
        balloon_line_mesh.ensure_balloon_outer_edge_mesh(
            scene=scene,
            work=work,
            page=page,
            entry=entry,
            body_object=obj,
            outer_edge_material=outer_mat,
            mask_info=mask_info,
        )
    else:
        balloon_line_mesh.remove_balloon_outer_edge_mesh(balloon_id)
    if inner_mat is not None:
        balloon_line_mesh.ensure_balloon_inner_edge_mesh(
            scene=scene,
            work=work,
            page=page,
            entry=entry,
            body_object=obj,
            inner_edge_material=inner_mat,
            mask_info=mask_info,
        )
    else:
        balloon_line_mesh.remove_balloon_inner_edge_mesh(balloon_id)
    if line_mat is not None:
        balloon_line_mesh.ensure_balloon_multi_line_mesh(
            scene=scene,
            work=work,
            page=page,
            entry=entry,
            body_object=obj,
            line_material=line_mat,
            mask_info=mask_info,
        )
    else:
        balloon_line_mesh.remove_balloon_multi_line_mesh(balloon_id)
    if line_mat is not None:
        balloon_line_mesh.ensure_balloon_tail_main_line_mesh(
            scene=scene,
            work=work,
            page=page,
            entry=entry,
            body_object=obj,
            line_material=line_mat,
            mask_info=mask_info,
        )
    else:
        balloon_line_mesh.remove_balloon_tail_main_line_mesh(balloon_id)


def _sync_balloon_render_modifier(entry, obj: bpy.types.Object) -> None:
    """旧 Geometry Nodes modifier の撤去 + 旧 clipped_fill spline の掃除."""
    balloon_id = str(getattr(entry, "id", "") or "")
    _remove_balloon_clip_mask(balloon_id)
    _discard_clipped_fill_geometry(obj)
    # Phase D 以降: 全描画責務を Python メッシュへ移行。旧 modifier が残って
    # いれば撤去する (新規 .blend では最初から作られない)。
    balloon_curve_render_nodes.ensure_modifier(obj)


def _sync_visibility_and_modifier(scene: bpy.types.Scene, work, page, entry, obj: bpy.types.Object) -> None:
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    try:
        if work is not None:
            los.assign_per_page_z_ranks(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: z order sync failed")
    mask_info = None
    try:
        mask_info = coma_content_mask.ensure_viewport_mask_for_entry(scene, work, page, entry)
        _sync_balloon_render_modifier(entry, obj)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: lightweight render node sync failed")
    try:
        _sync_balloon_band_meshes(scene, work, page, entry, obj, mask_info)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: line/edge/multi-line mesh sync failed")


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

    work = getattr(scene, "bname_work", None)
    mask_info = coma_content_mask.ensure_viewport_mask_for_entry(scene, work, page, entry)
    line_mat = _ensure_balloon_curve_material(
        None,
        material_name=f"{BALLOON_CURVE_MATERIAL_PREFIX}{balloon_id}",
        entry=entry,
        mask_info=mask_info,
        mask_power=_LINE_AND_EDGE_MASK_POWER,
    )
    fill_mat = _ensure_fill_material(f"{BALLOON_FILL_MATERIAL_PREFIX}{balloon_id}", entry, mask_info=mask_info)
    outer_mat = _ensure_color_material(
        f"{BALLOON_OUTER_EDGE_MATERIAL_PREFIX}{balloon_id}",
        _entry_margin_rgba(entry, "outer_white_margin_color"),
        mask_info=mask_info,
        mask_power=_LINE_AND_EDGE_MASK_POWER,
    )
    inner_mat = _ensure_color_material(
        f"{BALLOON_INNER_EDGE_MATERIAL_PREFIX}{balloon_id}",
        _entry_margin_rgba(entry, "inner_white_margin_color"),
        mask_info=mask_info,
        mask_power=_LINE_AND_EDGE_MASK_POWER,
    )

    obj = _ensure_curve_object_for_entry(balloon_id, line_mat, fill_mat, outer_mat, inner_mat)
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
    try:
        from . import balloon_merge_object

        balloon_merge_object.sync_groups_for_page(scene, work, page)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: merge display sync failed")
    return obj


def _spline_role_radius(spline) -> float | None:
    if getattr(spline, "type", "") == "BEZIER":
        points = getattr(spline, "bezier_points", []) or []
    else:
        points = getattr(spline, "points", []) or []
    if not points:
        return None
    return float(getattr(points[0], "radius", 1.0) or 0.0)


def _remove_clipped_fill_splines(curve: bpy.types.Curve) -> bool:
    """旧ジオメトリノード経路用の role 付きスプラインを削除する.

    Phase D 以降は role 付きスプラインを生成しないが、 旧 .blend の互換性のため、
    検出した非 BEZIER の POLY スプライン (= 外側フチ/内側フチ/見切れ塗り/多重線/
    主線フチ などのレガシー POLY スプライン) を全て削除する。
    ユーザーが BEZIER 制御点の radius を変更しても、その BEZIER スプラインは
    body/tail 由来のため残す。
    """
    changed = False
    for spline in reversed(list(getattr(curve, "splines", []) or [])):
        if str(getattr(spline, "type", "") or "") == "BEZIER":
            continue
        # POLY (= legacy role 付き帯スプライン)
        try:
            curve.splines.remove(spline)
            changed = True
        except Exception:  # noqa: BLE001
            pass
    return changed


def _discard_clipped_fill_geometry(obj: bpy.types.Object) -> None:
    curve = getattr(obj, "data", None)
    if curve is None:
        return
    was_generated = balloon_curve_source_state.detect_state(obj) == balloon_curve_source_state.STATE_GENERATED
    changed = _remove_clipped_fill_splines(curve)
    if changed:
        _tag_curve_object_updated(obj)
        if was_generated:
            balloon_curve_source_state.mark_generated(obj)


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
    outer_material: Optional[bpy.types.Material] = None,
    inner_material: Optional[bpy.types.Material] = None,
) -> bpy.types.Curve:
    curve_name = f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}_curve"
    curve = bpy.data.curves.get(curve_name)
    if curve is None:
        curve = bpy.data.curves.new(curve_name, "CURVE")
        curve.resolution_u = DEFAULT_BALLOON_CURVE_RESOLUTION_U
        curve.render_resolution_u = DEFAULT_BALLOON_CURVE_RESOLUTION_U
        curve[PROP_BALLOON_CURVE_RESOLUTION_INITIALIZED] = True
    _prepare_balloon_curve_data(curve, line_material, fill_material, outer_material, inner_material)
    return curve


def _prepare_balloon_curve_data(
    curve: bpy.types.Curve,
    line_material: bpy.types.Material,
    fill_material: Optional[bpy.types.Material],
    outer_material: Optional[bpy.types.Material] = None,
    inner_material: Optional[bpy.types.Material] = None,
) -> None:
    curve.dimensions = "2D"
    curve.bevel_depth = 0.0
    curve.bevel_resolution = 0
    try:
        curve.fill_mode = "NONE"
        curve.use_fill_caps = False
    except Exception:  # noqa: BLE001
        pass
    _set_data_materials(curve, (fill_material, outer_material, inner_material, line_material))


def _clear_curve_splines(curve: bpy.types.Curve) -> None:
    while len(curve.splines) > 0:
        curve.splines.remove(curve.splines[0])


def _entry_center_offset(entry) -> tuple[float, float]:
    return (
        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
    )


def _entry_curve_offset(entry) -> tuple[float, float]:
    return (
        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)) * 0.5,
        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)) * 0.5,
    )


def _entry_origin_xy(entry) -> tuple[float, float]:
    return (
        float(getattr(entry, "x_mm", 0.0) or 0.0)
        + max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)) * 0.5,
        float(getattr(entry, "y_mm", 0.0) or 0.0)
        + max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)) * 0.5,
    )


def _point_to_curve_xyz(point: tuple[float, float], offset: tuple[float, float], entry=None) -> tuple[float, float, float]:
    if entry is not None:
        point = free_transform.transform_entry_local_point(entry, point[0], point[1])
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
    point_radii: Sequence[float] | None = None,
    entry=None,
) -> None:
    if len(points) < 3:
        return
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    spline.use_cyclic_u = True
    spline.material_index = _MATERIAL_SLOT_LINE
    for index, point in enumerate(points):
        bp = spline.bezier_points[index]
        bp.co = _point_to_curve_xyz(point, offset, entry)
        is_sharp = index in sharp_indices
        handle_type = "VECTOR" if is_sharp else "AUTO"
        bp.handle_left_type = handle_type
        bp.handle_right_type = handle_type
        if point_radii is not None and index < len(point_radii):
            bp.radius = max(0.0, float(point_radii[index]))
        else:
            bp.radius = 1.0


def _add_bezier_anchor_loop(
    curve: bpy.types.Curve,
    anchors: Sequence[balloon_shapes.BezierAnchor],
    *,
    offset: tuple[float, float],
    entry=None,
) -> None:
    if len(anchors) < 3:
        return
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(anchors) - 1)
    spline.use_cyclic_u = True
    spline.material_index = _MATERIAL_SLOT_LINE
    for index, anchor in enumerate(anchors):
        anchor = free_transform.transform_entry_anchor(entry, anchor) if entry is not None else anchor
        bp = spline.bezier_points[index]
        bp.co = _point_to_curve_xyz(anchor.co, offset)
        left = anchor.handle_left if anchor.handle_left is not None else anchor.co
        right = anchor.handle_right if anchor.handle_right is not None else anchor.co
        bp.handle_left = _point_to_curve_xyz(left, offset)
        bp.handle_right = _point_to_curve_xyz(right, offset)
        bp.handle_left_type = str(anchor.handle_left_type or "FREE")
        bp.handle_right_type = str(anchor.handle_right_type or "FREE")
        bp.radius = 1.0


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
        "cloud_valley_sharp": bool(getattr(sp, "cloud_valley_sharp", False)),
        "dynamic_shape_base_kind": str(getattr(sp, "dynamic_shape_base_kind", "ellipse") or "ellipse"),
        "shape_seed": int(getattr(sp, "shape_seed", 0) or 0),
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
        "free_transform": free_transform.entry_snapshot(entry),
        "rounded": bool(getattr(entry, "rounded_corner_enabled", False)),
        "rounded_radius": float(getattr(entry, "rounded_corner_radius_mm", 0.0) or 0.0),
        "line_style": str(getattr(entry, "line_style", "") or ""),
        "line_width": float(getattr(entry, "line_width_mm", 0.3) or 0.0),
        "line_valley_width_pct": float(getattr(entry, "line_valley_width_pct", 100.0) or 0.0),
        "line_peak_width_pct": float(getattr(entry, "line_peak_width_pct", 100.0) or 0.0),
        "multi_line_count": int(getattr(entry, "multi_line_count", 3) or 3),
        "multi_line_width": float(getattr(entry, "multi_line_width_mm", 0.3) or 0.0),
        "multi_line_spacing": float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0),
        "multi_line_width_scale": float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0),
        "multi_line_spacing_scale": float(getattr(entry, "multi_line_spacing_scale_percent", 100.0) or 0.0),
        "multi_line_direction": str(getattr(entry, "multi_line_direction", "outside") or "outside"),
        "thorn_multi_line_valley_width_pct": float(getattr(entry, "thorn_multi_line_valley_width_pct", 100.0) or 0.0),
        "thorn_multi_line_peak_width_pct": float(getattr(entry, "thorn_multi_line_peak_width_pct", 100.0) or 0.0),
        "thorn_multi_line_length_scale": float(getattr(entry, "thorn_multi_line_length_scale_percent", 100.0) or 0.0),
        "thorn_multi_line_cross": bool(getattr(entry, "thorn_multi_line_cross_enabled", False)),
        "outer_edge_enabled": bool(getattr(entry, "outer_white_margin_enabled", False)),
        "outer_edge_width": float(getattr(entry, "outer_white_margin_width_mm", 1.0) or 0.0),
        "inner_edge_enabled": bool(getattr(entry, "inner_white_margin_enabled", False)),
        "inner_edge_width": float(getattr(entry, "inner_white_margin_width_mm", 1.0) or 0.0),
        "shape_params": shape_params,
        "tails": tails,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sync_curve_geometry(obj: bpy.types.Object, entry) -> None:
    curve = obj.data
    _clear_curve_splines(curve)
    if balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect")) == "none":
        return
    offset = _entry_curve_offset(entry)
    # 主線が別 Mesh で描画される形状ではカーブ側に main_line_fill_paths を追加しない。
    use_mesh_band = balloon_line_mesh.is_mesh_band_shape(entry)
    # フチが別 Mesh で描画される形状ではカーブ側に edge_paths を追加しない。
    use_shapely_line = balloon_line_mesh.is_shapely_line_shape(entry)
    # 多重線が別 Mesh で描画される形状ではカーブ側に multi_line_paths を追加しない。
    use_shapely_multi = balloon_line_mesh.is_shapely_multi_line_shape(entry)
    body_anchors = _body_bezier_for_entry(entry)
    if body_anchors is not None:
        _add_bezier_anchor_loop(curve, body_anchors, offset=offset, entry=entry)
        body_points = free_transform.transform_entry_local_points(
            entry,
            balloon_multiline_curve.sample_bezier_anchors(body_anchors, samples_per_segment=18),
        )
        if not use_mesh_band:
            balloon_multiline_curve.append_main_line_fill_paths(curve, entry, body_points, offset=offset)
        if not use_shapely_multi:
            balloon_multiline_curve.append_closed_multi_line_paths(curve, entry, body_points, offset=offset)
        if not use_shapely_line:
            balloon_multiline_curve.append_edge_paths(curve, entry, body_points, offset=offset)
    else:
        body_points, sharp = balloon_multiline_curve.body_outline_for_entry(entry)
        sharp_set = set(sharp)
        _add_bezier_loop(
            curve,
            body_points,
            sharp_indices=sharp_set,
            offset=offset,
            entry=entry,
        )
        body_points = free_transform.transform_entry_local_points(entry, body_points)
        if not use_mesh_band:
            balloon_multiline_curve.append_main_line_fill_paths(curve, entry, body_points, offset=offset)
        if not use_shapely_multi:
            balloon_multiline_curve.append_closed_multi_line_paths(curve, entry, body_points, offset=offset)
        if not use_shapely_line:
            balloon_multiline_curve.append_edge_paths(curve, entry, body_points, offset=offset)
    for tail in getattr(entry, "tails", []) or []:
        tail_points = _tail_polygon_for_entry(entry, tail)
        _add_bezier_loop(
            curve,
            tail_points,
            sharp_indices=set(range(len(tail_points))),
            offset=offset,
            entry=entry,
        )
        # しっぽの主線フチは Python メッシュ (ensure_balloon_tail_main_line_mesh) で
        # 焼き込むため、ジオメトリノード用の main_line_fill spline は追加しない。


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
    origin_x, origin_y = _entry_origin_xy(entry)
    obj.location.x = mm_to_m(origin_x)
    obj.location.y = mm_to_m(origin_y)
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
            origin_x, origin_y = _entry_origin_xy(entry)
            obj.location.x = _mm_to_m(origin_x + ox_mm)
            obj.location.y = _mm_to_m(origin_y + oy_mm)
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
    mask_info = coma_content_mask.ensure_viewport_mask_for_entry(scene, work, page, entry)
    line_mat = _ensure_balloon_curve_material(
        None,
        material_name=f"{BALLOON_CURVE_MATERIAL_PREFIX}{balloon_id}",
        entry=entry,
        mask_info=mask_info,
        mask_power=_LINE_AND_EDGE_MASK_POWER,
    )
    fill_mat = _ensure_fill_material(f"{BALLOON_FILL_MATERIAL_PREFIX}{balloon_id}", entry, mask_info=mask_info)
    outer_mat = _ensure_color_material(
        f"{BALLOON_OUTER_EDGE_MATERIAL_PREFIX}{balloon_id}",
        _entry_margin_rgba(entry, "outer_white_margin_color"),
        mask_info=mask_info,
        mask_power=_LINE_AND_EDGE_MASK_POWER,
    )
    inner_mat = _ensure_color_material(
        f"{BALLOON_INNER_EDGE_MATERIAL_PREFIX}{balloon_id}",
        _entry_margin_rgba(entry, "inner_white_margin_color"),
        mask_info=mask_info,
        mask_power=_LINE_AND_EDGE_MASK_POWER,
    )
    _prepare_balloon_curve_data(obj.data, line_mat, fill_mat, outer_mat, inner_mat)
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
        _sync_balloon_render_modifier(entry, obj)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: lightweight render node sync failed")
    try:
        _sync_balloon_band_meshes(scene, work, page, entry, obj, mask_info)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: lightweight line/edge/multi-line mesh sync failed")
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
    removed += balloon_line_mesh.cleanup_orphan_line_meshes(valid)
    removed += balloon_fill_mesh.cleanup_orphan_fill_meshes(valid)
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
