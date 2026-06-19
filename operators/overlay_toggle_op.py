"""B-MANGA オーバーレイ表示の切替 operator (Phase 3c).

GPU オーバーレイ (画像 / フキダシ / テキスト等の独自描画) を一括 ON/OFF。
Object 化されたレイヤー (raster Mesh / balloon Curve / text plane) のみを
見たいときに OFF にする。
"""

from __future__ import annotations

import bpy

from ..utils import log

_logger = log.get_logger(__name__)


class BMANGA_OT_overlay_toggle(bpy.types.Operator):
    bl_idname = "bmanga.overlay_toggle"
    bl_label = "オーバーレイ表示切替"
    bl_description = (
        "B-MANGA 独自の GPU オーバーレイ描画 (画像/フキダシ/テキスト等) を"
        "一括 ON/OFF します。OFF にすると Blender 標準 Object 描画 (raster"
        " メッシュ/フキダシ Curve/テキスト Plane) のみが見えます。"
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


_CLASSES = (BMANGA_OT_overlay_toggle,)


def register() -> None:
    bpy.types.Scene.bmanga_overlay_enabled = bpy.props.BoolProperty(
        name="オーバーレイ表示",
        description="B-MANGA 独自オーバーレイ描画の表示切替 (Phase 3c)",
        default=True,
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.Scene.bmanga_overlay_enabled
    except AttributeError:
        pass
