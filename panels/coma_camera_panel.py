"""コマ編集モード用カメラ操作パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode

B_NAME_CATEGORY = "B-MANGA"


def _settings(context):
    return getattr(context.scene, "bmanga_coma_camera_settings", None)


def _camera(context):
    cam = getattr(context.scene, "camera", None)
    return cam if cam is not None and getattr(cam, "type", "") == "CAMERA" else None


def _is_camera_view(context) -> bool:
    area = getattr(context, "area", None)
    space = getattr(context, "space_data", None)
    rv3d = getattr(space, "region_3d", None)
    if area is None or area.type != "VIEW_3D" or rv3d is None:
        return False
    return rv3d.view_perspective == "CAMERA"


def _draw_camera_settings(layout, context, cam) -> None:
    scene = context.scene
    row = layout.row()
    row.prop(scene, "camera", text="")

    split = layout.split(factor=0.4)
    split.label(text="焦点距離")
    split.prop(cam.data, "lens", text="")

    box = layout.box()
    box.label(text="奥行き表示範囲")
    row = box.row(align=True)
    row.prop(cam.data, "clip_start", text="開始")
    row.prop(cam.data, "clip_end", text="終了")

    box = layout.box()
    box.label(text="カメラのシフト")
    row = box.row(align=True)
    row.prop(cam.data, "shift_x", text="X")
    row.prop(cam.data, "shift_y", text="Y")
    row = box.row()
    row.enabled = _is_camera_view(context)
    row.operator("bmanga.coma_camera_shift_drag", text="ビューで調整")

    split = layout.split(factor=0.4)
    split.label(text="カメラの回転")
    split.prop(cam, "rotation_euler", index=1, text="")


def _draw_angle_list(layout, context, settings) -> None:
    box = layout.box()
    box.label(text="カメラプリセット")
    row = box.row()
    row.template_list(
        "UI_UL_list",
        "bmanga_coma_camera_angles",
        settings,
        "camera_angles",
        settings,
        "camera_angles_index",
        rows=3,
    )
    col = row.column(align=True)
    col.operator("bmanga.coma_camera_angle_add", icon="ADD", text="")
    col.operator("bmanga.coma_camera_angle_duplicate", icon="DUPLICATE", text="")
    col.operator("bmanga.coma_camera_angle_remove", icon="REMOVE", text="")
    box.operator("bmanga.coma_camera_angle_apply", text="適用")


class BMANGA_PT_coma_camera(Panel):
    bl_idname = "BMANGA_PT_coma_camera"
    bl_label = "カメラ設定"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 11

    @classmethod
    def poll(cls, context):
        return get_mode(context) == MODE_COMA

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = _settings(context)
        if settings is None:
            layout.operator("bmanga.coma_camera_ensure", text="コマ編集カメラを用意")
            return

        row = layout.row(align=True)
        row.operator("bmanga.coma_camera_ensure", text="カメラを整備", icon="CAMERA_DATA")

        cam = _camera(context)
        if cam is None:
            layout.label(text="カメラがありません", icon="ERROR")
            return

        _draw_camera_settings(layout, context, cam)
        box = layout.box()
        box.prop(scene, "bmanga_coma_camera_fisheye_layout_mode", text="魚眼モード")
        row = box.row(align=True)
        row.enabled = bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False))
        row.prop(scene, "bmanga_coma_camera_fisheye_fov", text="魚眼FOV")
        _draw_angle_list(layout, context, settings)


_CLASSES = (BMANGA_PT_coma_camera,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
