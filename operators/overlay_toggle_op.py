"""B-MANGA オーバーレイ表示の切替 operator."""

from __future__ import annotations

import bpy

from ..utils import log

_logger = log.get_logger(__name__)


def _overlay_enabled_update(scene, context) -> None:
    try:
        from ..core.work import get_work
        from ..utils import view_settings

        view_settings.copy_scene_to_work(scene, get_work(context))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..utils import coma_camera

        coma_camera.apply_coma_overlay_background_visibility(context, scene=scene)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..utils import page_preview_object

        page_preview_object.sync_page_previews(context, getattr(scene, "bmanga_work", None), force=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..core.mode import MODE_COMA, get_mode
        from ..utils import paper_guide_object

        if get_mode(context) != MODE_COMA:
            paper_guide_object.regenerate_all_paper_guides(scene, getattr(scene, "bmanga_work", None))
            paper_guide_object.apply_view_constant_thickness()
    except Exception:  # noqa: BLE001
        pass
    try:
        for area in context.screen.areas if context.screen else ():
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


class BMANGA_OT_overlay_toggle(bpy.types.Operator):
    bl_idname = "bmanga.overlay_toggle"
    bl_label = "オーバーレイ表示切替"
    bl_description = (
        "ページ番号・作品情報・選択枠・編集ハンドルなどの補助表示を"
        "一括 ON/OFF します。"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        new_val = not bool(getattr(scene, "bmanga_overlay_enabled", True))
        scene.bmanga_overlay_enabled = new_val
        try:
            from ..core.work import get_work
            from ..utils import view_settings

            view_settings.copy_scene_to_work(scene, get_work(context))
        except Exception:  # noqa: BLE001
            pass
        # 全 3D ビュー再描画
        try:
            for area in context.screen.areas if context.screen else ():
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
        self.report(
            {"INFO"},
            f"オーバーレイ {'表示' if new_val else '非表示'}",
        )
        return {"FINISHED"}


def _shape_guides_toggle_update(_self, context) -> None:
    try:
        for area in context.screen.areas if context.screen else ():
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


_CLASSES = (BMANGA_OT_overlay_toggle,)


def register() -> None:
    bpy.types.Scene.bmanga_overlay_enabled = bpy.props.BoolProperty(
        name="オーバーレイ表示",
        description="ページ番号・作品情報・選択枠・編集ハンドルなどの補助表示を切り替えます",
        default=True,
        update=_overlay_enabled_update,
    )
    bpy.types.Scene.bmanga_show_line_shape_guides = bpy.props.BoolProperty(
        name="端形状ガイドを表示",
        description="効果線・フキダシ(ウニフラ)の外端形状と内端形状をビューポートに細い線で表示します",
        default=True,
        update=_shape_guides_toggle_update,
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for prop in ("bmanga_overlay_enabled", "bmanga_show_line_shape_guides"):
        try:
            delattr(bpy.types.Scene, prop)
        except AttributeError:
            pass
