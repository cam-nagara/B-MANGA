"""Phase 5: ページ/コママスク Object 一括 operator."""

from __future__ import annotations

import bpy

from ..utils import log
from ..utils import mask_object as mask_obj

_logger = log.get_logger(__name__)


class BMANGA_OT_mask_regenerate_all(bpy.types.Operator):
    bl_idname = "bmanga.mask_regenerate_all"
    bl_label = "全マスクを再生成"
    bl_description = "現在のページの表示範囲を作り直し、コマ外が隠れる状態を整えます"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work
        from ..utils import page_file_scene

        work = get_work(context)
        return bool(
            work
            and getattr(work, "loaded", False)
            and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
            and getattr(context, "mode", "OBJECT") == "OBJECT"
        )

    def execute(self, context):
        from ..core.work import get_work
        from ..utils import page_file_scene

        if context.mode != "OBJECT":
            self.report({"WARNING"}, "Object Mode で実行してください")
            return {"CANCELLED"}
        work = get_work(context)
        page_id = page_file_scene.current_page_id(context.scene)
        if not page_id or not page_file_scene.is_page_edit_scene(context.scene):
            self.report({"WARNING"}, "ページ用blendファイルで実行してください")
            return {"CANCELLED"}
        mask_work = page_file_scene.work_for_pages(work, {page_id})
        scene = context.scene
        result = mask_obj.regenerate_all_masks(scene, mask_work)
        removed = mask_obj.remove_orphan_masks(scene, mask_work)
        # 全レイヤーへマスクを適用 (枠外を視覚的に切抜き)
        from ..utils import mask_apply

        applied = mask_apply.apply_masks_to_all_managed(scene)
        self.report(
            {"INFO"},
            f"用紙 {result['page_masks']} 件 / コマ {result['coma_masks']} 件の表示範囲を再生成、"
            f"不要な古いマスク {removed} 件を削除、{applied} 件のレイヤーへ適用",
        )
        return {"FINISHED"}


class BMANGA_OT_mask_remove_orphans(bpy.types.Operator):
    bl_idname = "bmanga.mask_remove_orphans"
    bl_label = "孤立マスクを削除"
    bl_description = "現在のページで不要になった古いマスクを削除します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work
        from ..utils import page_file_scene

        work = get_work(context)
        return bool(
            work
            and getattr(work, "loaded", False)
            and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
        )

    def execute(self, context):
        from ..core.work import get_work
        from ..utils import page_file_scene

        work = get_work(context)
        page_id = page_file_scene.current_page_id(context.scene)
        if not page_id or not page_file_scene.is_page_edit_scene(context.scene):
            self.report({"WARNING"}, "ページ用blendファイルで実行してください")
            return {"CANCELLED"}
        removed = mask_obj.remove_orphan_masks(
            context.scene,
            page_file_scene.work_for_pages(work, {page_id}),
        )
        self.report({"INFO"}, f"不要な古いマスク {removed} 件を削除しました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_mask_regenerate_all,
    BMANGA_OT_mask_remove_orphans,
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
