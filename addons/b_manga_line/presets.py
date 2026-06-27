"""B-MANGA Line preset storage and application."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import core


_SETTING_FIELDS = (
    "outline_thickness",
    "outline_color",
    "use_vertex_color",
    "even_thickness",
    "use_rim",
    "inner_line_enabled",
    "inner_line_angle",
    "inner_line_thickness",
    "intersection_method",
    "intersection_enabled",
    "intersection_target",
    "intersection_thickness",
    "use_camera_compensation",
    "camera_compensation_influence",
    "use_ao_influence",
    "ao_influence_strength",
    "edge_smooth_factor",
    "edge_midpoint_jitter_percent",
    "edge_width_curve_25",
    "edge_width_curve_50",
    "edge_width_curve_75",
    "use_camera_culling",
    "culling_margin",
    "use_inner_line_distance_limit",
    "inner_line_max_distance",
)


def _selected_meshes(context) -> list[bpy.types.Object]:
    return [obj for obj in context.selected_objects if obj.type == "MESH"]


def _preset_name(scene) -> str:
    raw = getattr(scene, "bmanga_line_preset_name", "")
    return str(raw).strip() or "ラインプリセット"


def _active_preset(scene):
    presets = scene.bmanga_line_presets
    index = scene.bmanga_line_preset_index
    if 0 <= index < len(presets):
        return presets[index]
    return None


def copy_settings_to_preset(settings, preset) -> None:
    for name in _SETTING_FIELDS:
        value = getattr(settings, name)
        if name == "outline_color":
            value = tuple(value)
        setattr(preset, name, value)


def copy_preset_to_settings(preset, settings) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for name in _SETTING_FIELDS:
            setattr(settings, name, getattr(preset, name))
    finally:
        core._propagating = old


def apply_line_settings(obj: bpy.types.Object, context) -> bool:
    if obj.type != "MESH":
        return False

    from . import (
        camera_comp,
        inner_lines,
        intersection_lines,
        outline_setup,
        vertex_analysis,
    )

    settings = obj.bmanga_line_settings
    use_vg = (
        settings.use_vertex_color
        or settings.use_ao_influence
        or abs(settings.edge_smooth_factor) > 0.001
    )
    ok = outline_setup.apply_outline(
        obj,
        thickness=settings.outline_thickness,
        color=tuple(settings.outline_color),
        use_vertex_color=settings.use_vertex_color,
        even_thickness=settings.even_thickness,
        use_rim=settings.use_rim,
        use_vertex_group=use_vg,
        scene=context.scene,
    )
    if not ok:
        return False

    mat = outline_setup.get_outline_material(obj)
    if settings.inner_line_enabled:
        inner_lines.apply_inner_lines(
            obj,
            angle=settings.inner_line_angle,
            thickness=settings.inner_line_thickness,
            material=mat,
        )
    else:
        inner_lines.remove_inner_lines(obj)

    if settings.intersection_enabled:
        intersection_lines.apply_intersection_lines(
            obj,
            target=settings.intersection_target,
            thickness=settings.intersection_thickness,
            material=mat,
            method=settings.intersection_method,
        )
    else:
        intersection_lines.remove_intersection_lines(obj)

    if settings.use_camera_compensation:
        camera_comp.store_reference(obj, context.scene)

    if use_vg:
        vertex_analysis.compute_and_apply_weights(obj, settings)

    if bool(obj.get(core.PROP_LINES_HIDDEN, False)):
        core.set_line_visibility(obj, False)
    else:
        core.set_line_visibility(obj, True)

    return True


class BMangaLinePreset(bpy.types.PropertyGroup):
    """Saved B-MANGA Line settings."""

    outline_thickness: FloatProperty(default=0.0003, min=0.0001, max=0.1)
    outline_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_vertex_color: BoolProperty(default=False)
    even_thickness: BoolProperty(default=True)
    use_rim: BoolProperty(default=True)

    inner_line_enabled: BoolProperty(default=False)
    inner_line_angle: FloatProperty(default=0.5235987756, min=0.0174532925, max=3.1415926536)
    inner_line_thickness: FloatProperty(default=0.0005, min=0.0001, max=0.05)

    intersection_method: EnumProperty(
        items=[
            ("BOOLEAN", "Boolean（精密）", ""),
            ("SDF", "SDF（高速）", ""),
        ],
        default="BOOLEAN",
    )
    intersection_enabled: BoolProperty(default=False)
    intersection_target: PointerProperty(type=bpy.types.Object)
    intersection_thickness: FloatProperty(default=0.0005, min=0.0001, max=0.05)

    use_camera_compensation: BoolProperty(default=False)
    camera_compensation_influence: FloatProperty(default=1.0, min=0.0, max=1.0)

    use_ao_influence: BoolProperty(default=False)
    ao_influence_strength: FloatProperty(default=0.5, min=0.0, max=1.0)

    edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    use_camera_culling: BoolProperty(default=False)
    culling_margin: FloatProperty(default=0.1745329252, min=0.0, max=1.5707963268)

    use_inner_line_distance_limit: BoolProperty(default=False)
    inner_line_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)


class BMANGA_LINE_OT_preset_save(bpy.types.Operator):
    """現在のライン設定をプリセットとして保存"""

    bl_idname = "bmanga_line.preset_save"
    bl_label = "保存/更新"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        name = _preset_name(scene)
        presets = scene.bmanga_line_presets
        preset = None
        index = -1
        for i, item in enumerate(presets):
            if item.name == name:
                preset = item
                index = i
                break
        if preset is None:
            preset = presets.add()
            preset.name = name
            index = len(presets) - 1
        copy_settings_to_preset(obj.bmanga_line_settings, preset)
        scene.bmanga_line_preset_index = index
        scene.bmanga_line_preset_name = name
        self.report({"INFO"}, f"ラインプリセット「{name}」を保存しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_preset_apply_selected(bpy.types.Operator):
    """プリセットを選択中の全オブジェクトに適用"""

    bl_idname = "bmanga_line.preset_apply_selected"
    bl_label = "選択中に適用"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_preset(context.scene) is not None and bool(_selected_meshes(context))

    def execute(self, context):
        preset = _active_preset(context.scene)
        if preset is None:
            self.report({"WARNING"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        count = 0
        for obj in _selected_meshes(context):
            copy_preset_to_settings(preset, obj.bmanga_line_settings)
            if apply_line_settings(obj, context):
                count += 1
        self.report({"INFO"}, f"{count} オブジェクトにプリセットを適用しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_preset_delete(bpy.types.Operator):
    """選択中のラインプリセットを削除"""

    bl_idname = "bmanga_line.preset_delete"
    bl_label = "削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_preset(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        presets = scene.bmanga_line_presets
        index = scene.bmanga_line_preset_index
        if not (0 <= index < len(presets)):
            return {"CANCELLED"}
        name = presets[index].name
        presets.remove(index)
        scene.bmanga_line_preset_index = min(index, len(presets) - 1)
        self.report({"INFO"}, f"ラインプリセット「{name}」を削除しました")
        return {"FINISHED"}


_CLASSES = (
    BMangaLinePreset,
    BMANGA_LINE_OT_preset_save,
    BMANGA_LINE_OT_preset_apply_selected,
    BMANGA_LINE_OT_preset_delete,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bmanga_line_presets = CollectionProperty(type=BMangaLinePreset)
    bpy.types.Scene.bmanga_line_preset_index = IntProperty(default=-1)
    bpy.types.Scene.bmanga_line_preset_name = StringProperty(
        name="プリセット名",
        default="ラインプリセット",
    )


def unregister() -> None:
    for attr in (
        "bmanga_line_preset_name",
        "bmanga_line_preset_index",
        "bmanga_line_presets",
    ):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
