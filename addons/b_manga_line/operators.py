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


def _invoke_delete_confirm(operator, context, event, message: str):
    """削除系オペレーター共通の確認ダイアログ（計画書§7）."""
    return context.window_manager.invoke_confirm(
        operator,
        event,
        title="確認",
        message=message,
        confirm_text="削除",
        icon="WARNING",
    )


_LINE_TARGET_ITEMS = (
    ("outline", "アウトライン", ""),
    ("inner", "稜谷線", ""),
    ("intersection", "交差線", ""),
    ("selection", "選択線", ""),
    ("bump", "バンプ線", ""),
)

_LINE_TARGET_LABELS = {
    "outline": "アウトライン",
    "inner": "稜谷線",
    "intersection": "交差線",
    "selection": "選択線",
    "bump": "バンプ線",
}

_REFLECT_ALL_CONFIRM_COUNT = 120


class BMANGA_LINE_OT_reflect_target(bpy.types.Operator):
    """選択オブジェクトの指定ラインを反映（無ければ作成・編集後なら作り直す）"""

    bl_idname = "bmanga_line.reflect_target"
    bl_label = "ラインを反映"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(items=_LINE_TARGET_ITEMS, default="outline")  # type: ignore[valid-type]
    # テスト用の逃げ道。待ち状態・指紋に関係なく重い経路を強制する（UI非公開）。
    force_rebuild: BoolProperty(default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        from . import reflect, selection, settings_draft

        settings_draft.flush(context)
        target = str(self.target)
        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        result = reflect.dispatch_target(
            target,
            targets_to_process,
            context,
            force_rebuild=bool(self.force_rebuild),
        )
        label = _LINE_TARGET_LABELS.get(target, "ライン")
        message = (
            f"{label}: 作成/再作成 {result.heavy_count}件・"
            f"見た目更新 {result.light_count}件・変更なし {result.unchanged_count}件"
        )
        self.report({"INFO"}, _with_lock_skip_note(message, skipped))
        return {"FINISHED"}


class BMANGA_LINE_OT_reflect_all(bpy.types.Operator):
    """選択オブジェクトのすべてのラインと中間頂点用ライン細分化を反映"""

    bl_idname = "bmanga_line.reflect_all"
    bl_label = "すべてのラインを反映"
    bl_options = {"REGISTER", "UNDO"}

    force_rebuild: BoolProperty(default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def _initial_reflect_summary(self, objects: list[bpy.types.Object]) -> dict[str, int]:
        summary = {
            "objects": len(objects),
            "applied": 0,
            "intersection": 0,
            "uniform": 0,
        }
        for obj in objects:
            if has_line(obj):
                summary["applied"] += 1
            settings = getattr(obj, "bmanga_line_settings", None)
            if settings is None:
                continue
            if bool(getattr(settings, "intersection_enabled", False)):
                summary["intersection"] += 1
            if bool(getattr(settings, "use_uniform_line_width", False)):
                summary["uniform"] += 1
        return summary

    def _needs_initial_confirm(self, objects: list[bpy.types.Object]) -> bool:
        summary = self._initial_reflect_summary(objects)
        return (
            summary["objects"] >= _REFLECT_ALL_CONFIRM_COUNT
            and summary["applied"] < summary["objects"]
            and (summary["intersection"] > 0 or summary["uniform"] > 0)
        )

    def invoke(self, context, event):
        from . import selection, settings_draft

        settings_draft.flush(context)
        targets_to_process = selection.updatable_mesh_objects(context)
        if self._needs_initial_confirm(targets_to_process):
            return context.window_manager.invoke_props_dialog(self, width=520)
        return self.execute(context)

    def draw(self, context):
        from . import selection

        layout = self.layout
        targets_to_process = selection.updatable_mesh_objects(context)
        summary = self._initial_reflect_summary(targets_to_process)
        layout.label(
            text=(
                f"選択メッシュ {summary['objects']}件 / "
                f"ライン適用済み {summary['applied']}件"
            ),
            icon="INFO",
        )
        if summary["intersection"]:
            layout.label(
                text=f"交差線 {summary['intersection']}件は初回作成に時間がかかります",
                icon="ERROR",
            )
        if summary["uniform"]:
            layout.label(
                text=f"線幅の均一化（頂点単位） {summary['uniform']}件",
                icon="INFO",
            )

    def execute(self, context):
        from . import reflect, selection, settings_draft

        settings_draft.flush(context)
        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        result = reflect.reflect_all(
            targets_to_process,
            context,
            force_rebuild=bool(self.force_rebuild),
        )
        affected: set[str] = set()
        for target_result in result.targets.values():
            for obj in target_result.heavy_objects:
                affected.add(obj.name_full)
            for obj in target_result.light_objects:
                affected.add(obj.name_full)
        affected.update(result.subdivision_updated.keys())
        self.report(
            {"INFO"},
            _with_lock_skip_note(
                f"{len(affected)} オブジェクトのすべてのラインを反映しました",
                skipped,
            ),
        )
        return {"FINISHED"}


def _set_auto_subdivision_setting(
    objects: list[bpy.types.Object],
    enabled: bool,
) -> None:
    from . import core

    old = core._propagating
    core._propagating = True
    try:
        for obj in objects:
            settings = getattr(obj, "bmanga_line_settings", None)
            if settings is None:
                continue
            if bool(getattr(settings, "auto_subdivision_for_midpoint", False)) == enabled:
                continue
            settings.auto_subdivision_for_midpoint = enabled
            record_override_edits(obj)
    finally:
        core._propagating = old


class BMANGA_LINE_OT_update_auto_subdivision(bpy.types.Operator):
    """選択オブジェクトの中間頂点用ライン細分化を反映・削除"""

    bl_idname = "bmanga_line.update_auto_subdivision"
    bl_label = "中間頂点用ライン細分化を反映"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=(
            ("REFLECT", "反映", ""),
            ("DELETE", "削除", ""),
        ),
        default="REFLECT",
    )  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def invoke(self, context, event):
        if str(self.action) != "DELETE":
            return self.execute(context)
        from . import selection

        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        message = (
            f"選択中の {len(targets_to_process)} オブジェクトから"
            "中間頂点用ライン細分化を削除します。"
        )
        if skipped:
            message += f"（ロック中の{skipped}件は対象外）"
        return _invoke_delete_confirm(self, context, event, message)

    def execute(self, context):
        from . import reflect, selection, settings_draft, update_state

        settings_draft.flush(context)
        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        action = str(self.action)

        if action == "DELETE":
            _set_auto_subdivision_setting(targets_to_process, False)

        reflect_result = reflect.reflect_all(
            targets_to_process,
            context,
            force_rebuild=True,
        )
        updated: dict[str, bpy.types.Object] = {}
        for obj in reflect_result.heavy_objects:
            updated[obj.name_full] = obj
        for result in reflect_result.targets.values():
            for obj in result.light_objects:
                updated[obj.name_full] = obj

        updated.update(reflect.refresh_plain_auto_subdivision(targets_to_process, context))

        # ここではライン細分化に影響される全ラインの見た目更新も走らせて
        # いるため、対象の更新待ち表示をまとめて解消してよい。
        update_state.clear_pending_many(targets_to_process, kind="visual")

        label = "削除" if action == "DELETE" else "反映"
        self.report(
            {"INFO"},
            _with_lock_skip_note(
                f"{len(updated)} オブジェクトの中間頂点用ライン細分化を{label}しました",
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


class BMANGA_LINE_OT_remove_all(bpy.types.Operator):
    """選択オブジェクトからすべてのラインを削除"""

    bl_idname = "bmanga_line.remove_all"
    bl_label = "すべてのラインを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(has_line(obj) for obj in context.selected_objects)

    def invoke(self, context, event):
        from . import selection

        targets_to_process = selection.updatable_mesh_objects(context)
        skipped = _locked_skip_count(context, len(targets_to_process))
        message = (
            f"選択中の {len(targets_to_process)} オブジェクトからすべてのライン"
            "（アウトライン・稜谷線・交差線・選択線・自動サブディビジョン）を削除します。"
        )
        if skipped:
            message += f"（ロック中の{skipped}件は対象外）"
        return _invoke_delete_confirm(self, context, event, message)

    def execute(self, context):
        from . import (
            intersection_lines,
            outline_setup,
            inner_lines,
            mesh_fingerprint,
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
            mesh_fingerprint.clear(obj)

        intersection_lines.refresh_scene_intersections(context.scene)
        self.report(
            {"INFO"},
            _with_lock_skip_note(f"{count} オブジェクトからすべてのラインを削除しました", skipped),
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
        from . import settings_draft

        settings_draft.flush(context)
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

        settings_draft.invalidate(context)

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
        from . import camera_comp, outline_setup, settings_draft

        settings_draft.flush(context)
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
        from . import presets, settings_draft, update_state

        settings_draft.flush(context)
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
        from . import selection, settings_draft, vertex_analysis

        settings_draft.flush(context)
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
        from . import settings_draft

        return settings_draft.get_line_camera(context) is not None

    def execute(self, context):
        from . import camera_comp, settings_draft

        settings_draft.flush(context)
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
        from . import settings_draft

        return (
            settings_draft.get_line_camera(context) is not None
            and any(obj.type == "MESH" for obj in context.selected_objects)
        )

    def execute(self, context):
        from . import camera_comp, core, settings_draft

        settings_draft.flush(context)
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

        settings_draft.invalidate(context)

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

    @classmethod
    def poll(cls, context):
        from . import aov_compositor

        return not aov_compositor.line_aov_compositor_exists(
            getattr(context, "scene", None),
        )

    def execute(self, context):
        from . import aov_compositor, outline_setup

        outline_setup.ensure_aov_passes(context.scene)
        outline_setup.repair_scene_line_materials(context.scene)
        aov_compositor.setup_line_aov_compositor(context.scene)
        self.report({"INFO"}, f"{AOV_COMPOSITE_NAME} を作成しました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_LINE_OT_reflect_target,
    BMANGA_LINE_OT_reflect_all,
    BMANGA_LINE_OT_update_auto_subdivision,
    BMANGA_LINE_OT_select_render_range_meshes,
    BMANGA_LINE_OT_remove_all,
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
