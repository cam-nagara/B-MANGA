"""効果線の基準パス編集操作."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import effect_line_path
from ..utils import layer_stack as layer_stack_utils
from ..utils import log

_logger = log.get_logger(__name__)


class BMANGA_OT_effect_line_base_path_edit(Operator):
    bl_idname = "bmanga.effect_line_base_path_edit"
    bl_label = "基準パスを編集"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        try:
            from . import effect_line_op

            obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
            return obj is not None and layer is not None and bounds is not None
        except Exception:  # noqa: BLE001
            return False

    def execute(self, context):
        try:
            source = self._ensure_source(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("effect base path edit failed")
            self.report({"ERROR"}, f"基準パスを開けません: {exc}")
            return {"CANCELLED"}
        if source is None:
            self.report({"WARNING"}, "基準パスを作成できませんでした")
            return {"CANCELLED"}
        self._select_source(context, source)
        self.report({"INFO"}, "基準パスを選択しました")
        return {"FINISHED"}

    def _ensure_source(self, context):
        from . import effect_line_op

        obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
        if obj is None or layer is None or bounds is None:
            return None
        params = getattr(context.scene, "bmanga_effect_line_params", None)
        if params is not None:
            effect_line_op._set_scene_params_syncing(context.scene, True)
            try:
                params.base_path_enabled = True
            finally:
                effect_line_op._set_scene_params_syncing(context.scene, False)
        effect_line_op._write_effect_strokes(context, obj, layer, bounds, params_override=params)
        layer_stack_utils.tag_view3d_redraw(context)
        return effect_line_path.find_effect_base_path_object(obj)

    def _select_source(self, context, source: bpy.types.Object) -> None:
        try:
            if getattr(context, "active_object", None) is not None:
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:  # noqa: BLE001
            pass
        for obj in getattr(context, "selected_objects", []) or []:
            obj.select_set(False)
        source.hide_viewport = False
        source.hide_select = False
        source.select_set(True)
        context.view_layer.objects.active = source
        try:
            bpy.ops.object.mode_set(mode="EDIT")
        except Exception:  # noqa: BLE001
            pass


_CLASSES = (BMANGA_OT_effect_line_base_path_edit,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
