"""Free-transform commands."""

from __future__ import annotations

import bpy
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
    free_transform.set_entry_offsets(entry, free_transform.zero_offsets(), enabled=False)
    return changed


class BNAME_OT_reset_free_transform(Operator):
    bl_idname = "bname.reset_free_transform"
    bl_label = "自由変形をリセット"
    bl_description = "選択中のフキダシ、テキスト、効果線の自由変形を元の矩形に戻します"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_stack_kind(context) in {"balloon", "text", "effect", "effect_legacy"}

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
        elif kind in {"effect", "effect_legacy"} and target is not None:
            from . import effect_line_op

            obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
            if obj is None or layer is None or bounds is None:
                obj, layer = layer_stack_utils._find_effect_layer_by_key(getattr(target, "name", target))
                bounds = effect_line_op.effect_layer_bounds(obj, layer)
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


_CLASSES = (BNAME_OT_reset_free_transform,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
