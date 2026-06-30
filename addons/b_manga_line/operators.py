"""B-MANGA Line オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty

from . import registration
from .core import AOV_NAME, PROP_LINES_HIDDEN, PROP_LINE_ONLY, has_line, has_outline


def _is_linked_line_object(obj: bpy.types.Object) -> bool:
    data = getattr(obj, "data", None)
    return (
        obj.type == "MESH"
        and has_line(obj)
        and (
            obj.library is not None
            or getattr(data, "library", None) is not None
            or getattr(obj, "override_library", None) is not None
            or getattr(data, "override_library", None) is not None
        )
    )


def _linked_line_objects(scene) -> list[bpy.types.Object]:
    return [obj for obj in scene.objects if _is_linked_line_object(obj)]


class BMANGA_LINE_OT_apply(bpy.types.Operator):
    """選択オブジェクトにアウトラインを適用"""

    bl_idname = "bmanga_line.apply"
    bl_label = "ラインを適用"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        from .presets import (
            apply_line_settings,
            _refresh_after_line_settings,
            _update_view_layer,
        )
        from . import outline_setup

        outline_setup.ensure_aov_passes(context.scene)

        count = 0
        _update_view_layer(context)
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            if apply_line_settings(
                obj,
                context,
                refresh_scene=False,
                transforms_fresh=True,
            ):
                count += 1
        _refresh_after_line_settings(context)

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
        from . import intersection_lines, outline_setup, inner_lines, plane_filter
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
            plane_filter.clear_cache(obj)

        intersection_lines.refresh_scene_intersections(context.scene)
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
        from . import camera_comp

        count = 0
        for obj in context.selected_objects:
            if set_line_visibility(obj, self.visible):
                count += 1
        if self.visible:
            camera_comp.refresh(context)
        action = "表示" if self.visible else "非表示"
        self.report({"INFO"}, f"{count} オブジェクトのラインを{action}にしました")
        return {"FINISHED"}


class BMANGA_LINE_OT_set_line_only(bpy.types.Operator):
    """一時マテリアル差し替えでラインのみ表示に切り替え"""

    bl_idname = "bmanga_line.set_line_only"
    bl_label = "ラインのみ表示を切り替え"
    bl_options = {"REGISTER", "UNDO"}

    line_only: BoolProperty(default=True)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(has_line(obj) for obj in context.selected_objects)

    def execute(self, context):
        from . import outline_setup, viewport_aov
        from .core import set_line_visibility

        changed_objects: set[int] = set()
        failed = 0
        line_objects = [obj for obj in context.selected_objects if has_line(obj)]

        if self.line_only:
            viewport_aov.disable_line_aov(context)
            for obj in line_objects:
                set_line_visibility(obj, True)
            for obj in line_objects:
                try:
                    if outline_setup.set_line_only(obj, True):
                        changed_objects.add(obj.as_pointer())
                except Exception:
                    failed += 1
        else:
            if viewport_aov.disable_line_aov(context):
                changed_objects.update(obj.as_pointer() for obj in line_objects)
            for obj in line_objects:
                if not bool(obj.get(PROP_LINE_ONLY, False)):
                    continue
                try:
                    if outline_setup.set_line_only(obj, False):
                        changed_objects.add(obj.as_pointer())
                except Exception:
                    failed += 1
        if failed:
            self.report({"WARNING"}, f"{failed} オブジェクトは素材を変更できませんでした")
        action = "ラインのみ表示" if self.line_only else "通常表示"
        self.report({"INFO"}, f"{len(changed_objects)} オブジェクトを{action}にしました")
        return {"FINISHED"}


class BMANGA_LINE_OT_refresh_linked(bpy.types.Operator):
    """リンク読み込み素材のラインを現在のコマカメラで再補正"""

    bl_idname = "bmanga_line.refresh_linked"
    bl_label = "リンク素材のラインを補正"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_linked_line_objects(context.scene))

    def execute(self, context):
        from . import camera_comp, outline_setup

        linked = _linked_line_objects(context.scene)
        outline_setup.ensure_aov_passes(context.scene)
        camera_comp.refresh(context)
        self.report({"INFO"}, f"{len(linked)} オブジェクトのラインを補正しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_apply_active_to_linked(bpy.types.Operator):
    """アクティブオブジェクトのライン設定をリンク読み込み素材へ適用"""

    bl_idname = "bmanga_line.apply_active_to_linked"
    bl_label = "リンク素材へ選択設定を上書き"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and has_line(obj) and bool(_linked_line_objects(context.scene))

    def execute(self, context):
        from . import presets

        source = context.active_object
        if source is None or not has_line(source):
            self.report({"WARNING"}, "ライン設定のあるオブジェクトを選択してください")
            return {"CANCELLED"}

        linked = _linked_line_objects(context.scene)
        applied = 0
        failed = 0
        source_settings = source.bmanga_line_settings
        presets._update_view_layer(context)
        for obj in linked:
            try:
                presets.copy_settings_to_settings(source_settings, obj.bmanga_line_settings)
                if presets.apply_line_settings(
                    obj,
                    context,
                    refresh_scene=False,
                    transforms_fresh=True,
                ):
                    applied += 1
                else:
                    failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
        presets._refresh_after_line_settings(context)

        if failed:
            self.report(
                {"WARNING"},
                f"{failed} オブジェクトは上書きできませんでした。ライブラリオーバーライドが必要な可能性があります",
            )
        self.report({"INFO"}, f"{applied} オブジェクトへライン設定を上書きしました")
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
            settings = obj.bmanga_line_settings
            for target in ("outline", "inner", "intersection"):
                total += vertex_analysis.compute_and_apply_weights(obj, settings, target)

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
                    vertex_analysis.compute_and_apply_weights(obj, settings, "outline")

        self.report({"INFO"}, f"{count} オブジェクトにAOを焼き付けました")
        return {"FINISHED"}


class BMANGA_LINE_OT_refresh_camera(bpy.types.Operator):
    """現在のカメラ位置でライン幅を再計算"""

    bl_idname = "bmanga_line.refresh_camera"
    bl_label = "線幅を更新"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import camera_comp

        return camera_comp.get_line_camera(context.scene) is not None

    def execute(self, context):
        from . import camera_comp

        camera_comp.refresh(context)
        self.report({"INFO"}, "線幅を更新しました")
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
    BMANGA_LINE_OT_refresh_linked,
    BMANGA_LINE_OT_apply_active_to_linked,
    BMANGA_LINE_OT_sync_weights,
    BMANGA_LINE_OT_bake_ao,
    BMANGA_LINE_OT_refresh_camera,
    BMANGA_LINE_OT_reset_camera_ref,
    BMANGA_LINE_OT_add_aov,
)


def register() -> None:
    for cls in _CLASSES:
        registration.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
