"""Free-transform commands."""

from __future__ import annotations

import math

import bpy
from bpy.props import BoolProperty, FloatProperty
from bpy.types import Operator

from ..utils import balloon_curve_object, free_transform, layer_stack as layer_stack_utils, text_real_object


def _active_stack_kind(context) -> str:
    item = layer_stack_utils.active_stack_item(context)
    return str(getattr(item, "kind", "") or "")


def _active_stack_target(context):
    item = layer_stack_utils.active_stack_item(context)
    resolved = layer_stack_utils.resolve_stack_item(context, item) if item is not None else None
    return resolved.get("target") if resolved else None, resolved


def _reset_entry_transform(entry) -> bool:
    if entry is None:
        return False
    changed = bool(getattr(entry, "free_transform_enabled", False)) or not free_transform.offsets_are_zero(
        free_transform.entry_offsets(entry)
    )
    if abs(float(getattr(entry, "free_transform_line_width_scale", 1.0) or 1.0) - 1.0) > 1.0e-6:
        changed = True
        entry.free_transform_line_width_scale = 1.0
    free_transform.set_entry_offsets(entry, free_transform.zero_offsets(), enabled=False)
    return changed


def _balloon_base_corner_points(entry) -> dict[str, tuple[float, float]]:
    width = max(0.1, float(getattr(entry, "width_mm", 0.0) or 0.0))
    height = max(0.1, float(getattr(entry, "height_mm", 0.0) or 0.0))
    return {
        free_transform.BOTTOM_LEFT: (0.0, 0.0),
        free_transform.BOTTOM_RIGHT: (width, 0.0),
        free_transform.TOP_RIGHT: (width, height),
        free_transform.TOP_LEFT: (0.0, height),
    }


def _balloon_pivot(entry) -> tuple[float, float]:
    width = max(0.1, float(getattr(entry, "width_mm", 0.0) or 0.0))
    height = max(0.1, float(getattr(entry, "height_mm", 0.0) or 0.0))
    return (
        width * 0.5 + float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
        height * 0.5 + float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
    )


def _set_balloon_transformed_corners(entry, transform) -> None:
    base = _balloon_base_corner_points(entry)
    offsets = free_transform.entry_offsets(entry)
    new_offsets: dict[str, tuple[float, float]] = {}
    for corner in free_transform.CORNERS:
        bx, by = base[corner]
        ox, oy = offsets.get(corner, (0.0, 0.0))
        nx, ny = transform(bx + float(ox), by + float(oy))
        new_offsets[corner] = (float(nx) - bx, float(ny) - by)
    free_transform.set_entry_offsets(entry, new_offsets, enabled=True)


def _transform_balloon_tails(entry, transform) -> None:
    """しっぽのポイントを同じ変換で追従させる."""
    for tail in getattr(entry, "tails", []) or []:
        for point in getattr(tail, "points", []) or []:
            ox = float(getattr(point, "x_mm", 0.0) or 0.0)
            oy = float(getattr(point, "y_mm", 0.0) or 0.0)
            nx, ny = transform(ox, oy)
            point.x_mm = float(nx)
            point.y_mm = float(ny)
        if bool(getattr(tail, "custom_points_enabled", False)):
            sx, sy = transform(
                float(getattr(tail, "start_x_mm", 0.0) or 0.0),
                float(getattr(tail, "start_y_mm", 0.0) or 0.0),
            )
            tail.start_x_mm = float(sx)
            tail.start_y_mm = float(sy)
            ex, ey = transform(
                float(getattr(tail, "end_x_mm", 0.0) or 0.0),
                float(getattr(tail, "end_y_mm", 0.0) or 0.0),
            )
            tail.end_x_mm = float(ex)
            tail.end_y_mm = float(ey)


def _sync_balloon_after_transform(context, page, entry) -> None:
    balloon_curve_object.on_balloon_entry_changed(entry)
    try:
        from . import balloon_op

        balloon_op._sync_balloon_merge_display_if_needed(page, entry)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import layer_link_duplicate_op

        layer_link_duplicate_op.propagate_linked_balloon_center_free(context, page, entry)
    except Exception:  # noqa: BLE001
        pass


class BMANGA_OT_free_transform_mode(Operator):
    bl_idname = "bmanga.free_transform_mode"
    bl_label = "自由変形"
    bl_description = (
        "四隅のハンドルをドラッグして形を自由に歪められるようにします"
        " (別のオブジェクトを選択すると通常操作に戻ります)"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _active_stack_kind(context) in {"balloon", "effect"}

    def execute(self, context):
        from . import object_tool_selection

        key = object_tool_selection.active_selection_key(context)
        if not key:
            self.report({"WARNING"}, "自由変形する対象が選択されていません")
            return {"CANCELLED"}
        wm = context.window_manager
        if hasattr(wm, "bmanga_free_transform_key"):
            wm.bmanga_free_transform_key = key
        layer_stack_utils.tag_view3d_redraw(context)
        self.report({"INFO"}, "自由変形: 四隅のハンドルをドラッグして変形 (別のオブジェクトを選択で終了)")
        return {"FINISHED"}


class BMANGA_OT_reset_free_transform(Operator):
    bl_idname = "bmanga.reset_free_transform"
    bl_label = "自由変形をリセット"
    bl_description = "選択中のフキダシ、テキスト、効果線の自由変形を元の矩形に戻します"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_stack_kind(context) in {"balloon", "text", "effect"}

    def execute(self, context):
        kind = _active_stack_kind(context)
        target, resolved = _active_stack_target(context)
        changed = False
        if kind == "balloon" and target is not None:
            page = resolved.get("page") if resolved else None
            with balloon_curve_object.suspend_auto_sync():
                changed = _reset_entry_transform(target)
            balloon_curve_object.on_balloon_entry_changed(target)
            try:
                from . import balloon_op

                balloon_op._sync_balloon_merge_display_if_needed(page, target)
            except Exception:  # noqa: BLE001
                pass
            try:
                from . import layer_link_duplicate_op

                layer_link_duplicate_op.propagate_linked_balloon_center_free(context, page, target)
            except Exception:  # noqa: BLE001
                pass
        elif kind == "text" and target is not None:
            page = resolved.get("page") if resolved else None
            with text_real_object.suspend_auto_sync():
                changed = _reset_entry_transform(target)
            text_real_object.on_text_free_transform_changed(target)
            if page is not None:
                try:
                    from . import coma_modal_state

                    active = coma_modal_state.get_active("text_tool")
                    editing_same_text = (
                        active is not None
                        and bool(getattr(active, "_editing", False))
                        and str(getattr(active, "_page_id", "") or "") == str(getattr(page, "id", "") or "")
                        and str(getattr(active, "_text_id", "") or "") == str(getattr(target, "id", "") or "")
                    )
                except Exception:  # noqa: BLE001
                    editing_same_text = False
                text_real_object.set_text_object_preview_hidden(target, page=page, hidden=editing_same_text)
        elif kind == "effect" and target is not None:
            from . import effect_line_op

            obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
            if obj is not None and layer is not None and bounds is not None:
                meta = effect_line_op._effect_meta(obj)
                key = effect_line_op._layer_meta_key(layer)
                entry = dict(meta.get(key, {}) if isinstance(meta.get(key, {}), dict) else {})
                payload = free_transform.effect_payload_from_meta_entry(entry)
                changed = free_transform.effect_payload_enabled(payload)
                free_transform.set_effect_payload_on_meta_entry(
                    entry,
                    {"enabled": False, "offsets": free_transform.zero_offsets()},
                )
                meta[key] = entry
                effect_line_op._write_effect_meta(obj, meta)
                effect_line_op._write_effect_strokes(context, obj, layer, bounds)
                effect_line_op._select_effect_layer(context, obj, layer)
        if changed:
            layer_stack_utils.sync_layer_stack_after_data_change(context)
            return {"FINISHED"}
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BMANGA_OT_balloon_free_transform_scale(Operator):
    bl_idname = "bmanga.balloon_free_transform_scale"
    bl_label = "拡大・縮小"
    bl_description = "選択中のフキダシを、形状を保ったまま自由変形で拡大・縮小します"
    bl_options = {"REGISTER", "UNDO"}

    scale_percent: FloatProperty(  # type: ignore[valid-type]
        name="倍率 (%)",
        default=120.0,
        min=1.0,
        soft_min=10.0,
        soft_max=400.0,
        subtype="PERCENTAGE",
    )
    keep_line_width: BoolProperty(name="線幅を維持", default=True)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _active_stack_kind(context) == "balloon"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        target, resolved = _active_stack_target(context)
        if target is None:
            return {"CANCELLED"}
        factor = max(0.01, float(self.scale_percent) / 100.0)
        page = resolved.get("page") if resolved else None
        px, py = _balloon_pivot(target)

        def _scale_point(x: float, y: float) -> tuple[float, float]:
            return px + (float(x) - px) * factor, py + (float(y) - py) * factor

        with balloon_curve_object.suspend_auto_sync():
            _set_balloon_transformed_corners(target, _scale_point)
            _transform_balloon_tails(target, _scale_point)
            if not bool(self.keep_line_width) and hasattr(target, "free_transform_line_width_scale"):
                current = max(0.01, float(getattr(target, "free_transform_line_width_scale", 1.0) or 1.0))
                target.free_transform_line_width_scale = max(0.01, current * factor)
        _sync_balloon_after_transform(context, page, target)
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BMANGA_OT_balloon_free_transform_rotate(Operator):
    bl_idname = "bmanga.balloon_free_transform_rotate"
    bl_label = "回転"
    bl_description = "選択中のフキダシを、形状を保ったまま自由変形で回転します"
    bl_options = {"REGISTER", "UNDO"}

    angle_deg: FloatProperty(name="角度", default=15.0, soft_min=-180.0, soft_max=180.0)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _active_stack_kind(context) == "balloon"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        target, resolved = _active_stack_target(context)
        if target is None:
            return {"CANCELLED"}
        page = resolved.get("page") if resolved else None
        px, py = _balloon_pivot(target)
        radians = math.radians(float(self.angle_deg))
        cos_v = math.cos(radians)
        sin_v = math.sin(radians)

        def _rotate_point(x: float, y: float) -> tuple[float, float]:
            dx = float(x) - px
            dy = float(y) - py
            return px + dx * cos_v - dy * sin_v, py + dx * sin_v + dy * cos_v

        with balloon_curve_object.suspend_auto_sync():
            _set_balloon_transformed_corners(target, _rotate_point)
            _transform_balloon_tails(target, _rotate_point)
        _sync_balloon_after_transform(context, page, target)
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BMANGA_OT_balloon_free_transform_scale_rotate(Operator):
    bl_idname = "bmanga.balloon_free_transform_scale_rotate"
    bl_label = "拡大・縮小・回転"
    bl_description = "選択中のフキダシを、形状を保ったまま拡大・縮小・回転します"
    bl_options = {"REGISTER", "UNDO"}

    scale_percent: FloatProperty(  # type: ignore[valid-type]
        name="倍率 (%)",
        default=100.0,
        min=1.0,
        soft_min=10.0,
        soft_max=400.0,
        subtype="PERCENTAGE",
    )
    angle_deg: FloatProperty(name="角度", default=0.0, soft_min=-180.0, soft_max=180.0)  # type: ignore[valid-type]
    keep_line_width: BoolProperty(name="線幅を維持", default=True)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _active_stack_kind(context) == "balloon"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        target, resolved = _active_stack_target(context)
        if target is None:
            return {"CANCELLED"}
        factor = max(0.01, float(self.scale_percent) / 100.0)
        page = resolved.get("page") if resolved else None
        px, py = _balloon_pivot(target)
        radians = math.radians(float(self.angle_deg))
        cos_v = math.cos(radians)
        sin_v = math.sin(radians)

        def _transform_point(x: float, y: float) -> tuple[float, float]:
            dx = (float(x) - px) * factor
            dy = (float(y) - py) * factor
            return px + dx * cos_v - dy * sin_v, py + dx * sin_v + dy * cos_v

        with balloon_curve_object.suspend_auto_sync():
            _set_balloon_transformed_corners(target, _transform_point)
            _transform_balloon_tails(target, _transform_point)
            if not bool(self.keep_line_width) and hasattr(target, "free_transform_line_width_scale"):
                current = max(0.01, float(getattr(target, "free_transform_line_width_scale", 1.0) or 1.0))
                target.free_transform_line_width_scale = max(0.01, current * factor)
        _sync_balloon_after_transform(context, page, target)
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_free_transform_mode,
    BMANGA_OT_reset_free_transform,
    BMANGA_OT_balloon_free_transform_scale,
    BMANGA_OT_balloon_free_transform_rotate,
    BMANGA_OT_balloon_free_transform_scale_rotate,
)


def register() -> None:
    bpy.types.WindowManager.bmanga_free_transform_key = bpy.props.StringProperty(default="")
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.WindowManager.bmanga_free_transform_key
    except AttributeError:
        pass
