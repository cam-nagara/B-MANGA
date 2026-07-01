"""B-MANGA Line batch updates for multi-selected setting changes."""

from __future__ import annotations

import bpy

from . import camera_comp, core, inner_lines, intersection_lines, outline_setup, presets
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
    "intersection_edge_width_curve_25",
    "intersection_edge_width_curve_50",
    "intersection_edge_width_curve_75",
}


def _line_objects(objects: list[bpy.types.Object]) -> list[bpy.types.Object]:
    return [
        obj for obj in objects
        if obj.type == "MESH" and obj.data is not None and core.has_line(obj)
    ]


def _outline_modifier(obj: bpy.types.Object):
    return obj.modifiers.get(core.MODIFIER_NAME)


def defer_intersection_viewport(objects) -> None:
    for obj in objects:
        for mod in core.iter_intersection_modifiers(obj):
            intersection_lines._queue_deferred_viewport_modifier(obj, mod)


def _target_modifier_exists(obj: bpy.types.Object, target: str) -> bool:
    if target == "inner":
        return obj.modifiers.get(core.GN_MODIFIER_NAME) is not None
    if target == "intersection":
        return any(core.iter_intersection_modifiers(obj))
    return _outline_modifier(obj) is not None


def _generated_line_objects(
    objects: list[bpy.types.Object],
    target: str,
) -> list[bpy.types.Object]:
    if target == "outline":
        return [obj for obj in objects if _outline_modifier(obj) is not None]
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


def _update_outline_enabled(objects: list[bpy.types.Object], context) -> None:
    needs_view_update = any(
        obj.bmanga_line_settings.outline_enabled and _outline_modifier(obj) is None
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
        if settings.outline_enabled and _outline_modifier(obj) is None:
            if _ensure_outline_modifier(obj, context):
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
            )
        elif target == "intersection":
            intersection_lines.update_parameters(
                obj,
                thickness=modifier_thickness_for_world_width(
                    obj,
                    settings.intersection_thickness,
                ),
            )


def _update_generated_offset(objects: list[bpy.types.Object], target: str) -> None:
    targets = _generated_line_objects(objects, target)
    for obj in targets:
        settings = obj.bmanga_line_settings
        if target == "inner":
            inner_lines.update_parameters(obj, offset=settings.inner_line_offset)
        elif target == "intersection":
            intersection_lines.update_parameters(
                obj,
                offset=settings.intersection_line_offset,
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
        )
        intersection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.intersection_thickness,
            ),
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
        for target in ("inner", "intersection"):
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
        )
        intersection_lines.update_parameters(
            obj,
            thickness=modifier_thickness_for_world_width(
                obj,
                settings.intersection_thickness,
            ),
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
            continue
        use_vg = (
            settings.use_vertex_color
            or settings.use_ao_influence
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


def _update_inner_angle(objects: list[bpy.types.Object], context) -> None:
    targets = _generated_line_objects(objects, "inner")
    for obj in targets:
        inner_lines.update_parameters(
            obj,
            angle=obj.bmanga_line_settings.inner_line_angle,
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


def _update_marked_inner_edges(objects: list[bpy.types.Object], context) -> None:
    from . import plane_filter

    create_targets = []
    refresh_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if plane_filter.should_exclude_generated_lines(obj, settings):
            inner_lines.remove_inner_lines(obj)
            continue
        if not camera_comp.inner_line_creation_in_range(obj, context.scene, settings):
            inner_lines.disable_inner_lines(obj)
            continue
        if obj.modifiers.get(core.GN_MODIFIER_NAME) is not None:
            inner_lines.update_parameters(
                obj,
                use_marked_edges=settings.use_marked_inner_edges,
            )
        elif not settings.inner_line_enabled:
            continue
        else:
            create_targets.append(obj)
    if not create_targets:
        return
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
            use_marked_edges=settings.use_marked_inner_edges,
            enable=False,
        ):
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

    presets._update_view_layer(context)
    refresh_targets = []
    for obj in objects:
        settings = obj.bmanga_line_settings
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
                use_marked_edges=settings.use_marked_inner_edges,
                enable=False,
            ):
                refresh_targets.append(obj)
        else:
            inner_lines.disable_inner_lines(obj)
    for obj in refresh_targets:
        inner_lines.enable_inner_lines(obj)
    if refresh_targets:
        _refresh_camera_objects(
            refresh_targets,
            context,
            update_visibility=True,
            width_targets=("inner",),
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
        if plane_filter.should_exclude_generated_lines(obj, settings):
            removed = inner_lines.remove_inner_lines(obj)
            removed |= intersection_lines.remove_intersection_lines(obj)
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


def _update_visibility_rules(objects: list[bpy.types.Object], context) -> None:
    needs_refresh = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        if (
            settings.use_camera_culling
            or settings.use_outline_distance_limit
            or settings.use_inner_line_distance_limit
            or settings.use_intersection_distance_limit
        ):
            needs_refresh.append(obj)
        else:
            visible = not bool(obj.get(core.PROP_LINES_HIDDEN, False))
            core.set_line_visibility(obj, visible)
    if needs_refresh:
        if len(needs_refresh) > MAX_IMMEDIATE_VISIBILITY_OBJECTS:
            return
        camera_comp.refresh_visibility_objects(context, needs_refresh)


def refresh_propagated_property(
    prop_name: str,
    objects: list[bpy.types.Object],
    context,
) -> None:
    line_objects = _line_objects(objects)
    if not line_objects:
        return

    if prop_name == "even_thickness":
        _update_outline_flag(line_objects, "use_even_offset", prop_name)
        return
    if prop_name == "outline_enabled":
        _update_outline_enabled(line_objects, context)
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
    if prop_name == "outline_color":
        _update_outline_color(line_objects)
        return
    if prop_name == "inner_line_color":
        _update_generated_color(line_objects, "inner")
        return
    if prop_name == "intersection_color":
        _update_generated_color(line_objects, "intersection")
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
    if prop_name in {"use_vertex_color", "use_ao_influence", "ao_influence_strength"}:
        _update_width_controls(line_objects, context)
        return
    if prop_name in {
        "edge_smooth_factor",
        "edge_midpoint_jitter_percent",
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
        "use_camera_culling",
        "culling_margin",
        "use_outline_distance_limit",
        "outline_max_distance",
        "use_inner_line_distance_limit",
        "inner_line_max_distance",
        "use_intersection_distance_limit",
        "intersection_max_distance",
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
    if prop_name in {
        "intersection_enabled",
    }:
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
        return
    if prop_name == "intersection_thickness":
        _update_generated_thickness(line_objects, context, "intersection")
        return
    if prop_name == "intersection_line_offset":
        _update_generated_offset(line_objects, "intersection")
        return

    _refresh_full(line_objects, context)
