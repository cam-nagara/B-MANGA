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
    StringProperty,
)

from . import core, modifier_stack, registration


_SETTING_FIELDS = (
    "outline_enabled",
    "outline_thickness",
    "outline_offset",
    "outline_color",
    "use_vertex_color",
    "auto_subdivision_for_midpoint",
    "even_thickness",
    "exclude_sheet_meshes",
    "use_uniform_line_width",
    "use_rim",
    "hide_through_transparent",
    "inner_line_enabled",
    "inner_line_angle",
    "use_marked_inner_edges",
    "inner_line_thickness",
    "inner_line_offset",
    "inner_line_color",
    "use_inner_line_creation_limit",
    "inner_line_creation_max_distance",
    "intersection_method",
    "intersection_enabled",
    "intersection_thickness",
    "intersection_line_offset",
    "intersection_color",
    "use_intersection_creation_limit",
    "intersection_creation_max_distance",
    "use_camera_compensation",
    "camera_compensation_influence",
    "line_width_reference_distance",
    "use_ao_influence",
    "ao_influence_strength",
    "edge_smooth_factor",
    "edge_midpoint_jitter_percent",
    "edge_midpoint_angle",
    "edge_width_curve_25",
    "edge_width_curve_50",
    "edge_width_curve_75",
    "inner_edge_smooth_factor",
    "inner_edge_midpoint_jitter_percent",
    "inner_edge_midpoint_angle",
    "inner_edge_width_curve_25",
    "inner_edge_width_curve_50",
    "inner_edge_width_curve_75",
    "intersection_edge_smooth_factor",
    "intersection_edge_midpoint_jitter_percent",
    "intersection_edge_midpoint_angle",
    "intersection_edge_width_curve_25",
    "intersection_edge_width_curve_50",
    "intersection_edge_width_curve_75",
    "use_camera_culling",
    "culling_margin",
    "use_outline_distance_limit",
    "outline_max_distance",
    "use_inner_line_distance_limit",
    "inner_line_max_distance",
    "use_intersection_distance_limit",
    "intersection_max_distance",
)
_COLOR_FIELDS = {"outline_color", "inner_line_color", "intersection_color"}


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
        if name in _COLOR_FIELDS:
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


def copy_settings_to_settings(source, target) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for name in _SETTING_FIELDS:
            value = getattr(source, name)
            if name in _COLOR_FIELDS:
                value = tuple(value)
            setattr(target, name, value)
    finally:
        core._propagating = old


def _update_view_layer(context) -> None:
    view_layer = getattr(context, "view_layer", None)
    update = getattr(view_layer, "update", None)
    if callable(update):
        update()


def _refresh_after_line_settings(context) -> None:
    from . import camera_comp, intersection_lines, outline_setup

    camera_comp.refresh(context)
    _update_view_layer(context)
    intersection_targets = intersection_lines.refresh_scene_intersections(context.scene)
    if intersection_targets:
        camera_comp.refresh_objects(
            context,
            intersection_targets,
            update_visibility=True,
            width_targets=("intersection",),
        )
    outline_setup.ensure_aov_passes(context.scene)


def apply_line_settings(
    obj: bpy.types.Object,
    context,
    *,
    refresh_scene: bool = True,
    transforms_fresh: bool = False,
) -> bool:
    if obj.type != "MESH":
        return False
    if not transforms_fresh:
        _update_view_layer(context)

    from . import (
        camera_comp,
        inner_lines,
        intersection_lines,
        outline_setup,
        plane_filter,
        scale_utils,
        subdivision_lod,
        vertex_analysis,
    )

    settings = obj.bmanga_line_settings
    if settings.auto_subdivision_for_midpoint:
        subdivision_lod.ensure_auto_subdivision(obj, context.scene)
    else:
        subdivision_lod.remove_auto_subdivision(obj)

    use_vg = (
        settings.use_uniform_line_width
        or vertex_analysis.has_width_controls(settings, "outline")
    )
    ok = outline_setup.apply_outline(
        obj,
        thickness=settings.outline_thickness,
        color=tuple(settings.outline_color),
        use_vertex_color=settings.use_vertex_color,
        even_thickness=settings.even_thickness,
        use_rim=settings.use_rim,
        offset=settings.outline_offset,
        use_vertex_group=use_vg,
        hide_through_transparent=settings.hide_through_transparent,
        scene=context.scene,
    )
    if not ok:
        return False

    exclude_generated = plane_filter.should_exclude_generated_lines(obj, settings)
    if (
        settings.inner_line_enabled
        and not exclude_generated
        and camera_comp.inner_line_creation_in_range(
            obj, context.scene, settings,
        )
    ):
        inner_lines.apply_inner_lines(
            obj,
            angle=settings.inner_line_angle,
            thickness=scale_utils.modifier_thickness_for_world_width(
                obj,
                settings.inner_line_thickness,
            ),
            offset=settings.inner_line_offset,
            material=outline_setup.get_line_material(obj, "inner"),
            use_marked_edges=settings.use_marked_inner_edges,
            midpoint_factor=(
                settings.inner_edge_smooth_factor
                if settings.auto_subdivision_for_midpoint
                else 0.0
            ),
            midpoint_jitter_percent=settings.inner_edge_midpoint_jitter_percent,
            width_curve_25=settings.inner_edge_width_curve_25,
            width_curve_50=settings.inner_edge_width_curve_50,
            width_curve_75=settings.inner_edge_width_curve_75,
        )
    else:
        inner_lines.remove_inner_lines(obj)

    intersection_in_range = camera_comp.intersection_line_creation_in_range(
        obj, context.scene, settings,
    )
    if not (
        settings.intersection_enabled
        and not exclude_generated
        and intersection_in_range
    ):
        intersection_lines.remove_intersection_lines(obj)

    camera_comp.store_unit_reference(obj, context.scene)

    if not settings.use_uniform_line_width:
        for target in ("outline", "inner", "intersection"):
            group_name = vertex_analysis.width_group_name(target)
            if vertex_analysis.has_width_controls(settings, target):
                vertex_analysis.compute_and_apply_weights(obj, settings, target)
            else:
                vertex_analysis.clear_width_weights(obj, group_name=group_name)

    if bool(obj.get(core.PROP_LINES_HIDDEN, False)):
        core.set_line_visibility(obj, False)
    else:
        core.set_line_visibility(obj, True)
    modifier_stack.reorder_line_modifiers(obj)

    if refresh_scene:
        _refresh_after_line_settings(context)

    return True


class BMangaLinePreset(bpy.types.PropertyGroup):
    """Saved B-MANGA Line settings."""

    outline_enabled: BoolProperty(default=True)
    outline_thickness: FloatProperty(default=0.0003, min=0.0001, max=1.0)
    outline_offset: FloatProperty(default=1.0, min=-1.0, max=1.0)
    outline_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_vertex_color: BoolProperty(default=False)
    auto_subdivision_for_midpoint: BoolProperty(default=False)
    even_thickness: BoolProperty(default=False)
    # 2026-07-03 ユーザー確定: 板ポリ除外だけは「初期値全オフ」の対象外でオン
    exclude_sheet_meshes: BoolProperty(default=True)
    use_uniform_line_width: BoolProperty(default=False)
    use_rim: BoolProperty(default=False)
    hide_through_transparent: BoolProperty(default=False)

    inner_line_enabled: BoolProperty(default=False)
    inner_line_angle: FloatProperty(default=1.0471975512, min=0.0174532925, max=3.1415926536)
    use_marked_inner_edges: BoolProperty(default=False)
    inner_line_thickness: FloatProperty(default=0.0003, min=0.0001, max=1.0)
    inner_line_offset: FloatProperty(default=1.0, min=-1.0, max=1.0)
    inner_line_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_inner_line_creation_limit: BoolProperty(default=True)
    inner_line_creation_max_distance: FloatProperty(default=10.0, min=0.1, max=1000.0)

    intersection_method: EnumProperty(
        items=[
            ("SHELL", "ライン素材（高速）", ""),
            ("BOOLEAN", "Boolean（精密）", ""),
            ("SDF", "SDF（高速）", ""),
        ],
        default="SHELL",
    )
    intersection_enabled: BoolProperty(default=False)
    intersection_thickness: FloatProperty(default=0.0003, min=0.0001, max=1.0)
    intersection_line_offset: FloatProperty(default=1.0, min=-1.0, max=1.0)
    intersection_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_intersection_creation_limit: BoolProperty(default=True)
    intersection_creation_max_distance: FloatProperty(default=10.0, min=0.1, max=1000.0)

    use_camera_compensation: BoolProperty(default=False)
    camera_compensation_influence: FloatProperty(default=1.0, min=0.0, max=1.0)
    line_width_reference_distance: FloatProperty(
        default=core.DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
        min=0.001,
        max=1000.0,
    )

    use_ao_influence: BoolProperty(default=False)
    ao_influence_strength: FloatProperty(default=0.5, min=0.0, max=1.0)

    edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    edge_midpoint_angle: FloatProperty(default=1.7453292520, min=0.0174532925, max=3.1415926536)
    edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    inner_edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    inner_edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    inner_edge_midpoint_angle: FloatProperty(default=1.7453292520, min=0.0174532925, max=3.1415926536)
    inner_edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    inner_edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    inner_edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    intersection_edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    intersection_edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    intersection_edge_midpoint_angle: FloatProperty(default=1.7453292520, min=0.0174532925, max=3.1415926536)
    intersection_edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    intersection_edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    intersection_edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    use_camera_culling: BoolProperty(default=False)
    culling_margin: FloatProperty(default=0.1745329252, min=0.0, max=1.5707963268)

    use_outline_distance_limit: BoolProperty(default=False)
    outline_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)

    use_inner_line_distance_limit: BoolProperty(default=False)
    inner_line_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)

    use_intersection_distance_limit: BoolProperty(default=False)
    intersection_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)


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
        _update_view_layer(context)
        for obj in _selected_meshes(context):
            copy_preset_to_settings(preset, obj.bmanga_line_settings)
            if apply_line_settings(
                obj,
                context,
                refresh_scene=False,
                transforms_fresh=True,
            ):
                count += 1
        _refresh_after_line_settings(context)
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
    for attr in (
        "bmanga_line_preset_name",
        "bmanga_line_preset_index",
        "bmanga_line_presets",
    ):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for cls in _CLASSES:
        registration.register_class(cls)
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
