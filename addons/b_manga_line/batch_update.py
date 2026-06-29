"""B-MANGA Line batch updates for multi-selected setting changes."""

from __future__ import annotations

import bpy

from . import camera_comp, core, inner_lines, intersection_lines, outline_setup, presets


def _line_objects(objects: list[bpy.types.Object]) -> list[bpy.types.Object]:
    return [
        obj for obj in objects
        if obj.type == "MESH" and obj.data is not None and core.has_line(obj)
    ]


def _outline_modifier(obj: bpy.types.Object):
    return obj.modifiers.get(core.MODIFIER_NAME)


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


def _update_outline_flag(objects: list[bpy.types.Object], attr: str, prop_name: str) -> None:
    for obj in objects:
        mod = _outline_modifier(obj)
        if mod is not None:
            setattr(mod, attr, bool(getattr(obj.bmanga_line_settings, prop_name)))


def _update_transparent_protection(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        outline_setup.update_transparent_protection(
            obj,
            bool(settings.hide_through_transparent),
            tuple(settings.outline_color),
        )


def _update_camera_compensation(objects: list[bpy.types.Object], context) -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        mod = _outline_modifier(obj)
        if mod is None:
            continue
        if settings.use_camera_compensation:
            camera_comp.store_unit_reference(obj, context.scene)
        else:
            mod.thickness = abs(settings.outline_thickness)
            inner_lines.update_parameters(obj, thickness=settings.inner_line_thickness)
            intersection_lines.update_parameters(
                obj,
                thickness=settings.intersection_thickness,
            )
    _refresh_camera(context)


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

        mod.thickness = abs(settings.outline_thickness)
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
            if vertex_analysis.has_width_controls(settings, target):
                vertex_analysis.compute_and_apply_weights(obj, settings, target)
            else:
                vertex_analysis.clear_width_weights(obj, group_name=group_name)
        inner_lines.update_parameters(obj, thickness=settings.inner_line_thickness)
        intersection_lines.update_parameters(obj, thickness=settings.intersection_thickness)


def _update_width_controls(objects: list[bpy.types.Object], context) -> None:
    from . import vertex_analysis

    for obj in objects:
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
    _refresh_camera(context)


def _update_inner_lines(objects: list[bpy.types.Object], context) -> None:
    presets._update_view_layer(context)
    for obj in objects:
        settings = obj.bmanga_line_settings
        if settings.inner_line_enabled and camera_comp.inner_line_creation_in_range(
            obj,
            context.scene,
            settings,
        ):
            inner_lines.apply_inner_lines(
                obj,
                angle=settings.inner_line_angle,
                thickness=settings.inner_line_thickness,
                material=outline_setup.get_outline_material(obj),
                use_marked_edges=settings.use_marked_inner_edges,
            )
        else:
            inner_lines.remove_inner_lines(obj)
    _refresh_camera(context)


def _update_intersections(objects: list[bpy.types.Object], context) -> None:
    if not objects:
        return
    intersection_lines.refresh_scene_intersections(context.scene)
    _refresh_camera(context)


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
    if prop_name == "use_rim":
        _update_outline_flag(line_objects, "use_rim", prop_name)
        return
    if prop_name == "hide_through_transparent":
        _update_transparent_protection(line_objects)
        return
    if prop_name == "use_camera_compensation":
        _update_camera_compensation(line_objects, context)
        return
    if prop_name == "use_uniform_line_width":
        _update_uniform_line_width(line_objects, context)
        return
    if prop_name in {"use_vertex_color", "use_ao_influence"}:
        _update_width_controls(line_objects, context)
        return
    if prop_name in {
        "use_camera_culling",
        "use_outline_distance_limit",
        "use_inner_line_distance_limit",
        "use_intersection_distance_limit",
    }:
        _refresh_camera(context)
        return
    if prop_name in {
        "inner_line_enabled",
        "use_marked_inner_edges",
        "use_inner_line_creation_limit",
    }:
        _update_inner_lines(line_objects, context)
        return
    if prop_name in {"intersection_enabled", "use_intersection_creation_limit"}:
        _update_intersections(line_objects, context)
        return

    _refresh_full(line_objects, context)
