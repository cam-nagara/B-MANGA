"""B-MANGA Line オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty

from .core import AOV_NAME, PROP_LINES_HIDDEN, has_line, has_outline


class BMANGA_LINE_OT_apply(bpy.types.Operator):
    """選択オブジェクトにアウトラインを適用"""

    bl_idname = "bmanga_line.apply"
    bl_label = "ラインを適用"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        from .presets import apply_line_settings
        from . import outline_setup

        outline_setup.ensure_aov_passes(context.scene)

        count = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            if apply_line_settings(obj, context):
                count += 1

        self.report({"INFO"}, f"{count} オブジェクトにラインを適用しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_remove(bpy.types.Operator):
    """選択オブジェクトからラインを削除"""

    bl_idname = "bmanga_line.remove"
    bl_label = "ラインを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(has_line(obj) for obj in context.selected_objects)

    def execute(self, context):
        from . import intersection_lines, outline_setup, inner_lines
        from .core import (
            PROP_BASE_THICKNESS,
            PROP_REF_DISTANCE,
            PROP_REF_FOV_TAN,
            PROP_REF_MODE,
        )

        count = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            removed_any = False
            removed_any |= outline_setup.remove_outline(obj)
            removed_any |= inner_lines.remove_inner_lines(obj)
            removed_any |= intersection_lines.remove_intersection_lines(obj)
            if removed_any:
                count += 1
            for key in (
                PROP_BASE_THICKNESS,
                PROP_REF_DISTANCE,
                PROP_REF_FOV_TAN,
                PROP_REF_MODE,
            ):
                if key in obj:
                    del obj[key]
            if PROP_LINES_HIDDEN in obj:
                del obj[PROP_LINES_HIDDEN]

        self.report({"INFO"}, f"{count} オブジェクトからラインを削除しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_set_visibility(bpy.types.Operator):
    """選択オブジェクトのライン表示を切り替え"""

    bl_idname = "bmanga_line.set_visibility"
    bl_label = "ライン表示を切り替え"
    bl_options = {"REGISTER", "UNDO"}

    visible: BoolProperty(default=True)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(has_line(obj) for obj in context.selected_objects)

    def execute(self, context):
        from .core import set_line_visibility

        count = 0
        for obj in context.selected_objects:
            if set_line_visibility(obj, self.visible):
                count += 1
        action = "表示" if self.visible else "非表示"
        self.report({"INFO"}, f"{count} オブジェクトのラインを{action}にしました")
        return {"FINISHED"}


class BMANGA_LINE_OT_set_line_only(bpy.types.Operator):
    """選択オブジェクトをラインのみ表示に切り替え"""

    bl_idname = "bmanga_line.set_line_only"
    bl_label = "ラインのみ表示を切り替え"
    bl_options = {"REGISTER", "UNDO"}

    line_only: BoolProperty(default=True)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(has_line(obj) for obj in context.selected_objects)

    def execute(self, context):
        from . import outline_setup

        count = 0
        failed = 0
        for obj in context.selected_objects:
            if not has_line(obj):
                continue
            try:
                if outline_setup.set_line_only(obj, self.line_only):
                    count += 1
            except Exception:
                failed += 1
        if failed:
            self.report({"WARNING"}, f"{failed} オブジェクトは素材を変更できませんでした")
        action = "ラインのみ表示" if self.line_only else "通常表示"
        self.report({"INFO"}, f"{count} オブジェクトを{action}にしました")
        return {"FINISHED"}


class BMANGA_LINE_OT_sync_weights(bpy.types.Operator):
    """全ソースから頂点ウェイトを再計算"""

    bl_idname = "bmanga_line.sync_weights"
    bl_label = "ウェイトを更新"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(has_outline(obj) for obj in context.selected_objects)

    def execute(self, context):
        from . import vertex_analysis

        total = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            total += vertex_analysis.compute_and_apply_weights(
                obj, obj.bmanga_line_settings
            )

        self.report({"INFO"}, f"{total} 頂点のウェイトを更新しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_bake_ao(bpy.types.Operator):
    """Cycles で AO を頂点カラーに焼き付け"""

    bl_idname = "bmanga_line.bake_ao"
    bl_label = "AOを焼き付け"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "OBJECT"
            and any(obj.type == "MESH" for obj in context.selected_objects)
        )

    def execute(self, context):
        from . import vertex_analysis

        prev_active = context.view_layer.objects.active
        meshes = [o for o in context.selected_objects if o.type == "MESH"]

        count = 0
        for obj in meshes:
            context.view_layer.objects.active = obj
            ok = vertex_analysis.bake_ao(context, obj)
            if ok:
                count += 1

        context.view_layer.objects.active = prev_active

        if count > 0:
            for obj in meshes:
                settings = obj.bmanga_line_settings
                if settings.use_ao_influence:
                    vertex_analysis.compute_and_apply_weights(obj, settings)

        self.report({"INFO"}, f"{count} オブジェクトにAOを焼き付けました")
        return {"FINISHED"}


class BMANGA_LINE_OT_refresh_camera(bpy.types.Operator):
    """現在のカメラ位置でライン幅を再計算"""

    bl_idname = "bmanga_line.refresh_camera"
    bl_label = "カメラ補正を更新"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import camera_comp

        return camera_comp.get_line_camera(context.scene) is not None

    def execute(self, context):
        from . import camera_comp

        camera_comp.refresh(context)
        self.report({"INFO"}, "カメラ補正を更新しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_reset_camera_ref(bpy.types.Operator):
    """現在のカメラ距離を基準にリセット"""

    bl_idname = "bmanga_line.reset_camera_ref"
    bl_label = "基準距離をリセット"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import camera_comp

        return (
            camera_comp.get_line_camera(context.scene) is not None
            and any(has_outline(obj) for obj in context.selected_objects)
        )

    def execute(self, context):
        from . import camera_comp

        count = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            if camera_comp.store_reference(obj, context.scene):
                count += 1

        self.report({"INFO"}, f"{count} オブジェクトの基準距離をリセットしました")
        return {"FINISHED"}


class BMANGA_LINE_OT_add_aov(bpy.types.Operator):
    """ビューレイヤーに BML_Line AOV パスを追加"""

    bl_idname = "bmanga_line.add_aov"
    bl_label = "AOVパスを追加"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import outline_setup

        added = outline_setup.ensure_aov_passes(context.scene)
        if added:
            self.report({"INFO"}, f"AOV '{AOV_NAME}' を追加しました")
        else:
            self.report({"INFO"}, f"AOV '{AOV_NAME}' は既に存在します")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_LINE_OT_apply,
    BMANGA_LINE_OT_remove,
    BMANGA_LINE_OT_set_visibility,
    BMANGA_LINE_OT_set_line_only,
    BMANGA_LINE_OT_sync_weights,
    BMANGA_LINE_OT_bake_ao,
    BMANGA_LINE_OT_refresh_camera,
    BMANGA_LINE_OT_reset_camera_ref,
    BMANGA_LINE_OT_add_aov,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
