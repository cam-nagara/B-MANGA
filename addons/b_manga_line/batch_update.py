"""B-MANGA Line batch updates for multi-selected setting changes."""

from __future__ import annotations

import bpy

from . import (
    camera_comp,
    core,
    inner_lines,
    intersection_lines,
    modifier_stack,
    outline_setup,
    outline_width_attribute,
    plane_filter,
    presets,
    selection_lines,
)
from .scale_utils import modifier_thickness_for_world_width

MAX_IMMEDIATE_VISIBILITY_OBJECTS = 64
MAX_IMMEDIATE_GENERATED_WIDTH_OBJECTS = 64
MAX_IMMEDIATE_INTERSECTION_REBUILD_OBJECTS = 64
MAX_IMMEDIATE_SHEET_REBUILD_OBJECTS = 64
_GENERATED_WIDTH_DETAIL_PROPS = {
    "inner_edge_midpoint_jitter_percent",
    "inner_edge_width_curve_25",
    "inner_edge_width_curve_50",
    "inner_edge_width_curve_75",
    "intersection_edge_midpoint_jitter_percent",
    "intersection_edge_midpoint_angle",
    "intersection_edge_width_curve_25",
    "intersection_edge_width_curve_50",
    "intersection_edge_width_curve_75",
    "selection_edge_midpoint_jitter_percent",
    "selection_edge_midpoint_angle",
    "selection_edge_width_curve_25",
    "selection_edge_width_curve_50",
    "selection_edge_width_curve_75",
}


def _line_objects(objects: list[bpy.types.Object]) -> list[bpy.types.Object]:
    return [
        obj for obj in objects
        if obj.type == "MESH" and obj.data is not None and core.has_line(obj)
    ]


def _outline_modifier(obj: bpy.types.Object):
    return obj.modifiers.get(core.MODIFIER_NAME)


def _has_outline_target(obj: bpy.types.Object) -> bool:
    return core.has_outline(obj)


def defer_intersection_viewport(objects) -> None:
    for obj in objects:
        for mod in core.iter_intersection_modifiers(obj):
            intersection_lines._queue_deferred_viewport_modifier(obj, mod)


def _target_modifier_exists(obj: bpy.types.Object, target: str) -> bool:
    if target == "inner":
        return obj.modifiers.get(core.GN_MODIFIER_NAME) is not None
    if target == "intersection":
        return any(core.iter_intersection_modifiers(obj))
    if target == "selection":
        return obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME) is not None
    return _has_outline_target(obj)


def _generated_line_objects(
    objects: list[bpy.types.Object],
    target: str,
) -> list[bpy.types.Object]:
    if target == "outline":
        return [obj for obj in objects if _has_outline_target(obj)]
    return [obj for obj in objects if _target_modifier_exists(obj, target)]


def _ensure_vertex_group(obj: bpy.types.Object, name: str):
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)
    if obj.data and obj.data.vertices:
        vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    return vg


def _refresh_full(objects: list[bpy.types.Object], context) -> None:
    presets._update_view_layer(context)
    for obj in objects:
        presets.apply_line_settings(
            obj,
            context,
            refresh_scene=False,
            transforms_fresh=True,
        )
    presets._refresh_after_line_settings(context)


def _refresh_camera(context) -> None:
    camera_comp.refresh(context)


def _refresh_camera_objects(
    objects: list[bpy.types.Object],
    context,
    *,
    update_visibility: bool = False,
    width_targets=None,
) -> bool:
    return camera_comp.refresh_objects(
        context,
        objects,
        update_visibility=update_visibility,
        width_targets=width_targets,
    )


def _update_outline_flag(objects: list[bpy.types.Object], attr: str, prop_name: str) -> None:
    for obj in objects:
        mod = _outline_modifier(obj)
        if mod is not None:
            value = bool(getattr(obj.bmanga_line_settings, prop_name))
            if prop_name == "use_rim":
                outline_setup.update_modifier_rim(obj, value)
            else:
                setattr(mod, attr, value)


def _ensure_outline_modifier(obj: bpy.types.Object, context) -> bool:
    from . import vertex_analysis

    settings = obj.bmanga_line_settings
    use_vg = (
        settings.use_uniform_line_width
        or vertex_analysis.has_width_controls(settings, "outline")
    )
    return outline_setup.apply_outline(
        obj,
        thickness=settings.outline_thickness,
        color=tuple(settings.outline_color),
        use_vertex_color=settings.use_vertex_color,
        even_thickness=settings.even_thickness,
        use_rim=settings.use_rim,
        offset=settings.outline_offset,
        use_vertex_group=use_vg,
        hide_through_transparent=settings.hide_through_transparent,
        scene=getattr(context, "scene", None),
    )


def _outline_creation_in_range(obj: bpy.types.Object, context) -> bool:
    return camera_comp.outline_line_creation_in_range(
        obj,
        getattr(context, "scene", None),
        obj.bmanga_line_settings,
    )


def _update_outline_enabled(objects: list[bpy.types.Object], context) -> None:
    needs_view_update = any(
        obj.bmanga_line_settings.outline_enabled
        and _outline_creation_in_range(obj, context)
        and not _has_outline_target(obj)
        for obj in objects
    )
    if any(obj.bmanga_line_settings.outline_enabled for obj in objects):
        defer_intersection_viewport(objects)
    if needs_view_update:
        presets._update_view_layer(context)
    refresh_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        created = False
        if settings.outline_enabled:
            if not _outline_creation_in_range(obj, context):
                if _has_outline_target(obj):
                    visibility_changed = core.set_outline_visibility_from_settings(obj)
                    if visibility_changed:
                        refresh_targets.append(obj)
                continue
            if not _has_outline_target(obj) and _ensure_outline_modifier(obj, context):
                created = True
                refresh_targets.append(obj)
        visibility_changed = core.set_outline_visibility_from_settings(obj)
        if (
            visibility_changed
            and obj not in refresh_targets
            and settings.outline_enabled
            and (
                created
                or settings.use_camera_culling
                or settings.use_outline_distance_limit
            )
        ):
            refresh_targets.append(obj)
    enabled_targets = [
        obj for obj in refresh_targets
        if obj.bmanga_line_settings.outline_enabled
    ]
    if enabled_targets and len(enabled_targets) <= MAX_IMMEDIATE_VISIBILITY_OBJECTS:
        _refresh_camera_objects(
            enabled_targets,
            context,
            update_visibility=True,
            width_targets=("outline",),
        )


def _update_outline_creation_range(objects: list[bpy.types.Object], context) -> None:
    refresh_targets = []
    needs_view_update = any(
        obj.bmanga_line_settings.outline_enabled
        and _outline_creation_in_range(obj, context)
        and not _has_outline_target(obj)
        for obj in objects
    )
    if needs_view_update:
        presets._update_view_layer(context)
    for obj in objects:
        settings = obj.bmanga_line_settings
        if not settings.outline_enabled:
            continue
        if not _outline_creation_in_range(obj, context):
            if _has_outline_target(obj) and core.set_outline_visibility_from_settings(obj):
                refresh_targets.append(obj)
            continue
        if not _has_outline_target(obj):
            if _ensure_outline_modifier(obj, context):
                refresh_targets.append(obj)
            continue
        if core.set_outline_visibility_from_settings(obj):
            refresh_targets.append(obj)
    if refresh_targets and len(refresh_targets) <= MAX_IMMEDIATE_VISIBILITY_OBJECTS:
        _refresh_camera_objects(
            refresh_targets,
            context,
            update_visibility=True,
            width_targets=("outline",),
        )


def _update_transparent_protection(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        outline_setup.update_transparent_protection(
            obj,
            bool(settings.hide_through_transparent),
            tuple(settings.outline_color),
        )


def _update_outline_color(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        outline_setup.update_material_color(
            obj,
            tuple(obj.bmanga_line_settings.outline_color),
        )


def _update_generated_color(objects: list[bpy.types.Object], target: str) -> None:
    targets = _generated_line_objects(objects, target)
    for obj in targets:
        material = outline_setup.get_line_material(obj, target)
        if target == "inner":
            inner_lines.update_parameters(obj, material=material)
        elif target == "intersection":
            intersection_lines.update_parameters(obj, material=material)
        elif target == "selection":
            selection_lines.update_parameters(obj, material=material)


def _inner_midpoint_kwargs(settings) -> dict[str, float]:
    factor = (
        float(settings.inner_edge_smooth_factor)
        if bool(getattr(settings, "auto_subdivision_for_midpoint", False))
        else 0.0
    )
    return {
        "midpoint_factor": factor,
        "midpoint_angle": core.inner_width_split_angle(settings),
        "midpoint_jitter_percent": float(settings.inner_edge_midpoint_jitter_percent),
        "width_curve_25": float(settings.inner_edge_width_curve_25),
        "width_curve_50": float(settings.inner_edge_width_curve_50),
        "width_curve_75": float(settings.inner_edge_width_curve_75),
    }


def _selection_midpoint_kwargs(settings) -> dict[str, float]:
    factor = (
        float(settings.selection_edge_smooth_factor)
        if bool(getattr(settings, "auto_subdivision_for_midpoint", False))
        else 0.0
    )
    return {
        "midpoint_factor": factor,
        "midpoint_angle": float(settings.selection_edge_midpoint_angle),
        "midpoint_jitter_percent": float(settings.selection_edge_midpoint_jitter_percent),
        "width_curve_25": float(settings.selection_edge_width_curve_25),
        "width_curve_50": float(settings.selection_edge_width_curve_50),
        "width_curve_75": float(settings.selection_edge_width_curve_75),
    }


def _update_outline_thickness(objects: list[bpy.types.Object], context) -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        if settings.use_camera_compensation and core.PROP_BASE_THICKNESS in obj:
            obj[core.PROP_BASE_THICKNESS] = settings.outline_thickness
    if _refresh_camera_objects(objects, context, width_targets=("outline",)):
        return
    for obj in objects:
        outline_setup.update_modifier_thickness(
            obj,
            obj.bmanga_line_settings.outline_thickness,
        )


def _update_outline_offset(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        outline_setup.update_modifier_offset(
            obj,
            obj.bmanga_line_settings.outline_offset,
        )


def _update_generated_thickness(
    objects: list[bpy.types.Object],
    context,
    target: str,
) -> None:
    targets = _generated_line_objects(objects, target)
    if not targets:
        return
    if _refresh_camera_objects(targets, context, width_targets=(target,)):
        return
    for obj in targets:
        settings = obj.bmanga_line_settings
        if target == "inner":
            inner_lines.update_parameters(
                obj,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.inner_line_thickness,
                ),
                **_inner_midpoint_kwargs(settings),
            )
        elif target == "intersection":
            intersection_lines.update_parameters(
                obj,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.intersection_thickness,
                ),
            )
        elif target == "selection":
            selection_lines.update_parameters(
                obj,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.selection_line_thickness,
                ),
                **_selection_midpoint_kwargs(settings),
            )


def _update_generated_offset(objects: list[bpy.types.Object], target: str) -> None:
    targets = _generated_line_objects(objects, target)
    for obj in targets:
        settings = obj.bmanga_line_settings
        if target == "inner":
            inner_lines.update_parameters(
                obj,
                offset=settings.inner_line_offset,
                **_inner_midpoint_kwargs(settings),
            )
        elif target == "intersection":
            intersection_lines.update_parameters(
                obj,
                offset=settings.intersection_line_offset,
            )
        elif target == "selection":
            selection_lines.update_parameters(
                obj,
                offset=settings.selection_line_offset,
                **_selection_midpoint_kwargs(settings),
            )


def _update_camera_compensation(objects: list[bpy.types.Object], context) -> None:
    targets = [
        obj for obj in objects
        if not bool(obj.bmanga_line_settings.use_uniform_line_width)
    ]
    if not targets:
        return
    for obj in targets:
        settings = obj.bmanga_line_settings
        mod = _outline_modifier(obj)
        if mod is None:
            outline_setup.sync_sheet_outline_width(obj)
            continue
        if settings.use_camera_compensation:
            camera_comp.store_unit_reference(obj, context.scene)
    if _refresh_camera_objects(targets, context):
        return
    for obj in targets:
        settings = obj.bmanga_line_settings
        mod = _outline_modifier(obj)
        if mod is None or settings.use_camera_compensation:
            continue
        mod.thickness = modifier_thickness_for_world_width(
            obj,
            settings.outline_thickness,
        )
        inner_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.inner_line_thickness,
            ),
            **_inner_midpoint_kwargs(settings),
        )
        intersection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.intersection_thickness,
            ),
        )
        selection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.selection_line_thickness,
            ),
            **_selection_midpoint_kwargs(settings),
        )


def _update_camera_influence(objects: list[bpy.types.Object], context) -> None:
    targets = [
        obj for obj in objects
        if bool(obj.bmanga_line_settings.use_camera_compensation)
        and not bool(obj.bmanga_line_settings.use_uniform_line_width)
    ]
    if targets:
        _refresh_camera_objects(targets, context)


def _update_line_width_reference_distance(
    objects: list[bpy.types.Object],
    context,
) -> None:
    targets = [
        obj for obj in objects
        if not bool(obj.bmanga_line_settings.use_uniform_line_width)
    ]
    if not targets:
        return
    for obj in targets:
        camera_comp.store_unit_reference(obj, context.scene)
    _refresh_camera_objects(targets, context)


def _update_uniform_line_width(objects: list[bpy.types.Object], context) -> None:
    from . import vertex_analysis

    if camera_comp.refresh_objects(context, objects):
        return

    for obj in objects:
        settings = obj.bmanga_line_settings
        mod = _outline_modifier(obj)
        if mod is None:
            outline_setup.sync_sheet_outline_width(obj)
            continue
        if settings.use_uniform_line_width:
            vg = _ensure_vertex_group(obj, core.VG_LINE_WIDTH)
            mod.vertex_group = vg.name
            mod.thickness_vertex_group = 0.0
            continue

        mod.thickness = modifier_thickness_for_world_width(
            obj,
            settings.outline_thickness,
        )
        if vertex_analysis.has_width_controls(settings, "outline"):
            vg = _ensure_vertex_group(obj, core.VG_LINE_WIDTH)
            mod.vertex_group = vg.name
            mod.thickness_vertex_group = 0.0
            vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
        else:
            mod.vertex_group = ""
            vertex_analysis.clear_width_weights(obj, group_name=core.VG_LINE_WIDTH)
        outline_width_attribute.ensure_outline_width_attribute(obj, settings)
        for target in ("inner", "intersection", "selection"):
            group_name = vertex_analysis.width_group_name(target)
            if not _target_modifier_exists(obj, target):
                vertex_analysis.clear_width_weights(obj, group_name=group_name)
                continue
            if vertex_analysis.has_width_controls(settings, target):
                vertex_analysis.compute_and_apply_weights(obj, settings, target)
            else:
                vertex_analysis.clear_width_weights(obj, group_name=group_name)
        inner_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.inner_line_thickness,
            ),
            **_inner_midpoint_kwargs(settings),
        )
        intersection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.intersection_thickness,
            ),
        )
        selection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.selection_line_thickness,
            ),
            **_selection_midpoint_kwargs(settings),
        )


def _update_width_controls(objects: list[bpy.types.Object], context) -> None:
    from . import vertex_analysis

    for obj in objects:
        settings = obj.bmanga_line_settings
        if settings.use_vertex_color:
            outline_setup._ensure_color_attribute(obj)
    pending_objects = objects
    uniform_targets = [
        obj for obj in objects
        if obj.bmanga_line_settings.use_uniform_line_width
    ]
    if uniform_targets and _refresh_camera_objects(
        uniform_targets,
        context,
        width_targets=("outline",),
    ):
        pending_objects = [obj for obj in objects if obj not in uniform_targets]

    for obj in pending_objects:
        settings = obj.bmanga_line_settings
        mod = _outline_modifier(obj)
        if mod is None:
            outline_setup.sync_sheet_outline_width(obj)
            continue
        use_vg = (
            settings.use_vertex_color
            or vertex_analysis.has_width_controls(settings, "outline")
        )
        if use_vg:
            vg = _ensure_vertex_group(obj, core.VG_LINE_WIDTH)
            mod.vertex_group = vg.name
            mod.thickness_vertex_group = 0.0
            if settings.use_vertex_color:
                outline_setup._ensure_color_attribute(obj)
            vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
        else:
            mod.vertex_group = ""
            vertex_analysis.clear_width_weights(obj, group_name=core.VG_LINE_WIDTH)
        outline_width_attribute.ensure_outline_width_attribute(obj, settings)
        outline_setup.sync_sheet_outline_width(obj)


def _update_width_target(
    objects: list[bpy.types.Object],
    context,
    target: str,
) -> None:
    from . import vertex_analysis

    targets = _generated_line_objects(objects, target)
    if not targets:
        return

    pending_targets = targets
    uniform_targets = [
        obj for obj in targets
        if obj.bmanga_line_settings.use_uniform_line_width
    ]
    if uniform_targets and _refresh_camera_objects(
        uniform_targets,
        context,
        width_targets=(target,),
    ):
        pending_targets = [obj for obj in targets if obj not in uniform_targets]

    group_name = vertex_analysis.width_group_name(target)
    for obj in pending_targets:
        settings = obj.bmanga_line_settings
        if vertex_analysis.has_width_controls(settings, target):
            if target == "outline":
                mod = _outline_modifier(obj)
                if mod is not None:
                    mod.vertex_group = group_name
                    mod.thickness_vertex_group = 0.0
            vertex_analysis.compute_and_apply_weights(obj, settings, target)
        else:
            if target == "outline":
                mod = _outline_modifier(obj)
                if mod is not None:
                    mod.vertex_group = ""
            vertex_analysis.clear_width_weights(obj, group_name=group_name)
        if target == "outline":
            outline_width_attribute.ensure_outline_width_attribute(obj, settings)
            outline_setup.sync_sheet_outline_width(obj)
        if target == "intersection":
            intersection_lines.update_parameters(obj)
        elif target == "inner":
            inner_lines.update_parameters(obj, **_inner_midpoint_kwargs(settings))
        elif target == "selection":
            selection_lines.update_parameters(obj, **_selection_midpoint_kwargs(settings))


def _update_inner_angle(objects: list[bpy.types.Object], context) -> None:
    targets = _generated_line_objects(objects, "inner")
    for obj in targets:
        inner_lines.update_parameters(
            obj,
            angle=obj.bmanga_line_settings.inner_line_angle,
            **_inner_midpoint_kwargs(obj.bmanga_line_settings),
        )
    pending_targets = targets
    uniform_targets = [
        obj for obj in targets
        if obj.bmanga_line_settings.use_uniform_line_width
    ]
    if uniform_targets and _refresh_camera_objects(
        uniform_targets,
        context,
        width_targets=("inner",),
    ):
        pending_targets = [obj for obj in targets if obj not in uniform_targets]
    _update_width_target(pending_targets, context, "inner")


def _update_selection_angle(objects: list[bpy.types.Object], context) -> None:
    targets = _generated_line_objects(objects, "selection")
    for obj in targets:
        selection_lines.update_parameters(
            obj,
            angle=obj.bmanga_line_settings.selection_line_angle,
            **_selection_midpoint_kwargs(obj.bmanga_line_settings),
        )
    pending_targets = targets
    uniform_targets = [
        obj for obj in targets
        if obj.bmanga_line_settings.use_uniform_line_width
    ]
    if uniform_targets and _refresh_camera_objects(
        uniform_targets,
        context,
        width_targets=("selection",),
    ):
        pending_targets = [obj for obj in targets if obj not in uniform_targets]
    _update_width_target(pending_targets, context, "selection")


def _update_marked_inner_edges(objects: list[bpy.types.Object], context) -> None:
    return


def _update_inner_lines(
    objects: list[bpy.types.Object],
    context,
    *,
    create_missing: bool = True,
) -> None:
    if not any(obj.bmanga_line_settings.inner_line_enabled for obj in objects):
        for obj in _generated_line_objects(objects, "inner"):
            inner_lines.disable_inner_lines(obj)
        return

    defer_intersection_viewport(objects)
    presets._update_view_layer(context)
    refresh_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if plane_filter.should_skip_inner_lines(obj, settings):
            inner_lines.remove_inner_lines(obj)
            continue
        if settings.inner_line_enabled and camera_comp.inner_line_creation_in_range(
            obj,
            context.scene,
            settings,
        ):
            if not create_missing:
                if obj.modifiers.get(core.GN_MODIFIER_NAME) is not None:
                    inner_lines.enable_inner_lines(obj)
                continue
            if inner_lines.apply_inner_lines(
                obj,
                angle=settings.inner_line_angle,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.inner_line_thickness,
                ),
                material=outline_setup.get_line_material(obj, "inner"),
                offset=settings.inner_line_offset,
                use_marked_edges=False,
                **_inner_midpoint_kwargs(settings),
                enable=False,
            ):
                refresh_targets.append(obj)
        else:
            if not settings.inner_line_enabled:
                inner_lines.disable_inner_lines(obj)
            elif obj.modifiers.get(core.GN_MODIFIER_NAME) is not None:
                if inner_lines.enable_inner_lines(obj):
                    refresh_targets.append(obj)
    for obj in refresh_targets:
        inner_lines.enable_inner_lines(obj)
    if refresh_targets:
        _refresh_camera_objects(
            refresh_targets,
            context,
            update_visibility=True,
            width_targets=("inner",),
        )


def _update_inner_creation_range(objects: list[bpy.types.Object], context) -> None:
    """作成範囲の設定変更を稜谷線へ反映（状態が変わるオブジェクトだけ再構築）."""
    from . import plane_filter

    create_targets = []
    refresh_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if not settings.inner_line_enabled:
            continue
        if plane_filter.should_skip_inner_lines(obj, settings):
            continue
        in_range = camera_comp.inner_line_creation_in_range(
            obj,
            context.scene,
            settings,
        )
        mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
        if not in_range:
            if mod is not None and inner_lines.enable_inner_lines(obj):
                refresh_targets.append(obj)
            continue
        if mod is None:
            create_targets.append(obj)
        elif inner_lines.enable_inner_lines(obj):
            refresh_targets.append(obj)
    if create_targets:
        presets._update_view_layer(context)
        for obj in create_targets:
            settings = obj.bmanga_line_settings
            if inner_lines.apply_inner_lines(
                obj,
                angle=settings.inner_line_angle,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.inner_line_thickness,
                ),
                material=outline_setup.get_line_material(obj, "inner"),
                offset=settings.inner_line_offset,
                use_marked_edges=False,
                **_inner_midpoint_kwargs(settings),
            ):
                refresh_targets.append(obj)
    if refresh_targets:
        _refresh_camera_objects(
            refresh_targets,
            context,
            update_visibility=True,
            width_targets=("inner",),
        )


def _update_selection_lines(
    objects: list[bpy.types.Object],
    context,
    *,
    create_missing: bool = True,
) -> None:
    if not any(obj.bmanga_line_settings.selection_line_enabled for obj in objects):
        for obj in _generated_line_objects(objects, "selection"):
            selection_lines.disable_selection_lines(obj)
        return

    presets._update_view_layer(context)
    refresh_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if settings.selection_line_enabled and camera_comp.selection_line_creation_in_range(
            obj,
            context.scene,
            settings,
        ):
            if not create_missing:
                if obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME) is not None:
                    selection_lines.enable_selection_lines(obj)
                continue
            if selection_lines.apply_selection_lines(
                obj,
                angle=settings.selection_line_angle,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.selection_line_thickness,
                ),
                material=outline_setup.get_line_material(obj, "selection"),
                offset=settings.selection_line_offset,
                **_selection_midpoint_kwargs(settings),
                enable=False,
            ):
                refresh_targets.append(obj)
        else:
            if not settings.selection_line_enabled:
                selection_lines.disable_selection_lines(obj)
            elif obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME) is not None:
                if selection_lines.enable_selection_lines(obj):
                    refresh_targets.append(obj)
    for obj in refresh_targets:
        selection_lines.enable_selection_lines(obj)
    if refresh_targets:
        _refresh_camera_objects(
            refresh_targets,
            context,
            update_visibility=True,
            width_targets=("selection",),
        )


def _update_selection_creation_range(objects: list[bpy.types.Object], context) -> None:
    create_targets = []
    refresh_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if not settings.selection_line_enabled:
            continue
        in_range = camera_comp.selection_line_creation_in_range(
            obj,
            context.scene,
            settings,
        )
        mod = obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME)
        if not in_range:
            if mod is not None and selection_lines.enable_selection_lines(obj):
                refresh_targets.append(obj)
            continue
        if mod is None:
            create_targets.append(obj)
        elif selection_lines.enable_selection_lines(obj):
            refresh_targets.append(obj)
    if create_targets:
        presets._update_view_layer(context)
        for obj in create_targets:
            settings = obj.bmanga_line_settings
            if selection_lines.apply_selection_lines(
                obj,
                angle=settings.selection_line_angle,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.selection_line_thickness,
                ),
                material=outline_setup.get_line_material(obj, "selection"),
                offset=settings.selection_line_offset,
                **_selection_midpoint_kwargs(settings),
            ):
                refresh_targets.append(obj)
    if refresh_targets:
        _refresh_camera_objects(
            refresh_targets,
            context,
            update_visibility=True,
            width_targets=("selection",),
        )


def _update_intersections(objects: list[bpy.types.Object], context) -> None:
    if not objects:
        return
    if not any(obj.bmanga_line_settings.intersection_enabled for obj in objects):
        for obj in _generated_line_objects(objects, "intersection"):
            intersection_lines.remove_intersection_lines(obj)
        if intersection_lines.scene_has_enabled_intersections(context.scene):
            intersection_lines.refresh_scene_intersections(context.scene)
        return

    presets._update_view_layer(context)
    refresh_targets = intersection_lines.refresh_scene_intersections(context.scene)
    if refresh_targets:
        _refresh_camera_objects(
            refresh_targets,
            context,
            update_visibility=True,
            width_targets=("intersection",),
        )


def _update_sheet_exclusion(objects: list[bpy.types.Object], context) -> None:
    from . import plane_filter

    removed_targets = []
    rebuild_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if plane_filter.should_skip_inner_lines(obj, settings):
            removed = inner_lines.remove_inner_lines(obj)
            if removed:
                removed_targets.append(obj)
        elif (
            not bool(getattr(settings, "exclude_sheet_meshes", False))
            and plane_filter.is_sheet_mesh(obj)
        ):
            rebuild_targets.append(obj)
    intersection_lines.prune_excluded_intersections(context.scene)
    if rebuild_targets and len(objects) <= MAX_IMMEDIATE_SHEET_REBUILD_OBJECTS:
        _update_inner_lines(rebuild_targets, context)
        _update_intersections(rebuild_targets, context)
    if removed_targets:
        _refresh_camera_objects(removed_targets, context, update_visibility=True)


def _update_auto_subdivision(objects: list[bpy.types.Object], context) -> None:
    from . import subdivision_lod

    for obj in objects:
        settings = obj.bmanga_line_settings
        if settings.auto_subdivision_for_midpoint:
            subdivision_lod.ensure_auto_subdivision(obj, context.scene)
            modifier_stack.reorder_line_modifiers(obj)
        else:
            subdivision_lod.remove_auto_subdivision(obj)


def _update_lines_visible(objects: list[bpy.types.Object], context) -> None:
    needs_refresh = False
    for obj in objects:
        visible = bool(obj.bmanga_line_settings.lines_visible)
        if core.set_line_visibility(obj, visible) and visible:
            needs_refresh = True
    if needs_refresh:
        camera_comp.refresh(context)


def _update_line_only_visible(objects: list[bpy.types.Object], context) -> None:
    del objects
    enabled = bool(getattr(context.scene, "bmanga_line_line_only_visible", False))
    core.set_scene_line_only(context, enabled)


def _update_match_subsurf_viewport_to_render(objects: list[bpy.types.Object]) -> None:
    from . import subdivision_lod

    for obj in objects:
        if bool(obj.bmanga_line_settings.match_subsurf_viewport_to_render):
            subdivision_lod.sync_viewport_levels_to_render(obj)
        else:
            subdivision_lod.reset_viewport_levels_to_zero(obj)


def _update_visibility_rules(objects: list[bpy.types.Object], context) -> None:
    needs_refresh = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if (
            settings.use_camera_culling
            or settings.use_outline_distance_limit
            or settings.use_inner_line_distance_limit
            or settings.use_intersection_distance_limit
            or settings.use_selection_line_distance_limit
        ):
            needs_refresh.append(obj)
        else:
            visible = not bool(obj.get(core.PROP_LINES_HIDDEN, False))
            core.set_line_visibility(obj, visible)
    if needs_refresh:
        if len(needs_refresh) > MAX_IMMEDIATE_VISIBILITY_OBJECTS:
            return
        camera_comp.refresh_visibility_objects(context, needs_refresh)


def _target_distance_limit_enabled(settings, target: str) -> bool:
    if target == "inner":
        return bool(settings.use_inner_line_distance_limit)
    if target == "intersection":
        return bool(settings.use_intersection_distance_limit)
    if target == "selection":
        return bool(settings.use_selection_line_distance_limit)
    return bool(settings.use_outline_distance_limit)


def _update_target_visibility_rules(
    objects: list[bpy.types.Object],
    context,
    target: str,
) -> None:
    needs_refresh = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if settings.use_camera_culling or _target_distance_limit_enabled(settings, target):
            needs_refresh.append(obj)
        else:
            visible = not bool(obj.get(core.PROP_LINES_HIDDEN, False))
            core.set_line_targets_visibility(obj, visible, (target,))
    if needs_refresh and not camera_comp.refresh_visibility_objects(
        context,
        needs_refresh,
        visibility_targets=(target,),
    ):
        for obj in needs_refresh:
            visible = not bool(obj.get(core.PROP_LINES_HIDDEN, False))
            core.set_line_targets_visibility(obj, visible, (target,))


def _update_generated_visual_parameters(
    objects: list[bpy.types.Object],
    target: str,
) -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        if target == "inner":
            inner_lines.update_parameters(
                obj,
                offset=settings.inner_line_offset,
                material=outline_setup.get_line_material(obj, "inner"),
                **_inner_midpoint_kwargs(settings),
            )
        elif target == "intersection":
            intersection_lines.update_parameters(
                obj,
                offset=settings.intersection_line_offset,
                material=outline_setup.get_line_material(obj, "intersection"),
            )
        elif target == "selection":
            selection_lines.update_parameters(
                obj,
                offset=settings.selection_line_offset,
                material=outline_setup.get_line_material(obj, "selection"),
                **_selection_midpoint_kwargs(settings),
            )


def refresh_target_visuals(
    target: str,
    objects: list[bpy.types.Object],
    context,
) -> list[bpy.types.Object]:
    """作成済みラインの見た目だけを線種別に更新する."""
    target = str(target)
    line_objects = _line_objects(objects)
    targets = _generated_line_objects(line_objects, target)
    if not targets:
        return []

    if target == "outline":
        _update_auto_subdivision(targets, context)
        _update_match_subsurf_viewport_to_render(targets)
        _update_outline_color(targets)
        _update_outline_offset(targets)
        _update_outline_flag(targets, "use_even_offset", "even_thickness")
        _update_outline_flag(targets, "use_rim", "use_rim")
        _update_transparent_protection(targets)
        _update_width_target(targets, context, "outline")
        _update_outline_thickness(targets, context)
    else:
        _update_auto_subdivision(targets, context)
        _update_match_subsurf_viewport_to_render(targets)
        _update_generated_color(targets, target)
        _update_generated_visual_parameters(targets, target)
        _update_width_target(targets, context, target)
        _update_generated_thickness(targets, context, target)

    _update_target_visibility_rules(targets, context, target)
    return targets


def refresh_propagated_property(
    prop_name: str,
    objects: list[bpy.types.Object],
    context,
) -> None:
    if prop_name == "line_only_visible":
        _update_line_only_visible([], context)
        return
    if prop_name in {
        "outline_enabled",
        "inner_line_enabled",
        "intersection_enabled",
        "selection_line_enabled",
    }:
        line_objects = [
            obj for obj in objects
            if obj.type == "MESH" and obj.data is not None
        ]
    else:
        line_objects = _line_objects(objects)
    if not line_objects:
        return

    if prop_name == "even_thickness":
        _update_outline_flag(line_objects, "use_even_offset", prop_name)
        return
    if prop_name == "outline_enabled":
        _update_outline_enabled(line_objects, context)
        return
    if prop_name in {
        "use_outline_creation_limit",
        "outline_creation_max_distance",
    }:
        _update_outline_creation_range(line_objects, context)
        return
    if prop_name == "use_rim":
        _update_outline_flag(line_objects, "use_rim", prop_name)
        return
    if prop_name == "hide_through_transparent":
        _update_transparent_protection(line_objects)
        return
    if prop_name == "exclude_sheet_meshes":
        _update_sheet_exclusion(line_objects, context)
        return
    if prop_name == "auto_subdivision_for_midpoint":
        _update_auto_subdivision(line_objects, context)
        return
    if prop_name == "lines_visible":
        _update_lines_visible(line_objects, context)
        return
    if prop_name == "match_subsurf_viewport_to_render":
        _update_match_subsurf_viewport_to_render(line_objects)
        return
    if prop_name == "outline_color":
        _update_outline_color(line_objects)
        return
    if prop_name == "inner_line_color":
        _update_generated_color(line_objects, "inner")
        return
    if prop_name == "intersection_color":
        _update_generated_color(line_objects, "intersection")
        return
    if prop_name == "selection_line_color":
        _update_generated_color(line_objects, "selection")
        return
    if prop_name == "outline_thickness":
        _update_outline_thickness(line_objects, context)
        return
    if prop_name == "outline_offset":
        _update_outline_offset(line_objects)
        return
    if prop_name == "use_camera_compensation":
        _update_camera_compensation(line_objects, context)
        return
    if prop_name == "camera_compensation_influence":
        _update_camera_influence(line_objects, context)
        return
    if prop_name == "line_width_reference_distance":
        _update_line_width_reference_distance(line_objects, context)
        return
    if prop_name == "use_uniform_line_width":
        _update_uniform_line_width(line_objects, context)
        return
    if prop_name == "use_vertex_color":
        _update_width_controls(line_objects, context)
        return
    if prop_name in {
        "edge_smooth_factor",
        "edge_midpoint_jitter_percent",
        "edge_midpoint_angle",
        "edge_width_curve_25",
        "edge_width_curve_50",
        "edge_width_curve_75",
    }:
        _update_width_target(line_objects, context, "outline")
        return
    if prop_name in {
        "inner_edge_smooth_factor",
        "inner_edge_midpoint_jitter_percent",
        "inner_edge_width_curve_25",
        "inner_edge_width_curve_50",
        "inner_edge_width_curve_75",
    }:
        if (
            prop_name in _GENERATED_WIDTH_DETAIL_PROPS
            and len(line_objects) > MAX_IMMEDIATE_GENERATED_WIDTH_OBJECTS
        ):
            return
        _update_width_target(line_objects, context, "inner")
        return
    if prop_name in {
        "intersection_edge_smooth_factor",
        "intersection_edge_midpoint_jitter_percent",
        "intersection_edge_midpoint_angle",
        "intersection_edge_width_curve_25",
        "intersection_edge_width_curve_50",
        "intersection_edge_width_curve_75",
    }:
        if (
            prop_name in _GENERATED_WIDTH_DETAIL_PROPS
            and len(line_objects) > MAX_IMMEDIATE_GENERATED_WIDTH_OBJECTS
        ):
            return
        _update_width_target(line_objects, context, "intersection")
        return
    if prop_name in {
        "selection_edge_smooth_factor",
        "selection_edge_midpoint_jitter_percent",
        "selection_edge_midpoint_angle",
        "selection_edge_width_curve_25",
        "selection_edge_width_curve_50",
        "selection_edge_width_curve_75",
    }:
        if (
            prop_name in _GENERATED_WIDTH_DETAIL_PROPS
            and len(line_objects) > MAX_IMMEDIATE_GENERATED_WIDTH_OBJECTS
        ):
            return
        _update_width_target(line_objects, context, "selection")
        return
    if prop_name in {
        "use_camera_culling",
        "culling_margin",
        "use_outline_distance_limit",
        "outline_max_distance",
        "use_inner_line_distance_limit",
        "inner_line_max_distance",
        "use_intersection_distance_limit",
        "intersection_max_distance",
        "use_selection_line_distance_limit",
        "selection_line_max_distance",
    }:
        _update_visibility_rules(line_objects, context)
        return
    if prop_name == "inner_line_enabled":
        _update_inner_lines(line_objects, context)
        return
    if prop_name == "use_marked_inner_edges":
        _update_marked_inner_edges(line_objects, context)
        return
    if prop_name in {
        "use_inner_line_creation_limit",
        "inner_line_creation_max_distance",
    }:
        _update_inner_creation_range(line_objects, context)
        return
    if prop_name == "inner_line_angle":
        _update_inner_angle(line_objects, context)
        return
    if prop_name == "inner_line_thickness":
        _update_generated_thickness(line_objects, context, "inner")
        return
    if prop_name == "inner_line_offset":
        _update_generated_offset(line_objects, "inner")
        return
    if prop_name == "selection_line_enabled":
        _update_selection_lines(line_objects, context)
        return
    if prop_name in {
        "use_selection_line_creation_limit",
        "selection_line_creation_max_distance",
    }:
        _update_selection_creation_range(line_objects, context)
        return
    if prop_name == "selection_line_angle":
        _update_selection_angle(line_objects, context)
        return
    if prop_name == "selection_line_thickness":
        _update_generated_thickness(line_objects, context, "selection")
        return
    if prop_name == "selection_line_offset":
        _update_generated_offset(line_objects, "selection")
        return
    if prop_name in {
        "intersection_enabled",
    }:
        if len(line_objects) > MAX_IMMEDIATE_INTERSECTION_REBUILD_OBJECTS:
            if any(obj.bmanga_line_settings.intersection_enabled for obj in line_objects):
                return
            for obj in _generated_line_objects(line_objects, "intersection"):
                intersection_lines.remove_intersection_lines(obj)
            return
        _update_intersections(line_objects, context)
        return
    if prop_name == "intersection_method":
        if len(line_objects) > MAX_IMMEDIATE_INTERSECTION_REBUILD_OBJECTS:
            return
        _update_intersections(line_objects, context)
        return
    if prop_name in {
        "use_intersection_creation_limit",
        "intersection_creation_max_distance",
    }:
        if len(line_objects) > MAX_IMMEDIATE_INTERSECTION_REBUILD_OBJECTS:
            return
        _update_intersections(line_objects, context)
        return
    if prop_name == "intersection_thickness":
        _update_generated_thickness(line_objects, context, "intersection")
        return
    if prop_name == "intersection_line_offset":
        _update_generated_offset(line_objects, "intersection")
        return

    _refresh_full(line_objects, context)
