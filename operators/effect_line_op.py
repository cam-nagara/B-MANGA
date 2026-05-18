"""効果線 Operator.

ビューポート上のドラッグ範囲から Grease Pencil の効果線レイヤーを作成し、
作成済み効果線の移動・リサイズを扱う。
"""

from __future__ import annotations

import json
import math
import bpy
from bpy.types import Operator

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_active_page, get_work
from ..utils import gp_layer_parenting as gp_parent, layer_hierarchy, log, object_selection, page_grid
from ..utils.geom import m_to_mm, mm_to_m
from ..utils import layer_stack as layer_stack_utils
from . import coma_modal_state, coma_picker, effect_line_link_op, selection_context_menu, view_event_region

_logger = log.get_logger(__name__)

_EFFECT_META_PROP = "bname_effect_line_meta"
_PARAM_SYNCING = False
_EFFECT_MIN_SIZE_MM = 2.0
_EFFECT_HANDLE_HIT_MM = 2.5
_EFFECT_STROKE_HIT_MM = 2.5
_EFFECT_DRAG_EPS_MM = 0.05


def _unique_layer_name(gp_data, base: str) -> str:
    existing = {layer.name for layer in getattr(gp_data, "layers", [])}
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}.{i:03d}"
        if candidate not in existing:
            return candidate
        i += 1


def _effect_meta(obj) -> dict:
    data = getattr(obj, "data", None)
    if data is None:
        return {}
    try:
        raw = data.get(_EFFECT_META_PROP, "{}")
    except Exception:  # noqa: BLE001
        return {}
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_effect_meta(obj, meta: dict) -> None:
    data = getattr(obj, "data", None)
    if data is None:
        return
    try:
        data[_EFFECT_META_PROP] = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line: metadata write failed")


def _layer_meta_key(layer) -> str:
    return str(getattr(layer, "name", "") or "")


def _set_layer_bounds(
    obj,
    layer,
    bounds: tuple[float, float, float, float],
    *,
    seed: int | None = None,
    params_data: dict | None = None,
    center_xy_mm: tuple[float, float] | None = None,
) -> None:
    x, y, w, h = bounds
    meta = _effect_meta(obj)
    key = _layer_meta_key(layer)
    prev = meta.get(key, {}) if isinstance(meta.get(key, {}), dict) else {}
    if seed is None:
        try:
            seed = int(prev.get("seed", 0))
        except Exception:  # noqa: BLE001
            seed = 0
    entry = dict(prev)
    entry.update({
        "x": float(x),
        "y": float(y),
        "w": max(_EFFECT_MIN_SIZE_MM, float(w)),
        "h": max(_EFFECT_MIN_SIZE_MM, float(h)),
        "seed": int(seed or 0),
    })
    if center_xy_mm is None:
        try:
            center_xy_mm = (float(prev["center_x"]), float(prev["center_y"]))
        except Exception:  # noqa: BLE001
            center_xy_mm = (float(x) + entry["w"] * 0.5, float(y) + entry["h"] * 0.5)
    entry["center_x"] = float(center_xy_mm[0])
    entry["center_y"] = float(center_xy_mm[1])
    if params_data is not None:
        entry["params"] = params_data
    meta[key] = entry
    _write_effect_meta(obj, meta)


def _remove_layer_bounds(obj, layer) -> None:
    meta = _effect_meta(obj)
    key = _layer_meta_key(layer)
    if key in meta:
        meta.pop(key, None)
        _write_effect_meta(obj, meta)


def _frame_drawing(layer):
    from ..utils import gpencil

    frame = gpencil.ensure_active_frame(layer)
    return getattr(frame, "drawing", None) if frame is not None else None


def _clear_drawing(drawing) -> None:
    if drawing is None:
        return
    try:
        drawing.remove_strokes()
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        count = len(getattr(drawing, "strokes", []))
        if count > 0:
            drawing.remove_strokes(indices=tuple(range(count)))
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line: clear drawing failed")


def _stroke_bounds(layer) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for frame in getattr(layer, "frames", []) or []:
        drawing = getattr(frame, "drawing", None)
        for stroke in getattr(drawing, "strokes", []) or []:
            for point in getattr(stroke, "points", []) or []:
                pos = getattr(point, "position", None)
                if pos is None:
                    continue
                try:
                    xs.append(m_to_mm(float(pos[0])))
                    ys.append(m_to_mm(float(pos[1])))
                except Exception:  # noqa: BLE001
                    continue
    if not xs or not ys:
        return None
    left = min(xs)
    bottom = min(ys)
    return left, bottom, max(_EFFECT_MIN_SIZE_MM, max(xs) - left), max(_EFFECT_MIN_SIZE_MM, max(ys) - bottom)


def effect_layer_bounds(obj, layer) -> tuple[float, float, float, float] | None:
    if obj is None or layer is None:
        return None
    key = _layer_meta_key(layer)
    stored = _effect_meta(obj).get(key)
    if isinstance(stored, dict):
        try:
            x = float(stored.get("x", 0.0))
            y = float(stored.get("y", 0.0))
            w = max(_EFFECT_MIN_SIZE_MM, float(stored.get("w", _EFFECT_MIN_SIZE_MM)))
            h = max(_EFFECT_MIN_SIZE_MM, float(stored.get("h", _EFFECT_MIN_SIZE_MM)))
            return x, y, w, h
        except Exception:  # noqa: BLE001
            pass
    return _stroke_bounds(layer)


def _bounds_center(bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    return float(bounds[0]) + float(bounds[2]) * 0.5, float(bounds[1]) + float(bounds[3]) * 0.5


def effect_layer_center(obj, layer, bounds=None) -> tuple[float, float] | None:
    if obj is None or layer is None:
        return None
    if bounds is None:
        bounds = effect_layer_bounds(obj, layer)
    if bounds is None:
        return None
    stored = _effect_meta(obj).get(_layer_meta_key(layer))
    if isinstance(stored, dict):
        try:
            return float(stored["center_x"]), float(stored["center_y"])
        except Exception:  # noqa: BLE001
            pass
    return _bounds_center(bounds)


def _page_world_offset_for_parent_key(context, parent_key: str) -> tuple[float, float] | None:
    parent_key = str(parent_key or "")
    if not parent_key:
        return None
    page_id = parent_key.split(":", 1)[0]
    if not page_id:
        return None
    work = get_work(context)
    if work is None:
        return None
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == page_id:
            return page_grid.page_total_offset_mm(work, context.scene, page_index)
    return None


def effect_layer_world_bounds(context, obj, layer, bounds=None) -> tuple[float, float, float, float] | None:
    """効果線のページ内 bounds をビューポート上の world mm bounds に変換する."""
    if obj is None or layer is None:
        return None
    if bounds is None:
        bounds = effect_layer_bounds(obj, layer)
    if bounds is None:
        return None
    x, y, w, h = bounds
    from ..utils import object_naming as on

    parent_key = gp_parent.parent_key(layer) or str(obj.get(on.PROP_PARENT_KEY, "") or "")
    offset = _page_world_offset_for_parent_key(context, parent_key)
    if offset is None and str(obj.get(on.PROP_KIND, "") or "") == "effect":
        try:
            offset = (m_to_mm(float(obj.location.x)), m_to_mm(float(obj.location.y)))
        except Exception:  # noqa: BLE001
            offset = (0.0, 0.0)
    ox, oy = offset if offset is not None else (0.0, 0.0)
    return float(x) + ox, float(y) + oy, float(w), float(h)


def effect_layer_world_point(context, obj, xy_mm: tuple[float, float] | None, layer=None) -> tuple[float, float] | None:
    if obj is None or xy_mm is None:
        return None
    from ..utils import object_naming as on

    parent_key = gp_parent.parent_key(layer) if layer is not None else ""
    parent_key = parent_key or str(obj.get(on.PROP_PARENT_KEY, "") or "")
    offset = _page_world_offset_for_parent_key(context, parent_key)
    if offset is None and str(obj.get(on.PROP_KIND, "") or "") == "effect":
        try:
            offset = (m_to_mm(float(obj.location.x)), m_to_mm(float(obj.location.y)))
        except Exception:  # noqa: BLE001
            offset = (0.0, 0.0)
    ox, oy = offset if offset is not None else (0.0, 0.0)
    return float(xy_mm[0]) + ox, float(xy_mm[1]) + oy


def active_effect_layer_bounds(context=None):
    ctx = context or bpy.context
    from ..utils import layer_stack as stack_utils

    key = str(getattr(getattr(ctx, "scene", None), "bname_active_effect_layer_name", "") or "")
    obj, active = stack_utils._find_effect_layer_by_key(key) if key else (None, None)
    layers = getattr(getattr(obj, "data", None), "layers", None) if obj is not None else None
    if active is None:
        active = getattr(layers, "active", None) if layers is not None else None
    bounds = effect_layer_bounds(obj, active)
    if bounds is None:
        return obj, active, None
    return obj, active, bounds


def _set_active_effect_layer(context, obj, layer) -> None:
    if obj is not None:
        try:
            context.view_layer.objects.active = obj
            obj.select_set(True)
        except Exception:  # noqa: BLE001
            pass
    if obj is not None and layer is not None:
        try:
            obj.data.layers.active = layer
        except Exception:  # noqa: BLE001
            pass
    scene = getattr(context, "scene", None)
    if scene is not None and layer is not None:
        if hasattr(scene, "bname_active_layer_kind"):
            scene.bname_active_layer_kind = "effect"
        if hasattr(scene, "bname_active_effect_layer_name"):
            scene.bname_active_effect_layer_name = layer_stack_utils._node_stack_key(layer)
        _load_layer_params_to_scene(context, obj, layer)


def _select_effect_layer(context, obj, layer) -> None:
    _set_active_effect_layer(context, obj, layer)
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    uid = layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(layer))
    if stack is not None:
        for i, item in enumerate(stack):
            if layer_stack_utils.stack_item_uid(item) == uid:
                layer_stack_utils.set_active_stack_index_silently(context, i)
                break
    layer_stack_utils.remember_layer_stack_signature(context)
    layer_stack_utils.tag_view3d_redraw(context)


def _seed_for_new_layer(obj) -> int:
    meta = _effect_meta(obj)
    used = []
    for item in meta.values():
        if isinstance(item, dict):
            try:
                used.append(int(item.get("seed", 0)))
            except Exception:  # noqa: BLE001
                pass
    return (max(used) + 1) if used else 1


def _seed_for_layer(obj, layer) -> int:
    stored = _effect_meta(obj).get(_layer_meta_key(layer), {})
    if isinstance(stored, dict):
        try:
            return int(stored.get("seed", 0))
        except Exception:  # noqa: BLE001
            return 0
    return 0


def _layer_params_data(obj, layer) -> dict:
    stored = _effect_meta(obj).get(_layer_meta_key(layer), {})
    if not isinstance(stored, dict):
        return {}
    params = stored.get("params", {})
    return params if isinstance(params, dict) else {}


def _scene_params_syncing(scene) -> bool:
    _ = scene
    return bool(_PARAM_SYNCING)


def _set_scene_params_syncing(scene, value: bool) -> None:
    _ = scene
    global _PARAM_SYNCING
    _PARAM_SYNCING = bool(value)


def _load_layer_params_to_scene(context, obj, layer) -> None:
    scene = getattr(context, "scene", None)
    params = getattr(scene, "bname_effect_line_params", None) if scene is not None else None
    data = _layer_params_data(obj, layer)
    if params is None or not data:
        return
    try:
        from ..core import effect_line

        _set_scene_params_syncing(scene, True)
        effect_line.effect_params_from_dict(params, data)
        if "opacity" not in data and hasattr(layer, "opacity") and hasattr(params, "opacity"):
            params.opacity = float(getattr(layer, "opacity", 1.0))
    finally:
        _set_scene_params_syncing(scene, False)


def _material_slot_index(obj, mat) -> int:
    mats = getattr(getattr(obj, "data", None), "materials", None)
    if mats is None or mat is None:
        return -1
    for i, existing in enumerate(mats):
        if existing is mat or getattr(existing, "name", "") == getattr(mat, "name", ""):
            return i
    try:
        mats.append(mat)
        return len(mats) - 1
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line: material slot append failed")
        return -1


def _effect_opacity(params) -> float:
    try:
        return max(0.0, min(1.0, float(getattr(params, "opacity", 1.0))))
    except Exception:  # noqa: BLE001
        return 1.0


def _rgba_with_opacity(color, opacity: float) -> tuple[float, float, float, float]:
    try:
        r, g, b, a = (float(color[i]) for i in range(4))
    except Exception:  # noqa: BLE001
        r, g, b, a = 0.0, 0.0, 0.0, 1.0
    alpha = max(0.0, min(1.0, a * float(opacity)))
    return (
        max(0.0, min(1.0, r)),
        max(0.0, min(1.0, g)),
        max(0.0, min(1.0, b)),
        alpha,
    )


def _effect_role_material_name(layer, role: str) -> str:
    base = str(getattr(layer, "name", "") or "Layer")
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in base)
    safe = safe.strip("_") or "Layer"
    return f"BName_Effect_{role}_{safe}"


def _ensure_effect_material(obj, name: str, color: tuple[float, float, float, float]) -> int:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
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
            gp_style.color = color
        except Exception:  # noqa: BLE001
            pass
    try:
        mat.diffuse_color = color
    except Exception:  # noqa: BLE001
        pass
    return _material_slot_index(obj, mat)


def _apply_material_settings(obj, layer, params) -> int:
    from ..utils import gpencil

    mat = gpencil.ensure_layer_material(
        obj,
        layer,
        activate=True,
        assign_existing=False,
    )
    gp_style = getattr(mat, "grease_pencil", None) if mat is not None else None
    if gp_style is None:
        return _material_slot_index(obj, mat)
    try:
        gp_style.show_stroke = True
    except Exception:  # noqa: BLE001
        pass
    opacity = _effect_opacity(params)
    try:
        if hasattr(layer, "opacity"):
            layer.opacity = 1.0
    except Exception:  # noqa: BLE001
        pass
    try:
        gp_style.color = _rgba_with_opacity(params.line_color, opacity)
    except Exception:  # noqa: BLE001
        pass
    try:
        fill = [float(c) for c in params.fill_color[:4]]
        fill[3] = max(0.0, min(1.0, fill[3] * float(params.fill_opacity) * opacity))
        gp_style.fill_color = tuple(fill)
    except Exception:  # noqa: BLE001
        pass
    try:
        gp_style.show_fill = bool(params.effect_type == "beta_flash" and params.fill_base_shape)
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.diffuse_color = tuple(getattr(gp_style, "color", mat.diffuse_color))
    except Exception:  # noqa: BLE001
        pass
    return _material_slot_index(obj, mat)


def copy_layer_effect_meta(obj, source_layer, dest_layer, *, include_link: bool = False) -> None:
    """効果線レイヤー複製時に描画範囲・詳細設定メタデータを引き継ぐ。"""
    if obj is None or source_layer is None or dest_layer is None:
        return
    source_key = _layer_meta_key(source_layer)
    dest_key = _layer_meta_key(dest_layer)
    if not source_key or not dest_key or source_key == dest_key:
        return
    meta = _effect_meta(obj)
    source = meta.get(source_key)
    if not isinstance(source, dict):
        return
    try:
        copied = json.loads(json.dumps(source, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        copied = dict(source)
    if not include_link:
        copied.pop(effect_line_link_op.LINK_ID_PROP, None)
    meta[dest_key] = copied
    _write_effect_meta(obj, meta)


class _EffectParamProxy:
    def __init__(self, fallback, data: dict):
        self._fallback = fallback
        self._data = data or {}

    def __getattr__(self, name: str):
        if name in self._data:
            return self._data[name]
        return getattr(self._fallback, name)


def _params_for_write(context, obj, layer, params_override=None):
    if params_override is not None:
        return params_override
    scene_params = getattr(context.scene, "bname_effect_line_params", None)
    if scene_params is None:
        return None
    try:
        from ..utils import effect_inout_curve

        effect_inout_curve.sync_ui_nodes_to_params(scene_params)
    except Exception:  # noqa: BLE001
        pass
    data = _layer_params_data(obj, layer)
    if data:
        if "opacity" not in data and hasattr(layer, "opacity"):
            data = dict(data)
            try:
                data["opacity"] = float(getattr(layer, "opacity", 1.0))
            except Exception:  # noqa: BLE001
                data["opacity"] = 1.0
        return _EffectParamProxy(scene_params, data)
    return scene_params


def _start_frame_outline_for_bounds(
    context,
    params,
    center_xy_mm: tuple[float, float],
) -> tuple[list[tuple[float, float]] | None, float]:
    if not bool(getattr(params, "start_to_coma_frame", False)):
        return None, 0.0
    page = get_active_page(context)
    if page is None:
        return None, 0.0
    panel = layer_stack_utils.coma_containing_point(page, center_xy_mm[0], center_xy_mm[1])
    if panel is None:
        return None, 0.0
    outline = layer_hierarchy.coma_polygon(panel)
    if len(outline) < 3:
        return None, 0.0
    return outline, max(0.0, float(getattr(params, "brush_size_mm", 0.0)))


def _write_effect_strokes(
    context,
    obj,
    layer,
    bounds: tuple[float, float, float, float],
    *,
    seed: int | None = None,
    params_override=None,
    propagate_link: bool = True,
    center_xy_mm: tuple[float, float] | None = None,
) -> int:
    from ..utils import gpencil
    from ..core import effect_line
    from . import effect_line_gen

    params = _params_for_write(context, obj, layer, params_override=params_override)
    if params is None:
        return 0
    x, y, w, h = bounds
    w = max(_EFFECT_MIN_SIZE_MM, float(w))
    h = max(_EFFECT_MIN_SIZE_MM, float(h))
    shape_center_xy = (float(x) + w * 0.5, float(y) + h * 0.5)
    focus_center_xy = center_xy_mm if center_xy_mm is not None else effect_layer_center(obj, layer, (float(x), float(y), w, h))
    if focus_center_xy is None:
        focus_center_xy = shape_center_xy
    seed_value = _seed_for_layer(obj, layer) if seed is None else int(seed)
    drawing = _frame_drawing(layer)
    if drawing is None:
        return 0
    line_material_index = _apply_material_settings(obj, layer, params)
    white_outline_black_material_index = line_material_index
    white_outline_white_material_index = line_material_index
    opacity = _effect_opacity(params)
    if getattr(params, "effect_type", "") == "white_outline":
        white_outline_black_material_index = _ensure_effect_material(
            obj,
            _effect_role_material_name(layer, "WhiteOutline_Black"),
            (0.0, 0.0, 0.0, opacity),
        )
        white_outline_white_material_index = _ensure_effect_material(
            obj,
            _effect_role_material_name(layer, "WhiteOutline_White"),
            (1.0, 1.0, 1.0, opacity),
        )
    start_guide_material_index = _ensure_effect_material(
        obj,
        _effect_role_material_name(layer, "StartShape_Purple"),
        (0.55, 0.12, 1.0, opacity),
    )
    end_guide_material_index = _ensure_effect_material(
        obj,
        _effect_role_material_name(layer, "EndShape_Cyan"),
        (0.0, 0.75, 1.0, opacity),
    )
    _clear_drawing(drawing)
    start_outline, start_extend = _start_frame_outline_for_bounds(context, params, focus_center_xy)
    strokes = effect_line_gen.generate_strokes(
        params,
        center_xy_mm=focus_center_xy,
        radius_xy_mm=(w * 0.5, h * 0.5),
        seed=seed_value,
        start_outline_mm=start_outline,
        start_extend_mm=start_extend,
        end_center_xy_mm=shape_center_xy,
    )
    guide_strokes = effect_line_gen.generate_shape_guide_strokes(
        params,
        center_xy_mm=focus_center_xy,
        radius_xy_mm=(w * 0.5, h * 0.5),
        start_outline_mm=start_outline,
        start_extend_mm=start_extend,
        seed=seed_value,
        end_center_xy_mm=shape_center_xy,
    )
    line_added = 0
    for stroke in strokes:
        material_index = line_material_index
        if stroke.role == "white_outline_black":
            material_index = white_outline_black_material_index
        elif stroke.role == "white_outline_white":
            material_index = white_outline_white_material_index
        if gpencil.add_stroke_to_drawing(
            drawing,
            stroke.points_xyz,
            radius=stroke.radius,
            radii=getattr(stroke, "radii", None),
            opacities=getattr(stroke, "opacities", None),
            cyclic=stroke.cyclic,
            material_index=material_index,
            curve_type=getattr(stroke, "curve_type", "POLY"),
            bezier_smooth=bool(getattr(stroke, "bezier_smooth", False)),
        ):
            line_added += 1
    for stroke in guide_strokes:
        material_index = start_guide_material_index if stroke.role == "start_guide" else end_guide_material_index
        gpencil.add_stroke_to_drawing(
            drawing,
            stroke.points_xyz,
            radius=stroke.radius,
            radii=getattr(stroke, "radii", None),
            opacities=getattr(stroke, "opacities", None),
            cyclic=stroke.cyclic,
            material_index=material_index,
            curve_type=getattr(stroke, "curve_type", "POLY"),
            bezier_smooth=bool(getattr(stroke, "bezier_smooth", False)),
        )
    gpencil.ensure_layer_material(obj, layer, activate=True, assign_existing=False)
    params_data = effect_line.effect_params_to_dict(params)
    _set_layer_bounds(
        obj,
        layer,
        (float(x), float(y), w, h),
        seed=seed_value,
        params_data=params_data,
        center_xy_mm=focus_center_xy,
    )
    if propagate_link:
        effect_line_link_op.propagate_linked_effect_strokes(
            context,
            obj,
            layer,
            (float(x), float(y), w, h),
            params_data,
            focus_center_xy,
        )
    return line_added


def on_effect_params_changed(context, _params) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or _scene_params_syncing(scene):
        return
    if getattr(scene, "bname_active_layer_kind", "") != "effect":
        return
    obj, layer, bounds = active_effect_layer_bounds(context)
    if obj is None or layer is None or bounds is None:
        return
    try:
        _write_effect_strokes(context, obj, layer, bounds, params_override=_params)
        layer_stack_utils.tag_view3d_redraw(context)
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line: param change rebuild failed")


def _creation_context_for_world_point(context, x_mm: float, y_mm: float):
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None
    page_index = coma_picker.find_page_at_world_mm(work, x_mm, y_mm)
    if page_index is None or not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)
    local_x = float(x_mm) - ox_mm
    local_y = float(y_mm) - oy_mm
    panel = layer_hierarchy.coma_containing_point(page, local_x, local_y)
    if panel is not None:
        parent_key = layer_hierarchy.coma_stack_key(page, panel)
    else:
        parent_key = layer_hierarchy.page_stack_key(page)
    return work, page, page_index, local_x, local_y, parent_key


def _parent_key_for_world_point(context, x_mm: float, y_mm: float) -> str:
    resolved = _creation_context_for_world_point(context, x_mm, y_mm)
    return str(resolved[5]) if resolved is not None else ""


def _event_local_xy_for_effect_obj(context, event, obj) -> tuple[float | None, float | None]:
    world_x_mm, world_y_mm = _event_world_xy_mm(context, event)
    if world_x_mm is None or world_y_mm is None:
        return None, None
    return _world_local_xy_for_effect_obj(context, obj, world_x_mm, world_y_mm)


def _world_local_xy_for_effect_obj(context, obj, x_mm: float, y_mm: float) -> tuple[float, float]:
    try:
        loc = getattr(obj, "location", None)
        if loc is not None:
            return float(x_mm) - m_to_mm(float(loc.x)), float(y_mm) - m_to_mm(float(loc.y))
    except Exception:  # noqa: BLE001
        pass
    try:
        from mathutils import Vector

        inv = obj.matrix_world.inverted()
        local = inv @ Vector((mm_to_m(float(x_mm)), mm_to_m(float(y_mm)), 0.0))
        return m_to_mm(float(local.x)), m_to_mm(float(local.y))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..utils import object_naming as on

        parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
        work = get_work(context)
        if work is not None and parent_key:
            page_id = parent_key.split(":", 1)[0]
            for i, page in enumerate(getattr(work, "pages", []) or []):
                if str(getattr(page, "id", "") or "") == page_id:
                    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, i)
                    return float(x_mm) - ox_mm, float(y_mm) - oy_mm
    except Exception:  # noqa: BLE001
        pass
    return float(x_mm), float(y_mm)


def _create_effect_layer(
    context,
    bounds: tuple[float, float, float, float] | None = None,
    *,
    parent_key: str = "",
):
    """新規効果線 GP Object を作成 (新設計: 1 effect = 1 GP Object @ コマ Collection).

    旧設計の集約 GP Object (`BName_EffectLines`) に layer を追加する方式を撤廃し、
    各効果線が独立した GP Object として該当コマ / ページ Collection 配下に
    配置される。 これにより Outliner 上で「効果線レイヤーが該当コマの中に
    作成」されるようになる。
    """
    from ..utils import effect_line_object as elo
    from . import effect_line_object_op as elop
    from ..utils import object_naming as on

    scene = context.scene
    params = getattr(scene, "bname_effect_line_params", None)
    suffix = getattr(params, "effect_type", "effect") if params is not None else "effect"

    # parent_kind / parent_key を解決
    parent_kind = "page"
    if parent_key and ":" in parent_key:
        parent_kind = "coma"
    elif not parent_key:
        # parent_key が空ならアクティブ page/coma から導出
        page_id, coma_id = elop._resolve_active_coma(context)
        if coma_id:
            parent_kind = "coma"
            parent_key = f"{page_id}:{coma_id}"
        elif page_id:
            parent_kind = "page"
            parent_key = page_id

    bname_id = elop._make_effect_bname_id()
    title = f"効果線_{suffix}"

    # z_index は parent 配下の effect Object 群の最大値 + 10
    max_z = 200
    for o in bpy.data.objects:
        if str(o.get(on.PROP_KIND, "") or "") != "effect":
            continue
        if str(o.get(on.PROP_PARENT_KEY, "") or "") != parent_key:
            continue
        try:
            z = int(o.get(on.PROP_Z_INDEX, 0) or 0)
        except Exception:  # noqa: BLE001
            z = 0
        if z > max_z:
            max_z = z
    z_index = max_z + 10

    obj = elo.create_effect_line_object(
        scene=scene,
        bname_id=bname_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
    )
    if obj is None or obj.data is None:
        return None, None
    gp_data = obj.data
    if len(gp_data.layers) == 0:
        layer = gp_data.layers.new(_unique_layer_name(gp_data, title))
    else:
        layer = gp_data.layers[0]
        try:
            layer.name = _unique_layer_name(gp_data, title)
        except Exception:  # noqa: BLE001
            pass
    gp_data.layers.active = layer
    # GP layer 側の parent_key も保持 (overlay / export pipeline が参照する)
    if parent_key:
        try:
            gp_parent.set_parent_key(layer, parent_key)
        except Exception:  # noqa: BLE001
            pass
    seed = _seed_for_new_layer(obj)
    if bounds is None:
        bounds = (70.0, 110.0, 80.0, 100.0)
    _write_effect_strokes(context, obj, layer, bounds, seed=seed)
    _select_effect_layer(context, obj, layer)
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return obj, layer


def reset_effect_center_to_bounds(context) -> bool:
    obj, layer, bounds = active_effect_layer_bounds(context)
    if obj is None or layer is None or bounds is None:
        return False
    _write_effect_strokes(context, obj, layer, bounds, center_xy_mm=_bounds_center(bounds))
    _select_effect_layer(context, obj, layer)
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return True


def _delete_effect_layer(context, obj, layer) -> None:
    """効果線レイヤーを削除する.

    新設計 (1 effect = 1 GP Object) では、 layer を消すと obj が空シェル
    として残るため、 obj 全体を削除する。 旧設計の集約 GP Object
    (BName_EffectLines) からの削除は layer のみ消す互換動作を維持する。
    """
    from ..utils import object_naming as on

    if obj is None or layer is None:
        return
    _remove_layer_bounds(obj, layer)
    is_new_effect_obj = str(obj.get(on.PROP_KIND, "") or "") == "effect"
    try:
        obj.data.layers.remove(layer)
    except Exception:  # noqa: BLE001
        return
    if is_new_effect_obj:
        # 新設計: 1 effect = 1 GP Object → obj 全体を削除
        try:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            try:
                if data is not None and data.users == 0:
                    blocks = getattr(bpy.data, "grease_pencils_v3", None) or getattr(
                        bpy.data, "grease_pencils", None
                    )
                    if blocks is not None:
                        blocks.remove(data)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
    if hasattr(context.scene, "bname_active_effect_layer_name"):
        context.scene.bname_active_effect_layer_name = ""
    layer_stack_utils.sync_layer_stack_after_data_change(context)


def _event_world_xy_mm(context, event) -> tuple[float | None, float | None]:
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import geom

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None, None
    _area, region, rv3d, mouse_x, mouse_y = view
    loc = region_2d_to_location_3d(region, rv3d, (mouse_x, mouse_y), (0.0, 0.0, 0.0))
    if loc is None:
        return None, None
    return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _event_in_view3d_window(context, event) -> bool:
    return view_event_region.is_view3d_window_event(context, event)


def _effect_hit_part(
    bounds: tuple[float, float, float, float],
    x_mm: float,
    y_mm: float,
    *,
    center_xy_mm: tuple[float, float] | None = None,
) -> str:
    x, y, w, h = bounds
    left, bottom, right, top = x, y, x + w, y + h
    threshold = min(_EFFECT_HANDLE_HIT_MM, max(0.35, min(w, h) * 0.25))
    in_expanded_bounds = (
        left - threshold <= x_mm <= right + threshold
        and bottom - threshold <= y_mm <= top + threshold
    )
    near_left = abs(x_mm - left) <= threshold
    near_right = abs(x_mm - right) <= threshold
    near_bottom = abs(y_mm - bottom) <= threshold
    near_top = abs(y_mm - top) <= threshold
    inside_x = left <= x_mm <= right
    inside_y = bottom <= y_mm <= top
    if in_expanded_bounds and near_left and near_top:
        return "top_left"
    if in_expanded_bounds and near_right and near_top:
        return "top_right"
    if in_expanded_bounds and near_left and near_bottom:
        return "bottom_left"
    if in_expanded_bounds and near_right and near_bottom:
        return "bottom_right"
    if in_expanded_bounds and near_left and inside_y:
        return "left"
    if in_expanded_bounds and near_right and inside_y:
        return "right"
    if in_expanded_bounds and near_top and inside_x:
        return "top"
    if in_expanded_bounds and near_bottom and inside_x:
        return "bottom"
    center_threshold = max(threshold, _EFFECT_HANDLE_HIT_MM)
    cx, cy = center_xy_mm if center_xy_mm is not None else (left + w * 0.5, bottom + h * 0.5)
    if math.hypot(float(x_mm) - float(cx), float(y_mm) - float(cy)) <= center_threshold:
        return "center"
    if not in_expanded_bounds:
        return ""
    if inside_x and inside_y:
        return "body"
    return ""


def _distance_to_segment_mm(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1.0e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
    nearest = (start[0] + dx * t, start[1] + dy * t)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _layer_stroke_hit_part(layer, x_mm: float, y_mm: float, tolerance_mm: float = _EFFECT_STROKE_HIT_MM) -> str:
    point = (float(x_mm), float(y_mm))
    tolerance = max(0.1, float(tolerance_mm))
    for frame in getattr(layer, "frames", []) or []:
        drawing = getattr(frame, "drawing", None)
        for stroke in getattr(drawing, "strokes", []) or []:
            pts: list[tuple[float, float]] = []
            for gp_point in getattr(stroke, "points", []) or []:
                pos = getattr(gp_point, "position", None)
                if pos is None:
                    continue
                try:
                    pts.append((m_to_mm(float(pos[0])), m_to_mm(float(pos[1]))))
                except Exception:  # noqa: BLE001
                    continue
            if len(pts) == 1 and math.hypot(point[0] - pts[0][0], point[1] - pts[0][1]) <= tolerance:
                return "body"
            if len(pts) < 2:
                continue
            for i in range(len(pts) - 1):
                if _distance_to_segment_mm(point, pts[i], pts[i + 1]) <= tolerance:
                    return "body"
            if bool(getattr(stroke, "cyclic", False)) and len(pts) > 2:
                if _distance_to_segment_mm(point, pts[-1], pts[0]) <= tolerance:
                    return "body"
    return ""


def _hit_effect_layer(context, x_mm: float, y_mm: float):
    """全 effect GP Object をスキャンし、 (obj, layer, bounds, part) を返す.

    新設計 (1 effect = 1 GP Object) に対応。 各 effect Object はデフォルトで
    1 layer ("content") を持つ。 旧設計の単一集約 Object (BName_EffectLines)
    は新規作成時に hide されるが、 念のため fallback として最後にスキャンする。
    """
    from ..utils import gpencil
    from ..utils import object_naming as on

    # 新設計の effect Object 群を Z 順 (新しい順) で並べる
    candidates: list[bpy.types.Object] = []
    for o in bpy.data.objects:
        if str(o.get(on.PROP_KIND, "") or "") != "effect":
            continue
        if o.hide_viewport:
            continue
        candidates.append(o)
    candidates.sort(key=lambda o: int(o.get(on.PROP_Z_INDEX, 0) or 0), reverse=True)

    # 旧設計の集約 obj が残っている場合は最後に追加 (互換性のため)
    legacy_obj = layer_stack_utils.get_effect_gp_object()
    if legacy_obj is not None and legacy_obj not in candidates:
        if not legacy_obj.hide_viewport:
            candidates.append(legacy_obj)

    for obj in candidates:
        local_x, local_y = _world_local_xy_for_effect_obj(context, obj, x_mm, y_mm)
        gp_data = getattr(obj, "data", None)
        if gp_data is None:
            continue
        layers = list(getattr(gp_data, "layers", []) or [])
        if not layers:
            continue
        for layer in reversed(layers):
            if gpencil.layer_effectively_hidden(layer):
                continue
            bounds = effect_layer_bounds(obj, layer)
            if bounds is None:
                continue
            part = _effect_hit_part(
                bounds,
                local_x,
                local_y,
                center_xy_mm=effect_layer_center(obj, layer, bounds),
            )
            if not part:
                part = _layer_stroke_hit_part(layer, local_x, local_y)
            if part:
                return obj, layer, bounds, part
    return (candidates[0] if candidates else None), None, None, ""


def _rect_from_points(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    left = min(float(x0), float(x1))
    right = max(float(x0), float(x1))
    bottom = min(float(y0), float(y1))
    top = max(float(y0), float(y1))
    return left, bottom, max(_EFFECT_MIN_SIZE_MM, right - left), max(_EFFECT_MIN_SIZE_MM, top - bottom)


class BNAME_OT_effect_line_generate(Operator):
    bl_idname = "bname.effect_line_generate"
    bl_label = "効果線を生成"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_effect_line_params", None) is not None

    def execute(self, context):
        try:
            _obj, layer = _create_effect_layer(context)
            added = len(getattr(_frame_drawing(layer), "strokes", []) or [])
        except Exception as exc:  # noqa: BLE001
            _logger.exception("effect_line_generate failed")
            self.report({"ERROR"}, f"効果線生成失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"効果線生成: {added} ストローク")
        return {"FINISHED"}


class BNAME_OT_effect_line_tool(Operator):
    bl_idname = "bname.effect_line_tool"
    bl_label = "効果線ツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _dragging: bool
    _drag_action: str
    _drag_obj_name: str
    _drag_layer_name: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_orig_x: float
    _drag_orig_y: float
    _drag_orig_w: float
    _drag_orig_h: float
    _drag_orig_center_x: float
    _drag_orig_center_y: float
    _drag_moved: bool

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and get_mode(context) != MODE_COMA)

    def invoke(self, context, _event):
        active = coma_modal_state.get_active("effect_line_tool")
        if active is not None:
            active.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        coma_modal_state.finish_active("coma_vertex_edit", context, keep_selection=True)
        coma_modal_state.finish_active("knife_cut", context, keep_selection=False)
        coma_modal_state.finish_active("edge_move", context, keep_selection=True)
        coma_modal_state.finish_active("layer_move", context, keep_selection=True)
        coma_modal_state.finish_active("balloon_tool", context, keep_selection=True)
        coma_modal_state.finish_active("text_tool", context, keep_selection=True)
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._clear_drag_state()
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active("effect_line_tool", self, context)
        self.report({"INFO"}, "効果線ツール: ドラッグで作成")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("effect_line_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if view_event_region.modal_navigation_ui_passthrough(self, context, event):
            return {"PASS_THROUGH"}
        if not _event_in_view3d_window(context, event):
            return {"PASS_THROUGH"}
        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if selection_context_menu.open_for_effect_tool(context, event):
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if self._should_leave_for_tool_key(event):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return {"PASS_THROUGH"}
        obj, layer, bounds, part = _hit_effect_layer(context, x_mm, y_mm)
        if obj is not None and layer is not None and bounds is not None:
            _select_effect_layer(context, obj, layer)
            if event.ctrl or event.shift:
                object_selection.select_key(
                    context,
                    object_selection.effect_key(layer),
                    mode="toggle" if event.ctrl else "add",
                )
                return {"RUNNING_MODAL"}
            object_selection.select_key(
                context,
                object_selection.effect_key(layer),
                mode="single",
            )
            local_x, local_y = _event_local_xy_for_effect_obj(context, event, obj)
            if local_x is None or local_y is None:
                local_x, local_y = x_mm, y_mm
            self._start_drag(obj, layer, part, local_x, local_y, bounds)
            return {"RUNNING_MODAL"}
        create_ctx = _creation_context_for_world_point(context, x_mm, y_mm)
        if create_ctx is None:
            return {"PASS_THROUGH"}
        work_for_focus, page_for_focus, _page_index, local_x, local_y, parent_key_for_create = create_ctx
        # 作成位置に応じて active 階層 (page or coma) を切替えて Outliner も同期
        try:
            from ..utils import active_target as _at

            if work_for_focus is not None:
                if page_for_focus is not None:
                    pk = "coma" if ":" in parent_key_for_create else "page"
                    _at.focus_creation_target(
                        context, work_for_focus, page_for_focus,
                        pk, parent_key_for_create,
                    )
        except Exception:  # noqa: BLE001
            pass
        if ":" in str(parent_key_for_create or ""):
            params = getattr(context.scene, "bname_effect_line_params", None)
            if params is not None and str(getattr(params, "effect_type", "") or "") != "speed":
                _set_scene_params_syncing(context.scene, True)
                try:
                    params.start_to_coma_frame = True
                finally:
                    _set_scene_params_syncing(context.scene, False)
        obj, layer = _create_effect_layer(
            context,
            (local_x, local_y, _EFFECT_MIN_SIZE_MM, _EFFECT_MIN_SIZE_MM),
            parent_key=parent_key_for_create,
        )
        object_selection.select_key(
            context,
            object_selection.effect_key(layer),
            mode="single",
        )
        self._start_drag(
            obj,
            layer,
            "create",
            local_x,
            local_y,
            (local_x, local_y, _EFFECT_MIN_SIZE_MM, _EFFECT_MIN_SIZE_MM),
        )
        return {"RUNNING_MODAL"}

    def _should_leave_for_tool_key(self, event) -> bool:
        return (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "K", "T"}
            and not event.ctrl
            and not event.alt
        )

    def _start_drag(
        self,
        obj,
        layer,
        action: str,
        x_mm: float,
        y_mm: float,
        bounds: tuple[float, float, float, float],
    ) -> None:
        self._dragging = True
        self._drag_action = "move" if action == "body" else action
        self._drag_obj_name = str(getattr(obj, "name", "") or "")
        self._drag_layer_name = str(getattr(layer, "name", "") or "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_orig_x = float(bounds[0])
        self._drag_orig_y = float(bounds[1])
        self._drag_orig_w = float(bounds[2])
        self._drag_orig_h = float(bounds[3])
        center = effect_layer_center(obj, layer, bounds) or _bounds_center(bounds)
        self._drag_orig_center_x = float(center[0])
        self._drag_orig_center_y = float(center[1])
        self._drag_moved = False

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_obj_name = ""
        self._drag_layer_name = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_orig_x = 0.0
        self._drag_orig_y = 0.0
        self._drag_orig_w = 0.0
        self._drag_orig_h = 0.0
        self._drag_orig_center_x = 0.0
        self._drag_orig_center_y = 0.0
        self._drag_moved = False

    def _modal_dragging(self, context, event):
        if not _event_in_view3d_window(context, event):
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self._finish_drag(context)
            elif event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
                self._cancel_drag(context)
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            self._update_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._update_drag(context, event)
            self._finish_drag(context)
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cancel_drag(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _drag_target(self, context):
        obj_name = str(getattr(self, "_drag_obj_name", "") or "")
        obj = bpy.data.objects.get(obj_name) if obj_name else None
        if obj is None:
            obj = layer_stack_utils.get_effect_gp_object()
        if obj is None:
            return None, None
        layers = getattr(getattr(obj, "data", None), "layers", None)
        if layers is None:
            return obj, None
        for layer in layers:
            if str(getattr(layer, "name", "") or "") == self._drag_layer_name:
                return obj, layer
        return obj, None

    def _update_drag(self, context, event) -> None:
        obj, layer = self._drag_target(context)
        if obj is None or layer is None:
            self._clear_drag_state()
            return
        x_mm, y_mm = _event_local_xy_for_effect_obj(context, event, obj)
        if x_mm is None or y_mm is None:
            return
        dx = float(x_mm) - self._drag_start_x
        dy = float(y_mm) - self._drag_start_y
        if abs(dx) > _EFFECT_DRAG_EPS_MM or abs(dy) > _EFFECT_DRAG_EPS_MM:
            self._drag_moved = True
        bounds = self._drag_result_bounds(dx, dy)
        center = self._drag_result_center(bounds, dx, dy)
        _write_effect_strokes(context, obj, layer, bounds, center_xy_mm=center)
        _select_effect_layer(context, obj, layer)

    def _drag_result_bounds(self, dx: float, dy: float) -> tuple[float, float, float, float]:
        action = str(getattr(self, "_drag_action", "") or "")
        x = float(self._drag_orig_x)
        y = float(self._drag_orig_y)
        w = float(self._drag_orig_w)
        h = float(self._drag_orig_h)
        if action == "create":
            return _rect_from_points(self._drag_start_x, self._drag_start_y, self._drag_start_x + dx, self._drag_start_y + dy)
        if action == "center":
            return x, y, w, h
        if action == "move":
            return x + dx, y + dy, w, h
        right = x + w
        top = y + h
        new_left = x
        new_right = right
        new_bottom = y
        new_top = top
        if "left" in action:
            new_left = min(right - _EFFECT_MIN_SIZE_MM, x + dx)
        if "right" in action:
            new_right = max(x + _EFFECT_MIN_SIZE_MM, right + dx)
        if "bottom" in action:
            new_bottom = min(top - _EFFECT_MIN_SIZE_MM, y + dy)
        if "top" in action:
            new_top = max(y + _EFFECT_MIN_SIZE_MM, top + dy)
        return new_left, new_bottom, new_right - new_left, new_top - new_bottom

    def _drag_result_center(
        self,
        bounds: tuple[float, float, float, float],
        dx: float,
        dy: float,
    ) -> tuple[float, float]:
        action = str(getattr(self, "_drag_action", "") or "")
        if action == "create":
            return _bounds_center(bounds)
        if action in {"move", "center"}:
            return self._drag_orig_center_x + dx, self._drag_orig_center_y + dy
        orig_bounds_center = (
            self._drag_orig_x + self._drag_orig_w * 0.5,
            self._drag_orig_y + self._drag_orig_h * 0.5,
        )
        new_bounds_center = _bounds_center(bounds)
        return (
            self._drag_orig_center_x + new_bounds_center[0] - orig_bounds_center[0],
            self._drag_orig_center_y + new_bounds_center[1] - orig_bounds_center[1],
        )

    def _finish_drag(self, context) -> None:
        obj, layer = self._drag_target(context)
        moved = bool(getattr(self, "_drag_moved", False))
        action = self._drag_action
        if action == "create" and not moved:
            _delete_effect_layer(context, obj, layer)
        elif moved:
            self._push_undo_step("B-Name: 効果線編集")
            layer_stack_utils.sync_layer_stack_after_data_change(context)
        else:
            layer_stack_utils.tag_view3d_redraw(context)
        self._clear_drag_state()

    def _cancel_drag(self, context) -> None:
        obj, layer = self._drag_target(context)
        if obj is not None and layer is not None:
            if self._drag_action == "create":
                _delete_effect_layer(context, obj, layer)
            else:
                bounds = (
                    self._drag_orig_x,
                    self._drag_orig_y,
                    self._drag_orig_w,
                    self._drag_orig_h,
                )
                _write_effect_strokes(
                    context,
                    obj,
                    layer,
                    bounds,
                    center_xy_mm=(self._drag_orig_center_x, self._drag_orig_center_y),
                )
                _select_effect_layer(context, obj, layer)
        self._clear_drag_state()

    def _push_undo_step(self, message: str) -> None:
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("effect_line_tool: undo_push failed")

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        self._clear_drag_state()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        coma_modal_state.clear_active("effect_line_tool", self, context)


_CLASSES = (
    BNAME_OT_effect_line_generate,
    BNAME_OT_effect_line_tool,
)


def register() -> None:
    from ..core.effect_line import BNameEffectLineParams

    bpy.types.Scene.bname_effect_line_params = bpy.props.PointerProperty(
        type=BNameEffectLineParams
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.Scene.bname_effect_line_params
    except AttributeError:
        pass
