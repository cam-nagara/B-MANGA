"""B-MANGA Line オペレーター."""

from __future__ import annotations

import bpy

from .core import AOV_NAME, get_settings, has_outline


class BMANGA_LINE_OT_apply(bpy.types.Operator):
    """選択オブジェクトにアウトラインを適用"""

    bl_idname = "bmanga_line.apply"
    bl_label = "ラインを適用"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        from . import outline_setup, inner_lines, camera_comp, vertex_analysis

        settings = get_settings(context)
        if settings is None:
            self.report({"ERROR"}, "設定が見つかりません")
            return {"CANCELLED"}

        use_vg = (
            settings.use_vertex_color
            or settings.use_ao_influence
            or abs(settings.edge_smooth_factor) > 0.001
        )

        count = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            ok = outline_setup.apply_outline(
                obj,
                thickness=settings.outline_thickness,
                color=tuple(settings.outline_color),
                use_vertex_color=settings.use_vertex_color,
                even_thickness=settings.even_thickness,
                use_vertex_group=use_vg,
                scene=context.scene,
            )
            if not ok:
                continue
            count += 1

            if settings.inner_line_enabled:
                inner_lines.apply_inner_lines(
                    obj,
                    angle=settings.inner_line_angle,
                    thickness=settings.inner_line_thickness,
                )
            else:
                inner_lines.remove_inner_lines(obj)

        # カメラ補正が有効なら基準値を保存
        if settings.use_camera_compensation:
            for obj in context.selected_objects:
                if obj.type == "MESH":
                    camera_comp.store_reference(obj, context.scene)

        # 頂点グループが必要な場合はウェイトを初期計算
        if use_vg:
            for obj in context.selected_objects:
                if obj.type == "MESH":
                    vertex_analysis.compute_and_apply_weights(obj, settings)

        self.report({"INFO"}, f"{count} オブジェクトにラインを適用しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_remove(bpy.types.Operator):
    """選択オブジェクトからアウトラインを削除"""

    bl_idname = "bmanga_line.remove"
    bl_label = "ラインを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(has_outline(obj) for obj in context.selected_objects)

    def execute(self, context):
        from . import outline_setup, inner_lines
        from .core import PROP_BASE_THICKNESS, PROP_REF_DISTANCE

        count = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            removed_outline = outline_setup.remove_outline(obj)
            inner_lines.remove_inner_lines(obj)
            if removed_outline:
                count += 1
            # カメラ補正のカスタムプロパティを削除
            for key in (PROP_BASE_THICKNESS, PROP_REF_DISTANCE):
                if key in obj:
                    del obj[key]

        self.report({"INFO"}, f"{count} オブジェクトからラインを削除しました")
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

        settings = get_settings(context)
        if settings is None:
            return {"CANCELLED"}

        total = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            total += vertex_analysis.compute_and_apply_weights(obj, settings)

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

        settings = get_settings(context)
        prev_active = context.view_layer.objects.active
        meshes = [o for o in context.selected_objects if o.type == "MESH"]

        count = 0
        for obj in meshes:
            context.view_layer.objects.active = obj
            ok = vertex_analysis.bake_ao(context, obj)
            if ok:
                count += 1

        context.view_layer.objects.active = prev_active

        # 焼き付け後にウェイトを再計算
        if count > 0 and settings is not None and settings.use_ao_influence:
            for obj in meshes:
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
        settings = get_settings(context)
        return (
            settings is not None
            and settings.use_camera_compensation
            and context.scene.camera is not None
        )

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
        return (
            context.scene.camera is not None
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

        added = outline_setup.ensure_aov_pass(context.view_layer)
        if added:
            self.report({"INFO"}, f"AOV '{AOV_NAME}' を追加しました")
        else:
            self.report({"INFO"}, f"AOV '{AOV_NAME}' は既に存在します")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_LINE_OT_apply,
    BMANGA_LINE_OT_remove,
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
