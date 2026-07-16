"""コマ編集モード用カメラ操作オペレーター."""

from __future__ import annotations

import math

import bpy
from bpy.props import BoolProperty, FloatVectorProperty
from bpy.types import Operator
from mathutils import Vector

from ..core.mode import MODE_COMA, get_mode
from ..utils import log, coma_camera
from . import view_event_region

_logger = log.get_logger(__name__)


def _is_coma_mode(context) -> bool:
    return get_mode(context) == MODE_COMA


def _camera(context):
    scene = getattr(context, "scene", None)
    cam = getattr(scene, "camera", None) if scene is not None else None
    return cam if cam is not None and getattr(cam, "type", "") == "CAMERA" else None


def _settings(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "bmanga_coma_camera_settings", None) if scene is not None else None


def _calculate_shift_drag(
    current_shift: tuple[float, float],
    current_mouse: tuple[float, float],
    shift_start: tuple[float, float],
    mouse_start: tuple[float, float],
    fine_adjust: bool,
    event_shift: bool,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], bool]:
    """カメラシフトドラッグの次状態を計算する."""
    fine_adjust_next = bool(event_shift)
    mouse_anchor = tuple(mouse_start)
    shift_anchor = tuple(shift_start)
    if fine_adjust_next != bool(fine_adjust):
        mouse_anchor = tuple(current_mouse)
        shift_anchor = tuple(current_shift)
    delta = Vector(current_mouse) - Vector(mouse_anchor)
    speed = 0.0001 if fine_adjust_next else 0.001
    next_shift = (
        float(shift_anchor[0]) - delta.x * speed,
        float(shift_anchor[1]) - delta.y * speed,
    )
    return next_shift, shift_anchor, mouse_anchor, fine_adjust_next


class BMANGA_OT_coma_camera_ensure(Operator):
    bl_idname = "bmanga.coma_camera_ensure"
    bl_label = "コマ編集カメラを用意"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_coma_mode(context)

    def execute(self, context):
        try:
            coma_camera.ensure_coma_camera_scene(context, generate_references=False)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel camera ensure failed")
            self.report({"ERROR"}, f"カメラ準備に失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_coma_camera_toggle_name_backgrounds(Operator):
    bl_idname = "bmanga.coma_camera_toggle_name_backgrounds"
    bl_label = "ページ画像を表示/非表示"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_coma_mode(context) and _camera(context) is not None

    def execute(self, context):
        visible = coma_camera.toggle_backgrounds_by_kind(context, "name")
        settings = _settings(context)
        if settings is not None:
            coma_camera.set_page_reference_visibility(
                context,
                show_all=bool(getattr(settings, "name_show_all_pages", False)),
            )
        self.report({"INFO"}, "ページ画像: 表示" if visible else "ページ画像: 非表示")
        return {"FINISHED"}


class BMANGA_OT_coma_camera_update_view(Operator):
    bl_idname = "bmanga.coma_camera_update_view"
    bl_label = "ビューを更新"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _is_coma_mode(context)

    def execute(self, context):
        coma_camera.update_view(context)
        return {"FINISHED"}


class BMANGA_OT_coma_camera_angle_add(Operator):
    bl_idname = "bmanga.coma_camera_angle_add"
    bl_label = "カメラプリセット追加"
    bl_description = "現在のカメラに関する設定をまとめてプリセットとして保存します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_coma_mode(context) and _camera(context) is not None

    def execute(self, context):
        cam = _camera(context)
        settings = _settings(context)
        if cam is None or settings is None:
            return {"CANCELLED"}
        item = settings.camera_angles.add()
        item.name = f"Angle {len(settings.camera_angles)}"
        item.location = cam.location
        item.rotation = cam.rotation_euler
        item.lens = float(getattr(cam.data, "lens", 35.0))
        item.shift_x = float(getattr(cam.data, "shift_x", 0.0))
        item.shift_y = float(getattr(cam.data, "shift_y", 0.0))
        item.fisheye_layout_mode = bool(context.scene.bmanga_coma_camera_fisheye_layout_mode)
        item.fisheye_fov = float(getattr(cam.data, "fisheye_fov", math.pi))
        item.bg_images_scale = float(getattr(settings, "bg_images_scale", 1.0))
        settings.camera_angles_index = len(settings.camera_angles) - 1
        return {"FINISHED"}


def _camera_angle_copy_name(settings, source_name: str) -> str:
    base = f"{source_name or 'カメラプリセット'} コピー"
    existing = {str(getattr(item, "name", "") or "") for item in settings.camera_angles}
    if base not in existing:
        return base
    for number in range(2, 1000):
        candidate = f"{base} {number}"
        if candidate not in existing:
            return candidate
    return base


def _copy_camera_angle(dst, src) -> None:
    for attr in (
        "location",
        "rotation",
        "lens",
        "shift_x",
        "shift_y",
        "fisheye_layout_mode",
        "fisheye_fov",
        "bg_images_scale",
    ):
        setattr(dst, attr, getattr(src, attr))


class BMANGA_OT_coma_camera_angle_duplicate(Operator):
    bl_idname = "bmanga.coma_camera_angle_duplicate"
    bl_label = "カメラプリセット複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return _is_coma_mode(context) and settings is not None and len(settings.camera_angles) > 0

    def execute(self, context):
        settings = _settings(context)
        if settings is None or not settings.camera_angles:
            return {"CANCELLED"}
        src_idx = max(0, min(int(settings.camera_angles_index), len(settings.camera_angles) - 1))
        src = settings.camera_angles[src_idx]
        item = settings.camera_angles.add()
        _copy_camera_angle(item, src)
        item.name = _camera_angle_copy_name(settings, str(getattr(src, "name", "") or ""))
        dst_idx = src_idx + 1
        settings.camera_angles.move(len(settings.camera_angles) - 1, dst_idx)
        settings.camera_angles_index = dst_idx
        return {"FINISHED"}


class BMANGA_OT_coma_camera_angle_remove(Operator):
    bl_idname = "bmanga.coma_camera_angle_remove"
    bl_label = "カメラプリセット削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return _is_coma_mode(context) and settings is not None and len(settings.camera_angles) > 0

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            return {"CANCELLED"}
        idx = int(settings.camera_angles_index)
        if not (0 <= idx < len(settings.camera_angles)):
            return {"CANCELLED"}
        settings.camera_angles.remove(idx)
        settings.camera_angles_index = min(max(0, idx - 1), max(0, len(settings.camera_angles) - 1))
        return {"FINISHED"}


class BMANGA_OT_coma_camera_angle_apply(Operator):
    bl_idname = "bmanga.coma_camera_angle_apply"
    bl_label = "カメラプリセット適用"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return (
            _is_coma_mode(context)
            and _camera(context) is not None
            and settings is not None
            and len(settings.camera_angles) > 0
        )

    def execute(self, context):
        cam = _camera(context)
        settings = _settings(context)
        if cam is None or settings is None:
            return {"CANCELLED"}
        idx = int(settings.camera_angles_index)
        if not (0 <= idx < len(settings.camera_angles)):
            return {"CANCELLED"}
        item = settings.camera_angles[idx]
        cam.location = item.location
        cam.rotation_euler = item.rotation
        cam.data.shift_x = float(item.shift_x)
        cam.data.shift_y = float(item.shift_y)
        cam.data.lens = float(item.lens)
        if hasattr(cam.data, "fisheye_fov"):
            cam.data.fisheye_fov = float(item.fisheye_fov)
        context.scene.bmanga_coma_camera_fisheye_fov = float(item.fisheye_fov)
        context.scene.bmanga_coma_camera_lens = float(item.lens)
        context.scene.bmanga_coma_camera_fisheye_layout_mode = bool(item.fisheye_layout_mode)
        settings.bg_images_scale = float(item.bg_images_scale)
        coma_camera.update_render_border_from_current_coma(context)
        return {"FINISHED"}


class BMANGA_OT_coma_camera_shift_drag(Operator):
    bl_idname = "bmanga.coma_camera_shift_drag"
    bl_label = "カメラのシフトをビューで調整"
    bl_description = "カメラビュー上でドラッグしてカメラシフトを調整します"
    bl_options = {"REGISTER", "UNDO"}

    shift_start: FloatVectorProperty(name="Start Shift", size=2)  # type: ignore[valid-type]
    shift_original: FloatVectorProperty(name="Original Shift", size=2)  # type: ignore[valid-type]
    mouse_start: FloatVectorProperty(name="Start Mouse Position", size=2)  # type: ignore[valid-type]
    fine_adjust: BoolProperty(name="Fine Adjust", default=False)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        area = getattr(context, "area", None)
        space = getattr(context, "space_data", None)
        rv3d = getattr(space, "region_3d", None)
        return (
            _is_coma_mode(context)
            and area is not None
            and area.type == "VIEW_3D"
            and rv3d is not None
            and rv3d.view_perspective == "CAMERA"
            and _camera(context) is not None
        )

    def invoke(self, context, event):
        if (
            event.type == "LEFTMOUSE"
            and event.value == "PRESS"
            and view_event_region.is_view3d_navigation_ui_event(context, event)
        ):
            return {"PASS_THROUGH"}
        cam = _camera(context)
        if cam is None:
            self.report({"WARNING"}, "カメラが選択されていません")
            return {"CANCELLED"}
        self.shift_start = (float(cam.data.shift_x), float(cam.data.shift_y))
        self.shift_original = self.shift_start
        self.mouse_start = (float(event.mouse_region_x), float(event.mouse_region_y))
        self.fine_adjust = bool(event.shift)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        cam = _camera(context)
        if cam is None:
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            next_shift, shift_start, mouse_start, fine_adjust = _calculate_shift_drag(
                (float(cam.data.shift_x), float(cam.data.shift_y)),
                (float(event.mouse_region_x), float(event.mouse_region_y)),
                (float(self.shift_start[0]), float(self.shift_start[1])),
                (float(self.mouse_start[0]), float(self.mouse_start[1])),
                bool(self.fine_adjust),
                bool(event.shift),
            )
            self.shift_start = shift_start
            self.mouse_start = mouse_start
            self.fine_adjust = fine_adjust
            cam.data.shift_x = next_shift[0]
            cam.data.shift_y = next_shift[1]
        elif event.type in {"RIGHTMOUSE", "ESC"}:
            cam.data.shift_x = float(self.shift_original[0])
            cam.data.shift_y = float(self.shift_original[1])
            return {"CANCELLED"}
        elif event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"}:
            return {"FINISHED"}
        return {"RUNNING_MODAL"}


_CLASSES = (
    BMANGA_OT_coma_camera_ensure,
    BMANGA_OT_coma_camera_toggle_name_backgrounds,
    BMANGA_OT_coma_camera_update_view,
    BMANGA_OT_coma_camera_angle_add,
    BMANGA_OT_coma_camera_angle_duplicate,
    BMANGA_OT_coma_camera_angle_remove,
    BMANGA_OT_coma_camera_angle_apply,
    BMANGA_OT_coma_camera_shift_drag,
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
