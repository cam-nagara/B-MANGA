"""B-MANGA Line オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import registration
from .core import (
    AOV_COMPOSITE_NAME,
    AOV_NAMES,
    PROP_LINES_HIDDEN,
    has_line,
    has_outline,
    record_override_edits,
)


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
    objs = [obj for obj in scene.objects if _is_linked_line_object(obj)]
    # オーバーライドが存在するライブラリ元は除外する。元にも書き込むと
    # オーバーライドと参照の差分が消え、上書きが保存時に破棄されてしまう。
    refs = {
        o.override_library.reference
        for o in objs
        if getattr(o, "override_library", None) is not None
        and o.override_library.reference is not None
    }
    return [o for o in objs if o not in refs]


def _locked_skip_count(context, updatable_count: int) -> int:
    """選択中メッシュのうち、ロックのため対象から除外された件数."""
    total = sum(1 for obj in context.selected_objects if obj.type == "MESH")
    return max(0, total - updatable_count)


def _with_lock_skip_note(message: str, skipped: int) -> str:
    if skipped:
        return f"{message}（ロック中のため{skipped}件を除外）"
    return message


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
            _reflect_applied_display_settings,
            _refresh_after_line_settings,
            _update_view_layer,
        )
        from . import outline_setup, selection

        outline_setup.ensure_aov_passes(context.scene)

        targets = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets))

        count = 0
        applied_objects: list[bpy.types.Object] = []
        _update_view_layer(context)
        for obj in targets:
            if apply_line_settings(
                obj,
                context,
                refresh_scene=False,
                transforms_fresh=True,
            ):
                count += 1
                applied_objects.append(obj)
        _refresh_after_line_settings(context, sources=applied_objects)
        _reflect_applied_display_settings(applied_objects, context)
        from . import update_state
        update_state.clear_pending_many(applied_objects)

        self.report(
            {"INFO"},
            _with_lock_skip_note(f"{count} オブジェクトにラインを適用しました", skipped),
        )
        return {"FINISHED"}


_LINE_TARGET_ITEMS = (
    ("outline", "アウトライン", ""),
    ("inner", "稜谷線", ""),
    ("intersection", "交差線", ""),
    ("selection", "選択線", ""),
    # バンプ線はモディファイア/マテリアルを生成しないため「作成」ボタンは
    # 出さない（panels.py側で作成ボタンを描画しない）。更新オペレーター
    # (bmanga_line.update_visual_target) の対象としてのみ使う。
    ("bump", "バンプ線", ""),
)

_TARGET_ENABLED_PROPS = {
    "outline": "outline_enabled",
    "inner": "inner_line_enabled",
    "intersection": "intersection_enabled",
    "selection": "selection_line_enabled",
    "bump": "bump_line_enabled",
}


def _target_enabled(obj: bpy.types.Object, target: str) -> bool:
    settings = getattr(obj, "bmanga_line_settings", None)
    prop_name = _TARGET_ENABLED_PROPS.get(target)
    return bool(settings is not None and prop_name and getattr(settings, prop_name, False))


def _has_any_updatable_line_target(context) -> bool:
    """更新系オペレーターのpoll向け: 他線種のhas_line()に加え、
    バンプ線はモディファイアを持たないため bump_line_enabled も見る。
    直前に無効化しただけ（bump_line_enabled=False だが更新待ち印は残っている）
    のオブジェクトも対象にし、無効化を反映する「更新」を必ず押せるようにする。
    """
    from . import update_state

    for obj in context.selected_objects:
        if has_line(obj):
            return True
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is not None and bool(getattr(settings, "bump_line_enabled", False)):
            return True
        if "bump" in update_state.pending_visual_targets(obj):
            return True
    return False


class BMANGA_LINE_OT_update_target(bpy.types.Operator):
    """選択オブジェクトの指定ラインだけを作成・再作成"""

    bl_idname = "bmanga_line.update_target"
    bl_label = "ラインを作成"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(items=_LINE_TARGET_ITEMS, default="outline")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        from . import camera_comp, intersection_lines, outline_setup, selection, update_state
        from .presets import (
            apply_line_settings,
            _update_view_layer,
        )

        target = str(self.target)
        line_targets = (target,)
        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        count = 0
        applied_objects: list[bpy.types.Object] = []
        _update_view_layer(context)
        for obj in targets_to_process:
            if apply_line_settings(
                obj,
                context,
                refresh_scene=False,
                transforms_fresh=True,
                line_targets=line_targets,
            ):
                count += 1
                applied_objects.append(obj)

        if target == "intersection":
            refresh_sources = [
                obj for obj in applied_objects if _target_enabled(obj, target)
            ]
            intersection_targets = (
                intersection_lines.refresh_scene_intersections(
                    context.scene,
                    sources=refresh_sources,
                )
                if refresh_sources
                else []
            )
            if intersection_targets:
                camera_comp.refresh_objects(
                    context,
                    intersection_targets,
                    update_visibility=True,
                    width_targets=line_targets,
                    visibility_targets=line_targets,
                )
            if refresh_sources or intersection_targets:
                outline_setup.ensure_aov_passes(context.scene)
        else:
            refresh_objects = [
                obj for obj in applied_objects if _target_enabled(obj, target)
            ]
            if refresh_objects:
                camera_comp.refresh_objects(
                    context,
                    refresh_objects,
                    update_visibility=True,
                    width_targets=line_targets,
                    visibility_targets=line_targets,
                )
        update_state.clear_pending_many(applied_objects, line_targets)

        labels = {
            "outline": "アウトライン",
            "inner": "稜谷線",
            "intersection": "交差線",
            "selection": "選択線",
        }
        self.report(
            {"INFO"},
            _with_lock_skip_note(
                f"{count} オブジェクトの{labels.get(target, 'ライン')}を作成しました",
                skipped,
            ),
        )
        return {"FINISHED"}


class BMANGA_LINE_OT_update_visual_target(bpy.types.Operator):
    """選択オブジェクトの作成済みラインの見た目だけを更新"""

    bl_idname = "bmanga_line.update_visual_target"
    bl_label = "ラインを更新"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(items=_LINE_TARGET_ITEMS, default="outline")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _has_any_updatable_line_target(context)

    def execute(self, context):
        from . import batch_update, selection, update_state

        target = str(self.target)
        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        updated_objects = batch_update.refresh_target_visuals(
            target,
            targets_to_process,
            context,
        )
        update_state.clear_pending_many(
            updated_objects,
            (target,),
            kind="visual",
        )
        labels = {
            "outline": "アウトライン",
            "inner": "稜谷線",
            "intersection": "交差線",
            "selection": "選択線",
            "bump": "バンプ線",
        }
        self.report(
            {"INFO"},
            _with_lock_skip_note(
                f"{len(updated_objects)} オブジェクトの{labels.get(target, 'ライン')}を更新しました",
                skipped,
            ),
        )
        return {"FINISHED"}


class BMANGA_LINE_OT_update_all_visual_targets(bpy.types.Operator):
    """選択オブジェクトの作成済みライン全種と中間頂点用サブディビジョンを更新"""

    bl_idname = "bmanga_line.update_all_visual_targets"
    bl_label = "すべてのラインを更新"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _has_any_updatable_line_target(context)

    def execute(self, context):
        from . import batch_update, selection, update_state

        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        results = batch_update.refresh_all_target_visuals(
            targets_to_process,
            context,
        )
        updated: dict[str, bpy.types.Object] = {}
        for objects in results.values():
            for obj in objects:
                updated[obj.name_full] = obj
        # 全線種を更新済みのため、未作成線種も含め更新待ち表示を解消する
        update_state.clear_pending_many(updated.values(), kind="visual")
        self.report(
            {"INFO"},
            _with_lock_skip_note(
                f"{len(updated)} オブジェクトのすべてのラインを更新しました",
                skipped,
            ),
        )
        return {"FINISHED"}


class BMANGA_LINE_OT_select_render_range_meshes(bpy.types.Operator):
    """レンダリング範囲内のメッシュを選択"""

    bl_idname = "bmanga_line.select_render_range_meshes"
    bl_label = "レンダリング範囲内を選択"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import camera_comp

        return (
            context.mode == "OBJECT"
            and camera_comp.get_line_camera(context.scene) is not None
        )

    def execute(self, context):
        from . import camera_comp

        scene = context.scene
        camera = camera_comp.get_line_camera(scene)
        if camera is None:
            self.report({"WARNING"}, "カメラがありません")
            return {"CANCELLED"}

        targets = []
        for obj in scene.objects:
            if obj.type != "MESH" or obj.data is None:
                continue
            if getattr(obj, "hide_select", False):
                continue
            try:
                if not obj.visible_get():
                    continue
            except RuntimeError:
                continue
            if camera_comp.object_overlaps_camera_view(obj, scene, camera):
                targets.append(obj)

        bpy.ops.object.select_all(action="DESELECT")
        for obj in targets:
            obj.select_set(True)
        if targets:
            context.view_layer.objects.active = targets[0]

        self.report({"INFO"}, f"{len(targets)} オブジェクトを選択しました")
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
        from . import (
            intersection_lines,
            outline_setup,
            inner_lines,
            plane_filter,
            selection,
            selection_lines,
            subdivision_lod,
        )
        from .core import (
            PROP_BASE_THICKNESS,
            PROP_REF_DISTANCE,
            PROP_REF_FOV_TAN,
            PROP_REF_MODE,
        )

        # ロック中は解除してから削除する運用（誤爆防止を優先）。
        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))

        count = 0
        for obj in targets_to_process:
            removed_any = False
            removed_any |= outline_setup.remove_outline(obj)
            removed_any |= inner_lines.remove_inner_lines(obj)
            removed_any |= selection_lines.remove_selection_lines(obj)
            removed_any |= intersection_lines.remove_intersection_lines(obj)
            removed_any |= subdivision_lod.remove_auto_subdivision(obj)
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
        self.report(
            {"INFO"},
            _with_lock_skip_note(f"{count} オブジェクトからラインを削除しました", skipped),
        )
        return {"FINISHED"}


class BMANGA_LINE_OT_set_settings_lock(bpy.types.Operator):
    """選択オブジェクトのライン設定ロックを切り替え"""

    bl_idname = "bmanga_line.set_settings_lock"
    bl_label = "ライン設定ロックを切り替え"
    bl_options = {"REGISTER", "UNDO"}

    lock: BoolProperty(default=True)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        target_lock = bool(self.lock)
        count = 0
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            settings = getattr(obj, "bmanga_line_settings", None)
            if settings is None:
                continue
            if bool(settings.settings_locked) == target_lock:
                continue
            settings.settings_locked = target_lock
            record_override_edits(obj)
            count += 1

        action = "ロック" if target_lock else "ロック解除"
        self.report({"INFO"}, f"{count} オブジェクトを{action}しました")
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
        from .core import set_scene_line_only

        changed = set_scene_line_only(context, bool(self.line_only))
        action = "ラインのみ表示" if self.line_only else "通常表示"
        self.report({"INFO"}, f"{changed} マテリアルを{action}にしました")
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
        from . import presets, update_state

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
                update_state.mark_pending(obj)
                applied += 1
                record_override_edits(obj)
            except Exception:  # noqa: BLE001
                failed += 1

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
        from . import selection, vertex_analysis

        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))

        total = 0
        for obj in targets_to_process:
            settings = obj.bmanga_line_settings
            for target in ("outline", "inner", "intersection", "selection"):
                total += vertex_analysis.compute_and_apply_weights(obj, settings, target)

        self.report(
            {"INFO"},
            _with_lock_skip_note(f"{total} 頂点のウェイトを更新しました", skipped),
        )
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
    """選択オブジェクトの原点までの距離を線幅基準距離にする"""

    bl_idname = "bmanga_line.reset_camera_ref"
    bl_label = "選択原点までの距離に設定"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import camera_comp

        return (
            camera_comp.get_line_camera(context.scene) is not None
            and any(obj.type == "MESH" for obj in context.selected_objects)
        )

    def execute(self, context):
        from . import camera_comp, core

        camera = camera_comp.get_line_camera(context.scene)
        if camera is None:
            self.report({"WARNING"}, "カメラがありません")
            return {"CANCELLED"}

        targets = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not targets:
            self.report({"WARNING"}, "メッシュオブジェクトを選択してください")
            return {"CANCELLED"}

        source = context.active_object if context.active_object in targets else targets[0]
        distance = max(
            0.001,
            (camera.matrix_world.translation - source.matrix_world.translation).length,
        )
        old = core._propagating
        core._propagating = True
        try:
            for obj in targets:
                obj.bmanga_line_settings.line_width_reference_distance = distance
                if has_outline(obj):
                    camera_comp.store_unit_reference(obj, context.scene)
        finally:
            core._propagating = old
        line_targets = [obj for obj in targets if has_outline(obj)]
        if line_targets:
            camera_comp.refresh_objects(context, line_targets)

        self.report({"INFO"}, f"{len(targets)} オブジェクトの線幅基準距離を更新しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_add_aov(bpy.types.Operator):
    """ビューレイヤーに B-MANGA Line AOV パスを追加"""

    bl_idname = "bmanga_line.add_aov"
    bl_label = "AOVパスを追加"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import outline_setup

        added = outline_setup.ensure_aov_passes(context.scene)
        if added:
            self.report({"INFO"}, f"{added} 個の線画AOVを追加しました")
        else:
            self.report({"INFO"}, f"線画AOVは既に存在します ({', '.join(AOV_NAMES)})")
        return {"FINISHED"}


class BMANGA_LINE_OT_setup_aov_composite(bpy.types.Operator):
    """線画だけを取り出す合成ノードを作成"""

    bl_idname = "bmanga_line.setup_aov_composite"
    bl_label = "線画合成ノードを作成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import aov_compositor, outline_setup

        outline_setup.ensure_aov_passes(context.scene)
        outline_setup.repair_scene_line_materials(context.scene)
        aov_compositor.setup_line_aov_compositor(context.scene)
        self.report({"INFO"}, f"{AOV_COMPOSITE_NAME} を作成しました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_LINE_OT_apply,
    BMANGA_LINE_OT_update_target,
    BMANGA_LINE_OT_update_visual_target,
    BMANGA_LINE_OT_update_all_visual_targets,
    BMANGA_LINE_OT_select_render_range_meshes,
    BMANGA_LINE_OT_remove,
    BMANGA_LINE_OT_set_settings_lock,
    BMANGA_LINE_OT_set_visibility,
    BMANGA_LINE_OT_set_line_only,
    BMANGA_LINE_OT_refresh_linked,
    BMANGA_LINE_OT_apply_active_to_linked,
    BMANGA_LINE_OT_sync_weights,
    BMANGA_LINE_OT_refresh_camera,
    BMANGA_LINE_OT_reset_camera_ref,
    BMANGA_LINE_OT_add_aov,
    BMANGA_LINE_OT_setup_aov_composite,
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
